"""灵感构思 B8：完整模式——每轮附上当前项目全部产出的完整内容，总长不超过 2 万字。"""

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm import options as O
from novel_writer.brainstorm.workspace import BrainstormWorkspace


class FakeRunner:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, sid):
        self.prompts.append(prompt)
        return "好", None


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "港")
    w.create("港")
    C.put_module(w, "premise", "命题标记甲")
    C.put_module(w, "brief", {"genre": "悬疑"})
    C.put_module(w, "cast", {"lin": {"name": "林", "role": "角色标记乙", "relations": []}})
    C.put_module(w, "plot", {"acts": [{"title": "起", "beats": ["转折标记丙"]}], "twists": [], "endings": []})
    C.put_module(w, "world", {"rules": [{"text": "规则标记丁", "cost": "代价标记戊"}], "locations": [],
                              "factions": [], "timeline": [], "taboos": []})
    C.put_module(w, "ideas", [{"id": "i1", "text": "灵感标记己", "tags": [], "source": "author", "status": "keep"},
                              {"id": "i2", "text": "丢弃标记庚", "tags": [], "source": "author", "status": "drop"}])
    C.put_module(w, "open_questions", [{"id": "q1", "text": "问题标记辛", "closed": False}])
    O.create(w, "备选标记壬", "premise", "备选内容标记癸", "备注标记子")
    return w


def test_full_scope_can_be_selected_now(ws):
    assert CH.set_scope(ws, "full") == "full"
    assert CH.get_scope(ws) == "full"


def test_full_mode_prompt_contains_every_output_in_full(ws):
    CH.set_scope(ws, "full")
    prompt = CH.build_prompt(ws, "继续", "追问")
    for marker in ("命题标记甲", "悬疑", "角色标记乙", "转折标记丙", "规则标记丁", "代价标记戊",
                   "灵感标记己", "问题标记辛", "备选标记壬", "备选内容标记癸", "备注标记子"):
        assert marker in prompt, marker
    assert "丢弃标记庚" not in prompt                     # 已丢弃的灵感不附


def test_summary_mode_still_omits_full_details(ws):
    prompt = CH.build_prompt(ws, "继续", "追问")
    assert "角色标记乙" not in prompt                     # 摘要只有角色名
    assert "代价标记戊" not in prompt
    assert "备选内容标记癸" not in prompt


def test_budget_reports_size_and_no_omission_when_small(ws):
    b = CH.context_budget(ws)
    assert b["limit"] == CH.FULL_LIMIT and b["omitted"] == []
    assert 0 < b["chars"] < CH.FULL_LIMIT


def test_limit_is_enforced_and_priority_is_kept(ws):
    # 放一段很大的灵感，把总长推到上限以上
    big = "灵感填充" * 5000                                  # 2 万字以上
    C.put_module(ws, "ideas", [{"id": f"i{n}", "text": big, "tags": [], "source": "author", "status": "keep"}
                               for n in range(3)])
    CH.set_scope(ws, "full")
    text, omitted = CH.full_context(ws)
    assert len(text) <= CH.FULL_LIMIT + 200              # 截断标记略超一点点
    assert "命题标记甲" in text and "角色标记乙" in text  # 高优先级内容保留
    assert omitted and any(o.startswith("灵感") for o in omitted)
    assert "开放问题" in " ".join(omitted) or "问题标记辛" not in text


def test_full_mode_notes_omitted_sections_in_prompt(ws):
    big = "填充" * 12000
    C.put_module(ws, "ideas", [{"id": "i1", "text": big, "tags": [], "source": "author", "status": "keep"}])
    CH.set_scope(ws, "full")
    prompt = CH.build_prompt(ws, "继续", "追问")
    assert "因字数上限" in prompt


def test_send_in_full_mode_uses_full_prompt(ws):
    CH.set_scope(ws, "full")
    runner = FakeRunner()
    CH.send(ws, "继续", runner=runner)
    assert "备选内容标记癸" in runner.prompts[0]


def test_full_mode_proposals_still_need_confirmation(ws):
    CH.set_scope(ws, "full")
    before = C.content_hash(ws)
    CH.send(ws, "改", runner=lambda p, s: ("好", None))
    assert C.content_hash(ws) == before


def test_api_budget_and_scope_select(tmp_path):
    root = BA.create_project("雾", base=str(tmp_path))["data"]["root"]
    assert BA.scope_set(root, "full")["ok"]
    res = BA.context_budget(root)
    assert res["ok"] and "chars" in res["data"] and "limit" in res["data"]
    assert "full" in BA.scope_get(root)["data"]["implemented"]
