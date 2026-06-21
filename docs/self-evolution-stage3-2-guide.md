# 自我演化 eval 反馈环（Stage 3.2 / Phase D）

> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 3.2，
> 设计依据 `next_doc/self_evolution_design.md` 第 5 节"Phase D：eval 反馈环"
> 与第 4.6 节验证流水线表格中的 T1 对比指标要求。

---

## 1. 这是什么

Stage 3.2 回答一个具体问题：**一个 skill 到底有没有用？** 在 `skill_propose`
（Stage 3.1）把某条 lesson 提炼成一份 SKILL.md 之后，需要有办法验证"加了这个
skill 之后，agent 表现是变好了还是没变化甚至变差"——而不是凭直觉判断。

新增的 `mini-agent eval` 命令复用 `test_cases/` 目录已有的场景文件作为回归集，
对同一批场景分别跑两遍（开启某个 skill / 排除该 skill），对比 **turns、
token 消耗、tool 调用次数与失败率**，输出结构化 JSON 报告。

核心实现分两部分：

1. **`src/mini_agent/evolution/eval_runner.py`** —— 场景加载、单场景执行、
   with/without 对比报告的核心引擎，不依赖 CLI，可被其他代码（未来的
   evolution-agent）直接调用。
2. **`src/mini_agent/cli/commands/eval_cmd.py`** —— `mini-agent eval` 命令行
   接口，构造真实 Agent 跑场景，把报告写到磁盘并打印摘要表格。

Stage 3.2 范围内**刻意不做**的事情（与 Stage 2.3 `EvolutionWorkspace` 的取舍
一致，避免战线过长）：

- 不在本模块内创建 git worktree 隔离——需要隔离环境跑 eval 时，直接组合
  `EvolutionWorkspace`（Stage 2.3）与本命令，两者不相互 import，通过
  `--project`/`--output` 路径参数自然衔接。
- 不做"多次试验取统计分布"（设计文档 7.10 节 Experiment 机制，属于 Phase H
  范畴）。每个场景每种模式默认只跑一次，衡量的是单次真实表现，不是统计置信区间。

---

## 2. 场景文件格式

直接复用 `test_cases/*.txt` 既有格式，不要求改造任何现有文件：

```
第一轮 prompt 文本，可以多行

第二轮 prompt 文本
（一个空行分隔出下一轮）

第三轮……
```

即：文件内容按"至少一个空行"切分为多个非空块，每块剥离首尾空白后即为一轮
输入；单轮场景（文件里没有空行）退化为只有一个元素的 turns 列表。每个文件
就是一个场景，场景名取文件名（不含扩展名）。

默认只匹配 `*.txt`，跳过 `test_cases/` 下已存在的 `.md`（人工测试手册）和
`inputs/`（辅助资源目录）。`--pattern` 可覆盖匹配规则。

### `--max-scenario-turns`：跑批安全阀

`test_cases/` 下个别文件（例如多分镜漫画脚本）用空行分隔了几十个段落，
这些文件的本意是"一份详细的长 prompt"而非"几十轮真实对话"。如果不加防护，
逐轮喂给 Agent 会意外产生几十次真实 LLM 调用，把一次轻量 eval 跑成长耗时、
高费用的任务。

`load_scenarios(..., max_turns=N)` 会跳过轮次数超过 N 的场景文件并打印警告
（不报错，不中断整批运行）；CLI 默认 `--max-scenario-turns 10`，传 `0`
可关闭此限制。

---

## 3. `mini-agent eval` 命令

### 3.1 用法

```bash
# 对比某个 skill 开启 vs 排除的效果
mini-agent eval --scenario test_cases/ --skill docx

# 自定义输出位置
mini-agent eval --scenario test_cases/ --skill docx --output /tmp/report.json

# 不传 --skill：只验证场景集本身能否跑通（baseline 冒烟跑）
mini-agent eval --scenario test_cases/
```

