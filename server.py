# -*- coding: utf-8 -*-
"""TrueFit 简旨 · 前端对接用的最小 Web 服务。

只做一件事：把 data/ 下已有的 pipeline 产物，通过 HTTP 暴露给前端。
不跑 LLM、不跑打分，纯读 JSON，秒级响应。

启动：
    python server.py
默认监听 http://localhost:8000
"""
import json
import threading
from pathlib import Path
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_EXTRACTED = PROJECT_ROOT / "data" / "extracted"
DATA_OUTPUT = PROJECT_ROOT / "data" / "output"

app = Flask(__name__, static_folder=None)
CORS(app)  # 允许前端跨域访问（开发期全放开）


def read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- 上传任务 ----------
import os
import time
import hashlib
import threading
from werkzeug.utils import secure_filename

JOBS_DIR = PROJECT_ROOT / "data" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def compute_fingerprint(resume_text: str, jd_text: str) -> dict:
    """计算输入指纹。

    - resume_hash / jd_hash：各自独立的 SHA-256（截 16 位 hex）
    - fingerprint：组合指纹（截 24 位 hex），用作 job_id
    - 只含简历+JD 文本，不含配置（配置只影响规则环节，不耗 token）
    """
    resume_hash = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()[:16]
    jd_hash = hashlib.sha256(jd_text.encode("utf-8")).hexdigest()[:16]
    fingerprint = hashlib.sha256(
        (resume_hash + "|" + jd_hash).encode("utf-8")
    ).hexdigest()[:24]
    return {"resume_hash": resume_hash, "jd_hash": jd_hash, "fingerprint": fingerprint}


def _find_cached_job(resume_hash: str, jd_hash: str):
    """遍历 data/jobs/ 下的 meta.json，按指纹找已完成的任务。

    返回 job_id；未命中返回 None。旧格式任务（时间戳命名、无指纹字段）不会误命中。
    """
    if not JOBS_DIR.exists():
        return None
    for job_dir in JOBS_DIR.iterdir():
        meta_path = job_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (meta.get("resume_hash") == resume_hash
                and meta.get("jd_hash") == jd_hash
                and meta.get("status") == "done"):
            return job_dir.name
    return None

# 内存里记活跃线程，避免 GC 提前回收
_ACTIVE_THREADS = {}


def _parse_uploaded_file(file_storage) -> str:
    """把上传的文件解析成纯文本。支持 .txt / .md / .pdf / .docx。"""
    filename = (file_storage.filename or "").lower()
    ext = os.path.splitext(filename)[1]

    if ext in (".txt", ".md", ".markdown", ""):
        return file_storage.read().decode("utf-8", errors="ignore")

    if ext == ".pdf":
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_storage.read()))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            raise ValueError(f"PDF 解析失败：{e}")

    if ext == ".docx":
        try:
            import io
            from docx import Document
            doc = Document(io.BytesIO(file_storage.read()))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            raise ValueError(f"DOCX 解析失败：{e}")

    if ext == ".doc":
        raise ValueError("不支持 .doc 老格式，请另存为 .docx 后重试")

    raise ValueError(f"不支持的文件格式：{ext}")


