"""Streamlit 看板测试：用 AppTest 无头运行 app.py（不启动浏览器）。"""
import os
import unittest
from datetime import datetime
from pathlib import Path

from streamlit.testing.v1 import AppTest

from adapters.base import FetchContext
from core.digest import write_report
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources
from dashboard.speech import speech_player_html
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")
RSS = ('<?xml version="1.0"?><rss version="2.0"><channel>'
       '<item><title>看板新闻甲</title><link>https://n/1</link></item>'
       '<item><title>看板新闻乙</title><link>https://n/2</link></item>'
       '</channel></rss>').encode("utf-8")
SRC = {"id": "news_a", "name": "新闻源A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/feed"}}


def run_app(root: Path) -> AppTest:
    os.environ["INFO_HUB_ROOT"] = str(root)
    os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def _feed(*titles):
    items = "".join(f"<item><title>{t}</title><link>https://n/{t}</link></item>" for t in titles)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'.encode("utf-8")


class StreamlitAppTests(unittest.TestCase):
    def test_subscription_tab_shows_cursor_and_updates(self):
        src = {"id": "novel_a", "name": "某小说", "category": "news", "kind": "subscription",
               "adapter": "subscription", "request": {"url": "https://n/feed"},
               "subscription": {"checker": "feed_latest"}}
        root = make_project({"novel_a": src})
        settings = load_settings(root)
        spec = load_sources(settings).specs["novel_a"]
        run_source(spec, settings, FetchContext(http_get=lambda u, **k: _feed("第1章")))
        run_source(spec, settings, FetchContext(http_get=lambda u, **k: _feed("第2章", "第1章")))
        at = run_app(root)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        rendered = " ".join(m.value for m in at.markdown)
        self.assertIn("某小说", rendered)
        self.assertIn("当前进度", rendered)
        self.assertIn("第2章", rendered)
        self.assertIn("上次：第1章", rendered)

    def test_subscription_tab_empty_hint(self):
        at = run_app(make_project({}))
        self.assertTrue(any("还没有订阅源" in i.value for i in at.info))

    def tearDown(self):
        os.environ.pop("INFO_HUB_ROOT", None)

    def test_empty_project_renders_all_tabs(self):
        root = make_project({})
        at = run_app(root)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        labels = [t.label for t in at.tabs]  # 含嵌套子页签，只校验顶层七个按顺序存在
        top = ["报告", "信息源", "抓取结果", "订阅", "添加信息源", "运行记录", "登录"]
        positions = [labels.index(t) for t in top]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(any("还没有报告" in i.value for i in at.info))

    def test_results_tab_shows_latest_snapshot(self):
        root = make_project({"news_a": SRC})
        settings = load_settings(root)
        spec = load_sources(settings).specs["news_a"]
        run_source(spec, settings, FetchContext(http_get=lambda u, **k: RSS))
        at = run_app(root)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertTrue(any("共 2 条" in c.value for c in at.caption), [c.value for c in at.caption])
        self.assertTrue(any("看板新闻甲" in str(df.value) for df in at.dataframe))

    def test_results_tab_without_snapshot_shows_hint(self):
        at = run_app(make_project({"news_a": SRC}))
        self.assertTrue(any("还没有抓取结果" in i.value for i in at.info))

    def test_sources_and_reports_are_shown(self):
        root = make_project({"news_a": SRC})
        settings = load_settings(root)
        spec = load_sources(settings).specs["news_a"]
        run_source(spec, settings, FetchContext(http_get=lambda u, **k: RSS))
        write_report(settings, {"id": "rolling", "every": "3h", "window": "3h", "categories": "all"},
                     datetime.now().astimezone())
        at = run_app(root)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        # 报告页：Markdown 中应包含抓取到的标题
        rendered = " ".join(m.value for m in at.markdown)
        self.assertIn("看板新闻甲", rendered)
        # 信息源页：数据表中应有该源
        self.assertGreaterEqual(len(at.dataframe), 1)

    def test_provider_missing_shows_error_not_crash(self):
        root = make_project({})
        at = run_app(root)
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("未找到 providers 配置" in e.value for e in at.error))

    def test_run_source_button_path(self):
        root = make_project({"news_a": SRC})
        at = run_app(root)
        self.assertEqual(len(at.exception), 0, [e.value for e in at.exception])
        self.assertTrue(any(b.label == "抓取一次" for b in at.button))


class NoBareExpressionTests(unittest.TestCase):
    """Streamlit 会把裸表达式语句当作"魔法输出"渲染到页面上（曾导致侧边栏打印源码）。
    这里禁止三元表达式、布尔、比较、变量名等纯表达式语句。"""

    def test_no_bare_streamlit_expression_statements(self):
        import ast
        tree = ast.parse(Path(APP).read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            # 普通函数调用语句（如 st.set_page_config(...)）不会被渲染，只检查纯表达式
            if isinstance(node, ast.Expr) and isinstance(
                    node.value, (ast.IfExp, ast.BoolOp, ast.Compare, ast.BinOp, ast.Name, ast.Attribute, ast.Subscript)):
                offenders.append(node.lineno)
        self.assertEqual(offenders, [], f"存在裸表达式语句，行号：{offenders}")


class SpeechHtmlTests(unittest.TestCase):
    def test_player_embeds_segments_and_guards_script_breakout(self):
        html = speech_player_html([{"text": "正文 </script><img src=x>"}], "标题<b>")
        self.assertNotIn("</script><img", html)
        self.assertIn("speechSynthesis", html)
        self.assertIn("标题&lt;b&gt;", html)


if __name__ == "__main__":
    unittest.main()
