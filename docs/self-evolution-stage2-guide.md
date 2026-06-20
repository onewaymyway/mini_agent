# 自我演化安全网（Stage 2）：StateRepo / 验证流水线 / EvolutionWorkspace

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 2，
> 设计依据 `next_doc/self_evolution_design.md` 第 4 节"安全网设计"。

---

## 1. 这是什么

Stage 2 实现了自我演化机制的**安全网三件套**：agent 未来若要"修改自己"（新增/调整
skill、调参、甚至改代码），所有改动都必须经过这三层机制，不允许绕过：

1. **`StateRepo`**（`src/mini_agent/evolution/state_repo.py`）——
   所有自我修改的唯一写入入口。把"agent 状态"和"项目代码"统一纳入同一个 git 仓库，
   用风险分级（T0~T3）而非"状态 vs 代码"来区分治理强度。
2. **验证流水线**（`src/mini_agent/evolution/validators.py`）——
   按 tier 递增的校验严格度，校验失败则 `StateRepo.apply()` 不落盘、不 commit。
3. **`EvolutionWorkspace`**（`src/mini_agent/evolution/workspace.py`）——
   基于 `git worktree` 的进程级隔离，验证"改动在隔离环境里能正常加载"。

以及配套的 **`/evolution` CLI 命令组**（`src/mini_agent/cli/commands/evolution.py`），
让人类可以查看自我修改历史、审查 diff、并在需要时一键回退。

Stage 2 范围内**还没有**任何机制能自动生成、提议或应用这些改动——那是 Stage 3
（接入 lesson → skill 提案 / 调参提案的实际生产者）的工作。本阶段交付的是"管道"，
不是"内容"。

---

## 2. 风险分级回顾（T0~T3）

| Tier | 含义 | Stage 2 对应校验 |
|---|---|---|
| T0 | 纯数据（lesson/memory 条目、profile 偏好、统计） | schema 校验（合法 JSON/JSONL） |
| T1 | 声明式资产（SKILL.md、subagent profile、permissions.json） | schema + 加载校验（真用对应加载器解析一遍） |
| T2 | 行为性代码（非安全关键的 .py 改动） | lint（语法 + 可选 ruff）+ 现有单测全过 |
| T3 | 安全关键改动（命中 `scripts/protected_paths.py` 清单的文件） | 与 T2 相同的校验，且**强制人审**（CLI 展示 diff，不自动 apply） |

**T3 红线是强制的、单向的**：调用方即使声明 `tier="T0"`，只要改动路径命中受保护
路径清单，`StateRepo.resolve_tier()` 一律把生效 tier 升级为 T3，且只升不降。这个
判定逻辑复用 Stage 0.1 已经做好的 `scripts/protected_paths.py`（不在 `mini_agent`
包内，import 失败时安全降级为"全部判定为受保护"，而不是静默放行）。

---

## 3. StateRepo：唯一写入入口

### 3.1 核心方法

```python
from pathlib import Path
from mini_agent.evolution.state_repo import StateRepo

repo = StateRepo(Path("/path/to/project"))   # 若尚无 .git，自动 git init

result = repo.apply(
    changes={"skills/foo/SKILL.md": "---\nname: foo\n---\n..."},  # None 值表示删除该文件
    message="Add foo skill",
    meta={
        "source_lessons": ["lesson_2026061501", "lesson_2026061203"],
        "session_id": "sess_xxxxx",
        "confidence": 0.82,
        "occurrence_count": 4,
        "proposed_by": "evolution-agent",
    },
    tier="T1",
    auto_validators=True,   # 按【生效】tier 自动从 validators.py 选校验函数
)

if result.ok:
    print(result.commit, result.tier, result.forced_tier)
else:
    print(result.validation_errors)   # 校验失败：未落盘、未 commit
```

`apply()` 的流程是**原子的**：先算出受保护路径强制升级后的生效 tier → 跑校验 →
任意一项失败就直接返回失败结果（文件系统和 git 历史都不会有任何变化）→ 全部通过
才真正写文件 + `git add -A` + `git commit`。不存在"文件已经改了但 git 没记录"
的中间状态。

### 3.2 结构化 commit message

每条自我修改的 commit message 都遵循统一格式：

```
[T1][evolution-agent] Add foo skill

source_lessons: lesson_2026061501, lesson_2026061203
session_id: sess_xxxxx
confidence: 0.82
occurrence_count: 4
proposed_by: evolution-agent
```

