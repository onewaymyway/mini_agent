"""大纲先行 O3：写作接入（输入与指令）、关键内容核对、定向修正、偏离记录、交给作者。"""

import json
import re
import sys
from pathlib import Path

import pytest

from novel_writer import blueprint_store as BS
from novel_writer import brief as BR
from novel_writer import brief_check as BC
from novel_writer import config, pipeline, steps as S, tool_api
from novel_writer.runner import NeedsUser
from novel_writer.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
from test_outline_first_o1 import ActInvoker, BLUEPRINT, _act_json, good  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


@pytest.fixture
def ws(tmp_path, monkeypatch):
    r = tool_api.create_novel("关键内容核对", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3,
                              writing_mode="outline_first")
    w = Workspace(r["data"]["workspace"])
    monkeypatch.setattr(BS, "load_for_ws", lambda _ws, strict=False: BLUEPRINT)
    monkeypatch.setattr(BR, "known_names", lambda _ws: ["李凌云"])
    monkeypatch.setattr(BR, "known_foreshadow", lambda _ws: ["F1"])
    BR.generate_act(w, 1, 1, 3, ActInvoker([_act_json([1, 2, 3])]))
    chapter = w.chapter_path(1, "章1", 3)
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text("# 第1章 章1\n\n" + "正文" * 200 + "\n", encoding="utf-8")
    return w


class Scripted:
    """按 skill 分派：novel-planner 为核对（写 check.json），revision-editor 为修正（写同名章节）。
    judge：每次核对返回的结构列表（用完重复最后一个）。fix：修正时返回的章节文本（None 表示不写文件）。"""

    def __init__(self, judges, fix_text=None, fix_length=None):
        self.judges = list(judges)
        self.fix_text = fix_text
        self.fix_length = fix_length
        self.judge_calls = 0
        self.fix_calls = 0
        self.prompts = []

    def __call__(self, *, skill, prompt, export_dir, step_id):
        self.prompts.append((skill, prompt))
        if skill == "novel-planner":
            self.judge_calls += 1
            out = Path(re.search(r"写入文件：(\S+)", prompt).group(1))
            verdict = self.judges[min(self.judge_calls - 1, len(self.judges) - 1)]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(verdict if isinstance(verdict, str) else json.dumps(verdict, ensure_ascii=False),
                           encoding="utf-8")
        elif skill == "revision-editor":
            self.fix_calls += 1
            if self.fix_text is None:
                return
            name = re.search(r"正文路径】(.+?)（", prompt).group(1).strip()
            Path(export_dir).mkdir(parents=True, exist_ok=True)
            text = self.fix_text if self.fix_length is None else "正" * self.fix_length
            (Path(export_dir) / Path(name).name).write_text(text, encoding="utf-8")


def _all_ok(b, deviations=()):
    items = {it["id"]: {"implemented": True, "evidence": "正文有依据"} for it in BC.key_items(b)}
    return {"items": [{"id": k, **v} for k, v in items.items()], "deviations": list(deviations)}


def _check_step(n=1):
    return S.StepDef(id=f"ch{n:02d}.brief_check", title="t", stage="p1", category=S.CAT_CHAPTER_REVIEW,
                     executor=S.EXECUTOR_PYTHON, handler="brief_check", chapter=n)


# ─────────────────────────── 计划与写作输入 ───────────────────────────

def test_outline_chapter_steps_check_before_machine_check(ws):
    plan = pipeline.build_plan(ws)
    by = {s.id: s for s in plan}
    assert "ch01.brief_check" in by
    assert by["ch01.brief_check"].deps == ("ch01.write_chapter",)
    # 写作 → 核对 → 乱码守卫 → 机器校验
    assert by["ch01.garble_guard"].deps == ("ch01.brief_check",)
    assert by["ch01.machine_check"].deps == ("ch01.garble_guard",)
    write = by["ch01.write_chapter"]
    assert "大纲规划/章节简述/第001章.json" in write.inputs
    assert "大纲先行" in write.instructions and "不得偏离" in write.instructions


def test_direct_plan_is_unchanged_by_o3(tmp_path):
    r = tool_api.create_novel("直写", novels_dir=str(tmp_path / "n9"), index_file=str(tmp_path / "s9.json"),
                              gear="G3", total_chapters_planned=2)
    w = Workspace(r["data"]["workspace"])
    ids = {s.id for s in pipeline.build_plan(w)}
    assert "ch01.brief_check" not in ids
    write = next(s for s in pipeline.build_plan(w) if s.id == "ch01.write_chapter")
    assert not any("章节简述" in i for i in write.inputs)


# ─────────────────────────── 核对与偏离 ───────────────────────────

