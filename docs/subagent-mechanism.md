# SubAgent 机制说明

mini-agent 的并发执行能力依赖于 SubAgent 机制——主 Agent 可以派生出多个独立的 Sub-Agent，在后台并行执行任务。

**补充阅读**：
- [Task 日志实时查看与切换](task-focus-viewing.md) — 方向键实时查看 SubAgent 任务日志
- [Plan 与 Task 指南](plan-and-task-guide.md) — 结构化执行计划

---

## 1. 核心概念

### SubAgent 是什么

SubAgent 是对主 Agent 的轻量包装，在独立线程中运行：

- **独立运行**：每个 SubAgent 在独立线程中执行，不阻塞主线程
- **独立历史**：拥有独立的对话历史、独立的统计信息
- **配置继承**：继承主 Agent 的 LLMConfig（provider/model），但可单独覆盖
- **输出捕获**：stdout 不直接打印，改为写入 TaskRecord.log_lines
- **线程安全**：状态写入通过 TaskRecord 的 lock 保护

### TaskManager 是什么

TaskManager 是并发任务调度器，负责：

- 接收 Task 提交
- 依赖关系解析（`depends_on`）
- 并发上限控制（max_workers）
- SubAgent 生命周期管理
- 任务状态查询和取消

---

## 2. 核心组件

### 2.1 SubAgent (`src/mini_agent/orchestrator/sub_agent.py`)

**关键特性：**

1. **自动重试机制**
   - 对可重试错误（HTTP 5xx、超时）自动重试最多 3 次
   - 每次重试间隔 2 秒，给服务端缓冲时间
   - 4xx 等客户端错误直接抛出，不浪费重试

2. **状态管理**
   - `PENDING` → `RUNNING` → `DONE/FAILED/CANCELLED`
   - 状态切换在 `_run_body()` 中执行，确保真正开始执行时才切换
   - 终态通知只执行一次，避免重复回调

3. **输出捕获**
   - 输出通过 `on_log` 回调写入 `TaskRecord.log_lines`
   - 不替换全局 `sys.stdout`（多线程不安全）
   - 支持实时日志回调 `on_log(task_id, line)`
   - 支持终端状态通知 `on_terminal(task_id, old_status, new_status)`

4. **调试日志**
   - 关键事件写入 `test_result/subagent_debug.jsonl`
   - 包含时间戳、task_id、事件类型、详细信息
   - 错误发生时写入完整 traceback

**生命周期：**

```python
sub = SubAgent(record, base_cfg, on_log=my_callback)
sub.start()       # 非阻塞，启动后台线程
sub.join()        # 阻塞等待完成（可选）
sub.cancel()      # 发送取消信号
```

**状态流转：**

```
PENDING ──start()──→ [等待信号量] ──acquire──→ RUNNING
                                              │
                                       ┌──────┴──────┐
                                       ▼             ▼
                                     DONE        FAILED
                                       │             │
                                       └──────┬──────┘
                                              ▼
                                          CANCELLED
```

### 2.2 TaskManager (`src/mini_agent/orchestrator/task_manager.py`)

**调度机制：**

1. **后台调度循环**
   - 持续轮询（0.3 秒间隔）
   - 检查依赖关系，启动就绪任务
   - 受并发信号量限制（默认 max_workers=4）

2. **依赖解析**
   - `depends_on` 列表全部 `DONE` → 可以启动
   - 任何依赖 `FAILED/CANCELLED` → 当前任务 `CANCELLED`

3. **并发控制**
   - 同时统计 `RUNNING` 和已分配 SubAgent 的 `PENDING` 任务
   - 避免重复调度

**使用方式：**

```python
mgr = TaskManager(base_cfg, max_workers=4)
mgr.start()

tid1 = mgr.submit(Task(prompt="写测试", id="test"))
tid2 = mgr.submit(Task(prompt="写文档", id="docs"))
tid3 = mgr.submit(Task(prompt="运行", id="run", depends_on=[tid1, tid2]))

mgr.wait_all()
results = mgr.list_records()
mgr.stop()
```

---

## 3. 执行流程

### 3.1 任务提交

```
用户输入 → 主 Agent 解析 → create_plan()/spawn_agent()
                            ↓
                    TaskManager.submit()
                            ↓
                    TaskRecord 创建，状态 PENDING
                            ↓
                    返回 task_id
```

### 3.2 任务调度

```
调度循环 (_scheduler_loop)
     ↓
_tick() 周期调用
     ↓
检查 active_count < limit?
     ↓
找出依赖满足的任务
     ↓
按创建时间排序
     ↓
_top N 个启动 (_launch())
     ↓
创建 SubAgent 并 start()
     ↓
SubAgent 等待信号量
```

