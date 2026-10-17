"""入口脚本输出不能因控制台编码（Windows 的 GBK）而失败：emoji 等字符必须可以打印。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_entrypoint_prints_emoji_under_gbk_console():
    env = dict(os.environ, PYTHONIOENCODING="gbk")  # 模拟 Windows 中文控制台
    code = "import sys; sys.path.insert(0, 'entrypoints'); import _common; print('🟡 第1章 ✔ 完成')"
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert b"Traceback" not in proc.stderr
