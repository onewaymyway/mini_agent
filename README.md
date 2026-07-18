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
- `src/mini_agent/perception/library_index.py`（`consolidate()` 新增步骤 5b；`source_kind` 透传）
- `src/mini_agent/history/compression.py`（compact 阶段解析并入队 entities/facts）
- `src/mini_agent/evolution/outcome_tracker.py`（新增 `_write_eval_success_experience`）
- `src/mini_agent/cli/commands/wiki.py`（新增 `/wiki stats` 子命令）
- `src/mini_agent/storage/paths.py`（新增 `world_candidates_pending_path`）
- `src/mini_agent/config/models.py`（新增 `CompressConfig.extract_world_model`）
- `src/mini_agent/prompts/system/compress_summarizer.md` / `src/mini_agent/prompts/user/compress_summary_request.md`（compact 输出 schema 扩展 entities/facts）

## 验证方式

1. 全部改动文件已通过 `py_compile` 语法检查。
2. 端到端功能脚本验证过 `parse_world_response → queue_entities/queue_facts →
   consolidate_pending → compute_stats` 全链路，以及 `decision_writer` 更新分支
   `source_kind` 保留的回归测试。
3. 项目自带测试 `tests/test_outcome_tracker.py`（5 passed）、
   `tests/test_selective_compression.py`（21 passed）、
   `tests/test_exploration_outcome_recording.py`、
   `tests/test_negative_outcome_downweighting.py` 全部保持通过，无新增失败。

## 快速体验新增能力

```bash
# 查看 wiki 内容来源分布（P0）
/wiki stats
```

改进计划全文见随附的 `wiki式知识库改进计划.md`（已更新执行状态）。
