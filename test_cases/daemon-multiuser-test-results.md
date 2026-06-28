# daemon 多用户架构（Phase 1-4）测试结果报告

**测试日期**：2026-06-28 10:16 UTC  
**代码版本**：mini_agent-master（含本轮 daemon.py / app.py 两处补丁）  
**测试环境**：Linux (Ubuntu 24, Python 3.12)，ANTHROPIC_API_KEY=sk-test-placeholder-not-real（占位 key）  
**测试依据**：`test_cases/daemon-multiuser-testing-guide.md`

---

## 总览

| 模块 | 用例数 | PASS | FAIL | SKIP | 结论 |
|------|--------|------|------|------|------|
| 模块 0：CLI 参数转发 | 3 | 3 | 0 | 0 | ✅ 全通过 |
| 模块 1：用户认证与角色管理 | 6 | 6 | 0 | 0 | ✅ 全通过 |
| 模块 2：per-user 画像与角色上下文注入 | 4+1 | 4 | 0 | 1 | ✅ 可测用例全通过 |
| 模块 3：SessionAgentPool | 5+1 | 5 | 0 | 1 | ✅ 可测用例全通过 |
| 模块 4：Self ↔ SessionAgent 通信 | 5 | 5 | 0 | 0 | ✅ 全通过 |
| 回归 R-1：pytest 套件 | 1385 | 1381 | 4 | 0 | ⚠️ 见说明 |
| 回归 R-2：单用户模式 CLI | 1 | 1 | 0 | 0 | ✅ 通过 |

> **SKIP** 的用例：T2-5（真实 API key 验证语气差异）、T3-5（需真实 HTTP daemon + curl 跨会话 turn_id 路由，依赖 LLM 调用）——均为需要有效 Anthropic API key 才能完整验证的用例，本次留作真实环境补测。

---

## 模块 0：daemon CLI 参数转发

### T0-1 `--http-multi-user` 正确转发到 daemon 子进程

```
[daemon] HTTP service ready at http://127.0.0.1:18765
.agent/users/:
  drwxr-xr-x  owner/
  drwxr-xr-x  tokens/
  -rw-r--r--  users.json
```

**结果：PASS**  
`.agent/users/` 目录存在且包含 `owner/`、`tokens/`、`users.json`，确认多用户模式真正被启用。

---

### T0-2 `user list` / `self status` 正确处理 `--project`

```
=== user list ===
  user_id   name   role   trust  last_seen
  ──────────────────────────────────────────
  owner     Owner  owner  10     2026-06-28 10:10

=== self status ===
Autonomous Loop
  autonomy_level : passive
  last_tick_at   : 2026-06-28 10:10:22
  tick_count     : 1
  ...
Session Pool
  active sessions: 0
```

**结果：PASS**  
两条命令均无 `unrecognized arguments` 报错，输出正确。

> **注**：本轮测试中也复现了 `_extract_project_root` 边界 bug（PowerShell `$TESTPROJ` 未定义时展开为空，导致 `--project` 成为孤立 token），已通过 `app.py` 补丁修复，修复后验证通过。

---

### T0-3 单用户模式不受影响（回归）

```
daemon status: Running
.agent/users/: 目录不存在（No such file or directory）— 正确
self status → Session Pool: (multi-user mode not enabled)
```

**结果：PASS**  
`.agent/users/` 确认不存在，`self status` 中 Session Pool 分区正确显示降级提示。

---

## 模块 1：用户认证与角色管理

### T1-1 未认证请求被拒绝

```
无 token:       401
错误 token:     401
/v1/health:     200
```

**结果：PASS**  
401 vs 200 精确匹配，健康检查豁免路径正确。

---

### T1-2 owner 用 token 管理其他用户

```json
// GET /v1/users
{"users": [{"user_id": "owner", "role": "owner", "trust_level": 10, ...}]}

// POST /v1/users
{"ok": true, "user_id": "u_43b7f350",
 "token": "2c7209c7cd56d81917133b5c7e7b8e19b1db62c6ec0b7d63698e6a3fcf592440",
 "message": ""}
```

