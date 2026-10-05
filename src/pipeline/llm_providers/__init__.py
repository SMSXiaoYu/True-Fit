# -*- coding: utf-8 -*-
"""LLM Provider 包：按名称获取后端实例。

目前支持：
- deepseek：云端 DeepSeek API（OpenAI 兼容协议），需 DEEPSEEK_API_KEY
- ollama：  本地 Ollama（HTTP API，默认 http://localhost:11434）
"""
from .base import LLMError, LLMProvider

__all__ = ["LLMError", "LLMProvider", "get_provider"]


def get_provider(name="deepseek", model=None):
    """按名称返回 Provider 实例（懒导入，避免不用的后端拉起重依赖）。

    :param name:  "deepseek" 或 "ollama"
    :param model: 覆盖默认模型名；None 时各 Provider 读自己的默认值/环境变量
    """
    key = (name or "deepseek").strip().lower()
    if key == "deepseek":
        from .deepseek_provider import DeepSeekProvider
        return DeepSeekProvider(model=model)
    if key == "ollama":
        from .ollama_provider import OllamaProvider
        return OllamaProvider(model=model)
    raise LLMError(f"未知的 LLM provider：{name!r}，可选：deepseek / ollama")
