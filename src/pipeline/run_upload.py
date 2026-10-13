# -*- coding: utf-8 -*-
"""上传任务的 pipeline 执行入口。

跟 run_pipeline.run() 的区别：
- run() 面向"离线跑已有数据"，接收文本 + clusters，一次跑完
- run_upload() 面向"用户上传"，自己构造 clusters（每 E 一个 G），
  且每步更新进度到 meta.json

流程：step1 → step2 → step3 → step4 → 构造 clusters → step5 → Gate → step6
"""
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from .step1_extract_resume import extract_resume
    from .step2_extract_jd import extract_jd
    from .step3_score import score_experiences
    from .step4_tier import tier_experiences
    from .step5_generate_changes import generate_changes
    from .stepG_gate import validate_changes
    from .step6_report import generate_report
    from .llm_client import get_last_usage
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from step1_extract_resume import extract_resume
    from step2_extract_jd import extract_jd
    from step3_score import score_experiences
    from step4_tier import tier_experiences
    from step5_generate_changes import generate_changes
    from stepG_gate import validate_changes
    from step6_report import generate_report
    from llm_client import get_last_usage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = PROJECT_ROOT / "data" / "jobs"


def _write_meta(job_dir: Path, meta: dict):
    """原子写 meta.json（先写 .tmp 再 rename），避免读到半截。"""
    tmp = job_dir / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(job_dir / "meta.json")


def _update_progress(job_dir: Path, meta: dict, step: str, status: str = "running"):
    """更新进度并落盘。"""
    meta["status"] = status
    meta["progress"] = step
    meta["updated_at"] = time.time()
    _write_meta(job_dir, meta)


_LOG_LOCK = threading.Lock()   # 调用1、2并行时两个线程同时追加日志，需串行化

def _append_log(job_dir: Path, msg: str, started_at: float = None):
    """追加一条处理日志到 logs.json（加锁 + 原子写，供并行线程安全调用）。

    格式：{"time": 相对开始的秒数, "msg": 日志内容}。started_at 为 None 时 time=0。
    """
    with _LOG_LOCK:
        p = job_dir / "logs.json"
        try:
            logs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except Exception:
            logs = []
        t = round(time.time() - started_at, 1) if started_at else 0.0
        logs.append({"time": t, "msg": msg})
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


def _build_atomic_clusters(experiences: list, scored_rows: list) -> dict:
    """构造"每个 E 一个 G"的 clusters。

    experiences: 简历提炼产出的 experiences 列表
    scored_rows: step3+4 产出的 rows，每条含 id / tier.tier
    """
    tier_by_id = {}
    for r in scored_rows or []:
        t = (r.get("tier") or {}).get("tier") or "related"
        tier_by_id[r.get("id")] = t

    clusters = []
    for i, e in enumerate(experiences):
        eid = e.get("id")
        clusters.append({
            "group_id": f"G{i+1}",
            "group_title": e.get("title") or f"经历{i+1}",
            "member_ids": [eid],
            "tier": tier_by_id.get(eid, "related"),
        })
    return {"clusters": clusters, "omitted_ids": []}