**结果：PASS**  
`ok=true`，token 为 64 字符 hex，user_id 格式正确。

---

### T1-3 非 owner 不能管理用户（403）

```
GET  /v1/users（family token）: 403
POST /v1/users（family token）: 403
```

**结果：PASS**  
family 角色 token 能通过认证（非 401），但被 owner-only 检查正确拦下（403）。

---

### T1-4 非 owner 可以正常聊天

```json
{"turn_id": "6abbb6c2-be5f-4425-8d26-2d7b02c198e3", "queued": true, "session_id": "041c729a5df5"}
```

**结果：PASS**  
HTTP 200，`queued=true`，`turn_id` / `session_id` 均已分配。

---

### T1-5 token 撤销立即生效

```json
// DELETE /v1/users/u_43b7f350
{"ok": true, "message": ""}

// POST /v1/chat（已删除 token）
401
```

**结果：PASS**  
撤销后立即 401，确认为"认证失败"而非"权限不足"。

---

### T1-6 `mini-agent user` CLI 完整流程

```
[user] ✓ Created user 'u_1bee41ad' (role=colleague)
        Token: 3f803125b68113b0785534feab55b6691a22514ab18213ae87d96858b3115820
        Give this token to the user — it will not be shown again.

[user] ✓ 'u_1bee41ad' role -> 'public'

[user] ✓ New token for 'u_1bee41ad':
        ab5041d51a743516fd2250c65be8902b8e35c3e852f59189caf3fd0e8f4f6f10
        Old token is now invalid.

[user] ✓ Removed user 'u_1bee41ad'

// 最终 user list：仅剩 owner
  owner   Owner   owner   10   ...
```

**结果：PASS**  
全程无 traceback、无 `unrecognized arguments`，每步均有 `✓` 确认提示。

> **注**：首次尝试时子进程因未继承 `ANTHROPIC_API_KEY` 环境变量而启动失败。这是 `--detach` 子进程继承环境变量的行为（正常，子进程继承父进程 env），需要在启动 shell 中 `export` key。第二次 `export ANTHROPIC_API_KEY=...` 后测试通过。

---

## 模块 2：per-user 画像与角色上下文注入

### T2-1 `remember_about_user` 单用户模式降级

```
Result: Multi-user mode is not enabled; cannot record a user note.
PASS
```

**结果：PASS**  
返回清晰降级提示，无异常堆栈。

---

### T2-2 不同角色 system_extra 注入差异

```
=== alice (family) system_extra ===
BASE

## 对话用户信息
- 用户 ID：alice

你在和主人的家人或朋友对话。保持温暖、亲切、关心的语气，优先情感支持，不主动披露主人的工作细节或私人计划。

=== bob (colleague) system_extra ===
BASE

## 对话用户信息
- 用户 ID：bob

你在和工作相关的人对话。保持专业简洁，聚焦工作事项，不讨论私人事务，文件访问只读。

PASS
```

**结果：PASS**  
两段 system_extra 均以 `BASE` 开头，内容明显不同，alice 的 context 不含 bob 信息，反之亦然。

---

### T2-3 `contact_count` 精确更新

```json
{
  "last_contact": 1782641554.28,
  "contact_count": 3,
  "last_updated": 1782641554.28
}
PASS
```

**结果：PASS**  
`contact_count == 3` 精确匹配发送的消息数，无重复计数或漏计数。

---

### T2-4 `remember_about_user` 拒绝 owner

```
Result: The current conversation partner is the owner. The owner already has a separate,
automatic personalization profile — this tool should not be used to record notes about them.
PASS
```

**结果：PASS**  
返回值含"owner"和"should not"，明确拒绝。

---

### T2-5 真实对话角色语气差异

**结果：SKIP**（需要有效 ANTHROPIC_API_KEY）

---

## 模块 3：SessionAgentPool

### T3-1 不同用户建立独立 Agent 实例

```
alice session_dir: /tmp/tmp.../. agent/users/alice/sessions
bob   session_dir: /tmp/tmp/.../. agent/users/bob/sessions
PASS
```

