"""阶段 P18：已接受自动探索源的周期复查、替代入口、退役（伪造站点，不访问网络）。"""
import json
import unittest
from datetime import datetime, timedelta

from core import autoexplore, discovery, review
from core.settings import load_settings
from core.sources import load_sources
from core.tick import tick
from server import api
from tests.helpers import make_project
from tests.test_autoexplore import FakeCtx, FakeLLM, PAGE, SITE
from tests.test_autoexplore_p14 import RSS

NOW = datetime(2026, 11, 27, 12, 0).astimezone()
SID = "auto_good"
FEED = f"{SITE}feed.xml"
BROKEN = (b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head>'
          b'<body><p>\xe6\x94\xb9\xe7\x89\x88\xe4\xba\x86</p></body></html>')
SOURCE = {"id": SID, "name": "好站", "category": "news", "kind": "hot_list", "adapter": "html_list",
          "enabled": True, "request": {"url": SITE},
          "parse": {"list_selector": "div.hot-item", "fields": {"title": "span.t", "url": "a@href"}},
          "policy": {"max_items": 20, "dedup_by": "url"}, "schedule": {"every": "60m"}, "tags": ["自动探索"]}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = make_project({SID: SOURCE})
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir
        self._accept(SID)

    def _accept(self, sid, origin="auto_explore"):
        discovery.add_candidate(self.data, {
            "id": f"auto:{sid}", "template_id": "auto_explore", "template_name": "t", "query": "q", "reason": "r",
            "url": SITE, "status": "accepted", "created_at": NOW.isoformat(timespec="seconds"),
            "decided_at": NOW.isoformat(timespec="seconds"), "source": dict(SOURCE, id=sid), "origin": origin})

    def cfg(self, **kw):
        base = {"enabled": "true"}
        base.update(kw)
        (self.root / "config" / "discovery.yaml").write_text(
            "review:\n" + "".join(f"  {k}: {v}\n" for k, v in base.items()), encoding="utf-8")

    def healthy(self):
        return FakeCtx({SITE: PAGE.encode()})

    def broken(self, **extra):
        routes = {SITE: BROKEN, FEED: RSS}
        routes.update(extra)
        return FakeCtx(routes)

    def run_review(self, ctx, llm=None, now=NOW, **kw):
        return review.run(self.settings, llm, ctx=ctx, now=now, provider_label="fake", **kw)

    def entry(self):
        return review.load_state(self.data)["sources"][SID]


class ConfigTests(Base):
    def test_defaults_are_conservative(self):
        cfg = review.load_config(self.root)
        self.assertFalse(cfg["enabled"])
        self.assertFalse(cfg["auto_retire"])
        self.assertEqual(cfg["every_days"], 30)

    def test_bad_values_fall_back(self):
        self.cfg(every_days="abc", max_alternatives=99)
        cfg = review.load_config(self.root)
        self.assertEqual(cfg["every_days"], 30)
        self.assertEqual(cfg["max_alternatives"], 10)


