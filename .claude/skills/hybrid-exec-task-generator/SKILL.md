---
name: hybrid-exec-task-generator
description: 帮助用户为 hybrid_exec 系统（脚本/LLM/Agent 混合执行，`src/mini_agent/hybrid_exec/`）构建、测试、调试、改进一个符合规范的"混合任务"（`TaskSpec`）——从零写出 `task_id`/`description`/`input_data`/`output_validator`，用 `mini-agent hybrid-exec run`（或工具 `run_hybrid_exec_task`/真实 `HybridExecutor`）跑一次验证，看 attempts 决策轨迹排查探索/修复/降级为什么没按预期走，用 `mini-agent hybrid-exec list/show`（或工具 `list_hybrid_exec_tasks`/`show_hybrid_exec_task`）检查/手工调整 `.agent/hybrid_exec/scripts/<task_id>/` 里落盘的脚本版本，最终决定是留作独立调用、命令行触发、Agent 工具调用，还是接入 workflow 的 `hybrid_step`。当用户说"帮我做一个hybrid_exec任务"、"这个任务用脚本/LLM/Agent混合执行"、"给xxx写个TaskSpec"、"这个hybrid task一直探索失败"、"脚本总是被修复失败降级"、"hybrid_step怎么配"、"用命令行跑一下这个hybrid任务"时使用。
triggers: hybrid_exec, TaskSpec, HybridExecutor, default_executor, hybrid_step, 混合执行, 脚本探索, 脚本修复, output_validator, ScriptRepository, force_reexplore, allow_tiers, dry-run, 降级, retire, 退役, mini-agent hybrid-exec, run_hybrid_exec_task, list_hybrid_exec_tasks, show_hybrid_exec_task
---

# hybrid_exec 任务生成器（构建 / 测试 / 调试 / 改进 TaskSpec）

用于 `next_doc/hybrid_exec_design_plan.md` 描述的脚本/LLM/Agent 混合执行
系统。核心问题是把用户的一个具体需求（"从文本抽取实体"、"把日志汇总成
摘要"……）表达成一个规范的 `TaskSpec`，跑通"脚本优先、坏了先修脚本、
修不好再降级 LLM/Agent"这条决策链路，并在效果不理想时知道去哪一层排查。

完整字段/流程/存储细节见 `docs/hybrid-exec-guide.md`（本 skill 只摘录
生成/调试时必需的部分，字段语义有分歧时以该文档为准）。**不**用于生成
`workflow.yaml` 里普通的 `agent`/`python_step` 类型 step——那是
workflow-generator skill 的场景；hybrid_exec 任务只有明确要用"脚本优先、
自动降级"这套机制时才用本 skill。

## 第一步：判断这个需求适不适合做成 hybrid_exec 任务

先问自己（或直接问用户）：

- **会被重复调用吗？** 只跑一次的一次性任务不值得，直接让 Agent 做或写
  个一次性脚本更划算——hybrid_exec 的核心价值是"探索一次、固化成脚本、
  以后重复调用时低成本命中"，摊销的是**重复调用**的成本。
- **输入结构是否基本稳定？** 每次调用形状差异很大（比如任务描述本身随
  每次调用大幅变化）会导致脚本反复失配、频繁走修复/降级，收益不明显。
  这种更适合直接用 `allow_tiers=(LLM,)` 或 `(AGENT,)` 长期走 Fallback，
  不强求产出脚本。
- **能不能写出 `output_validator`？** 能明确"什么样的产出算对"（哪怕只是
  "是个 dict 且包含某几个 key"这种弱校验）时，探索/修复阶段的自动判定会
  可靠很多；完全写不出校验标准的任务，dry-run 只能退化成"不抛异常就算
  过"，脚本质量没有兜底，需要更依赖人工抽查前几次产出。

都满足或至少满足前两条，才继续下面的构建流程。

## 第二步：起草 TaskSpec

关键字段（完整定义见 `src/mini_agent/hybrid_exec/spec.py`）：