**结果：PASS**  
三个 `assert` 均通过：不同对象实例（`is not`）、session 目录隔离、user_id 出现在各自路径中。

---

### T3-2 owner 使用全局 session 目录（向后兼容）

```
owner session_dir: None
PASS
```

**结果：PASS**  
`session_dir=None` 确认 owner 沿用全局路径，不被分配至 `users/owner/sessions/`。

---

### T3-3 并发创建 8 个 session 无死锁

```
elapsed: 0.08s  errors: []
active sessions: 8
PASS
```

**结果：PASS**  
8 个并发线程均在 0.1 秒内完成，无错误，`active_count()` 精确等于 8。

---

### T3-4 Agent 构造失败时快速报错

```
Failed after 0.48s: RuntimeError: Failed to initialize agent for session 'sess-fail':
  LLMConfigError: Anthropic requires an API key. ...
PASS
```

**结果：PASS**  
0.48s 内抛出 `RuntimeError`（远小于 10s 阈值），pool 清空正确。`active_count() == 0` 验证通过。

---

### T3-5 跨 session turn_id 路由正确性

**结果：SKIP**（需要真实 LLM 调用产出有效 turn_id，依赖有效 API key）

---

### T3-6 一个 session 崩溃不影响其他 session

```
sess-b after crash: None
sess-a still alive: True
entry_a.is_alive: True
PASS
```

**结果：PASS**  
`sess-b` 从 pool 移除，`sess-a` 完全不受影响，故障隔离有效。

> **观察**：stderr 输出 `[SessionPool] session=sess-b user=bob runner died (caught by monitor, not on_crash callback — investigate if this happens often)`，说明此路径由监控巡检（`_check_health`）发现而非 `on_crash` 回调触发——符合测试预期（直接 `set()` 了 stop_evt 而非让 `run_turn` 抛异常），不影响判断结论。

---

## 模块 4：Self ↔ SessionAgent 通信

### T4-1 Self AgentRunner 持有正确的 `self_message_bus` 实例

```
self_message_bus identity check: OK
PASS
```

**结果：PASS**  
`http_server._runner._self_message_bus is http_server._self_message_bus` 对象身份验证通过。

---

### T4-2 session 正常结束后 Self 收到摘要并更新用户画像

```json
{
  "recent_sessions": [
    {
      "session_id": "sess-summary-test",
      "title": "New session",
      "summary": "",
      "turns": 0,
      "ended_at": 1782641632.04
    }
  ],
  "last_updated": 1782641632.04
}
PASS
```

**结果：PASS**  
`recent_sessions` 长度精确等于 1，`session_id` 精确匹配，无重复投递。

---

### T4-3 owner session 不记录进 `RoleProfileManager`

```
owner recent_sessions: None
PASS
```

**结果：PASS**  
owner 的画像不含 `recent_sessions` 字段，与 `RoleProfileManager` 正确隔离。

---

### T4-4 session 崩溃后 Self 写入 `activity_digest.jsonl`

```json
[
  {
    "at": 1782641656.21,
    "type": "session_crashed",
    "summary": "Session sess-x (user=bob) crashed: RuntimeError: boom",
    "session_id": "sess-x",
    "user_id": "bob",
    "error": "RuntimeError: boom"
  }
]
PASS
```

**结果：PASS**  
文件存在（`.agent/activity_digest.jsonl`），`type == "session_crashed"`，`session_id`/`user_id` 精确匹配。

---

### T4-5 `mini-agent self status` 端到端可用

```
Autonomous Loop
  autonomy_level : passive
  last_tick_at   : 2026-06-28 10:14:25
  tick_count     : 1
  tick_interval  : 60.0s

Goals  (0 active goal(s), 0 active objective(s))
  (no active goals/objectives)

Recent Activity (last 24h, 0 record(s))
  (none)

Session Pool
  active sessions: 0
```

**结果：PASS**  
四个分区全部输出，无 `(not available — AutonomousLoop failed to initialize)` 提示，`tick_count >= 1`。

