"""compose/ffrun.py — 运行 ffmpeg/ffprobe 子进程，可响应停止请求。

子进程以 0.2 秒为周期轮询，期间检查停止信号；收到停止则结束子进程并抛出 cancel.Cancelled。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Tuple

from .. import cancel, joblog
from ..media.ffmpeg import find_ffmpeg, find_ffprobe


class ComposeError(RuntimeError):
    pass


def run(args: List[str]) -> Tuple[int, str, str]:
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                cancel.check()
    except BaseException:
        proc.kill()
        proc.communicate()
        raise
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def ffmpeg(args: List[str]) -> None:
    joblog.bound_log(f"ffmpeg 编码：{Path(str(args[-1])).name}")
    code, _out, err = run([find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error"] + args)
    if code != 0:
        raise ComposeError("ffmpeg 失败：" + (err.strip().splitlines() or ["未知错误"])[-1][:300])


def probe(path) -> dict:
    code, out, err = run([find_ffprobe(), "-v", "error", "-print_format", "json",
                          "-show_format", "-show_streams", str(path)])
    if code != 0:
        raise ComposeError("ffprobe 失败：" + err.strip()[-200:])
    return json.loads(out)
