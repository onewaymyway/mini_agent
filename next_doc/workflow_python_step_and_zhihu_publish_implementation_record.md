# python_step + 知乎发布 workflow 实施记录

关联计划：`next_doc/workflow_python_step_and_zhihu_publish_plan.md`
状态：A/B/C/D/E 全部落地，测试通过（见下）

## 已完成

### A. workflow 规范修订
- `schema.py::WorkflowStep` 新增 `script_path`/`params`/`output_file` 字段，`from_dict`/
  `to_dict` 同步；`STEP_TYPES` 加入 `"python_step"`。
- `validate()`：`python_step` 必填 `script_path`；不再要求 `python_step` 必须有非空 `prompt`；
  内联 `prompt` 超 5 行时收集 warning 到 `wf.last_validate_warnings`（不阻断，向后兼容）。
- `store.py` 新增 `_resolve_script_paths()`，规则与 `_resolve_prompt_files()` 一致（相对
  workflow 目录解析 + 路径穿越保护）。
- 新建 `next_doc/workflow_authoring_guide.md`：prompt/脚本外置规范正式文档。

### B. `python_step` executor
- 新建 `workflow/py_context.py`：`PyStepContext`/`PyStepLLM`。`PyStepLLM.ask_json()` 用
  `json_repair` 做宽松解析 + 解析失败重试，支持代码块围栏剥离。
- 新建 `workflow/agent_spawn.py`：`build_minimal_agent()`，从原 `SkillAgentStepExecutor`
  里抽出的"构造最小 Agent"逻辑，改为不依赖 runner 实例的纯函数。
- `runner.py` 新增 `_spawn_minimal_agent()`，转发到 `agent_spawn.build_minimal_agent()`；
  `SkillAgentStepExecutor` 重构为调用它（消除重复实现）。
- 新建 `workflow/py_step_runner.py`：子进程侧入口，`runpy` 执行脚本 `run(ctx)`。
- `executors.py` 新增 `PythonStepExecutor`（子进程隔离执行，`python_step_enabled` 开关拦截，
  超时/进程组管理对齐 `ScriptStepExecutor`），注册为 `"python_step"`。
- `config/models.py` + `config/loader.py`：新增 `python_step_enabled`（默认 `False`）/
  `python_step_timeout_seconds`。
- `runner.py::_execute_step()`：`executor.execute()` 前设置 `self._current_step_results`
  （供 `PythonStepExecutor` 读取全部上游 `StepResult`）；执行成功后调用新增的
  `_write_step_output_file()`，把输出统一落到 session `output/` 目录（§A3 契约，对所有
  executor 类型通用，不止 `python_step`）。

**实现过程中发现并修正的两个问题**（值得记录，避免同类错误再犯）：
1. `PyStepLLM` 最初用 `callable(helper)` 隐式判断"传入的是 helper 实例还是构造工厂"，
   但 `LLMHelper` 实例和测试用的 `MagicMock` 都是 callable 的，导致把已构造好的 helper
   误判成工厂反复调用，触发死循环/挂起。改成显式的 `helper=` / `helper_factory=` 两个参数，
   不再做隐式类型判断。
2. `py_step_runner.py` 最初在构造 `PyStepContext` 时**立即**构建 `LLMHelper`（哪怕脚本根本
   不调用 `ctx.llm`），导致所有 `python_step`（包括纯数据搬运、不需要 LLM 的脚本）都被迫
   要求一个有效的 provider 配置才能跑。改成惰性构造（`helper_factory`），只有脚本真的调用
   `ctx.llm.ask()/ask_json()` 时才会去构建 `LLMHelper`。

### C. 批量过滤
- `.agent/workflows/zhihu_content_publish/steps/03_filter.py`：`BATCH_SIZE=15` 分批调用
  `ctx.llm.ask_json()`；漏判比例超过 `MISS_RATIO_THRESHOLD=0.2` 时拆成子批（`sub_size`
  为漏判数量的一半，下限 `MIN_SUB_BATCH=3`）递归重试，而不是直接丢弃漏判的问题。

### D. 知乎发布 workflow
- `.agent/workflows/zhihu_content_publish/`：`workflow.yaml`（骨架，无内联长 prompt）+
  `prompts/{01,02,03_batch,04}.md` + `steps/{01_analyze_doc,03_filter}.py`。