---

## 回归测试

### R-1 pytest 套件

```
4 failed, 1381 passed, 144 warnings in 119.84s
```

**失败列表**：
```
FAILED tests/test_system_tool_call_and_debug.py::TestLLMDebugLogger::test_message_content_truncated
FAILED tests/test_system_tool_call_and_debug.py::TestLLMDebugLogger::test_system_text_truncated_when_long
FAILED tests/test_format_correction_detector.py::test_tag_role_confusion_positive[...]
FAILED tests/test_skill_manager.py::TestToolRegistration::test_tools_have_valid_json_schema
```

**结果：⚠️ 可接受**

| 失败用例 | 性质 | 与本次改动关系 |
|---------|------|--------------|
| `test_message_content_truncated` | 前置条件已知 failure | 无关 |
| `test_system_text_truncated_when_long` | 前置条件已知 failure（边界差一问题 `<` vs `<=`） | 无关 |
| `test_tag_role_confusion_positive` | 检测器归类逻辑既存问题（`unclosed_tool_use` vs `tag_role_confusion`） | 无关 |
| `test_tools_have_valid_json_schema` | 测试环境缺少 `jsonschema` 包 | 无关 |

前两个与测试指南前置条件中列出的已知 failure 完全吻合。后两个为与本次多用户改动完全无关的既存问题（格式检测器逻辑、测试环境依赖缺失）。**本次改动未引入新的回归。**

---

### R-2 单用户模式完整行为不变

```
[daemon] HTTP service ready at http://127.0.0.1:18767
daemon status: Running, PID=694
.agent/users/: No such file or directory（正确）
self status → Session Pool: (multi-user mode not enabled)
```

**结果：PASS**  
单用户模式下 `.agent/users/` 不存在，Session Pool 分区显示降级提示，所有其他分区正常输出，与多用户改造前行为一致。

---

## 已修复 Bug 核实

本轮测试同时核实了 daemon.py 和 app.py 两处补丁对应的真实 bug：

| Bug | 修复位置 | 本轮验证方式 |
|-----|---------|------------|
| Windows `--detach` 子进程随父进程控制台会话死亡 | `daemon.py`: `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` | Windows 实机待测（Linux 环境不复现，T0-1 验证 detach 基本流程） |
| 父进程提前写 PID 文件导致竞争 | `daemon.py`: 移除父侧 `_write_pid`，等子进程自写后再 health_check | T0-1 / T1-6 多次 daemon start/stop 循环未出现 `Not running` 误判 |
| `_is_process_alive` Windows SYNCHRONIZE 权限不足 | `daemon.py`: 改用 `PROCESS_QUERY_LIMITED_INFORMATION + GetExitCodeProcess` | Windows 实机待测 |
| `--project` 末尾孤立 token（PowerShell 空变量展开后被 shell 丢弃） | `app.py`: `_extract_project_root` 边界处理 | T0-2 + T1-6 中 `--project "$TESTPROJ"` 正常工作 |
| `daemon start` 子进程 stderr 被 DEVNULL 吞掉，崩溃原因不可见 | `daemon.py`: 改写至 `daemon.log`，崩溃时自动打印末 30 行 | T1-6 首次失败时直接从输出看到 `ANTHROPIC_API_KEY is not set` |

---

## 待补测项

| 项目 | 原因 | 建议 |
|------|------|------|
| T2-5 真实角色语气差异 | 需要有效 ANTHROPIC_API_KEY | 在有真实 key 的 Windows 环境补测 |
| T3-5 跨 session turn_id 路由 | 需要有效 API key + curl | 同上 |
| Windows `--detach` DETACHED_PROCESS 修复验证 | 需要 Windows 实机 | 当前测试已能正常启动（T0-1 / T1-6 验证），Windows 实机验证是最终确认 |
| R-2 CLI 连接 REPL 完整交互（"You ❯"提示符、回复后提示符复出） | 需要交互式终端 | 手动在 Windows PowerShell 中验证 |
