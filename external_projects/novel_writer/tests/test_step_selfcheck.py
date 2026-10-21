"""agent 会话内自检：写完文件先运行 check_step，问题当场改，不等整步结束后再开一次新会话重做。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from novel_writer import character_store as CS
from novel_writer import pipeline as P
from novel_writer import runner as R
from novel_writer import validators as V
from test_m3 import isolated, new_book  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "entrypoints"))
import check_step  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_restore_flag():
    yield
    V.set_restore_enabled(True)


def _rec(cid, name):
    return {"id": cid, "name": name, "role": "supporting", "introduced_chapter": 1, "state_log": [],
            "current_state": {"as_of_chapter": 1}}


def _setup(ws):
    base = ws.path("characters_dir")
    base.mkdir(parents=True, exist_ok=True)
    for cid, name in (("char_001", "甲"), ("char_002", "乙")):
        (base / f"{cid}.md").write_text(CS._encode(_rec(cid, name)) + CS._body(_rec(cid, name)), encoding="utf-8")
    CS.backup_dir(base, ws.studio / "backup" / "characters")
    return base


def test_selfcheck_ok_and_reports_problems_without_restoring(isolated, capsys):
    ws = new_book(isolated)
    base = _setup(ws)
    assert check_step.main(["--workspace", str(ws.root), "--step", "ch02.sync"]) == 0
    assert capsys.readouterr().out.strip() == "OK"
    (base / "char_002.md").write_text("# 乙\n只有叙述", encoding="utf-8")
    assert check_step.main(["--workspace", str(ws.root), "--step", "ch02.sync"]) == 1
    out = capsys.readouterr().out
    assert "char_002.md" in out and "直到输出 OK" in out
    # 自检不还原：agent 还在工作，应当自己改，而不是被备份覆盖
    assert (base / "char_002.md").read_text(encoding="utf-8").startswith("# 乙")


def test_selfcheck_applies_the_same_deterministic_repair(isolated, capsys):
    ws = new_book(isolated)
    base = _setup(ws)
    p = base / "char_001.md"
    p.write_text("﻿" + p.read_text(encoding="utf-8"), encoding="utf-8")   # BOM
    assert check_step.main(["--workspace", str(ws.root), "--step", "ch02.sync"]) == 0
    assert not p.read_text(encoding="utf-8").startswith("﻿")


def test_selfcheck_unknown_step_and_workspace(isolated, capsys):
    ws = new_book(isolated)
    assert check_step.main(["--workspace", str(ws.root), "--step", "nope"]) == 2
    assert check_step.main(["--workspace", str(isolated / "不存在"), "--step", "x"]) == 2


def test_prompt_asks_for_selfcheck_only_for_steps_with_content_checks(isolated):
    ws = new_book(isolated)
    plan = {s.id: s for s in P.build_plan(ws)}
    sync = R.build_prompt(ws, plan["ch02.sync"])
    assert "自检（写回执之前必须做）" in sync and "check_step.py" in sync and "--step \"ch02.sync\"" in sync
    assert sys.executable in sync and "直到输出 OK" in sync
    assert "自检（写回执之前必须做）" not in R.build_prompt(ws, plan["ch02.write_chapter"])


def test_script_runs_standalone_as_subprocess(isolated):
    ws = new_book(isolated)
    _setup(ws)
    r = subprocess.run([sys.executable, str(ROOT / "entrypoints" / "check_step.py"),
                        "--workspace", str(ws.root), "--step", "ch02.sync"],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(isolated))
    assert r.returncode == 0 and r.stdout.strip() == "OK", r.stderr
