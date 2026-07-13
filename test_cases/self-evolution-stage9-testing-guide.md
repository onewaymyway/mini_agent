# Stage 9 功能测试手册（Phase H：自主运行时）

> 本文档面向 Stage 9 的实现者和 QA，给出每个新功能的**具体测试步骤、前置条件、
> 预期结果和常见失效模式**。按功能模块组织，大体遵循 `next_doc/self_evolution_stage9_plan.md`
> 的依赖顺序——前序模块的测试必须通过后，再测试后续模块。
>
> 单元测试文件建议对应关系见文末附录。

---

## 前置条件（所有测试共用）

1. Stage 0-8 的 `tests/` 全绿（`pytest tests/` 无失败）
2. 已有一个包含若干条 `traces.jsonl`、`activity_log.jsonl`、`memory.jsonl` 记录的项目目录（可用现有项目，或用 Stage 4-8 测试套件的 fixtures 生成）
3. 设置测试用环境变量，避免使用真实 API key 消耗配额：
   ```bash
   export MINI_AGENT_TEST_MODE=1   # 如果代码支持 mock LLM
   export ANTHROPIC_API_KEY=sk-test-placeholder
   ```
4. 工作目录始终在项目根（含 `.agent/` 子目录）

---

## 模块 1：守护进程管理（`cli/daemon.py`）

> 对应 `next_doc/self_evolution_stage9_plan.md` 第三节。
> **本模块是所有后续模块的物理前提，必须最先通过。**

### T1-1 前台启动与停止

**步骤**

```bash
mini-agent daemon start      # 不带 --detach，前台运行
```

在另一个终端：

```bash
mini-agent daemon status
```

回到第一个终端，按 `Ctrl-C`。

**预期结果**
- `daemon start` 启动后终端阻塞（不返回 shell 提示符）
- `daemon status` 输出含 PID、HTTP 端口、`autonomy_level: passive`、上次 tick 时间（初始为 N/A 或启动时间）
- `Ctrl-C` 后进程退出，`.agent/daemon.pid` 和 `.agent/daemon_info.json` **被自动清理**（不残留）

**常见失效**
- `.agent/daemon.pid` 残留：说明 signal handler 未正确注册，检查 `daemon.py` 的 `atexit`/`signal.signal` 注册逻辑
- `daemon status` 报"未运行"但进程实际存在：PID 文件路径与进程检测路径不一致

---

### T1-2 后台启动（`--detach`）

**步骤**

```bash
mini-agent daemon start --detach
echo "退出码: $?"
mini-agent daemon status
sleep 2
mini-agent daemon status   # 再次确认仍存活
mini-agent daemon stop
mini-agent daemon status   # 确认已停止
```

**预期结果**
- `--detach` 立即返回 shell（退出码 0）
- 首次 `daemon status`：`running, PID=XXXXX, port=8765, autonomy_level=passive`
- `daemon stop` 后：`daemon status` 报"未运行"，`.agent/daemon.pid` 已清理

---

### T1-3 异常崩溃后残留 PID 文件的清理

**步骤**

```bash
# 手动写入一个不存在的 PID
echo 99999 > .agent/daemon.pid
mini-agent daemon status
mini-agent daemon start --detach   # 应能正常启动，不卡住
mini-agent daemon stop
```

**预期结果**
- `daemon status` 检测到 PID 99999 进程不存在，输出"daemon 未运行（残留 PID 文件已清理）"
- `daemon start --detach` 成功启动（不因残留文件报错）

---

### T1-4 CLI 连接模式（daemon 已运行时）

**步骤**

```bash
# 终端 A：启动 daemon
mini-agent daemon start --detach

# 终端 B：启动普通 CLI（不带 daemon 子命令）
mini-agent
```

**预期结果**
- 终端 B 输出"Connected to running daemon (PID=XXXXX, port=8765)"
- 在终端 B 输入一条消息，响应**正常返回**（由 daemon 进程内的 AgentRunner 处理）
- 在终端 B 的 REPL 中输入 `exit`，终端 B 退出但终端 A 的 daemon **继续运行**（`daemon status` 仍报存活）

