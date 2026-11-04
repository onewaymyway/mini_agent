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


K3 = "sk-eeee5555ffff6666"


def test_three_keys_from_explicit_providers_json_are_all_loaded(mods, tmp_path, monkeypatch):
    import json as _json
    cfg = tmp_path / "providers.json"
    cfg.write_text(_json.dumps({"providers": {"agnes": {"api_keys": [K1, K2, K3]}}}), encoding="utf-8")
    monkeypatch.setenv("AGNES_PROVIDERS_JSON", str(cfg))
    monkeypatch.delenv("AGNES_API_KEYS", raising=False)
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    kp = sys.modules["agnes_key_pool"]
    pool = kp.build_key_pool(str(tmp_path))
    assert len(pool) == 3
    summary = pool.config_summary()
    assert "共 3 把" in summary and str(cfg) in summary and "AGNES_PROVIDERS_JSON" in summary
    lines = pool.status_lines()
    assert len(lines) == 3 and all(f"#{i + 1}" in ln for i, ln in enumerate(lines))
    for k in (K1, K2, K3):
        assert k not in summary and all(k not in ln for ln in lines)


def test_video_shot_logs_all_keys_with_status_masked(mods, tmp_path, monkeypatch, capsys):
    import json as _json
    cfg = tmp_path / "providers.json"
    cfg.write_text(_json.dumps({"providers": {"agnes": {"api_keys": [K1, K2, K3]}}}), encoding="utf-8")
    monkeypatch.setenv("AGNES_PROVIDERS_JSON", str(cfg))
    monkeypatch.delenv("AGNES_API_KEYS", raising=False)
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    pool = sys.modules["agnes_key_pool"].build_key_pool(str(tmp_path))
    c = mods.AgnesVideoClient(key_pool=pool, max_retries=1)
    monkeypatch.setattr(c, "_generate_video_impl", lambda *a, **k: {"success": True})
    c.generate_video(prompt="p")
    log = capsys.readouterr().out
    assert "共 3 把" in log and "#1" in log and "#2" in log and "#3" in log
    assert "本镜头使用 key #1" in log
    assert K1 not in log and K2 not in log and K3 not in log


def test_failure_logs_show_masked_raw_server_body(mods, capsys, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    pool = sys.modules["agnes_key_pool"].AgnesKeyPool([K1, K2])
    c = mods.AgnesVideoClient(api_key=K1, key_pool=pool, max_retries=1)
    raw = '{"error":{"message":"bad request for key ' + K1 + ' and sk-zzzz9999yyyy","code":"x"}}'
    c.session = _Session([_Resp(400, raw)])
    c._post("http://x/videos", {})
    log = capsys.readouterr().out
    assert "bad request for key" in log                  # 原始内容确实打印了
    assert K1 not in log and "sk-zzzz9999yyyy" not in log    # 密钥全部脱敏


def test_queue_full_prints_raw_body_on_first_round(mods, capsys, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = mods.AgnesVideoClient(api_key=K1, max_retries=1, queue_wait_seconds=10)
    body = '{"error":{"message":"video_queue_full, please retry later"}}'
    c.session = _Session([_Resp(429, body), _Resp(200, body={"video_id": "v1"})])
    c._post("http://x/videos", {})
    log = capsys.readouterr().out
    assert "排队已满·服务端原始返回" in log and "video_queue_full" in log


def test_every_queue_full_round_prints_raw_body(mods, capsys, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = mods.AgnesVideoClient(api_key=K1, max_retries=1, queue_wait_seconds=10000)
    body = '{"code":"video_queue_full","message":"video queue is full, please retry later"}'
    c.session = _Session([_Resp(503, body)] * 3 + [_Resp(200, body={"video_id": "v1"})])
    c._post("http://x/videos", {})
    log = capsys.readouterr().out
    assert log.count("排队已满·服务端原始返回") == 3          # 每一次失败都打印，不抽样
