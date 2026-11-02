"""视频网络开关测试：默认不使用系统代理；创建、查询、下载共用同一 session；停止时轮询与下载立即退出。

HTTP 全部通过 monkeypatch 模拟；系统代理通过环境变量模拟（设为一个不存在的本地地址，
若代码误用了代理，请求会直接失败而不是静默通过）。不产生任何真实调用。
"""

import json
import shutil
import threading
from pathlib import Path

import pytest
import requests

from novel_to_video import cancel, tool_api
from novel_to_video import project_store as ps
from novel_to_video.providers import skill_bridge
from novel_to_video.providers.skill_providers import SkillVideoProvider

REPO_SKILLS = Path(__file__).resolve().parents[3] / ".claude" / "skills"
DOWNLOAD_URL = "https://platform-outputs.agnes-ai.space/videos/agnes-video-2.5/task_x.mp4"
FAKE_PROXY = "http://127.0.0.1:9"   # 9 号端口几乎必定无服务：误用代理会暴露为连接失败


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    shutil.copytree(REPO_SKILLS / "gen_video_with_text", tmp_path / ".claude" / "skills" / "gen_video_with_text")
    shutil.copytree(REPO_SKILLS / "gen_image_with_text", tmp_path / ".claude" / "skills" / "gen_image_with_text")
    monkeypatch.setenv("NOVEL_TO_VIDEO_SKILLS_DIR", str(tmp_path / ".claude" / "skills"))
    monkeypatch.setenv("HTTPS_PROXY", FAKE_PROXY)
    monkeypatch.setenv("HTTP_PROXY", FAKE_PROXY)
    monkeypatch.delenv("AGNES_API_KEYS", raising=False)
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    skill_bridge._CACHE.clear()
    (tmp_path / "providers.json").write_text(
        json.dumps({"providers": {"agnes": {"api_keys": ["k1"]}}}), encoding="utf-8")
    yield tmp_path
    skill_bridge._CACHE.clear()


def _video_module():
    return skill_bridge.modules()["video"]


def _proxies_for(session, url):
    return session.merge_environment_settings(url, {}, None, None, None)["proxies"]


class _Resp:
    def __init__(self, code=200, body=None, chunks=None, text=""):
        self.status_code = code
        self._body = body if body is not None else {}
        self.text = text or json.dumps(self._body, ensure_ascii=False)
        self.ok = code < 400
        self._chunks = chunks or []

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size=1024):
        yield from self._chunks


# ----------------------------------------------------------------- 默认与开关

def test_default_does_not_use_system_proxy(isolated):
    client = _video_module().AgnesVideoClient(api_key="k")
    assert client.use_system_proxy is False
    assert client.session.trust_env is False
    # 即使设置了环境变量代理，直连下载地址时也不会引入任何代理
    assert _proxies_for(client.session, DOWNLOAD_URL) == {}


def test_switch_on_uses_environment_proxy(isolated):
    client = _video_module().AgnesVideoClient(api_key="k", use_system_proxy=True)
    assert client.use_system_proxy is True and client.session.trust_env is True
    assert _proxies_for(client.session, DOWNLOAD_URL).get("https") == FAKE_PROXY


def test_provider_reads_switch_from_video_config(isolated):
    off = SkillVideoProvider(video_cfg={"max_wait_sec": 60})
    on = SkillVideoProvider(video_cfg={"use_system_proxy": True})
    assert off.client.session.trust_env is False
    assert on.client.session.trust_env is True


def test_project_default_is_direct(tmp_path):
    assert ps.DEFAULT_OPTIONS["video"]["use_system_proxy"] is False


def test_set_video_proxy_persists_to_project(tmp_path):
    ps.write_json_atomic(tmp_path / "project.json", {"video": {"max_wait_sec": 3600}})
    r = tool_api.set_video_proxy(str(tmp_path), True)
    assert r["ok"] and r["data"]["use_system_proxy"] is True
    saved = ps.read_json(tmp_path / "project.json")["video"]
    assert saved["use_system_proxy"] is True and saved["max_wait_sec"] == 3600   # 其它字段保留
    tool_api.set_video_proxy(str(tmp_path), False)
    assert ps.read_json(tmp_path / "project.json")["video"]["use_system_proxy"] is False