@app.post("/api/upload")
def api_upload():
    """接收简历 + JD，启动后台任务，返回 job_id。

    支持两种提交方式：
    - multipart/form-data：字段 resume_file / jd_file（文件）
    - application/json：{"resume_text": "...", "jd_text": "..."}
    """
    from flask import request

    resume_text = None
    jd_text = None

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        rf = request.files.get("resume_file")
        jf = request.files.get("jd_file")
        if not rf or not jf:
            return jsonify({"error": "缺少 resume_file 或 jd_file"}), 400
        try:
            resume_text = _parse_uploaded_file(rf).strip()
            jd_text = _parse_uploaded_file(jf).strip()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    else:
        data = request.get_json(silent=True) or {}
        resume_text = (data.get("resume_text") or "").strip()
        jd_text = (data.get("jd_text") or "").strip()

    if not resume_text:
        return jsonify({"error": "简历内容为空"}), 400
    if not jd_text:
        return jsonify({"error": "JD 内容为空"}), 400

    # 指纹 = 输入的身份证，直接用作 job_id
    # 同一份简历+JD 不管传几次，job_id 都一样 → 天然复用缓存
    fp = compute_fingerprint(resume_text, jd_text)
    job_id = fp["fingerprint"]
    job_dir = JOBS_DIR / job_id

    # 先查缓存：job 目录已存在
    if job_dir.exists():
        try:
            meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        if meta.get("status") == "done":
            # 已有完整产物 → 不跑 pipeline，零 token 直接返回
            return jsonify({"job_id": job_id, "cached": True})
        if meta.get("status") == "running":
            # 正在跑 → 幂等返回，前端继续轮询，不重复启动线程
            return jsonify({"job_id": job_id, "cached": False, "running": True})
        # status=failed / 产物不完整 → 落到下面重跑

    # 启动后台线程
    def _worker():
        try:
            import sys
            sys.path.insert(0, str(PROJECT_ROOT / "src"))
            from pipeline.run_upload import run_upload
            run_upload(resume_text, jd_text, job_id, fingerprint=fp)
        except Exception as e:
            print(f"[job {job_id}] 失败：{e}")
        finally:
            _ACTIVE_THREADS.pop(job_id, None)

    th = threading.Thread(target=_worker, daemon=True)
    _ACTIVE_THREADS[job_id] = th
    th.start()

    return jsonify({"job_id": job_id, "cached": False})


@app.get("/api/job/<job_id>")
def api_job(job_id):
    """查任务进度。返回 meta + logs（处理日志数组）。"""
    job_dir = JOBS_DIR / job_id
    meta_path = job_dir / "meta.json"
    if not meta_path.exists():
        return jsonify({"error": "任务不存在"}), 404
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"status": "running", "progress": "读取中…", "logs": []})

    # 附加处理日志（logs.json，不存在则返回空数组）
    logs_path = job_dir / "logs.json"
    try:
        logs = json.loads(logs_path.read_text(encoding="utf-8")) if logs_path.exists() else []
    except Exception:
        logs = []
    meta["logs"] = logs if isinstance(logs, list) else []
    return jsonify(meta)


