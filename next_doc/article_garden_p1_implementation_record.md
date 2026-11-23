# article_garden P1 实施记录（导入与单篇摘要）

- **关联方案**：`next_doc/article_garden_external_project_plan.md`（§5.1 阶段 A、§21-1 章程草稿反推、§14 P1）
- **完成日期**：2026-11-23
- **状态**：已完成，等待确认后进入 P2

## 1. 范围（与方案 §14 P1 对齐）
A：导入（预览→确认）、正文哈希与去重、增量单篇摘要、代码校验、修复循环、长文分块、文章库页面；另含 §21-1 的章程草稿反推（P0 记录中明确留给 P1）。

## 2. 新增 / 修改的文件
| 路径 | 说明 |
|---|---|
| `article_garden/library/front_matter.py` | 解析/写出 front matter；坏 YAML 整段当正文，不抛异常 |
| `article_garden/library/hashing.py` | 去空白后的 SHA-256（前 16 位） |
| `article_garden/library/catalog.py` | 文章索引 `library/index.json`：编号分配、查重、索引缺失时从文件重建 |
| `article_garden/library/importer.py` | 收集（文件/目录/zip，GBK 回退）、预览、确认导入 |
| `article_garden/digest/llm_io.py` | 去代码围栏、解析 YAML，错误信息可读 |
| `article_garden/digest/validate.py` | 摘要校验：引文逐字存在、概念名存在、枚举值、自评范围、悬空概念 |
| `article_garden/digest/summarize.py` | 提示词；`ask_validated` 修复循环；按章节分块；每篇共享修复预算 |
| `article_garden/digest/runner.py` | 增量运行、单篇隔离、摘要落盘、运行日志（joblog） |
| `article_garden/digest/charter_draft.py` | 由摘要反推章程草稿；采用前不生效；采用时备份旧章程 |
| `article_garden/domains.py` | `article_count()` 改为读文章索引 |
| `entrypoints/import_library.py` `digest.py` `draft_charter.py` | 命令行入口；后台运行时的错误同时写入运行日志 |
| `ui/library_page.py` | 文章库页：导入预览、文章表、摘要状态、单篇详情、章程草稿 |
| `project.yaml` | 登记 3 个新入口（LLM 入口为 `managed`，其余 `unmanaged`） |
| `tests/fakes.py` `test_library.py` `test_digest.py` `test_ui_library.py` | 新增 30 项测试（`test_entrypoints` 中原有用例不变） |
| `PROJECT.md` `docs/README.md` `docs/USAGE.md` | 文档更新；USAGE 为新增的使用说明 |

## 3. 关键实现与决定
| 事项 | 做法 | 理由 |
|---|---|---|
| 源文件 | 只读取字节，永远不写回源位置；入库为副本 | 避免误改用户源库（方案 Q4） |
| 去重 | 按“去空白后正文”的哈希 | 换行、缩进差异不算变化；同时查已入库与本批内部 |
| 引文校验 | 去空白与 Markdown 标记后做子串匹配，中英文标点必须一致 | LLM 常改动粗体符号与换行，但不应放过编造的引文 |
| 修复预算 | 按“每篇”共享，用尽抛 `DigestFailed` 并保留最后输出 | 方案 §8.1 第 5 步“每篇最多修复轮数” |
| 单篇失败 | 失败只写该篇记录，其它篇继续 | 方案 §14 P1 验收“坏数据能自动修复”，且不阻塞其它篇 |
| 增量判定 | 记录中的 `content_hash` 与 `digest_version` 同时匹配且上次成功 | 内容或提示词版本变化都会触发重做 |
| 长文 | >6000 字按章节分块；每块提取概念与断言，再一次元信息调用 | 避免单次输入过长导致遗漏与幻觉 |
| 章程草稿 | 写入 `drafts/`，需显式采用；采用时先备份 | 方案 Q19；草稿永远不能静默覆盖章程 |
| 后台任务 | 看板以子进程启动入口；入口的前置错误写入运行日志 | 长任务不阻塞页面；stderr 在后台模式下不可见，所以必须进日志 |
| 导入状态 | 导入文章标记 `status: published` | 既有文章视为已发布；生成文章在 P6 才有草稿流程（见 §5） |

## 4. 验证
- `pytest`：**94 项全部通过**（P0 的 63 项 + P1 新增 30 项 + 原先因缺 fastapi 而跳过的清单校验，安装依赖后已执行并通过）。
- P0 基线 63 项全部保持通过。
- 清单校验：`load_manifest` 对 `project.yaml` 校验通过，登记 5 个入口。
- 静态检查：`pyflakes` 无告警。
- 修复循环、分块、增量、单篇隔离、章程草稿的“先草稿后采用”、索引重建、损坏索引保护、CLI 预览/确认、看板页面渲染，均有对应测试。
- 测试期间发现并修复的问题（记录在此，便于复查）：
  1. `catalog` 模块导入路径写错（实际在 `library/` 下），导致看板页面报错——由 `test_ui_smoke` 捕获，已修正。
  2. 运行器的异常捕获写法有误（引用了不存在的 `joblog.Cancelled`），已改为使用 `cancel.Cancelled` 并整体重写该函数。
  3. 章程草稿模块缺少 `from __future__ import annotations`，在 3.9 下 `X | None` 注解会出错，已补充。

## 5. 与方案的偏差 / 未做
- **§9 / §19 中的预算强制执行未做**：`step_timeout_minutes`、`max_llm_calls_per_day`、`global_max_llm_calls_per_day` 目前只是设置项，**不会被强制**。统一放在 P7（预算与自动化）实现。P1 唯一真正生效的预算是“每篇修复轮数上限”。
- **看板“停止”按钮未做**：摘要以子进程后台运行，停止信号目前只在同一进程内有效；跨进程停止需要文件信号，留待 P7。
- **删除单篇未提供界面**：目前需手动删除文件并改索引（已在 USAGE 说明）。
- **§21-7（用 20 篇校准去重阈值）未做**：去重阈值与段落级相似度属于 P4/P6 的 `overlap` 校验，本期未涉及；P1 只做了“同正文哈希”的精确去重。
- **§19.3 导入预览的“拖拽/目录”交互**：目前为上传 + 本机路径输入，满足 P1 验收；更细的交互在后续迭代中打磨。
- 生成文章（`origin=generated`）与 `draft/reviewed` 状态流程属于 P6，本期未涉及。

## 6. 已知限制
- **真实模型输出质量未验证**：全部测试使用确定性的假模型。提示词在真实模型上是否稳定、修复轮数是否足够，需要用你的 20 余篇文章实际跑一次，再据结果调整提示词与阈值。
- 未在真实浏览器中点过看板（同 P0）。
- 长文分块按字数与章节切分，超长单节（无标题的超长段落）只能按段落切，可能切在论述中间；引文跨块时会被判定为不存在并进入修复，属于已知代价。