---

### T1-5 `--no-daemon` 回退兼容

**步骤**

```bash
# daemon 已在运行
mini-agent daemon start --detach

# 使用 --no-daemon 启动
mini-agent --no-daemon
```

**预期结果**
- `--no-daemon` 模式下，CLI **不**连接已运行的 daemon，而是在进程内直接创建 Agent 实例（与 Stage 0-8 行为一致）
- 现有单元测试套件（`tests/test_session.py` 等）在 `--no-daemon` 路径下**全部通过**（回归验证）

---

### T1-6 两个 CLI 同时连接同一个 daemon

**步骤**

```bash
mini-agent daemon start --detach

# 终端 A
mini-agent
# 终端 B（同时开启）
mini-agent
```

在终端 A 发送消息，观察终端 B 是否能看到该消息的响应（或只有终端 A 能看到）。

**预期结果**
- 两个连接均正常工作，消息在 daemon 内部排队（FIFO），不互相干扰
- 终端 B 看到的输出应包含"来自其他客户端"的标注（复用 `[web]` 区分标注），不混淆

---

## 模块 2：`autonomy_level` 字段与档位切换

> 对应 `next_doc/self_evolution_stage9_plan.md` 第五节。

### T2-1 新建项目时字段默认值

**步骤**

```bash
rm -f ~/.agent/self_profile.json   # 或使用新的测试目录
mini-agent --no-daemon
```

在 REPL 中：

```
/agent autonomy
```

退出后：

```bash
cat ~/.agent/self_profile.json | python3 -m json.tool | grep autonomy_level
```

**预期结果**
- `/agent autonomy` 输出 `当前自主等级：passive`
- `self_profile.json` 中 `operating_state.autonomy_level` 值为 `"passive"`

---

### T2-2 旧版 `self_profile.json` 向后兼容

**步骤**

```bash
# 构造一个没有 autonomy_level 字段的旧版文件
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path(".agent/self_profile.json")
data = json.loads(p.read_text()) if p.exists() else {}
data.setdefault("operating_state", {})
data["operating_state"].pop("autonomy_level", None)  # 删除字段
p.write_text(json.dumps(data))
print("已删除 autonomy_level 字段")
EOF

mini-agent --no-daemon
```

在 REPL 中：

```
/agent autonomy
exit
```

**预期结果**
- 启动**不报错、不崩溃**
- `/agent autonomy` 输出 `当前自主等级：passive`（默认值兜底）

---

### T2-3 切换档位的交互确认

**步骤**

在 REPL 中：

```
/agent autonomy maintenance
```

**预期结果**
- 输出明确的二次确认提示，包含"maintenance 将启用后台周期性任务自主触发"字样
- 输入 `y` 确认后，`self_profile.json` 中 `autonomy_level` 变为 `"maintenance"`
- 输入 `n` 取消后，字段保持原值

---

### T2-4 `/agent autonomy` 不能作为工具被 agent 调用

**步骤**

```bash
# 在所有 tools/ 目录下搜索 autonomy 相关工具注册
grep -r "autonomy" src/mini_agent/tools/ --include="*.py"
grep -r "\"agent_autonomy\"\|'agent_autonomy'" src/mini_agent/ --include="*.py"
```

**预期结果**
- `tools/` 目录下**不存在**任何与修改 `autonomy_level` 相关的工具定义
- agent 在对话中**无法**通过工具调用修改该字段（只能通过用户手动输入 `/agent autonomy` 命令）

---

### T2-5 紧急降级命令（`--emergency`）

**步骤**

```
/agent autonomy maintenance    # 先切换到 maintenance
/agent autonomy passive --emergency
```

**预期结果**
- `--emergency` 路径**不显示**二次确认提示，**立即**将字段写回 `"passive"`
- 输出 `[emergency] 已立即降级到 passive 档位`

---

## 模块 3：Goal Backlog（`perception/goal_backlog.py`）

