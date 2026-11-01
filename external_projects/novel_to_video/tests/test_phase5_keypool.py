"""Phase 5 key 池切换演练：限流切换、全部冷却不发空 key、非重试错误不切换、冷却恢复。"""

import pytest

from novel_to_video.providers.keypool import KeyPool
from novel_to_video.providers.video import AgnesVideoProvider


class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self._body = body or {}
        self.text = text or str(body or "")
        self.ok = code < 400

    def json(self):
        return self._body


class ScriptSession:
    """按顺序返回预设响应，并记录每次请求使用的 Authorization。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.auth = []

    def _take(self, headers):
        self.auth.append(headers.get("Authorization", ""))
        if not self.responses:
            raise AssertionError("意外的额外请求")
        return self.responses.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        return self._take(headers)

    def post(self, url, json=None, headers=None, timeout=None):
        return self._take(headers)


def _provider(pool, session, **kw):
    return AgnesVideoProvider(pool, session=session, sleep=lambda s: None,
                              poll_interval=0.01, max_wait=5, **kw)


DONE = Resp(200, {"status": "completed", "video_url": "http://v"})


def test_poll_rotates_key_on_rate_limit():
    pool = KeyPool(["k1", "k2"], cooldown=600)
    sess = ScriptSession([Resp(429, text="rate limit"), DONE])
    url = _provider(pool, sess)._poll("vid")
    assert url == "http://v"
    assert sess.auth == ["Bearer k1", "Bearer k2"]


def test_poll_never_sends_empty_key_when_all_cooling():
    pool = KeyPool(["k1"], cooldown=0.2)
    pool.report_rate_limit("k1")
    sess = ScriptSession([])  # 不应发出任何请求
    with pytest.raises(RuntimeError, match="超时"):
        _provider(pool, sess)._poll("vid")
    assert sess.auth == []


def test_poll_resumes_after_cooldown():
    pool = KeyPool(["k1"], cooldown=0.05)
    pool.report_rate_limit("k1")
    import time
    time.sleep(0.1)  # 冷却结束
    sess = ScriptSession([DONE])
    assert _provider(pool, sess)._poll("vid") == "http://v"
    assert sess.auth == ["Bearer k1"]


def test_create_rotates_on_rate_limit_then_succeeds():
    pool = KeyPool(["k1", "k2"], cooldown=600)
    sess = ScriptSession([Resp(429, text="too many requests"), Resp(200, {"video_id": "v1"})])
    vid, err = _provider(pool, sess)._create({"prompt": "x"})
    assert vid == "v1" and err is None
    assert sess.auth == ["Bearer k1", "Bearer k2"]


def test_create_does_not_rotate_on_auth_error():
    pool = KeyPool(["k1", "k2"], cooldown=600)
    sess = ScriptSession([Resp(401, text="invalid api key")])
    vid, err = _provider(pool, sess)._create({"prompt": "x"})
    assert vid is None
    msg, fatal = err
    assert fatal is True
    assert sess.auth == ["Bearer k1"]  # 鉴权错误不换 key、不重试


def test_create_reports_all_keys_cooling_without_request():
    pool = KeyPool(["k1"], cooldown=600)
    pool.report_rate_limit("k1")
    sess = ScriptSession([])
    vid, err = _provider(pool, sess)._create({"prompt": "x"})
    assert vid is None
    assert err[0] == "所有 API key 均在冷却中"
    assert sess.auth == []


def test_success_clears_cooldown():
    pool = KeyPool(["k1"], cooldown=600)
    pool.report_rate_limit("k1")  # 冷却中
    pool._until["k1"] = 0.0  # 模拟冷却结束
    sess = ScriptSession([DONE])
    _provider(pool, sess)._poll("vid")
    assert pool.acquire() == "k1"
