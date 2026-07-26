# workflow python_step 机制 + 知乎内容发布 workflow 改进计划

状态：待执行
关联现有文档：`workflow_mechanism_improvement_plan_p10.md`、`workflow_directory_mode_design.md`、`llm_helper_unification_plan.md`
关联现有代码：`src/mini_agent/workflow/{schema,executors,runner,store}.py`、`src/mini_agent/llm/service.py`（`LLMHelper`）、`.claude/skills/browser-cdp/`

---

## 0. 背景与结论（先说清楚"为什么这样改"）

现状盘点（已核实，不是假设）：

1. `executors.py` 已有 `StepExecutor` 插件机制（`agent`/`role_agent`/`sub_workflow`/`tool_call`/`human_input`/`script`/`skill_agent` 7 种内置类型），新增类型只需加一个类 + 在 `_EXECUTORS` 注册一行，不用碰 `runner.py` 核心循环。**本次新增 `python_step` 直接复用这条路径。**
2. `llm/service.py::LLMHelper` 已经是"多 provider + 多 key 轮转 fallback + 统一重试策略"的封装，**不需要重新造**，只需要把它注入到脚本执行上下文里。
3. `schema.py::WorkflowStep` 已经有 `prompt_file` 字段（目录模式 workflow 下，`prompt_file` 相对 workflow 目录解析，加载时读文件内容填充 `prompt`，序列化时只写 `prompt_file` 不写展开文本）。**这条能力已经存在，本次要做的是把它从"可选项"变成"规范里的默认做法"，并且让 `python_step` 也遵循同一约定（脚本路径外置、不内嵌大段文本在 YAML 里）。**

本计划要做的三件事：

- **A. workflow 规范修订**：prompt 默认放外部文件，YAML 只留骨架；`python_step` 的脚本代码同理外置成独立 `.py` 文件。
- **B. 新增 `python_step` executor**：脚本内可调用 `LLMHelper`（含批量合并调用以省 token/延迟）、可调用"临时 Agent 判断"、可读写 session 固定目录。
- **C. 落地知乎发布 workflow**：按新规范实现，目录化组织，过滤步骤走批量 LLM 判断而不是逐条调用。
- **D. browser-cdp 稳定性修复**：统一"是否已有可用实例"的判定入口、强制固定 `--name` 复用 profile。

---

## A. workflow 规范修订（先改规范，再改代码，最后照规范写 workflow）

### A1. Prompt 外置规则（写入 `next_doc/workflow_authoring_guide.md`，新建）

规则：

- **禁止**在 `workflow.yaml` 的 `steps[].prompt` 里写超过 3 行的内联文本。超过 3 行的一律用 `prompt_file: prompts/xxx.md`。
- 目录化 workflow（`workflows_dir/<name>/`）标准结构：

```
<workflow_name>/
├── workflow.yaml          # 只有骨架：id/type/depends_on/prompt_file/output_file/条件/超时等
├── prompts/
│   ├── 01_analyze_doc.md
│   ├── 02_search_zhihu.md
│   ├── 03_filter_batch.md      # 批量过滤用的 prompt 模板
│   └── 04_enrich_questions.md
├── steps/                       # python_step 的脚本代码，同样外置
│   ├── 01_analyze_doc.py
│   ├── 03_filter.py
│   └── 04_enrich.py
├── agents/                      # 已有：本地角色 profile（如果用到）
└── skills/                      # 已有：本地 skill（如果用到）
```

- `prompt_file` 里允许使用与内联 `prompt` 相同的占位符语法（`{step_id.output}`、`{step_id.score}` 等），加载阶段先读文件再走原有的占位符替换逻辑，两者完全等价，只是来源不同。
- **迁移期兼容**：不废弃内联 `prompt` 字段（避免破坏已有 194 个测试和历史 workflow），`schema.py::validate()` 新增一条 **warning 级**（不是 error）检查：内联 `prompt` 超过某行数阈值（建议 5 行）时在 `workflow validate` 输出里提示"建议改用 prompt_file"，但不阻断保存/运行。

### A2. `python_step` 脚本外置规则

- `WorkflowStep` 新增字段 `script_path: Optional[str]`（类比 `prompt_file`，相对 workflow 目录解析，单文件模式 workflow 下要求必须绝对路径或相对 `project_root`）。
- 约定脚本入口函数签名固定为 `def run(ctx: PyStepContext) -> Union[str, dict]:`，见 B 节。
- `type: python_step` 时校验规则（`schema.py::WorkflowDef.validate()` 新增分支）：`script_path` 必填，否则报错，语义与现有 `script` 类型必填 `step.script` 保持一致的校验风格。

