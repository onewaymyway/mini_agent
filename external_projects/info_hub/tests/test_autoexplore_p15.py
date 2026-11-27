"""阶段 P15：渲染时观察 JSON 接口，识别列表型接口并生成 json_api 候选（伪造站点与浏览器，不访问网络）。"""
import json
import unittest
from datetime import datetime
from pathlib import Path

from core import autoexplore, discovery
from core.explore_json_api import observe_json
from core.explore_render import HeadedRenderer
from core.settings import load_settings
from tests.helpers import make_project
from tests.test_autoexplore import FakeCtx, SITE

NOW = datetime(2026, 11, 27, 12, 0).astimezone()
SHELL = b"<html><body><div id='app'></div></body></html>"
API = "https://good.example/api/hot?type=day&token=SECRET&page=1"
CLEAN_API = "https://good.example/api/hot?type=day&page=1"
API_PAYLOAD = {"code": 0, "data": {"list": [{"title": f"接口标题{i}", "link": f"https://good.example/p/{i}"}
                                              for i in range(1, 6)]}}


class FakeResponse:
    def __init__(self, url, status=200, ctype="application/json", payload=None):
        self.url, self.status, self.headers = url, status, {"content-type": ctype}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """模拟页面加载时触发的网络响应。"""
    responses: list = []

    def __init__(self, profile_dir, status_path):
        pass

    def fetch_html(self, url, *, headless=True, timeout_ms=0, on_response=None, **_):
        for r in FakeSession.responses:
            if on_response:
                on_response(r)
        return SHELL.decode("utf-8")


class ObserverTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.scratch = Path(tempfile.mkdtemp())

    def test_observes_list_json_and_ignores_others(self):
        FakeSession.responses = [
            FakeResponse(API, payload=API_PAYLOAD),
            FakeResponse("https://good.example/api/err", status=500, payload=API_PAYLOAD),
            FakeResponse("https://good.example/img.png", ctype="image/png", payload=None),
            FakeResponse("https://good.example/api/small", payload={"list": [{"title": "a", "link": "https://x"}]}),
        ]
        r = HeadedRenderer(self.scratch, session_factory=FakeSession)
        r.html(SITE)
        self.assertEqual(len(r.last_observations), 1)
        obs = r.last_observations[0]
        self.assertEqual(obs["items_path"], "data.list")
        self.assertNotIn("token", obs["endpoint"])      # 令牌参数已剔除
        self.assertIn("page=1", obs["endpoint"])

    def test_observation_does_not_persist_body(self):
        FakeSession.responses = [FakeResponse(API, payload=API_PAYLOAD)]
        r = HeadedRenderer(self.scratch, session_factory=FakeSession)
        r.html(SITE)
        blob = json.dumps(r.last_observations, ensure_ascii=False)
        self.assertNotIn("接口标题", blob)              # 只保留结论，不保留标题等正文

    def test_non_list_json_not_a_candidate(self):
        self.assertIsNone(observe_json("https://good.example/api/x", {"a": 1, "b": {"c": "d"}}))

    def test_list_needs_title_and_https_link(self):
        bad = {"list": [{"title": f"t{i}", "link": f"/relative/{i}"} for i in range(5)]}
        self.assertIsNone(observe_json("https://good.example/api/x", bad))


class RendererStub:
    def __init__(self, observations):
        self.obs = observations
        self.last_observations = []
        self.calls = 0

    def html(self, url):
        self.calls += 1
        self.last_observations = list(self.obs)
        return SHELL.decode("utf-8")


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        (self.root / "config" / "seed_sites.yaml").write_text(f"sites:\n  - url: {SITE}\n", encoding="utf-8")
        (self.root / "config" / "discovery.yaml").write_text(
            "autoexplore:\n  render: headed\n  render_daily: 5\n", encoding="utf-8")
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir

    def _ctx(self):
        # 候选使用剔除令牌后的地址；只有匿名可访问的接口才会通过预览
        return FakeCtx({SITE: SHELL, CLEAN_API: json.dumps(API_PAYLOAD).encode("utf-8")})

    def test_api_candidate_without_any_model(self):
        renderer = RendererStub([observe_json(API, API_PAYLOAD)])
        out = autoexplore.run(self.settings, None, ctx=self._ctx(), now=NOW, renderer=renderer)
        self.assertEqual(len(out["added"]), 1, out)
        cand = out["added"][0]
        self.assertEqual(cand["template_id"], "auto_api")
        self.assertEqual(cand["source"]["adapter"], "json_api")
        self.assertFalse(cand["source"]["enabled"])
        self.assertNotIn("token", cand["source"]["request"]["url"])
        self.assertEqual(renderer.calls, 1)              # 渲染只一次，预览复用缓存

    def test_api_cap_per_site(self):
        (self.root / "config" / "discovery.yaml").write_text(
            "autoexplore:\n  render: headed\n  render_daily: 5\n  max_apis_per_site: 1\n", encoding="utf-8")
        second = "https://good.example/api/other?x=1"
        ctx = FakeCtx({SITE: SHELL, CLEAN_API: json.dumps(API_PAYLOAD).encode(), second: json.dumps(API_PAYLOAD).encode()})
        renderer = RendererStub([observe_json(API, API_PAYLOAD), observe_json(second, API_PAYLOAD)])
        out = autoexplore.run(self.settings, None, ctx=ctx, now=NOW, renderer=renderer)
        self.assertEqual(len([c for c in out["added"] if c["template_id"] == "auto_api"]), 1)

    def test_no_observations_no_api_candidate(self):
        out = autoexplore.run(self.settings, None, ctx=self._ctx(), now=NOW, renderer=RendererStub([]))
        self.assertEqual(out["added"], [])

    def test_unreachable_endpoint_is_low_quality(self):
        renderer = RendererStub([observe_json(API, API_PAYLOAD)])
        ctx = FakeCtx({SITE: SHELL})   # 接口在普通 HTTP 下不可用（例如需要渲染时的令牌）
        out = autoexplore.run(self.settings, None, ctx=ctx, now=NOW, renderer=renderer)
        self.assertEqual(out["added"], [])
        self.assertIn("low_quality", out["skipped"])


if __name__ == "__main__":
    unittest.main()
