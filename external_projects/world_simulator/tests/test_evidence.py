"""tests/test_evidence.py — 第二十四轮 A3：证据层（规整 / 追加写存储 / 状态折叠 / 时效）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.4.2–5.4.4。无需真实 LLM、无需联网。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import evidence as ev
from world_simulator.state_model import SimManifest
from world_simulator.store import SimStore, now_iso


def rec(**kw):
    base = {"element_id": "e", "claim": "要点", "source_url": "https://example.org/a", "ev_id": "ev_0001"}
    base.update(kw)
    return base


# ── 规整 ─────────────────────────────────────────────────────────────


def test_url_only_http_https():
    for bad in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "ftp://x.org/a", "//x.org", "example.org",
                "https://", "https://a b.org", "", None, 5, "https://x.org/" + "a" * 600):
        assert ev.clean_url(bad) == "", bad
    assert ev.clean_url(" https://example.org/a?b=1 ") == "https://example.org/a?b=1"
    assert ev.clean_url("HTTP://Example.org") == "HTTP://Example.org"


def test_record_requires_claim_and_valid_url():
    assert ev.normalize_record(rec(claim="  ")) is None
    assert ev.normalize_record(rec(source_url="javascript:x")) is None
    assert ev.normalize_record("x") is None
    assert ev.normalize_record(rec()) is not None


def test_record_cleaning_and_defaults():
    r = ev.normalize_record(rec(claim="a\x00b\n  c" + "x" * 500, confidence="great", published_at="2026-13", status="weird",
                                flags=["out_of_bounds", "evil"], value=True, unit="Wh"))
    assert "\x00" not in r["claim"] and "\n" not in r["claim"] and len(r["claim"]) <= ev.MAX_CLAIM_LEN
    assert r["confidence"] == "low" and r["published_at"] == "" and r["status"] == "active"
    assert r["flags"] == ["out_of_bounds"]
    assert "value" not in r and "unit" not in r  # 布尔不是数值；没有数值就不带单位
    assert ev.normalize_record(rec(value=float("nan")))["claim"] == "要点"
    assert "value" not in ev.normalize_record(rec(value=float("inf")))


def test_published_at_formats():
    assert ev.clean_published_at("2026") == "2026"
    assert ev.clean_published_at("2026-03") == "2026-03"
    assert ev.clean_published_at("2026-03-15") == "2026-03-15"
    for bad in ("March 2026", "2026-00", "2026-03-32", "0999", "", None, "26"):
        assert ev.clean_published_at(bad) == ""


def test_ev_id_only_kept_in_canonical_format():
    assert ev.normalize_record(rec(ev_id="tmp_1"))["ev_id"] == ""
    assert ev.normalize_record(rec(ev_id="ev_0007"))["ev_id"] == "ev_0007"


def test_lowest_confidence():
    assert ev.lowest_confidence([{"confidence": "high"}, {"confidence": "medium"}]) == "medium"
    assert ev.lowest_confidence([{"confidence": "high"}, {"confidence": "low"}]) == "low"
    assert ev.lowest_confidence([]) == "low"


def test_assign_ids_continues_after_max_and_keeps_existing():
    existing = [rec(ev_id="ev_0003"), rec(ev_id="ev_0001")]
    out = ev.assign_ids([rec(ev_id=""), rec(ev_id="ev_0009"), rec(ev_id="")], existing)
    assert [r["ev_id"] for r in out] == ["ev_0004", "ev_0009", "ev_0005"]


# ── 追加写存储 ───────────────────────────────────────────────────────


def test_append_load_roundtrip_and_never_rewrites(tmp_path):
    path = tmp_path / "evidence.jsonl"
    assert ev.load_records(path) == []
    assert ev.append_records(path, [rec(ev_id="ev_0001"), rec(ev_id="ev_0002", claim="二")]) == 2
    first = path.read_text(encoding="utf-8")
    ev.append_records(path, [rec(ev_id="ev_0003")])
    assert path.read_text(encoding="utf-8").startswith(first)  # 只追加
    assert [r["ev_id"] for r in ev.load_records(path)] == ["ev_0001", "ev_0002", "ev_0003"]


def test_append_skips_invalid_and_idless(tmp_path):
    path = tmp_path / "e.jsonl"
    assert ev.append_records(path, [rec(ev_id=""), rec(source_url="x"), "junk"]) == 0
    assert not path.exists()


def test_status_events_fold_without_rewriting(tmp_path):
    path = tmp_path / "e.jsonl"
    ev.append_records(path, [rec(ev_id="ev_0001"), rec(ev_id="ev_0002", claim="二")])
    before = path.read_text(encoding="utf-8")
    assert ev.append_status(path, "ev_0001", "superseded")
    assert ev.append_status(path, "ev_0002", "rejected")
    assert path.read_text(encoding="utf-8").startswith(before)
    by = {r["ev_id"]: r["status"] for r in ev.load_records(path)}
    assert by == {"ev_0001": "superseded", "ev_0002": "rejected"}
    assert ev.append_status(path, "ev_0001", "bogus") is False
    assert ev.append_status(path, "tmp_1", "rejected") is False


def test_load_tolerates_garbage_and_orphans_and_duplicates(tmp_path):
    path = tmp_path / "e.jsonl"
    lines = [
        "not json", "[1,2]", json.dumps(rec(ev_id="ev_0001", claim="先")), json.dumps(rec(ev_id="ev_0001", claim="后")),
        json.dumps({"_op": "status", "ev_id": "ev_0099", "status": "rejected"}),
        json.dumps({"_op": "status", "ev_id": "ev_0001", "status": "nope"}), "",
        json.dumps(rec(ev_id="ev_0002", source_url="javascript:x")),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    got = ev.load_records(path)
    assert [(r["ev_id"], r["claim"], r["status"]) for r in got] == [("ev_0001", "先", "active")]


# ── 查询与时效 ───────────────────────────────────────────────────────


def test_staleness_uses_real_days_and_is_not_guessed():
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    old = rec(retrieved_at=(now - timedelta(days=181)).isoformat())
    fresh = rec(retrieved_at=(now - timedelta(days=179)).isoformat())
    assert ev.is_stale(old, now=now, ttl=180) is True
    assert ev.is_stale(fresh, now=now, ttl=180) is False
    assert ev.is_stale(rec(), now=now) is False  # 没有检索时间 → 不判过期
    assert ev.is_stale(rec(retrieved_at="garbage"), now=now) is False
    assert ev.is_stale(old, now=now, ttl=365) is False
    assert ev.is_stale(old, now=now, ttl=-5) is True  # 非法 ttl 回退默认 180
    assert [r["stale"] for r in ev.annotate_stale([old, fresh], now=now)] == [True, False]


def test_for_element_and_known_ids():
    rows = [rec(ev_id="ev_0001", element_id="a", status="active"), rec(ev_id="ev_0002", element_id="b", status="rejected"),
            rec(ev_id="ev_0003", element_id="b", status="active")]
    assert [r["ev_id"] for r in ev.for_element(rows, "b")] == ["ev_0003"]
    assert [r["ev_id"] for r in ev.for_element(rows, "b", statuses=("active", "rejected"))] == ["ev_0002", "ev_0003"]
    assert ev.known_ids(rows) == ["ev_0001", "ev_0002", "ev_0003"]


# ── SimStore 封装：不随分支走 ────────────────────────────────────────


def test_store_wrappers_live_at_sim_root_not_in_branches(tmp_path):
    store = SimStore.for_root(tmp_path, "s1")
    ts = now_iso()
    store.save_manifest(SimManifest(sim_id="s1", template="t", intent="i", title="t", created_at=ts, updated_at=ts))
    assert store.evidence_path == tmp_path / "s1" / "evidence.jsonl"
    assert store.append_evidence([rec(ev_id="ev_0001")]) == 1
    assert store.set_evidence_status("ev_0001", "rejected")
    assert store.load_evidence()[0]["status"] == "rejected"
    assert "branches" not in str(store.evidence_path)
    assert store.evidence_path == SimStore.for_root(tmp_path, "s1").evidence_path  # 任何分支读到的是同一份
