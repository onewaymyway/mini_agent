"""R25 挡位与预算按项目记忆：修改后写入项目设置，重新读取仍生效；不影响其它项目与全局默认值。"""
import json

from article_to_video import app_settings as S
from helpers_p2 import make_project


def _project(tmp_path, monkeypatch, name):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    return make_project(tmp_path / name)


def test_budget_change_is_saved_to_this_project(tmp_path, monkeypatch):
    pdir = _project(tmp_path, monkeypatch, "a")
    root = tmp_path / "global"
    changed = S.save_budget(pdir, gear="G2", max_images=30, max_minutes=12.5, root=root)
    assert changed is True
    saved = json.loads((pdir / "project.json").read_text(encoding="utf-8"))["pipeline"]
    assert saved["gear"] == "G2" and saved["max_images"] == 30 and saved["max_minutes"] == 12.5
    eff = S.effective_group(pdir, "pipeline", root)
    assert (eff["gear"], eff["max_images"], eff["max_minutes"]) == ("G2", 30, 12.5)


def test_unchanged_budget_does_not_rewrite_the_file(tmp_path, monkeypatch):
    pdir = _project(tmp_path, monkeypatch, "a")
    root = tmp_path / "global"
    S.save_budget(pdir, gear="G1", max_images=5, max_minutes=0.0, root=root)
    before = (pdir / "project.json").read_text(encoding="utf-8")
    assert S.save_budget(pdir, gear="G1", max_images=5, max_minutes=0.0, root=root) is False
    assert (pdir / "project.json").read_text(encoding="utf-8") == before


def test_other_projects_and_global_default_are_untouched(tmp_path, monkeypatch):
    a = _project(tmp_path, monkeypatch, "a")
    b = _project(tmp_path, monkeypatch, "b")
    root = tmp_path / "global"
    default = S.effective_group(b, "pipeline", root)
    S.save_budget(a, gear="G2", max_images=30, max_minutes=12.5, root=root)
    assert S.effective_group(b, "pipeline", root) == default
    assert not (root / S.GLOBAL_FILE).exists()


def test_the_saved_budget_survives_a_fresh_read(tmp_path, monkeypatch):
    pdir = _project(tmp_path, monkeypatch, "a")
    root = tmp_path / "global"
    S.save_budget(pdir, gear="G2", max_images=8, max_minutes=3.0, root=root)
    # 模拟重新打开项目：重新从磁盘读取
    again = S.effective_group(pdir, "pipeline", root)
    assert (again["gear"], again["max_images"], again["max_minutes"]) == ("G2", 8, 3.0)
