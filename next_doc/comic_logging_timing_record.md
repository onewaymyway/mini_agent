# world_forge · 运行日志时间戳、心跳与图像超时实施记录

> 问题：日志只显示相对秒数，没有墙上时间；图像调用「生成中」之后长时间没有后续，无法判断是在等待还是已卡住。
> 测试：`python -m pytest -q tests` 全部通过（240 项，本次新增 6 项）。

## 1. 诊断

1. `format_entry` 只显示「从运行开始的秒数」，记录里其实已有时间字段，但没有显示。
2. 图像调用（`provider.generate`）是同步阻塞调用，期间没有任何输出，也没有超时。模型或网络卡住时，日志停在「生成中……」。

## 2. 改动

- **时间戳**：`runlog.clock_now()` 生成 `HH:MM:SS.mmm`；每条记录保存 `clock` 字段；`format_entry` 行首显示墙上时间，随后是运行秒数。旧记录没有 `clock` 字段时，回退为 ISO 时间的时分秒。
- **心跳与超时**：`runlog.call_with_heartbeat(fn, log, label, timeout, beat)` 在守护线程中执行阻塞调用，每 `HEARTBEAT_SECONDS`（15 秒）记录一次心跳；超过 `timeout` 则放弃等待，返回超时标记。
- **统一图像入口** `engine._gen_image`：所有图像调用（立绘、角色表、场景图、漫画格、候选）都经过它，记录提交时间、完成/失败时间与用时；超时记为失败并继续。超时由 `WORLD_FORGE_IMAGE_TIMEOUT` 控制（默认 180 秒，最少 10 秒）。
- **文本调用**：`LoggedLLM.ask` 同样使用心跳，记录提交与返回的时钟时间。

## 3. 改动文件

路径均相对于 `external_projects/world_forge/`：`world_forge/runlog.py`、`world_forge/engine.py`、`tests/test_logging_timing.py`（新增）、`docs/README.md`、`PROJECT.md`。

## 4. 验证

- 每行日志以墙上时钟时间开头（格式测试）。
- 慢调用期间产生心跳，并最终返回结果。
- 超时后返回超时标记，并在日志中说明已放弃等待。
- 异常原样返回，不被吞掉。
- 卡住的图像调用记为失败，不阻塞整个渲染。

## 5. 说明与限制

- 超时后，后台线程仍可能继续运行直到底层请求结束，但不再影响流程。若底层请求长期占用资源，需要在 provider 层设置网络超时（此处未改动 provider 的网络设置）。
- 心跳只说明程序仍在等待，不能说明模型是否真的在处理；模型端的进度无法从外部得知。
- 默认超时 180 秒是估计值，若真实图像模型生成一张图需要更长时间，请调大 `WORLD_FORGE_IMAGE_TIMEOUT`。
