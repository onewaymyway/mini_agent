"""P6 接入辅助测试：发现未登记目录、确认门槛、非破坏性接入、幂等、恢复与巡检提示。

对应 next_doc/management_output_project_model_plan.md 第 8.6 节与第 9 章 P6。

保证：
  1. list_unregistered 只读，只列出未登记的目录，忽略 _ 与 . 开头的目录；
  2. 未确认（confirm=False）时拒绝接入，且不产生任何写入；
  3. 接入只新增文件：目录中原有文件逐字节不变；
  4. PROJECT_INFO.md 仅在缺失且给出介绍时创建，已存在则不覆盖；
  5. 接入后写入 PROJECT.json，managed=True，状态保持 floating；
  6. 重复接入幂等，不产生任何写入；
  7. 接入后可正常发布版本，删除注册表后仍可从 PROJECT.json 恢复版本历史；
  8. 巡检对未登记目录给出 unregistered 建议，接入后消失；
  9. 接入不修改 goals.json。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mini_agent.output_registry import RegistryError, adopt_output_project, list_unregistered, rebuild_index
from mini_agent.output_registry.lifecycle import publish_version
from mini_agent.output_registry.patrol import collect_metrics, evaluate, UNCALIBRATED_THRESHOLDS
from mini_agent.output_registry.registry import load_record, registry_dir


def _tree_hash(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, _dn, fns in os.walk(root):
        for fn in fns:
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
    return out


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    root = p / "output_projects"
    (root / "legacy_site" / "output").mkdir(parents=True)
    (root / "legacy_site" / "output" / "index.html").write_text("<h1>old</h1>", encoding="utf-8")
    (root / "legacy_site" / "notes.txt").write_text("keep me", encoding="utf-8")
    (root / "_registry").mkdir()
    (root / ".hidden").mkdir()
    (p / ".agent").mkdir()
    (p / ".agent" / "goals.json").write_text(json.dumps({"goals": []}), encoding="utf-8")
    return p


def test_list_unregistered_only_returns_unregistered_dirs(proj: Path):
    rows = list_unregistered(proj)
    assert [r["slug"] for r in rows] == ["legacy_site"]
    assert rows[0]["has_project_info"] is False
    assert rows[0]["file_count"] == 2
    assert rows[0]["suggested_title"] == "legacy_site"


def test_list_unregistered_is_read_only(proj: Path):
    before = _tree_hash(proj)
    list_unregistered(proj)
    assert _tree_hash(proj) == before


def test_adopt_without_confirm_is_refused_and_writes_nothing(proj: Path):
    before = _tree_hash(proj)
    with pytest.raises(RegistryError):
        adopt_output_project(proj, "legacy_site", confirm=False)
    assert _tree_hash(proj) == before


def test_adopt_keeps_original_files_byte_identical(proj: Path):
    root = proj / "output_projects" / "legacy_site"
    before = {k: v for k, v in _tree_hash(root).items()}
    result = adopt_output_project(proj, "legacy_site", confirm=True)
    after = _tree_hash(root)
    assert result["managed"] is True
    assert result["project_json_written"] is True
    for rel, digest in before.items():
        assert after[rel] == digest, rel
    assert set(after) - set(before) == {"PROJECT.json"}


def test_adopt_writes_registry_record_and_mirror(proj: Path):
    adopt_output_project(proj, "legacy_site", confirm=True, title="旧站点")
    record = load_record(proj, "legacy_site")
    assert record["managed"] is True
    assert record["legacy"] is False
    assert record["status"] == "floating"
    assert record["title"] == "旧站点"
    assert any(e.get("event") == "adopted" for e in record["status_history"])
    mirror = json.loads((proj / "output_projects" / "legacy_site" / "PROJECT.json").read_text(encoding="utf-8"))
    assert mirror["managed"] is True


def test_adopt_creates_intro_only_when_missing(proj: Path):
    root = proj / "output_projects" / "legacy_site"
    adopt_output_project(proj, "legacy_site", confirm=True, intro="一个旧站点")
    text = (root / "PROJECT_INFO.md").read_text(encoding="utf-8")
    assert "一个旧站点" in text


def test_adopt_never_overwrites_existing_intro(proj: Path):
    root = proj / "output_projects" / "legacy_site"
    (root / "PROJECT_INFO.md").write_text("# 原介绍\n\n原文\n", encoding="utf-8")
    adopt_output_project(proj, "legacy_site", confirm=True, intro="新文字")
    assert (root / "PROJECT_INFO.md").read_text(encoding="utf-8") == "# 原介绍\n\n原文\n"


def test_adopt_is_idempotent(proj: Path):
    adopt_output_project(proj, "legacy_site", confirm=True)
    before = _tree_hash(proj)
    again = adopt_output_project(proj, "legacy_site", confirm=True)
    assert again["already_registered"] is True
    assert _tree_hash(proj) == before


def test_adopt_rejects_missing_dir_and_bad_slug(proj: Path):
    with pytest.raises(RegistryError):
        adopt_output_project(proj, "no_such_dir", confirm=True)
    with pytest.raises(RegistryError):
        adopt_output_project(proj, "../escape", confirm=True)


def test_adopted_project_can_publish_and_recover_after_registry_loss(proj: Path):
    adopt_output_project(proj, "legacy_site", confirm=True)
    publish_version(proj, "legacy_site", "1.0.0", changelog="首发")
    # 删除注册表后，应能从 PROJECT.json 恢复版本历史
    import shutil
    shutil.rmtree(registry_dir(proj))
    rebuild_index(proj)
    record = load_record(proj, "legacy_site")
    assert [v["version"] for v in record["versions"]] == ["1.0.0"]


def test_patrol_reports_unregistered_until_adopted(proj: Path):
    rows = collect_metrics(proj)
    assert any(r["slug"] == "legacy_site" and r["registered"] is False for r in rows)
    kinds = {(f["slug"], f["kind"]) for f in evaluate(rows, dict(UNCALIBRATED_THRESHOLDS))}
    assert ("legacy_site", "unregistered") in kinds

    adopt_output_project(proj, "legacy_site", confirm=True)
    rows = collect_metrics(proj)
    kinds = {(f["slug"], f["kind"]) for f in evaluate(rows, dict(UNCALIBRATED_THRESHOLDS))}
    assert ("legacy_site", "unregistered") not in kinds


def test_adopt_does_not_touch_goals_json(proj: Path):
    goals = proj / ".agent" / "goals.json"
    before = goals.read_bytes()
    adopt_output_project(proj, "legacy_site", confirm=True)
    assert goals.read_bytes() == before