def _result_payload(job_id: str):
    """组装某 job 的最终产物（/api/result 与 /api/cache 共用）。

    返回 (jsonify 结果, None)；产物不完整/不存在时返回 (None, error response)。
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        return None, (jsonify({"error": "任务不存在"}), 404)

    def _read_json(name):
        p = job_dir / name
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    resume = _read_json("resume.json")
    jd = _read_json("jd.json")
    clusters = _read_json("clusters.json")
    changes = _read_json("changes.json")
    scored = _read_json("scored.json")
    report_md = (job_dir / "report.md").read_text(encoding="utf-8") \
        if (job_dir / "report.md").exists() else ""

    if resume is None or jd is None:
        return None, (jsonify({"error": "产物不完整，任务可能未完成"}), 400)

    return (jsonify({
        "resume": resume,
        "jd": jd,
        "clusters": clusters,
        "changes": changes,
        "scored": scored,
        "report_markdown": report_md,
    }), None)


@app.get("/api/result/<job_id>")
def api_result(job_id):
    """拿任务的最终产物。组装成前端 buildModels 需要的 bundle 格式。"""
    payload, err = _result_payload(job_id)
    return err if err else payload


@app.get("/api/cache")
def api_cache():
    """按输入指纹查缓存。

    遍历 data/jobs/ 的 meta.json 比对指纹，命中返回与 /api/result 同构的产物，
    未命中返回 {"found": false}。前端打开页面/点生成前先查这里，避免重复跑 LLM。
    """
    from flask import request

    resume_hash = (request.args.get("resume_hash") or "").strip()
    jd_hash = (request.args.get("jd_hash") or "").strip()

    if not resume_hash or not jd_hash:
        return jsonify({"error": "缺少 resume_hash 或 jd_hash"}), 400

    job_id = _find_cached_job(resume_hash, jd_hash)
    if not job_id:
        return jsonify({"found": False})

    payload, err = _result_payload(job_id)
    if err:
        # 目录在但产物不完整 → 视为未命中（下次 upload 会重跑）
        return jsonify({"found": False})
    return jsonify({"found": True, "job_id": job_id, "result": payload.get_json()})

# ---------- 探活 ----------
@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "msg": "后端活着", "root": str(PROJECT_ROOT)})


# ---------- 简历提炼产物（调用1）----------
@app.get("/api/resume/<resume_id>")
def get_resume(resume_id):
    data = read_json(DATA_EXTRACTED / "resumes" / f"{resume_id}.json")
    if data is None:
        return jsonify({"error": f"resumes/{resume_id}.json 不存在"}), 404
    return jsonify(data)


# ---------- JD 提炼产物（调用2）----------
@app.get("/api/jd/<jd_id>")
def get_jd(jd_id):
    data = read_json(DATA_EXTRACTED / "jds" / f"{jd_id}.json")
    if data is None:
        return jsonify({"error": f"jds/{jd_id}.json 不存在"}), 404
    return jsonify(data)


# ---------- 聚类结果 ----------
@app.get("/api/clusters/<resume_id>")
def get_clusters(resume_id):
    data = read_json(DATA_OUTPUT / f"clusters_{resume_id}.json")
    if data is None:
        return jsonify({"error": f"clusters_{resume_id}.json 不存在"}), 404
    return jsonify(data)


# ---------- 改写指令（调用5 产物）----------
@app.get("/api/changes/<resume_id>")
def get_changes(resume_id):
    # 优先读 _live 版本（真实 LLM 跑出来的），没有就退回到普通版本
    for name in (f"changes_{resume_id}_live.json", f"changes_{resume_id}.json"):
        data = read_json(DATA_OUTPUT / name)
        if data is not None:
            return jsonify({"file": name, "data": data})
    return jsonify({"error": f"changes_{resume_id}*.json 不存在"}), 404


# ---------- 匹配报告（Markdown 文本）----------
@app.get("/api/report/<resume_id>")
def get_report(resume_id):
    for name in (f"report_{resume_id}_live.md", f"report_{resume_id}.md"):
        path = DATA_OUTPUT / name
        if path.exists():
            return jsonify({"file": name, "markdown": path.read_text(encoding="utf-8")})
    return jsonify({"error": f"report_{resume_id}*.md 不存在"}), 404


# ---------- 一次性把结果页要用的东西全给前端 ----------
# ---------- 一次性把结果页要用的东西全给前端 ----------
@app.get("/api/bundle/<resume_id>/<jd_id>")
def get_bundle(resume_id, jd_id):
    """结果页一次请求拿全：简历 + JD + 聚类 + changes + report。"""
    # 简历：按顺序试 A_live → A → A_backend_5y
    resume = None
    for name in (f"{resume_id}_live.json", f"{resume_id}.json", f"{resume_id}_backend_5y.json"):
        resume = read_json(DATA_EXTRACTED / "resumes" / name)
        if resume is not None:
            break
    # JD：同理
    jd = None
    for name in (f"{jd_id}_live.json", f"{jd_id}.json"):
        jd = read_json(DATA_EXTRACTED / "jds" / name)
        if jd is not None:
            break

    bundle = {
        "resume": resume,
        "jd": jd,
        "clusters": read_json(DATA_OUTPUT / f"clusters_{resume_id}.json"),
    }
    for name in (f"changes_{resume_id}_live.json", f"changes_{resume_id}.json"):
        d = read_json(DATA_OUTPUT / name)
        if d is not None:
            bundle["changes"] = d
            bundle["changes_file"] = name
            break
    for name in (f"report_{resume_id}_live.md", f"report_{resume_id}.md"):
        p = DATA_OUTPUT / name
        if p.exists():
            bundle["report_markdown"] = p.read_text(encoding="utf-8")
            bundle["report_file"] = name
            break

    if bundle.get("resume") is None:
        return jsonify({"error": f"找不到简历文件：resumes/{resume_id}*.json"}), 404
    if bundle.get("jd") is None:
        return jsonify({"error": f"找不到 JD 文件：jds/{jd_id}*.json"}), 404

    return jsonify(bundle)


# ---------- 列出当前可用的数据文件（方便排查）----------
@app.get("/api/files")
def list_files():
    out = {"extracted": {}, "output": {}}
    for sub in ("resumes", "jds"):
        d = DATA_EXTRACTED / sub
        out["extracted"][sub] = sorted(p.name for p in d.glob("*.json")) if d.exists() else []
    if DATA_OUTPUT.exists():
        out["output"] = sorted(p.name for p in DATA_OUTPUT.iterdir() if p.is_file())
    return jsonify(out)

# ---------- 真实打分（现跑 step3 + step4，几秒出结果）----------
@app.get("/api/scored/<resume_id>/<jd_id>")
def get_scored(resume_id, jd_id):
    """
    现跑一遍打分 + 分层（纯规则，不调 LLM）。
    依赖 run_pipeline.py 里的 run() + use_mock=True。
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    try:
        from pipeline.run_pipeline import run as run_pipeline
    except ImportError as e:
        return jsonify({"error": f"导入 pipeline 失败：{e}"}), 500

    # 找到简历和 JD 的提炼产物
    resume_file = None
    for name in (f"{resume_id}_live.json", f"{resume_id}.json"):
        p = DATA_EXTRACTED / "resumes" / name
        if p.exists():
            resume_file = p
            break
    jd_file = None
    for name in (f"{jd_id}_live.json", f"{jd_id}.json"):
        p = DATA_EXTRACTED / "jds" / name
        if p.exists():
            jd_file = p
            break

    if not resume_file or not jd_file:
        return jsonify({"error": "找不到提炼产物"}), 404

    # 聚类（打分/分层不依赖聚类，但 run() 需要它，给个空壳）
    clusters_path = DATA_OUTPUT / f"clusters_{resume_id}.json"
    clusters = read_json(clusters_path) or {"clusters": [], "omitted_ids": []}

    # changes 也用现有的，避免触发 LLM
    changes_path = None
    for name in (f"changes_{resume_id}_live.json", f"changes_{resume_id}.json"):
        p = DATA_OUTPUT / name
        if p.exists():
            changes_path = p
            break

    mock_paths = {
        "resume": resume_file,
        "jd": jd_file,
        "changes": changes_path,
    }

    try:
        result = run_pipeline(
            resume_text="", jd_text="",
            resume_id=resume_id, jd_id=jd_id,
            clusters=clusters,
            use_mock=True,
            mock_paths=mock_paths,
        )
    except Exception as e:
        import traceback
        return jsonify({
            "error": f"跑 pipeline 失败：{e}",
            "trace": traceback.format_exc(),
        }), 500

    scored = result.get("scored") or {}
    return jsonify({
        "rows": scored.get("rows", []),
        "distribution": scored.get("distribution", {}),
        "weights": scored.get("weights", {}),
        "groups": scored.get("groups", {}),
    })

