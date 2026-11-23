# article_garden P0 实施记录（骨架与多文章库）

- **关联方案**：`next_doc/article_garden_external_project_plan.md`（§14 P0、§19、§20）
- **完成日期**：2026-11-23
- **状态**：已完成，等待确认后进入 P1

## 1. 范围（与方案 §14 P0 对齐）
项目目录、`project.yaml`/entrypoints、文章库注册表与切换器、新建向导（含章程模板）、章程、两层设置、LLM provider（仅就绪检查）、日志与取消、health、看板框架与待办收件箱空壳。

## 2. 关键实现与决定
| 事项 | 做法 | 理由 |
|---|---|---|
| 多库隔离 | `DomainContext.path()` 校验结果仍在库根内；页面只能拿到当前库的 ctx | 从结构上避免串库与 `..` 越界 |
| 注册表 | `data/domains.json` 原子写；损坏时抛 `DomainError` 并保留原文件 | 不把损坏当“空”，否则后续写入会覆盖注册表 |
| 删除 | 必须输入库名；先导出 zip 备份，成功后才删除 | 不可撤销操作的双保险 |
| 设置分层 | 覆盖文件稀疏；`automation` 只允许库级，`global_*` 只允许全局级；非法值回退默认并告警，读取不抛异常 | 保守 opt-in；新库不继承其它库的放权 |
| 章程 | `normalize_charter` 容错 + 保存前备份 `.bak`；`scope.out` 优先于 `scope.in` | 手改文件不会让看板打不开 |
| 看板测试 | Streamlit `AppTest`（脚本真实执行）；`AppTest` 不支持按名切换可调用页面，故页面级测试用 `from_function` 直接渲染页面函数，整站用 `from_file` | 无浏览器环境下的最大覆盖 |
| 复制自 article_to_video | `cancel.py / joblog.py / netproxy.py`、`providers/text.py` 结构 | 自包含，不跨项目 import |

## 3. 验证
- `pytest`：64 项通过（文章库 20、章程 7、设置 14、入口 4、看板冒烟 19）。
- 变异检查 5 项全部使测试变红：路径越界校验、全局层禁止库级项、删除确认、整数类型校验、范围外优先。
- `project.yaml` 经 mini_agent 的 `load_manifest` 校验通过。

## 4. 与方案的偏差 / 未做
- 方案 §19.2 的顶栏“今日预算”未做——预算账本属于 P7；P0 顶栏只显示文章库、挡位、后台任务状态。
- 导航角标（待办数量）未做——待办来源在 P4 之后才存在。
- 新建向导中的“章程草稿由文章反推”（§21-1）属于 P1（需要摘要）。

## 5. 已知限制
- 未在真实浏览器中点过看板；未在真实模型环境验证“就绪检查”。
- 浏览器本地存储不依赖（当前库只用 session_state + URL 参数）。