> 对应 `next_doc/self_evolution_stage9_plan.md` 第六节。

### T3-1 创建 Goal 和 Objective

**步骤**

在 REPL 中：

```
/agent goals add "提升 bash 工具安全性" --priority 10 --tag security
/agent goals
```

**预期结果**
- `.agent/goals.json` 新增一条 `level=goal` 的节点
- `/agent goals` 列表中显示该 Goal，状态为 `active`

---

### T3-2 关联 Objective 到已有 WorkThread

**步骤**

先确认 `.agent/work_index.json` 中存在至少一个 WorkThread（`wt_xxx`），记下其 ID。

```
/agent goals obj add "观察拦截命中率两周" --goal <goal_id> --thread <wt_id>
/agent goals
```

**预期结果**
- 新增 `level=objective` 节点，`work_thread_ref` 字段与 `wt_id` 一致
- `/agent goals` 显示树形结构：Goal → Objective

---

### T3-3 `has_actionable_work()` 返回值正确

**步骤**

```python
# 在测试脚本中
import sys; sys.path.insert(0, "src")
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths

paths = AgentPaths(".")
gb = GoalBacklog(paths)
gb.load()

# 有 active objective 时
print("has_actionable_work:", gb.has_actionable_work())  # 期望 True

# 将所有 objective 标记为 completed 后
for node in gb._nodes.values():
    if node.level == "objective":
        node.status = "completed"
print("has_actionable_work:", gb.has_actionable_work())  # 期望 False
```

**预期结果**
- 有 active Objective 时返回 `True`，全部非 active 时返回 `False`

---

### T3-4 原子写入（断电模拟）

**步骤**

```python
import os, threading, time
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths

paths = AgentPaths(".")
gb = GoalBacklog(paths)
gb.load()

# 记录写入前文件大小
before_size = os.path.getsize(".agent/goals.json")

# 并发写入（模拟竞争）
def save():
    for _ in range(20):
        gb.save()
        time.sleep(0.01)

threads = [threading.Thread(target=save) for _ in range(3)]
for t in threads: t.start()
for t in threads: t.join()

# 验证文件仍为合法 JSON
import json
json.loads(open(".agent/goals.json").read())
print("JSON 结构完整")
```

**预期结果**
- 无异常，文件始终为合法 JSON（原子写入保护）

---

## 模块 4：AutonomousLoop 调度器（`evolution/autonomous_loop.py`）

> 对应 `next_doc/self_evolution_stage9_plan.md` 第七节。

### T4-1 passive 档位不读取 GoalBacklog

**步骤**

```python
from unittest.mock import MagicMock, patch
from mini_agent.evolution.autonomous_loop import AutonomousLoop

goal_backlog = MagicMock()
loop = AutonomousLoop(
    goal_backlog=goal_backlog,
    input_queue=MagicMock(),
    paths=MagicMock(),
    cfg=MagicMock(),
)

# 强制 passive 档位
with patch.object(loop, "_get_autonomy_level", return_value="passive"):
    loop.tick()

# 验证 GoalBacklog 的任何方法都未被调用
goal_backlog.has_actionable_work.assert_not_called()
goal_backlog.next_task_description.assert_not_called()
print("passive 边界验证通过")
```

**预期结果**
- `goal_backlog` 的任何方法**均未被调用**

---

### T4-2 passive 档位触发 巩固循环（时间门控）

**步骤**

```python
from unittest.mock import MagicMock, patch
from mini_agent.evolution.autonomous_loop import AutonomousLoop

loop = AutonomousLoop(
    goal_backlog=MagicMock(),
    input_queue=MagicMock(),
    paths=MagicMock(),
    cfg=MagicMock(),
)

with patch.object(loop, "_get_autonomy_level", return_value="passive"), \
     patch("mini_agent.evolution.autonomous_loop.should_run_consolidation", return_value=True) as mock_should, \
     patch("mini_agent.evolution.autonomous_loop.run_consolidation") as mock_run:
    loop.tick()
    mock_run.assert_called_once()
    print("巩固循环 被触发，验证通过")
```