# ----------------------------------------------------------------- 下载走同一 session

def test_download_uses_session_not_bare_requests(isolated, monkeypatch, tmp_path):
    mod = _video_module()
    seen = {}

    def fake_session_get(self, url, **kw):
        seen["trust_env"] = self.trust_env
        seen["url"] = url
        return _Resp(200, chunks=[b"abc", b"def"])

    def bare_get(*a, **kw):
        raise AssertionError("下载不应绕过客户端 session 直接调用 requests.get")

    monkeypatch.setattr(requests.Session, "get", fake_session_get)
    monkeypatch.setattr(requests, "get", bare_get)
    client = mod.AgnesVideoClient(api_key="k", max_retries=1)
    save = tmp_path / "v.mp4"
    client._save_video_from_url(DOWNLOAD_URL, str(save))
    assert save.read_bytes() == b"abcdef"
    assert seen["trust_env"] is False and seen["url"] == DOWNLOAD_URL


def test_stop_during_download_removes_partial_file(isolated, monkeypatch, tmp_path):
    mod = _video_module()
    ev = threading.Event()

    def fake_session_get(self, url, **kw):
        def chunks():
            yield b"part"
            ev.set()                  # 用户在下载途中停止
            yield b"never"
        return _StreamResp(chunks())

    monkeypatch.setattr(requests.Session, "get", fake_session_get)
    client = mod.AgnesVideoClient(api_key="k", max_retries=3, cancel_event=ev)
    save = tmp_path / "half.mp4"
    with pytest.raises(mod.ClientCancelled):
        client._save_video_from_url(DOWNLOAD_URL, str(save))
    assert not save.exists()          # 不留下看起来存在但损坏的半成品


class _StreamResp(_Resp):
    def __init__(self, gen):
        super().__init__(200)
        self._gen = gen

    def iter_content(self, chunk_size=1024):
        return self._gen


# ----------------------------------------------------------------- 轮询立即停止

def test_polling_stops_after_signal_without_more_queries(isolated, monkeypatch, tmp_path):
    mod = _video_module()
    ev = threading.Event()
    queries = []

    def fake_post(self, url, **kw):
        return _Resp(200, {"video_id": "task_1"})

    def fake_get(self, url, **kw):
        queries.append(url)
        ev.set()                      # 第一次查询后用户停止
        return _Resp(200, {"status": "in_progress"})

    monkeypatch.setattr(requests.Session, "post", fake_post)
    monkeypatch.setattr(requests.Session, "get", fake_get)
    client = mod.AgnesVideoClient(api_key="k", max_retries=2, cancel_event=ev)
    res = client.generate_video(prompt="p", save_path=str(tmp_path / "x.mp4"),
                                poll_interval=0.01, max_wait_seconds=3600)
    assert res.get("cancelled") is True and res.get("success") is False
    assert len(queries) == 1          # 停止后不再继续轮询


def test_queue_wait_is_interruptible(isolated, monkeypatch):
    mod = _video_module()
    ev = threading.Event()

    def fake_post(self, url, **kw):
        ev.set()
        return _Resp(503, text='{"error":"video_queue_full","message":"please retry later"}')

    monkeypatch.setattr(requests.Session, "post", fake_post)
    client = mod.AgnesVideoClient(api_key="k", max_retries=3, queue_wait_seconds=3600, cancel_event=ev)
    res = client.create_video(prompt="p")
    assert res.get("cancelled") is True      # 排队等待中被停止：不再等待 5 秒退避


def test_provider_maps_cancel_to_stop_not_failure(isolated, tmp_path):
    provider = SkillVideoProvider(video_cfg={})
    provider.client.generate_video = lambda **kw: {"success": False, "cancelled": True, "error": "用户已停止"}
    cancel.begin(tmp_path)
    with cancel.bind(tmp_path):
        with pytest.raises(cancel.Cancelled):
            provider.generate("p", tmp_path / "o.mp4", seconds=4, reference_images=[])
