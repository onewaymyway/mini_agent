# 完稿后修改流程（revision_flow）

> 设计依据：`next_doc/novel_writer_post_completion_revision_plan.md`（仓库根目录 next_doc/）。
> 本流程只在全书终审通过、书已写完之后使用，**独立于写作流水线**：不新增流水线步骤，不写
> `pipeline_state.json`，不被 worker、挡位、G5 自动重跑调用。现有章节修订、构思修订、巡检逻辑不变。

## 核心约定

- **原稿只读**：完稿快照 `v001` 只读，每个文件有 sha256 清单（`_versions/manifests/v001.json`），`verify` 可随时校验。
- **修改出新版本**：每次执行先从父版本整体复制出 `vNNN`，所有改写只写入新目录。定稿（校验 + 只读）后才成为 head。
  未成功的运行留下未定稿的派生目录，可续跑或丢弃，不影响 head。
- **两种时间语义，由作者逐次选择**：
  - `retcon` 追溯式：全书一直如此，涉及旧设定的章节全部改为新设定。
  - `from_chapter` 从第 N 章起：N 之前的章节不动；N 之后涉及设定的章节改写。N 之前若出现新设定关键词，会给出警告。
- **先预览，后执行**：变更 → 预览（受影响章节、命中词、警告、预计调用数）→ 作者确认 → 执行。未确认的预览不能执行。

## 数据布局

```
<小说目录>/
├── _versions/
│   ├── index.json            {"head": "v002", "versions": [{id, parent, kind, created_at, note}]}
│   ├── manifests/vNNN.json   文件哈希清单（baseline / derived）
│   └── vNNN/                 完整内容副本（大纲规划、正文、章节总结、一致性检查、修订记录）
└── _revision/
    ├── settings.json         可配置上限（见下）
    ├── changes/chg_*.json    变更
    ├── preview/prv_*.json    预览（confirmed 标志）
    ├── runs/run_*.json       运行记录（状态、已完成章节、LLM 调用数、日志、派生物建议）
    ├── runs/run_*.lock       运行期间的归属锁（记录持有进程号；进程结束后残留，用于识别中断）
    ├── runs/<run>/chNNN/attemptKK/out/   每次尝试的执行器产出（便于排查）
    ├── triage/tri_*.json     章节反馈分诊记录
    └── jobs/<任务>.log       实时进度日志（逐行，带时间戳）；<任务>.status.json 为 running / done / failed
```

## 可配置上限（`_revision/settings.json`，当前通过 CLI 修改，界面后续接入）

| 键 | 默认 | 含义 |
|---|---|---|
| `max_chapters_per_run` | 20 | 单次运行最多改写的章数，超出则暂停，续跑继续 |
| `max_llm_calls_per_run` | 60 | 单次运行最多 LLM 调用次数（含重试） |
| `max_attempts_per_chapter` | 2 | 单章最多尝试次数（首次 + 带反馈重试） |
| `length_ratio_min` / `length_ratio_max` | 0.7 / 1.3 | 改写后字数与原字数之比的允许范围 |
| `stop_on_failure` | true | 某章失败即停止，后续章节保持原文 |
| `review_scope` | hits | 语义判定的候选范围：`hits` 只判定命中定位词的章节；`all` 全书逐章判定（成本高，能找到纯指代的章节） |

## 命令行

```
python entrypoints/post_revision.py --novel <dir> baseline            # P0 创建 v001（终审需已通过）
python entrypoints/post_revision.py --novel <dir> status
python entrypoints/post_revision.py --novel <dir> settings --set max_chapters_per_run=10
python entrypoints/post_revision.py --novel <dir> change-add --json change.json   # P1
python entrypoints/post_revision.py --novel <dir> preview --base v001 --changes chg_xxx [--review]
python entrypoints/post_revision.py --novel <dir> exclude --preview prv_xxx --chapter 3 [--include]  # 确认前调整章节范围
python entrypoints/post_revision.py --novel <dir> confirm --preview prv_xxx
python entrypoints/post_revision.py --novel <dir> run --preview prv_xxx           # P2（调用 LLM）
python entrypoints/post_revision.py --novel <dir> resume --run run_xxx            # 因上限暂停或停止后续跑
python entrypoints/post_revision.py --novel <dir> stop --run run_xxx              # 请求停止（当前章节结束后生效）
python entrypoints/post_revision.py --novel <dir> triage --base v002 --chapter 12 --feedback "..."  # P3（调用 LLM）
python entrypoints/post_revision.py --novel <dir> accept --triage tri_xxx --mode from_chapter --from 12
python entrypoints/post_revision.py --novel <dir> diff --a v001 --b v002          # P4
python entrypoints/post_revision.py --novel <dir> export --version v002           # P4
python entrypoints/post_revision.py --novel <dir> verify --version v001
python entrypoints/post_revision.py --novel <dir> discard --version v003          # P5 撤销（仅叶子版本）
```

变更 JSON 示例（设定变更）：

```json
{"kind": "setting", "title": "玉佩换成银钥匙", "before": "玉佩", "after": "银钥匙",
 "reason": "情节需要", "terms": ["玉佩"], "after_terms": ["银钥匙"],
 "mode": "from_chapter", "effective_from": 2}
```

