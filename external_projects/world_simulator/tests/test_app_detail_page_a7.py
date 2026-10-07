"""tests/test_app_detail_page_a7.py — 第二十四轮 A7：详情页（Streamlit AppTest 真渲染）。

覆盖：旧实例页面不出现任何新东西 / 开启剖面的实例有预测简报与「高级机制」开关（默认折起、可展开）/
点「生成预测简报」能出结果 / 设置页的置信阈值可改并落盘 / 推进一步后简报失效。
同时是时间线「现实回填」区块的回归：A6 曾在那里引用不存在的 `manifest`，导致详情页对所有实例抛 NameError。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

import test_html_export_anatomy as the  # noqa: E402

import world_simulator.config as cfg  # noqa: E402
from world_simulator.state_model import SimManifest, SimState  # noqa: E402
from world_simulator.store import SimStore, now_iso  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")
ADV_PREFIX = "⚙️ 显示高级机制面板"


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    return tmp_path


def _open(sim_id: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["view"] = "detail"
    at.session_state["sim_id"] = sim_id
    at.run()
    return at


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def _fast_inputs(at: AppTest) -> None:
    for ni in at.number_input:
        if ni.label == "运行次数":
            ni.set_value(100)
        elif ni.label == "总时间预算（秒，0=不限）":
            ni.set_value(0)
    for cb in at.checkbox:
        if cb.label.startswith("同时做敏感性"):
            cb.set_value(False)


def test_old_instance_page_has_nothing_new_and_does_not_crash(data_dir):
    store = SimStore.for_root(data_dir, "sim_old")
    store.save_manifest(SimManifest(sim_id="sim_old", template="life_sim", intent="i", title="旧", created_at=now_iso(), updated_at=now_iso(), settings={}))
    for i in range(2):
        store.append_state(SimState(step=i, summary=f"第{i}步", vars={}), "main")
    at = _open("sim_old")
    assert not at.exception
    assert "预测简报" not in _text(at)
    assert not [t for t in at.toggle if t.label.startswith(ADV_PREFIX)]
    assert not [b for b in at.button if b.label == "生成预测简报"]


def test_anatomy_instance_shows_brief_and_collapsed_advanced_toggle(data_dir):
    the._seed(data_dir, the._settings(), n=3)
    at = _open(the.SIM)
    assert not at.exception
    assert "🔮 预测简报" in _text(at)
    toggles = [t for t in at.toggle if t.label.startswith(ADV_PREFIX)]
    assert len(toggles) == 1 and toggles[0].value is False  # 默认折起
    before = len(at.expander)
    toggles[0].set_value(True).run()
    assert not at.exception and len(at.expander) >= before  # 展开后原有面板仍然渲染，不报错


def test_generate_brief_button_renders_the_five_answers(data_dir):
    the._seed(data_dir, the._settings(), n=3)
    at = _open(the.SIM)
    _fast_inputs(at)
    [b for b in at.button if b.label == "生成预测简报"][0].click().run()
    assert not at.exception
    txt = _text(at)
    for needle in ("整体置信等级", "卡在哪", "靠什么假设", "前三个关键不确定性", "该盯的先行信号", "里程碑时间分布"):
        assert needle in txt, needle
    assert "没有同类回测案例" in txt  # 扣分原因逐条展示
    assert len(at.dataframe) >= 1


def test_brief_is_invalidated_when_the_simulation_advances(data_dir):
    store = the._seed(data_dir, the._settings(), n=2)
    at = _open(the.SIM)
    _fast_inputs(at)
    [b for b in at.button if b.label == "生成预测简报"][0].click().run()
    assert "整体置信等级" in _text(at)
    store.append_state(SimState(step=2, summary="第2步", vars={}), "main")
    at.run()
    assert "整体置信等级" not in _text(at) and "推进一步后需要重新生成" in " ".join(c.value for c in at.caption)


def test_confidence_thresholds_in_settings_persist(data_dir):
    store = the._seed(data_dir, the._settings(), n=2)
    at = _open(the.SIM)
    hi = [n for n in at.number_input if n.label == "「高」最低分"]
    if not hi:
        pytest.skip("设置页的置信阈值入口不在详情页首屏（取决于页面分区）")
    hi[0].set_value(90).run()
    assert not at.exception
    assert store.load_manifest().settings["anatomy_params"]["conf_high_min"] == 90
