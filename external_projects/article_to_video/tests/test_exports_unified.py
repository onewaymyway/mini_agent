"""成片统一列出：两种方案的成片并列显示，方案由所在目录决定；两个方案目录互不包含。"""
import os
import time

from article_to_video.compose.export import SCHEME_CLASSIC, SCHEME_DECK, list_all_exports
from article_to_video.deck.store import deck_home, deck_root
from article_to_video.pipeline.reset import reset_project


def _mp4(path, when):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.utime(path, (when, when))


def test_both_schemes_are_listed_together_newest_first(tmp_path):
    now = time.time()
    _mp4(tmp_path / "exports" / "标题.mp4", now - 100)
    _mp4(tmp_path / "deck" / "exports" / "标题_新方案.mp4", now)
    rows = list_all_exports(tmp_path)
    assert [r["name"] for r in rows] == ["标题_新方案.mp4", "标题.mp4"]
    assert [r["scheme"] for r in rows] == [SCHEME_DECK, SCHEME_CLASSIC]


def test_scheme_follows_the_folder_not_the_file_name(tmp_path):
    _mp4(tmp_path / "exports" / "标题_新方案.mp4", time.time())
    assert list_all_exports(tmp_path)[0]["scheme"] == SCHEME_CLASSIC


def test_neither_scheme_directory_contains_the_other(tmp_path):
    classic = tmp_path / "parts" / "P01"
    deck = deck_root(tmp_path, "P01")
    for a, b in ((classic, deck), (deck, classic), (tmp_path / "exports", deck_home(tmp_path)),
                 (deck_home(tmp_path), tmp_path / "exports")):
        assert not str(b.resolve()).startswith(str(a.resolve()) + os.sep)


def test_classic_reset_never_touches_the_deck_side(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    from helpers_p2 import make_project
    pdir = make_project(tmp_path)
    (deck_root(pdir, "P01") / "audio").mkdir(parents=True)
    (deck_root(pdir, "P01") / "audio" / "a.wav").write_bytes(b"d")
    _mp4(deck_home(pdir) / "exports" / "标题_新方案.mp4", time.time())
    _mp4(pdir / "exports" / "标题.mp4", time.time())
    reset_project(pdir, from_stage="script", confirm=True)
    assert (deck_root(pdir, "P01") / "audio" / "a.wav").is_file()
    assert (deck_home(pdir) / "exports" / "标题_新方案.mp4").is_file()
    assert not (pdir / "exports").exists()
