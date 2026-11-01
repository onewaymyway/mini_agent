"""media/binaries.py — ffmpeg / ffprobe 的统一查找（与 novel-video-studio/scripts/common.py 一致）。

查找优先级（找到即停）：
  1. 环境变量：FFMPEG_BIN / FFPROBE_BIN（本项目），或 NOVEL_FFMPEG_PATH / NOVEL_FFPROBE_PATH（skill）
  2. imageio_ffmpeg（pip 包自带的 ffmpeg 二进制；ffprobe 按同目录同名规则猜测）
  3. conda 环境自动探测：当前激活环境优先，其次名字含 novel/mv/video 的环境，再次其它环境
  4. 系统 PATH（shutil.which）

找不到时抛出 MediaError，并列出每一种尝试过的方式，而不是只说“找不到”。
结果会缓存；找不到时不缓存，安装完成后下一次调用即可生效。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

_CONDA_ENV_NAME_HINTS = ("novel", "mv", "video")
_CACHE: Dict[str, str] = {}


class BinaryNotFound(RuntimeError):
    """找不到可执行文件。消息中列出了已尝试的查找方式。"""


def reset_cache() -> None:
    _CACHE.clear()


def _env_candidates(exe: str) -> List[str]:
    """本项目变量与 skill 变量都认，按这个顺序。"""
    if exe == "ffmpeg":
        return ["FFMPEG_BIN", "NOVEL_FFMPEG_PATH"]
    return ["FFPROBE_BIN", "NOVEL_FFPROBE_PATH"]


def _conda_bin_dir(prefix: str) -> Path:
    if os.name == "nt":
        return Path(prefix) / "Library" / "bin"
    return Path(prefix) / "bin"


def _iter_conda_prefixes() -> Iterator[str]:
    seen = set()
    current = os.environ.get("CONDA_PREFIX")
    if current and current not in seen:
        seen.add(current)
        yield current
    conda = shutil.which("conda") or shutil.which("mamba")
    if not conda:
        return
    try:
        r = subprocess.run([conda, "env", "list", "--json"], capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            return
        envs = json.loads(r.stdout).get("envs", []) or []
    except Exception:  # noqa: BLE001 - conda 探测失败不影响其它查找方式
        return
    hinted = [e for e in envs if any(h in Path(e).name.lower() for h in _CONDA_ENV_NAME_HINTS)]
    rest = [e for e in envs if e not in hinted]
    for e in hinted + rest:
        if e not in seen:
            seen.add(e)
            yield e


def _find(exe: str) -> Tuple[Optional[str], List[str]]:
    tried: List[str] = []
    win_exe = f"{exe}.exe"

    for var in _env_candidates(exe):
        val = os.environ.get(var)
        tried.append(f"环境变量 {var}" + (f"（={val}）" if val else "（未设置）"))
        if val and Path(val).exists():
            return val, tried

    try:
        import imageio_ffmpeg  # type: ignore
        base = imageio_ffmpeg.get_ffmpeg_exe()
        if exe == "ffmpeg":
            candidate = base
        else:
            candidate = str(Path(base).parent / Path(base).name.replace("ffmpeg", "ffprobe"))
        tried.append(f"imageio_ffmpeg（{candidate}）")
        if Path(candidate).exists():
            return candidate, tried
    except ImportError:
        tried.append("imageio_ffmpeg（未安装）")
    except Exception as exc:  # noqa: BLE001
        tried.append(f"imageio_ffmpeg（出错：{type(exc).__name__}）")

    checked = []
    for prefix in _iter_conda_prefixes():
        cand = _conda_bin_dir(prefix) / (win_exe if os.name == "nt" else exe)
        checked.append(str(cand))
        if cand.exists():
            tried.append(f"conda 环境自动探测（命中 {cand}）")
            return str(cand), tried
    tried.append("conda 环境自动探测（已检查: " + "; ".join(checked) + "）" if checked
                 else "conda 环境自动探测（未找到 conda/mamba 或没有可用环境）")

    found = shutil.which(exe)
    tried.append(f"系统 PATH（shutil.which('{exe}')" + (f" -> {found}）" if found else "，未找到）"))
    return (found, tried) if found else (None, tried)


def _resolve(exe: str) -> str:
    if exe in _CACHE:
        return _CACHE[exe]
    found, tried = _find(exe)
    if not found:
        hint = ("请安装 ffmpeg 套件（如 `pip install imageio-ffmpeg`，或用 conda 安装到某个环境），"
                f"或设置环境变量 {_env_candidates(exe)[0]} 指向具体的 {exe} 可执行文件。")
        raise BinaryNotFound(f"找不到 {exe} 可执行文件，已尝试以下方式均未命中：\n  - "
                             + "\n  - ".join(tried) + "\n" + hint)
    _CACHE[exe] = found
    return found


def find_ffmpeg() -> str:
    return _resolve("ffmpeg")


def find_ffprobe() -> str:
    return _resolve("ffprobe")
