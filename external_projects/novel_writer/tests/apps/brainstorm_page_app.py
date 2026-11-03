"""灵感构思页面的冒烟测试入口（AppTest 驱动）。"""
import os
import sys

sys.path.insert(0, os.environ["NOVEL_PROJECT"])

from ui.brainstorm import page_brainstorm  # noqa: E402

page_brainstorm()
