"""阶段 P19-2：看板参数表单（纯逻辑 + 临时项目中的渲染与保存）。"""
import json
import os
import unittest

from streamlit.testing.v1 import AppTest

from core import autoexplore, explore_params
from core.settings import load_settings
from dashboard.explore_params_form import changed_values, widget_spec
from tests.helpers import make_project

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def row(key, kind, value, source="default", **kw):
    base = {"key": key, "label": key, "kind": kind, "default": value, "min": 0, "max": 100,
            "choices": [], "hint": "", "group": "g", "value": value, "source": source}
    base.update(kw)
    return base


class FormLogicTests(unittest.TestCase):
    def test_widget_spec_by_kind(self):
        spec = widget_spec(row("n", "int", 5, min=0, max=10))
        self.assertEqual((spec["kind"], spec["min"], spec["max"], spec["step"]), ("int", 0, 10, 1))
        spec = widget_spec(row("r", "float", 0.5, min=0, max=1))
        self.assertLess(spec["step"], 0.5)
        spec = widget_spec(row("c", "choice", "off", choices=["off", "headed"]))
        self.assertEqual(spec["options"], ["off", "headed"])

    def test_help_mentions_source_and_hint(self):
        spec = widget_spec(row("n", "int", 5, source="dashboard", hint="每天上限"))
        self.assertIn("每天上限", spec["help"])
        self.assertIn("看板设置", spec["help"])
        spec = widget_spec(row("n", "int", 5, source="default"))
        self.assertIn("默认值", spec["help"])

    def test_only_changed_values_are_submitted(self):
        rows = [row("daily", "int", 20), row("min_items", "int", 3), row("ratio", "float", 0.8)]
        submitted = {"daily": 20, "min_items": 4, "ratio": 0.8, "unknown": 1}
        self.assertEqual(changed_values(rows, submitted), {"min_items": 4})

    def test_float_tolerance_and_type_mixing(self):
        rows = [row("ratio", "float", 0.8)]
        self.assertEqual(changed_values(rows, {"ratio": 0.8000000000001}), {})
        self.assertEqual(changed_values(rows, {"ratio": 1}), {"ratio": 1})


class DashboardPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        self.runtime = load_settings(self.root).paths.state_dir / "runtime_config.json"
        (self.root / "config" / "discovery.yaml").write_text("autoexplore:\n  min_items: 6\n", encoding="utf-8")

    def tearDown(self):
        os.environ.pop("INFO_HUB_ROOT", None)

    def test_panel_renders_all_fields_with_current_values(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        keys = {n.key for n in at.number_input} | {c.key for c in at.checkbox} | {s.key for s in at.selectbox}
        self.assertIn("ep_autoexplore_daily", keys)
        self.assertIn("ep_autoexplore_render", keys)
        self.assertIn("ep_review_enabled", keys)
        self.assertEqual(at.number_input(key="ep_autoexplore_min_items").value, 6)   # 来自 yaml

    def test_saving_one_change_writes_only_that_key(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        at.number_input(key="ep_autoexplore_daily").set_value(7)
        form_button = [b for b in at.button if b.label == "💾 保存修改"][0]
        form_button.click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(autoexplore.load_config(self.root)["daily"], 7)
        stored = json.loads(self.runtime.read_text(encoding="utf-8"))["explore_params"]["autoexplore"]
        self.assertEqual(stored, {"daily": 7})                       # 未改动的 min_items 不会被固定
        self.assertEqual(autoexplore.load_config(self.root)["min_items"], 6)

    def test_clear_button_resets_section_and_form_shows_yaml_value(self):
        explore_params.save(self.root, "autoexplore", {"min_items": 9})
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertEqual(at.number_input(key="ep_autoexplore_min_items").value, 9)
        clear = [b for b in at.button if b.key == "ep_reset_autoexplore"][0]
        clear.click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(autoexplore.load_config(self.root)["min_items"], 6)
        self.assertEqual(at.number_input(key="ep_autoexplore_min_items").value, 6)


if __name__ == "__main__":
    unittest.main()
