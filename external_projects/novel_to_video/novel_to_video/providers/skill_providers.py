"""providers/skill_providers.py — 图像与视频 provider：内部调用 mini_agent 的 Agnes skill。

对外保持原 ImageProvider / VideoProvider 接口与 ImageResult / VideoResult 结构不变，
调用方（G-3 定妆图、C-8 小场景视频）无需改动。
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import List, Optional

from .. import joblog

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
        tier = _image_tier(size)
        joblog.bound_log(f"图像接口请求：档位 {tier}，比例 {ratio or '默认'}，模型 {self.client.model}")
        t0 = time.monotonic()
        try:
            with contextlib.redirect_stdout(joblog.console_tee()):  # skill 的重试与失败信息写入日志
                result = self.client.generate_image(prompt=prompt, size=tier, ratio=ratio,
                                                    save_path=str(save_path))
        except Exception as exc:  # 网络等底层异常也要包装成结果，避免中断整批任务
            joblog.bound_log(f"图像接口异常：{type(exc).__name__}: {exc}", level="error")
            return ImageResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        joblog.bound_log(f"图像接口返回，用时 {time.monotonic() - t0:.1f} 秒")
        if result.get("success") and save_path.is_file():
            return ImageResult(ok=True, path=str(save_path))
        text, non_retryable = _failure(result)
        return ImageResult(ok=False, error=text, non_retryable=non_retryable)


DEFAULT_VIDEO_CFG = {"max_wait_sec": 3600, "max_retries": 5, "queue_wait_sec": 3600, "poll_interval_sec": 5}


class SkillVideoProvider(VideoProvider):
    name = "agnes-skill-video"

    def __init__(self, pool=None, video_cfg=None):
        pool = pool or build_pool()
        if pool is None:
            raise RuntimeError("no-key")
        cfg = {**DEFAULT_VIDEO_CFG, **{k: v for k, v in (video_cfg or {}).items() if v is not None}}
        self.max_wait_sec = int(cfg["max_wait_sec"])     # 轮询等待视频完成的上限（秒）
        self.poll_interval_sec = float(cfg.get("poll_interval_sec") or 5.0)   # 轮询间隔
        self.client = modules()["video"].AgnesVideoClient(
            key_pool=pool, max_retries=int(cfg["max_retries"]), queue_wait_seconds=int(cfg["queue_wait_sec"]))

    def generate(self, prompt: str, save_path: Path, *, seconds: int, reference_images: List[Path],
                 aspect_ratio: str = "16:9", size: str = "720P") -> VideoResult:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        images = [str(p) for p in reference_images] or None
        joblog.bound_log(f"视频接口请求：{seconds} 秒，比例 {aspect_ratio}，"
                         f"{'参考图 ' + str(len(images)) + ' 张' if images else '纯文本'}（生成通常需要数分钟）")
        t0 = time.monotonic()
        try:
            with contextlib.redirect_stdout(joblog.console_tee()):
                result = self.client.generate_video(
                prompt=prompt,
                mode="reference" if images else "text",
                seconds=str(int(seconds)),
                size=size,
                aspect_ratio=aspect_ratio,
                poll_interval=self.poll_interval_sec,
                max_wait_seconds=self.max_wait_sec,
                images=images,
                save_path=str(save_path),
            )
        except Exception as exc:
            joblog.bound_log(f"视频接口异常：{type(exc).__name__}: {exc}", level="error")
            return VideoResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        joblog.bound_log(f"视频接口返回，用时 {time.monotonic() - t0:.1f} 秒")
        if result.get("success") and save_path.is_file():
            return VideoResult(ok=True, path=str(save_path))
        text, non_retryable = _failure(result)
        return VideoResult(ok=False, error=text, non_retryable=non_retryable)
