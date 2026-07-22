# Workflow 体系 P9 候选池 · 第一批实施记录

对应思考稿：`next_doc/workflow_system_next_directions.md` §6 里标为
"现在就能做、互相独立、无需等待数据积累"的四项：1a / 3 / 1b / 2，
按文档建议顺序（1a → 3 → 1b → 2）依次实施。§4（主动感知与建议）、
§5（权限与信任模型）按原文档结论暂不启动，本轮未涉及。

## 新增文件

| 文件 | 作用 |
|---|---|
| `src/mini_agent/workflow/git_integration.py` | [P9-2] workflow 定义文件的 git 集成：`is_git_repo()` 轻量探测、`save_hint()` 保存后的 commit 提示文案、`git_log_for_workflow()`（复用 `git log --oneline`）、`git_diff_for_workflow()`（`git diff` + 一层 step 级别结构化摘要）。全部只读或只打印提示，不做任何写入/自动 commit |
| `next_doc/workflow_system_p9_implementation_record.md` | 本文档 |

## 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/workflow/api_helpers.py` | **[P9-1a]** 新增 `get_workflow_stats(cfg, name)`：聚合 `list_workflow_runs()` 已落盘的 `WorkflowSession` 历史数据，返回 `total_runs`/`success_rate`/`step_stats`（`avg_duration`/`avg_score` 0-100/`fail_rate`/`avg_retries_used`）/`condition_stats`（`true_rate` = 未被跳过的比例）。纯读取聚合，不改动执行逻辑，不产生新落盘数据。**[P9-1b]** 将原 `preview_workflow(cfg, name, inputs)` 拆分为 `preview_workflow_def(cfg, wf, inputs)`（直接接受内存中的 `WorkflowDef` 对象）+ 保留原签名的 `preview_workflow` 作为"按名读盘后委托"的薄包装，使得"刚生成、还未保存"的 workflow 也能跑一次 dry-run。同时用 `schema.condition_referenced_names()` 替换原来"含 `.` 就当作运行时依赖"的粗糙判断，让只引用 `inputs.xxx` 的 condition 也能被静态求值（跟 P9-3 的 `inputs` 命名空间语义保持一致） |
| `src/mini_agent/workflow/tools.py` | 新增 `get_workflow_stats` 工具（LLM 可调用，Markdown 格式输出）。`generate_workflow`/`build_workflow_from_summary` 新增 `_format_dry_run_preview()` 辅助函数并在生成成功后调用（**P9-1b**），把并发分批 + condition 求值结果追加进返回文本；dry-run 本身失败不阻塞 YAML 展示（`log_exception` 吞掉异常）。`save_workflow` 保存成功后调用 `git_integration.save_hint()` 追加 git commit 提示（**P9-2**，非 git 仓库时不显示，不打扰用户）。模块顶部工具清单 docstring 同步更新 |
| `src/mini_agent/workflow/schema.py` | **[P9-3]** 新增模块级公共函数 `condition_referenced_names(condition)`（ast 静态解析 condition 表达式里所有 `xxx.yyy` 属性访问的顶层名字 `xxx`，语法错误返回空集合）。`WorkflowDef.validate()` 新增一轮 condition 静态一致性检查：对每个有 `condition` 的 step，用该函数抽取引用的 step id，检查是否存在（不存在报错）、是否在该 step 的 `depends_on`（直接或传递闭包）里（不在报错，`inputs` 除外——`inputs` 是运行时始终可见的外部参数，不受依赖约束） |
| `src/mini_agent/workflow/runner.py` | **[P9-3]** `WorkflowRunner._eval_condition()` 新增 `inputs: Optional[dict] = None` 参数，在求值命名空间里增加 `ns["inputs"] = SimpleNamespace(**inputs)`，使 condition 可以写 `inputs.env == 'prod' and check.passed` 这类"外部输入 + step 结果"的组合表达式；`_run_one_step()` 调用处同步传入 `inputs`。`inputs` 里存在非法 Python 标识符 key 时静默退化为空 `SimpleNamespace`（不影响其它 step 引用的求值） |
| `src/mini_agent/api/routes.py` | 新增 `GET /v1/workflows/{name}/stats` 路由（**P9-1a**），包装 `api_helpers.get_workflow_stats`；路由清单 docstring 同步更新 |
| `src/mini_agent/cli/commands/workflow_cmd.py` | 新增三个子命令：`/workflow stats <name>`（**P9-1a**，等价于 `get_workflow_stats` 工具）、`/workflow history <name>`（**P9-2**，包装 `git_log_for_workflow`）、`/workflow diff <name>`（**P9-2**，包装 `git_diff_for_workflow`）。用法提示和子命令分发表同步更新 |

## 与思考稿的对应关系 / 实现时的取舍

