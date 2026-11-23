# article_garden P5 实施记录（资料收集）

- **关联方案**：`next_doc/article_garden_external_project_plan.md`（§7 阶段 E、§16 风险、§14 P5、Q8、Q10）
- **完成日期**：2026-11-23
- **状态**：已完成，等待确认后进入 P6

## 1. 验收对照（方案 §14 P5）
| 验收项 | 结果 |
|---|---|
| 证据不足的选题不硬写 | 证据不充分（覆盖率 < 60%、可信来源 < 2 条、或无 A/B 级）时选题转为「已放弃」，原因写入证据包与状态历史（`test_insufficient_evidence_drops_topic_with_reason`） |
| 缓存命中不重复抓取 | 搜索与网页均按时间戳缓存；第二次调研时搜索后端调用 0 次，网络请求 0 次（`test_cached_search_and_pages_are_not_requested_again`） |

## 2. 新增 / 修改的文件
| 路径 | 说明 |
|---|---|
| `article_garden/research/search.py` | 搜索后端（包装 mini_agent web_search）、查询缓存（TTL 30 天） |
| `article_garden/research/fetch.py` | 抓取：robots 检查（带缓存）、按域名节流、超时、类型与大小限制、网页缓存；HTTP 可注入 |
| `article_garden/research/extract.py` | 正文提取：trafilatura 优先，标准库回退 |
| `article_garden/research/queries.py` | 查询生成（模型或模板）、校验、去重、限长 |
| `article_garden/research/sources.py` | 来源卡：可信度分级、摘录选择、落盘、删除 |
| `article_garden/research/pack.py` | 证据包：要点覆盖、充分性判断、写入 pack.yaml |
| `article_garden/research/runner.py` | 调研流程、中断恢复、手动资料、删除来源后的重新评估 |
| `article_garden/planning/states.py` | 状态机扩展：调研中、已调研；开始/结束/退回调研 |
| `ui/research_page.py` | 调研页（替换占位页）：证据包、来源卡（可删）、手动补充、网址抓取 |
| `entrypoints/research.py` | 调研入口；`--add-url`、`--remove-source` |
| `app.py` | 调研页改为 `ui.research_page` |
| `project.yaml` | 登记 `research`（`managed`；`allowed_domains` 保持为空，见第 5 节） |
| `tests/test_research.py` | 新增 16 项测试（全部假网络） |
| `tests/test_ui_smoke.py` | 调研页冒烟测试指向新页面 |
| `PROJECT.md` `docs/USAGE.md` `docs/README.md` | 文档更新 |

## 3. 关键实现与决定
| 事项 | 做法 | 理由 |
|---|---|---|
| 搜索 | 复用 mini_agent 的 `create_web_search_provider`；失败抛 `SearchError`，调研记录后继续 | 方案 Q10；降级为仅手动资料（§16） |
| robots | 200 按规则；404/410 允许；其它错误或 401/403 拒绝；结果缓存 | 方案 §7 第 4 步；“无法读取时保守不抓”避免误抓 |
| 节流 | 同一域名相邻请求至少间隔 1 秒（可配置的 `min_interval` 默认 1.0） | 方案 §7 第 4 步 |
| 网络代理 | `trust_env=False`，不读取系统代理 | 与 netproxy 约定一致 |
| 大小与类型 | 超过 2 MB 截断；非 HTML 跳过 | 防止下载大文件 |
| 摘录 | 只保留包含必须覆盖概念的段落，拼接不超过 `excerpt_chars_max` | 方案 §8.3 版权：只摘事实与短摘录 |
| 可信度 | 域名启发式（A：学术与官方文档；B：开源与技术社区；C：其它；D：章程 avoid 命中） | 方案 Q20 的分级；启发式仅作起点 |
| 充分性 | 覆盖率 ≥ 60%；达到最低可信度的来源 ≥ 2；至少一条 A/B | 首版阈值，方案 §21-7 之后校准 |
| 中断 | 停止或异常时选题退回调研前状态，并记录原因 | 避免卡在“调研中” |
| 手动资料 | 粘贴文本或网址；手动来源跨重新调研保留 | 方案 §7 与 §16：允许用户手动提供资料 |

## 4. 验证
- `pytest`：**152 项全部通过**（P4 的 136 项 + P5 新增 16 项）。
- `pyflakes` 无告警；清单校验通过（9 个入口）。
- 测试中修正的问题：仅有测试文件的导入与一处弱断言（看板测试原先接受占位文字），已改为检查具体控件（按钮、选择框、表格）。
- 所有网络行为（搜索、抓取、robots）通过注入的假函数测试，**没有在真实网络环境中运行过**。

## 5. 与方案的偏差 / 未做
- **没有真实网络验证**：搜索后端、真实网站的编码与重定向、robots 行为，需要在你的环境中实际跑一次确认。
- **站内优先只用互链文章**：方案 §7 第 2 步的三段式检索（FTS 召回 → 概念扩展 → 摘要精排）未做；目前只把选题的互链文章列为“库内已有”。
- **“提取候选事实”未做**：方案 §7 第 5 步的候选事实由模型抽取，首版只有摘录，抽取留给 P6 按需要处理。
- **证据不足直接放弃**：方案写“转 dropped / 回 proposed”，首版选择直接放弃（原因写明），避免同一选题反复调研。
- **trafilatura 未安装**：没有新增必需依赖；安装后会自动使用更好的正文提取。
- **`resources.allowed_domains` 留空**：调研会访问任意网站，无法列出固定域名。robots 与节流在代码中强制执行。
- **查询生成的模型路径未在真实模型上验证**（测试使用假模型）。
- 看板没有批量调研，也没有调研进度的实时条（依赖「运行日志」）。

## 6. 已知限制
- 可信度启发式可能误判，需要人工通过删除或手动补充修正。
- 网页缓存与搜索缓存的 30 天 TTL 是固定值，未放入设置页。
- 单域名节流只在同一进程内生效；多个进程同时调研同一域名时不会协调。
