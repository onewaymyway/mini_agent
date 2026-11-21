"""R36：生图 key 与诊断——与 mini_agent 生图 skill 一致的 key 解析（含 providers.json），以及生图开启/调用日志。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from article_to_video import joblog
from article_to_video.app_settings import set_field
from article_to_video.providers import keypool
from article_to_video.providers.image import (AgnesImageProvider, image_diagnosis, log_image_diagnosis,
                                              project_image_provider)
from article_to_video.providers.keypool import KeyPool, pool_from_env, resolve_keys
from test_deck_phase4 import small_project  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("AGNES_API_KEYS", "AGNES_API_KEY", "AGNES_PROVIDERS_JSON"):
        monkeypatch.delenv(k, raising=False)


def _fake_skill_tree(tmp_path: Path, providers: dict | None) -> Path:
    """模拟 mini_agent 仓库结构：.claude/skills/gen_image_with_text/agnes_key_pool.py，并可放 providers.json。"""
    root = tmp_path / "mini_agent"
    skill = root / ".claude" / "skills" / "gen_image_with_text"
    skill.mkdir(parents=True)
    (skill / "agnes_key_pool.py").write_text("# stub\n", encoding="utf-8")
    if providers is not None:
        (root / "providers.json").write_text(json.dumps(providers), encoding="utf-8")
    return skill


def test_env_keys_still_work_and_take_priority_over_files(tmp_path, monkeypatch):
    skill = _fake_skill_tree(tmp_path, {"providers": {"agnes": {"api_keys": ["from-file"]}}})
    monkeypatch.setenv("AGNES_API_KEYS", "k1, k2")
    info = resolve_keys(start=skill)
    assert info["keys"] == ["k1", "k2"] and info["source"] == "环境变量 AGNES_API_KEYS"


def test_providers_json_is_used_like_the_skill(tmp_path):
    skill = _fake_skill_tree(tmp_path, {"providers": {"agnes": {"api_keys": ["a", "b", "a"]}}})
    info = resolve_keys(start=skill)
    assert info["keys"] == ["a", "b"]                              # 去重保序
    assert "providers.json" in info["source"] and info["path"].endswith("providers.json")


def test_providers_json_fallback_chain_form(tmp_path):
    skill = _fake_skill_tree(tmp_path, {"llm_fallback_chain": [{"provider": "agnes", "api_key": "x"}]})
    assert resolve_keys(start=skill)["keys"] == ["x"]


def test_explicit_providers_json_path(tmp_path, monkeypatch):
    skill = _fake_skill_tree(tmp_path, None)
    alt = tmp_path / "alt.json"
    alt.write_text(json.dumps({"providers": {"agnes": {"api_keys": ["alt-key"]}}}), encoding="utf-8")
    monkeypatch.setenv("AGNES_PROVIDERS_JSON", str(alt))
    info = resolve_keys(start=skill)
    assert info["keys"] == ["alt-key"] and "AGNES_PROVIDERS_JSON" in info["searched"][1]


def test_not_found_lists_where_it_looked(tmp_path):
    skill = _fake_skill_tree(tmp_path, None)
    info = resolve_keys(start=skill)
    assert info["keys"] == [] and info["source"] == "未找到"
    text = " ".join(info["searched"])
    assert "AGNES_API_KEYS" in text and "providers.json" in text and "AGNES_API_KEY" in text


def test_explicit_env_dict_never_reads_files(tmp_path):
    """测试与调用方传入的环境字典：只看字典，不读磁盘（保证测试确定性）。"""
    _fake_skill_tree(tmp_path, {"providers": {"agnes": {"api_keys": ["disk"]}}})
    assert pool_from_env({}) is None
    assert pool_from_env({"AGNES_API_KEY": "one"}).source == "环境变量 AGNES_API_KEY"


def test_diagnosis_reports_source_without_leaking_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("AGNES_API_KEYS", "secret-1,secret-2")
    diag = image_diagnosis("agnes")
    assert diag["ok"] and diag["keys"] == 2 and "secret" not in json.dumps(diag, ensure_ascii=False)
    assert image_diagnosis("agnes", env={})["ok"] is False


def test_status_lines_are_written_to_the_job_log(small_project, tmp_path, monkeypatch):
    with joblog.bind(small_project, "deck"):
        log_image_diagnosis(image_diagnosis("agnes", env={}), enabled=True)
        log_image_diagnosis(image_diagnosis("agnes", env={"AGNES_API_KEY": "z"}), enabled=True)
        log_image_diagnosis(image_diagnosis("agnes", env={}), enabled=False)
    msgs = [r["message"] for r in joblog.tail(small_project, scopes=["deck"])]
    assert any("未找到可用的 agnes key" in m for m in msgs)
    assert any("检查过" in m for m in msgs)
    assert any("已开启，服务 agnes，1 把 key" in m for m in msgs)
    assert any("已在设置中关闭" in m for m in msgs)


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = 0

    def post(self, *a, **k):
        self.posts += 1
        return self.responses.pop(0)


def _png_b64():
    import base64
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_generation_logs_attempts_and_success(small_project, tmp_path):
    pool = KeyPool(["k1", "k2"])
    pool.source = "测试"
    sess = FakeSession([FakeResp(429, text="rate limit"), FakeResp(200, {"data": [{"b64_json": _png_b64()}]})])
    prov = AgnesImageProvider(pool, session=sess, sleep=lambda s: None)
    with joblog.bind(small_project, "deck"):
        res = prov.generate("一个圆", tmp_path / "x.png", ratio="16:9")
    assert res.ok and sess.posts == 2
    msgs = [r["message"] for r in joblog.tail(small_project, scopes=["deck"])]
    assert any(m.startswith("生图请求") for m in msgs)
    assert any("生图限流" in m and "换 key" in m for m in msgs)
    assert any(m.startswith("生图成功") and "用时" in m for m in msgs)


def test_project_provider_follows_the_switch(small_project, monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "k")
    assert project_image_provider(small_project) is not None
    set_field(small_project, "visual", "images.enabled", False)
    assert project_image_provider(small_project) is None          # 关闭后不构造、不调用
    set_field(small_project, "visual", "images.enabled", True)
    monkeypatch.delenv("AGNES_API_KEY")
    assert project_image_provider(small_project) is None          # 没有 key：返回 None（不静默）


def test_background_logs_the_service_in_use(small_project, monkeypatch):
    from article_to_video.deck import background as bg
    from article_to_video.providers.image import StubImageProvider
    with joblog.bind(small_project, "deck"):
        bg.ensure_section_backgrounds(small_project, "P01",
                                      [{"key": "s1", "title": "节", "context": ""}],
                                      provider=StubImageProvider(), style_anchor="a",
                                      orientation="landscape", size="1024x1024")
        bg.ensure_section_backgrounds(small_project, "P01",
                                      [{"key": "s2", "title": "节二", "context": ""}],
                                      provider=None, style_anchor="a",
                                      orientation="landscape", size="1024x1024")
    msgs = [r["message"] for r in joblog.tail(small_project, scopes=["deck"])]
    assert any("背景：生图服务 stub" in m for m in msgs)
    assert any("生图服务不可用" in m for m in msgs)