# ---------- 顺手把 test.html 也托管了 ----------
@app.get("/")
def index():
    return send_from_directory(PROJECT_ROOT, "test.html")

@app.get("/live")
def live():
    return send_from_directory(PROJECT_ROOT, "index_live.html")

if __name__ == "__main__":
    print("=" * 60)
    print("TrueFit mock server")
    print("  http://localhost:8000/api/ping")
    print("  http://localhost:8000/api/files")
    print("  http://localhost:8000/api/bundle/A/JD1_backend")
    print("=" * 60)

    # bge-m3 模型预热：后台线程加载（约 20-30s），不阻塞服务启动。
    # 否则首个上传任务的调用3（打分）要额外付 ~23s 模型加载时间。
    def _warmup_model():
        try:
            from src.pipeline.embedding_utils import get_model
            get_model()
            print("[warmup] bge-m3 预热完成")
        except Exception as e:
            print(f"[warmup] 模型预热失败（不影响启动）: {e}")

    threading.Thread(target=_warmup_model, daemon=True).start()

    # debug 模式说明：
    #   - 开发阶段可改回 debug=True（改代码自动重启，方便）
    #   - 测量耗时 / 做性能优化时，务必用 debug=False（自动重载会重启进程，
    #     模型预热失效、耗时被污染）；改代码后手动重启 server 即可
    #   - 上线时同样用 debug=False
    app.run(host="127.0.0.1", port=8000, debug=False)