章节反馈变更（`kind: "chapter"`）通常由 `triage` + `accept` 生成，也可手写：`{"kind": "chapter", "title": "...", "instruction": "压缩开头", "chapters": [1]}`。

## 各阶段实现状态

| 阶段 | 状态 | 模块 | 说明 |
|---|---|---|---|
| P0 完稿版本基线 | 完成 | `versions.py`、`common.py`、`config.py` | 终审门禁、只读快照、哈希清单、校验 |
| P1 变更与预览 | 完成 | `changes.py` | 两种时间语义、受影响章节（子串匹配）、预览与确认 |
| P1+ LLM 语义判定 | 完成 | `changes.py` | `preview --review`：对每个候选章节判定是否真涉及变更（识别指代、排除同字误报）；判定为不涉及的默认排除，确认前可用 `exclude` 调整；判定失败的章节保留并警告 |
| P1+ 候选发现 | 完成 | `changes.py` | `review_scope=all` 时，设定变更对全书（from_chapter 时为生效章及之后）逐章判定，找回纯指代的章节 |
| P2 滚动改写 | 完成 | `executor.py` | 章序滚动、单章校验与重试、失败隔离、上限暂停与续跑、请求停止（章节间生效）；只改写未被排除的章节 |
| P2+ 中断恢复 | 完成 | `executor.py` | 运行期间写 pid 锁；`recover_interrupted` 把「running 但持有进程已不存在」的运行标记为 interrupted，可续跑（界面进入运行页时自动执行） |
| P3 章节反馈分诊 | 完成 | `feedback.py` | LLM 分诊为 chapter / setting；作者确认后才生成变更 |
| P4 派生物与对比导出 | 完成（重跑需手动） | `derived.py` | 派生物建议清单、版本逐文件差异、按版本导出 |
| P5 撤销与丢弃 | 完成 | `versions.py` | `discard`（仅叶子版本，不能丢弃 v001）、`undo_head` |
| 实时进度 | 完成 | `jobs.py` | 生成预览、开始改写、续跑、分诊都在后台线程执行，界面每 1.5 秒刷新日志：每章开始判定、每次尝试、判定结果、校验结果，以及 agent 的逐行输出（经框架的行接收器实时写入） |
| 界面 | 完成 | `ui/post_revision.py` | 导航「📝 完稿修改」：版本（基线、校验、差异、导出、撤销）/ 变更与预览（排除、确认）/ 运行（后台执行、停止、续跑、派生物建议与日志）/ 章节反馈（分诊、采纳）/ 设置 |

## 已知限制

- **默认候选仍是子串匹配**：`review_scope=hits` 时只判定命中定位词的章节，纯指代章节需要切到 `all`（成本为全书章数的 LLM 调用）。
- **语义判定依赖 LLM 的判断**：判定结果会写入预览，作者应核对被排除与保留的章节理由（`review.reason`）。
- **执行器未在真实 LLM 下验证**：测试使用桩执行器（按提示中的路径读取原文并替换），调用约定与 `llm_convert` 一致，
  但实际写作质量与提示遵循情况需本机用真实模型跑一次。
- **派生物不自动重跑**：一致性、人物索引、伏笔账本、全书终审报告只给出 `derived_marks` 建议，由作者决定何时重跑。
- **停止是协作式的**：`stop` 写入标记，当前章节的 LLM 调用结束后才停下，不会中断正在进行的调用。
- **滚动改写会累积漂移**：后面章节只看到前一章的最新文本，远处章节的一致性依赖一致性检查兜底。
- **后台线程**：界面的运行在 Streamlit 进程内的守护线程中执行，服务重启会中断它。中断后运行记录会在下次打开运行页时被标记为「已中断」并可续跑；锁的判定依据是进程号是否存在，同机多进程适用，跨机器共享目录不适用。
- **实时日志的粒度**：每一行 agent 输出都会实时写入日志；但模型最终的文字回复只有在一次调用结束后才完整可见（框架是非流式的）。
- **中断点的粒度**：恢复时从未完成的章节重新开始，已写入的章节不会重做，但正在进行的那一章的 LLM 调用会丢失，需要重新调用一次。
- **Windows 下的只读与删除**：已加权限修正，但只在 Linux 上测试过。
- 流程完全不读写流水线状态；若作者在完稿后又回去用流水线重跑章节，那是独立于本流程的操作，两者不会自动同步。

## 测试

`tests/test_revision_flow.py`（34 例）与 `tests/test_revision_flow_ui.py`（6 例，AppTest 冒烟）覆盖：基线门禁与只读、配置校验、变更校验、两种语义的章节集合与警告、
预览确认门禁、retcon / from_chapter 执行、原稿不被触碰、校验失败重试、失败即停、上限暂停与续跑、
基于 head 的二次修改与撤销、分诊（设定 / 章内 / 坏 JSON 重试 / 失败不可采纳）、版本差异、按版本导出、语义判定与排除、全书候选、停止与续跑、模拟崩溃后的中断识别与恢复、存活与死亡进程的锁判定。界面测试覆盖渲染、表单保存、后台生成预览与日志写入（使用不调用 LLM 的路径）；日志流与 agent 输出的映射由 jobs 单元测试覆盖。
