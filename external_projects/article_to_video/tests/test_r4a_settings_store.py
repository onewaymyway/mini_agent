"""R4a：统一设置的存储与解析——全局默认、项目覆盖（稀疏）、生效值、恢复默认、旧项目兼容。"""
import json

import pytest

from article_to_video import app_settings as S
from article_to_video.paths import projects_root
from article_to_video.project_store import read_json, write_json_atomic
from article_to_video.visual.config import save_visual_config
from helpers_p1 import make_project


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return tmp_path


def _raw(pdir, group):
    return read_json(pdir / "project.json").get(group)


# ---------- 纯函数 ----------

def test_diff_keeps_only_changed_leaves():
    base = {"a": 1, "b": {"x": 1, "y": 2}, "c": [1, 2]}
    full = {"a": 1, "b": {"x": 9, "y": 2}, "c": [1, 2], "d": 5}
    assert S.diff(full, base) == {"b": {"x": 9}, "d": 5}


def test_get_set_del_path_cleans_empty_parents():
    d = {}
    S.set_path(d, "images.max", 3)
    assert d == {"images": {"max": 3}}
    S._del_path(d, "images.max")
    assert d == {}


def test_builtin_groups_are_normalized_and_complete():
    assert S.builtin_group("settings")["video"]["resolution"] == "1080P"
    assert "engine_chain" in S.builtin_group("settings")["tts"]
    assert S.builtin_group("pipeline") == {"gear": "G0", "outline_engine": "llm", "max_images": 0, "max_minutes": 0.0}
    assert S.builtin_group("logs") == {"max_lines": 0}


# ---------- 全局默认 ----------

def test_global_save_stores_only_non_default_and_strips_identity(home):
    S.save_global_group("settings", {"mode": "faithful"})
    S.save_global_group("bgm", {"enabled": True, "path": "assets/x.mp3", "volume": 0.4})
    data = json.loads((projects_root() / S.GLOBAL_FILE).read_text(encoding="utf-8"))
    assert data["settings"] == {"mode": "faithful"}
    assert data["bgm"] == {"enabled": True, "volume": 0.4}      # 路径属于项目，不进入全局


def test_corrupt_global_file_is_ignored_with_warning(home):
    (projects_root()).mkdir(parents=True, exist_ok=True)
    (projects_root() / S.GLOBAL_FILE).write_text("{不是 JSON", encoding="utf-8")
    gl, warn = S.load_global_raw()
    assert gl == {} and warn and "无法读取" in warn[0]
    assert S.inherited_group("settings")["mode"] == "condensed"


# ---------- 继承与覆盖 ----------

def test_project_inherits_global_until_overridden(home):
    pdir = make_project(home)
    S.save_global_group("visual", {"images": {"max_per_minute": 1.0}})
    assert S.effective_group(pdir, "visual")["images"]["max_per_minute"] == 1.0
    assert S.source_of(pdir, "visual", "images.max_per_minute") == "全局"
    assert S.source_of(pdir, "visual", "images.shots_per_image") == "内置"


def test_override_wins_and_global_change_does_not_touch_it(home):
    pdir = make_project(home)
    S.set_field(pdir, "visual", "images.max_per_minute", 2.0)
    assert S.source_of(pdir, "visual", "images.max_per_minute") == "项目"
    S.save_global_group("visual", {"images": {"max_per_minute": 0.5, "shots_per_image": 3}})
    eff = S.effective_group(pdir, "visual")
    assert eff["images"]["max_per_minute"] == 2.0      # 项目覆盖保持
    assert eff["images"]["shots_per_image"] == 3       # 未覆盖的字段跟随全局


def test_set_field_stores_only_difference_from_inherited(home):
    pdir = make_project(home)
    S.set_field(pdir, "pipeline", "gear", "G1")
    assert _raw(pdir, "pipeline") == {"gear": "G1"}
    S.set_field(pdir, "pipeline", "gear", "G0")         # 与继承值相同：不再保留覆盖
    assert _raw(pdir, "pipeline") == {}


def test_reset_field_returns_to_global(home):
    pdir = make_project(home)
    S.save_global_group("pipeline", {"gear": "G1"})
    S.set_field(pdir, "pipeline", "gear", "G2")
    assert S.effective_group(pdir, "pipeline")["gear"] == "G2"
    S.reset_field(pdir, "pipeline", "gear")
    assert S.effective_group(pdir, "pipeline")["gear"] == "G1"
    assert S.source_of(pdir, "pipeline", "gear") == "全局"


def test_identity_fields_are_never_inherited(home):
    pdir = make_project(home, author="作者甲")
    S.save_global_group("branding", {"author": "全局作者", "credits": {"end_card": False}})
    assert S.effective_group(pdir, "branding")["author"] == "作者甲"
    assert S.effective_group(pdir, "branding")["credits"]["end_card"] is False


def test_legacy_full_copy_is_treated_as_explicit_override(home):
    pdir = make_project(home)
    pj = read_json(pdir / "project.json")
    pj["settings"]["mode"] = "highlights"                  # 旧项目：字段完整保存在项目里
    write_json_atomic(pdir / "project.json", pj)
    S.save_global_group("settings", {"mode": "faithful", "target_minutes": 6.0})
    eff = S.effective_group(pdir, "settings")
    assert eff["mode"] == "highlights"                     # 旧项目显式值保持不变
    assert eff["target_minutes"] == 6.0                   # 其余字段跟随全局


def test_effective_project_replaces_groups_but_raw_file_is_untouched(home):
    pdir = make_project(home)
    S.save_global_group("settings", {"persona": "author"})
    before = read_json(pdir / "project.json")
    eff = S.load_effective_project(pdir)
    assert eff["settings"]["persona"] == "author"
    assert read_json(pdir / "project.json") == before      # 读取生效值不会改写原始文件


def test_save_visual_config_writes_only_overrides(home):
    pdir = make_project(home)
    save_visual_config(pdir, S.builtin_group("visual"))     # 全部与内置默认相同
    assert _raw(pdir, "visual") == {}
    cur = S.builtin_group("visual")
    cur["bible"]["style_anchor"] = "水墨风格，留白较多"
    res = save_visual_config(pdir, cur)
    assert res["config"]["bible"]["style_anchor"].startswith("水墨风格")
    stored = _raw(pdir, "visual")
    assert list(stored) == ["bible"]                                  # 只保存风格锚这一项覆盖
    assert stored["bible"]["style_anchor"] == res["config"]["bible"]["style_anchor"]  # 规范化后的值（含禁文字约束）


# ---------- 导入：只保存与全局不同的字段 ----------

def test_import_stores_only_fields_that_differ_from_global(home):
    S.save_global_group("settings", {"mode": "faithful"})
    pdir = make_project(home)                              # 导入时未指定任何设置
    raw = _raw(pdir, "settings")
    assert "mode" not in raw                               # 与全局一致，不写入项目
    assert S.effective_group(pdir, "settings")["mode"] == "faithful"


def test_nested_orientation_from_import_is_applied(home):
    from article_to_video.importer import import_article
    from conftest import REFERENCE_ARTICLE
    res = import_article(home, source_text=REFERENCE_ARTICLE.read_text(encoding="utf-8"), source_name="r.md",
                         author="甲", settings={"video": {"orientation": "portrait", "resolution": "720P"}})
    pdir = res["project_dir"]
    from pathlib import Path
    assert S.effective_group(Path(pdir), "settings")["video"]["orientation"] == "portrait"
