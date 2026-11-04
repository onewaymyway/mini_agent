"""providers/skill_providers.py — 图像与视频 provider：内部调用 mini_agent 的 Agnes skill。

对外保持原 ImageProvider / VideoProvider 接口与 ImageResult / VideoResult 结构不变，
调用方（G-3 定妆图、C-8 小场景视频）无需改动。
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import List, Optional

from .. import cancel, joblog

from .image import ImageProvider, ImageResult
from .skill_bridge import build_pool, modules
from .video import VideoProvider, VideoResult

IMAGE_TIERS = ("1K", "2K", "3K", "4K")


def _image_tier(size: str) -> str:
    """skill 推荐档位式尺寸（配合 ratio）。精确尺寸（如 1024x1024）统一映射为 1K。"""
    return size if size in IMAGE_TIERS else "1K"


def _readable_error(err) -> str:
    """把 skill 返回的嵌套错误整理成可读文本。

    skill 失败时的结构形如 {"success": False, "error": {"status_code": 403, "error": "<服务端原文 JSON>"}}，
    直接 str() 会得到一长串带花括号的 repr，真正的原因（服务端 message）淹没其中。
    这里逐层解开，输出 “HTTP 403：<服务端 message>”；解析不了就退回截断后的原文。
    """
    import json as _json
    status = None
    depth = 0
    while isinstance(err, dict) and depth < 4:
        status = err.get("status_code", status)
        if "error" not in err:
            break
        err, depth = err["error"], depth + 1
    msg = err
    if isinstance(err, str):
        try:
            body = _json.loads(err)
            inner = body.get("error", body) if isinstance(body, dict) else body
            msg = (inner.get("message") or inner.get("error") or err) if isinstance(inner, dict) else err
        except ValueError:
            msg = err
    text = " ".join(str(msg if msg is not None else "未知错误").split())
    if len(text) > 300:
        text = text[:300] + "…"
    return f"HTTP {status}：{text}" if status else text


def _failure(result: dict) -> tuple:
    err = result.get("error")
    non_retryable = bool(result.get("non_retryable")) or (isinstance(err, dict) and bool(err.get("non_retryable")))
    text = _readable_error(err) if err is not None else "未知错误"
    if result.get("key_restricted") or (isinstance(err, dict) and err.get("key_restricted")):
        text = ("所有 API key 所在账号都已受限（账号异常 / 访问受限），无法继续生成。"
                "请检查账号状态（如联系平台支持），或在 AGNES_API_KEYS 中补充可用的 key 后重新运行。原始信息：" + text[:200])
    return text, non_retryable


class SkillImageProvider(ImageProvider):
    name = "agnes-skill-image"

    def __init__(self, pool=None, *, model: Optional[str] = None, image_cfg: Optional[dict] = None):
        pool = pool or build_pool()
        if pool is None:
            raise RuntimeError("no-key")
        # image_cfg 来自项目配置的 image 块；use_system_proxy 默认 False（直连）
        self.use_system_proxy = bool((image_cfg or {}).get("use_system_proxy", False))
        self.client = modules()["image"].AgnesImageClient(key_pool=pool, model=model,
                                                          use_system_proxy=self.use_system_proxy)

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 size: str = "1024x1024") -> ImageResult:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        tier = _image_tier(size)
        joblog.bound_log(f"图像接口请求：档位 {tier}，比例 {ratio or '默认'}，模型 {self.client.model}")
        t0 = time.monotonic()
        try:
            with contextlib.redirect_stdout(joblog.console_tee()):  # skill 的重试与失败信息写入日志
                # 经 cancel.call：用户停止时立即返回，不等待图像接口
                result = cancel.call(self.client.generate_image, prompt=prompt, size=tier, ratio=ratio,
                                     save_path=str(save_path))
        except Exception as exc:  # 网络等底层异常也要包装成结果，避免中断整批任务
            joblog.bound_log(f"图像接口异常：{type(exc).__name__}: {exc}", level="error")
            return ImageResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        joblog.bound_log(f"图像接口返回，用时 {time.monotonic() - t0:.1f} 秒")
        if result.get("success") and save_path.is_file():
            return ImageResult(ok=True, path=str(save_path))
        text, non_retryable = _failure(result)
        return ImageResult(ok=False, error=text, non_retryable=non_retryable)


# use_system_proxy 默认 False：视频创建、查询与下载都不读取系统代理（环境变量与 Windows 注册表）。
# 系统代理若不允许访问视频下载域名，会出现 403；需要时在挡位页勾选“使用系统代理”。
DEFAULT_VIDEO_CFG = {"max_wait_sec": 3600, "max_retries": 5, "queue_wait_sec": 3600,
                     "poll_interval_sec": 5, "use_system_proxy": False}


class SkillVideoProvider(VideoProvider):
    name = "agnes-skill-video"

    def __init__(self, pool=None, video_cfg=None):
        pool = pool or build_pool()
        if pool is None:
            raise RuntimeError("no-key")
        cfg = {**DEFAULT_VIDEO_CFG, **{k: v for k, v in (video_cfg or {}).items() if v is not None}}
        self.max_wait_sec = int(cfg["max_wait_sec"])     # 轮询等待视频完成的上限（秒）
        self.poll_interval_sec = float(cfg.get("poll_interval_sec") or 5.0)   # 轮询间隔
        self.use_system_proxy = bool(cfg.get("use_system_proxy", False))
        self.client = modules()["video"].AgnesVideoClient(
            key_pool=pool, max_retries=int(cfg["max_retries"]), queue_wait_seconds=int(cfg["queue_wait_sec"]),
            use_system_proxy=self.use_system_proxy)

    def generate(self, prompt: str, save_path: Path, *, seconds: int, reference_images: List[Path],
                 aspect_ratio: str = "16:9", size: str = "720P") -> VideoResult:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        images = [str(p) for p in reference_images] or None
        # 把当前运行的取消信号交给 skill：轮询、排队等待、下载都会检查它
        self.client.cancel_event = cancel.current_event()
        joblog.bound_log(f"视频接口请求：{seconds} 秒，比例 {aspect_ratio}，"
                         f"{'参考图 ' + str(len(images)) + ' 张' if images else '纯文本'}（生成通常需要数分钟）")
        t0 = time.monotonic()
        try:
            with contextlib.redirect_stdout(joblog.console_tee()):
                # 经 cancel.call：用户停止时立即返回；skill 内部轮询也会在下一次检查时退出
                result = cancel.call(
                    self.client.generate_video,
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
            if result.get("cancelled"):
                joblog.bound_log("视频接口：已按用户请求停止（远端任务如已创建，可能仍在服务端生成）", level="warn")
                raise cancel.Cancelled(cancel.STOP_MESSAGE)
        except Exception as exc:
            joblog.bound_log(f"视频接口异常：{type(exc).__name__}: {exc}", level="error")
            return VideoResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        joblog.bound_log(f"视频接口返回，用时 {time.monotonic() - t0:.1f} 秒")
        if result.get("success") and save_path.is_file():
            return VideoResult(ok=True, path=str(save_path))
        text, non_retryable = _failure(result)
        return VideoResult(ok=False, error=text, non_retryable=non_retryable)
