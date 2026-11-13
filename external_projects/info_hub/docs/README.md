# info_hub 项目文档

info_hub 是一个独立的信息聚合项目：定时抓取多个信息源，整理成纯文本数据，生成报告，并在 Streamlit 看板中阅读、反馈，以及按兴趣推荐。

本目录是项目的使用与维护文档。更详细的设计决策与历次实施记录在仓库的 `next_doc/` 目录中（见文末“相关文档”）。

## 文档索引

| 文档 | 内容 | 适合谁读 |
|---|---|---|
| [快速开始](quickstart.md) | 安装依赖、启动看板、第一次抓取 | 第一次使用的人 |
| [架构](architecture.md) | 模块划分、数据流、目录约定 | 要改代码的人 |
| [信息源](sources.md) | 适配器类型、`source.yaml` 写法、现有信息源列表、如何添加 | 要添加或维护信息源的人 |
| [配置](configuration.md) | `settings.yaml`、`feed` 段、看板可调参数、站点与分类配置 | 要调参数的人 |
| [看板](dashboard.md) | 各页签功能与操作说明 | 日常使用的人 |
| [推荐信息流](recommendation.md) | 推荐的五段流水线、分区、反馈与评估 | 想理解推荐结果的人 |
| [运行与运维](operations.md) | 命令行、定时调度、专用浏览器、调试文件、常见问题 | 部署与排障的人 |
| [测试](testing.md) | 如何运行测试、已知的失败用例 | 改代码的人 |

## 项目边界
- **不调用 LLM 即可完整工作**。LLM 只用于看板里的“对话生成信息源”和推荐的可选增强（默认关闭）。
- **数据全部是纯文本**（JSON / JSONL / Markdown），保存在本机 `data/` 目录，不引入数据库。
- **不绕过访问限制**。需要登录的站点使用专用浏览器的登录态；遇到安全验证页时报告拦截，不做伪造或模拟。
- **完全自包含**：可整体移动到任意路径，在 mini_agent 中用 `mini-agent projects register <路径>` 注册即可。

## 相关文档
- `../PROJECT.md`：项目的总体说明与 mini_agent 外部项目接入方式（entrypoints、端口、目录约定）。
- `../../../next_doc/info_hub_external_project_plan.md`：项目的原始设计。
- `../../../next_doc/info_hub_implementation_record.md`：分阶段实施记录，包括每项的验证情况与未验证事项。
- `../../../next_doc/info_hub_recommendation_feed_design.md`：推荐信息流的完整设计（第 1–21 节）。
- `../../../next_doc/info_hub_dedicated_browser_design.md`：专用浏览器与看板相关设计。
- `../../../next_doc/info_hub_sources_expansion_plan.md`：信息源扩展计划与各源的状态。