class JudgeTests(Base):
    def test_healthy_source_resets_failures(self):
        self.cfg()
        review._save_state(self.data, {"sources": {SID: {"failures": 2, "status": "failing"}}})
        out = self.run_review(self.healthy(), force=True)
        self.assertEqual((out["reviewed"], out["ok"], out["failing"]), (1, 1, 0))
        self.assertEqual(self.entry()["failures"], 0)
        self.assertEqual(self.entry()["status"], "ok")

    def test_only_accepted_auto_sources_are_reviewed(self):
        manual = dict(SOURCE, id="manual_one", request={"url": "https://other.example/"})
        root = make_project({SID: SOURCE, "manual_one": manual})
        settings = load_settings(root)
        discovery.add_candidate(settings.paths.data_dir, {
            "id": f"auto:{SID}", "template_id": "x", "template_name": "t", "query": "q", "reason": "r", "url": SITE,
            "status": "accepted", "created_at": NOW.isoformat(), "decided_at": None, "origin": "auto_explore",
            "source": dict(SOURCE)})
        ctx = FakeCtx({SITE: PAGE.encode()})
        out = review.run(settings, None, ctx=ctx, now=NOW, force=True)
        self.assertEqual(out["reviewed"], 1)
        self.assertNotIn("https://other.example/", ctx.fetched)

    def test_not_due_is_skipped_and_failing_rechecks_sooner(self):
        self.cfg()
        review._save_state(self.data, {"sources": {SID: {"last_review": (NOW - timedelta(days=10)).isoformat()}}})
        out = self.run_review(self.healthy())
        self.assertEqual((out["reviewed"], out["skipped"].get("not_due")), (0, 1))
        review._save_state(self.data, {"sources": {SID: {"last_review": (NOW - timedelta(days=10)).isoformat(),
                                                         "failures": 1}}})
        self.assertEqual(self.run_review(self.healthy())["reviewed"], 1)     # 失败过的源 7 天就复查

    def test_browser_required_source_is_never_reviewed(self):
        src = dict(SOURCE, browser={"required": True, "site": "s", "headless": False})
        root = make_project({SID: src})
        settings = load_settings(root)
        discovery.add_candidate(settings.paths.data_dir, {
            "id": f"auto:{SID}", "template_id": "x", "template_name": "t", "query": "q", "reason": "r", "url": SITE,
            "status": "accepted", "created_at": NOW.isoformat(), "decided_at": None, "origin": "auto_explore",
            "source": dict(src)})
        ctx = FakeCtx({SITE: PAGE.encode()})
        out = review.run(settings, None, ctx=ctx, now=NOW, force=True)
        self.assertEqual(out["skipped"].get("browser_required"), 1)
        self.assertEqual(ctx.fetched, [])

    def test_disabled_source_not_reviewed(self):
        from core.source_store import update_source_file
        update_source_file(self.settings, SID, dict(SOURCE, enabled=False))
        out = self.run_review(self.healthy(), force=True)
        self.assertEqual((out["reviewed"], out["skipped"].get("disabled")), (0, 1))


