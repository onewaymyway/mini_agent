# 使用说明

这是本次针对 `mini_agent` wiki 记忆机制的**改动文件包**，目录结构与原项目一致
（`src/mini_agent/...`），直接覆盖到原项目对应路径即可（新文件是新增，其余是
对既有文件的原地修改，非整份重写）。

## 文件清单

新增文件：
- `src/mini_agent/history/world_extraction.py`
- `src/mini_agent/wiki/world_writer.py`
- `src/mini_agent/wiki/experience_writer.py`
- `src/mini_agent/wiki/stats.py`

修改文件：
- `src/mini_agent/wiki/migration.py`（`mirror_entity` 新增 `source_kind` 参数）
- `src/mini_agent/wiki/decision_writer.py`（修复更新分支丢失 `source_kind` 的 bug；新建页面打上 `source_kind=decision`）
- `src/mini_agent/wiki/__init__.py`（导出新模块）
- `src/mini_agent/perception/library_index.py`（`consolidate()` 新增步骤 5b；`source_kind` 透传；步骤 7 透传 `wiki_embed_call` 给 `consolidate_topics`）
- `src/mini_agent/wiki/topics.py`（新增语义聚类候选生成 `find_semantic_topic_candidates`，`consolidate_topics` 新增 `embed_call` 参数，与既有 tag+密度路径并存）
- `src/mini_agent/history/compression.py`（compact 阶段解析并入队 entities/facts）
- `src/mini_agent/evolution/outcome_tracker.py`（新增 `_write_eval_success_experience`：自我进化正面判定 → 经验页面）
- `src/mini_agent/agent/reminders_correction.py`（新增 session 级纠正计数器 `_session_correction_count`）
- `src/mini_agent/agent/lifecycle.py`（三处 session 边界重置纠正计数器）
- `src/mini_agent/agent/profile.py`（session 结束、摘要生成后，若无纠正且有工具调用，写入会话级正面经验）
- `src/mini_agent/cli/commands/wiki.py`（新增 `/wiki stats` 子命令）
- `src/mini_agent/storage/paths.py`（新增 `world_candidates_pending_path`）
- `src/mini_agent/config/models.py`（新增 `CompressConfig.extract_world_model`）
- `src/mini_agent/prompts/system/compress_summarizer.md` / `src/mini_agent/prompts/user/compress_summary_request.md`（compact 输出 schema 扩展 entities/facts）

## 验证方式

1. 全部改动文件已通过 `py_compile` 语法检查。
2. 端到端功能脚本验证过 `parse_world_response → queue_entities/queue_facts →
   consolidate_pending → compute_stats` 全链路、`decision_writer` 更新分支
   `source_kind` 保留的回归测试、以及 `experience_writer` 两条来源
   （`experience_success` / `experience_session_reflection`）的写入。
3. 对 `tests/` 目录做了修改前后的全量比对：138 failed / 1766 passed / 12
   errors，两次结果完全一致（失败用例集合逐条比对相同），确认未引入新的
   回归；失败均为沙盒环境缺少可选依赖（如 `rich`/`json_repair`/`pydantic`/
   `uvicorn`）或与本次改动无关的既有 mock 问题。
4. `wiki/topics.py` 的语义聚类候选路径（P3）额外用端到端脚本验证：4 篇
   embedding 相近但彼此无 tag/强链接的页面被正确聚类生成专题页，2 篇不
   相关页面未被误聚，且重复运行不会对同一批页面重复生成（幂等性验证）。

## 快速体验新增能力

```bash
# 查看 wiki 内容来源分布（P0）
/wiki stats
```

改进计划全文见随附的 `wiki式知识库改进计划.md`（已更新执行状态）。
