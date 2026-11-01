"""providers/skill_providers.py — 图像与视频 provider：内部调用 mini_agent 的 Agnes skill。

对外保持原 ImageProvider / VideoProvider 接口与 ImageResult / VideoResult 结构不变，
调用方（G-3 定妆图、C-8 小场景视频）无需改动。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .image import ImageProvider, ImageResult
from .skill_bridge import build_pool, modules
from .video import VideoProvider, VideoResult

IMAGE_TIERS = ("1K", "2K", "3K", "4K")


def _image_tier(size: str) -> str:
    """skill 推荐档位式尺寸（配合 ratio）。精确尺寸（如 1024x1024）统一映射为 1K。"""
    return size if size in IMAGE_TIERS else "1K"


def _failure(result: dict) -> tuple:
    err = result.get("error")
    non_retryable = bool(result.get("non_retryable")) or (isinstance(err, dict) and bool(err.get("non_retryable")))
    text = err if isinstance(err, str) else str(err)[:300] if err is not None else "未知错误"
    return text, non_retryable


class SkillImageProvider(ImageProvider):
    name = "agnes-skill-image"

    def __init__(self, pool=None, *, model: Optional[str] = None):
        pool = pool or build_pool()
        if pool is None:
            raise RuntimeError("no-key")
        self.client = modules()["image"].AgnesImageClient(key_pool=pool, model=model)

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 size: str = "1024x1024") -> ImageResult:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = self.client.generate_image(prompt=prompt, size=_image_tier(size), ratio=ratio,
                                                save_path=str(save_path))
        except Exception as exc:  # 网络等底层异常也要包装成结果，避免中断整批任务
            return ImageResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        if result.get("success") and save_path.is_file():
            return ImageResult(ok=True, path=str(save_path))
        text, non_retryable = _failure(result)
        return ImageResult(ok=False, error=text, non_retryable=non_retryable)


class SkillVideoProvider(VideoProvider):
    name = "agnes-skill-video"

    def __init__(self, pool=None):
        pool = pool or build_pool()
        if pool is None:
            raise RuntimeError("no-key")
        self.client = modules()["video"].AgnesVideoClient(key_pool=pool)

    def generate(self, prompt: str, save_path: Path, *, seconds: int, reference_images: List[Path],
                 aspect_ratio: str = "9:16") -> VideoResult:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        images = [str(p) for p in reference_images] or None
        try:
            result = self.client.generate_video(
                prompt=prompt,
                mode="reference" if images else "text",
                seconds=str(int(seconds)),
                aspect_ratio=aspect_ratio,
                images=images,
                save_path=str(save_path),
            )
        except Exception as exc:
            return VideoResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        if result.get("success") and save_path.is_file():
            return VideoResult(ok=True, path=str(save_path))
        text, non_retryable = _failure(result)
        return VideoResult(ok=False, error=text, non_retryable=non_retryable)
