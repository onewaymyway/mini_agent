"""providers/image.py — 图像生成 provider。

ImageProvider.generate() 永不抛异常，统一返回 ImageResult：
  ok=True  → 已写入 save_path
  ok=False → error 为可读原因，non_retryable 表示需要先修配置
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .keypool import KeyPool, is_non_retryable, is_rate_limit

AGNES_IMAGE_ENDPOINT = "https://apihub.agnes-ai.com/v1/images/generations"
AGNES_IMAGE_MODEL = "agnes-image-2.5-flash"


@dataclass
class ImageResult:
    ok: bool
    path: Optional[str] = None
    error: Optional[str] = None
    non_retryable: bool = False


class ImageProvider:
    name = "base"

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 size: str = "1024x1024") -> ImageResult:  # pragma: no cover - interface
        raise NotImplementedError


class AgnesImageProvider(ImageProvider):
    """Agnes 生图接口（与 gen_image_with_text skill 同一接口）。"""

    name = "agnes"

    def __init__(self, pool: KeyPool, *, endpoint: str = AGNES_IMAGE_ENDPOINT,
                 model: str = AGNES_IMAGE_MODEL, timeout: int = 300, max_retries: int = 3,
                 session=None, sleep=time.sleep):
        self.pool = pool
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 size: str = "1024x1024") -> ImageResult:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "extra_body": {"response_format": "b64_json"},
        }
        if ratio:
            payload["ratio"] = ratio

        attempts = 0
        last_error = "未知错误"
        while attempts < self.max_retries:
            key = self.pool.acquire()
            if key is None:
                wait = self.pool.min_wait()
                if wait > 0:
                    self._sleep(min(wait, 120))
                    continue
                return ImageResult(False, error="所有 API key 均在冷却中", non_retryable=False)
            try:
                resp = self.session.post(
                    self.endpoint, json=payload, timeout=self.timeout,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                )
                status, text = resp.status_code, resp.text or ""
            except Exception as exc:  # 网络层异常计入普通重试
                attempts += 1
                last_error = f"请求异常：{exc}"
                self._sleep(1.5 * attempts)
                continue

            if status != 200:
                if is_rate_limit(status, text):
                    self.pool.report_rate_limit(key)  # 换 key 立即重试，不计次数
                    last_error = f"限流（HTTP {status}）"
                    continue
                if is_non_retryable(status, text):
                    return ImageResult(False, error=f"参数/鉴权错误（HTTP {status}）：{text[:200]}",
                                       non_retryable=True)
                attempts += 1
                last_error = f"HTTP {status}：{text[:200]}"
                self._sleep(1.5 * attempts)
                continue

            try:
                data = resp.json()["data"][0]
                raw = base64.b64decode(data["b64_json"])
            except Exception as exc:
                attempts += 1
                last_error = f"响应解析失败：{exc}"
                continue

            self.pool.report_success(key)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(raw)
            return ImageResult(True, path=str(save_path))

        return ImageResult(False, error=last_error)


class StubImageProvider(ImageProvider):
    """离线占位图：用 PIL 画一张带文字的图，用于测试与无 key 时的流程演示。"""

    name = "stub"

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 size: str = "1024x1024") -> ImageResult:
        from PIL import Image, ImageDraw
        save_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (768, 1024), (38, 44, 56))
        draw = ImageDraw.Draw(img)
        draw.rectangle([24, 24, 744, 1000], outline=(120, 140, 170), width=4)
        lines = ["STUB PORTRAIT", ""] + [prompt[i:i + 44] for i in range(0, min(len(prompt), 220), 44)]
        for i, line in enumerate(lines):
            draw.text((48, 60 + i * 28), line, fill=(230, 230, 230))
        img.save(save_path, format="PNG")
        return ImageResult(True, path=str(save_path))
