# -*- coding: utf-8 -*-
"""LLM Provider 抽象基类与共用异常。

所有后端（DeepSeek 云端 API、本地 Ollama 等）都实现 LLMProvider.chat，
上层只依赖这个统一接口，不感知具体后端差异。
"""
from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """LLM 调用失败（重试耗尽 / 服务未启动 / 未配置 Key / 返回不可解析）。"""


class LLMProvider(ABC):
    """LLM 后端统一接口。子类需设置 name 并实现 chat()。"""

    #: 后端标识名，如 "deepseek" / "ollama"
    name = "base"

    @abstractmethod
    def chat(self, system_prompt: str, user_prompt: str):
        """传入 system / user 两段 prompt，返回 (assistant 文本, usage)。

        :param system_prompt: 系统指令（prompt 模板正文）
        :param user_prompt:    用户输入（简历 / JD 等待处理文本）
        :return:               (text, usage) 元组；usage 为 {"prompt","completion","total"} 字典
                               或 None（provider 不支持用量统计时）
        """
        raise NotImplementedError
