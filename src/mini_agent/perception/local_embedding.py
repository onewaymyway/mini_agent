"""
perception/local_embedding.py — 本地离线 embedding 推理（方案一）

只有 MemoryConfig.embedding_enabled=True 时，memory_factory.py 才会
import 本模块；本模块内部再对 onnxruntime/tokenizers 做延迟 import，
双重延迟保证"关闭开关=零依赖引入"（模块顶层不 import 任何推理依赖）。

不调用任何云端 API，模型权重（ONNX，几十 MB）运行时按需从 Hugging Face
下载到本地缓存目录，之后离线复用。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional


_BUILTIN_MODELS = {
    "bge-small-zh-v1.5": {
        "repo_id": "BAAI/bge-small-zh-v1.5",
        "onnx_file": "onnx/model_quantized.onnx",   # INT8 量化版本
        "dim": 512,
    },
    "embedding-gemma-300m": {
        "repo_id": "google/embeddinggemma-300m",
        "onnx_file": "onnx/model_int4.onnx",
        "dim": 768,   # 支持 MRL 截断到 128/256/512
    },
}


def _resolve_model_source(model_name: str) -> dict:
    """内置候选名 → repo 信息；否则视为用户自定义本地路径。"""
    if model_name in _BUILTIN_MODELS:
        return dict(_BUILTIN_MODELS[model_name])
    # 用户自定义本地路径：把 model_name 本身当作模型文件所在目录
    return {"repo_id": None, "onnx_file": None, "dim": None, "local_path": model_name}


class LocalEmbeddingModel:
    """
    包装 ONNX Runtime 推理 + 分词，提供 embed(text: str) -> list[float]。

    首次调用时：
      1. 检查 cache_dir 下模型文件是否存在，不存在则从 Hugging Face 下载
         （下载失败/无网络：抛出异常，由调用方 HybridMemoryBackend 捕获后
         整体降级为纯 TF-IDF，不影响记忆检索可用性）。
      2. 用 onnxruntime.InferenceSession 加载模型（CPU provider）。
      3. 用 tokenizers 库加载对应分词器配置。

    线程/进程安全：InferenceSession 本身线程安全，多个调用方可共享同一个
    LocalEmbeddingModel 单例（见 get_shared_embedding_model()）。
    """

    def __init__(self, model_name: str, cache_dir: Optional[Path] = None):
        self._model_name = model_name
        self._cache_dir = cache_dir or (Path.home() / ".agent" / "models")
        self._session = None   # 懒加载
        self._tokenizer = None
        self._source = _resolve_model_source(model_name)

    def embed(self, text: str) -> list[float]:
        self._ensure_loaded()
        tokens = self._tokenizer.encode(text)
        input_ids = tokens.ids if hasattr(tokens, "ids") else tokens
        attention_mask = getattr(tokens, "attention_mask", None) or [1] * len(input_ids)

        import numpy as np

        input_ids_arr = np.array([input_ids], dtype=np.int64)
        attention_mask_arr = np.array([attention_mask], dtype=np.int64)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids_arr,
                "attention_mask": attention_mask_arr,
            },
        )
        last_hidden = outputs[0][0]   # (seq_len, dim)

        # mean pooling（按 attention_mask 加权）
        mask = np.array(attention_mask, dtype=np.float32).reshape(-1, 1)
        summed = (last_hidden * mask).sum(axis=0)
        counted = max(mask.sum(), 1e-9)
        pooled = summed / counted

        # L2 normalize
        norm = math.sqrt(float((pooled * pooled).sum())) or 1.0
        return (pooled / norm).tolist()

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort   # 延迟 import，见模块文档
        from tokenizers import Tokenizer

        model_dir = self._download_if_needed()
        onnx_path = model_dir / (self._source.get("onnx_file") or "model.onnx")
        tokenizer_path = model_dir / "tokenizer.json"

        self._session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))

    def _download_if_needed(self) -> Path:
        """确保模型文件在本地缓存目录，返回该目录路径。"""
        local_path = self._source.get("local_path")
        if local_path:
            return Path(local_path)

        repo_id = self._source["repo_id"]
        target_dir = self._cache_dir / self._model_name
        target_dir.mkdir(parents=True, exist_ok=True)

        onnx_file = self._source["onnx_file"]
        onnx_target = target_dir / onnx_file
        tokenizer_target = target_dir / "tokenizer.json"

        if onnx_target.exists() and tokenizer_target.exists():
            return target_dir

        from huggingface_hub import hf_hub_download

        downloaded_onnx = hf_hub_download(repo_id=repo_id, filename=onnx_file)
        downloaded_tokenizer = hf_hub_download(repo_id=repo_id, filename="tokenizer.json")

        import shutil
        onnx_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(downloaded_onnx, onnx_target)
        shutil.copy(downloaded_tokenizer, tokenizer_target)
        return target_dir


_instance_cache: dict[str, "LocalEmbeddingModel"] = {}   # 进程内单例缓存，按 model_name 复用


def get_shared_embedding_model(model_name: str, cache_dir: Optional[Path] = None) -> "LocalEmbeddingModel":
    """进程内单例：多个调用方共享同一份加载好的模型。"""
    key = f"{model_name}:{cache_dir}"
    if key not in _instance_cache:
        _instance_cache[key] = LocalEmbeddingModel(model_name, cache_dir)
    return _instance_cache[key]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """简单的 numpy-free 余弦相似度（向量已 L2 归一化时等价于点积，
    但为了健壮性这里不假设输入已归一化）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)


__all__ = [
    "LocalEmbeddingModel",
    "get_shared_embedding_model",
    "cosine_similarity",
]
