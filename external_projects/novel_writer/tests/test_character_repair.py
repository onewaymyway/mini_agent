"""角色文件写坏时的确定性修复、改前备份与还原：不为格式问题让 agent 把整步重做。"""

from __future__ import annotations

import json

from novel_writer import character_store as CS
from novel_writer import validators as V
from novel_writer import steps as S
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _rec(cid="char_001", name="李凌云", **kw):
    r = {"id": cid, "name": name, "role": "protagonist", "introduced_chapter": 1,
         "state_log": [{"chapter": 1, "status": "出场"}],
         "current_state": {"as_of_chapter": 1, "location": "山门", "emotional": "平静"}}
    r.update(kw)
    return r


def _good_text(rec):
    return CS._encode(rec) + CS._body(rec)


def _dir(tmp_path):
    d = tmp_path / "角色"
    d.mkdir()
    return d


def test_bom_and_leading_blank_lines_are_repaired(tmp_path):
    d = _dir(tmp_path)
    (d / "char_001.md").write_text("﻿\n\n" + _good_text(_rec()), encoding="utf-8")
    assert CS.repair_dir(d) == ["char_001.md"]
    assert CS.problems_for_dir(d) == []


def test_whole_file_json_with_fence_and_wrapper_is_repaired(tmp_path):
    d = _dir(tmp_path)
    body = "```json\n" + json.dumps({"char_001": _rec()}, ensure_ascii=False, indent=2) + "\n```"
    (d / "char_001.md").write_text(body, encoding="utf-8")
    assert CS.repair_dir(d) == ["char_001.md"]
    chars, probs = CS.load_records(d)
    assert probs == [] and chars["char_001"]["name"] == "李凌云"


def test_header_without_dashes_and_multiline_values_is_repaired(tmp_path):
    d = _dir(tmp_path)
    text = ('id: "char_001"\nname: "李凌云"\nrole: "protagonist"\nintroduced_chapter: 1\n'
            'current_state: {\n  "as_of_chapter": 1,\n  "location": "山门"\n}\n'
            'state_log: [{"chapter":1,"status":"出场"}]\n\n# 正文\n')
    (d / "char_001.md").write_text(text, encoding="utf-8")
    assert CS.repair_dir(d) == ["char_001.md"]
    chars, probs = CS.load_records(d)
    assert probs == [] and chars["char_001"]["current_state"]["location"] == "山门"


def test_unrecoverable_file_is_left_alone(tmp_path):
    d = _dir(tmp_path)
    (d / "char_001.md").write_text("# 李凌云\n他是主角，性格孤僻。\n", encoding="utf-8")
    assert CS.repair_dir(d) == []
    assert (d / "char_001.md").read_text(encoding="utf-8").startswith("# 李凌云")


def test_repair_refuses_when_result_would_be_invalid(tmp_path):
    d = _dir(tmp_path)
    bad = _rec(id="char_999")      # id 与文件名不一致：不能擅自改
    (d / "char_001.md").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    assert CS.repair_dir(d) == []


def test_good_files_are_never_rewritten(tmp_path):
    d = _dir(tmp_path)
    p = d / "char_001.md"
    p.write_text(_good_text(_rec()), encoding="utf-8")
    before = p.stat().st_mtime_ns
    assert CS.repair_dir(d) == [] and p.stat().st_mtime_ns == before


def test_backup_and_restore_only_touch_invalid_files(tmp_path):
    d, b = _dir(tmp_path), tmp_path / "bak"
    (d / "char_001.md").write_text(_good_text(_rec()), encoding="utf-8")
    (d / "char_002.md").write_text(_good_text(_rec("char_002", "王道长")), encoding="utf-8")
    CS.backup_dir(d, b)
    (d / "char_001.md").write_text("# 被写坏了\n", encoding="utf-8")
    new2 = _good_text(_rec("char_002", "王道长", current_state={"as_of_chapter": 5}))
    (d / "char_002.md").write_text(new2, encoding="utf-8")        # 合格的新内容必须保留
    assert CS.restore_invalid(d, b) == ["char_001.md"]
    assert CS.problems_for_dir(d) == []
    assert (d / "char_002.md").read_text(encoding="utf-8") == new2


def test_output_check_repairs_then_restores_and_reports(isolated):
    ws = new_book(isolated)
    base = ws.path("characters_dir")
    base.mkdir(parents=True, exist_ok=True)
    (base / "char_001.md").write_text(_good_text(_rec()), encoding="utf-8")
    (base / "char_002.md").write_text(_good_text(_rec("char_002", "王道长")), encoding="utf-8")
    CS.backup_dir(base, ws.studio / "backup" / "characters")
    step = S.StepDef(id="ch02.sync", title="t", stage="p2", category=S.CAT_CHAPTER_REVIEW,
                     executor=S.EXECUTOR_SKILL, skill="character-manager", chapter=2)
    # char_001：BOM（可确定性修复）；char_002：纯叙述（不可修复，需还原）
    (base / "char_001.md").write_text("﻿" + _good_text(_rec()), encoding="utf-8")
    (base / "char_002.md").write_text("# 王道长\n只剩一段叙述", encoding="utf-8")
    probs = V._check_character_files(ws, step)
    log = ws.path("studio_log").read_text(encoding="utf-8")
    assert "已确定性修复 1 个角色文件" in log and "已还原为本步骤开始前的版本" in log
    assert any("char_002.md" in p and "已还原" in p for p in probs)
    assert CS.problems_for_dir(base) == []        # 目录里不再留损坏文件
    # 模拟本章同步后的正确内容：主要角色记录第 2 章状态（章后同步的新校验要求）
    rec2 = _rec(current_state={"as_of_chapter": 2, "location": "山门", "emotional": "平静"},
                state_log=[{"chapter": 1, "status": "出场"}, {"chapter": 2, "status": "再现"}])
    (base / "char_001.md").write_text(_good_text(rec2), encoding="utf-8")
    assert V._check_character_files(ws, step) == []   # 再检查已合格