### A3. `output_file` 固定输出契约（新增字段，通用于所有 step 类型）

- `WorkflowStep.output_file: Optional[str]`：该 step 执行完成后，`runner._execute_step()` 统一负责把 `StepResult.output` 写一份到 `session.output_dir / step.output_file`，不管这个 step 是 `agent`/`skill_agent`/`python_step` 哪种类型产生的输出。
- 这样"每个流程固定输出文件名，都在 session 的 output 目录下"这条要求在 runner 层面统一保证，而不是依赖每个 agent prompt 自觉写对路径（prompt 里当然还是要提示 agent 把详细数据整理成 JSON，但落盘这一步不再依赖 agent 自己拼路径，减少不确定性）。
- `session.output_dir` 已存在（`.agent/workflow_sessions/wfs_xxx/output/`），本项无需新建机制，只需要 runner 收口这一步写文件的逻辑。

**改动文件清单（A 部分）**：
- `src/mini_agent/workflow/schema.py`：`WorkflowStep` 加 `script_path`、`output_file` 字段；`from_dict`/`to_dict` 同步；`validate()` 加 `python_step` 必填校验 + `prompt_file` 建议 warning。
- `src/mini_agent/workflow/store.py`：加载阶段解析 `script_path`（校验文件存在，不预先读入内存，因为是子进程独立执行，不像 `prompt_file` 需要读文本替换占位符）。
- `src/mini_agent/workflow/runner.py`：`_execute_step()` 收尾处加"若 `step.output_file` 设置，写入 session output_dir"的统一逻辑。
- 新建 `next_doc/workflow_authoring_guide.md`：写规范文档本身。

---

## B. 新增 `python_step` Executor

### B1. `PyStepContext`（新文件 `src/mini_agent/workflow/py_context.py`）

```python
@dataclass
class PyStepContext:
    session_dir: Path
    output_dir: Path
    step_id: str
    inputs: dict[str, StepResult]      # 上游 step 结果，key 为 step_id
    llm: "PyStepLLM"                    # 见 B2
    run_agent_turn: Callable[..., str]  # 见 B3
    params: dict                        # workflow.yaml 里 step 级 params 透传（脚本自定义参数）
```

### B2. `PyStepLLM`：脚本里调用 LLM 的入口，直接包一层 `LLMHelper`

```python
class PyStepLLM:
    def __init__(self, helper: LLMHelper): self._helper = helper

    def ask(self, prompt, *, system="", max_retries=3,
            override_model=None, override_provider=None) -> str:
        return self._helper.ask(prompt, system=system, max_retries=max_retries,
                                 override_model=override_model,
                                 override_provider=override_provider)

    def ask_json(self, prompt, *, system="", schema_hint="", max_retries=3) -> dict:
        """约定返回 JSON，内部用 json_repair 兜底解析（复用现有
        evolution/judge 判定里已经在用的 json_repair 依赖），解析失败按
        max_retries 重试并把上次的解析错误追加进 prompt 里提示模型修正。"""
```

**不新建 provider/重试/fallback 逻辑，`PyStepLLM` 只是 `LLMHelper` 的窄接口封装**，保证 `python_step` 脚本用到的 LLM 能力和主 Agent、`skill_agent`、evaluator 走的是同一套 client_pool（同样支持 `/model` 切换、同样的多 key 轮转 fallback）。

### B3. `run_agent_turn`：用于"需要 agent 判断力而非单次问答"的场景

```python
def run_agent_turn(prompt: str, *, skill_name: Optional[str] = None,
                    max_turns: int = 6) -> str:
    """临时起一个最小 Agent（复用 SkillAgentStepExecutor 里已有的构造逻辑，
    抽成 runner._spawn_minimal_agent(step, skill_name) 共享函数，
    python_step 和 skill_agent executor 都调用它，避免逻辑重复两份）。"""
```

抽取动作：把 `executors.py::SkillAgentStepExecutor.execute()` 里"构造 step_cfg → PermissionGuard → Agent → 激活 skill → run_turn"这段逻辑提到 `runner.py` 里做成 `WorkflowRunner._spawn_minimal_agent(step, prompt, skill_name=None, max_turns=None)`，`SkillAgentStepExecutor` 和新的 `python_step` 里的 `ctx.run_agent_turn` 都调用它。**这是本计划里唯一一处对现有代码的重构（消除重复），其余都是新增。**

