"""tests/test_html_export.py — 第十三轮：模拟结果导出为静态 HTML
网页（`world_simulator/html_export.py`）单测。

对应 `next_doc/world_simulator_thirteenth_round_html_export_plan.md`
第 7 节验收点：意图/背景、因果线（含降级路径）、时间线正序、复盘报告
存在与不存在两种情况、`state0` 单独一步不报错、多主体 `per_entity`
分组展示、graphviz 渲染异常时的降级。
"""

from __future__ import annotations

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pytest
except ImportError:
    pytest = None

from world_simulator import causal_graph as cg_mod
from world_simulator import html_export as he
from world_simulator import retrospective as retro
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimStore, now_iso


def _make_manifest(sim_id: str, **settings_overrides) -> SimManifest:
    settings = {
        "desired_state": {
            "conditions": ["财务自由"],
            "constraints": ["不能借钱"],
            "assumptions": ["市场稳定"],
        },
        "objectives": ["资产净值"],
        "resource_fields": ["cash"],
        "causal_lines": [{"id": "line_biz", "description": "业务发展线"}],
    }
    settings.update(settings_overrides)
    return SimManifest(
        sim_id=sim_id,
        template="life_sim",
        intent="我想模拟辞职创业",
        title="辞职创业模拟",
        created_at=now_iso(),
        updated_at=now_iso(),
        settings=settings,
    )


def _seed_two_step_sim(tmp_path: Path, sim_id: str = "sim_export") -> SimStore:
    store = SimStore.for_root(tmp_path, sim_id)
    store.save_manifest(_make_manifest(sim_id))

    option = ChoiceOption(id="a", label="全职创业", description="All in，辞职专心做")
    s0 = SimState(
        step=0,
        summary="起点：在职、有存款",
        vars={"cash": 1000, "stage": "在职"},
        options=[option],
        chosen_option_id="a",
        chosen_by="user",
        chosen_reason="窗口期不等人",
    )
    s1 = SimState(
        step=1,
        summary="辞职了",
        narrative="递交了辞呈，正式开始创业",
        vars={"cash": 900, "stage": "创业"},
        time_label="第 1 个月",
        key_drivers=["市场机会窗口"],
        causal_links=[
            {"driver": "市场机会窗口", "effect": "决定辞职", "line_id": "line_biz"}
        ],
        capabilities_gained=[
            {
                "capability": "能独立谈客户",
                "capability_kind": "technology",
                "first_occurrence": True,
                "enables": ["更快拿到第一个客户"],
            }
        ],
        major_decision=True,
    )
    store.append_state(s0)
    store.append_state(s1)
    return store


def test_export_includes_intent_and_background(tmp_path):
    _seed_two_step_sim(tmp_path)
    html = he.export_simulation_html(tmp_path, "sim_export")
    assert "辞职创业模拟" in html
    assert "我想模拟辞职创业" in html
    assert "财务自由" in html and "不能借钱" in html and "市场稳定" in html
    assert "资产净值" in html


def test_timeline_is_chronological_ascending(tmp_path):
    _seed_two_step_sim(tmp_path)
    html = he.export_simulation_html(tmp_path, "sim_export")
    assert "STEP 0" in html and "STEP 1" in html
    # 正序：step 0 的内容必须出现在 step 1 之前，这是本轮功能的核心
    # 要求（区别于详情页现有的倒序展示）。
    assert html.index("STEP 0") < html.index("STEP 1")
    # 选择信息挂在"产生下一个状态之前"的那一步展示，同 app.py 既有
    # 的记录方式一致。
    assert "全职创业" in html
    assert "major_decision" not in html.lower()  # 不泄露内部字段名
    assert "重大决策" in html  # 但应该有可读的中文标注


def test_causal_overview_renders_declared_lines_and_graph(tmp_path):
    _seed_two_step_sim(tmp_path)
    html = he.export_simulation_html(tmp_path, "sim_export")
    assert "因果线总览" in html
    assert "line_biz" in html
    assert "业务发展线" in html