这保证后续"剪枝/冲突检测"和"能力地图"等机制（Stage 3+）可以直接从 `git log`
反查到每次改动的来源 lesson、触发 session、可信度等元信息，不需要额外的旁路存储。

### 3.3 历史查询与回退

```python
commits = repo.log(limit=20)          # list[CommitInfo]：commit/author/date/subject/body/files
diff_text = repo.diff("HEAD~1", "HEAD")
new_commit = repo.revert(commits[0].commit)   # git revert（生成新 commit），不是 reset --hard
repo.checkout_file(commits[0].commit, "skills/foo/SKILL.md")  # 仅恢复单个文件，不自动 commit
```

`revert()` 故意不用 `git reset --hard`：设计文档 4.3 节强调"试过 X、效果不好、
已回退"本身是历史的一部分，应该保留可追溯的记录，而不是让某次尝试在历史里
彻底消失。

---

## 4. 验证流水线：按 tier 升级

`src/mini_agent/evolution/validators.py` 提供四组校验函数，按 `validators_for_tier(tier)`
取用：

```python
from mini_agent.evolution.validators import validators_for_tier

validators_for_tier("T0")  # [validate_t0_schema]
validators_for_tier("T1")  # [validate_t0_schema, validate_t1_load]
validators_for_tier("T2")  # [validate_t2_lint, validate_t2_existing_tests]
validators_for_tier("T3")  # [validate_t3]  （内部等价于 T2 的全部校验项）
```

- **T0 schema 校验**：`.json` 必须是合法 JSON，`.jsonl` 逐行合法 JSON。
- **T1 加载校验**：
  - `SKILL.md`（嵌套 `skills/<name>/SKILL.md` 或扁平 `skills/<name>.md`）
    复用 `mini_agent.skills._parse_skill` 实际解析一遍——保证"通过校验"和
    "agent 运行时真的能加载"是同一套代码路径。
  - `.agent/agents/*.md`（自定义 subagent profile）校验 YAML frontmatter
    必须含 `name` 字段。
  - `permissions.json` 复用 T0 的 JSON 合法性校验。
- **T2 lint + 现有单测**：对改动的 `.py` 文件先做 `compile()` 语法检查（环境无
  ruff 时的兜底），ruff 存在则额外跑一次；再在 `StateRepo.root` 下跑一次
  `pytest tests -q`（找不到 `tests/` 目录则视为不适用，直接放行）。
- **T3**：直接复用 T2 的全部校验项；"diff 必须显式标红、强制人审"是 CLI
  展示层的职责（见下文 `/evolution` 命令），不属于校验函数本身的工作。

### 4.1 Stage 2 的取舍

T2/T3 的"副本进程 smoke boot"和"eval 场景对比"**没有**塞进 `validators.py`
的校验函数里——这部分逻辑属于 `EvolutionWorkspace`（见下一节），保持"校验"
（轻量、同步、跑在调用方进程内）与"隔离验证"（较重、需要独立进程/worktree）
两层关注点分离。完整的 eval 场景批量对比留到 Stage 3。

---

## 5. EvolutionWorkspace：进程级隔离

```python
from mini_agent.evolution.state_repo import StateRepo
from mini_agent.evolution.workspace import EvolutionWorkspace

repo = StateRepo(project_root)

with EvolutionWorkspace.create(repo, branch="evolve/2026-06-20-bash-safety") as ws:
    # ws.path 是一个 git worktree，与主仓库共享对象库（近零磁盘/时间成本）
    result = ws.smoke_boot(timeout=60)
    if result.ok:
        ...  # 验证通过，后续可以 merge 该分支
    else:
        print(result.reason, result.stderr)
# 退出 with 块自动 destroy()：git worktree remove --force（不删分支，除非 delete_branch=True）
```

- **`create()`**：`git worktree add <path> -b <branch>`，分支已存在时直接复用
  （支持"smoke_boot 失败后修复重试"的重入场景）。
- **`needs_isolated_venv()`**：比较 worktree 内 `requirements.txt`/`pyproject.toml`
  与主仓库当前版本是否一致，不一致才建独立 venv——避免为了"保险"而无条件
  抵消掉 `git worktree` 共享对象库带来的速度优势。
- **`smoke_boot()`**：Stage 2 范围内的"最低验证"——在 worktree 目录下用子进程
  `import mini_agent` 全套关键模块（`Agent`、`ToolRegistry`、`SkillLoader`、
  `PermissionGuard`）并构造一次最简对象图，不依赖真实 LLM API key，验证的是
  "代码改动没有破坏模块加载"，而不是真正跑一次完整对话。
