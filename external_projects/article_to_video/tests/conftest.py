import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

REFERENCE_ARTICLE = Path(__file__).resolve().parent / "fixtures" / "reference_article.md"


import threading  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cancel_signal():
    """每个测试绑定一个新的停止信号，避免一个测试的“停止”泄漏到后续测试（同一线程）。"""
    from article_to_video import cancel
    cancel.bind(threading.Event())
    yield
    cancel.bind(threading.Event())
