"""图像接口（G-3 定妆图）：账号受限（403 unusual activity）时换 key 重试；全部受限则明确停止并给出可读错误。"""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

from novel_to_video.providers import skill_providers as sp

SKILLS = Path(__file__).resolve().parents[3] / ".claude" / "skills" / "gen_image_with_text"
RESTRICTED = ('{"error":{"code":"","message":"We\'ve detected unusual activity on your account. '
              'Access has been temporarily restricted.","type":"AgnesAI_error"}}')
K1, K2, K3, K4 = ("sk-aaaa1111bbbb2222", "sk-cccc3333dddd4444", "sk-eeee5555ffff6666", "sk-gggg7777hhhh8888")


@pytest.fixture
def mods():
    sys.path.insert(0, str(SKILLS))
    spec = importlib.util.spec_from_file_location("agnes_image_skill_restricted", SKILLS / "agnes_tools.py")
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
        self.responses, self.auth_seen = list(responses), []

    def post(self, url, headers=None, **k):
        self.auth_seen.append(headers["Authorization"])
        return self.responses.pop(0)


def _client(mods, keys, responses, max_retries=3):
    pool = mods.AgnesKeyPool(keys) if len(keys) > 1 else None
    c = mods.AgnesImageClient(api_key=keys[0], key_pool=pool, max_retries=max_retries)
    c.session = _Session(responses)
    return c


def test_restricted_keys_are_skipped_until_a_good_one_is_found(mods, monkeypatch, capsys):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    ok = _Resp(200, body={"data": [{"url": "http://img"}]})
    c = _client(mods, [K1, K2, K3, K4], [_Resp(403, RESTRICTED), _Resp(403, RESTRICTED), ok])
    out = c._post({"prompt": "p"})
    assert out == {"data": [{"url": "http://img"}]}
    assert c.session.auth_seen == [f"Bearer {K1}", f"Bearer {K2}", f"Bearer {K3}"]   # 逐把换 key
    log = capsys.readouterr().out
    assert "#1 sk-a…2222" in log and "#2 sk-c…4444" in log and "#3 sk-e…6666" in log
    assert "unusual activity" in log                                                 # 服务端原文进日志
    for k in (K1, K2, K3, K4):
        assert k not in log                                                          # 完整 key 不进入日志


def test_all_keys_restricted_stops_without_sleeping_and_flags(mods, monkeypatch, capsys):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    c = _client(mods, [K1, K2, K3, K4], [_Resp(403, RESTRICTED)] * 4 + [_Resp(200, body={"data": [{"url": "u"}]})])
    out = c._post({})
    assert out["key_restricted"] is True and out["non_retryable"] is True
    assert len(c.session.auth_seen) == 4 and slept == []                             # 4 把各试一次，无空等
    log = capsys.readouterr().out
    assert "冷却中" in log and all(k not in log for k in (K1, K2, K3, K4))


def test_single_key_restricted_stops_without_retrying(mods):
    c = _client(mods, [K1], [_Resp(403, RESTRICTED), _Resp(200, body={"data": [{"url": "u"}]})])
    out = c._post({})
    assert out["key_restricted"] is True and c.session.auth_seen == [f"Bearer {K1}"]


def test_generate_image_propagates_flags_and_failure_text_is_readable(mods):
    c = _client(mods, [K1, K2], [_Resp(403, RESTRICTED), _Resp(403, RESTRICTED)])
    res = c.generate_image(prompt="p")
    assert res["success"] is False and res["key_restricted"] is True and res["non_retryable"] is True
    text, non_retryable = sp._failure(res)
    assert non_retryable and "所有 API key" in text
    assert "{'success'" not in text and "unusual activity" in text                   # 不再是 dict repr


def test_readable_error_unwraps_nested_dict():
    err = {"success": False, "error": {"status_code": 403, "error": RESTRICTED}}
    text = sp._readable_error(err)
    assert text.startswith("HTTP 403：We've detected unusual activity") and "{" not in text
    assert sp._readable_error("连接超时") == "连接超时"


def test_rate_limit_still_rotates_and_logs_switch(mods, monkeypatch, capsys):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _client(mods, [K1, K2], [_Resp(429, "rate limited"), _Resp(200, body={"data": [{"url": "u"}]})])
    assert c._post({}) == {"data": [{"url": "u"}]}
    assert c.session.auth_seen == [f"Bearer {K1}", f"Bearer {K2}"]
    assert "触发限流" in capsys.readouterr().out


def test_key_switch_lines_are_warn_not_error_in_job_log():
    from novel_to_video import joblog
    assert joblog.infer_level("[KEY 切换·POST] 账号异常/访问受限：失败的 key #1 sk-a…2222 → 切换到 key #2 sk-c…4444") == "warn"
    assert joblog.infer_level("[账号受限·服务端原始返回] status_code=403: unusual activity") == "warn"
    assert joblog.infer_level("[KEY 受限·POST] 失败的 key #4 账号受限，且没有可切换的 key，停止。") == "error"
    assert joblog.infer_level("[KEY] key 配置来源：providers.json，共 4 把") == "info"