| 字段 | 怎么定 |
|---|---|
| `task_id` | 稳定标识，脚本仓库按它归档。用蛇形命名 + 版本后缀习惯，如 `extract_entities_v1`；**同一个 task_id 复用会命中已有脚本**，改需求想重新探索要么换新 `task_id`，要么用 `force_reexplore=True` |
| `description` | 自然语言目标描述，直接进 Explorer/Repairer 的 prompt。要具体：输入从哪个字段读、输出要是什么结构、边界条件怎么处理，写得越模糊，探索出来的脚本质量越随缘 |
| `input_data` | **本次调用**的真实输入样例（不是 schema 描述）——探索阶段会拿这份数据做 dry-run，必须是脚本 `run(ctx)` 里 `ctx.params` 能读到的真实值，不能是占位符 |
| `output_validator` | `Callable[[Any], tuple[bool, str]]`，返回 `(是否通过, 原因)`。不传则"不抛异常即算成功"。**优先写这个**，哪怕只是弱校验（类型 + 必需 key），比完全不校验可靠得多 |
| `allow_tiers` | 三层子集 `(script, llm, agent)`。先用 `(script, llm)` 控制成本，观察 LLMExplorer 是否够用；确实需要多轮探查环境/数据形状再逐步开 `agent`（成本明显更高） |
| `max_script_repair_attempts` | 默认 2，脚本报错后最多修几轮（最后一轮若预算够且允许 AGENT 会升级到 AgentRepairer） |
| `force_reexplore` | 调试/迭代 description 时常用，强制忽略仓库里已有脚本重新探索一次 |
| `agent_fs_write_enabled` | 默认 `False`，只读沙箱；确实需要 Explorer/Repairer 拉起的 Agent 读写项目文件时才显式打开 |

## 第三步：跑一次验证（不要凭空猜结果）

优先用现成的接入方式，而不是每次都手写驱动脚本——这三条路径底层都是同
一个 `HybridExecutor`，结果和落盘的脚本仓库完全一致，选哪个只取决于
"Agent 在对话里操作"还是"人在终端里操作"：

**(a) 在当前 Agent 对话里直接调用工具**（最省事，Agent 自己就能做，不用
落盘任何驱动脚本）：

```
调用工具 run_hybrid_exec_task(
    task_id="extract_entities_v1",
    description="从输入文本中抽取人名/机构名，返回 {\"entities\": [...]}",
    input_json='{"text": "张三在腾讯工作，李四在阿里巴巴。"}',
    allow_tiers="script,llm",
)
```
返回文本里直接带 `ok=`/`tier_used=`/`script_version=`/`output=`；`ok=False`
时还会带上 attempts 决策轨迹的简要串。想先看仓库里有没有这个 task_id、
现状如何，先调用 `list_hybrid_exec_tasks()` / `show_hybrid_exec_task(task_id)`。

**(b) 用命令行**（人在终端里手动验证，或要写进调试记录方便复现）：

```bash
mini-agent hybrid-exec run extract_entities_v1 \
  --field 'text=张三在腾讯工作，李四在阿里巴巴。' \
  --desc '从输入文本中抽取人名/机构名，返回 {"entities": [...]}' \
  --allow-tiers script,llm \
  -v   # 打印完整 attempts 决策轨迹，见第四步
```
`input_data` 支持 `--field key=value`（可重复）/`--input-file <path>`/
位置参数 JSON 字符串/stdin 管道四种传法，Windows PowerShell 下优先用
`--field` 或 `--input-file`（位置参数里的 JSON 双引号容易被 PowerShell
转义丢失，报 `unrecognized arguments: xxx}`；见 `docs/hybrid-exec-guide.md`
§十四）。`mini-agent hybrid-exec list` / `show <task_id>` 对应第五步的仓库
检查，不用再手动 `read_file` 拼路径。

**(c) 需要自定义 `output_validator` 时，才手写驱动脚本**（(a)/(b) 都不支持
传自定义校验函数，只能"不抛异常即算成功"；确实需要精确校验产出结构时用
这条路径，用 `bash` 工具跑）：

```python
import sys
sys.path.insert(0, "src")  # 若尚未 pip install -e .
from mini_agent.hybrid_exec import default_executor, TaskSpec, ExecutionTier

executor = default_executor(project_root=".")  # 自动读该项目的 providers.json
result = executor.run(TaskSpec(
    task_id="extract_entities_v1",
    description="从输入文本中抽取人名/机构名，返回 {\"entities\": [...]}",
    input_data={"text": "张三在腾讯工作，李四在阿里巴巴。"},
    allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM),
    output_validator=lambda out: (
        isinstance(out, dict) and isinstance(out.get("entities"), list),
        f"期望 dict 且含 entities 列表，实际 {out!r}",
    ),
))
print("ok=", result.ok, "tier=", result.tier_used.value, "version=", result.script_version)
print("output=", result.output)
print("attempts=")
for a in result.attempts:
    print(f"  [{'OK' if a.ok else 'FAIL'}] {a.stage} ({a.tier.value}) {a.reason}")
```