### B4. `PythonStepExecutor`（`executors.py` 新增类 + 注册）

- 执行方式：子进程隔离。`runner` 侧起一个 `subprocess.Popen([sys.executable, "-m", "mini_agent.workflow.py_step_runner", ...])`，通过临时文件/stdin 传 `script_path` + 序列化后的 `inputs`/`params`/路径信息；子进程内 `runpy.run_path(script_path)` 拿到 `run` 函数执行。
- **LLM 调用怎么跨进程**：子进程内的 `ctx.llm` 不直接持有 `LLMClientPool`（那是主进程 Agent 的状态），而是通过一个进程内启的本地回环 HTTP 服务（`runner` 侧在派发 `python_step` 前临时起一个只在本次 step 执行期间存活的 `127.0.0.1:<random port>` 小服务，代理到主进程的 `LLMClientPool`），`PyStepLLM.ask()` 实际是对这个本地服务发 POST。这样保证：① 多个脚本进程共享同一个 `LLMClientPool` 的 key 轮转/fallback 状态（不会各自建连接池导致 key 轮转状态不同步）；② 子进程崩溃不影响主进程的 pool 状态。
  - 复杂度权衡：如果这一步实现成本觉得太高，可以先做**简化版**——子进程直接用 `LLMHelper.from_config(app_cfg)` 独立建一条 pool（牺牲"状态跟随主 Agent /model 切换"这一条，但实现简单很多，先跑通再优化）。**建议先做简化版验证流程可行，再决定要不要上代理服务。**
- 超时/取消：复用 `ScriptStepExecutor` 已有的 `timeout` + `CREATE_NEW_PROCESS_GROUP`（Windows）/`start_new_session`（Unix）进程组管理方式，保证 watchdog 能杀掉整个子进程树。
- 返回值处理：子进程 stdout 最后一行约定为 JSON 结果包（`{"ok": true, "output": ..., "output_is_json": bool}`），失败时非 0 退出码 + stderr，`PythonStepExecutor.execute()` 里按 `ScriptStepExecutor` 同样的模式抛 `RuntimeError` 携带 stdout/stderr，走现有的 retry_on_error/质检门逻辑，不用改 runner 主循环。
- 默认关闭开关：新增 `cfg.workflow.python_step_enabled`（默认 `False`，语义和 `script_step_enabled` 一致——防止分享出去的 workflow YAML 变成任意代码执行入口）。

**改动/新增文件清单（B 部分）**：
- 新建 `src/mini_agent/workflow/py_context.py`：`PyStepContext`/`PyStepLLM`。
- 新建 `src/mini_agent/workflow/py_step_runner.py`：子进程侧入口（`runpy` 执行脚本、组装 `ctx`、序列化结果输出到 stdout）。
- `src/mini_agent/workflow/executors.py`：新增 `PythonStepExecutor`，注册进 `_EXECUTORS["python_step"]`；把 `SkillAgentStepExecutor` 里构造 Agent 那段代码抽到 `runner.py::_spawn_minimal_agent`。
- `src/mini_agent/config.py`（或 workflow 配置所在处）：加 `python_step_enabled` 开关，风格对齐 `script_step_enabled`。
- `src/mini_agent/workflow/schema.py`：`validate()` 补充 `python_step` 类型必填 `script_path` 的校验。

---

## C. 批量过滤：一次 LLM 调用处理多条数据

现状问题：如果第 3 步逐条问题调用一次 `ask()`，10~30 个候选问题就是 10~30 次串行/并行请求，延迟和 token（每次都要重复问题背景/判断标准的 system prompt）都浪费。

改法：`steps/03_filter.py` 内部做**分批（batch）+ 结构化 JSON 输出**，而不是逐条判断：

```python
# steps/03_filter.py
def run(ctx: PyStepContext) -> dict:
    candidates = json.loads(ctx.inputs["search_zhihu"].output)["questions"]
    doc_summary = json.loads(ctx.inputs["analyze_doc"].output)

    BATCH_SIZE = 15   # 经验值：单批过多会导致模型漏判/输出截断，需要结合
                       # 实测模型的稳定输出长度调整，作为脚本内常量集中管理
    kept = []
    prompt_tmpl = load_prompt_file(ctx, "prompts/03_filter_batch.md")

    for batch in chunk(candidates, BATCH_SIZE):
        prompt = prompt_tmpl.format(
            doc_summary=json.dumps(doc_summary, ensure_ascii=False),
            questions_json=json.dumps(batch, ensure_ascii=False, indent=2),
        )
        result = ctx.llm.ask_json(
            prompt,
            schema_hint='{"decisions": [{"id": "...", "keep": true/false, "reason": "..."}]}',
            max_retries=3,
        )
        decisions = {d["id"]: d for d in result.get("decisions", [])}
        for q in batch:
            d = decisions.get(q["id"])
            if d and d.get("keep"):
                kept.append({**q, "filter_reason": d.get("reason", "")})

    return {"kept_questions": kept, "total_input": len(candidates), "total_kept": len(kept)}
```