**预期结果**
- `run_consolidation` 被调用一次

---

### T4-3 maintenance 档位在有 GoalBacklog 工作时提交 Task

**步骤**

```python
from unittest.mock import MagicMock, patch
from mini_agent.evolution.autonomous_loop import AutonomousLoop

goal_backlog = MagicMock()
goal_backlog.has_actionable_work.return_value = True
goal_backlog.next_task_description.return_value = "观察 bash-safety skill 命中率"

input_queue = MagicMock()
loop = AutonomousLoop(
    goal_backlog=goal_backlog,
    input_queue=input_queue,
    paths=MagicMock(),
    cfg=MagicMock(),
)

arbiter = MagicMock()
arbiter.can_run_autonomous.return_value = True
loop._arbiter = arbiter

with patch.object(loop, "_get_autonomy_level", return_value="maintenance"), \
     patch("mini_agent.evolution.autonomous_loop.should_run_consolidation", return_value=False):
    loop.tick()

# 验证 enqueue 被调用且 initiator="autonomous"
calls = input_queue.enqueue.call_args_list
assert any(c.kwargs.get("initiator") == "autonomous" for c in calls), \
    "enqueue 未携带 initiator='autonomous'"
print("maintenance 提交任务验证通过")
```

**预期结果**
- `input_queue.enqueue` 被调用，且 `initiator="autonomous"`

---

### T4-4 daemon 无客户端连接时自主触发（端到端）

> 此测试需要真实启动 daemon，是本模块最关键的验证项。

**步骤**

```bash
# 调小 巩固循环 间隔用于测试（通过临时配置）
cat > /tmp/test_agent_config.json <<'EOF'
{
  "consolidation_interval_hours": 0.01
}
EOF

mini-agent daemon start --detach --config /tmp/test_agent_config.json

# 等待约 60 秒（不开启任何 CLI/Web 连接）
sleep 70

# 检查 巩固循环 是否被触发
cat .agent/consolidation_rhythm.json | python3 -m json.tool | grep last_run_at
```

**预期结果**
- `last_run_at` 字段更新为当前时间附近（验证 daemon 在无客户端时仍自主触发）

---

### T4-5 `should_tick()` 间隔控制

**步骤**

```python
import time
from unittest.mock import MagicMock
from mini_agent.evolution.autonomous_loop import AutonomousLoop

loop = AutonomousLoop(
    goal_backlog=MagicMock(),
    input_queue=MagicMock(),
    paths=MagicMock(),
    cfg=MagicMock(),
    tick_interval_seconds=2,
)

assert loop.should_tick() == True   # 首次应该可以 tick
loop._last_tick_at = time.time()
assert loop.should_tick() == False  # 刚 tick 过，不应再 tick
time.sleep(2.1)
assert loop.should_tick() == True   # 间隔过后，可以再 tick
print("tick 间隔控制验证通过")
```

---

## 模块 5：资源仲裁（`evolution/resource_arbiter.py`）

> 对应 `next_doc/self_evolution_stage9_plan.md` 第八节。

### T5-1 预算超限时阻止自主任务

**步骤**

```python
from mini_agent.evolution.resource_arbiter import ResourceArbiter
from mini_agent.storage.paths import AgentPaths
from unittest.mock import patch

arbiter = ResourceArbiter(paths=AgentPaths("."), cfg=None)

# 模拟预算已耗尽
with patch.object(arbiter, "_get_budget", return_value={"daily_token_budget": 1000, "used_today": 1001}):
    result = arbiter.can_run_autonomous()
    assert result == False, "预算超限应返回 False"
    print("预算超限阻止验证通过")

# 预算充足
with patch.object(arbiter, "_get_budget", return_value={"daily_token_budget": 1000, "used_today": 500}):
    result = arbiter.can_run_autonomous()
    assert result == True, "预算充足应返回 True"
    print("预算充足放行验证通过")
```

---

