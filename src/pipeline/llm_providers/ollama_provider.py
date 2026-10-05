# -*- coding: utf-8 -*-
"""本地 Ollama Provider。

直接走 Ollama 自带 HTTP API（不依赖 ollama Python SDK，只需 requests）：
- 默认地址 http://localhost:11434
- 默认模型 qwen2.5:7b，构造参数 / 环境变量可覆盖
- Ollama 未启动 / 模型未拉取时给出可操作的中文报错
"""
import os

import requests

from .base import LLMError, LLMProvider

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
TIMEOUT_SECONDS = 180  # 本地 7B 在 CPU 上首 token/整段生成可能较慢，给足超时


def _resolve_host(host):
    """地址优先级：构造参数 > OLLAMA_BASE_URL > OLLAMA_HOST > 默认值。

    兼容官方 OLLAMA_HOST 习惯写法（127.0.0.1:11434，不带 http 前缀）。
    """
    raw = host or os.environ.get("OLLAMA_BASE_URL") or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw
    return raw.rstrip("/")


class OllamaProvider(LLMProvider):
    """本地 Ollama 后端。

    :param model:   模型名（默认 qwen2.5:7b，也可用环境变量 OLLAMA_MODEL 覆盖）
    :param host:    Ollama 服务地址（默认 http://localhost:11434）
    :param timeout: 单次请求超时秒数（本地 CPU 推理默认给 180s）
    """

    name = "ollama"

    def __init__(self, model=None, host=None, timeout=TIMEOUT_SECONDS):
        self.host = _resolve_host(host)
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """非流式调用 POST /api/chat，返回 message.content。"""
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
        except requests.exceptions.ConnectionError as err:
            raise LLMError(
                f"连不上本地 Ollama（{self.host}）。请确认已安装并启动服务：\n"
                "  ollama serve\n"
                f"如尚未拉取模型，另开一个终端执行：ollama pull {self.model}\n"
                f"原始错误：{err}"
            )
        except requests.exceptions.Timeout:
            raise LLMError(
                f"Ollama 调用超时（{self.timeout}s）。"
                f"本地模型 {self.model} 在 CPU 上推理可能过慢，可换更小的模型（如 qwen2.5:3b）。"
            )
        except requests.exceptions.RequestException as err:
            raise LLMError(f"Ollama 请求失败：{err}")

        if resp.status_code == 404:
            raise LLMError(
                f"本地没有模型 {self.model}（HTTP 404）。请先拉取：\n"
                f"  ollama pull {self.model}\n响应内容：{resp.text[:200]}"
            )
        if resp.status_code != 200:
            raise LLMError(f"Ollama 返回 HTTP {resp.status_code}：{resp.text[:300]}")

        try:
            data = resp.json()
            return data["message"]["content"]
        except ValueError as err:
            raise LLMError(f"Ollama 返回不是合法 JSON：{err}；原文前 200 字：{resp.text[:200]}")
        except KeyError as err:
            raise LLMError(f"Ollama 返回缺少字段 {err}；原文前 200 字：{resp.text[:200]}")
