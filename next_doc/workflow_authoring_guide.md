# workflow 编写规范：prompt 外置 + python_step 脚本外置

状态：已落地（规则由 `schema.py::WorkflowDef.validate()` 做 warning 级提示，非强制阻断）
关联：next_doc/workflow_python_step_and_zhihu_publish_plan.md §A

## 规则

1. **prompt 一律外置到 `prompts/*.md`**，`workflow.yaml` 的 `steps[].prompt` 字段不写超过
   3 行的内联文本。超过阈值（5 行）的内联 prompt，`WorkflowDef.validate()` 会给出 warning
   （不阻断保存/运行，向后兼容旧 workflow），建议改用：

   ```yaml
   - id: analyze_doc
     type: agent
     prompt_file: prompts/01_analyze_doc.md   # 相对 workflow 目录解析
   ```

   `prompt_file` 支持和内联 `prompt` 完全相同的占位符语法（`{step_id.output}` 等），加载阶段
   先读文件内容再走占位符替换，两者语义等价，只是来源不同。

2. **`python_step` 的脚本代码外置到 `steps/*.py`**，`workflow.yaml` 只写 `script_path`：

   ```yaml
   - id: filter_questions
     type: python_step
     script_path: steps/03_filter.py
     output_file: filtered_questions.json
   ```

   脚本必须暴露 `def run(ctx: PyStepContext) -> str | dict:` 入口函数。`ctx` 提供的接口见
   `src/mini_agent/workflow/py_context.py`：`ctx.llm.ask()/ask_json()`（LLM 调用，转发到
   `LLMHelper`）、`ctx.run_agent_turn()`（临时起一个最小 Agent 处理需要判断力的子任务）、
   `ctx.params`（自定义参数）、`ctx.input_output()/input_json()`（读上游 step 输出）、
   `ctx.load_prompt_file()`（读 `prompts/*.md`）、`ctx.write_output()`（往 output_dir 落盘
   中间产物）。

3. **每个 step 声明 `output_file`**，产出统一由 runner 落盘到当前 workflow session 的
   `output/` 目录（`.agent/workflow_sessions/<wfs_id>/output/<output_file>`），不管这个 step
   是哪种 executor 类型产生的输出，都不需要 agent prompt 或脚本自己拼路径。

## 标准目录结构（目录化 workflow）

```
<workflow_name>/
├── workflow.yaml          # 骨架：id/type/depends_on/prompt_file/script_path/output_file 等
├── prompts/
│   └── *.md                # 每个 agent/skill_agent step 的 prompt
├── steps/
│   └── *.py                 # 每个 python_step 的脚本代码
├── agents/                  # 已有：本地角色 profile（如果用到）
└── skills/                  # 已有：本地 skill（如果用到）
```

## 安全边界

- `prompt_file`/`script_path` 的相对路径解析严格限制在 workflow 目录内（`store.py` 里
  `_resolve_prompt_files()`/`_resolve_script_paths()` 都会做 `relative_to()` 路径穿越校验，
  越界的会被忽略并打印警告，交由 `validate()`/执行前的必填校验再次拦截）。
- `python_step` 默认被 `cfg.workflow.python_step_enabled=False` 关闭（语义与
  `script_step_enabled` 一致），防止分享出去的 workflow YAML 变成任意 Python 代码执行入口。
  需要在 `agent_config.json` 里显式开启：

  ```json
  {"workflow": {"python_step_enabled": true}}
  ```

## 参考实现

`.agent/workflows/zhihu_content_publish/` 是按本规范落地的完整示例（4 个 step、4 个 prompt
文件、2 个 python_step 脚本），可以直接参考其目录结构和 `workflow.yaml` 写法。
