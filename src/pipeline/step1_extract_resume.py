# -*- coding: utf-8 -*-
"""调用1：简历提炼（LLM）。

- prompt 读自 src/prompts/调用1_简历提炼_v8.md（不在代码里硬编码）
- 输入：简历纯文本 + resume_id
- 输出：完整 experiences JSON，并落盘到 data/extracted/resumes/{resume_id}.json
- 产品函数：extract_resume(resume_text, resume_id) -> dict
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
PROMPT_FILE = "调用1_简历提炼_v8.md"
OUTPUT_DIR = PROJECT_ROOT / "data" / "extracted" / "resumes"

SYSTEM_PROMPT = "你是一个严谨的简历结构化提取器，只输出 JSON，不输出任何其他文字。"
# prompt 中“（把简历A的纯文本贴在这里）”占位行的匹配模式
_PLACEHOLDER_PATTERNS = [r"（把简历[^）]*贴在这里）", r"\(把简历[^)]*贴在这里\)"]


def extract_resume(resume_text, resume_id, *, use_mock=False, mock_path=None, llm_call=None):
    """把简历纯文本提炼为结构化 JSON。

    参数:
        resume_text: 简历纯文本
        resume_id:   简历 ID（如 A_backend_5y），决定落盘文件名
        use_mock:    True 时不调 LLM，直接读已有的提炼产物（离线测试用）
        mock_path:   use_mock=True 时自定义 JSON 路径，默认 data/extracted/resumes/{id}.json
        llm_call:    可注入的 LLM 调用函数（测试用），默认用 llm_client.call_llm
    返回:
        提炼后的完整 dict（含 experiences / source_store 等）
    """
    if use_mock:
        path = Path(mock_path) if mock_path else OUTPUT_DIR / f"{resume_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    caller = llm_call or call_llm
    prompt = load_prompt(PROMPT_FILE)
    user_prompt = inject_input(prompt, _PLACEHOLDER_PATTERNS, resume_text.strip())
    raw = caller(SYSTEM_PROMPT, user_prompt)
    data = parse_json_response(raw)

    # 保证 resume_id 与传入一致
    if isinstance(data, dict):
        data["resume_id"] = data.get("resume_id") or resume_id

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{resume_id}.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    # 离线自测：用已有提炼产物走 mock 分支
    demo = extract_resume("", "A_backend_5y", use_mock=True)
    print(f"mock 载入成功：resume_id={demo.get('resume_id')}，"
          f"experiences={len(demo.get('experiences', []))} 段")
