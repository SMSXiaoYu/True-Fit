# -*- coding: utf-8 -*-
"""TrueFit 简旨 · 前端对接用的最小 Web 服务。

只做一件事：把 data/ 下已有的 pipeline 产物，通过 HTTP 暴露给前端。
不跑 LLM、不跑打分，纯读 JSON，秒级响应。

启动：
    python server.py
默认监听 http://localhost:8000
"""
import json
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
import uuid
import threading
from werkzeug.utils import secure_filename

JOBS_DIR = PROJECT_ROOT / "data" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

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

    # 生成 job_id
    job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]

    # 启动后台线程
    def _worker():
        try:
            import sys
            sys.path.insert(0, str(PROJECT_ROOT / "src"))
            from pipeline.run_upload import run_upload
            run_upload(resume_text, jd_text, job_id)
        except Exception as e:
            print(f"[job {job_id}] 失败：{e}")
        finally:
            _ACTIVE_THREADS.pop(job_id, None)

    th = threading.Thread(target=_worker, daemon=True)
    _ACTIVE_THREADS[job_id] = th
    th.start()

    return jsonify({"job_id": job_id})


@app.get("/api/job/<job_id>")
def api_job(job_id):
    """查任务进度。"""
    meta_path = JOBS_DIR / job_id / "meta.json"
    if not meta_path.exists():
        return jsonify({"error": "任务不存在"}), 404
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"status": "running", "progress": "读取中…"})
    return jsonify(meta)


@app.get("/api/result/<job_id>")
def api_result(job_id):
    """拿任务的最终产物。组装成前端 buildModels 需要的 bundle 格式。"""
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        return jsonify({"error": "任务不存在"}), 404

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
        return jsonify({"error": "产物不完整，任务可能未完成"}), 400

    return jsonify({
        "resume": resume,
        "jd": jd,
        "clusters": clusters,
        "changes": changes,
        "scored": scored,
        "report_markdown": report_md,
    })

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
    app.run(host="127.0.0.1", port=8000, debug=True)