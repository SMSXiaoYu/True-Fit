# -*- coding: utf-8 -*-
"""LLM 调用统一入口 + prompt 工具函数。

后端通过 Provider 抽象支持两种，按需切换：
- deepseek（默认）：云端 API，需在 .env 配 DEEPSEEK_API_KEY
- ollama：        本地模型，默认 http://localhost:11434、模型 qwen2.5:7b

provider 选择优先级（高 → 低）：
1. call_llm(..., provider="ollama") 函数参数
2. 环境变量 LLM_PROVIDER=ollama（写在项目根 .env 里即可）
3. 默认 deepseek

调用1/2/5 脚本只用 call_llm(system, user) 两个位置参数，接口完全不变。
"""
import json
import os
import re
import threading
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
# 启动时从项目根目录 .env 加载环境变量（已存在的真实环境变量优先，不会被 .env 覆盖）
load_dotenv(ROOT / ".env")

# 双模式导入：包导入（from src.pipeline.llm_client import ...）
# 与脚本直跑（python src/pipeline/llm_client.py）都要能用
try:
    from .llm_providers import get_provider
    from .llm_providers.base import LLMError
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from llm_providers import get_provider
    from llm_providers.base import LLMError

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

DEFAULT_PROVIDER = "deepseek"

# 线程局部用量存储：call_llm 把当前线程最近一次的 usage 存这里，
# run_upload 在同线程（或 worker 线程内）调 get_last_usage() 取出。
# 用 thread-local 是因为调用1、2 并行跑在不同线程，互不覆盖。
_thread_local = threading.local()


def call_llm(system_prompt, user_prompt, provider=None, model=None):
    """调用 LLM，返回 assistant 文本内容（usage 另存到线程局部，见 get_last_usage）。

    :param provider: "deepseek" 或 "ollama"；None 时读环境变量 LLM_PROVIDER，再缺省 deepseek
    :param model:    覆盖该后端的默认模型名（None 用各 Provider 默认值）

    重试 / 超时 / 报错口径由具体 Provider 负责。
    """
    name = provider or os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER)
    text, usage = get_provider(name, model=model).chat(system_prompt, user_prompt)
    _thread_local.last_usage = usage
    return text


def get_last_usage():
    """返回当前线程最近一次 call_llm 的 usage（{"prompt","completion","total"} 或 None）。

    必须在 call_llm 所在的同一线程内调用才能取到值（调用1、2 并行时各自的 worker 线程）。
    """
    return getattr(_thread_local, "last_usage", None)


# ============ 通用工具 ============

def load_prompt(filename):
    """读取 src/prompts/ 下的 prompt 文件，返回文本。"""
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"prompt 文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def inject_input(prompt_text, marker_patterns, payload):
    """把 payload 注入 prompt：优先替换占位括号，找不到占位符则追加到末尾。"""
    for pat in marker_patterns:
        new = re.sub(pat, lambda _m: payload, prompt_text, count=1)
        if new != prompt_text:
            return new
    return prompt_text + "\n\n" + payload


def parse_json_response(text):
    """从 LLM 返回文本中提取 JSON（剥离 ```json 围栏和前后说明文字），返回 dict/list。"""
    if text is None:
        raise LLMError("LLM 返回为空")
    t = text.strip()
    # 优先匹配 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.DOTALL)
    if m:
        t = m.group(1).strip()
    else:
        # 兜底：截取第一个 { 或 [ 到最后一个 } 或 ]
        start = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
        if start != -1:
            close_ch = "}" if t[start] == "{" else "]"
            end = t.rfind(close_ch)
            if end > start:
                t = t[start:end + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError as err:
        raise LLMError(f"LLM 返回不是合法 JSON：{err}\n原文前 200 字：{text[:200]}")
