"""新方案运行的实时日志：范围为 deck，阶段开始/完成与每章进度都会写入，且边运行边写。"""
import json

import pytest

from article_to_video import joblog
from article_to_video.deck.pipeline import run_deck_pipeline
from article_to_video.project_store import write_json_atomic
from article_to_video.providers.tts import SilenceTTS
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project
from test_deck_phase2 import FakeLLM, FIXTURE


def _entries(pdir):
    p = joblog.log_path(pdir)
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


class WatchingLLM(FakeLLM):
    """记录每次调用模型时，日志已经写到了哪一行（证明日志是边运行边写的）。"""

    def __init__(self, pdir):
        super().__init__()
        self.pdir = pdir
        self.seen = []

    def ask(self, prompt, system=""):
        self.seen.append(len(_entries(self.pdir)))
        return super().ask(prompt, system)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "测试文章", "confirmed": True,
                       "parts": [{"id": "P01", "title": "引言", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    return pdir


def test_stage_progress_is_logged_under_the_deck_scope(project):
    with joblog.bind(project, "deck"):
        st = run_deck_pipeline(project, gear="G3", until="script", llm=FakeLLM(), tts=SilenceTTS())
    assert st["state"] == "done"
    deck_lines = [e for e in _entries(project) if e.get("scope") == "deck"]
    msgs = [e["message"] for e in deck_lines]
    assert any("新方案开始运行" in m for m in msgs)
    assert any("开始幻灯片规划" in m for m in msgs)
    assert any("幻灯片规划完成" in m for m in msgs)
    assert any("开始讲稿" in m for m in msgs)
    assert any("讲稿完成" in m for m in msgs)


def test_logs_are_written_while_the_run_is_in_progress(project):
    llm = WatchingLLM(project)
    with joblog.bind(project, "deck"):
        run_deck_pipeline(project, gear="G3", until="script", llm=llm, tts=SilenceTTS())
    assert llm.seen and llm.seen[0] > 0          # 调用讲稿模型时，规划阶段的日志已经落盘
    assert llm.seen == sorted(llm.seen)          # 日志只增不减
