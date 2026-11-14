"""AppTest 用的最小应用：渲染 📖 阅读页（环境变量 RD_WS 指向小说目录）。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.reading import render_reading  # noqa: E402

render_reading(os.environ["RD_WS"])
