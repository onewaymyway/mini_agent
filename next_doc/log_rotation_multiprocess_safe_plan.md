# 日志轮转多进程安全改造计划

## 问题
daemon 运行时，另开一个非 daemon 的 mini_agent 进程，一旦发生报错，写
`~/.agent/logs/error.jsonl` 时会出现 `--- Logging error ---`，错误日志丢失。

## 根因
1. 标准库 `RotatingFileHandler` 创建后一直持有文件句柄（`delay=False`）。
2. Windows 上 Python 打开的文件不带 `FILE_SHARE_DELETE`，任一进程持有句柄时，
   其他进程轮转的 `os.rename(error.jsonl, error.jsonl.1)` 抛
   `PermissionError [WinError 32]`。
3. 该异常在 `Handler.emit` 内被 `Handler.handleError` 吞掉，只打印到 stderr，
   `errors.py::log_exception` 的 try/except 与 `_log_internal_failure` 兜底都不会触发。
4. 标准库 `doRollover` 在最后一步失败前已经删除/移动了旧备份，重试会再丢一代备份。

## 方案
新增 `src/mini_agent/log_handlers.py::SafeRotatingFileHandler`：
- 写入时才打开文件，写完立即关闭（持有时间缩短到毫秒级）。
- 轮转：先把当前文件改名为 `<file>.<pid>.rolling`（唯一可能被占用的一步），成功后才
  移动旧备份；改名遇 `PermissionError` 重试 3 次（间隔 200ms），仍失败则跳过轮转继续
  追加，并通过回调转存到 `logging_failures.log`。
- 轮转失败后 30 秒冷却，避免文件持续超限时每条日志都额外阻塞。
- 重试前复查文件大小，避免别的进程刚轮转完又把新的小文件轮转掉。
- 写入彻底失败时，把原始记录一并转存到 `logging_failures.log`。

接入点：`errors.py`、`api/http_log.py`、`skills/generative_capability/capability_debug.py`
三处 `RotatingFileHandler` 统一替换为 `build_safe_rotating_handler()`；文件名与路径不变，
不做按进程拆分（保守默认，后续如仍不足再作为可选项加入）。

## 已知局限
- 轮转被长期占用时，文件会暂时超过 10MB，直到占用方释放。
- 两个进程同时轮转仍有极小的竞态窗口，由“重试前复查大小”缓解，不是严格互斥。
- Linux 上无法真实复现 WinError 32，单元测试用 monkeypatch `os.replace` 模拟；
  建议在 Windows 上用 daemon + CLI 实测一次（可临时调小 `_MAX_BYTES`）。

## 测试
`tests/test_log_handlers.py`（7 项）。
