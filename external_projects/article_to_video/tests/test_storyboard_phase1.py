"""分镜改进·第一期（以测试文章 tests/fixtures/harness_article.md 为样本）：

  * 箭头语义：只用 ↑ 的链为堆叠层级（direction=up），不得画成流程；其余为流程；
  * 回路：重复节点、回路词（再次/回到/循环）、末尾省略号；
  * 超限不静默截断：流程、表格、列表按 part 分页（续镜），原文项目必须全部出现在某个镜头中；
  * 堆叠层图渲染；结构整体一次显示；分页页码；
  * 规则版：堆叠用 stack，超限写入说明而不是静默截断。
"""
from pathlib import Path

import pytest

from article_to_video.render.renderers import render_scene_frames
from article_to_video.source.ascii_graph import classify_code
from article_to_video.source.md_parse import parse_markdown
from article_to_video.visual.director import _build_beat
from article_to_video.visual.director_llm import absence_warnings, beat_context, parse_window
from article_to_video.visual.shots import sentence_id
from article_to_video.visual.states import visible_counts

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"


@pytest.fixture(scope="module")
def blocks():
    return parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]


def _by_id(blocks, bid):
    return next(b for b in blocks if b["id"] == bid)


# ---------- 箭头语义与回路 ----------

def test_upward_chain_is_stack_in_written_order():
    info = classify_code(["Application Layer", "        ↑", "AgentOS / Harness Layer", "        ↑",
                          "Model Layer", "        ↑", "Compute Layer"])
    assert info["shape"] == "chain" and info["direction"] == "up"
    assert info["nodes"] == ["Application Layer", "AgentOS / Harness Layer", "Model Layer", "Compute Layer"]
    assert info["loop"] is False


def test_forward_chain_is_flow():
    info = classify_code(["Model", " ↓", "Harness", " ↓", "Environment"])
    assert info["direction"] == "forward" and info["loop"] is False


def test_loop_signals_repeat_word_and_ellipsis():
    assert classify_code(["感知 → 思考 → 行动 → 再次行动"])["loop"] is True            # 回路词
    assert classify_code(["Model ↓ Harness ↓ Experience ↓ ……"])["loop"] is True       # 末尾省略号
    assert classify_code(["资本 → AI → 资本扩大资本"])["loop"] is False               # 普通流程
    assert classify_code(["超级AI … → 数据中心 → 超级AI"])["loop"] is True            # 重复（保留旧行为）


def test_fixture_block_classification(blocks):
    layers = _by_id(blocks, "s25.p03")
    assert layers["shape"] == "chain" and layers["direction"] == "up"
    assert layers["nodes"][-1] == "Compute Layer"                                     # 底层在最后
    assert _by_id(blocks, "s24.p07")["node_count"] == 9                               # 不再静默截断到 6
    assert _by_id(blocks, "s03.p19")["loop"] is True                                  # 含“再次行动”
    assert _by_id(blocks, "s10.p05")["loop"] is True                                  # 末尾 ……
    assert _by_id(blocks, "s17.p18")["loop"] is False                                 # 线性的进化链
    assert len(_by_id(blocks, "s21.p03")["rows"]) == 16
    assert len(_by_id(blocks, "s03.p08")["items"]) == 9


# ---------- 状态：结构整体一次显示 ----------

def test_structured_scenes_show_all_items_at_once():
    items = ["甲", "乙", "丙"]
    assert visible_counts({"type": "table", "items": items}) == [3]
    assert visible_counts({"type": "diagram", "items": items}) == [3]
    assert visible_counts({"type": "stack", "items": items}) == [3]
    assert visible_counts({"type": "list", "items": items}) == [3]
    assert visible_counts({"type": "keypoint", "items": items}) == [1, 2, 3]   # 要点仍逐条出现
    assert visible_counts({"type": "formula", "items": items}) == [0, 1, 2, 3]  # 公式先出现，解释逐条
    assert visible_counts({"type": "table", "items": []}) == [0]


# ---------- 模型版：分页、覆盖、堆叠 ----------

NODES = ["步骤一", "步骤二", "步骤三", "步骤四", "步骤五", "步骤六", "步骤七", "步骤八", "步骤九"]
CHAIN = {"id": "blkC", "kind": "code", "shape": "chain", "direction": "forward", "nodes": NODES,
         "loop": False, "section": "sx"}
STACK = {"id": "blkS", "kind": "code", "shape": "chain", "direction": "up",
         "nodes": ["应用层", "平台层", "模型层", "算力层"], "loop": False, "section": "sy"}


def _script_beat(bid, sentences, refs):
    return {"id": bid, "role": "claim", "source_refs": refs, "sentences": [{"text": t, "quote": False} for t in sentences]}


def _ctx(beat, blocks):
    return {beat["id"]: beat_context(beat, blocks)}


def _window(obj, beat, blocks):
    return parse_window(obj, [beat], _ctx(beat, blocks), prev=None, ill_used=0, allowance=10, shots=2)


def _s(bid, i):
    return sentence_id(bid, i)


def test_flow_split_into_two_pages_covers_all_nodes():
    bid = "P09.b01"
    beat = _script_beat(bid, ["先看" + "、".join(NODES[:6]) + "这六步。", "再看" + "、".join(NODES[6:]) + "。"], ["blkC"])
    obj = {"shots": [{"sentences": [_s(bid, 0)], "kind": "flow", "part": [1, 6]},
                     {"sentences": [_s(bid, 1)], "kind": "flow", "part": [7, 9]}]}
    scenes, errs, _ = _window(obj, beat, [CHAIN])
    assert errs == []
    assert [s["items"] for s in scenes] == [NODES[:6], NODES[6:]]
    assert scenes[0]["page"] == {"index": 1, "total": 2} and scenes[1]["page"] == {"index": 2, "total": 2}
    assert scenes[0]["loop"] is False