跑之前确认 `providers.json` 已配置好（(a)/(b)/(c) 三条路径都会像主 Agent
一样自动加载，不需要手动传 model/provider/api_key；没配好会在真正发起
调用时报错，报错信息里能看出是 provider 没配还是 model 名不对）。若这个
任务将来要嵌入某个 `python_step` 脚本内部调用，改用
`default_executor(ctx.project_root, llm=ctx.llm)`——见 `docs/hybrid-exec-guide.md`
§一.1，此时不需要（也不应该）用 (a)/(b)/(c) 独立测；应直接在目标
`python_step` 脚本里用 `ctx.llm` 现场验证，复用 workflow 当次已解析好的
provider/模型。

## 第四步：看 attempts 定位问题出在哪一层

`ExecutionResult.attempts`（CLI 用 `-v` 打印，工具 `run_hybrid_exec_task`
失败时自动带上）是完整决策轨迹，`stage` 命名规律：

| `stage` 前缀 | 含义 | 没通过时该看什么 |
|---|---|---|
| `explore_llm` / `explore_llm_dryrun` | LLMExplorer 产出脚本 / 该脚本用真实 `input_data` 跑通过没 | 产出报错通常是 prompt 里 `description` 不够具体，或模型没理解 `run(ctx)` 协议（可以直接打印 `LLMExplorer.explore()` 产出的源码看哪里错）；dryrun 报错看 `reason` 里的 traceback，多是 `ctx.params` key 名理解错了——回头改 `description`，别急着开 `AGENT` |
| `explore_agent` / `explore_agent_dryrun` | LLMExplorer 没通过后升级 AgentExplorer 的结果 | 只有 `allow_tiers` 含 `AGENT` 才会走到；仍失败通常是任务本身对"输入形状"依赖太强，`input_data` 样例不够有代表性 |
| `script_run` / `script_run_validate` | 命中已有脚本后的真实执行 / 校验结果 | `script_run` 失败进修复阶段；`script_run_validate` 失败说明脚本能跑但结果不对，同样按失败处理进修复——先看是不是 `output_validator` 本身写太严格 |
| `repair_llm#N` / `repair_llm#N_dryrun` | 第 N 轮 LLM 修复 / 该轮修复后 dry-run 结果 | 反复修不好，通常是原脚本的错误定位方式对 LLM 不友好——检查报错信息本身是否清晰（比如裸 `Exception` 而不是具体异常类型），必要时先人工把脚本改成报更明确的错，再让它继续修 |
| `repair_agent#N...` | 最后一轮升级到 AgentRepairer | 同上，且只在 `max_script_repair_attempts > 1` 且允许 `AGENT` 时出现 |
| `fallback_llm` / `fallback_llm_validate` | 脚本这条路彻底放弃后，LLM 直接给答案 | 走到这里说明脚本层面已经不可用，先看前面 `explore_*`/`repair_*` 为什么全失败，而不是只在这一层调 prompt |
| `fallback_agent` / `fallback_agent_validate` | 最高层级兜底，没有更弱的降级空间 | 这里还是不通过，`ok` 会如实反映校验失败——回头重新审视 `output_validator` 是不是定义得不合理，或任务本身超出当前模型能力 |
| `proactive_reexplore_check` | 仅 `ReexplorePolicy` 启用时出现，判断是否要机会主义重探索 | 与本次是否成功无关，不用于排查失败 |

**调试口诀**：先看最早失败的那个 `stage`，大概率问题根源就在那一层，不要
跳过它直接去改更下游（比如脚本 dry-run 就没过，却跑去调
`output_validator`，治标不治本）。

## 第五步：检查/手工干预脚本仓库（有需要时）

脚本真实落盘在 `.agent/hybrid_exec/scripts/<task_id>/`（`vN.py` +
`meta.json`）。查看用命令行 `mini-agent hybrid-exec list`（列举所有
task_id 及当前状态）/`show <task_id>`（某个 task_id 的 `meta.json` +
`vN.py` 源码一起打印），或对话里让 Agent 调用工具
`list_hybrid_exec_tasks()`/`show_hybrid_exec_task(task_id)`，都不需要手动
`read_file`/`list_dir` 拼路径了：

- **想看当前 active 的是哪个版本**：`show_hybrid_exec_task(task_id)` 或
  `mini-agent hybrid-exec show <task_id>` 直接给出 `active_version`、每个
  版本的 `success_count`/`fail_count`/`consecutive_fail`/`status`。
- **脚本写得不理想，想直接手改**：可以用 `write_file`/`patch_file` 改
  `vN.py` 本身（比如探索出来的脚本能跑但风格不好），改完**必须**用第三步
  的方式（(a)/(b)/(c) 任一）重新跑一次（走 `script_run`/
  `script_run_validate` 路径）验证没改坏；不要只改文件就假设它还能用。
