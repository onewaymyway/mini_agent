"""视频接口：排队已满（please retry later）不计入重试次数，一直等待；普通错误仍按次数重试；等待上限可配置。"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

from novel_to_video import tool_api
from novel_to_video import project_store as ps

SKILL = Path(__file__).resolve().parents[3] / ".claude" / "skills" / "gen_video_with_text" / "agnes_tools.py"


@pytest.fixture
def client_cls():
    sys.path.insert(0, str(SKILL.parent))          # skill 内部会 import 同目录的 agnes_key_pool
    spec = importlib.util.spec_from_file_location("agnes_video_skill", SKILL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.AgnesVideoClient


class _Resp:
    def __init__(self, status, text="", body=None):
        self.status_code, self.text, self.ok = status, text, 200 <= status < 300
        self._body = body or {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, *a, **k):
        self.calls += 1
        return self.responses.pop(0)


QUEUE_FULL = '{"code":"video_queue_full","message":"video queue is full, please retry later"}'
PLAIN_503 = '{"code":"upstream","message":"bad gateway"}'


def _client(client_cls, responses, **kw):
    c = client_cls(api_key="k", max_retries=2, **kw)
    c.session = _Session(responses)
    return c


def test_queue_full_is_waited_out_without_spending_retries(client_cls, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    responses = [_Resp(503, QUEUE_FULL) for _ in range(7)] + [_Resp(200, body={"video_id": "v1"})]
    c = _client(client_cls, responses, queue_wait_seconds=3600)
    out = c._post("http://x/videos", {})
    assert out == {"video_id": "v1"}
    assert c.session.calls == 8                  # 7 次排队 + 1 次成功，超过 max_retries=2 仍成功


def test_plain_errors_still_stop_after_retry_limit(client_cls, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _client(client_cls, [_Resp(503, PLAIN_503) for _ in range(5)])
    out = c._post("http://x/videos", {})
    assert out["success"] is False
    assert c.session.calls == 2                  # 普通错误仍按 max_retries=2 计数


def test_queue_wait_has_an_upper_bound(client_cls, monkeypatch):
    slept = {"total": 0.0}
    monkeypatch.setattr(time, "sleep", lambda s: slept.__setitem__("total", slept["total"] + s))
    c = _client(client_cls, [_Resp(503, QUEUE_FULL) for _ in range(500)], queue_wait_seconds=30)
    out = c._post("http://x/videos", {})
    assert out["success"] is False
    assert slept["total"] <= 30 + 30                 # 最多超出一个退避周期


def test_video_wait_defaults_match_the_spec():
    assert ps.DEFAULT_VIDEO_WAIT == {"max_wait_sec": 3600, "max_retries": 5, "queue_wait_sec": 3600,
                                    "use_system_proxy": False}


def test_set_video_wait_saves_and_validates(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"gear": "G0"}), encoding="utf-8")
    r = tool_api.set_video_wait(str(tmp_path), 3600, 5, 7200)
    assert r["ok"] and r["data"] == {"max_wait_sec": 3600, "max_retries": 5, "queue_wait_sec": 7200}
    saved = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert saved["gear"] == "G0" and saved["video"]["max_wait_sec"] == 3600
    assert not tool_api.set_video_wait(str(tmp_path), 10, 5, 0)["ok"]        # 等待上限太短
    assert not tool_api.set_video_wait(str(tmp_path), 3600, 11, 0)["ok"]    # 重试次数超范围