完整参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--scenario DIR` | 必填 | 场景目录，通常是 `test_cases/` |
| `--skill NAME` | 无 | 要对比的 skill 名；不传则只跑一遍 baseline，不做对比 |
| `--pattern GLOB` | `*.txt` | 场景文件匹配规则 |
| `--max-scenario-turns N` | `10` | 跳过轮次数超过 N 的场景文件；传 `0` 关闭限制 |
| `--project DIR` | 当前目录 | Agent 运行所在的项目根目录 |
| `--skills-dir DIR` | `<project>/skills` | skill 目录（存在才会构造 SkillLoader） |
| `--output FILE` | `<project>/.agent/eval_result.json` | JSON 报告写入位置 |
| `--no-sandbox` | 关闭 | 关闭沙箱模式（不建议——场景 prompt 来自文件，sandbox 默认开启更安全） |
| `--max-turns N` | `AppConfig` 默认值 | 覆盖单场景每轮的最大 agentic turns |
| `--quiet` | 关闭 | 不打印逐场景进度 |

### 3.2 接入点：`cli/app.py` 的命令短路

`mini-agent` 的主入口位置参数是 `prompt`（直接接受自然语言指令），这与
argparse 的子命令模式天然冲突（不支持"位置参数 + 互斥子命令"既要又要）。
因此 `eval` 子命令没有走 `build_parser()`（`cli/parser.py`）的常规参数体系，
而是在 `cli/app.py` 的 `main()` 入口最前面短路：

```python
if len(sys.argv) > 1 and sys.argv[1] == "eval":
    from mini_agent.cli.commands.eval_cmd import run_eval_cli
    return run_eval_cli(sys.argv[2:])