- **想清空重来**：删掉整个 `<task_id>/` 目录，或调用时传
  `force_reexplore=True`（CLI 对应 `--force-reexplore`，工具对应同名参数）
  更安全（不用手动清目录，且失败了旧脚本还在）。
- **连续失败太多次被自动退役**（`meta.json` 里 `status: retired`）：下次
  `run()` 会自动重新走探索，不需要手动处理；如果想立刻验证退役后的重探索
  行为，直接再跑一次第三步的方式即可。

## 第六步：定下来之后怎么用

四种落地形态，按场景选，不互斥（同一个 `task_id` 可以同时被多条路径
调用，共享同一份脚本仓库）：

1. **主 Agent 对话里直接用**：确定好的 `task_id`/`description` 直接让主
   Agent 以后调用 `run_hybrid_exec_task(task_id=..., description=..., input_json=...)`
   即可，不用每次都重新走这个 skill 的构建流程——仓库里已有 active 脚本
   时会直接命中。
2. **命令行触发**（cron/systemd/CI，或人工手动跑一次）：
   ```bash
   mini-agent hybrid-exec run extract_entities_v1 --field 'text=...'
   ```
3. **独立调用**（daemon 自主循环、其它 workflow 的 `python_step`
   内部当库调用）：把第三步 (c) 的驱动脚本思路整理成正式代码里的一次
   `default_executor(project_root).run(TaskSpec(...))` 调用；嵌入某个
   `python_step` 脚本内部时改用 `llm=ctx.llm`（见第三步末尾）。
4. **接入 workflow**（作为某个 workflow 的一个 step）：写成 `hybrid_step`
   类型（需要 `myplugins/hybrid_step.py` 插件已启用，删除该文件即禁用）：

   ```yaml
   - id: extract_entities
     type: hybrid_step
     depends_on: [fetch_text]
     params:
       task_id: extract_entities_v1        # 与前面验证时用的保持一致，直接复用已探索出的脚本
       description: "从输入文本中抽取人名/机构名，返回 JSON"
       allow_tiers: [script, llm]
       max_script_repair_attempts: 2
       result_required_keys: [entities]     # 等价于给一个"必须含这些 key"的 output_validator
   ```

   这种写法下 `hybrid_step` 会按 `step.model → wf.defaults.model → 全局
   cfg.model` 解析出模型、构造一次共享的 `LLMHelper` 给探索/修复/兜底
   共用，不需要在 YAML 里额外指定 provider——和主 Agent、独立调用路径的
   模型解析规则一致。想先在沙箱里验证这个 step 而不是接入整条 DAG，用
   workflow-debugger skill 的 `test_workflow_step`。

## 常见坑

- **`input_data` 传成了 schema 描述而不是真实样例**：探索阶段的 dry-run
  是拿这份数据真跑一次，传占位符/说明文字会导致 dry-run 通不过、误判为
  "脚本写得不好"，其实是输入给错了。
- **`output_validator` 抛异常**：`run_validator` 内部会捕获，但排查时容易
  忘记检查校验函数本身是否对某些边界输入（比如 `output` 是 `None`）会
  抛异常——先确认 `output_validator` 自己够健壮。
- **同一个 `task_id` 换了完全不同的需求**：会直接命中旧脚本、拿旧逻辑跑
  新需求，产出通常是"能跑但结果不对"→ 触发修复循环、越修越怪。换需求要
  么换新 `task_id`，要么显式 `force_reexplore=True`。
- **一上来就开 `allow_tiers` 含 `AGENT`**：成本明显更高，多数任务
  `(SCRIPT, LLM)` 就够，先跑通再按需升级。
- **独立执行场景下重复 new 出多个 `default_executor()`**：每次
  `default_executor(project_root)` 内部只构造一次共享的 `LLMHelper`（与
  主 Agent 的 `LLMClientPool` 构造方式一致，含多 key 轮转/故障转移），但
  这个共享是"每个 `HybridExecutor` 实例内部"的——如果在一个循环里反复
  `default_executor()`，也会反复重建。同一批调用应该复用同一个
  `HybridExecutor` 实例（`executor = default_executor(...)` 建一次，
  `executor.run(task)` 多次调用），不要每次都重新 `default_executor()`。
- **命令行位置参数传 JSON 在 Windows PowerShell 下报
  `unrecognized arguments: xxx}`**：PowerShell 把含双引号的字符串传给
  原生 exe 时容易丢失内层引号转义，导致 JSON 被空格拆散。改用
  `--field key=value`（可重复）或 `--input-file <path>` 即可绕开，不要在
  这个问题上排查 hybrid_exec 本身的逻辑（详见
  `docs/hybrid-exec-guide.md` §十四）。
