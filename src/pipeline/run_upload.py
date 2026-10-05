# -*- coding: utf-8 -*-
"""上传任务的 pipeline 执行入口。

跟 run_pipeline.run() 的区别：
- run() 面向"离线跑已有数据"，接收文本 + clusters，一次跑完
- run_upload() 面向"用户上传"，自己构造 clusters（每 E 一个 G），
  且每步更新进度到 meta.json

流程：step1 → step2 → step3 → step4 → 构造 clusters → step5 → Gate → step6
"""
import json
import time
import traceback
from pathlib import Path

try:
    from .step1_extract_resume import extract_resume
    from .step2_extract_jd import extract_jd
    from .step3_score import score_experiences
    from .step4_tier import tier_experiences
    from .step5_generate_changes import generate_changes
    from .stepG_gate import validate_changes
    from .step6_report import generate_report
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


def run_upload(resume_text: str, jd_text: str, job_id: str) -> dict:
    """跑一次上传任务。**这个函数是同步的，由 server.py 放到后台线程里跑。**

    参数:
        resume_text: 简历纯文本（已从 PDF/Word 解析出）
        jd_text:     JD 纯文本
        job_id:      任务 ID（由 server.py 生成），产物写入 data/jobs/{job_id}/
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
    }
    _write_meta(job_dir, meta)

    resume_id = job_id   # 复用 job_id 作为 resume_id，产物不污染 data/extracted/
    jd_id = job_id

    try:
        # ---- step1: 简历提炼 ----
        _update_progress(job_dir, meta, "正在分析简历…")
        resume = extract_resume(resume_text, resume_id, use_mock=False)
        (job_dir / "resume.json").write_text(
            json.dumps(resume, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- step2: JD 提炼 ----
        _update_progress(job_dir, meta, "正在分析岗位要求…")
        jd = extract_jd(jd_text, jd_id, use_mock=False)
        (job_dir / "jd.json").write_text(
            json.dumps(jd, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- step3: 打分 ----
        _update_progress(job_dir, meta, "正在评估经历匹配度…")
        declared_hard = (resume.get("skills_declared") or {}).get("hard", [])
        scored = score_experiences(
            resume["experiences"], jd["requirements"],
            skills_declared_hard=declared_hard,
        )

        # ---- step4: 分层 ----
        _update_progress(job_dir, meta, "正在整理经历层级…")
        tier_experiences(scored)
        (job_dir / "scored.json").write_text(
            json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- 构造 clusters（每 E 一个 G）----
        clusters = _build_atomic_clusters(resume["experiences"], scored.get("rows", []))
        (job_dir / "clusters.json").write_text(
            json.dumps(clusters, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- step5: 改写指令 ----
        _update_progress(job_dir, meta, "正在生成瘦身简历…")
        changes = generate_changes(
            jd["requirements"], clusters, resume["experiences"],
            resume_id=resume_id,
            source_store=resume.get("source_store"),
            use_mock=False,
        )
        (job_dir / "changes.json").write_text(
            json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- 环节G: Gate 校验 ----
        _update_progress(job_dir, meta, "正在校验事实准确性…")
        gate = validate_changes(changes["changes"], resume.get("source_store", {}))
        (job_dir / "gate.json").write_text(
            json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- step6: 匹配报告 ----
        _update_progress(job_dir, meta, "正在生成匹配报告…")
        report = generate_report(scored, gate["passed_changes"], jd, resume)
        (job_dir / "report.md").write_text(report, encoding="utf-8")

        # ---- 完成 ----
        meta["status"] = "done"
        meta["progress"] = "已完成"
        meta["finished_at"] = time.time()
        meta["elapsed_seconds"] = round(meta["finished_at"] - meta["started_at"], 1)
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