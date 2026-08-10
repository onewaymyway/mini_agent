# 全局错误日志 tool_executor 落盘开关

- **版本**: v1
- **触发背景**: 用户反馈 `~/.agent/logs/error.jsonl` 里绝大部分记录都是
  `where: "mini_agent.tool_executor"`（多数是工具执行过程中的预期内降级
  路径，如历史消息里 tool_result 的 JSON 解析失败，被 `log_exception`
  兜底记录），淹没了其它真正需要关注的错误。`errors.py::error_log_stats()`
  已经有一个 `exclude_tool_executor` 参数，但那只影响**看板统计展示时的
  过滤**，不影响文件本身写入了什么——用户想要的是"从源头控制要不要写"，
  并且明确要求改 `errors.py` 内部逻辑，不去改 `tool_executor.py` 里几十
  处分散的 `log_exception(...)` 调用点。

## 设计

**配置项**：`AppConfig.save_tool_executor_error_logs: bool`（默认
`True`，不改变现有行为），对应 `agent_config.json` 顶层字段
`"save_tool_executor_error_logs": false`。跟已有的
`bash_stream_output_enabled` 等布尔开关走同一套 `_fb(...)` 读取模式
（`config/loader.py`）。

**收口位置**：`errors.py` 新增模块级开关
`_SAVE_TOOL_EXECUTOR_ERROR_LOGS`（默认 `True`）+
`configure_tool_executor_log_saving(enabled)` 设置函数 +
`_is_tool_executor_where(where)` 判断函数（`where` 前缀匹配
`"mini_agent.tool_executor"`，与 `error_log_stats()` 里现有的判断逻辑
保持一致的前缀写法）。

- `log_exception()`：解析出 `resolved_where` 后立刻检查——如果开关关闭
  且命中 tool_executor 前缀，直接跳过（不构造 record、不格式化
  traceback，省掉这条记录唯一有意义的开销），`reraise=True` 时仍然正常
  重新抛出原异常（日志是否落盘跟要不要继续抛异常是两件独立的事）。
- `_RootErrorRouteHandler.emit()`：同样加一次检查（`record.name` 而不是
  `where` 字段），为将来"某处改用 `logging.getLogger("mini_agent.
  tool_executor")` 而不是直接调用 `log_exception()`"的场景保持行为一致，
  当前代码库里 `tool_executor.py` 实际都是走 `log_exception()` 直接调用，
  这一处是面向未来的防御性对齐，不是当前必须触发的路径。

**配置生效路径**：`config/loader.py::load_config()` 构造完 `AppConfig`
后，调用一次 `errors.configure_tool_executor_log_saving(cfg.
save_tool_executor_error_logs)` 完成桥接。选择在 `load_config()` 里
桥接而不是在 `errors.py` 内部反向读取 `AppConfig`，是为了避免
`errors.py`（几乎被项目里所有模块底层依赖）反向 import 配置模块引入
循环依赖风险——`errors.py` 只暴露一个纯函数式的 setter，被谁调用、什么
时候调用，由拥有完整 `AppConfig` 的调用方决定。

**已知局限（本次不处理的取舍）**：`_SAVE_TOOL_EXECUTOR_ERROR_LOGS` 是
进程级单一开关。daemon 多用户架构下，如果不同 session/project 分别
`load_config()` 出不同的 `save_tool_executor_error_logs` 取值，最后一次
调用会覆盖前面的（"后来者生效"），不是按 session 隔离生效。这在单用户
CLI/单进程场景下没有影响（当前主要使用场景），多用户 daemon 场景如果
未来需要"按项目/用户分别控制"，需要把开关从模块级全局变量升级为
按某个 key（如 project_root 或 user_id）区分的字典，属于更大的改动，
本次不做。

## 验证方式

- 单元测试：
  1. `configure_tool_executor_log_saving(False)` 后，`where="mini_agent.
     tool_executor"` 的 `log_exception()` 调用不写入日志文件；
     `where="mini_agent.other_module"` 的调用仍正常写入。
  2. `configure_tool_executor_log_saving(True)`（默认值）恢复原有行为，
     两类 `where` 都正常写入。
  3. `reraise=True` 时，无论开关是否关闭、是否命中 tool_executor 前缀，
     原异常都会被重新抛出。
  4. `load_config()` 读取 `agent_config.json` 里的
     `save_tool_executor_error_logs` 字段后，`errors.py` 里的模块开关
     被正确同步。
- 手动验证：在 `agent_config.json` 里加一行
  `"save_tool_executor_error_logs": false`，跑一段会触发
  `tool_executor.py` 里那类 JSON 解析异常的会话，确认
  `~/.agent/logs/error.jsonl` 不再新增 `where` 为 tool_executor 的记录，
  其它模块的错误记录不受影响。

## 实施状态

- [x] `config/models.py` 新增 `AppConfig.save_tool_executor_error_logs`
- [x] `config/loader.py` 读取该字段并桥接到
      `errors.configure_tool_executor_log_saving()`
- [x] `errors.py` 新增模块开关 + `log_exception()` / 
      `_RootErrorRouteHandler.emit()` 接入判断逻辑
- [x] 单元测试
- [x] `next_doc/growth_advisor_implementation_record.md` 或类似位置
      追加实施记录（见文末"备注"——本次改动跟成长顾问无关，记录写在
      本文档自身的"实施状态"里，不追加到那份文档）

## 测试结果

- 新增 `tests/test_errors_tool_executor_toggle.py`（8 项）全部通过：
  开关默认开启时正常写入、关闭后 tool_executor 记录被跳过、非
  tool_executor 记录不受影响、`reraise=True` 时开关关闭也不影响异常
  重新抛出、前缀匹配覆盖细分 `where`、`load_config()` 正确桥接开关到
  `errors.py`（打开/关闭两个方向都验证了）。
- 跑了 `tests/test_config_catalog_list_seed_merge.py`（8 项）确认
  `config/loader.py` 的既有改动没有引入回归，全部通过。
- 未跑 `tests/test_kanban_config_routes.py`（本地环境缺
  `fastapi` 依赖，跟本次改动无关的环境限制，非代码问题）。

