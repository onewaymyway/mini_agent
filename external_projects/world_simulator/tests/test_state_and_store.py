"""tests/test_state_and_store.py — state_model / store 单元测试。

阶段一核心闭环里"不依赖 LLM"的那部分：数据结构的序列化/反序列化，
以及基于 `atomic_write` 的落盘/读回是否一致。engine.py 涉及 LLM 调用
的部分见 `test_engine.py`（用 monkeypatch 打桩 workflow 引擎）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimNotFoundError, SimStore, list_sim_ids, now_iso


def test_state_roundtrip():
    state = SimState(
        step=1,
        summary="摘要",
        narrative="发生了一些事",
        vars={"age": 21, "stage": "大学"},
        options=[ChoiceOption(id="a", label="选项A", description="去实习")],
        chosen_option_id="a",
        chosen_by="user",
    )
    d = state.to_dict()
    restored = SimState.from_dict(d)
    assert restored.step == 1
    assert restored.vars["age"] == 21
    assert restored.options[0].id == "a"
    assert restored.chosen_option_id == "a"


def test_choice_option_normalizes_urgency_and_preserves_action_reason_time_window():
    """阶段三十三第二批（4.2/4.3 节）：`action_reason`/`urgency`/
    `time_window` 应该正确序列化/反序列化；`urgency` 的归一化规则
    同 `risk_level`——不认识的取值退化为 `medium`，`None` 表示未声明，
    不伪造默认值。"""
    opt = ChoiceOption(
        id="a", label="转型", description="转向 AI 相关岗位",
        action_reason="利用已有能力降低转型成本",
        urgency="high", time_window="数周内",
    )
    restored = ChoiceOption.from_dict(opt.to_dict())
    assert restored.action_reason == "利用已有能力降低转型成本"
    assert restored.urgency == "high"
    assert restored.time_window == "数周内"

    # 未声明时保持 None/空字符串，不伪造默认值
    unset = ChoiceOption.from_dict({"id": "b", "label": "维持现状"})
    assert unset.urgency is None
    assert unset.action_reason == ""
    assert unset.time_window == ""

    # 不认识的取值退化为 medium（同 risk_level 的既有归一化规则）
    weird = ChoiceOption.from_dict({"id": "c", "label": "x", "urgency": "超级紧急"})
    assert weird.urgency == "medium"

    # critical 是合法取值，不会被归一化掉
    critical = ChoiceOption.from_dict({"id": "d", "label": "x", "urgency": "critical"})
    assert critical.urgency == "critical"


def test_choice_option_normalizes_action_type():
    """阶段三十三第六批（4.7 节）：`action_type` 只认
    `single`/`combo`/`conditional` 三个取值，不认识的取值/缺省都
    归一化为默认值 `single`（不像 `urgency` 那样用 `None` 表示
    "未声明"——`action_type` 本身默认就该是 `single`，不存在
    "未声明"这个中间状态）。"""
    default = ChoiceOption.from_dict({"id": "a", "label": "x"})
    assert default.action_type == "single"

    combo = ChoiceOption.from_dict({"id": "b", "label": "x", "action_type": "combo"})
    assert combo.action_type == "combo"

    conditional = ChoiceOption.from_dict({"id": "c", "label": "x", "action_type": "CONDITIONAL"})
    assert conditional.action_type == "conditional"

    weird = ChoiceOption.from_dict({"id": "d", "label": "x", "action_type": "multi_step"})
    assert weird.action_type == "single"

    restored = ChoiceOption.from_dict(
        ChoiceOption(id="e", label="x", action_type="combo").to_dict()
    )
    assert restored.action_type == "combo"


def test_choice_option_prerequisites_and_consequences_default_and_roundtrip():
    """第五轮方案 5.1 节：`prerequisites` 默认空列表，`consequences`
    默认 `None`；非默认值能正确序列化/反序列化。"""
    default = ChoiceOption.from_dict({"id": "a", "label": "x"})
    assert default.prerequisites == []
    assert default.consequences is None

    with_fields = ChoiceOption.from_dict(
        {
            "id": "b",
            "label": "x",
            "prerequisites": ["手头有 3 个月生活费缓冲"],
            "consequences": {"short_term": "收入下降", "long_term": "长期竞争力提升"},
        }
    )
    assert with_fields.prerequisites == ["手头有 3 个月生活费缓冲"]
    assert with_fields.consequences == {
        "short_term": "收入下降",
        "long_term": "长期竞争力提升",
    }

    restored = ChoiceOption.from_dict(with_fields.to_dict())
    assert restored.prerequisites == with_fields.prerequisites
    assert restored.consequences == with_fields.consequences


def test_choice_option_consequences_ignores_unknown_keys_and_blank_values():
    """`consequences` 只认 `short_term`/`long_term` 两个 key，且空
    字符串视为未声明（清理后整体为空则归一化为 `None`，不留一个
    空字典）。"""
    only_blank = ChoiceOption.from_dict(
        {"id": "a", "label": "x", "consequences": {"short_term": "", "unknown_key": "x"}}
    )
    assert only_blank.consequences is None

    partial = ChoiceOption.from_dict(
        {"id": "b", "label": "x", "consequences": {"short_term": "收入下降", "unknown_key": "x"}}
    )
    assert partial.consequences == {"short_term": "收入下降"}


def test_choice_option_prerequisites_strips_and_drops_blank_entries():
    opt = ChoiceOption.from_dict(
        {"id": "a", "label": "x", "prerequisites": ["  有缓冲  ", "", "   "]}
    )
    assert opt.prerequisites == ["有缓冲"]


def test_state_roundtrip_preserves_decision_reason():
    """阶段三十三第二批（4.2 节）：`SimState.decision_reason` 应该
    正确序列化/反序列化，旧数据（没有这个字段）落回空字符串。"""
    state = SimState(
        step=3, summary="s",
        options=[ChoiceOption(id="a", label="A"), ChoiceOption(id="b", label="B")],
        decision_reason="职业转型机会正在形成",
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.decision_reason == "职业转型机会正在形成"

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.decision_reason == ""


def test_state_roundtrip_preserves_option_warnings():
    """阶段三十三第三批（4.4/4.5 节）：`option_warnings` 应该正确
    序列化/反序列化，旧数据（没有这个字段）落回空列表。"""
    state = SimState(
        step=4,
        summary="s",
        options=[ChoiceOption(id="a", label="A")],
        option_warnings=[
            {"option_id": "a", "kind": "metric_adjustment_pattern", "note": "疑似指标调节"}
        ],
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.option_warnings == [
        {"option_id": "a", "kind": "metric_adjustment_pattern", "note": "疑似指标调节"}
    ]

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.option_warnings == []


def test_state_roundtrip_preserves_decision_opportunity():
    """阶段三十三第五批（4.10 节）：`decision_opportunity` 应该正确
    序列化/反序列化，`options` 为空/旧数据缺字段时落回 `None`。"""
    opportunity = {
        "trigger_line_ids": ["tech"],
        "trigger_node_ids": ["fast"],
        "decision_reason": "关键技术窗口即将关闭",
        "context_note": "",
    }
    state = SimState(
        step=4,
        summary="s",
        options=[ChoiceOption(id="a", label="A")],
        decision_reason="关键技术窗口即将关闭",
        decision_opportunity=opportunity,
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.decision_opportunity == opportunity
    assert restored.decision_reason == "关键技术窗口即将关闭"

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.decision_opportunity is None

    # decision_opportunity 不是 dict 的非法值同样兜底为 None，不报错
    malformed_restored = SimState.from_dict({"step": 0, "summary": "s", "decision_opportunity": "oops"})
    assert malformed_restored.decision_opportunity is None


def test_state_roundtrip_preserves_uncertain_fields():
    """阶段十一（4.3 节）：`uncertain_fields` 应该原样经过
    to_dict/from_dict 往返，旧数据（没有这个字段）也应该正常落回空
    列表，不影响加载。"""
    state = SimState(
        step=2,
        summary="s",
        vars={"startup_success_rate": 0.18},
        uncertain_fields=[
            {"field": "startup_success_rate", "confidence": "low", "note": "主观估计"}
        ],
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.uncertain_fields == [
        {"field": "startup_success_rate", "confidence": "low", "note": "主观估计"}
    ]

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.uncertain_fields == []


def test_state_roundtrip_preserves_key_drivers():
    """阶段十三（4.5 节）：`key_drivers` 应该原样经过 to_dict/from_dict
    往返，旧数据（没有这个字段）也应该正常落回空列表。"""
    state = SimState(
        step=3, summary="s",
        key_drivers=["市场需求超预期", "现金储备见底被迫收缩"],
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.key_drivers == ["市场需求超预期", "现金储备见底被迫收缩"]

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.key_drivers == []


def test_state_roundtrip_preserves_causal_links():
    """阶段十五（4.7 节）：`causal_links` 应该原样经过 to_dict/from_dict
    往返，旧数据（没有这个字段）也应该正常落回空列表；非字典项应该被
    跳过而不是报错中断。"""
    state = SimState(
        step=3, summary="s",
        key_drivers=["现金储备见底"],
        causal_links=[
            {
                "driver": "现金储备见底",
                "affected_fields": ["cash", "stage"],
                "effect": "被迫从「自由职业」转为「求稳定工作」",
            }
        ],
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.causal_links == [
        {
            "driver": "现金储备见底",
            "affected_fields": ["cash", "stage"],
            "effect": "被迫从「自由职业」转为「求稳定工作」",
        }
    ]

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.causal_links == []

    mixed_restored = SimState.from_dict(
        {"step": 0, "summary": "s", "causal_links": ["不是字典", {"driver": "x"}]}
    )
    assert mixed_restored.causal_links == [{"driver": "x"}]


def test_state_roundtrip_preserves_line_updates():
    """阶段二十二（4.13 节）：`line_updates` 应该原样经过
    to_dict/from_dict 往返，旧数据（没有这个字段）也应该正常落回空
    字典；非字典 value 应该被跳过而不是报错中断。"""
    state = SimState(
        step=3, summary="s",
        line_updates={
            "tech": {"time_label": "第 3 年", "summary": "AI 成本持续下降", "advanced": True},
            "negotiation": {"time_label": "第 2 轮", "summary": "双方各让一步"},
        },
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.line_updates == {
        "tech": {"time_label": "第 3 年", "summary": "AI 成本持续下降", "advanced": True},
        "negotiation": {"time_label": "第 2 轮", "summary": "双方各让一步"},
    }

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.line_updates == {}

    mixed_restored = SimState.from_dict(
        {"step": 0, "summary": "s", "line_updates": {"tech": "不是字典", "ok": {"summary": "s"}}}
    )
    assert mixed_restored.line_updates == {"ok": {"summary": "s"}}


def test_state_roundtrip_preserves_relation_violations():
    """阶段十六（4.8 节）：`relation_violations` 应该原样经过
    to_dict/from_dict 往返，旧数据（没有这个字段）也应该正常落回空
    列表；非字典项应该被跳过而不是报错中断。"""
    state = SimState(
        step=4, summary="s",
        relation_violations=[
            {"from": "cash", "to": "inventory.value", "delta_from": -100, "delta_to": 20}
        ],
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.relation_violations == [
        {"from": "cash", "to": "inventory.value", "delta_from": -100, "delta_to": 20}
    ]

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.relation_violations == []

    mixed_restored = SimState.from_dict(
        {"step": 0, "summary": "s", "relation_violations": ["不是字典", {"from": "x"}]}
    )
    assert mixed_restored.relation_violations == [{"from": "x"}]


def test_manifest_roundtrip():
    ts = now_iso()
    m = SimManifest(
        sim_id="life_sim_abc123",
        template="life_sim",
        intent="模拟一个刚毕业的人生",
        title="毕业生的选择",
        created_at=ts,
        updated_at=ts,
    )
    d = m.to_dict()
    restored = SimManifest.from_dict(d)
    assert restored.sim_id == "life_sim_abc123"
    assert restored.status == "active"
    assert restored.pilot_mode == "manual"


def test_store_save_load_and_history(tmp_path):
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "life_sim_test01")

    assert not store.exists()

    ts = now_iso()
    manifest = SimManifest(
        sim_id="life_sim_test01",
        template="life_sim",
        intent="test intent",
        title="test title",
        created_at=ts,
        updated_at=ts,
    )
    store.save_manifest(manifest)
    assert store.exists()

    loaded = store.load_manifest()
    assert loaded.sim_id == "life_sim_test01"

    state0 = SimState(step=0, summary="初始状态", vars={"age": 22})
    store.append_state(state0)
    state1 = SimState(step=1, summary="推进一步后", vars={"age": 23})
    store.append_state(state1)

    current = store.load_current_state()
    assert current is not None
    assert current.step == 1
    assert current.summary == "推进一步后"

    history = store.load_history()
    assert [s.step for s in history] == [0, 1]

    assert list_sim_ids(data_dir) == ["life_sim_test01"]


def test_append_state_is_true_append_write_not_full_rewrite(tmp_path):
    """阶段二十七（4.18 节）：`append_state()` 应该是真正的追加写——
    验证方式是往 `state_history.jsonl` 手动塞一行"不是合法 JSON 的
    垃圾字节"在文件末尾模拟"上次追加中途出问题留下的脏数据"场景
    不适用（那不是本测试目标），这里只验证正常路径下多次
    `append_state()` 之后文件行数与调用次数一致、且每次调用不需要
    先读回整份历史就能正确追加（通过 mock `load_history` 确保它
    没有被 `append_state` 调用来验证"不再依赖整体重写"这个实现
    事实）。
    """
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "append_write_test")

    store.append_state(SimState(step=0, summary="s0"))
    store.append_state(SimState(step=1, summary="s1"))
    store.append_state(SimState(step=2, summary="s2"))

    history_path = store.state_history_path()
    lines = [l for l in history_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3
    assert json.loads(lines[0])["step"] == 0
    assert json.loads(lines[2])["step"] == 2

    # `append_state` 不应该依赖先 `load_history()` 再整体重写：直接
    # 把 `SimStore.load_history` 换成一个"调用即失败"的替身，确认
    # `append_state` 仍能成功追加第四条，证明它不经过这条读取路径。
    def _boom(*_args, **_kwargs):  # pragma: no cover - 只用于断言未被调用
        raise AssertionError("append_state 不应该调用 load_history()")

    store.load_history = _boom  # type: ignore[method-assign]
    store.append_state(SimState(step=3, summary="s3"))

    lines_after = [
        l for l in history_path.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    assert len(lines_after) == 4
    assert json.loads(lines_after[3])["step"] == 3


def test_load_manifest_missing_raises(tmp_path):
    store = SimStore.for_root(tmp_path / "data", "does_not_exist")
    import pytest

    with pytest.raises(SimNotFoundError):
        store.load_manifest()


def test_pilot_config_defaults_when_no_file(tmp_path):
    store = SimStore.for_root(tmp_path / "data", "sim1")
    assert store.load_pilot_config("main") == {"pilot_mode": "manual", "autopilot": {}}
    assert store.load_pilot_config("br_xyz") == {"pilot_mode": "manual", "autopilot": {}}


def test_pilot_config_save_and_load_roundtrip(tmp_path):
    store = SimStore.for_root(tmp_path / "data", "sim1")
    store.save_pilot_config("main", "autopilot", {"risk_preference": "保守"})
    assert store.load_pilot_config("main") == {
        "pilot_mode": "autopilot",
        "autopilot": {"risk_preference": "保守"},
    }


def test_pilot_config_falls_back_to_manifest_for_legacy_main(tmp_path):
    """旧数据：main 分支没有独立的 pilot_config.json，退回读 manifest 顶层字段。"""
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "sim1")
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id="sim1", template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts,
            pilot_mode="autopilot", autopilot={"review_mode": "silent"},
        )
    )
    assert store.load_pilot_config("main") == {
        "pilot_mode": "autopilot",
        "autopilot": {"review_mode": "silent"},
    }


def test_delete_branch_dir_removes_pilot_config_too(tmp_path):
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "sim1")
    store.save_pilot_config("br_abc", "autopilot", {})
    assert store.pilot_config_path("br_abc").exists()
    store.delete_branch_dir("br_abc")
    assert not store.pilot_config_path("br_abc").exists()
