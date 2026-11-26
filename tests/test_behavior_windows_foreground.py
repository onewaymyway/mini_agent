"""Windows 前台窗口采集：pywin32 优先，缺失时回退 ctypes。

Linux 沙盒无法调用真实 Win32 API，因此：
- 组合逻辑 _foreground_from_api 用假 api 覆盖各种情况；
- 后端选择用 monkeypatch 覆盖；
- 真实 ctypes 调用只在 Windows 实机验证（见文档清单）。
"""

import pytest

from mini_agent.perception.behavior.collectors import active_window as aw


class FakeApi:
    def __init__(self, hwnd=1001, title="report.docx - Word", pid=4242,
                 path=r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"):
        self._hwnd, self._title, self._pid, self._path = hwnd, title, pid, path

    def foreground(self):
        return self._hwnd

    def title(self, hwnd):
        return self._title

    def pid(self, hwnd):
        return self._pid

    def image_path(self, pid):
        return self._path


def test_program_name_is_basename_of_image_path():
    assert aw._foreground_from_api(FakeApi()) == ("WINWORD.EXE", "report.docx - Word")


def test_no_foreground_window_returns_none():
    assert aw._foreground_from_api(FakeApi(hwnd=None)) is None
    assert aw._foreground_from_api(FakeApi(hwnd=0)) is None


def test_access_denied_falls_back_to_unknown_but_keeps_title():
    # 提权进程 OpenProcess 失败 → image_path 为空串，程序名降级为 unknown，标题仍保留
    result = aw._foreground_from_api(FakeApi(path=""))
    assert result == ("unknown", "report.docx - Word")


def test_pid_zero_skips_image_lookup():
    class NoLookup(FakeApi):
        def image_path(self, pid):
            raise AssertionError("pid 为 0 时不应查询映像路径")

    assert aw._foreground_from_api(NoLookup(pid=0)) == ("unknown", "report.docx - Word")


def test_empty_title_is_allowed():
    assert aw._foreground_from_api(FakeApi(title="")) == ("WINWORD.EXE", "")


def test_ctypes_detect_is_none_off_windows():
    if aw._is_windows():
        pytest.skip("仅在非 Windows 上验证降级")
    assert aw._detect_windows_ctypes() is None
    assert aw.windows_backend() is None


def test_backend_prefers_pywin32_when_importable(monkeypatch):
    monkeypatch.setattr(aw, "_pywin32_importable", lambda: True)
    monkeypatch.setattr(aw, "_is_windows", lambda: True)
    assert aw.windows_backend() == "pywin32"
    monkeypatch.setattr(aw, "_detect_windows_pywin32", lambda: ("pyw.exe", "t"))
    monkeypatch.setattr(aw, "_detect_windows_ctypes", lambda: ("ctypes.exe", "t"))
    assert aw._detect_windows() == ("pyw.exe", "t")


def test_backend_falls_back_to_ctypes_without_pywin32(monkeypatch):
    monkeypatch.setattr(aw, "_pywin32_importable", lambda: False)
    monkeypatch.setattr(aw, "_is_windows", lambda: True)
    assert aw.windows_backend() == "ctypes"
    monkeypatch.setattr(aw, "_detect_windows_pywin32", lambda: pytest.fail("不应调用 pywin32"))
    monkeypatch.setattr(aw, "_detect_windows_ctypes", lambda: ("WINWORD.EXE", "x"))
    assert aw._detect_windows() == ("WINWORD.EXE", "x")


def test_collector_available_via_ctypes_fallback(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(aw, "_pywin32_importable", lambda: False)
    monkeypatch.setattr(aw, "_is_windows", lambda: True)
    monkeypatch.setattr(aw, "_detect_windows_ctypes", lambda: ("WINWORD.EXE", "x"))
    assert aw.detect_active_window() == ("WINWORD.EXE", "x")


def test_ctypes_exception_is_logged_and_returns_none(monkeypatch):
    monkeypatch.setattr(aw, "_is_windows", lambda: True)
    monkeypatch.setattr(aw, "_WIN32_API", None)

    def boom():
        raise OSError("dll load failed")

    monkeypatch.setattr(aw, "_Win32Api", boom)
    assert aw._detect_windows_ctypes() is None