### T5-2 `daily_token_budget <= 0` 时不限制

**步骤**

```python
arbiter = ResourceArbiter(paths=AgentPaths("."), cfg=None)
with patch.object(arbiter, "_get_budget", return_value={"daily_token_budget": 0, "used_today": 999999}):
    result = arbiter.can_run_autonomous()
    assert result == True, "budget=0 时应不限制"
    print("无限制模式验证通过")
```

---

### T5-3 路径冲突检测（tracing 已开启）

**步骤**

```python
from mini_agent.evolution.resource_arbiter import ResourceArbiter
from mini_agent.storage.paths import AgentPaths
from unittest.mock import patch

arbiter = ResourceArbiter(paths=AgentPaths("."), cfg=None)

# 模拟用户最近触碰了 src/mini_agent/agent.py
mock_touched = {"src/mini_agent/agent.py", "src/mini_agent/config/models.py"}

with patch.object(arbiter, "recent_user_touched_paths", return_value=mock_touched):
    # 自主任务计划操作相同文件 → 冲突
    conflict = arbiter.check_path_conflict({"src/mini_agent/agent.py"})
    assert conflict == True, "路径重叠应检测到冲突"

    # 自主任务计划操作不同文件 → 无冲突
    no_conflict = arbiter.check_path_conflict({"docs/README.md"})
    assert no_conflict == False, "无重叠路径不应产生冲突"
    print("路径冲突检测验证通过")
```

---

### T5-4 tracing 未开启时保守降级

**步骤**

```python
arbiter = ResourceArbiter(paths=AgentPaths("."), cfg=None)

# 模拟 traces.jsonl 不存在
with patch.object(arbiter, "_traces_available", return_value=False):
    conflict = arbiter.check_path_conflict({"any/file.py"})
    assert conflict == True, "tracing 不可用时应保守视为冲突"
    print("tracing 不可用降级验证通过")
```

---

### T5-5 `PAUSED` 状态机（用户活动抢占）

**步骤**

```python
from mini_agent.orchestrator.task import TaskStatus

# 验证 PAUSED 值存在
assert hasattr(TaskStatus, "PAUSED"), "TaskStatus 缺少 PAUSED 值"

# 验证 PAUSED 不是终态（completed/failed/cancelled 是终态）
assert TaskStatus.PAUSED not in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}, \
    "PAUSED 不应是终态"
print("PAUSED 状态机验证通过")
```

---

### T5-6 探索预算与目标预算独立核算

**步骤**

```python
arbiter = ResourceArbiter(paths=AgentPaths("."), cfg=None)

mock_budget = {
    "daily_token_budget": 1000,
    "exploration_budget_ratio": 0.1,   # 100 tokens 探索预算
    "used_today_goals": 950,            # 目标预算几乎耗尽
    "used_today_exploration": 50,       # 探索预算还有 50 tokens
}

with patch.object(arbiter, "_get_budget", return_value=mock_budget):
    # 目标预算不足，禁止自主 goal task
    assert arbiter.can_run_autonomous() == False

    # 探索预算仍有余量，允许探索任务
    assert arbiter.can_run_exploration() == True

    print("探索预算独立核算验证通过")
```

---

## 模块 6：`initiator` 字段与 tier 上浮（`evolution/state_repo.py`）

> 对应 `next_doc/self_evolution_stage9_plan.md` 第九节。

### T6-1 向后兼容：现有调用点不传 `initiator` 时行为不变

**步骤**

```python
# 抽查几个现有调用点的签名，确认不需要改动
import ast, pathlib

target_files = [
    "src/mini_agent/tools/skill_manager.py",
    "src/mini_agent/evolution/consolidation.py",
]

for f in target_files:
    src = pathlib.Path(f).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            if func == "apply":
                # 检查是否有 initiator 关键字参数
                kw_names = [k.arg for k in node.keywords]
                if "initiator" not in kw_names:
                    print(f"  {f}:{node.lineno} → apply() 无 initiator 参数（使用默认值 'user'）✓")
```

