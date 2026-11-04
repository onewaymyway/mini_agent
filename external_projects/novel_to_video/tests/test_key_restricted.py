"""视频接口：账号受限（403 unusual activity / 访问受限）视为 key 级故障：切换 key 重试；全部受限则明确停止。"""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

from novel_to_video.providers import skill_providers as sp

SKILLS = Path(__file__).resolve().parents[3] / ".claude" / "skills" / "gen_video_with_text"
RESTRICTED = ('{"error":{"code":"","message":"We\'ve detected unusual activity on your account. '
              'Access has been temporarily restricted.","type":"AgnesAI_error"}}')


@pytest.fixture
def mods():
    sys.path.insert(0, str(SKILLS))
    spec = importlib.util.spec_from_file_location("agnes_video_skill_restricted", SKILLS / "agnes_tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, status, text="", body=None):
        self.status_code, self.text, self.ok = status, text, 200 <= status < 300
        self._body = body or {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.auth_seen = []

    def post(self, url, headers=None, **k):
        self.auth_seen.append(headers["Authorization"])
        return self.responses.pop(0)


K1 = "sk-aaaa1111bbbb2222"
K2 = "sk-cccc3333dddd4444"


def test_restricted_key_is_skipped_and_request_resubmitted(mods, monkeypatch, capsys):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    pool = mods.AgnesKeyPool([K1, K2])
    c = mods.AgnesVideoClient(api_key=K1, key_pool=pool, max_retries=1)
    c.session = _Session([_Resp(403, RESTRICTED), _Resp(200, body={"video_id": "v9"})])
    out = c._post("http://x/videos", {})
    assert out == {"video_id": "v9"}
    assert c.api_key == K2
    assert c.session.auth_seen == [f"Bearer {K1}", f"Bearer {K2}"]     # 第二次请求确实换成了新 key
    assert pool.acquire() == K2                                         # 受限的 K1 处于冷却中
    log = capsys.readouterr().out
    assert "#1 sk-a…2222" in log and "#2 sk-c…4444" in log              # 日志写出失败的 key 与切换到的 key
    assert K1 not in log and K2 not in log                              # 完整 key 不进入日志


def test_single_key_restricted_stops_without_retrying(mods):
    c = mods.AgnesVideoClient(api_key=K1, max_retries=3)
    c.session = _Session([_Resp(403, RESTRICTED), _Resp(200, body={"video_id": "x"})])
    out = c._post("http://x/videos", {})
    assert out["success"] is False and out["key_restricted"] is True and out["non_retryable"] is True
    assert c.session.auth_seen == [f"Bearer {K1}"]                      # 只请求了一次


def test_all_keys_restricted_reports_clearly(mods, capsys):
    pool = mods.AgnesKeyPool([K1, K2])
    c = mods.AgnesVideoClient(api_key=K1, key_pool=pool, max_retries=1)
    c.session = _Session([_Resp(403, RESTRICTED), _Resp(403, RESTRICTED)])
    out = c._post("http://x/videos", {})
    assert out["key_restricted"] is True
    text, non_retryable = sp._failure(out)
    assert "所有 API key" in text and non_retryable
    log = capsys.readouterr().out
    assert "冷却中" in log and K1 not in log and K2 not in log


def test_restriction_during_polling_resubmits_the_shot(mods, monkeypatch):
    pool = mods.AgnesKeyPool([K1, K2])
    c = mods.AgnesVideoClient(api_key=K1, key_pool=pool, max_retries=1)
    calls = []

    def impl(*a, **k):
        calls.append(c.api_key)
        if len(calls) == 1:
            return {"success": False, "key_restricted": True, "non_retryable": True}
        return {"success": True, "video_url": "u"}

    monkeypatch.setattr(c, "_generate_video_impl", impl)
    out = c.generate_video(prompt="p")
    assert out == {"success": True, "video_url": "u"}
    assert len(calls) == 2                                              # 整条镜头重新提交一次