def run_upload(resume_text: str, jd_text: str, job_id: str, *, fingerprint: dict = None) -> dict:
    """跑一次上传任务。**这个函数是同步的，由 server.py 放到后台线程里跑。**

    参数:
        resume_text: 简历纯文本（已从 PDF/Word 解析出）
        jd_text:     JD 纯文本
        job_id:      任务 ID（由 server.py 生成，= 输入指纹），产物写入 data/jobs/{job_id}/
        fingerprint: 可选，server.py 算好的输入指纹
                     {"resume_hash","jd_hash","fingerprint"}，写入 meta.json 供缓存查询
    返回:
        dict，包含所有产物路径（正常完成时）
        失败时抛异常，异常信息由 server.py 捕获写入 meta.json
    """
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # 记录输入（便于排查）
    (job_dir / "resume.txt").write_text(resume_text, encoding="utf-8")
    (job_dir / "jd.txt").write_text(jd_text, encoding="utf-8")

    meta = {
        "job_id": job_id,
        "status": "running",
        "progress": "开始",
        "started_at": time.time(),
        "updated_at": time.time(),
        "timings": {},   # 每步耗时（秒），见下方各 _t_* 记录
        "tokens": {},    # Token 用量（LLM 返回 usage 时记录，取不到为 null）
        "gate": {},      # Gate 校验统计
        "input_features": {},  # 输入特征
        "cache": {"hit": False, "hit_steps": []},  # 缓存命中信息（run_upload 只在 cache miss 时调用）
    }
    if fingerprint:
        meta["resume_hash"] = fingerprint.get("resume_hash")
        meta["jd_hash"] = fingerprint.get("jd_hash")
        meta["fingerprint"] = fingerprint.get("fingerprint")
    _write_meta(job_dir, meta)

    resume_id = job_id   # 复用 job_id 作为 resume_id，产物不污染 data/extracted/
    jd_id = job_id

    try:
        # ---- 调用1（简历提炼）+ 调用2（JD 提炼）：互不依赖，并行执行 ----
        _update_progress(job_dir, meta, "正在读取简历与 JD…")
        _t0 = time.time()
        _t_resume = [0.0]   # 用 list 以便在线程内写入
        _t_jd = [0.0]
        _usage_resume = [None]
        _usage_jd = [None]

        def _run_resume():
            _ta = time.time()
            _append_log(job_dir, "解析简历结构...", meta["started_at"])
            r = extract_resume(resume_text, resume_id, use_mock=False)
            # call_llm 把 usage 存在当前 worker 线程的 thread-local，必须在此线程内读取
            _usage_resume[0] = get_last_usage()
            _t_resume[0] = time.time() - _ta
            _append_log(job_dir, f"提取到 {len(r.get('experiences') or [])} 段经历", meta["started_at"])
            return r

        def _run_jd():
            _ta = time.time()
            _append_log(job_dir, "解析 JD 要求...", meta["started_at"])
            j = extract_jd(jd_text, jd_id, use_mock=False)
            _usage_jd[0] = get_last_usage()
            _t_jd[0] = time.time() - _ta
            _append_log(job_dir, f"提取到 {len((j.get('requirements') or {}).get('responsibilities') or [])} 项要求", meta["started_at"])
            return j

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_resume = executor.submit(_run_resume)
            fut_jd = executor.submit(_run_jd)
            resume = fut_resume.result()   # 任一失败在此抛出，走统一异常处理
            jd = fut_jd.result()

        meta["timings"]["step1_resume_extract"] = round(_t_resume[0], 1)
        meta["timings"]["step2_jd_extract"] = round(_t_jd[0], 1)
        meta["timings"]["step1_2_parallel_max"] = round(max(_t_resume[0], _t_jd[0]), 1)

        # ---- Token 用量 ----
        meta["tokens"]["step1"] = _usage_resume[0]
        meta["tokens"]["step2"] = _usage_jd[0]

        # ---- 输入特征 ----
        resume_exps = resume.get("experiences") or []
        jd_reqs = jd.get("requirements") or {}
        jd_skills = jd_reqs.get("skills") or []
        jd_responsibilities = jd_reqs.get("responsibilities") or []
        resume_industries = list(dict.fromkeys(
            e.get("industry") for e in resume_exps if e.get("industry")
        ))
        jd_industry_raw = (jd_reqs.get("industry") or {}).get("preferred") or []
        meta["input_features"] = {
            "resume_chars": len(resume_text),
            "jd_chars": len(jd_text),
            "resume_experiences_count": len(resume_exps),
            "jd_requirements_count": len(jd_skills) + len(jd_responsibilities),
            "resume_industries": resume_industries,
            "jd_industry": jd_industry_raw[0] if jd_industry_raw else None,
        }
        _write_meta(job_dir, meta)

        (job_dir / "resume.json").write_text(
            json.dumps(resume, ensure_ascii=False, indent=2), encoding="utf-8")
        (job_dir / "jd.json").write_text(
            json.dumps(jd, ensure_ascii=False, indent=2), encoding="utf-8")

        
                # ---- 前置检查：确保数据有效 ----
        if not resume.get("experiences"):
            raise ValueError(
                "简历中未提取到任何经历。可能原因：\n"
                "1. 简历内容太短或不完整\n"
                "2. 上传的文件内容为空或格式不对\n"
                "请检查后重试。"
            )
        jd_reqs = jd.get("requirements") or {}
        if not jd_reqs.get("responsibilities"):
            raise ValueError(
                "JD 中未提取到职责描述。可能原因：\n"
                "1. JD 内容太短或不完整\n"
                "2. 上传的文件内容为空或格式不对\n"
                "请检查后重试。"
            )

        # ---- step3: 打分（第3步：分析匹配度） ----
        _update_progress(job_dir, meta, "正在评估经历匹配度…")
        _append_log(job_dir, "正在分析匹配度...", meta["started_at"])
        _t = time.time()
        declared_hard = (resume.get("skills_declared") or {}).get("hard", [])
        scored = score_experiences(
            resume["experiences"], jd["requirements"],
            skills_declared_hard=declared_hard,
        )
        meta["timings"]["step3_score"] = round(time.time() - _t, 1)

        # ---- step4: 分层（第3步：分析匹配度） ----
        _update_progress(job_dir, meta, "正在整理经历层级…")
        _t = time.time()
        tier_experiences(scored)
        meta["timings"]["step4_tier"] = round(time.time() - _t, 1)
        dist = {"core": 0, "related": 0, "other": 0}
        _tier_cn2en = {"核心": "core", "相关": "related", "其他": "other"}
        for r in scored.get("rows", []):
            t = (r.get("tier") or {}).get("tier") or "related"
            t = _tier_cn2en.get(t, t)   # step4 输出中文 tier（核心/相关/其他）→ 归一成英文键
            dist[t] = dist.get(t, 0) + 1
        _append_log(job_dir, f"分层完成：核心 {dist['core']} / 相关 {dist['related']} / 其他 {dist['other']}", meta["started_at"])
        (job_dir / "scored.json").write_text(
            json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- 构造 clusters（每 E 一个 G）----
        clusters = _build_atomic_clusters(resume["experiences"], scored.get("rows", []))
        (job_dir / "clusters.json").write_text(
            json.dumps(clusters, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- step5 + Gate + step6（第4步：整理相关经历） ----
        _update_progress(job_dir, meta, "正在生成瘦身简历…")
        _append_log(job_dir, "正在整理相关经历...", meta["started_at"])
        _t = time.time()
        changes = generate_changes(
            jd["requirements"], clusters, resume["experiences"],
            resume_id=resume_id,
            source_store=resume.get("source_store"),
            use_mock=False,
        )
        meta["timings"]["step5_rewrite"] = round(time.time() - _t, 1)
        meta["tokens"]["step5"] = get_last_usage()   # step5 在主线程跑，可直接读 thread-local
        (job_dir / "changes.json").write_text(
            json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- 环节G: Gate 校验（第4步：整理相关经历） ----
        _update_progress(job_dir, meta, "正在校验事实准确性…")
        _t = time.time()
        gate = validate_changes(changes["changes"], resume.get("source_store", {}))
        meta["timings"]["gate"] = round(time.time() - _t, 1)

        # ---- Gate 统计 ----
        gate_results = gate.get("results") or []
        gate_rejections = [
            {"output_id": r.get("output_id"), "reason": r.get("reason")}
            for r in gate_results if not r.get("passed")
        ]
        meta["gate"] = {
            "total": gate.get("total", 0),
            "passed": gate.get("passed", 0),
            "rejected": gate.get("rejected", 0),
            "rejections": gate_rejections,
        }
        (job_dir / "gate.json").write_text(
            json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- step6: 匹配报告（第4步：整理相关经历） ----
        _update_progress(job_dir, meta, "正在生成匹配报告…")
        _t = time.time()
        report = generate_report(scored, gate["passed_changes"], jd, resume)
        meta["timings"]["step6_report"] = round(time.time() - _t, 1)
        (job_dir / "report.md").write_text(report, encoding="utf-8")

        # ---- 完成 ----
        _append_log(job_dir, "整理完成", meta["started_at"])
        meta["status"] = "done"
        meta["progress"] = "已完成"
        meta["finished_at"] = time.time()
        meta["elapsed_seconds"] = round(meta["finished_at"] - meta["started_at"], 1)
        meta["timings"]["total"] = meta["elapsed_seconds"]

        # ---- Token 用量汇总（当前各步为 null，汇总也为 null）----
        _token_steps = [meta["tokens"].get(k) for k in ("step1", "step2", "step5") if meta["tokens"].get(k)]
        if _token_steps:
            meta["tokens"]["total"] = {
                k: sum(t.get(k, 0) for t in _token_steps) for k in ("prompt", "completion", "total")
            }
        else:
            meta["tokens"]["total"] = None
        _write_meta(job_dir, meta)

        return {
            "job_id": job_id,
            "status": "done",
            "elapsed_seconds": meta["elapsed_seconds"],
        }

    except Exception as e:
        meta["status"] = "failed"
        meta["error"] = f"{type(e).__name__}: {e}"
        meta["traceback"] = traceback.format_exc()
        meta["failed_at"] = time.time()
        _write_meta(job_dir, meta)
        raise