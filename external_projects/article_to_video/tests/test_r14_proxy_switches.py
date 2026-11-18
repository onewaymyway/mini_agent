"""R14：大模型与生图的「使用系统代理」开关，默认关闭。"""
import os
import threading

from article_to_video.netproxy import proxy_policy
from article_to_video.project_store import normalize_settings
from article_to_video.providers import image as I
from article_to_video.providers.keypool import KeyPool
from article_to_video.providers.text import MiniAgentTextLLM
from article_to_video.visual.config import normalize_visual


def test_defaults_off():
    assert normalize_settings({})[0]["llm"]["use_system_proxy"] is False
    assert normalize_visual({})[0]["images"]["use_system_proxy"] is False
    assert normalize_settings({"llm": {"use_system_proxy": True}})[0]["llm"]["use_system_proxy"] is True
    assert normalize_visual({"images": {"use_system_proxy": True}})[0]["images"]["use_system_proxy"] is True


def test_image_session_trust_env(monkeypatch):
    pool = KeyPool(["k"])
    assert I.AgnesImageProvider(pool).session.trust_env is False
    assert I.AgnesImageProvider(pool, use_system_proxy=True).session.trust_env is True
    monkeypatch.setenv("AGNES_API_KEYS", "k1")
    assert I.make_image_provider("agnes", use_system_proxy=True).session.trust_env is True
    assert I.make_image_provider("agnes").session.trust_env is False


def test_proxy_policy_hides_and_restores(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://p:1")
    monkeypatch.setenv("NO_PROXY", "x")
    with proxy_policy(False):
        assert "HTTPS_PROXY" not in os.environ and os.environ["NO_PROXY"] == "*"
        with proxy_policy(False):     # 嵌套
            pass
        assert "HTTPS_PROXY" not in os.environ
    assert os.environ["HTTPS_PROXY"] == "http://p:1" and os.environ["NO_PROXY"] == "x"


def test_proxy_policy_on_changes_nothing(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://p:1")
    with proxy_policy(True):
        assert os.environ["HTTPS_PROXY"] == "http://p:1"


def test_llm_ask_runs_without_proxy_by_default(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://p:1")
    seen = {}

    class H:
        def ask(self, prompt, system=""):
            seen["proxy"] = os.environ.get("HTTPS_PROXY")
            return "ok"
    for flag, expect in ((False, None), (True, "http://p:1")):
        llm = MiniAgentTextLLM(use_system_proxy=flag)
        llm._helper = H()
        assert llm.ask("hi") == "ok" and seen["proxy"] == expect
    assert os.environ["HTTPS_PROXY"] == "http://p:1"
