"""
[kanban_session_management_plan.md] `SessionStore`（会话登记表）以及
`make_token`/`verify_token` 改成"签名 + session_id 双重校验"之后的单测。
不依赖 Streamlit 运行时，直接测 `apps/mini_agent_kanban/auth.py`。
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

from auth import SessionStore, make_token, verify_token  # noqa: E402


@pytest.fixture()
def session_store(tmp_path):
    return SessionStore(tmp_path / "kanban_sessions.json")


def test_create_returns_session_id_and_exp(session_store):
    sid, exp = session_store.create("alice", client_id="1.2.3.4")
    assert isinstance(sid, str) and len(sid) > 0
    assert isinstance(exp, int)
    assert exp > time.time()


def test_is_valid_true_right_after_create(session_store):
    sid, _exp = session_store.create("alice")
    assert session_store.is_valid(sid) is True
    assert session_store.is_valid(sid, username="alice") is True


def test_is_valid_false_for_wrong_username(session_store):
    sid, _exp = session_store.create("alice")
    assert session_store.is_valid(sid, username="bob") is False


def test_is_valid_false_for_unknown_session(session_store):
    assert session_store.is_valid("does-not-exist") is False


def test_is_valid_false_for_expired_session(session_store):
    sid, _exp = session_store.create("alice", ttl_seconds=-10)
    assert session_store.is_valid(sid) is False


def test_revoke_makes_session_invalid(session_store):
    sid, _exp = session_store.create("alice")
    assert session_store.revoke(sid) is True
    assert session_store.is_valid(sid) is False


def test_revoke_unknown_session_returns_false(session_store):
    assert session_store.revoke("ghost") is False


def test_revoke_all_for_user_excludes_current_session(session_store):
    sid1, _ = session_store.create("alice")
    sid2, _ = session_store.create("alice")
    sid3, _ = session_store.create("alice")
    revoked = session_store.revoke_all_for_user("alice", except_session_id=sid2)
    assert revoked == 2
    assert session_store.is_valid(sid1) is False
    assert session_store.is_valid(sid2) is True
    assert session_store.is_valid(sid3) is False


def test_revoke_all_for_user_does_not_touch_other_users(session_store):
    alice_sid, _ = session_store.create("alice")
    bob_sid, _ = session_store.create("bob")
    session_store.revoke_all_for_user("alice")
    assert session_store.is_valid(alice_sid) is False
    assert session_store.is_valid(bob_sid) is True


def test_revoke_all_wipes_everyone(session_store):
    sid1, _ = session_store.create("alice")
    sid2, _ = session_store.create("bob")
    count = session_store.revoke_all()
    assert count == 2
    assert session_store.is_valid(sid1) is False
    assert session_store.is_valid(sid2) is False


def test_list_sessions_filters_by_username(session_store):
    session_store.create("alice")
    session_store.create("alice")
    session_store.create("bob")
    assert len(session_store.list_sessions(username="alice")) == 2
    assert len(session_store.list_sessions(username="bob")) == 1
    assert len(session_store.list_sessions()) == 3


def test_list_sessions_excludes_expired(session_store):
    sid_live, _ = session_store.create("alice")
    sid_expired, _ = session_store.create("alice", ttl_seconds=-10)
    sessions = session_store.list_sessions(username="alice")
    ids = {s["session_id"] for s in sessions}
    assert sid_live in ids
    assert sid_expired not in ids


def test_touch_updates_last_seen_when_stale(session_store):
    sid, _ = session_store.create("alice")
    before = session_store.list_sessions(username="alice")[0]["last_seen"]
    # 手动把 last_seen 往前拨，模拟"已经过了节流窗口"
    data = session_store._load()  # noqa: SLF001 — 测试内部读取，验证行为用
    data[sid]["last_seen"] = before - 1000
    session_store._save(data)  # noqa: SLF001
    session_store.touch(sid, min_interval=300)
    after = session_store.list_sessions(username="alice")[0]["last_seen"]
    assert after > before - 1000


def test_touch_throttled_within_min_interval(session_store):
    sid, _ = session_store.create("alice")
    before = session_store.list_sessions(username="alice")[0]["last_seen"]
    session_store.touch(sid, min_interval=300)  # 刚创建，距上次 last_seen 很近
    after = session_store.list_sessions(username="alice")[0]["last_seen"]
    assert after == before  # 没被节流窗口内的调用更新


def test_touch_unknown_session_is_noop(session_store):
    session_store.touch("ghost")  # 不应该抛异常


def test_make_token_and_verify_token_roundtrip():
    secret = b"test-secret-key"
    token = make_token("alice", "sess123", int(time.time()) + 3600, secret)
    result = verify_token(token, secret)
    assert result == ("alice", "sess123")


def test_verify_token_rejects_tampered_signature():
    secret = b"test-secret-key"
    token = make_token("alice", "sess123", int(time.time()) + 3600, secret)
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert verify_token(tampered, secret) is None


def test_verify_token_rejects_expired():
    secret = b"test-secret-key"
    token = make_token("alice", "sess123", int(time.time()) - 10, secret)
    assert verify_token(token, secret) is None


def test_verify_token_rejects_old_three_part_format():
    """升级前签发的旧格式 token（username:exp:sig，3 段）应该被视为无效，
    强制重新登录，而不是抛异常或误判成功。"""
    secret = b"test-secret-key"
    old_style_token = "alice:9999999999:deadbeef"
    assert verify_token(old_style_token, secret) is None


def test_verify_token_wrong_secret_fails():
    token = make_token("alice", "sess123", int(time.time()) + 3600, b"secret-a")
    assert verify_token(token, b"secret-b") is None


def test_end_to_end_session_revocation_invalidates_token():
    """模拟真实使用流程：签发 token → 校验通过 → 撤销会话 → 同一个 token
    的签名校验依然"合法"，但配合 SessionStore.is_valid 复核后应判定失效。
    这正是 render_login_gate 里"签名有效 + 会话仍然存活"双重检查的核心
    行为，防止已撤销的会话仅凭合法签名就能继续登录。"""
    import tempfile
    secret = b"test-secret-key"
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(Path(tmp) / "sessions.json")
        sid, exp = store.create("alice", client_id="9.9.9.9")
        token = make_token("alice", sid, exp, secret)

        result = verify_token(token, secret)
        assert result == ("alice", sid)
        assert store.is_valid(sid, "alice") is True

        store.revoke(sid)

        # 签名校验本身依然通过（签名没变、没过期）……
        result_after_revoke = verify_token(token, secret)
        assert result_after_revoke == ("alice", sid)
        # ……但 SessionStore 复核后判定已失效，调用方应据此拒绝登录。
        assert store.is_valid(sid, "alice") is False
