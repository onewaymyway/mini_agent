"""
[kanban_account_management_ui_plan.md] `UserStore` 管理员身份相关新方法的
单测：`is_admin` / `set_admin` / `admin_count` / `list_users_detailed`，
以及新增的 `add_user(..., is_admin=...)` 参数和"最后一个管理员不能被降级/
删除"的保护逻辑。不依赖 Streamlit 运行时，直接测 `apps/mini_agent_kanban/
auth.py`。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

from auth import LastAdminError, UserStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return UserStore(tmp_path / "kanban_users.json")


def test_old_format_file_without_is_admin_defaults_to_false(tmp_path):
    """旧格式文件里没有 is_admin 字段的账户，读取时按 False 处理，不报错。"""
    users_file = tmp_path / "kanban_users.json"
    users_file.write_text(
        json.dumps({"alice": {"salt": "aa", "hash": "bb"}}), encoding="utf-8"
    )
    store = UserStore(users_file)
    assert store.is_admin("alice") is False
    assert store.admin_count() == 0
    detailed = store.list_users_detailed()
    assert detailed == [{"username": "alice", "is_admin": False, "created_at": None}]


def test_add_user_default_not_admin(store):
    store.add_user("bob", "password1")
    assert store.is_admin("bob") is False


def test_add_user_is_admin_true_persists(store):
    store.add_user("alice", "password1", is_admin=True)
    assert store.is_admin("alice") is True
    # 重新打开一个 UserStore 实例，确认是落盘的，不是内存缓存
    store2 = UserStore(store.path)
    assert store2.is_admin("alice") is True


def test_admin_count(store):
    store.add_user("alice", "password1", is_admin=True)
    store.add_user("bob", "password1", is_admin=False)
    store.add_user("carol", "password1", is_admin=True)
    assert store.admin_count() == 2


def test_set_admin_promote(store):
    store.add_user("bob", "password1")
    assert store.set_admin("bob", True) is True
    assert store.is_admin("bob") is True


def test_set_admin_unknown_user_returns_false(store):
    assert store.set_admin("ghost", True) is False


def test_set_admin_rejects_demoting_last_admin(store):
    store.add_user("alice", "password1", is_admin=True)
    with pytest.raises(LastAdminError):
        store.set_admin("alice", False)
    # 拒绝之后账户仍然是管理员，没有被部分修改
    assert store.is_admin("alice") is True


def test_set_admin_allows_demoting_when_another_admin_exists(store):
    store.add_user("alice", "password1", is_admin=True)
    store.add_user("bob", "password1", is_admin=True)
    assert store.set_admin("bob", False) is True
    assert store.is_admin("bob") is False
    assert store.is_admin("alice") is True


def test_remove_user_rejects_removing_last_admin(store):
    store.add_user("alice", "password1", is_admin=True)
    with pytest.raises(LastAdminError):
        store.remove_user("alice")
    assert store.list_users() == ["alice"]


def test_remove_user_allows_removing_non_last_admin(store):
    store.add_user("alice", "password1", is_admin=True)
    store.add_user("bob", "password1", is_admin=True)
    assert store.remove_user("bob") is True
    assert store.list_users() == ["alice"]


def test_remove_user_nonexistent_returns_false(store):
    assert store.remove_user("ghost") is False


def test_bootstrap_zero_admins_not_protected_by_last_admin_logic(store):
    """兜底逻辑：admin_count() == 0 时，压根没有"当前是管理员"这个前提，
    不会触发"最后一个管理员"保护——因为这个保护只在"目标账户当前是管理员
    且降级/删除后会归零"时生效，admin_count() 已经是 0 的情况下这条分支
    走不到。"""
    store.add_user("alice", "password1", is_admin=False)
    assert store.admin_count() == 0
    # 非管理员账户可以被随便删除/设置，不受"最后一个管理员"保护牵连
    assert store.set_admin("alice", True) is True
    assert store.admin_count() == 1


def test_add_user_upsert_preserves_admin_when_reset_password(store):
    """"改自己密码"路径依赖：add_user 传入当前 is_admin 值时不会把管理员
    身份误重置成 False。"""
    store.add_user("alice", "password1", is_admin=True)
    store.add_user("alice", "newpassword", is_admin=store.is_admin("alice"))
    assert store.is_admin("alice") is True
    assert store.verify("alice", "newpassword") is True


def test_list_users_detailed_includes_created_at(store):
    store.add_user("alice", "password1", is_admin=True)
    detailed = store.list_users_detailed()
    assert len(detailed) == 1
    assert detailed[0]["username"] == "alice"
    assert detailed[0]["is_admin"] is True
    assert isinstance(detailed[0]["created_at"], float)


def test_list_users_original_method_unaffected(store):
    """list_users() 原方法保留不动，manage_users.py list 命令继续用它。"""
    store.add_user("bob", "password1", is_admin=True)
    assert store.list_users() == ["bob"]