def test_causal_graph_falls_back_to_text_when_svg_rendering_fails(tmp_path, monkeypatch):
    """模拟"`graphviz` 包/`dot` 二进制不可用"的场景：直接让
    `_edges_to_dot()`（SVG 渲染路径唯一依赖的输入构造函数）抛异常，
    断言 `_render_causal_graph_visual()` 捕获后降级为文字列表、且
    不影响导出的其它章节继续渲染成功（对应规划文档第 4 节的降级
    路径要求）。
    """
    _seed_two_step_sim(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("dot 二进制不可用（模拟环境异常）")

    monkeypatch.setattr(he, "_edges_to_dot", _boom)

    html = he.export_simulation_html(tmp_path, "sim_export")
    # 降级路径：因果线数据本身还在（以文字摘要形式），没有内嵌 SVG。
    assert "<svg" not in html
    assert "因果线图形化渲染不可用" in html
    assert "line_biz" in html
    # 页面其余章节依然渲染成功，不因为这一步失败而整体报错。
    assert "辞职创业模拟" in html
    assert "STEP 1" in html


def test_causal_graph_renders_inline_svg_when_available(tmp_path):
    """graphviz 环境可用时（本项目环境已验证 `dot` 二进制存在），
    走正常路径应该产出内嵌 SVG，而不是文字降级版本。
    """
    if shutil.which("dot") is None:
        pytest.skip("Graphviz dot binary not available")
    _seed_two_step_sim(tmp_path)
    html = he.export_simulation_html(tmp_path, "sim_export")
    assert "<svg" in html


def test_export_without_retrospective_omits_section(tmp_path):
    _seed_two_step_sim(tmp_path)
    html = he.export_simulation_html(tmp_path, "sim_export")
    assert "复盘报告" not in html


def test_export_with_retrospective_includes_report(tmp_path):
    _seed_two_step_sim(tmp_path)
    report = retro.RetrospectiveReport(
        turning_points=[{"step": 1, "chosen_option": "全职创业", "why": "现金流开始下降"}],
        what_went_well=[{"point": "果断辞职", "evidence": "第 1 步"}],
        what_to_reflect_on=[{"point": "现金储备偏薄", "evidence": "cash 从 1000 降到 900"}],
        lessons=[{"lesson": "创业前应留够半年生活费", "source": "what_to_reflect_on"}],
        caveats=["这是基于这次模拟内部记录的总结，不是对你个人能力的心理分析"],
    )
    record = retro.RetrospectiveRecord(
        id="r1", sim_id="sim_export", branch="main", up_to_step=1,
        created_at=now_iso(), report=report,
    )
    retro._save_append(tmp_path, "sim_export", record)

    html = he.export_simulation_html(tmp_path, "sim_export")
    assert "复盘报告" in html
    assert "现金流开始下降" in html
    assert "果断辞职" in html
    assert "现金储备偏薄" in html
    assert "创业前应留够半年生活费" in html


def test_state0_only_no_chosen_fields_does_not_crash(tmp_path):
    sim_id = "sim_state0_only"
    store = SimStore.for_root(tmp_path, sim_id)
    store.save_manifest(_make_manifest(sim_id))
    store.append_state(SimState(step=0, summary="仅有初始状态"))

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "仅有初始状态" in html
    assert "STEP 0" in html
    assert "STEP 1" not in html


def test_per_entity_desired_state_grouped_display(tmp_path):
    sim_id = "sim_multi_entity"
    store = SimStore.for_root(tmp_path, sim_id)
    manifest = _make_manifest(
        sim_id,
        multi_entity_mode=True,
        desired_state={
            "per_entity": {
                "甲方": {"conditions": ["签下合同"]},
                "乙方": {"conditions": ["价格更低"], "constraints": ["预算有限"]},
            }
        },
    )
    store.save_manifest(manifest)
    store.append_state(SimState(step=0, summary="谈判开始", vars={}))

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "各主体各自的理想状态" in html
    assert "甲方" in html and "签下合同" in html
    assert "乙方" in html and "价格更低" in html and "预算有限" in html


def test_branch_fork_note_rendered_when_not_main(tmp_path):
    from world_simulator import branch_manager as bm

    sim_id = "sim_branching"
    store = SimStore.for_root(tmp_path, sim_id)
    store.save_manifest(_make_manifest(sim_id))
    store.append_state(SimState(step=0, summary="起点", vars={}))
    store.append_state(
        SimState(
            step=1, summary="走向 A",
            options=[ChoiceOption(id="a", label="方向A"), ChoiceOption(id="b", label="方向B")],
        )
    )
    new_branch = bm.fork_branch(
        tmp_path, sim_id, from_step=0, source_branch="main", branch_id="alt", switch=False
    )

    html = he.export_simulation_html(tmp_path, sim_id, branch=new_branch)
    assert "从 main 分支" in html
    assert "第 0 步分叉而来" in html


def test_causal_overview_breakdown_by_line_ascending_and_separate_from_timeline(tmp_path):
    """第十四轮：「因果线总览」要按线展示时间正序的点序列 + 未来因果树，
    并且和「时间线」两节分开渲染，不混排在一起。
    """
    sim_id = "sim_causal_breakdown"
    store = SimStore.for_root(tmp_path, sim_id)
    manifest = _make_manifest(
        sim_id,
        causal_lines=[
            {
                "id": "line_biz",
                "label": "业务发展线",
                "future_tree": {
                    "branches": [
                        {"id": "b1", "description": "快速扩张", "likelihood": "high", "status": "active"},
                        {"id": "b2", "description": "收缩关停", "likelihood": "low", "status": "resolved"},
                    ]
                },
            }
        ],
    )
    store.save_manifest(manifest)
    store.append_state(
        SimState(
            step=0, summary="起点", vars={},
            line_updates={"line_biz": {"time_label": "第 1 月", "summary": "起步筹备", "trend": "steady"}},
        )
    )
    store.append_state(
        SimState(
            step=1, summary="推进", vars={},
            line_updates={"line_biz": {"time_label": "第 2 月", "summary": "签下首单", "trend": "accelerating"}},
            causal_links=[{"driver": "客户信任", "effect": "签单", "line_id": "line_biz"}],
        )
    )

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "业务发展线" in html
    # 按线的时间点序列必须正序：第 1 月早于第 2 月出现。
    assert html.index("第 1 月") < html.index("第 2 月")
    assert "未来因果树" in html
    assert "快速扩张" in html and "收缩关停" in html
    assert "关联因果链" in html and "客户信任" in html
    # 「因果线总览」和「时间线（正序）」是两个独立小节，且总览在前。
    assert html.index("因果线总览") < html.index("时间线（正序）")


def test_causal_overview_breakdown_degrades_for_undeclared_line_ids(tmp_path):
    """没有在 `causal_lines` 里声明、但历史 `line_updates` 里实际出现过的
    line_id，也要能在按线总览里退化展示出来（label 退化为 id 本身）。
    """
    sim_id = "sim_causal_undeclared"
    store = SimStore.for_root(tmp_path, sim_id)
    store.save_manifest(_make_manifest(sim_id, causal_lines=[]))
    store.append_state(
        SimState(
            step=0, summary="起点", vars={},
            line_updates={"line_ghost": {"time_label": "第 1 步", "summary": "自发出现的线"}},
        )
    )

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "line_ghost" in html
    assert "自发出现的线" in html


def test_html_escapes_user_supplied_free_text(tmp_path):
    sim_id = "sim_escape"
    store = SimStore.for_root(tmp_path, sim_id)
    manifest = _make_manifest(sim_id)
    manifest.intent = "<script>alert(1)</script>"
    manifest.title = "带 <b>标签</b> 的标题"
    store.save_manifest(manifest)
    store.append_state(SimState(step=0, summary="s0", vars={}))

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_field_ledger_renders_table_with_resource_and_non_resource_icons(tmp_path):
    """第十五轮阶段三（3.6 节）：记账表格要展示字段/类型（含图标）/
    变化前→变化后/原因；`resource_fields` 声明过的字段用 💰，其它数值
    指标用 📈/📉，同一套底层数据结构。"""
    sim_id = "sim_ledger"
    store = SimStore.for_root(tmp_path, sim_id)
    store.save_manifest(_make_manifest(sim_id, resource_fields=["cash"]))
    store.append_state(SimState(step=0, summary="起点", vars={"cash": 1000, "tech": 60}))
    store.append_state(
        SimState(
            step=1,
            summary="签单 + 调优",
            vars={"cash": 1500, "tech": 65},
            field_ledger=[
                {
                    "field": "cash",
                    "kind": "increase",
                    "amount": 500,
                    "value_before": 1000,
                    "value_after": 1500,
                    "reason": "签下新客户，预付款到账",
                },
                {
                    "field": "tech",
                    "kind": "increase",
                    "amount": 5,
                    "value_before": 60,
                    "value_after": 65,
                    "reason": "完成一轮模型调优",
                },
            ],
        )
    )

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "📒 记账" in html
    assert "💰 收入" in html  # cash 声明为 resource_fields，用资源措辞
    assert "📈 提升" in html  # tech 未声明，用非资源指标措辞
    assert "签下新客户，预付款到账" in html
    assert "完成一轮模型调优" in html
    assert "1000" in html and "1500" in html
    assert "60" in html and "65" in html


def test_field_ledger_absent_when_no_entries(tmp_path):
    """没有记账记录的步骤，「记账」小节整体不出现，不留空标题——同
    `key_drivers`/`causal_links` 现有处理方式一致（3.6 节末尾要求）。"""
    sim_id = "sim_ledger_empty"
    store = SimStore.for_root(tmp_path, sim_id)
    store.save_manifest(_make_manifest(sim_id))
    store.append_state(SimState(step=0, summary="起点", vars={"cash": 1000}))
    store.append_state(SimState(step=1, summary="平淡的一步", vars={"cash": 1000}))

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "📒 记账" not in html


def test_ledger_violations_render_with_transparent_correction_notice(tmp_path):
    """修正一次之后仍剩余的 `ledger_violations` 要在表格下方用醒目
    提示列出，措辞要写清楚"已尝试自动修正一次"（3.6 节）。"""
    sim_id = "sim_ledger_violation"
    store = SimStore.for_root(tmp_path, sim_id)
    store.save_manifest(_make_manifest(sim_id, resource_fields=["cash"]))
    store.append_state(SimState(step=0, summary="起点", vars={"cash": 1000}))
    store.append_state(
        SimState(
            step=1,
            summary="账没对上",
            vars={"cash": 1400},
            field_ledger=[
                {
                    "field": "cash",
                    "kind": "increase",
                    "amount": 500,
                    "value_before": 1000,
                    "value_after": 1500,
                    "reason": "签下新客户",
                }
            ],
            ledger_violations=[
                {
                    "field": "cash",
                    "issue": "end_mismatch_with_actual",
                    "expected": 1500,
                    "actual": 1400,
                }
            ],
        )
    )

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "已尝试自动修正一次" in html
    assert "末笔变化后值与最终实际值不符" in html
    assert "1500" in html and "1400" in html


def test_field_ledger_and_violations_absent_when_empty(tmp_path):
    """没有不一致提示的步骤，提示小节也不出现（即便有正常记账）。"""
    sim_id = "sim_ledger_no_violation"
    store = SimStore.for_root(tmp_path, sim_id)
    store.save_manifest(_make_manifest(sim_id, resource_fields=["cash"]))
    store.append_state(SimState(step=0, summary="起点", vars={"cash": 1000}))
    store.append_state(
        SimState(
            step=1,
            summary="正常记账",
            vars={"cash": 1500},
            field_ledger=[
                {
                    "field": "cash",
                    "kind": "increase",
                    "amount": 500,
                    "value_before": 1000,
                    "value_after": 1500,
                    "reason": "签下新客户",
                }
            ],
        )
    )

    html = he.export_simulation_html(tmp_path, sim_id)
    assert "📒 记账" in html
    assert "已尝试自动修正一次" not in html