### 3.3 任务执行

```
SubAgent._run()
     ↓
等待信号量 (concurrency.py)
     ↓
获得信号量
     ↓
_run_body() - 设置 RUNNING 状态
     ↓
_build_agent() - 创建独立 Agent 实例
     ↓
_run_with_capture() - 执行并捕获输出
     ↓
    ├─ 成功 → DONE
    ├─ 5xx/超时 → 重试（最多 3 次）
    └─ 其他错误 → FAILED
     ↓
设置终态，通知回调
```

---

## 4. 重试机制

### 4.1 可重试错误

`_is_retryable_error()` 判断标准：

- ✅ HTTP 5xx（500/502/503/504）- 服务端临时错误
- ✅ Timeout / Timed out - 超时错误
- ❌ HTTP 4xx - 客户端错误（鉴权失败、参数错误）

### 4.2 重试流程

```
尝试 1: agent.run_turn(prompt)
   ↓ 异常：HTTP 500
等待 2.0 秒
   ↓
尝试 2: agent.run_turn(prompt)
   ↓ 异常：HTTP 503
等待 2.0 秒
   ↓
尝试 3: agent.run_turn(prompt)
   ↓ 成功
返回结果
```

或：

```
尝试 1-3 全部失败
   ↓
抛出最后一次异常
   ↓
Task 状态 → FAILED
```

### 4.3 配置参数

```python
class SubAgent:
    _RETRY_MAX_ATTEMPTS = 3    # 最多尝试 3 次
    _RETRY_DELAY = 2.0         # 每次重试间隔 2 秒
```

---

## 5. 降级重试链（Stage 7 / 13.2 + 15.3）

SubAgent 任务失败（`TaskStatus.FAILED`）时，TaskManager 在通知"最终失败"之前，先尝试**降级重试**：按预设的降级策略自动重新提交任务，而不立即宣告失败。

### 5.1 降级阶段

降级按以下顺序尝试（每次 FAILED 触发一次 `_try_demotion()`）：

```
阶段一：profile fallback（13.2）
  → 按 Task.fallback_profiles 列表顺序，依次切换 SubAgent 使用的 agent profile

阶段二：scope demotion（15.3）
  → 在 Task.prompt 末尾追加 Task.demotion_scope 约束文本，缩小任务目标后重试

阶段三：放弃
  → 超出 max_demotion_attempts，任务最终标记为 FAILED
```

### 5.2 Task 配置字段

```python
@dataclass
class Task:
    # ...

    # 13.2 profile 降级链
    fallback_profiles: list[str] = field(default_factory=list)
    # 例：["senior_dev", "minimal"] 表示失败后先换 senior_dev，再换 minimal

    # 15.3 scope 降级策略
    demotion_scope: str = ""
    # 例："仅输出分析报告，不要修改任何文件"

    # 最多尝试几次降级（profile + scope 合计）
    max_demotion_attempts: int = 0  # 0 = 不启用降级
```

### 5.3 使用示例

```python
from mini_agent.orchestrator.task import Task

# 失败后先降级到 senior_dev profile，再降级到 minimal profile，
# 最后缩小目标范围后重试（合计最多 3 次降级）
task = Task(
    prompt="重构 auth 模块，使用 async/await 替换同步 IO",
    fallback_profiles=["senior_dev", "minimal"],
    demotion_scope="仅输出分析报告，不要修改任何文件",
    max_demotion_attempts=3,
)
```

**降级流程**：

```
初始运行（原始 prompt，默认 profile）
   ↓ FAILED
降级 1/3：切换到 senior_dev profile，重试
   ↓ FAILED
降级 2/3：切换到 minimal profile，重试
   ↓ FAILED
降级 3/3：追加 demotion_scope 约束，重试
   ↓ FAILED 或 DONE（成功）
```

### 5.4 TaskRecord 降级追踪字段

```python
@dataclass
class TaskRecord:
    demotion_attempts: int = 0          # 已尝试的降级次数
    active_fallback_profile: str = ""   # 当前使用的 fallback profile（""=原始）
    demoted_scope: bool = False         # 是否已切换到 demotion_scope
```

### 5.5 设计约束

- **task_id 不变**：降级后复用原始 task_id，`depends_on` 引用不失效
- **不产生新 TaskRecord**：原地重置为 PENDING，下次 `_tick()` 自动调度
- **`max_demotion_attempts=0` 时不启用**：默认行为与原有重试机制完全兼容