**预期结果**
- 现有调用点均无 `initiator` 参数（使用默认值 `"user"`），行为不变

---

### T6-2 T0→T1 自动上浮规则

**步骤**

```python
from mini_agent.evolution.state_repo import StateRepo
from mini_agent.storage.paths import AgentPaths

repo = StateRepo(paths=AgentPaths("."))

# user 发起的 T0 不上浮
effective, forced = repo.resolve_tier(paths=["docs/README.md"], tier="T0", initiator="user")
assert effective == "T0", f"user 发起 T0 不应上浮，实际: {effective}"

# autonomous 发起的 T0 上浮为 T1
effective, forced = repo.resolve_tier(paths=["docs/README.md"], tier="T0", initiator="autonomous")
assert effective == "T1", f"autonomous 发起 T0 应上浮为 T1，实际: {effective}"
assert "initiator_upgrade" in (forced or ""), "forced 原因应包含 initiator_upgrade"

# autonomous 发起的 T1 不再上浮
effective, forced = repo.resolve_tier(paths=["docs/README.md"], tier="T1", initiator="autonomous")
assert effective == "T1", f"autonomous 发起 T1 不应进一步上浮"

# T3 不受影响
effective, forced = repo.resolve_tier(paths=["CLAUDE.md"], tier="T0", initiator="autonomous")
assert effective == "T3", "受保护路径上浮优先级高于 initiator 规则"

print("tier 上浮规则验证通过")
```

---

### T6-3 `/agent autonomy` 命令不调用 `StateRepo.apply()`

**步骤**

```python
from unittest.mock import MagicMock, patch
import mini_agent.cli.commands  # 引入命令模块

# 在 /agent autonomy 命令的处理路径中，注入 mock StateRepo
mock_repo = MagicMock()

with patch("mini_agent.evolution.state_repo.StateRepo", return_value=mock_repo):
    # 模拟执行 /agent autonomy maintenance 并确认
    from mini_agent.cli.repl import handle_agent_cmd  # 假设该函数存在
    handle_agent_cmd(["autonomy", "maintenance"], agent=MagicMock(), confirmed=True)

mock_repo.apply.assert_not_called()
print("/agent autonomy 不走 StateRepo.apply() 验证通过")
```

---

## 模块 7：activity_digest.jsonl 与晨报

> 对应 `next_doc/self_evolution_stage9_plan.md` 第八节 §8.2。

### T7-1 自主任务完成后追加摘要记录

**步骤**

```python
from mini_agent.evolution.resource_arbiter import ResourceArbiter
from mini_agent.storage.paths import AgentPaths
import json, pathlib

paths = AgentPaths(".")
arbiter = ResourceArbiter(paths=paths, cfg=None)

# 手动触发一条摘要记录
arbiter.record_digest({
    "type": "task_completed",
    "objective_id": "obj_test001",
    "summary": "测试摘要记录",
    "initiator": "autonomous",
})

# 验证文件存在且为合法 jsonl
digest_path = pathlib.Path(".agent/activity_digest.jsonl")
assert digest_path.exists()
lines = [json.loads(l) for l in digest_path.read_text().strip().split("\n") if l]
last = lines[-1]
assert last["type"] == "task_completed"
assert last["initiator"] == "autonomous"
assert "at" in last
print("摘要记录写入验证通过")
```

---

### T7-2 `/digest` 命令分组展示

**步骤**

手动向 `.agent/activity_digest.jsonl` 写入三种类型的记录：

```bash
python3 - <<'EOF'
import json, time, pathlib

records = [
    {"at": time.time() - 3600, "type": "task_completed",   "summary": "完成 bash-safety 观察", "initiator": "autonomous"},
    {"at": time.time() - 1800, "type": "consolidation_completed", "prune_count": 1, "capability_count": 10},
    {"at": time.time() - 900,  "type": "soft_goal_created", "goal_id": "goal_test", "title": "测试软目标"},
]

p = pathlib.Path(".agent/activity_digest.jsonl")
with p.open("a") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print("已写入测试摘要记录")
EOF
```