`prompts/03_filter_batch.md` 里明确要求"逐条给出 id + keep + reason 的 JSON 数组，不要遗漏任何一条 id"，并在 prompt 里把"如何算符合要求"的判断标准写清楚（文档主题相关性、问题是否适合作为答案发布、是否已有大量高质量回答导致不值得再答等），这部分判断标准本身就是需要 agent 理解力的地方，用 prompt 承载，不写死成 python 规则。

**批大小与漏判保护**：`ask_json` 返回的 `decisions` 数量如果明显少于 `batch` 数量（比如漏了超过 20%），脚本里应该把这批**打散成更小的子批重试**（而不是直接丢弃漏判的问题），避免"因为模型输出被截断而误判为不符合要求"。这个降级重试逻辑写在 `steps/03_filter.py` 里，不需要框架层支持。

如果候选问题数量本身不大（比如 <20），可以退化成一批打完；如果候选很多（>100），批大小和是否要并发发多批请求（`ThreadPoolExecutor` 并发调用 `ctx.llm.ask_json`，`LLMClientPool` 本身支持多 key 轮转，天然能扛并发）是脚本内部的效率优化，不影响 workflow 规范层面的设计。

---

## D. 知乎发布 workflow：完整目录

```
.agent/workflows/zhihu_content_publish/
├── workflow.yaml
├── prompts/
│   ├── 01_analyze_doc.md
│   ├── 02_search_zhihu.md
│   └── 04_enrich_questions.md
├── steps/
│   ├── 01_analyze_doc.py
│   └── 03_filter.py
│       └── prompts/03_filter_batch.md  # 见上，与 03_filter.py 配套
```

`workflow.yaml`（骨架，无内联长文本）：

```yaml
name: zhihu_content_publish
description: 分析文档→知乎搜索候选问题→批量LLM筛选→补全详情
version: "1.0"
mode: interactive

defaults:
  model: null            # 继承全局默认，各 step 可覆盖
  retry_on_error: 1

steps:
  - id: analyze_doc
    type: python_step
    script_path: steps/01_analyze_doc.py
    output_file: doc_analysis.json
    timeout: 120

  - id: search_zhihu
    type: skill_agent
    skill_name: browser-cdp
    prompt_file: prompts/02_search_zhihu.md
    depends_on: [analyze_doc]
    output_file: search_results.json
    max_turns: 20
    timeout: 900

  - id: filter_questions
    type: python_step
    script_path: steps/03_filter.py
    depends_on: [analyze_doc, search_zhihu]
    output_file: filtered_questions.json
    timeout: 300

  - id: enrich_questions
    type: skill_agent
    skill_name: browser-cdp
    prompt_file: prompts/04_enrich_questions.md
    depends_on: [filter_questions]
    output_file: final_result.json
    max_turns: 30
    timeout: 1200
```

`steps/01_analyze_doc.py`（示意，单次 LLM 调用即可完成，不需要批量）：

```python
def run(ctx: PyStepContext) -> dict:
    doc_path = ctx.params["doc_path"]      # workflow 输入参数，运行时通过
                                            # run_workflow(inputs={"doc_path": ...}) 传入
    text = Path(doc_path).read_text(encoding="utf-8")
    prompt_tmpl = load_prompt_file(ctx, "../prompts/01_analyze_doc.md")
    result = ctx.llm.ask_json(
        prompt_tmpl.format(doc_text=text[:8000]),
        schema_hint='{"summary": "...", "topic": "...", "search_keywords": ["...", "..."]}',
    )
    return result
```

`prompts/02_search_zhihu.md` / `prompts/04_enrich_questions.md`：内容基本就是你原始需求里第 2、4 步的描述，搬进独立文件即可，占位符用 `{analyze_doc.output}` / `{filter_questions.output}`。

