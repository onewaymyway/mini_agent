"""video_format.py — 视频输出格式（横屏/竖屏、分辨率、帧率）的唯一来源。

默认：横屏 16:9，720P（1280x720），24fps。所有需要尺寸或比例的地方都从这里取：
  * 媒体合成（media/ffmpeg.py 的画面尺寸与帧率）；
  * 视频接口请求参数（aspect_ratio、size）；
  * 定妆图比例（地点）与封面背景比例；
  * 小场景提示词中的画幅描述。

配置位于项目 project.json 的 video 块，可在挡位页“视频输出格式”里修改。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

ORIENTATIONS = {
    "landscape": {"label": "横屏 16:9", "aspect_ratio": "16:9"},
    "portrait": {"label": "竖屏 9:16", "aspect_ratio": "9:16"},
}
RESOLUTIONS = {"720P": 720, "1080P": 1080}     # 短边像素
FPS_CHOICES = (24, 25, 30)

DEFAULT_FORMAT = {"orientation": "landscape", "resolution": "720P", "fps": 24, "poll_interval_sec": 5}


@dataclass(frozen=True)
class VideoFormat:
    orientation: str
    aspect_ratio: str
    resolution: str
    width: int
    height: int
    fps: int
    poll_interval_sec: float

    @property
    def key(self) -> str:
        """写进缓存键：格式变化后，已生成的小场景视频会按新格式重新生成。"""
        return f"{self.orientation}-{self.resolution}-{self.width}x{self.height}-{self.fps}"

    @property
    def is_portrait(self) -> bool:
        return self.orientation == "portrait"

    @property
    def frame_prompt(self) -> str:
        """提示词中的画幅描述。"""
        return "vertical 9:16 frame" if self.is_portrait else "horizontal 16:9 frame"

    @property
    def describe(self) -> str:
        return f"{ORIENTATIONS[self.orientation]['label']} {self.width}x{self.height} {self.fps}fps {self.resolution}"


def _dims(orientation: str, resolution: str) -> tuple[int, int]:
    short = RESOLUTIONS[resolution]
    long_side = int(round(short * 16 / 9 / 2)) * 2       # 偶数，保证 H.264 可用
    return (short, long_side) if orientation == "portrait" else (long_side, short)


def resolve(cfg: Optional[Dict[str, Any]]) -> VideoFormat:
    """把项目配置解析为 VideoFormat；缺省或非法值回退到默认（横屏 720P 24fps）。"""
    cfg = cfg or {}
    orientation = cfg.get("orientation") if cfg.get("orientation") in ORIENTATIONS else DEFAULT_FORMAT["orientation"]
    resolution = cfg.get("resolution") if cfg.get("resolution") in RESOLUTIONS else DEFAULT_FORMAT["resolution"]
    try:
        fps = int(cfg.get("fps") or DEFAULT_FORMAT["fps"])
    except (TypeError, ValueError):
        fps = DEFAULT_FORMAT["fps"]
    if fps not in FPS_CHOICES:
        fps = DEFAULT_FORMAT["fps"]
    try:
        poll = float(cfg.get("poll_interval_sec") or DEFAULT_FORMAT["poll_interval_sec"])
    except (TypeError, ValueError):
        poll = float(DEFAULT_FORMAT["poll_interval_sec"])
    w, h = _dims(orientation, resolution)
    return VideoFormat(orientation=orientation, aspect_ratio=ORIENTATIONS[orientation]["aspect_ratio"],
                       resolution=resolution, width=w, height=h, fps=fps, poll_interval_sec=max(1.0, poll))


def load(project_dir: Path) -> VideoFormat:
    """读取项目的视频格式（项目配置的 video 块）。"""
    import json
    path = Path(project_dir) / "project.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8")).get("video") or {}
    except (OSError, ValueError):
        cfg = {}
    return resolve(cfg)