- `search_zhihu`/`enrich_questions` 两步保留为 `skill_agent`（真实浏览器交互需要临场应变），
  `analyze_doc`/`filter_questions` 用 `python_step`（纯结构化数据产出，走批量/可靠路径）。

### E. browser-cdp 稳定性修复（含一次自我纠错）
- **第一版判断有误**：最初把登录态丢失归因于"专用实例 profile 目录在项目内 `temp/` 下
  容易被清理脚本清空"，把默认目录改到了 `~/.cdp_skill/profiles/`。经进一步排查（对比
  `launch_zhihu_logged_in.py`/`zhihu_search_with_login.py`/`browser_launch.py` 三处不同
  的浏览器启动路径），这个判断不成立，已改回项目本地目录 `temp_cdp/cdp_brower_data/`。
- **真正根因**：`launch_zhihu_logged_in.py`（一个独立的知乎登录浏览器启动脚本，固定端口
  9336）拉起新 Chrome 进程前没有清理 `SingletonLock`/`SingletonSocket`/`SingletonCookie`
  这几个单实例锁文件——这几个文件在 Chrome 异常退出（被强制杀进程/崩溃）后会残留，导致
  下次启动时 Chrome 认为"另一个实例正在用这个 profile"，从而不会正常加载 cookies/session，
  表现为"重启后登录状态丢了"（profile 目录和数据其实都完好，只是没被正确加载）。
  `browser_launch.py::spawn_browser()` 本来就有这一步清理逻辑，只是这个独立脚本没有跟上。
  修复：给 `launch_zhihu_logged_in.py` 补上同样的 `_remove_stale_singleton_locks()`。
- 另外新增 profile 目录下的锁文件 `.mini_agent_lock.json`（port/pid/启动时间），作为
  `registry.json` 之外的第二条识别线索，`registry.json` 状态丢失时也能正确探测复用已有
  实例，不会误判"无可用实例"而重复创建。
- `SKILL.md` 补充醒目提示：同一任务内所有浏览器调用必须固定同一个 `--name`。
- 详见 `next_doc/browser_cdp_stability_fixes.md`（含第一版判断错误的更正说明）。

## 测试

新增：
- `tests/test_python_step.py`（11 项）：schema 字段往返、`validate()` 规则、
  `PythonStepExecutor` 开关拦截、`PyStepLLM.ask_json` 的解析/重试逻辑。
- `tests/test_python_step_subprocess_e2e.py`（2 项）：真实拉起子进程的端到端冒烟测试
  （不 mock 子进程），验证参数传递、上游输出读取、`ctx.write_output()`、异常传播。
- `tests/test_zhihu_workflow_steps.py`（6 项）：知乎 workflow 两个 `python_step` 脚本的
  业务逻辑单测（runpy 加载 + fake ctx，不依赖真实 LLM/浏览器），包括批量调用次数断言
  （32 条候选 → 3 次 LLM 调用而非 32 次）和漏判子批重试断言。

回归：
- `tests/test_workflow_directory_mode.py` 因 `SkillAgentStepExecutor` 重构，2 个测试
  （`test_unknown_skill_without_bundle_or_global_dir_raises`/`test_uses_local_skill_from_bundle`）
  改为绑定真实 `WorkflowRunner._spawn_minimal_agent` 而非纯 `MagicMock`，验证委托后行为不变。

**全部测试结果**：`pytest tests/ -k "workflow or python_step"`（跳过 2 个因缺
`fastapi`/`uvicorn` 依赖而无法收集、与本次改动无关的文件）→ **125 passed**。

## 已知取舍（记录在案，非遗漏）

- `python_step` 子进程走"独立构造 `LLMHelper`"（计划 §B4 简化版），不共享主进程
  `LLMClientPool` 的 key 轮转/fallback 运行时状态，也不跟随主 Agent 运行期 `/model` 切换。
  跨进程共享状态的本地代理服务版本留作后续可选优化（计划 F 表第 7 项），先跑通验证简化版
  是否够用。
- `run_agent_turn()` 在子进程内每次调用都会重新构造一个最小 Agent（没有做跨调用复用），
  对于同一个 `python_step` 内需要多次调用 `run_agent_turn` 的场景（比如逐条判断而非批量），
  这是可接受的性能取舍——本次知乎 workflow 的过滤步骤已经改成批量 `ask_json` 而不是逐条走
  `run_agent_turn`，不受此影响。