`search_zhihu` / `enrich_questions` 为什么仍用 `skill_agent` 而不是 `python_step`：这两步是真实浏览器交互（页面结构会变、需要临场应对弹窗/滚动加载/反爬），属于"需要 agent 随机应变"的场景，python 脚本硬编码选择器稳定性反而更差；`analyze_doc`/`filter_questions` 是纯粹的"给定输入产出结构化 JSON"，用 `python_step` 换取流程稳定性和批量效率。

---

## E. browser-cdp 修复（对应你反馈的两个具体问题）

1. **"识别不到已启动的调试浏览器"**：`browser_launch.py::cmd_dedicated`/`cmd_ensure` 里把"registry 记录"和"端口真实探测"统一收口成一个函数：先 `is_debug_port_alive(host, port)` 真实探测，探测成功直接 attach 返回；只有探测失败时才看 registry 决定是否清理锁文件后重启。不要出现"registry 认为在跑但没有实探"或"实探能连但代码走了重新创建分支"的不一致路径。
2. **"登录状态记不住"**：本质是 `--dedicated` 模式下没有每次固定传相同 `--name`（对应固定 `profile_dir`）。整改动作：
   - 在 workflow 级别（`workflow.yaml` 的 `defaults` 或专门的 `browser` 配置块）固定一个 `browser_profile_name: zhihu_session`，同一个 workflow 的所有 `skill_agent` 调 browser-cdp 时统一传这个 name，而不是每次临时决定。
   - `SKILL.md` 里把"必须固定 `--name`"写成硬性前置条件，而不是可选参数说明。
   - 加一个 `profile_dir/.mini_agent_lock.json`（pid+port+启动时间），启动前先读它做真实探测，比纯内存/纯 registry 更可靠，尤其 workflow 并行跑多个 step 时避免竞争创建多个实例。

这部分改动集中在 `.claude/skills/browser-cdp/browser_launch.py` 和 `SKILL.md`，不涉及 workflow 核心代码，可以和 A/B/C 并行推进。

---

## F. 执行顺序与验收（按依赖关系排列，每步都要跑通再进下一步）

| 顺序 | 任务 | 产出 | 验收方式 |
|---|---|---|---|
| 1 | A1/A2/A3 schema 改动 | `schema.py`/`store.py` 加字段，`validate()` 加规则 | 跑现有 workflow 相关测试（`test_cases/workflow_test.md` + 单测），确认向后兼容不破坏 |
| 2 | B3 重构：抽取 `_spawn_minimal_agent` | `runner.py` 新方法，`SkillAgentStepExecutor` 改为调用它 | 现有 `skill_agent` 类型 workflow 回归跑一遍，行为不变 |
| 3 | B1/B2/B4：`python_step` 简化版（子进程独立建 pool，不做本地代理服务） | `py_context.py`/`py_step_runner.py`/`executors.py` 新增 | 写一个最小 workflow（一个 `python_step` 调 `ctx.llm.ask()` 返回文本）跑通 |
| 4 | E：browser-cdp 探测统一 + 固定 `--name` 约定 | `browser_launch.py` 改动 + `SKILL.md` 更新 | 手动验证：先手动开一个调试浏览器，跑 `--ensure` 能正确 attach 而不新建；两次调用同一 `--name` 能复用登录态 |
| 5 | D：知乎 workflow 落地（4 个 step + prompts + steps 脚本） | `.agent/workflows/zhihu_content_publish/` 全套文件 | 用一篇真实文档跑全流程，检查 4 个 `output_file` 都按约定文件名落在 session output 目录 |
| 6 | C：批量过滤脚本 + 漏判重试保护 | `steps/03_filter.py` + `prompts/03_filter_batch.md` | 造 30+ 候选问题的测试数据，对比逐条调用 vs 批量调用的耗时和 token 用量 |
| 7（可选，视 3 的简化版效果决定要不要做） | B4 的本地代理服务版本（子进程共享主进程 `LLMClientPool` 状态） | 性能/一致性有明显问题时再做 | — |

---

## G. 风险与开关小结

- `python_step_enabled` 默认 `False`，风格对齐 `script_step_enabled`，防止分享出去的 workflow 变成任意代码执行入口。
- `prompt_file`/`script_path` 相对路径解析要严格限制在 workflow 目录内（防止 `../../` 路径穿越），复用 `resource_bundle.py` 已有的路径安全检查逻辑（若尚无，此次一并补上）。
- 批量过滤存在"漏判/误判"的风险，通过 F 表第 6 步的对比测试 + 小批重试兜底来控制，不追求一次性完美，先跑通验证批量收益是否达到预期（延迟/token 下降但准确率不明显下降），再决定默认批大小。