在 REPL 中：

```
/digest
```

**预期结果**
- 三种类型分组展示，而不是混在一起
- `soft_goal_created` 类型单独列出（区别于普通任务完成）
- `consolidation_completed` 类型单独列出（巩固循环 运维活动）

---

### T7-3 CLI 连接时自动展示上次离线后的摘要

**步骤**

```bash
# 确保 activity_digest.jsonl 中有记录（T7-2 已写入）
# 断开连接后重新连接
mini-agent   # 连接模式
```

**预期结果**
- 连接后第一屏输出"自上次交互以来 daemon 的活动摘要"
- 摘要内容与 `/digest` 命令内容一致

---

## 模块 8：探索实验机制（`perception/exploration_sandbox.py`）

> 对应 `next_doc/self_evolution_stage9_plan.md` 第十一节。

### T8-1 Experiment 预注册冻结（追加写不可篡改）

**步骤**

```python
from mini_agent.perception.exploration_sandbox import ExplorationSandbox, Experiment
from mini_agent.storage.paths import AgentPaths
import json, pathlib, time

paths = AgentPaths(".")
sandbox = ExplorationSandbox(paths=paths, cfg=None, arbiter=None)

# 创建并冻结一个实验
exp = Experiment(
    id="exp_test_001",
    hypothesis="bash-safety skill 能减少 50% 的误执行",
    motivation="capability_map 置信度低",
    method="对比 100 次 session 的拦截率",
    status="designed",
    created_at=time.time(),
)
sandbox.register(exp)   # frozen_at 在此设置

# 读取 experiments.jsonl，找到该 id 的第一条记录（冻结记录）
exp_path = pathlib.Path(".agent/experiments.jsonl")
records = [json.loads(l) for l in exp_path.read_text().strip().split("\n") if l]
first = next(r for r in records if r["id"] == "exp_test_001")
frozen_hypothesis = first["hypothesis"]

# 尝试"修改"——追加一条 hypothesis 不同的记录（实验数据结构应拒绝或追加）
sandbox.update(exp.id, {"hypothesis": "篡改后的假设"})

# 查询当前状态时，hypothesis 应使用第一条冻结记录的值
current = sandbox.get(exp.id)
# 如果存储层正确实现，hypothesis 应保持原值
assert current.hypothesis == frozen_hypothesis, \
    f"hypothesis 被篡改！当前值: {current.hypothesis}"
print("预注册冻结机制验证通过")
```

---

### T8-2 rejected 实验设置冷却期

**步骤**

```python
import time
from mini_agent.perception.exploration_sandbox import ExplorationSandbox
from mini_agent.storage.paths import AgentPaths
from unittest.mock import patch

sandbox = ExplorationSandbox(paths=AgentPaths("."), cfg=None, arbiter=None)

# 创建并注册实验（T8-1 已有，或重新创建）
# 将其标记为 rejected
sandbox.finalize("exp_test_001", outcome="rejected", conclusion="假设不成立")

# 验证冷却期已设置（cooldown_until > now）
exp = sandbox.get("exp_test_001")
assert exp.cooldown_until is not None
assert exp.cooldown_until > time.time()

# 验证 next_candidate() 不再选中该实验
candidate = sandbox.next_candidate()
assert candidate is None or candidate.id != "exp_test_001", \
    "冷却期内不应选中 rejected 实验"
print("冷却期机制验证通过")
```

---

### T8-3 探索沙盒强制沙箱模式（`--sandbox`）

**步骤**

```python
from mini_agent.perception.exploration_sandbox import ExplorationSandbox
from mini_agent.storage.paths import AgentPaths
from unittest.mock import MagicMock, patch

arbiter = MagicMock()
arbiter.can_run_exploration.return_value = True

sandbox = ExplorationSandbox(paths=AgentPaths("."), cfg=MagicMock(), arbiter=arbiter)

with sandbox.create(capability_id="test_cap", goal="验证 X 方案") as ctx:
    # 验证 EvolutionWorkspace 以 sandbox=True 创建
    assert ctx.workspace.sandbox == True, "探索沙盒必须强制启用 sandbox 模式"
    ctx.report.success = True
    ctx.record_tokens(100)

# 退出后 worktree 被清理
print("沙盒强制模式验证通过")
```