def test_all_implemented_passes_and_records_nonkey_deviation(ws):
    b = BR.load_brief(ws, 1)
    brief_before = (BR.brief_json_path(ws, 1)).read_text(encoding="utf-8")
    inv = Scripted([_all_ok(b, deviations=[{"where": "第二场", "original": "在山门等待",
                                            "change": "在溪边等待", "reason": "正文需要水声"}])])
    out = BC.h_brief_check(ws, _check_step(), {}, invoker=inv)
    assert out["deviations"] == 1 and out["fixes"] == 0 and inv.fix_calls == 0
    devs = BR.load_deviations(ws, 1)
    assert len(devs) == 1 and devs[0]["change"] == "在溪边等待" and devs[0]["check"] == "ch01.brief_check"
    md = BR.brief_md_path(ws, 1).read_text(encoding="utf-8")
    assert "在溪边等待" in md and "偏离记录" in md
    # 偏离不改动简述 JSON：确认与写作输入都不会因此失效
    assert BR.brief_json_path(ws, 1).read_text(encoding="utf-8") == brief_before


def test_deviation_without_reason_is_recorded_as_unexplained(ws):
    b = BR.load_brief(ws, 1)
    inv = Scripted([_all_ok(b, deviations=[{"where": "结尾", "original": "a", "change": "b", "reason": ""}])])
    BC.h_brief_check(ws, _check_step(), {}, invoker=inv)
    assert BR.load_deviations(ws, 1)[0]["reason"] == "未说明"


def test_missing_key_item_triggers_targeted_fix_then_passes(ws):
    b = BR.load_brief(ws, 1)
    missing = _all_ok(b)
    missing["items"][0] = {"id": "e1", "implemented": False, "evidence": "正文没有这件事"}
    inv = Scripted([missing, _all_ok(b)], fix_text="修正后的正文" * 60)
    out = BC.h_brief_check(ws, _check_step(), {}, invoker=inv)
    assert out["fixes"] == 1 and inv.fix_calls == 1 and inv.judge_calls == 2
    chapter = next(c for c in Workspace(ws.root).list_chapters() if c.number == 1).path
    assert chapter.read_text(encoding="utf-8").startswith("修正后的正文")
    fix_prompt = next(p for s, p in inv.prompts if s == "revision-editor")
    assert "- e1（must_events）：" in fix_prompt


def test_still_missing_after_two_fixes_hands_over_to_author(ws):
    b = BR.load_brief(ws, 1)
    bad = _all_ok(b)
    bad["items"][0] = {"id": "e1", "implemented": False, "evidence": "没写"}
    inv = Scripted([bad], fix_text="修正" * 200)
    with pytest.raises(NeedsUser) as exc:
        BC.h_brief_check(ws, _check_step(), {}, invoker=inv)
    assert inv.fix_calls == BC.MAX_FIXES
    assert exc.value.items[0]["id"] == "e1" and exc.value.items[0]["evidence"] == "没写"


def test_fix_with_bad_length_is_not_written(ws):
    b = BR.load_brief(ws, 1)
    bad = _all_ok(b)
    bad["items"][0] = {"id": "e1", "implemented": False, "evidence": "没写"}
    original = next(c for c in Workspace(ws.root).list_chapters() if c.number == 1).path.read_text(encoding="utf-8")
    inv = Scripted([bad], fix_text="x", fix_length=3)
    with pytest.raises(RuntimeError, match="字数异常"):
        BC.h_brief_check(ws, _check_step(), {}, invoker=inv)
    assert next(c for c in Workspace(ws.root).list_chapters() if c.number == 1).path.read_text(encoding="utf-8") == original


def test_judge_retries_once_on_invalid_json(ws):
    b = BR.load_brief(ws, 1)
    inv = Scripted(["不是json", _all_ok(b)])
    out = BC.h_brief_check(ws, _check_step(), {}, invoker=inv)
    assert inv.judge_calls == 2 and out["deviations"] == 0


def test_missing_judgement_counts_as_unimplemented(ws):
    b = BR.load_brief(ws, 1)
    partial = {"items": [{"id": "e1", "implemented": True, "evidence": "x"}], "deviations": []}
    inv = Scripted([partial], fix_text="修正" * 200)
    with pytest.raises(NeedsUser):
        BC.h_brief_check(ws, _check_step(), {}, invoker=inv)


def test_key_items_cover_every_key_field(ws):
    b = BR.load_brief(ws, 1)
    kinds = [it["kind"] for it in BC.key_items(b)]
    assert kinds.count("must_events") == len(b["key"]["must_events"])
    assert kinds.count("must_states") == len(b["key"]["must_states"])
    assert kinds[-1] == "must_hook"
