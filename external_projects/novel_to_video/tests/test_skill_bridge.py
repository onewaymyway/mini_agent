"""skill 桥接测试：key 从 providers.json 解析（不依赖环境变量）、图像与视频经 skill 客户端调用、错误分类。

隔离：把 mini_agent 的两个 skill 目录复制到临时目录，在其上级放置临时 providers.json，
通过 NOVEL_TO_VIDEO_SKILLS_DIR 指向副本。HTTP 通过 monkeypatch 模拟，不产生任何真实调用。
"""

import json
import shutil
from pathlib import Path

import pytest
import requests

from novel_to_video.providers import factory, skill_bridge
from novel_to_video.providers.skill_providers import SkillImageProvider, SkillVideoProvider

REPO_SKILLS = Path(__file__).resolve().parents[3] / ".claude" / "skills"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """复制 skill 目录到 tmp/.claude/skills，并在 tmp 根放置 providers.json。"""
    for name in ("gen_image_with_text", "gen_video_with_text"):
        shutil.copytree(REPO_SKILLS / name, tmp_path / ".claude" / "skills" / name)
    monkeypatch.setenv("NOVEL_TO_VIDEO_SKILLS_DIR", str(tmp_path / ".claude" / "skills"))
    monkeypatch.delenv("AGNES_API_KEYS", raising=False)
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("NOVEL_TO_VIDEO_PROVIDERS", raising=False)
    skill_bridge._CACHE.clear()
    yield tmp_path
    skill_bridge._CACHE.clear()


