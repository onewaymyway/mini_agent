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
    ├── runs/<run>/chNNN/attemptKK/out/   每次尝试的执行器产出（便于排查）
    └── triage/tri_*.json     章节反馈分诊记录
```

## 可配置上限（`_revision/settings.json`，当前通过 CLI 修改，界面后续接入）

| 键 | 默认 | 含义 |
|---|---|---|
| `max_chapters_per_run` | 20 | 单次运行最多改写的章数，超出则暂停，续跑继续 |
| `max_llm_calls_per_run` | 60 | 单次运行最多 LLM 调用次数（含重试） |
| `max_attempts_per_chapter` | 2 | 单章最多尝试次数（首次 + 带反馈重试） |
| `length_ratio_min` / `length_ratio_max` | 0.7 / 1.3 | 改写后字数与原字数之比的允许范围 |
| `stop_on_failure` | true | 某章失败即停止，后续章节保持原文 |

## 命令行

```
python entrypoints/post_revision.py --novel <dir> baseline            # P0 创建 v001（终审需已通过）
python entrypoints/post_revision.py --novel <dir> status
python entrypoints/post_revision.py --novel <dir> settings --set max_chapters_per_run=10
python entrypoints/post_revision.py --novel <dir> change-add --json change.json   # P1
python entrypoints/post_revision.py --novel <dir> preview --base v001 --changes chg_xxx
python entrypoints/post_revision.py --novel <dir> confirm --preview prv_xxx
python entrypoints/post_revision.py --novel <dir> run --preview prv_xxx           # P2（调用 LLM）
python entrypoints/post_revision.py --novel <dir> resume --run run_xxx            # 因上限暂停后续跑
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
| P2 滚动改写 | 完成（语义筛选未做） | `executor.py` | 章序滚动、单章校验与重试、失败隔离、上限暂停与续跑 |
| P3 章节反馈分诊 | 完成 | `feedback.py` | LLM 分诊为 chapter / setting；作者确认后才生成变更 |
| P4 派生物与对比导出 | 完成（重跑需手动） | `derived.py` | 派生物建议清单、版本逐文件差异、按版本导出 |
| P5 撤销与丢弃 | 完成 | `versions.py` | `discard`（仅叶子版本，不能丢弃 v001）、`undo_head` |
| 界面 | 未做 | — | 设定页/独立修改页待接入，当前通过 CLI 与 settings.json |

## 已知限制

- **受影响章节用子串匹配**：`terms` 以文本包含判断，会漏掉指代（如“他父亲”），也可能误报同名或同字。
  方案中的 LLM 语义筛选（过滤误报、识别指代）**尚未实现**，预览里的章节清单需作者自行核对。
- **执行器未在真实 LLM 下验证**：测试使用桩执行器（按提示中的路径读取原文并替换），调用约定与 `llm_convert` 一致，
  但实际写作质量与提示遵循情况需本机用真实模型跑一次。
- **派生物不自动重跑**：一致性、人物索引、伏笔账本、全书终审报告只给出 `derived_marks` 建议，由作者决定何时重跑。
- **没有运行中取消**：只能等运行因上限或失败停下后再丢弃/续跑。
- **滚动改写会累积漂移**：后面章节只看到前一章的最新文本，远处章节的一致性依赖一致性检查兜底。
- **Windows 下的只读与删除**：已加权限修正，但只在 Linux 上测试过。
- 流程完全不读写流水线状态；若作者在完稿后又回去用流水线重跑章节，那是独立于本流程的操作，两者不会自动同步。

## 测试

`tests/test_revision_flow.py`（18 例）覆盖：基线门禁与只读、配置校验、变更校验、两种语义的章节集合与警告、
预览确认门禁、retcon / from_chapter 执行、原稿不被触碰、校验失败重试、失败即停、上限暂停与续跑、
基于 head 的二次修改与撤销、分诊（设定 / 章内 / 坏 JSON 重试 / 失败不可采纳）、版本差异、按版本导出。
