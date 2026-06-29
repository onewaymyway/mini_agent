"""
tests/test_permissions_interrupted_by_http.py
— permissions.py::_InterruptedByHTTP 根因修复回归测试

背景：这个异常类用于表示"本地终端正在阻塞等待用户输入（确认/权限
审批），但 HTTP 端（另一个 CLI 客户端、web demo）先给出了响应"这一
中断信号。ui/terminal.py::Terminal.confirm() 在 interrupt_event 被
外部 set() 时需要抛出这个异常，用的写法是：

    try:
        from mini_agent.permissions import _InterruptedByHTTP
    except ImportError:
        class _InterruptedByHTTP(Exception): pass
    raise _InterruptedByHTTP()

这个类之前在 permissions.py 里从未被真正定义过（只在注释/字符串里
提到这个名字）。这意味着上面那个 import 总是失败，每次命中这个分支都
会动态生成一个全新的本地类对象。如果任何调用方（比如
cli/daemon.py::_handle_connected_permission）也用同样的"尝试 import
失败就本地定义"模式去 except 这个类型，捕获到的是调用方自己生成的
另一个类对象，跟 confirm() 实际抛出的那个类对象不是同一个 —— Python
的 except 是按类型身份匹配的，两个名字相同但定义位置/时机不同的类
不是同一个类型，精确 except 匹配会失败，异常会真的向上传播而不是被
正常处理为"已被其他端响应，停止等待"。

permissions.py 自己调用 confirm() 的地方（_prompt_with_http）一直是用
宽泛的 except Exception 配合检查 decided_event.is_set() 绕开了这个
坑，没有暴露出来；但这是一个该修的根因——本次修复把这个类真正定义在
permissions.py 里，让所有"try import, except 本地定义"的调用点都能
找到同一个真正存在的类。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_interrupted_by_http_is_a_real_class_in_permissions_module():
    """_InterruptedByHTTP 必须是 permissions.py 模块里真正定义的类，
    不是只存在于注释/字符串里的名字。"""
    from mini_agent import permissions

    assert hasattr(permissions, "_InterruptedByHTTP")
    cls = permissions._InterruptedByHTTP
    assert isinstance(cls, type)
    assert issubclass(cls, Exception)


def test_two_try_except_import_sites_get_the_same_class_object():
    """
    核心回归测试：模拟 ui/terminal.py 和 cli/daemon.py 两处独立的
    "try: from mini_agent.permissions import _InterruptedByHTTP except
    ImportError: class _InterruptedByHTTP(Exception): pass" 写法，
    确认它们现在拿到的是同一个类对象（is 比较），而不是两个分别动态
    生成的本地类。这是修复前会失败的核心断言——修复前这个 import 总是
    失败，每次都会创建一个新的本地类，两次执行这段代码拿到的类对象
    不是同一个。
    """

    def _simulate_import_site():
        try:
            from mini_agent.permissions import _InterruptedByHTTP as _Cls
        except ImportError:
            class _Cls(Exception):
                pass
        return _Cls

    cls_a = _simulate_import_site()
    cls_b = _simulate_import_site()

    assert cls_a is cls_b, (
        "两处独立的 'try import except 本地定义' 代码拿到了不同的类对象——"
        "说明 from mini_agent.permissions import _InterruptedByHTTP 没有"
        "成功，又退化回了每次动态生成本地类的旧行为"
    )


def test_exception_raised_in_one_module_caught_by_except_in_another():
    """
    端到端验证：一个"模块"（模拟 ui/terminal.py）抛出的
    _InterruptedByHTTP 实例，能被另一个"模块"（模拟
    cli/daemon.py::_handle_connected_permission）用标准的
    "from mini_agent.permissions import _InterruptedByHTTP" + 精确
    except 捕获到——这正是 _handle_connected_permission 现在依赖的
    捕获路径（见该函数文档字符串"历史教训"一节）。
    """

    def _raise_like_terminal_py():
        try:
            from mini_agent.permissions import _InterruptedByHTTP
        except ImportError:
            class _InterruptedByHTTP(Exception):
                pass
        raise _InterruptedByHTTP()

    def _catch_like_daemon_py():
        try:
            from mini_agent.permissions import _InterruptedByHTTP
        except ImportError:
            class _InterruptedByHTTP(Exception):
                pass

        try:
            _raise_like_terminal_py()
            return "not_raised"
        except _InterruptedByHTTP:
            return "caught_precisely"
        except Exception:
            return "caught_by_broad_except_only"

    result = _catch_like_daemon_py()
    assert result == "caught_precisely", (
        f"期望精确 except _InterruptedByHTTP 捕获成功，实际结果："
        f"{result!r}（caught_by_broad_except_only 说明精确匹配仍然失败，"
        f"只能靠宽泛兜底）"
    )


def test_interrupted_by_http_importable_directly():
    """最基本的导入检查——确认正常的 import 语句能工作，不需要任何
    try/except 包装也能成功（因为类现在真的存在了）。"""
    from mini_agent.permissions import _InterruptedByHTTP

    # 能正常实例化和抛出/捕获
    try:
        raise _InterruptedByHTTP("test")
    except _InterruptedByHTTP as e:
        assert str(e) == "test"
