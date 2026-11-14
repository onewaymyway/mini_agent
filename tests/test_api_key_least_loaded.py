"""least_loaded 轮转策略：负载分散、冷却跳过、跨进程共享、租约释放。"""
import time
from types import SimpleNamespace

from mini_agent.llm.base import LLMRateLimitError
from mini_agent.llm.client_pool import ApiKeyPool, LLMClientPool, ProviderEntry
from mini_agent.llm.key_state import (
    FileKeyState, MemoryKeyState, STATE_FILENAME, key_id,
)
from mini_agent.llm.retry import RetryPolicy

KEYS = ["k1", "k2", "k3"]


def _total_leases(store) -> int:
    return store.transact(lambda d: sum(len(r["leases"]) for r in d["keys"].values()))


def test_concurrent_checkouts_spread_across_keys():
    pool = ApiKeyPool(KEYS, rotation="least_loaded", store=MemoryKeyState())
    leases = [pool.checkout() for _ in range(3)]
    assert {l.key for l in leases} == set(KEYS)


def test_checkin_frees_slot_and_idle_key_is_preferred():
    pool = ApiKeyPool(KEYS, rotation="least_loaded", store=MemoryKeyState())
    a, b, c = pool.checkout(), pool.checkout(), pool.checkout()
    pool.checkin(a)
    assert pool.checkout().key == a.key


def test_cooled_key_is_never_selected_while_cooling():
    pool = ApiKeyPool(KEYS, rotation="least_loaded", store=MemoryKeyState(), cooldown=60)
    nxt = pool.on_error("k1", LLMRateLimitError("limited"))
    assert nxt in {"k2", "k3"}
    for _ in range(6):
        lease = pool.checkout()
        assert lease.key != "k1"
        pool.checkin(lease)


def test_on_success_clears_failure_state():
    pool = ApiKeyPool(KEYS, rotation="least_loaded", store=MemoryKeyState(), cooldown=60)
    pool.on_error("k1", LLMRateLimitError("limited"))
    assert pool.snapshot()[0]["available"] is False
    pool.on_success("k1")
    assert pool.snapshot()[0]["available"] is True
    assert pool.snapshot()[0]["fail_count"] == 0


def test_state_is_shared_across_processes(tmp_path):
    """两个 pool 各自持有独立的 FileKeyState 实例，模拟 daemon 与外部项目两个进程。"""
    path = tmp_path / ".agent" / STATE_FILENAME
    proc_a = ApiKeyPool(KEYS, rotation="least_loaded", store=FileKeyState(path))
    proc_b = ApiKeyPool(KEYS, rotation="least_loaded", store=FileKeyState(path))

    la = proc_a.checkout()
    lb = proc_b.checkout()
    assert la.key != lb.key                       # B 看得见 A 的在途租约

    proc_a.on_error("k1", LLMRateLimitError("limited"))
    for _ in range(4):
        lease = proc_b.checkout()
        assert lease.key != "k1"                  # B 看得见 A 设置的冷却
        proc_b.checkin(lease)

    # 状态文件里只有 key 摘要，没有明文
    text = path.read_text(encoding="utf-8")
    assert "k1" not in text.replace(key_id("k1"), "") or '"k1"' not in text
    assert key_id("k1") in text


def test_leaked_lease_expires_and_does_not_count(tmp_path):
    path = tmp_path / ".agent" / STATE_FILENAME
    store = FileKeyState(path)

    def seed(data):
        data["keys"][key_id("k1")] = {"leases": {"dead": time.time() - 1},
                                      "last_used": 0.0, "cooldown_until": 0.0, "fail_count": 0}
        for k in ("k2", "k3"):
            data["keys"][key_id(k)] = {"leases": {"live": time.time() + 100},
                                       "last_used": 0.0, "cooldown_until": 0.0, "fail_count": 0}
    store.transact(seed)

    pool = ApiKeyPool(KEYS, rotation="least_loaded", store=store)
    assert pool.checkout().key == "k1"            # 过期租约不计入负载


