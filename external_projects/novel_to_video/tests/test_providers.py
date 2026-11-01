import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from novel_to_video.providers.image import AgnesImageProvider, StubImageProvider
from novel_to_video.providers.keypool import (KeyPool, is_non_retryable, is_rate_limit,
                                               keys_from_env, pool_from_env)
from novel_to_video.providers.factory import ProviderError, make_image_provider


class Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text or ("" if body is None else str(body))

    def json(self):
        return self._body


def png_b64():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


OK_BODY = None


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append({"auth": headers["Authorization"], "payload": json})
        item = self.responses.pop(0)
        return item


def ok_resp():
    return Resp(200, {"data": [{"b64_json": png_b64()}]})


# ---------------- key pool

def test_classifiers():
    assert is_rate_limit(429) and is_rate_limit(500, "You've reached the API rate limit")
    assert is_non_retryable(401) and is_non_retryable(422)
    assert not is_non_retryable(429) and not is_non_retryable(500)


def test_keys_from_env_dedup_and_single():
    env = {"AGNES_API_KEYS": "k1, k2 ,k1", "AGNES_API_KEY": "k3"}
    assert keys_from_env(env) == ["k1", "k2", "k3"]
    assert pool_from_env({}) is None


def test_pool_rotates_on_rate_limit_and_cools_down():
    pool = KeyPool(["a", "b"], cooldown=60)
    assert pool.acquire() == "a"
    assert pool.report_rate_limit("a") == "b"
    assert pool.acquire() == "b"
    assert pool.report_rate_limit("b") is None  # 全部冷却
    assert pool.acquire() is None


def test_pool_requires_keys():
    with pytest.raises(ValueError):
        KeyPool([])


# ---------------- Agnes provider

def test_agnes_success_writes_png(tmp_path):
    pool = KeyPool(["k1"])
    sess = FakeSession([ok_resp()])
    prov = AgnesImageProvider(pool, session=sess, sleep=lambda s: None)
    out = tmp_path / "a.png"
    res = prov.generate("prompt", out, ratio="3:4")
    assert res.ok and out.is_file()
    assert sess.calls[0]["payload"]["ratio"] == "3:4"
    assert sess.calls[0]["payload"]["extra_body"]["response_format"] == "b64_json"


def test_agnes_rate_limit_switches_key_without_consuming_retries(tmp_path):
    pool = KeyPool(["k1", "k2"])
    sess = FakeSession([Resp(429, text="rate limit"), ok_resp()])
    prov = AgnesImageProvider(pool, session=sess, max_retries=1, sleep=lambda s: None)
    res = prov.generate("p", tmp_path / "b.png")
    assert res.ok
    assert [c["auth"] for c in sess.calls] == ["Bearer k1", "Bearer k2"]


def test_agnes_non_retryable_stops_immediately(tmp_path):
    pool = KeyPool(["k1"])
    sess = FakeSession([Resp(422, text="bad mode")])
    prov = AgnesImageProvider(pool, session=sess, max_retries=3, sleep=lambda s: None)
    res = prov.generate("p", tmp_path / "c.png")
    assert not res.ok and res.non_retryable
    assert len(sess.calls) == 1


def test_agnes_server_error_retries_then_fails(tmp_path):
    pool = KeyPool(["k1"])
    sess = FakeSession([Resp(500, text="boom")] * 3)
    prov = AgnesImageProvider(pool, session=sess, max_retries=3, sleep=lambda s: None)
    res = prov.generate("p", tmp_path / "d.png")
    assert not res.ok and not res.non_retryable and "500" in res.error
    assert len(sess.calls) == 3


def test_factory_requires_key(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEYS", raising=False)
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("NOVEL_TO_VIDEO_PROVIDERS", raising=False)
    with pytest.raises(ProviderError):
        make_image_provider("agnes")


def test_factory_forced_stub(monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    assert isinstance(make_image_provider("agnes"), StubImageProvider)


def test_stub_image_writes_file(tmp_path):
    res = StubImageProvider().generate("hello", tmp_path / "s.png")
    assert res.ok and Path(res.path).is_file()
