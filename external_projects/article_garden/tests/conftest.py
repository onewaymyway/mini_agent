"""测试公共夹具：每个测试使用独立的数据根目录（ARTICLE_GARDEN_HOME），互不影响。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("ARTICLE_GARDEN_HOME", str(home))
    return home
