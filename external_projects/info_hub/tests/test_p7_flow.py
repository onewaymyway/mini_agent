"""P7 端到端流程：表单添加、LLM 草稿（假模型）、预览、修改、确认、查重、脚本不被提前执行。"""
import json
import unittest
from pathlib import Path

import yaml

from adapters.base import FetchContext
from core import store
from core.settings import load_settings
from core.source_generator import confirm_draft, generate_draft, preview_draft, revise_draft
from core.source_store import SourceError, load_draft
from server import api
from tests.helpers import make_project

LIST_HTML = ('<html><head><title>热榜</title></head><body>'
             + "".join(f'<div class="hot-item"><span class="t">热点{i}</span><a href="/q/{i}">x</a></div>' for i in range(1, 6))
             + "</body></html>").encode("utf-8")


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def html_list_answer(sid="zhihu_like", list_selector="div.hot-item"):
    return json.dumps({
        "adapter": "html_list",
        "explanation": "页面是重复的列表结构，适合 html_list",
        "source": {
            "id": sid, "name": "热榜示例", "category": "hot_community", "kind": "hot_list",
            "tags": ["测试"], "enabled": True,
            "request": {"url": "https://hot.example.com/hot"},
            "parse": {"list_selector": list_selector, "fields": {"title": "span.t", "url": "a@href"}},
            "policy": {"max_items": 20, "dedup_by": "url"},
            "schedule": {"every": "30m"},
            "browser": {"required": False},
        },
        "script": "",
    }, ensure_ascii=False)


def script_answer(marker: Path):
    script = (
        "from pathlib import Path\n"
        f"def fetch(ctx):\n"
        f"    Path({str(marker)!r}).write_text('ran')\n"
        "    return [{'title': '脚本条目', 'url': 'https://s/1'}]\n"
    )
    return json.dumps({
        "adapter": "script", "explanation": "需要自定义逻辑",
        "source": {"id": "scripted", "name": "脚本源", "category": "news", "kind": "custom",
                   "request": {"url": "https://s/api"}, "policy": {"max_items": 5}, "schedule": {"every": "60m"}},
        "script": script,
    }, ensure_ascii=False)


def fake_ctx():
    return FetchContext(http_get=lambda url, **kw: LIST_HTML)