def test_missing_nodes_are_reported_not_silently_dropped():
    bid = "P09.b01"
    beat = _script_beat(bid, ["先看" + "、".join(NODES[:6]) + "这六步。", "补充说明。"], ["blkC"])
    obj = {"shots": [{"sentences": [_s(bid, 0)], "kind": "flow", "part": [1, 6]},
                     {"sentences": [_s(bid, 1)], "kind": "keypoint", "items": ["补充说明"]}]}
    scenes, errs, _ = _window(obj, beat, [CHAIN])
    assert scenes == []
    assert any("未展示第 7、8、9 项" in e for e in errs)


def test_page_range_out_of_bounds_is_an_error():
    bid = "P09.b01"
    beat = _script_beat(bid, ["先看" + "、".join(NODES) + "。"], ["blkC"])
    obj = {"shots": [{"sentences": [_s(bid, 0)], "kind": "flow", "part": [1, 12]}]}
    _, errs, _ = _window(obj, beat, [CHAIN])
    assert any("超出范围" in e for e in errs)


def test_upward_chain_uses_stack_and_flow_is_rejected():
    bid = "P09.b02"
    beat = _script_beat(bid, ["由下往上看，" + "、".join(STACK["nodes"]) + "逐层堆叠。"], ["blkS"])
    ok, errs, _ = _window({"shots": [{"sentences": [_s(bid, 0)], "kind": "stack"}]}, beat, [STACK])
    assert errs == [] and ok[0]["type"] == "stack" and ok[0]["items"] == STACK["nodes"]
    scenes2, errs2, _ = _window({"shots": [{"sentences": [_s(bid, 0)], "kind": "flow"}]}, beat, [STACK])
    assert errs2 == [] and scenes2[0]["type"] == "keypoint" and scenes2[0]["fallback_from"] == "flow"
    assert absence_warnings(scenes2, {"beats": [beat]}, {bid: beat_context(beat, [STACK])}) == [
        f"{bid}：原文的流程或堆叠层级未在本段画面中出现（口播未提及，或模型选择了卡片），请在分镜页检查"]


def test_table_paging_with_fixture_covers_sixteen_rows(blocks):
    tbl = _by_id(blocks, "s21.p03")
    bid = "P21.b01"
    beat = _script_beat(bid, ["第一组对照。", "第二组对照。", "第三组对照。"], ["s21.p03"])
    obj = {"shots": [{"sentences": [_s(bid, 0)], "kind": "table", "part": [1, 6]},
                     {"sentences": [_s(bid, 1)], "kind": "table", "part": [7, 12]},
                     {"sentences": [_s(bid, 2)], "kind": "table", "part": [13, 16]}]}
    scenes, errs, _ = _window(obj, beat, [tbl])
    assert errs == []
    shown = [r for s in scenes for r in s["rows"]]
    assert len(shown) == len(tbl["rows"]) == 16
    assert all(s["page"]["total"] == 3 for s in scenes)


def test_list_paging_with_fixture_covers_nine_items(blocks):
    lst = _by_id(blocks, "s03.p08")                 # 9 项，每镜上限 5
    bid = "P03.b01"
    beat = _script_beat(bid, ["第一批身份。", "第二批身份。"], ["s03.p08"])
    obj = {"shots": [{"sentences": [_s(bid, 0)], "kind": "list", "part": [1, 5]},
                     {"sentences": [_s(bid, 1)], "kind": "list", "part": [6, 9]}]}
    scenes, errs, _ = _window(obj, beat, [lst])
    assert errs == []
    assert [i for s in scenes for i in s["items"]] == lst["items"]


def test_beat_context_offers_stack_not_flow_for_upward_chain():
    beat = _script_beat("P09.b03", ["层级说明。"], ["blkS"])
    ctx = beat_context(beat, [STACK])
    assert "stack" in ctx["allowed"] and "flow" not in ctx["allowed"]
    assert any("自上而下" in n for n in ctx["notes"])


# ---------- 渲染 ----------

def test_stack_renders_all_layers_and_page_tag():
    
    scene = {"type": "stack", "items": ["应用层", "平台层", "模型层", "算力层"], "emphasis": [],
             "page": {"index": 1, "total": 2}}
    frames = render_scene_frames(scene, (1280, 720), "landscape", "ink")
    assert len(frames) == 1 and frames[0].getbbox() is not None


# ---------- 规则版 ----------

def test_rules_director_uses_stack_and_records_truncation(blocks):
    lst = _by_id(blocks, "s03.p08")
    beat = {"id": "P03.b01", "role": "claim", "source_refs": ["blkS"], "sentences": [{"text": "x", "quote": False}]}
    sc = _build_beat(beat, 1, [STACK], illustrate=False)
    assert sc["type"] == "stack" and sc["items"] == STACK["nodes"]
    sc2 = _build_beat({"id": "P03.b02", "role": "claim", "source_refs": ["s03.p08"],
                       "sentences": [{"text": "y", "quote": False}]}, 1, [lst], illustrate=False)
    # R27：列表不再被静默截断到 5 条（自适应布局保证全部显示），所有条目都在镜头里，没有截断记录
    assert len(sc2["items"]) == len(lst["items"]) and "truncated" not in sc2
