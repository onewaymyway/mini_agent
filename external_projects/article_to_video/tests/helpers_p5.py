"""P5 测试辅助：用 ffmpeg 生成短的假章视频（测试转场与导出，避免每次都做完整合成）。"""
import subprocess
from pathlib import Path

from article_to_video.media.ffmpeg import find_ffmpeg
from article_to_video.compose.part import video_dir
from article_to_video.project_store import write_json_atomic


def fake_part(pdir: Path, part_id: str, seconds: float = 2.0, subtitles=None) -> None:
    vdir = video_dir(pdir, part_id)
    vdir.mkdir(parents=True, exist_ok=True)
    out = vdir / "part.mp4"
    subprocess.run([find_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=1280x720:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000",
                    "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(out)], check=True)
    write_json_atomic(vdir / "info.json", {
        "schema": 1, "part": part_id, "duration": seconds,
        "subtitles": subtitles if subtitles is not None else [
            {"id": f"{part_id}.s01", "start": 0.2, "end": 1.0, "text": f"{part_id} 第一句"}],
    })
