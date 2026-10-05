# -*- coding: utf-8 -*-
"""bge-m3 语义向量工具（纯 CPU 环境）。

- 模型：BAAI/bge-m3（首次运行自动下载，约 2GB，缓存于 HuggingFace 默认目录）
- CPU 环境务必 use_fp16=False
- 模型单例加载一次，全局复用（加载本身要几秒，编码 CPU 约 5-10 秒/批）

对外函数：
  encode_texts(texts)      -> np.ndarray，(n, 1024) 句向量
  cosine_sim_matrix(a, b)  -> np.ndarray，(len(a), len(b)) 余弦相似度矩阵
"""
import os

import numpy as np

# 国内直连 HuggingFace 经常超时，默认走 hf-mirror 镜像下载模型；
# 已显式设置 HF_ENDPOINT 时不覆盖（直连/自建镜像场景）。
# 必须在 import FlagEmbedding/huggingface_hub 之前设置。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 禁用 Xet 协议：新版 huggingface_hub 默认用 Xet 直连 cas-server.xethub.hf.co
# （不走 HF_ENDPOINT 镜像，国内 401），强制改走镜像的普通 /resolve/ HTTP 下载。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

_model = None
MODEL_ID = "BAAI/bge-m3"
# 只下载推理必需文件，排除 imgs/.DS_Store 等仓库杂项
# （hf-mirror 对 imgs/.DS_Store 返回 403，整仓下载会因此失败）
_ALLOW_PATTERNS = [
    "config.json",
    "sentence_bert_config.json",
    "config_sentence_transformers.json",
    "1_Pooling/config.json",
    "tokenizer*",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "modules.json",
    "pytorch_model.bin",   # bge-m3 权重是 .bin（该仓库没有 model.safetensors）
    "*.safetensors",       # 兼容未来仓库切换格式
    "*.pt",                # colbert_linear.pt / sparse_linear.pt，BGEM3FlagModel 初始化会读
]


def get_model():
    """懒加载 bge-m3 模型（全局单例）。首次自动按白名单下载到 HF 缓存。"""
    global _model
    if _model is None:
        # 延迟 import：只在真正用时才引入（导入本身会拉起 torch，较重）
        from FlagEmbedding import BGEM3FlagModel
        from huggingface_hub import snapshot_download
        local_path = snapshot_download(MODEL_ID, allow_patterns=_ALLOW_PATTERNS)
        # 注意：CPU 环境用 use_fp16=False
        _model = BGEM3FlagModel(local_path, use_fp16=False)
    return _model


def encode_texts(texts):
    """把一组文本编码成向量。返回 (n, 1024) 的 numpy 数组。"""
    model = get_model()
    return model.encode(list(texts))["dense_vecs"]


def cosine_sim_matrix(a, b):
    """a 和 b 的余弦相似度矩阵。返回 (len(a), len(b))。"""
    a_norm = a / np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return a_norm @ b_norm.T