---

## 6. Debug 日志

日志文件：`test_result/subagent_debug.jsonl`

**记录的事件：**

```json
{"ts": 1234567890.123, "task_id": "abc123", "event": "sub_agent_start", "details": {"task_name": "写测试"}}
{"ts": 1234567890.456, "task_id": "abc123", "event": "queued", "details": {"active": 2, "limit": 4}}
{"ts": 1234567890.789, "task_id": "abc123", "event": "semaphore_acquired", "details": {"label": "abc123 写测试"}}
{"ts": 1234567891.000, "task_id": "abc123", "event": "run_body_start", "details": {"model": "claude-sonnet-4-6", "max_turns": 10}}
{"ts": 1234567891.100, "task_id": "abc123", "event": "agent_built", "details": {"stats": "<AgentStats>"}}
{"ts": 1234567895.200, "task_id": "abc123", "event": "llm_retry", "details": {"attempt": 1, "max_attempts": 3, "error": "HTTP 500", "delay_s": 2.0}}
{"ts": 1234567900.300, "task_id": "abc123", "event": "done", "details": {"input_tokens": 1234, "output_tokens": 567, "tool_calls": 10, "turns": 5}}
```

**日志格式：**

- 每行一个 JSON 对象
- `ts`: Unix 时间戳
- `task_id`: 任务 ID
- `event`: 事件类型
- `details`: 事件详情（可选）

---

## 6. 常见问题

### 6.1 为什么任务卡在 PENDING？

- 可能原因：并发上限已达（active >= limit）
- 解决方法：等待其他任务完成，或增加 `max_workers`

### 6.2 任务重复执行怎么办？

- 已修复：`_tick()` 同时统计 RUNNING 和已分配 SubAgent 的任务
- 如果仍有问题，检查 `concurrency.py` 信号量配置

### 6.3 SubAgent 输出看不到？

- 正常：输出写入 `TaskRecord.log_lines`，不直接打印
- 查看方式：`mgr.get(task_id).log_lines`

### 6.4 为什么任务会失败？

- LLM API 错误（重试后仍失败）
- 超时错误
- 工具执行异常
- 依赖任务失败（级联取消）

### 6.5 如何取消任务？

```python
mgr.cancel(task_id)  # 发送取消信号
# SubAgent 在下一个检查点生效
```

---

## 7. 相关文件

| 文件 | 职责 |
|------|------|
| `src/mini_agent/orchestrator/sub_agent.py` | SubAgent 实现（线程包装、重试、输出捕获） |
| `src/mini_agent/orchestrator/task_manager.py` | TaskManager 实现（调度循环、依赖解析） |
| `src/mini_agent/orchestrator/task.py` | Task 数据模型（Task, TaskRecord, TaskStatus） |
| `src/mini_agent/orchestrator/concurrency.py` | 并发信号量控制 |
| `test_result/subagent_debug.jsonl` | SubAgent 调试日志 |

---

## 8. 与其他系统的关系

### 8.1 与 Plan 系统的关系

- Plan 系统：结构化执行计划，注入 system prompt
- SubAgent：真正的并发执行，纯线程模型
- 结合使用：Plan 定义任务依赖，SubAgent 执行任务

### 8.2 与 Task 工具的关系

- `spawn_agent` 工具：从 LLM 侧发起任务请求
- TaskManager.submit()：实际的任务调度入口
- SubAgent：执行具体的任务内容

### 8.3 与自我演化系统的关系（Stage 3.3 / Phase E）

主 agent 的部分运行期状态会被 SubAgent 继承/共享，详见
[自我演化 SubAgent 信息继承（Stage 3.3）](self-evolution-stage3-3-guide.md)：

- **Skill 继承**：spawn 时通过 `Task.active_skills` 字段把主 agent 当前激活的
  skill 列表透传给 SubAgent，SubAgent 启动后按名称自动激活同一批 skill。
- **`ToolResultCache` 跨 SubAgent 共享**：`tool_cache_enabled` 开启时，
  `TaskManager` 持有一个加锁的全局缓存实例，避免并发 SubAgent 重复读取
  同一份文件。
- **lesson 回流**：SubAgent 与主 agent 共享同一个 `memory.jsonl` 磁盘路径，
  SubAgent 进入终态时触发主 agent 的 memory backend `reload()`，使本 session
  后续检索能看到 SubAgent 期间产生的新 lesson。

---

*最后更新：2026-06（反映 SubAgent 重试机制、状态管理修复、调试日志新增、Stage 3.3 信息继承机制）*