def _entry_for(pool):
    entry = ProviderEntry(
        config=SimpleNamespace(api_key="k1", provider="p", model="m"),
        client=SimpleNamespace(),
        key_pool=pool,
    )

    def rebuild(key, _e=entry):
        _e.config = SimpleNamespace(api_key=key, provider="p", model="m")
    entry.rebuild_client_with_key = rebuild
    return entry


def test_call_entry_releases_lease_on_error_and_switches_key():
    store = MemoryKeyState()
    pool = ApiKeyPool(KEYS, rotation="least_loaded", store=store, cooldown=60)
    entry = _entry_for(pool)
    llm_pool = LLMClientPool(entries=[entry])
    used = []

    def call_fn(_client):
        used.append(entry.config.api_key)
        if entry.config.api_key == "k1":
            raise LLMRateLimitError("limited")
        return "ok"

    policy = RetryPolicy(max_retries=0, conditions=[], retry_delay=0, retry_on_exception=False)
    result = llm_pool._call_entry(entry=entry, call_fn=call_fn, retry_policy=policy,
                                  on_switch_key=None)

    assert result == "ok"
    assert used[0] == "k1" and used[1] in {"k2", "k3"}
    assert _total_leases(store) == 0              # 失败与成功两条路径都已释放租约


# ─────────────────────────────────────────────────────────────────────────────
# 子任务路径（build_pool_with_primary）与外部项目路径
# ─────────────────────────────────────────────────────────────────────────────

from mini_agent.llm.base import LLMConfig
from mini_agent.llm.client_pool import _key_store_for, build_pool_with_primary
from mini_agent.llm.key_state import get_file_backend


def _app_cfg(project_root=None, keys=("ka", "kb", "kc")):
    return SimpleNamespace(
        llm_fallback_chain=[{"provider": "anthropic", "model": "m", "api_keys": list(keys)}],
        llm_fallback_on=None,
        llm_fallback_max_rounds=1,
        llm_fallback_round_wait=0.0,
        llm_provider="anthropic",
        model="m",
        llm_base_url=None,
        max_tokens=1024,
        use_system_tool_call=False,
        system_message_format="system_field",
        project_root=project_root,
    )


def _primary(key="ka"):
    return LLMConfig(provider="anthropic", model="m", api_key=key, base_url=None)


def test_subagent_primary_adopts_multikey_pool():
    pool = build_pool_with_primary(_app_cfg(), _primary("ka"))
    first = pool._entries[0]
    assert first.key_pool is not None                           # 不再是 None
    assert {s.key for s in first.key_pool._states} == {"ka", "kb", "kc"}
    # 被认领的条目不应再以备选身份重复出现
    pools_with_keys = [e for e in pool._entries if e.key_pool is not None]
    assert len(pools_with_keys) == 1


def test_subagent_pools_share_key_state_via_file(tmp_path):
    cfg = _app_cfg(project_root=str(tmp_path))
    a = build_pool_with_primary(cfg, _primary("ka"))
    b = build_pool_with_primary(cfg, _primary("ka"))
    la = a._entries[0].key_pool.checkout()
    lb = b._entries[0].key_pool.checkout()
    assert la.key != lb.key                                      # 两个子任务互相看得见在途
    assert (tmp_path / ".agent" / STATE_FILENAME).exists()
    a._entries[0].key_pool.checkin(la)
    b._entries[0].key_pool.checkin(lb)


def test_external_project_resolves_to_main_root_store(tmp_path, monkeypatch):
    main = tmp_path / "main"
    ext = tmp_path / "ext"
    main.mkdir()
    ext.mkdir()
    (ext / "project.yaml").write_text("name: demo\n", encoding="utf-8")
    monkeypatch.setenv("MINI_AGENT_MAIN_PROJECT_ROOT", str(main))
    store = _key_store_for(SimpleNamespace(project_root=str(ext)))
    assert store._path == (main / ".agent" / STATE_FILENAME).resolve()
