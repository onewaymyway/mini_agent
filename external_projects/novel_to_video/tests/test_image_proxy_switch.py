"""图像网络开关：生图请求与图片下载默认直连，不读取系统代理；开启后才读取。"""
import json

import pytest
import requests

from novel_to_video import tool_api
from novel_to_video.providers.skill_bridge import modules


class _Resp:
    def __init__(self, content=b"PNGDATA"):
        self.content = content

    def raise_for_status(self):
        return None


def _client(use_system_proxy):
    cls = modules()["image"].AgnesImageClient
    return cls(api_key="k", use_system_proxy=use_system_proxy)


def test_default_is_direct_and_session_ignores_env_proxy():
    c = _client(False)
    assert c.use_system_proxy is False and c.session.trust_env is False


def test_enabled_reads_system_proxy():
    c = _client(True)
    assert c.use_system_proxy is True and c.session.trust_env is True


def test_download_uses_session_not_bare_requests(tmp_path, monkeypatch):
    c = _client(False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("不应直接调用 requests.get"))
    seen = []
    monkeypatch.setattr(c.session, "get", lambda url, **k: seen.append(url) or _Resp())
    out = tmp_path / "a.png"
    c._save_image_from_url("https://example.invalid/a.png", str(out))
    assert out.read_bytes() == b"PNGDATA" and seen == ["https://example.invalid/a.png"]


def test_disabled_never_falls_back_to_proxy_bypass(tmp_path, monkeypatch):
    """关闭代理时，下载失败只做常规重试，不再发起“绕过代理”的兜底请求。"""
    mod = modules()["image"]
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    c = _client(False)
    calls = []

    def boom(url, **kwargs):
        calls.append(kwargs)
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(c.session, "get", boom)
    with pytest.raises(RuntimeError):
        c._save_image_from_url("https://example.invalid/a.png", str(tmp_path / "a.png"), max_retries=2)
    assert len(calls) == 2 and all("proxies" not in k for k in calls)


def test_enabled_falls_back_to_direct_after_retries(tmp_path, monkeypatch):
    mod = modules()["image"]
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    c = _client(True)
    calls = []

    def flaky(url, **kwargs):
        calls.append(kwargs)
        if kwargs.get("proxies"):
            return _Resp()                    # 显式绕过代理的兜底成功
        raise requests.exceptions.ConnectionError("proxy down")

    monkeypatch.setattr(c.session, "get", flaky)
    out = tmp_path / "b.png"
    c._save_image_from_url("https://example.invalid/b.png", str(out), max_retries=2)
    assert out.read_bytes() == b"PNGDATA"
    assert calls[-1].get("proxies") == {"http": None, "https": None}


def test_set_image_proxy_persists_and_defaults_off(tmp_path):
    from test_chapter_pipeline import build_export
    from novel_to_video import project_store as ps
    src = build_export(tmp_path / "src", chapters=(1,))
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    proj = res["data"]["project_dir"]
    from pathlib import Path
    assert json.loads((Path(proj) / "project.json").read_text(encoding="utf-8")).get("image", {}).get(
        "use_system_proxy", False) is False
    assert tool_api.set_image_proxy(proj, True)["ok"]
    assert tool_api._image_cfg(Path(proj))["use_system_proxy"] is True
    assert tool_api.set_image_proxy(proj, False)["ok"]
    assert tool_api._image_cfg(Path(proj))["use_system_proxy"] is False
