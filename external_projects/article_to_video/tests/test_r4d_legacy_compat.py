"""R4d：旧项目兼容——R3 之前保存的完整设置字段保留为显式覆盖，行为不变；全局改动不影响已覆盖字段。"""
from article_to_video import app_settings as S
from article_to_video.compose.config import load_compose_config
from article_to_video.audio.synth import project_tts_config
from article_to_video.project_store import read_json, write_json_atomic
from article_to_video.visual.config import load_visual_config
from helpers_p1 import make_project

LEGACY_SETTINGS = {
    "mode": "highlights", "persona": "author", "target_minutes": 6.0, "episode_max_minutes": 12.0,
    "video": {"orientation": "portrait", "resolution": "720P", "fps": 25},
    "tts": {"narration_voice": "zh-CN-YunxiNeural", "quote_voice": "zh-CN-XiaoyiNeural",
            "engine_chain": ["edge-tts"], "edge_proxy": "direct", "max_quotes_per_part": 1,
            "lead_in_sec": 0.5, "tail_sec": 0.5, "sentence_gap_sec": 0.15},
}
LEGACY_COMPOSE = {"transition": "cut", "transition_sec": 0.6, "subtitles": "off",
                  "progress_bar": False, "chapter_label": False}


def _legacy_project(home):
    pdir = make_project(home)
    pj = read_json(pdir / "project.json")
    pj["settings"] = LEGACY_SETTINGS
    pj["compose"] = LEGACY_COMPOSE
    pj["visual"] = S.builtin_group("visual")          # R3 之前的完整副本
    write_json_atomic(pdir / "project.json", pj)
    return pdir


def test_legacy_effective_values_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = _legacy_project(tmp_path)
    eff = S.effective_group(pdir, "settings")
    assert eff["mode"] == "highlights" and eff["persona"] == "author"
    assert eff["video"]["orientation"] == "portrait" and eff["video"]["resolution"] == "720P"
    assert project_tts_config(pdir)["narration_voice"] == "zh-CN-YunxiNeural"
    assert load_compose_config(pdir)["subtitles"] == "off"


def test_global_change_does_not_touch_legacy_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = _legacy_project(tmp_path)
    S.save_global_group("settings", {"mode": "faithful", "persona": "host", "target_minutes": 20.0,
                                     "video": {"orientation": "landscape", "resolution": "1080P"}})
    S.save_global_group("compose", {"transition": "dissolve", "subtitles": "always"})
    eff = S.effective_group(pdir, "settings")
    assert eff["mode"] == "highlights"                        # 旧项目显式值保持
    assert eff["target_minutes"] == 6.0                       # 旧项目显式值保持
    assert load_compose_config(pdir)["subtitles"] == "off"    # 旧项目显式值保持
    assert load_compose_config(pdir)["transition"] == "cut"


def test_restore_to_global_follows_global_again(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = _legacy_project(tmp_path)
    S.save_global_group("compose", {"subtitles": "off"})
    S.reset_field(pdir, "compose", "subtitles")
    assert load_compose_config(pdir)["subtitles"] == "off"
    assert S.source_of(pdir, "compose", "subtitles") == "全局"
    assert S.source_of(pdir, "compose", "transition") == "项目"  # 其它显式字段不受影响


def test_legacy_visual_copy_matches_builtin_values(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = _legacy_project(tmp_path)
    assert load_visual_config(pdir) == S.builtin_group("visual")


def test_untouched_project_without_settings_inherits_everything(tmp_path, monkeypatch):
    """新版本创建的项目不含设置覆盖：全部字段随全局默认变化。"""
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    assert read_json(pdir / "project.json").get("settings", {}) == {}
    S.save_global_group("compose", {"transition": "dissolve"})
    assert load_compose_config(pdir)["transition"] == "dissolve"
