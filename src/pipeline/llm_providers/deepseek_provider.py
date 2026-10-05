# -*- coding: utf-8 -*-
"""DeepSeek API Provider（OpenAI 兼容协议）。

逻辑从原 llm_client.py 原样搬入：
- openai SDK，base_url=https://api.deepseek.com，默认 model=deepseek-flash
- API Key 从 .env / 环境变量 DEEPSEEK_API_KEY 读取
- 内置 3 次指数退避重试、60 秒超时
"""
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from .base import LLMError, LLMProvider

ROOT = Path(__file__).resolve().parents[3]   # src/pipeline/llm_providers/ 上三级 = 项目根
# 独立使用本 Provider 时也能读到项目根 .env（已存在的真实环境变量优先，不被覆盖）
load_dotenv(ROOT / ".env")

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
TIMEOUT_SECONDS = 60
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # 指数退避基数（秒）：2, 4, 8

# .env 尚未替换模板值时的占位特征（中英文）
_PLACEHOLDER_MARKERS = ("your-key-here", "你的真实key", "你的key")


class DeepSeekProvider(LLMProvider):
    """云端 DeepSeek 后端。model 可用构造参数或环境变量 DEEPSEEK_MODEL 覆盖。"""

    name = "deepseek"

    def __init__(self, model=None, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES):
        self.model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self.max_retries = max_retries

    def _get_client(self):
        """构造 OpenAI 兼容客户端；缺少/未替换 API Key 时抛 LLMError。"""
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key or any(m in api_key.lower() for m in _PLACEHOLDER_MARKERS):
            raise LLMError(
                "未配置有效的 DEEPSEEK_API_KEY。请在项目根目录操作：\n"
                f"  1. 复制模板：{(ROOT / '.env.example')} → {(ROOT / '.env')}\n"
                "  2. 编辑 .env，把 DEEPSEEK_API_KEY 改成真实的 DeepSeek Key（sk- 开头）\n"
                "（也可直接设置同名环境变量；或改用本地后端 provider='ollama'）"
            )
        return OpenAI(api_key=api_key, base_url=BASE_URL, timeout=self.timeout)

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """调用 DeepSeek，返回 assistant 文本；失败按指数退避重试。"""
        client = self._get_client()
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                    timeout=self.timeout,
                )
                return resp.choices[0].message.content
            except Exception as err:  # SDK 异常 / 超时 / 网络错误统一重试
                last_err = err
                if attempt < self.max_retries:
                    time.sleep(RETRY_BACKOFF ** attempt)
        raise LLMError(f"DeepSeek 调用重试 {self.max_retries} 次后仍失败：{last_err}")