1. **1a（执行历史汇总统计）**：思考稿里给出的字段（`total_runs`/`success_rate`/
   `step_stats`/`condition_stats`）原样落地，`avg_score` 额外统一换算成 0-100
   （`StepResult.score` 内部按 0-1 存储，跟 `runner._resolve_prompt` 里
   `int(score*100)` 的换算口径保持一致，避免用户看到两套不同量纲的评分）。
2. **1b（生成后自动 dry-run）**：思考稿设想"在生成结果里追加一段 dry-run 输出"，
   实现时发现原 `preview_workflow()` 强制走 `store.load(name)`，而生成阶段
   workflow 还没保存——因此先把纯计算部分拆成 `preview_workflow_def()`
   接受对象本身，`preview_workflow()` 变成"按名读盘 + 委托"的薄包装，
   两个使用场景（已保存工作流的 `/preview` 接口 / 刚生成还未保存的
   dry-run）共用同一份核心逻辑，没有另起一套。
3. **3（condition 增强）**：思考稿点出的两个缺口——"引用不到 inputs"和
   "写错了要等运行期才暴露"——都按建议的最小改动方案落地：`inputs` 作为
   独立命名空间对象（不摊平进顶层，避免跟 step id 同名冲突）；静态检查
   只做 ast 级别的引用一致性核对，不真的 `eval`，符合思考稿"不需要重做
   DSL，现有沙箱 eval 够用"的结论。检测到未声明依赖时报错而不是警告，
   因为这类笔误如果放过，运行时的表现是"这步被跳过了"而非"表达式有问题"，
   等价于把一个明确的配置错误伪装成了业务逻辑判断结果，误导性比直接拒绝
   保存更大。
4. **2（git 集成）**：`save_workflow` 只提示不自动 commit（思考稿明确
   "自动 commit 属于代用户做决定，违反 1.3 提到的原则"）；`/workflow diff`
   在原始 `git diff` 之上包了一层 step 级别的结构化摘要——通过分别对
   `git show HEAD:<path>` 和工作区当前文件做 YAML 解析、按 step id 对比
   字段差异实现，解析失败（比如 HEAD 版本还不存在这个文件）时退化为只有
   原始 diff，不影响主要信息的展示。这是思考稿里明确标注的"优先级最低"
   的子项，实现上控制在最小可用范围，没有做逐字段的语义化 diff（比如
   prompt 文本变化没有做词级 diff，只显示"从 A 改成了 B"）。

## 验证记录

- 所有新增/修改文件通过 `python3 -m py_compile` 语法检查。
- `pytest tests/test_session_to_workflow.py tests/test_workflow_directory_mode.py
  tests/test_workflow_parallel.py tests/test_workflow_step_session_dir.py
  tests/test_workflow_step_types.py` 共 98 个用例全部通过（含
  `_eval_condition` 签名变化后 `test_condition_skip_still_respected_in_parallel_layer`
  用 `patch.object(..., return_value=False)` 打桩的用例，不受新增参数影响）。
- 手工验证 `condition_referenced_names()` 与 `validate()` 的静态一致性检查：
  构造"引用未声明依赖的 step"和"引用不存在的 step"两种笔误，均能在
  `validate()` 阶段被拦下并给出定位到具体 step/表达式的报错。
- 手工验证 `_eval_condition()` 的 `inputs` 命名空间：
  `"inputs.env == 'prod' and check.passed"` 在 `inputs={"env": "prod"}` 时求值
  为 `True`，`inputs={"env": "dev"}` 时为 `False`。
- 手工端到端验证 `get_workflow_stats()`：构造一次 `WorkflowSession` 落盘记录，
  统计出的 `total_runs`/`success_rate`/`step_stats`/`condition_stats` 与预期
  一致（含 `avg_score` 的 0-100 换算）。
- 手工验证 `preview_workflow_def()` 对内存中（尚未保存）的 `WorkflowDef`
  正确输出并发分批和 condition 预览文案。
- 手工在临时 git 仓库中验证 `git_integration.py` 三个函数：`is_git_repo`
  正确识别、`git_log_for_workflow` 返回提交历史、`git_diff_for_workflow`
  正确生成 step 级别摘要（"步骤 'a' 的 condition 从 None 改成了 'true'"）
  + 原始 diff、`save_hint` 生成预期的提示文案。

## 未涉及/暂不做的部分（与思考稿结论一致）

- §4 主动感知与建议：思考稿明确"应等 P8 上线并观察一段真实使用之后再评估"，
  本轮未启动。
- §5 权限与信任模型升级：思考稿明确"只在 workflow 分享/市场出现时才紧迫"，
  本轮未启动，仅作为前置记录保留在原文档里。
- `condition` 的自定义 DSL 替代方案：思考稿 §3.3 已明确排除，本轮未涉及。
- `/workflow diff` 的结构化摘要目前只到"字段级别"（step 的哪个字段变了），
  未对 `prompt` 等长文本字段做词级 diff——思考稿本身把这条标为"优先级
  最低"，且原始 diff 一并展示，不影响信息完整性。