```

检测到 `sys.argv[1] == "eval"` 后整体转发给 `run_eval_cli`，不进入主
`build_parser()` 流程——这是与现有单命令体系共存成本最低的接入方式。

### 3.3 默认 Agent 工厂

`eval_cmd.py` 内的 `_make_default_agent_factory()` 构造真实可用的 Agent：
走与 `mini-agent` 主入口同一条 `load_config()` 路径（不重新发明配置加载
逻辑），从环境变量 / `providers.json` 取真实 API key，**真实调用 LLM**——
eval 的意义就是衡量真实效果，不是用假数据自欺欺人。

每次调用都构造一个全新的 Agent（独立 history、独立 stats），保证场景之间
互不污染：同一个场景在 with/without 两种模式下跑的是两个完全独立的 session。

几个针对"批量跑场景"场景特化的调整：

- `auto_save_session=False` —— eval 跑的是临时评测会话，不写入 `sessions/`
  目录污染真实会话历史。
- `cfg.stream = False` —— 只关心最终统计数据，不需要终端流式渲染。
- **重试策略收紧**：生产环境默认 15 次重试 + 固定 5s 退避，eval 跑批场景下
  这会让单个真实失败的场景拖慢整批运行（15 × 5s ≈ 75s 起步）。eval 改为
  1 次重试、1 秒退避——网络抖动仍能恢复一次，真实失败也能快速失败进入
  下一个场景/模式。

### 3.4 `--without-skill`：`SkillLoader.exclude()`

要保证对比的公平性，"排除某个 skill"必须做到**完全不参与**，而不是"默认
不激活但仍可能被关键词命中"。为此 `SkillLoader`（`skills/__init__.py`）
新增 `exclude(name)` 方法，与已有的 `deactivate(name)` 有本质区别：

| 方法 | 行为 | 是否仍可能被 `auto_activate()` 重新拉起 |
|---|---|---|
| `deactivate(name)` | 仅从 `_active` 列表移除 | 是——skill 仍在 `_all` 里，关键词命中会重新激活 |
| `exclude(name)` | 从 `_all` 中整体删除 | 否——彻底不存在于本次 SkillLoader 实例中 |

```python
loader.exclude("docx")   # docx 从 _all 中被 del，无法再被 activate() 或 auto_activate() 命中
```

`exclude()` 返回布尔值表示是否真的移除了（传入不存在的名字返回 `False`，
调用方可借此判断拼写错误）。

---

## 4. 输出报告结构

```json
{
  "skill": "docx",
  "scenario_dir": "test_cases",
  "generated_at": "2026-06-20T15:30:00",
  "scenarios": [
    {
      "scenario": "create_python_code_and_run_test",
      "with_skill": { "mode": "with_skill", "ok": true, "turns": 3,
                       "input_tokens": 1200, "output_tokens": 450,
                       "tool_calls": 4, "tool_failures": 0,
                       "tool_failure_rate": 0.0, "duration_seconds": 8.21, "error": "" },
      "without_skill": { "mode": "without_skill", "ok": true, "turns": 5, "...": "..." },
      "delta": { "turns": -2, "input_tokens": -300, "output_tokens": -120,
                 "tool_calls": -1, "tool_failures": 0, "tool_failure_rate": 0.0 }
    }
  ],
  "summary": {
    "with_skill":    { "scenarios_ok": 8, "scenarios_total": 8, "total_turns": 24,
                        "total_input_tokens": 9600, "total_output_tokens": 3600,
                        "total_tool_calls": 32, "total_tool_failures": 1,
                        "tool_failure_rate": 0.0312 },
    "without_skill": { "...": "..." }
  }
}
```

`delta = with_skill - without_skill`：负数表示开启 skill 后该项指标更优
（turns/token/失败率越低越好）。`skill=None`（baseline 模式）时
`with_skill` 与 `without_skill` 是同一份结果，`delta` 全为 0——用于"只想
验证场景集本身能否跑通"，不关心某个具体 skill 的对比。

CLI 会在终端打印一份对齐的摘要表格（`_print_summary`），同时把完整报告
写入 `--output` 指定路径（默认 `<project>/.agent/eval_result.json`，与
`EvolutionWorkspace.write_eval_result()` 落盘位置同一约定，便于 Stage 2
的隔离环境与本命令组合使用）。

---

## 5. 与 `EvolutionWorkspace`（Stage 2.3）组合使用

设计文档 4.5 节："副本化运行天然产出 eval 数据"。`eval_runner.py` 与
`EvolutionWorkspace` 没有相互 import，而是通过路径参数自然组合：

```python
ws = EvolutionWorkspace.create(repo, branch="evolve/add-docx-skill")
# mini-agent eval --scenario test_cases/ --skill docx \
#     --project <ws.path> --output <ws.eval_result_path()>
```

即：在隔离 worktree 内跑 eval，结果写到该 worktree 专属的
`eval_result.json`，验证完毕后由调用方决定是否 merge 该分支。Stage 3.2
本身不引入 worktree 隔离（按文档原话"先在主进程内跑，牺牲隔离性换开发
速度"），把隔离留给调用方按需接入。

---

## 6. 测试

```bash
pytest tests/test_eval_runner.py -v                          # 场景加载/单场景执行/对比报告（27 例）
pytest tests/test_eval_cli.py -v                              # CLI 参数解析/输出/报告落盘（23 例）
pytest tests/test_skill_manager.py -k TestSkillLoaderExclude  # SkillLoader.exclude()（7 例）
```

共 57 个新增/相关测试用例，全部通过，无回归。

---

## 7. 相关文档

- [自我演化实施计划](../next_doc/self_evolution_implementation_plan.md) — Stage 3.2 的完整需求背景
- [自我演化设计文档](../next_doc/self_evolution_design.md) — 第 5 节 Phase D / 第 4.6 节验证流水线表格
- [自我演化安全网（Stage 2）](self-evolution-stage2-guide.md) — `EvolutionWorkspace` 隔离环境，可与本命令组合使用
- [Skill 系统指南](skill-system-guide.md) — `SkillLoader` 的激活/排除机制
- [自我演化 lesson → skill 闭环（Stage 3.1）](self-evolution-stage3-1-guide.md) — eval 用于验证该闭环产出的 skill 是否真的有效

---

*创建时间：2026-06（self_evolution_implementation_plan.md Stage 3.2）*
