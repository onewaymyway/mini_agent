"""providers/image.py — 图像生成 provider。

ImageProvider.generate() 永不抛异常，统一返回 ImageResult：
  ok=True  → 已写入 save_path
  ok=False → error 为可读原因，non_retryable 表示需要先修配置
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .. import cancel
from .keypool import KeyPool, is_non_retryable, is_rate_limit

AGNES_IMAGE_ENDPOINT = "https://apihub.agnes-ai.com/v1/images/generations"
AGNES_IMAGE_MODEL = "agnes-image-2.5-flash"

# 每次生图最多附带的参考图数量（动态漫模式与视频模式共用的上限）
MAX_REFERENCE_IMAGES = 3
# 参考图长边上限（像素）：超过则等比缩小，控制请求体大小
REFERENCE_MAX_EDGE = 1024


@dataclass
class ImageResult:
    ok: bool
    path: Optional[str] = None
    error: Optional[str] = None
    non_retryable: bool = False
    # 参考图：实际发送的数量，以及因超过上限而被丢弃的文件（按原顺序）
    ref_count: int = 0
    dropped_refs: List[str] = field(default_factory=list)


class ImageProvider:
    name = "base"

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 size: str = "1024x1024",
                 reference_images: Optional[Sequence[Path]] = None) -> ImageResult:  # pragma: no cover - interface
        raise NotImplementedError


# ---------------- 参考图

class ReferenceError(ValueError):
    """参考图无法使用（文件缺失或无法解码）。发送请求前即报错，不消耗 key 与重试次数。"""


def select_references(paths: Iterable[Path | str], limit: int = MAX_REFERENCE_IMAGES) -> Tuple[List[Path], List[Path]]:
    """按原顺序取前 limit 张，返回 (保留, 丢弃)。不检查文件是否存在。"""
    items = [Path(p) for p in paths]
    return items[:limit], items[limit:]


def encode_reference(path: Path, *, max_edge: int = REFERENCE_MAX_EDGE) -> str:
    """读取参考图，长边超过 max_edge 时等比缩小，返回 PNG 的 data URI。"""
    from PIL import Image

    p = Path(path)
    if not p.is_file():
        raise ReferenceError(f"参考图不存在：{p.name}")
    try:
        with Image.open(p) as im:
            im.load()
            img = im.convert("RGB")
    except Exception as exc:  # noqa: BLE001 - 损坏文件要给出可读原因
        raise ReferenceError(f"参考图无法读取：{p.name}（{type(exc).__name__}）") from exc
    long_edge = max(img.size)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def prepare_references(paths: Iterable[Path | str], *, limit: int = MAX_REFERENCE_IMAGES
                       ) -> Tuple[List[str], List[Path]]:
    """取前 limit 张并编码。返回 (data URI 列表, 被丢弃的文件)。任一保留的参考图无效则抛 ReferenceError。"""
    kept, dropped = select_references(paths, limit)
    return [encode_reference(p) for p in kept], dropped


class AgnesImageProvider(ImageProvider):
    """Agnes 生图接口（与 gen_image_with_text skill 同一接口）。"""

    name = "agnes"

    def __init__(self, pool: KeyPool, *, endpoint: str = AGNES_IMAGE_ENDPOINT,
                 model: str = AGNES_IMAGE_MODEL, timeout: int = 300, max_retries: int = 3,
                 session=None, sleep=cancel.sleep):   # 可中断的等待：停止时立即返回
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
                 size: str = "1024x1024",
                 reference_images: Optional[Sequence[Path]] = None) -> ImageResult:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": size,
            "extra_body": {"response_format": "b64_json"},
        }
        if ratio:
            payload["ratio"] = ratio
        dropped: List[Path] = []
        if reference_images:
            try:
                uris, dropped = prepare_references(reference_images)
            except ReferenceError as exc:
                return ImageResult(False, error=str(exc), non_retryable=True)
            # 无参考图时不写入 image 字段，请求体与改动前逐字节一致
            payload["extra_body"]["image"] = uris
        ref_info = {"ref_count": len(payload["extra_body"].get("image", [])),
                    "dropped_refs": [str(p) for p in dropped]}

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
                resp = cancel.call(  # 经 cancel.call：用户停止时立即返回，不等待网络
                    self.session.post, self.endpoint, json=payload, timeout=self.timeout,
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
            return ImageResult(True, path=str(save_path), **ref_info)

        return ImageResult(False, error=last_error, **ref_info)


class StubImageProvider(ImageProvider):
    """离线占位图：用 PIL 画一张带文字的图，用于测试与无 key 时的流程演示。"""

    name = "stub"

    def __init__(self) -> None:
        # 每次调用记录参考图情况，供测试断言（不含 base64 内容）
        self.reference_log: List[dict] = []

    def generate(self, prompt: str, save_path: Path, *, ratio: Optional[str] = None,
                 size: str = "1024x1024",
                 reference_images: Optional[Sequence[Path]] = None) -> ImageResult:
        from PIL import Image, ImageDraw
        dropped: List[Path] = []
        names: List[str] = []
        if reference_images:
            try:
                _, dropped = prepare_references(reference_images)  # 与真实接口同样校验与截断
            except ReferenceError as exc:
                return ImageResult(False, error=str(exc), non_retryable=True)
            kept, _ = select_references(reference_images)
            names = [p.name for p in kept]
        self.reference_log.append({"prompt": prompt, "refs": names})
        info = {"ref_count": len(names), "dropped_refs": [str(p) for p in dropped]}
        save_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (768, 1024), (38, 44, 56))
        draw = ImageDraw.Draw(img)
        draw.rectangle([24, 24, 744, 1000], outline=(120, 140, 170), width=4)
        ascii_prompt = prompt.encode("ascii", "ignore").decode("ascii")  # 字库仅覆盖 ASCII
        lines = ["STUB PORTRAIT", ""] + [ascii_prompt[i:i + 44] for i in range(0, min(len(ascii_prompt), 220), 44)]
        for i, line in enumerate(lines):
            draw.text((48, 60 + i * 28), line, fill=(230, 230, 230))
        img.save(save_path, format="PNG")
        return ImageResult(True, path=str(save_path), **info)