- **`write_eval_result()`**：把结果写入 `<worktree>/.agent/eval_result.json`，
  打通设计文档 4.5 节"主进程只读 eval_result.json 做对比"的落盘机制；
  真正的 tool 失败率 / turns / token 对比数据由 Stage 3 填充。
- **`--sandbox-permissions strict`**：设计文档明确"直接复用现有 `permissions.py`
  的 `--sandbox` flag，无需新发明"——本模块没有引入任何新权限机制。

### 5.1 销毁与清理

```python
ws.destroy(delete_branch=False)   # 默认只删 worktree，保留分支（便于后续 merge / 复查）
ws.destroy(delete_branch=True)    # 验证失败、确定要放弃这次尝试时，一并删分支
```

设计文档 4.5 节："最后只 merge 验证通过的那个，其余直接 `git worktree remove
--force` + 删分支，不留痕迹。" `destroy()` 把"是否已经决定放弃"这个判断权
交给调用方，不做隐含假设。

---

## 6. `/evolution` CLI 命令

在 REPL 内可用：

```
/evolution log [N]            # 展示最近 N 条自我修改 commit（默认 10），表格形式
/evolution show <commit>      # 展示单条 commit 的完整结构化信息 + diff
/evolution diff <commit>      # 展示某次 commit 的改动 diff（标红/标绿语法高亮）
/evolution revert <commit>    # 生成 revert commit，并自动记录一条 lesson
```

`commit` 参数支持完整 hash 或前缀（取第一个匹配项）。

### 6.1 revert 自动反哺 lesson

设计文档 4.3 节："回退记录反哺 lesson 库——每次 revert 生成一条
`source="revert_record"` 的 lesson。" `/evolution revert` 在 git revert 成功后，
复用 Stage 1 已经打通的 `MemoryEntry` 写入路径（`agent._memory.add()` +
`agent._append_memory_delta()`），与 SessionEnd 反思、规则触发的 lesson 走
同一套存储，保证后续检索/剪枝/能力地图机制对三种来源一视同仁：

```python
MemoryEntry(
    entry_type="lesson",
    trigger="曾提案改动 04bc5f88（Add foo skill），已通过 /evolution revert 撤销",
    outcome="该改动被判定不应保留，已生成 revert commit fe042736 撤销其效果",
    suggested_action="不建议未经修改地重新尝试与 04bc5f88 同方向的改动",
    confidence=0.9,
    source="revert_record",
)
```

lesson 写入失败（memory 未启用、写入异常）只会打印警告，不影响 revert 本身——
revert 是一次已经完成的 git 操作，lesson 记录是锦上添花的审计产物。

---

## 7. 测试

```bash
pytest tests/test_state_repo.py -v              # StateRepo：apply/log/diff/revert/T3 强制升级
pytest tests/test_evolution_validators.py -v    # 验证流水线：T0~T3 各层校验函数
pytest tests/test_evolution_workspace.py -v     # EvolutionWorkspace：worktree 生命周期 + smoke_boot
pytest tests/test_evolution_cli.py -v           # /evolution 命令组
```

共 107 个新增测试用例，全部通过；项目现有 718 个测试无回归（仍是 Stage 0/1
之前就存在的 3 个与本阶段无关的预先失败用例：`test_tools_have_valid_json_schema`
与 `debug_logger.py` 的两个截断边界用例）。

CLI 测试的输出捕获没有用 `capsys`——`mini_agent.ui.terminal.term` 是模块级单例，
用后台线程异步消费消息队列来串行渲染，它持有的 `rich.Console` 在模块导入时就
绑定了 `sys.stdout`，`capsys` 既捕获不到也存在竞态。测试改为 monkeypatch
`term._console` 为写入 `io.StringIO()` 的 `Console`，并复用 `terminal.py` 自身
"进入输入模式前排空队列"用的 noop+`queue.join()` 哨兵技巧来同步等待渲染线程
处理完毕，再读取缓冲区内容。

---

## 8. 相关文档

- [自我演化实施计划](../next_doc/self_evolution_implementation_plan.md) — Stage 2 的完整需求背景
- [自我演化设计文档](../next_doc/self_evolution_design.md) — 第 4 节安全网整体设计
- [受保护路径清单](protected-paths-guide.md) — Stage 0.1，T3 强制升级判定的依据
- [记忆管理指南](memory-management-guide.md) — Stage 1 的 lesson/memory 写入路径，revert 记录复用了这套存储

---

*创建时间：2026-06（self_evolution_implementation_plan.md Stage 2）*
