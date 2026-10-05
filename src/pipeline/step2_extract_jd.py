# -*- coding: utf-8 -*-
"""调用2：JD 提炼（LLM）。

- prompt 读自 src/prompts/调用2_JD提炼_v4.md（不在代码里硬编码）
- 输入：JD 纯文本 + jd_id
- 输出：完整 requirements JSON，并落盘到 data/extracted/jds/{jd_id}.json
- 产品函数：extract_jd(jd_text, jd_id) -> dict
"""
import json
from pathlib import Path

try:  # 兼容「包导入」与「脚本直跑」两种方式
    from .llm_client import call_llm, load_prompt, inject_input, parse_json_response
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from llm_client import call_llm, load_prompt, inject_input, parse_json_response

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = "调用2_JD提炼_v4.md"
OUTPUT_DIR = PROJECT_ROOT / "data" / "extracted" / "jds"

SYSTEM_PROMPT = "你是一个严谨的 JD（职位描述）结构化提取器，只输出 JSON，不输出任何其他文字。"
# prompt 中“（把 JD的原文贴在这里）”占位行的匹配模式
_PLACEHOLDER_PATTERNS = [r"（把\s*JD[^）]*原文[^）]*贴在这里）", r"\(把\s*JD[^)]*原文[^)]*贴在这里\)"]


def extract_jd(jd_text, jd_id, *, use_mock=False, mock_path=None, llm_call=None):
    """把 JD 纯文本提炼为结构化 JSON。

    参数:
        jd_text:  JD 纯文本
        jd_id:    JD ID（如 JD1_backend），决定落盘文件名
        use_mock: True 时不调 LLM，直接读已有的提炼产物（离线测试用）
        mock_path: use_mock=True 时自定义 JSON 路径，默认 data/extracted/jds/{id}.json
        llm_call: 可注入的 LLM 调用函数（测试用）
    返回:
        提炼后的完整 dict（含 requirements 等）
    """
    if use_mock:
        path = Path(mock_path) if mock_path else OUTPUT_DIR / f"{jd_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    caller = llm_call or call_llm
    prompt = load_prompt(PROMPT_FILE)
    user_prompt = inject_input(prompt, _PLACEHOLDER_PATTERNS, jd_text.strip())
    raw = caller(SYSTEM_PROMPT, user_prompt)
    data = parse_json_response(raw)

    if isinstance(data, dict):
        data["jd_id"] = data.get("jd_id") or jd_id

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{jd_id}.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    demo = extract_jd("", "JD1_backend", use_mock=True)
    req = demo.get("requirements", {})
    print(f"mock 载入成功：jd_id={demo.get('jd_id')}，"
          f"skills={len(req.get('skills', []))} 项，"
          f"responsibilities={len(req.get('responsibilities', []))} 条")