def _write_providers(root: Path, body: dict) -> None:
    (root / "providers.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def test_keys_resolved_from_providers_json_block(isolated):
    _write_providers(isolated, {"providers": {"agnes": {"api_keys": ["k1", "k2"], "key_rotation": "round_robin"}}})
    cfg = skill_bridge.key_config()
    assert cfg["count"] == 2 and cfg["source"] == "providers.json" and cfg["rotation"] == "round_robin"


def test_keys_resolved_from_llm_fallback_chain(isolated):
    _write_providers(isolated, {"llm_fallback_chain": [{"provider": "agnes", "api_keys": ["k9"]}]})
    assert skill_bridge.key_config()["count"] == 1


def test_env_overrides_providers_json(isolated, monkeypatch):
    _write_providers(isolated, {"providers": {"agnes": {"api_keys": ["file-key"]}}})
    monkeypatch.setenv("AGNES_API_KEYS", "env-a,env-b")
    cfg = skill_bridge.key_config()
    assert cfg["count"] == 2 and "AGNES_API_KEYS" in cfg["source"]


def test_no_key_message_lists_where_it_looked(isolated):
    msg = factory.no_key_message("图像")
    assert "providers.json" in msg and "AGNES_API_KEYS" in msg and "0 把 key" in msg


def test_factory_builds_skill_providers_without_env(isolated):
    _write_providers(isolated, {"providers": {"agnes": {"api_keys": ["k1"]}}})
    assert isinstance(factory.make_image_provider("agnes"), SkillImageProvider)
    assert isinstance(factory.make_video_provider("agnes"), SkillVideoProvider)


def test_factory_reports_missing_key_clearly(isolated):
    with pytest.raises(factory.ProviderError, match="未找到可用的 Agnes API key"):
        factory.make_image_provider("agnes")


class _Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self._body = body if body is not None else {}
        self.text = text or json.dumps(self._body, ensure_ascii=False)
        self.ok = code < 400

    def json(self):
        return self._body


def test_image_success_goes_through_skill_and_saves_file(isolated, monkeypatch, tmp_path):
    _write_providers(isolated, {"providers": {"agnes": {"api_keys": ["k1"]}}})
    seen = {}

    def fake_post(self, url, json=None, headers=None, timeout=None, **kw):
        seen["auth"] = headers.get("Authorization")
        seen["model"] = json.get("model")
        return _Resp(200, {"data": [{"url": "http://img/x.png"}]})

    def fake_save(self, url, save_path, max_retries=3):
        Path(save_path).write_bytes(b"PNG")

    monkeypatch.setattr(requests.Session, "post", fake_post)
    img_mod = skill_bridge.modules()["image"]
    monkeypatch.setattr(img_mod.AgnesImageClient, "_save_image_from_url", fake_save)

    prov = factory.make_image_provider("agnes")
    out = tmp_path / "out" / "p.png"
    res = prov.generate("a lone figure", out, ratio="9:16")
    assert res.ok, res.error
    assert out.read_bytes() == b"PNG"
    assert seen["auth"] == "Bearer k1"
    assert seen["model"] == "agnes-image-2.5-flash"


def test_image_auth_error_is_non_retryable(isolated, monkeypatch, tmp_path):
    _write_providers(isolated, {"providers": {"agnes": {"api_keys": ["bad"]}}})
    monkeypatch.setattr(requests.Session, "post",
                        lambda self, url, json=None, headers=None, timeout=None, **kw:
                        _Resp(401, {"error": "invalid api key"}, text="invalid api key"))
    prov = factory.make_image_provider("agnes")
    res = prov.generate("x", tmp_path / "p.png", ratio="9:16")
    assert not res.ok and res.non_retryable is True


def test_image_rate_limit_switches_key_then_succeeds(isolated, monkeypatch, tmp_path):
    _write_providers(isolated, {"providers": {"agnes": {"api_keys": ["k1", "k2"]}}})
    auths = []

    def fake_post(self, url, json=None, headers=None, timeout=None, **kw):
        auths.append(headers["Authorization"])
        if len(auths) == 1:
            return _Resp(429, {"error": "rate limit"}, text="rate limit exceeded")
        return _Resp(200, {"data": [{"url": "http://img/x.png"}]})

    def fake_save(self, url, save_path, max_retries=3):
        Path(save_path).write_bytes(b"PNG")

    monkeypatch.setattr(requests.Session, "post", fake_post)
    monkeypatch.setattr(skill_bridge.modules()["image"].AgnesImageClient, "_save_image_from_url", fake_save)
    monkeypatch.setattr("time.sleep", lambda s: None)
    res = factory.make_image_provider("agnes").generate("x", tmp_path / "p.png", ratio="9:16")
    assert res.ok, res.error
    assert auths[0] == "Bearer k1" and auths[-1] == "Bearer k2"


def test_video_reference_flow_through_skill(isolated, monkeypatch, tmp_path):
    _write_providers(isolated, {"providers": {"agnes": {"api_keys": ["k1"]}}})
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG")
    created = {}

    def fake_post(self, url, json=None, headers=None, timeout=None, **kw):
        created["mode"] = json.get("mode")
        created["seconds"] = json.get("seconds")
        return _Resp(200, {"video_id": "v1"})

    def fake_get(self, url, params=None, headers=None, timeout=None, **kw):
        return _Resp(200, {"status": "completed", "video_url": "http://v/out.mp4"})

    def fake_save(self, url, save_path):
        Path(save_path).write_bytes(b"MP4")

    vid_mod = skill_bridge.modules()["video"]
    monkeypatch.setattr(requests.Session, "post", fake_post)
    monkeypatch.setattr(requests.Session, "get", fake_get)
    monkeypatch.setattr(vid_mod.AgnesVideoClient, "_save_video_from_url", fake_save)
    monkeypatch.setattr("time.sleep", lambda s: None)

    prov = factory.make_video_provider("agnes")
    out = tmp_path / "clip.mp4"
    res = prov.generate("slow push-in", out, seconds=4, reference_images=[ref], aspect_ratio="9:16")
    assert res.ok, res.error
    assert out.read_bytes() == b"MP4"
    assert created["mode"] == "reference" and str(created["seconds"]) == "4"


def test_real_repo_skill_is_discoverable():
    """在真实仓库结构中能找到 skill（不要求有 key）。"""
    assert skill_bridge.find_skills_dir().name == "skills"
