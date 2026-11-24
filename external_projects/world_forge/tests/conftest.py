"""测试公共设置：数据目录指向临时目录，provider 使用离线实现。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLD_FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORLD_FORGE_PROVIDERS", "stub")
    yield


@pytest.fixture
def llm():
    from world_forge.providers.text import RuleTextLLM

    return RuleTextLLM()


@pytest.fixture
def img():
    from world_forge.providers.image import StubImageProvider

    return StubImageProvider()


@pytest.fixture
def world(llm):
    """创建世界并批准宪章，返回 world id。"""
    from world_forge import engine, proposals, repo

    wid = engine.create_world(llm, concept="蒸汽与符文并存的海上帝国")["world_id"]
    pid = next(p["id"] for p in repo.load_proposals(wid) if p["op"] == "set_charter")
    proposals.approve(wid, pid)
    return wid