class P7FlowTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)

    def test_generate_preview_confirm_creates_source(self):
        llm = FakeLLM(html_list_answer())
        draft = generate_draft(self.settings, llm, "https://hot.example.com/hot", name="热榜示例",
                               category="hot_community", ctx=fake_ctx(), provider_label="测试")
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["adapter"], "html_list")
        self.assertEqual(draft["errors"], [])
        self.assertEqual(draft["llm_provider"], "测试")
        # 模型收到的是结构摘要而不是整页 HTML
        self.assertIn("div.hot-item", llm.prompts[0])
        self.assertNotIn("<html", llm.prompts[0])

        draft = preview_draft(self.settings, draft["id"], ctx=fake_ctx())
        self.assertTrue(draft["preview"]["ok"], draft["preview"])
        self.assertEqual(draft["preview"]["total"], 5)

        result = confirm_draft(self.settings, draft["id"])
        self.assertEqual(result["id"], draft["id"])
        src_yaml = yaml.safe_load((self.root / "sources" / draft["id"] / "source.yaml").read_text(encoding="utf-8"))
        self.assertEqual(src_yaml["adapter"], "html_list")
        index = store.read_json(self.root / "sources" / "_index.json")
        self.assertIn(draft["id"], [e["id"] for e in index["sources"]])
        self.assertFalse((self.root / "data" / "drafts" / draft["id"]).exists())

    def test_revise_dialogue_updates_same_draft(self):
        llm = FakeLLM(html_list_answer(list_selector="div.wrong"), html_list_answer(list_selector="div.hot-item"))
        draft = generate_draft(self.settings, llm, "https://hot.example.com/hot", name="热榜示例",
                               category="hot_community", ctx=fake_ctx())
        self.assertEqual(draft["source"]["parse"]["list_selector"], "div.wrong")
        draft2 = revise_draft(self.settings, llm, draft["id"], "列表选择器不对，应该是 hot-item", ctx=fake_ctx())
        self.assertEqual(draft2["id"], draft["id"])
        self.assertEqual(draft2["source"]["parse"]["list_selector"], "div.hot-item")
        self.assertEqual(draft2["revisions_count"], 2)
        self.assertIn("用户对上一版的反馈", llm.prompts[1])

    def test_script_draft_is_not_executed_until_preview(self):
        marker = self.root / "ran.marker"
        llm = FakeLLM(script_answer(marker))
        draft = generate_draft(self.settings, llm, "https://s/api", name="脚本源", category="news", ctx=fake_ctx())
        self.assertFalse(marker.exists(), "生成草稿时不应执行脚本")
        self.assertTrue(draft["has_script"])
        draft = preview_draft(self.settings, draft["id"], ctx=fake_ctx())
        self.assertTrue(marker.exists(), "预览时才执行脚本")
        self.assertTrue(draft["preview"]["ok"], draft["preview"])
        confirm_draft(self.settings, draft["id"])
        self.assertTrue((self.root / "sources" / "scripted" / "script.py").exists())

    def test_duplicate_blocks_confirm_unless_forced(self):
        # 先添加一个已有源
        existing = {"id": "old_hot", "name": "旧热榜", "category": "hot_community", "kind": "hot_list",
                    "adapter": "html_list", "request": {"url": "https://hot.example.com/hot"},
                    "parse": {"list_selector": "div.hot-item", "fields": {"title": "span.t"}},
                    "policy": {}, "schedule": {"every": "60m"}}
        from core.source_store import write_source
        write_source(self.settings, existing)
        draft = generate_draft(self.settings, FakeLLM(html_list_answer(sid="new_hot")),
                               "https://hot.example.com/hot", name="新热榜", category="hot_community",
                               ctx=fake_ctx())
        self.assertIn("old_hot", draft["duplicates"])
        with self.assertRaises(SourceError):
            confirm_draft(self.settings, draft["id"])
        confirm_draft(self.settings, draft["id"], force=True)
        self.assertTrue((self.root / "sources" / "new_hot").exists())

    def test_invalid_category_blocks_confirm(self):
        bad = json.loads(html_list_answer())
        bad["source"]["category"] = "no_such"
        draft = generate_draft(self.settings, FakeLLM(json.dumps(bad)), "https://hot.example.com/hot",
                               name="热榜示例", ctx=fake_ctx())
        self.assertTrue(any("分类" in e for e in draft["errors"]))
        with self.assertRaises(SourceError):
            confirm_draft(self.settings, draft["id"])

    def test_generate_rejects_non_http_url(self):
        with self.assertRaises(SourceError):
            generate_draft(self.settings, FakeLLM(), "file:///etc/passwd", ctx=fake_ctx())


class FormTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)

    def test_form_html_list_create_and_preview(self):
        form = {"id": "form_hot", "name": "表单热榜", "category": "hot_community", "kind": "hot_list",
                "adapter": "html_list", "url": "https://hot.example.com/hot", "every": "30m", "max_items": 10,
                "list_selector": "div.hot-item", "fields": {"title": "span.t", "url": "a@href"}}
        import core.runner as runner
        orig = runner.default_context
        runner.default_context = lambda s: fake_ctx()
        try:
            out = api.preview_form(self.settings, {"source": form})
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["preview"]["total"], 5)
            created = api.create_form(self.settings, {"source": form})
        finally:
            runner.default_context = orig
        self.assertEqual(created["id"], "form_hot")
        self.assertTrue((self.root / "sources" / "form_hot" / "source.yaml").exists())

    def test_form_validation_errors_reported(self):
        out = api.preview_form(self.settings, {"source": {"id": "Bad Id", "name": "", "category": "nope",
                                                           "kind": "hot_list", "adapter": "html_list",
                                                           "url": "https://x"}})
        self.assertFalse(out["ok"])
        self.assertTrue(out["errors"])

    def test_create_duplicate_returns_409_unless_force(self):
        from server.api import ApiError
        form = {"id": "dup_a", "name": "a", "category": "news", "kind": "feed", "adapter": "rss",
                "url": "https://news.example.com/feed"}
        api.create_form(self.settings, {"source": form})
        with self.assertRaises(ApiError) as cm:
            api.create_form(self.settings, {"source": dict(form, id="dup_b")})
        self.assertEqual(cm.exception.status, 409)
        api.create_form(self.settings, {"source": dict(form, id="dup_b"), "force": True})


if __name__ == "__main__":
    unittest.main()
