# 贡献指南

## 架构收敛重构期间的规则（Sprint 0 起生效）

mini_agent 正在按 `next_doc/refactor_plan/README.md` 的规划做架构收敛
重构（"能力堆叠型 Agent" → "Self-centered Personal AI Runtime"）。在
这个过程完成之前，新增代码需要额外遵守以下规则，避免边重构边继续制造
新的架构债务。

### 规则一：冻结新增顶层一级概念

**不允许**在 `src/mini_agent/` 下新增名字以 `Manager` / `Scheduler` /
`Advisor` 结尾的顶层模块（文件或目录），除非满足下面的例外条件。

原因见 `next_doc/refactor_plan/01-evaluation-and-gaps.md`：旧架构里
概念数量失控的一大来源，就是每个新功能都倾向于新开一个顶层
"XxxManager"，而不是先考虑并入已有的 `Goal` / `Experience` / `Action`
等核心概念（见 `next_doc/refactor_plan/00-original-architecture-proposal.md`
的 8 大核心概念）。

**检查方式**：提交前本地运行

```bash
python scripts/lint_no_new_toplevel_concepts.py
```

退出码非 0 表示存在违规命名，脚本会打印具体文件名。当前仓库还没有接入
CI，所以这一步暂时需要贡献者/审阅者手动跑一次（或配成本地
pre-commit hook）；一旦仓库接入 CI，这一步会被加入流水线自动拦截，
详见脚本文件头注释。

**例外流程**：如果确实需要新增这样的顶层概念（比如它明显不属于任何
已有核心概念），需要：
1. 在 PR 描述里说明"为什么不能并入已有概念"；
2. 评审通过后，把新名字加入
   `scripts/lint_no_new_toplevel_concepts.py` 的 `WHITELIST` 集合，
   并在旁边写清楚来源（哪个 PR / 评审记录）；
3. 不允许绕过脚本直接合并（比如临时改名躲过 lint 之后再改回来）。

### 规则二：改代码必须同步更新对应文档

如果你的改动属于 `next_doc/refactor_plan/02` - `11` 任意一个 Phase
的范围，必须遵守
`next_doc/refactor_plan/12-execution-and-doc-sync-norms.md`
规定的"改代码 → 同次提交内更新对应 Phase 文档 / `MIGRATION_STATUS.md`"
流程，不允许"代码合并了，文档留到以后再补"。

### 规则三：Goal 模式改动需要先跑特征测试

`goal_mode/` 是本轮重构第一条迁移链（Sprint 1 起），其行为已经在
`tests/test_goal_mode.py`（状态机主流程）与
`tests/test_goal_mode_characterization.py`（Sprint 0 新增的迁移安全网，
覆盖 `CoarseStepExecutor` 及 `GoalRunner` 里若干纯函数/轻状态辅助方法）
里录制了"迁移前"的行为快照。改动 `goal_mode/` 下的代码前，先确保这两个
文件全部通过；如果你的改动是有意改变行为（而不是纯重构、行为应保持一致），
需要在同一个 PR 里更新对应的快照测试，并在 PR 描述里说明"哪条行为被
有意改变了、为什么"。

## 一般规范

其余通用的代码风格、测试运行方式等，暂未在本文档中展开，以仓库现有
`tests/` 下的测试写法为准（`pytest`，配置见 `pyproject.toml`）。