class AlternativeTests(Base):
    def test_failure_proposes_replacement_feed_without_model(self):
        self.cfg()
        out = self.run_review(self.broken(), force=True)
        self.assertEqual((out["failing"], out["alternatives"]), (1, 1), out)
        cand = [c for c in discovery.list_candidates(self.data) if c.get("replaces") == SID][0]
        self.assertEqual(cand["origin"], "auto_review")
        self.assertEqual(cand["status"], "pending")
        self.assertFalse(cand["source"]["enabled"])                 # 默认停用，需确认
        self.assertEqual(cand["source"]["request"]["url"], FEED)
        self.assertEqual(self.entry()["failures"], 1)
        self.assertTrue(self.entry()["alternatives"])
        self.assertTrue(load_sources(self.settings).specs[SID].enabled)   # 原源此时仍保持启用

    def test_pending_alternative_not_duplicated_on_next_review(self):
        self.run_review(self.broken(), force=True)
        out = self.run_review(self.broken(), force=True)
        self.assertEqual(out["alternatives"], 0)
        self.assertEqual(out["skipped"].get("alt_alternative_pending"), 1)

    def test_rejected_alternative_not_proposed_again(self):
        self.run_review(self.broken(), force=True)
        cid = [c for c in discovery.list_candidates(self.data) if c.get("replaces") == SID][0]["id"]
        discovery.decide(self.data, cid, "rejected")
        out = self.run_review(self.broken(), force=True)
        self.assertEqual(out["alternatives"], 0)
        self.assertEqual(out["skipped"].get("alt_already_known"), 1)

    def test_common_feed_path_tried_only_when_homepage_declares_none(self):
        plain = b"<html><body>nothing</body></html>"
        ctx = FakeCtx({SITE: plain, f"{SITE}feed": RSS})
        out = self.run_review(ctx, force=True)
        self.assertEqual(out["alternatives"], 1, out)
        ctx2 = FakeCtx({SITE: BROKEN, FEED: RSS, f"{SITE}feed": RSS})   # 首页已声明订阅：不再猜常见路径
        self.run_review(ctx2, force=True)
        self.assertNotIn(f"{SITE}feed", ctx2.fetched)

    def test_robots_disallow_blocks_alternatives(self):
        ctx = self.broken(**{f"{SITE}robots.txt": b"User-agent: *\nDisallow: /\n"})
        out = self.run_review(ctx, force=True)
        self.assertEqual(out["alternatives"], 0)
        self.assertGreaterEqual(out["skipped"].get("alt_robots_disallow", 0), 1)
        self.assertNotIn(FEED, ctx.fetched)

    def test_denylisted_host_gets_no_alternatives(self):
        (self.root / "config" / "denylist.yaml").write_text("hosts:\n  - good.example\n", encoding="utf-8")
        ctx = self.broken()
        out = self.run_review(ctx, force=True)
        self.assertEqual(out["alternatives"], 0)
        self.assertEqual(out["skipped"].get("alt_denylisted"), 1)

    def test_redraft_with_model_when_selector_broke(self):
        self.cfg()
        changed = PAGE.replace("hot-item", "card").encode()          # 版面改了：旧选择器取不到
        llm = FakeLLM(SITE)
        llm_json = json.loads(llm.ask("x"))
        llm_json["source"]["parse"]["list_selector"] = "div.card"
        llm.ask = lambda prompt, system="": json.dumps(llm_json, ensure_ascii=False)
        ctx = FakeCtx({SITE: changed})
        out = self.run_review(ctx, llm=llm, force=True)
        self.assertEqual(out["alternatives"], 1, out)
        cand = [c for c in discovery.list_candidates(self.data) if c.get("replaces") == SID][0]
        self.assertEqual(cand["source"]["parse"]["list_selector"], "div.card")
        self.assertIn("模型重建", cand["reason"])
        self.assertEqual(list((self.data / "drafts").glob("*/draft.json")) if (self.data / "drafts").exists() else [], [])


class RetireTests(Base):
    def test_consecutive_failures_only_suggest_by_default(self):
        self.cfg(retire_after=2)
        self.run_review(self.broken(), force=True)
        out = self.run_review(self.broken(), force=True)
        self.assertEqual((out["retire_suggested"], out["retired"]), (1, 0))
        self.assertEqual(self.entry()["status"], "retire_suggested")
        self.assertTrue(load_sources(self.settings).specs[SID].enabled)   # 默认不自动改动

    def test_auto_retire_disables_and_records(self):
        self.cfg(retire_after=2, auto_retire="true")
        self.run_review(self.broken(), force=True)
        out = self.run_review(self.broken(), force=True)
        self.assertEqual(out["retired"], 1)
        self.assertFalse(load_sources(self.settings).specs[SID].enabled)
        rows = review.retired_rows(self.data)
        self.assertEqual((rows[0]["source_id"], rows[0]["by"]), (SID, "auto"))
        self.assertEqual(self.entry()["status"], "retired")
        again = self.run_review(self.healthy(), force=True)             # 退役后不再复查
        self.assertEqual((again["reviewed"], again["skipped"].get("retired")), (0, 1))

    def test_retire_is_idempotent_and_keeps_files(self):
        review.retire(self.settings, SID, "手动", by="user", now=NOW)
        self.assertTrue(review.retire(self.settings, SID, "again").get("already"))
        self.assertEqual(len(review.retired_rows(self.data)), 1)
        self.assertTrue((self.settings.paths.sources_dir / SID / "source.yaml").exists())

    def test_retire_api_validates_and_reports_missing(self):
        with self.assertRaises(api.ApiError):
            api.retire_source_api(self.settings, {"source_id": "../x"})
        with self.assertRaises(api.ApiError):
            api.retire_source_api(self.settings, {"source_id": "nope"})
        api.retire_source_api(self.settings, {"source_id": SID, "reason": "不想要了"})
        self.assertFalse(load_sources(self.settings).specs[SID].enabled)


