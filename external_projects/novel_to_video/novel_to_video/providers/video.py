"""providers/video.py — 视频生成 provider（小场景片段）。

AgnesVideoProvider 与 gen_video_with_text skill 同一接口：
  POST {BASE}/videos（reference 模式，参考图以 data URI 传入）→ video_id
  GET  {QUERY}?video_id=..&model_name=..（轮询）→ status completed 时取 url
限流走 KeyPool 切换 key；参数/鉴权错误立即停止。
StubVideoProvider：用参考图经 ffmpeg 生成带推近效果的静帧片段，供离线演示与测试。
"""

from __future__ import annotations

import base64
import mimetypes
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..media import ffmpeg as mf
from .keypool import KeyPool, is_non_retryable, is_rate_limit

AGNES_BASE = "https://apihub.agnes-ai.com/v1"
AGNES_QUERY = "https://apihub.agnes-ai.com/agnesapi"
AGNES_VIDEO_MODEL = "agnes-video-2.5-flash"


@dataclass
class VideoResult:
    ok: bool
    path: Optional[str] = None
    error: Optional[str] = None
    non_retryable: bool = False


class VideoProvider(ABC):
    name = "base"

    @abstractmethod
    def generate(self, prompt: str, save_path: Path, *, seconds: int, reference_images: List[Path],
                 aspect_ratio: str = "9:16") -> VideoResult: ...


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


class AgnesVideoProvider(VideoProvider):
    name = "agnes"

    def __init__(self, pool: KeyPool, *, base: str = AGNES_BASE, query: str = AGNES_QUERY,
                 model: str = AGNES_VIDEO_MODEL, session=None, sleep=time.sleep,
                 poll_interval: float = 5.0, max_wait: int = 1800, max_retries: int = 3):
        self.pool = pool
        self.base, self.query = base, query
        self.model = model
        self._sleep = sleep
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self.max_retries = max_retries
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def _headers(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _create(self, payload: dict):
        """返回 (video_id, None) 或 (None, ImageLikeError)。"""
        attempts = 0
        last = "未知错误"
        while attempts < self.max_retries:
            key = self.pool.acquire()
            if key is None:
                # 全部 key 冷却：等待一次（计入尝试次数，防止无限空转），仍不可用则放弃
                attempts += 1
                wait = self.pool.min_wait()
                if wait <= 0 or attempts >= self.max_retries:
                    return None, ("所有 API key 均在冷却中", False)
                self._sleep(min(wait, 120))
                continue
            try:
                resp = self.session.post(f"{self.base}/videos", json=payload,
                                         headers=self._headers(key), timeout=120)
                status, text = resp.status_code, resp.text or ""
            except Exception as exc:
                attempts += 1
                last = f"创建任务请求异常：{exc}"
                self._sleep(1.5 * attempts)
                continue
            if status == 200:
                body = resp.json()
                vid = body.get("video_id") or body.get("id") or body.get("task_id")
                if not vid:
                    return None, (f"创建任务响应缺少 video_id：{str(body)[:200]}", False)
                self.pool.report_success(key)
                return vid, None
            if is_rate_limit(status, text):
                self.pool.report_rate_limit(key)
                last = f"限流（HTTP {status}）"
                continue
            if is_non_retryable(status, text):
                return None, (f"参数/鉴权错误（HTTP {status}）：{text[:200]}", True)
            attempts += 1
            last = f"HTTP {status}：{text[:200]}"
            self._sleep(1.5 * attempts)
        return None, (last, False)

    def _poll(self, video_id: str) -> Optional[str]:
        """返回视频 url；失败或超时抛 RuntimeError。"""
        waited = 0.0
        while waited < self.max_wait:
            key = self.pool.acquire()
            if key is None:
                # 全部 key 冷却中：等待最早恢复的 key，绝不用空 key 发请求
                wait = self.pool.min_wait()
                self._sleep(min(max(wait, 0.1), 120))
                waited += max(wait, 0.1)
                continue
            resp = self.session.get(self.query, params={"video_id": video_id, "model_name": self.model},
                                    headers=self._headers(key), timeout=60)
            if is_rate_limit(resp.status_code, resp.text or ""):
                self.pool.report_rate_limit(key)  # 冷却并切换，下一轮用新 key
                self._sleep(self.poll_interval)
                waited += self.poll_interval
                continue
            body = resp.json() if resp.ok else {}
            self.pool.report_success(key)
            status = body.get("status")
            if status == "completed":
                url = body.get("video_url") or body.get("url")
                if not url and isinstance(body.get("data"), list) and body["data"]:
                    url = body["data"][0].get("url") or body["data"][0].get("video_url")
                if not url:
                    raise RuntimeError("任务完成但未返回视频地址")
                return url
            if status == "failed":
                raise RuntimeError(f"视频任务失败：{str(body)[:200]}")
            self._sleep(self.poll_interval)
            waited += self.poll_interval
        raise RuntimeError(f"视频生成超时（>{self.max_wait}s）")

    def generate(self, prompt: str, save_path: Path, *, seconds: int, reference_images: List[Path],
                 aspect_ratio: str = "9:16") -> VideoResult:
        payload = {
            "model": self.model, "prompt": prompt, "mode": "reference",
            "seconds": str(seconds), "size": "720P", "aspect_ratio": aspect_ratio,
            "images": [_data_uri(p) for p in reference_images[:3]],
        }
        vid, err = self._create(payload)
        if err:
            msg, fatal = err
            return VideoResult(False, error=msg, non_retryable=fatal)
        try:
            url = self._poll(vid)
            raw = self.session.get(url, timeout=300)
            raw.raise_for_status()
        except Exception as exc:
            return VideoResult(False, error=f"视频下载/轮询失败：{exc}")
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(raw.content)
        return VideoResult(True, path=str(save_path))


class StubVideoProvider(VideoProvider):
    name = "stub"

    def generate(self, prompt: str, save_path: Path, *, seconds: int, reference_images: List[Path],
                 aspect_ratio: str = "9:16") -> VideoResult:
        if not reference_images:
            return VideoResult(False, error="stub 视频需要至少一张参考图")
        try:
            mf.still_clip(reference_images[0], save_path, seconds)
        except mf.MediaError as exc:
            return VideoResult(False, error=str(exc))
        return VideoResult(True, path=str(save_path))
