"""tests/test_html_export_anatomy.py — 第二十四轮 A7：静态 HTML 导出接入元素档案与预测简报。

覆盖：无数据不输出（旧实例导出逐字节不变）/ 档案 + 简报 / 自包含 / 转义 / 可复现 / 区块失败降级 / SVG。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_forecast_brief as tfb  # noqa: E402

from world_simulator import anatomy as an  # noqa: E402
from world_simulator import forecast_brief as fb  # noqa: E402
from world_simulator import html_export as he  # noqa: E402
from world_simulator import html_export_anatomy as ha  # noqa: E402
from world_simulator.state_model import SimManifest, SimState  # noqa: E402
from world_simulator.store import SimStore, now_iso  # noqa: E402

SIM = "sim_a7"
MARKERS = ("ws-anat-", "🔮 预测简报", "🧬 ")


def _seed(tmp_path, settings, n=2):
    store = SimStore.for_root(tmp_path, SIM)
    m = SimManifest(sim_id=SIM, template="life_sim", intent="意图", title="标题", created_at=now_iso(), updated_at=now_iso(), settings=dict(settings))
    store.save_manifest(m)
    for i in range(n):
        store.append_state(SimState(step=i, summary=f"第{i}步", vars={}), "main")
    return store


def _settings(*lines, **extra):
    extra.setdefault("anatomy_params", {"mc_runs": 150})
    return tfb._settings(*lines, **extra)


def _export(tmp_path, **kw):
    kw.setdefault("forecast_budget_sec", 0)
    return he.export_simulation_html(tmp_path, SIM, **kw)


# ── 无数据：旧实例导出逐字节不变 ─────────────────────────────────────


def test_old_instance_export_is_byte_identical(tmp_path, monkeypatch):
    _seed(tmp_path, {})
    html = _export(tmp_path)
    for marker in MARKERS:
        assert marker not in html
    assert ha.EXTRA_CSS not in html
    monkeypatch.setattr(ha, "sections_html", lambda *a, **k: "")
    assert _export(tmp_path) == html


def test_switch_on_without_anatomy_renders_nothing(tmp_path):
    _seed(tmp_path, {"element_modeling_enabled": True, "anatomy_enabled": True, "causal_lines": [{"id": "x", "label": "x", "kind": "element"}]})
    html = _export(tmp_path)
    assert all(m not in html for m in MARKERS) and ha.EXTRA_CSS not in html


def test_anatomy_switch_off_hides_even_existing_anatomy(tmp_path):
    s = _settings()
    s["anatomy_enabled"] = False
    _seed(tmp_path, s)
    html = _export(tmp_path)
    assert all(m not in html for m in MARKERS)


# ── 有数据：档案 + 简报 ──────────────────────────────────────────────


def test_profile_and_brief_are_rendered_with_css(tmp_path):
    _seed(tmp_path, _settings())
    html = _export(tmp_path)
    assert "🧬 固态电池" in html and "🔮 预测简报" in html and ha.EXTRA_CSS in html
    assert "不是" in html and "概率" in html                       # 诚实声明
    assert "整体置信等级" in html and "没有同类回测案例" in html  # 扣分原因逐条展示
    assert "卡在哪" in html and "前三个关键不确定性" in html and "里程碑时间分布" in html
    assert "<svg" in html and "<polygon" in html                 # 分位带


def test_with_forecast_false_keeps_profile_only(tmp_path):
    _seed(tmp_path, _settings())
    html = _export(tmp_path, with_forecast=False)
    assert "🧬 固态电池" in html and "🔮 预测简报" not in html


def test_engine_inactive_element_gets_profile_but_no_brief(tmp_path):
    only_signal = tfb._line("e", "只有信号", an.normalize_anatomy({"signals": [{"id": "s", "watch": "w", "means": "m"}]}))
    _seed(tmp_path, _settings(only_signal))
    html = _export(tmp_path)
    assert "🧬 只有信号" in html and "🔮 预测简报" not in html


def test_export_is_self_contained(tmp_path):
    _seed(tmp_path, _settings())
    html = _export(tmp_path)
    low = html.lower()
    assert "<script" not in low and "<link" not in low and "<img" not in low and "src=" not in low
    assert "@import" not in low and "url(" not in low


def test_export_is_reproducible_with_fixed_seed(tmp_path):
    _seed(tmp_path, _settings())
    assert _export(tmp_path) == _export(tmp_path)


def test_text_is_escaped_and_unsafe_links_not_linked(tmp_path):
    a = tfb._anat(metrics=[tfb._metric(basis=tfb.SRC, cur_basis=tfb.SRC)], status="reviewed")
    line = tfb._line(label="<script>alert(1)</script>", anatomy=a)
    store = _seed(tmp_path, _settings(line))
    recs = [
        tfb.evm.normalize_record({"ev_id": "ev_0001", "element_id": "el", "claim": "<b>断言</b>", "source_url": "javascript:alert(1)", "retrieved_at": "2026-09-01T00:00:00+00:00"}),
    ]
    store.append_evidence(recs)
    html = _export(tmp_path)
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)" in html
    assert "<b>断言</b>" not in html and 'href="javascript' not in html.lower()


def test_http_evidence_is_linked_with_noopener(tmp_path):
    a = tfb._anat(metrics=[tfb._metric(basis=tfb.SRC, cur_basis=tfb.SRC)], status="reviewed")
    store = _seed(tmp_path, _settings(tfb._line(anatomy=a)))
    store.append_evidence([tfb.evm.normalize_record({"ev_id": "ev_0001", "element_id": "el", "claim": "c", "source_url": "https://example.org/a", "retrieved_at": "2026-09-01T00:00:00+00:00"})])
    html = _export(tmp_path)
    assert 'href="https://example.org/a"' in html and 'rel="noopener noreferrer"' in html


def test_truncated_forecast_is_announced(tmp_path):
    _seed(tmp_path, _settings(anatomy_params={"mc_runs": 5000}))
    html = _export(tmp_path, forecast_budget_sec=1e-9)
    assert "时间预算" in html


def test_block_failure_degrades_to_one_line(tmp_path, monkeypatch):
    _seed(tmp_path, _settings())

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(fb, "run_brief", boom)
    html = _export(tmp_path)
    assert "预测简报渲染失败" in html and "🧬 固态电池" in html


def test_export_does_not_write_forecast_to_disk(tmp_path):
    store = _seed(tmp_path, _settings())
    before = sorted(p.name for p in Path(store.root).rglob("*") if p.is_file()) if hasattr(store, "root") else None
    _export(tmp_path)
    after = sorted(p.name for p in Path(store.root).rglob("*") if p.is_file()) if hasattr(store, "root") else None
    assert before == after


# ── SVG ──────────────────────────────────────────────────────────────


def test_fan_svg_needs_two_points_and_draws_threshold():
    assert ha.fan_svg({"days": [0], "p10": [1], "p50": [1], "p90": [1]}) == ""
    assert ha.fan_svg({"days": [0, None], "p10": [1, 2], "p50": [1, 2], "p90": [1, 2]}) == ""
    m = {"name": "成本", "days": [0, 100, 200], "p10": [10, 8, 6], "p50": [10, 9, 8], "p90": [10, 10, 10], "target": {"value": 7}}
    svg = ha.fan_svg(m)
    assert "<polygon" in svg and "目标 7" in svg and "stroke-dasharray" in svg
    far = ha.fan_svg({**m, "target": {"value": 99}})  # 目标远在曲线之外：纵轴被撑开，仍然画出
    assert "目标 99" in far and ">99<" in far
    assert "目标" not in ha.fan_svg({k: v for k, v in m.items() if k != "target"})
    flat = ha.fan_svg({"days": [0, 1], "p10": [5, 5], "p50": [5, 5], "p90": [5, 5]})
    assert "nan" not in flat.lower() and "inf" not in flat.lower()


def test_range_and_tornado_svg_skip_empty_input():
    assert ha.range_svg([], 100) == "" and ha.range_svg([{"status": "reached"}], 100) == ""
    row = {"status": "pending", "name": "里程碑", "p10": 10, "p50": 50, "p90": None}
    assert "<rect" in ha.range_svg([row], 100)  # P90 缺失（视野内没出现）画到视野末
    assert ha.tornado_svg([]) == "" and ha.tornado_svg([{"label": "a", "score": 0}]) == ""
    assert "<rect" in ha.tornado_svg([{"label": "a", "score": 2.0}, {"label": "b", "score": 1.0}])
