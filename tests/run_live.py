# -*- coding: utf-8 -*-
"""真实 API 跑一次完整 pipeline（复用/调试用）。

用法（在项目根目录）：
    python tests/run_live.py

说明：
- use_mock=False：调用1/2/5 真实请求 DeepSeek（API Key 从项目根 .env 读取）
- 产物用 *_live 命名，不覆盖 data/ 下的基准数据（A_backend_5y / JD1_backend / changes_A）
- 调用3打分/调用4分层/环节G/调用6报告 仍是纯规则，不产生 API 费用
"""
import json
import sys
import time
from pathlib import Path

# 解决 import：直接 `python tests/run_live.py` 时 sys.path[0] 是 tests/，
# 顶层包 src 不在路径里。把项目根（tests/ 的上一级）插到 sys.path 最前面。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.pipeline.run_pipeline import run  # noqa: E402

# ===== 可调参数（调试时改这里即可）=====
RESUME_ID = "A_live"
JD_ID = "JD1_live"
RESUME_MD = PROJECT_ROOT / "data" / "original" / "resumes" / "A_backend_5y.md"
JD_MD = PROJECT_ROOT / "data" / "original" / "jds" / "JD1_backend.md"
CLUSTERS_JSON = PROJECT_ROOT / "data" / "output" / "clusters_A.json"


def main():
    resume_text = RESUME_MD.read_text(encoding="utf-8")
    jd_text = JD_MD.read_text(encoding="utf-8")
    clusters = json.loads(CLUSTERS_JSON.read_text(encoding="utf-8"))

    print("=" * 72)
    print(f"真实 API pipeline：{RESUME_ID} × {JD_ID}（use_mock=False）")
    print("=" * 72)
    t0 = time.time()

    result = run(
        resume_text, jd_text,
        resume_id=RESUME_ID,
        jd_id=JD_ID,
        clusters=clusters,
        use_mock=False,
    )

    elapsed = time.time() - t0
    gate = result["gate"]
    scored = result["scored"]

    print("-" * 72)
    print(f"分层分布：{scored['distribution']}")
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
    print("-" * 72)
    print(f"耗时 {elapsed:.1f} 秒")
    print("产物：")
    print(f"  {PROJECT_ROOT / 'data' / 'extracted' / 'resumes' / f'{RESUME_ID}.json'}")
    print(f"  {PROJECT_ROOT / 'data' / 'extracted' / 'jds' / f'{JD_ID}.json'}")
    print(f"  {PROJECT_ROOT / 'data' / 'output' / f'changes_{RESUME_ID}.json'}")
    print(f"  {result['report_path']}")


if __name__ == "__main__":
    main()
