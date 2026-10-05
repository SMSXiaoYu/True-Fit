# -*- coding: utf-8 -*-
"""TrueFit 简旨 · Pipeline 主流程串联。

环节顺序：
  step1 简历提炼 → step2 JD提炼 → step3 打分 → step4 分层
  →（用户/系统聚类 clusters）→ step5 改写指令 → 环节G Gate 校验 → step6 匹配报告

入口函数：
  run(resume_text, jd_text, resume_id, jd_id, clusters, use_mock=False) -> dict

use_mock=True 时跳过全部 LLM 调用，改读 data/ 下已有提炼产物，
用于离线验证打分/分层/Gate/报告这些纯规则环节。
"""
import json
from pathlib import Path

try:  # 兼容「包导入」与「脚本直跑」两种方式
    from .step1_extract_resume import extract_resume
    from .step2_extract_jd import extract_jd
    from .step3_score import score_experiences
    from .step4_tier import tier_experiences
    from .step5_generate_changes import generate_changes
    from .stepG_gate import validate_changes
    from .step6_report import generate_report
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from step1_extract_resume import extract_resume
    from step2_extract_jd import extract_jd
    from step3_score import score_experiences
    from step4_tier import tier_experiences
    from step5_generate_changes import generate_changes
    from stepG_gate import validate_changes
    from step6_report import generate_report

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_OUTPUT = PROJECT_ROOT / "data" / "output"


def _groups_from_clusters(clusters):
    """clusters JSON → {group_id: [member_exp_id...]}，供报告“改动说明”配对经历名。"""
    return {g["group_id"]: g.get("member_ids", []) for g in clusters.get("clusters", [])}


def run(resume_text, jd_text, resume_id, jd_id, clusters, *, use_mock=False, mock_paths=None):
    """跑完整条 pipeline。

    参数:
        resume_text: 简历纯文本（use_mock=True 时可传空串）
        jd_text:     JD 纯文本（use_mock=True 时可传空串）
        resume_id:   简历 ID（如 A / A_backend_5y）
        jd_id:       JD ID（如 JD1_backend）
        clusters:    聚类结果 dict {"clusters": [...], "omitted_ids": [...]}
        use_mock:    True 时 LLM 环节全部读本地已有产物，不调 DeepSeek
        mock_paths:  use_mock=True 时可选的显式产物路径，形如
                     {"resume": Path, "jd": Path, "changes": Path}
    返回:
        {
          "resume": 提炼后简历 dict,
          "jd": 提炼后 JD dict,
          "scored": step3+4 产出（含 rows / distribution / groups）,
          "changes": step5 产出 {"changes": [...]},
          "gate": 环节G 校验结果（通过/拦截明细）,
          "report": 匹配报告 Markdown 字符串,
          "report_path": 报告落盘路径
        }
    """
    mock_paths = mock_paths or {}

    # ---- 调用1 / 调用2：LLM 提炼（mock 时读已有 JSON）----
    resume = extract_resume(resume_text, resume_id, use_mock=use_mock,
                            mock_path=mock_paths.get("resume"))
    jd = extract_jd(jd_text, jd_id, use_mock=use_mock,
                    mock_path=mock_paths.get("jd"))

    # ---- 调用3：四维度打分 ----
    declared_hard = (resume.get("skills_declared") or {}).get("hard", [])
    scored = score_experiences(
        resume["experiences"], jd["requirements"], skills_declared_hard=declared_hard
    )

    # ---- 调用4：分层 ----
    tier_experiences(scored)

    # ---- 用户/系统聚类：挂到 scored 上供报告使用 ----
    scored["groups"] = _groups_from_clusters(clusters)

    # ---- 调用5：改写指令生成（LLM；mock 时读 data/output/changes_{resume_id}.json）----
    changes = generate_changes(
        jd["requirements"], clusters, resume["experiences"],
        resume_id=resume_id, source_store=resume.get("source_store"),
        use_mock=use_mock, mock_path=mock_paths.get("changes"),
    )

    # ---- 环节G：四道门禁校验 ----
    gate = validate_changes(changes["changes"], resume.get("source_store", {}))

    # ---- 调用6：匹配报告（基于 Gate 通过的 changes）----
    report = generate_report(scored, gate["passed_changes"], jd, resume)
    DATA_OUTPUT.mkdir(parents=True, exist_ok=True)
    report_path = DATA_OUTPUT / f"report_{resume_id}.md"
    report_path.write_text(report, encoding="utf-8")

    return {
        "resume": resume,
        "jd": jd,
        "scored": scored,
        "changes": changes,
        "gate": gate,
        "report": report,
        "report_path": str(report_path),
    }


def _demo_mock():
    """离线自测：用 A_backend_5y × JD1_backend 的已有产物跑规则环节。"""
    data_extracted = PROJECT_ROOT / "data" / "extracted"
    clusters = json.loads((DATA_OUTPUT / "clusters_A.json").read_text(encoding="utf-8"))
    resume_md = (PROJECT_ROOT / "data" / "original" / "resumes" / "A_backend_5y.md")
    jd_md = (PROJECT_ROOT / "data" / "original" / "jds" / "JD1_backend.md")
    # 既有产物命名：提炼为 A_backend_5y/JD1_backend，changes 为 changes_A.json
    mock_paths = {
        "resume": data_extracted / "resumes" / "A_backend_5y.json",
        "jd": data_extracted / "jds" / "JD1_backend.json",
        "changes": DATA_OUTPUT / "changes_A.json",
    }
    result = run(
        resume_md.read_text(encoding="utf-8") if resume_md.exists() else "",
        jd_md.read_text(encoding="utf-8") if jd_md.exists() else "",
        resume_id="A", jd_id="JD1_backend", clusters=clusters,
        use_mock=True, mock_paths=mock_paths,
    )

    scored = result["scored"]
    gate = result["gate"]
    print("=" * 72)
    print("Pipeline mock 跑通（不调用任何 LLM）")
    print("=" * 72)
    print(f"经历数：{len(scored['rows'])} ｜ 分层分布：{scored['distribution']}")
    print(f"changes：{len(result['changes']['changes'])} 条 ｜ "
          f"Gate 通过 {gate['passed']}/{gate['total']}，拦截 {gate['rejected']}")
    for r in gate["results"]:
        flag = "✅" if r["passed"] else "❌"
        extra = f"（{r['reason']}）" if r["reason"] else ""
        print(f"  {flag} {r['output_id']} {r['group_id']}{extra}")
    print("-" * 72)
    print("报告前 6 行预览：")
    for line in result["report"].splitlines()[:6]:
        print("  " + line)
    print(f"\n完整报告已写入: {result['report_path']}")
    return result


if __name__ == "__main__":
    _demo_mock()
