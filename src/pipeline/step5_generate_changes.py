# -*- coding: utf-8 -*-
"""调用5：改写指令生成（LLM）。

- prompt 读自 src/prompts/调用5_改写指令_v7.md（不在代码里硬编码）
- 输入：requirements JSON（JD）+ clusters JSON（用户/系统聚类）+ experiences JSON（简历经历）
        另需 source_store 作为 original_texts 传给 LLM
- 输出：{"changes": [...]}，落盘到 data/output/changes_{resume_id}.json
- 产品函数：generate_changes(requirements, clusters, experiences) -> dict
"""
import json
from pathlib import Path

try:  # 兼容「包导入」与「脚本直跑」两种方式
    from .llm_client import call_llm, load_prompt, parse_json_response
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from llm_client import call_llm, load_prompt, parse_json_response

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = "调用5_改写指令_v7.md"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

SYSTEM_PROMPT = "你是简历改写指令生成器，严格按 Schema 输出 JSON，不输出任何其他文字。"


def _build_user_prompt(requirements, clusters, experiences, source_store):
    """把四份输入拼成调用5 prompt 的数据区。"""
    blocks = [
        ("requirements（JD 核心要求）", requirements),
        ("clusters（聚类结果，含 group_id / group_title / member_ids / tier）", clusters),
        ("experiences（简历经历结构化数据）", experiences),
        ("original_texts（原文库，source_ids 只能引用这里的 key）", source_store or {}),
    ]
    parts = ["# 输入数据\n"]
    for title, payload in blocks:
        parts.append(f"## {title}\n")
        parts.append("```json")
        parts.append(json.dumps(payload, ensure_ascii=False, indent=2))
        parts.append("```\n")
    return "\n".join(parts)


def generate_changes(requirements, clusters, experiences, *,
                     resume_id="out", source_store=None,
                     use_mock=False, mock_path=None, llm_call=None):
    """生成改写指令 changes 数组。

    参数:
        requirements: JD requirements dict（jd["requirements"]）
        clusters:     聚类结果 dict（{"clusters": [...], "omitted_ids": [...]}）
        experiences:  简历经历列表（resume["experiences"]）
        resume_id:    简历 ID，决定落盘文件名 changes_{resume_id}.json
        source_store: 原文库 dict（resume["source_store"]），作为 original_texts
        use_mock:     True 时不调 LLM，直接读已有 changes 产物（离线测试用）
        mock_path:    use_mock=True 时自定义 JSON 路径
        llm_call:     可注入的 LLM 调用函数（测试用）
    返回:
        {"changes": [...]} dict
    """
    if use_mock:
        path = Path(mock_path) if mock_path else OUTPUT_DIR / f"changes_{resume_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    caller = llm_call or call_llm
    system_prompt = SYSTEM_PROMPT
    user_prompt = load_prompt(PROMPT_FILE) + "\n\n---\n\n" + _build_user_prompt(
        requirements, clusters, experiences, source_store
    )
    raw = caller(system_prompt, user_prompt)
    data = parse_json_response(raw)
    if not isinstance(data, dict) or "changes" not in data:
        raise ValueError("调用5 返回缺少 changes 字段")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"changes_{resume_id}.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    mock = generate_changes(None, None, None, resume_id="A", use_mock=True)
    print(f"mock 载入成功：changes {len(mock.get('changes', []))} 条")