class AcceptReplacementTests(Base):
    def test_accepting_replacement_retires_original(self):
        self.run_review(self.broken(), force=True)
        cid = [c for c in discovery.list_candidates(self.data) if c.get("replaces") == SID][0]["id"]
        res = api.accept_candidate(self.settings, cid)                  # review: 前缀的 id 必须能被接受
        self.assertEqual(res["retired"], SID)
        specs = load_sources(self.settings).specs
        self.assertFalse(specs[SID].enabled)
        self.assertTrue(any(s.enabled and s.id != SID for s in specs.values()))
        self.assertEqual(review.retired_rows(self.data)[0]["reason"].split(":")[0], "replaced_by")

    def test_auto_prefixed_candidates_can_be_accepted_or_rejected(self):
        """回归：此前候选 id 校验只认 disc_ 前缀，自动探索候选（auto:）在看板上无法加入。"""
        cand = {"id": "auto:new.example", "template_id": "auto_explore", "template_name": "t", "query": "new.example",
                "reason": "r", "url": "https://new.example/", "status": "pending", "created_at": NOW.isoformat(),
                "decided_at": None, "origin": "auto_explore",
                "source": dict(SOURCE, id="auto_new", enabled=False, request={"url": "https://new.example/"})}
        discovery.add_candidate(self.data, cand)
        self.assertEqual(api.accept_candidate(self.settings, "auto:new.example")["id"], "auto:new.example")
        discovery.add_candidate(self.data, dict(cand, id="auto:new.example#news", source=dict(cand["source"], id="auto_n2")))
        self.assertEqual(api.reject_candidate(self.settings, "auto:new.example#news")["status"], "rejected")
        for bad in ("../etc", "auto:../x", "review:", "auto:a b"):
            with self.assertRaises(api.ApiError):
                api.accept_candidate(self.settings, bad)


class ControlTests(Base):
    def test_paused_review_does_nothing(self):
        autoexplore.set_paused(self.data, True, NOW)
        ctx = self.broken()
        out = self.run_review(ctx, force=True)
        self.assertTrue(out["paused"])
        self.assertEqual(ctx.fetched, [])

    def test_off_by_default_in_tick_and_maybe_run(self):
        self.assertIsNone(review.maybe_run(self.settings, NOW))

    def test_maybe_run_once_per_day(self):
        from unittest import mock
        self.cfg()
        ctx = self.healthy()
        with mock.patch.object(autoexplore, "_default_llm", return_value=(None, "")), \
                mock.patch("adapters.base.FetchContext", return_value=ctx):
            self.assertIsNotNone(review.maybe_run(self.settings, NOW))
            self.assertIsNone(review.maybe_run(self.settings, NOW + timedelta(hours=3)))
            self.assertIsNotNone(review.maybe_run(self.settings, NOW + timedelta(days=1)))

    def test_tick_survives_review_failure(self):
        from unittest import mock
        with mock.patch("core.review.maybe_run", side_effect=RuntimeError("boom")):
            tick(self.settings, now=NOW, ctx=self.healthy())       # 不应抛出

    def test_status_rows_and_summary(self):
        self.cfg()
        self.run_review(self.broken(), force=True)
        data = api.review_status_api(self.settings)
        self.assertTrue(data["enabled"])
        row = [r for r in data["rows"] if r["source_id"] == SID][0]
        self.assertEqual((row["status"], row["failures"]), ("failing", 1))
        self.assertEqual(len(row["pending_alternatives"]), 1)
        self.assertEqual(data["last"]["failing"], 1)


if __name__ == "__main__":
    unittest.main()
