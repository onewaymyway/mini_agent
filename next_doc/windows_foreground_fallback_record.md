# Windows 前台窗口采集回退（实施记录）

## 问题
`perception/behavior/collectors/active_window.py` 的 Windows 实现依赖 `win32gui`、`win32process`、`psutil`。`pywin32` 未安装时采集器在 Windows 上静默不可用，而 `requirements.txt` 中没有 pywin32。

## 改动
| 文件 | 内容 |
|---|---|
| `src/mini_agent/perception/behavior/collectors/active_window.py` | 原实现移入 `_detect_windows_pywin32()`；新增 `_Win32Api`（ctypes 直调 user32/kernel32）、纯函数 `_foreground_from_api()`、`_detect_windows_ctypes()`、`windows_backend()`；`_detect_windows()` 按 pywin32 → ctypes 选择，首次成功记录后端；OS 判断封装为 `_is_windows()` 便于测试 |
| `tests/test_behavior_windows_foreground.py` | 10 个用例：程序名取自映像路径、无前台窗口、无权限降级为 unknown、pid=0 不查询、空标题、非 Windows 降级、后端优先级、回退、采集器可用性、异常记录 |
| `docs/behavior-perception-guide.md` | 说明两种后端、边界与 Windows 实机清单 |

## 设计取舍
- pywin32 路径保持不变，已安装的环境行为不受影响。
- 逻辑与系统调用分离：组合逻辑用假 api 测试，真实 ctypes 调用只能在 Windows 实机验证。
- 无权限时保留窗口标题，只把程序名降级为 `unknown`，尽量不丢信息。
- 仅使用 `GetForegroundWindow` 等只读查询，不做窗口内容读取，与感知层的隐私边界一致。

## 验证
- 新增 10 项测试通过。
- 行为感知相关测试通过。
- 全量测试：见下方结果。

## 未验证（需 Windows 实机）
`GetWindowTextW` 对 UWP 应用返回的标题（前台进程常为 `ApplicationFrameHost.exe`，本次未处理）；提权进程的 `OpenProcess` 拒绝行为；锁屏时前台窗口为空的表现。