---

### T8-4 探索预算耗尽时抛出异常

**步骤**

```python
from mini_agent.perception.exploration_sandbox import ExplorationSandbox, ExplorationBudgetExhausted
from mini_agent.storage.paths import AgentPaths
from unittest.mock import MagicMock

arbiter = MagicMock()
arbiter.can_run_exploration.return_value = False   # 预算耗尽

sandbox = ExplorationSandbox(paths=AgentPaths("."), cfg=None, arbiter=arbiter)

try:
    with sandbox.create(capability_id="test", goal="any"):
        pass
    assert False, "应该抛出 ExplorationBudgetExhausted"
except ExplorationBudgetExhausted:
    print("预算耗尽异常验证通过")
```

---

## 回归测试

Stage 9 改动了多个已有文件，需要确认现有测试不被破坏。

### R-1 Stage 0-8 单元测试全绿

```bash
pytest tests/ -x -q --ignore=tests/test_daemon_process.py \
                      --ignore=tests/test_autonomy_level.py \
                      --ignore=tests/test_goal_backlog.py \
                      --ignore=tests/test_autonomous_loop.py \
                      --ignore=tests/test_resource_arbitration.py \
                      --ignore=tests/test_activity_digest.py \
                      --ignore=tests/test_state_repo_initiator.py \
                      --ignore=tests/test_experiment.py
```

**预期结果**：无任何失败（Stage 0-8 的所有测试通过）

---

### R-2 `StateRepo.apply()` 向后兼容

```bash
pytest tests/test_state_repo.py -v
```

**预期结果**：所有测试通过，且没有"unexpected keyword argument 'initiator'"类型的错误

---

### R-3 TaskStatus 扩展不破坏现有状态机

```bash
pytest tests/test_orchestrator.py tests/test_concurrency.py -v
```

**预期结果**：所有测试通过（新增 `PAUSED` 不影响现有 DONE/FAILED/CANCELLED 路径）

---

### R-4 `--no-daemon` 路径完全兼容 Stage 0-8 行为

```bash
# 临时设置 --no-daemon 为默认（或在测试中 mock daemon 检测逻辑返回"未运行"）
pytest tests/test_session.py tests/test_llm.py tests/test_skill_manager.py -v
```

**预期结果**：所有测试通过

---

## 附录：测试文件与模块对应表

| 测试文件 | 对应本手册模块 | 核心验证点 |
|---------|--------------|----------|
| `tests/test_daemon_process.py` | 模块 1 | 进程生命周期独立于客户端、PID 管理、CLI 连接模式 |
| `tests/test_autonomy_level.py` | 模块 2 | 字段默认值、向后兼容、命令边界、紧急降级 |
| `tests/test_goal_backlog.py` | 模块 3 | 读写、WorkThread 关联、原子写入 |
| `tests/test_autonomous_loop.py` | 模块 4 | 三档位分支边界、无客户端自主触发（端到端）|
| `tests/test_resource_arbitration.py` | 模块 5 | 预算硬限制、路径冲突检测、PAUSED 状态、探索预算隔离 |
| `tests/test_activity_digest.py` | 模块 7 | 摘要记录写入、分组展示、连接时自动展示 |
| `tests/test_state_repo_initiator.py` | 模块 6 | T0→T1 上浮、向后兼容、`/agent autonomy` 不走 apply() |
| `tests/test_experiment.py` | 模块 8 | 预注册冻结、冷却期、沙盒强制、预算异常 |

---

*参见：[Stage 9 功能设计指南](self-evolution-stage9-guide.md)、[Stage 9 详细方案](../next_doc/self_evolution_stage9_plan.md)、[巩固循环 后台循环指南](self-evolution-consolidation-guide.md)*
