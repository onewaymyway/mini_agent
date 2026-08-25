# 决策/取舍知识提炼计划

> 本计划建立在《Wiki 式知识库重构计划》之上：决策知识以 `decisions/*.md` 页面形式存在，
> 与 `entities/*.md` 通过显式 links 互相引用，不是独立于 wiki 体系之外的另一套存储。

## 一、设计理念

工程决策的价值不在"做了什么"，而在"为什么这么做、还考虑过什么、为什么否决了别的方案"。这类知识有一个特殊性质：**它只有在被推翻的时候才显得有价值**——大部分时候团队/agent 不会回头查"这个方案当初为什么这么定"，只有在有人（人类或 agent 自己）打算换一种做法时，才需要这份历史。所以决策知识提炼的核心目标不是"记录得全"，而是**"能在恰好需要的那一刻被自动带出来"**——防止同一个已经被否决过的方案被重新提出、重新论证一遍、得出同样的结论。

## 二、当前现状

现有知识写入分两条线：

1. **规则触发的 lesson**（`perception/lesson_rules.py`）：同一工具连续失败 ≥N 次、权限拒绝后重试成功，两条纯规则模板，不调用 LLM，`entry_type="lesson"`、`confidence=0.6`。
2. **人类显式纠正**（`perception/correction_detector.py`）：识别出人类否定了 agent 之前的结论，生成 `confidence=0.7` 的记忆条目。

压缩策略层面，`history/compression.py` 的 `LLMSummaryStrategy` 在 compact 触发时（触发条件由 `history/triggers.py` 的触发器框架管理：token 阈值、话题切换、冗余度等）会把一段 turn 历史交给 LLM 生成摘要，目的是"恢复上下文"，不涉及知识沉淀。

## 三、当前问题

1. **两条现有提取线都是"信号驱动"，且信号都是负面的**：连续失败、权限拒绝、人类否定——本质上都是"出了问题"才触发。而工程决策往往发生在一切顺利、没有报错、没有人纠正的正常推进过程中，这类场景现有两条线完全覆盖不到。
2. **决策过程的信息目前只存在于压缩摘要里，且摘要的目的不匹配**：`LLMSummaryStrategy` 生成摘要是为了"恢复对话上下文"，追求的是信息密度和连贯性，不是"把决策取舍结构化地留存下来供以后检索"，两种目标混在一起会互相拖累——摘要写得越结构化、越像知识条目，作为"恢复上下文"用就越啰嗦；反之亦然。
3. **没有机制在做出新决定之前检查"是否已经决定过"**：即使决策记录下来了，现有系统也没有任何环节会在 agent 提出新方案前主动去查有没有相关的历史决策，导致记录了但没被用起来的风险很高。
4. **决策的可信度体系需要独立分层，但现有系统只有一套 confidence 语义**：人类纠正 0.7、规则触发 0.6，这套体系对应的是"证据的客观性强弱"；决策复盘是 agent 对自己历史行为的二次解读，主观重构风险更高，不能直接套用现有两档，需要单独定位。

## 四、为什么要构建新方案

如果不单独建这条线，决策知识会一直散落、不可复用——每次要理解"为什么系统长这样"，只能靠人去读代码注释或者问 agent 现场复盘（现场复盘的结果还可能和当初真实的取舍不一致，因为 agent 此刻并不知道自己当初否决过什么）。而防止"重新论证已经被否决的方案"这件事，只有先把决策显式沉淀下来、并且在恰当时机主动召回，才有可能做到——这不是锦上添花的功能，而是随着项目复杂度和重构频率增加（现有的六阶段判断系统统一迁移就是很好的例子），重复劳动和来回摇摆的成本会越来越高，必须有机制兜底。

在 wiki 化的前提下，这件事变得更容易落地：决策本来就该是 wiki 里独立的一类页面，`links` 机制天然支持"这个决策影响了哪些实体""这个决策属于哪个专题"，检索时的图扩展步骤也天然支持"查某个模块时把它相关的决策历史一起带出来"——不需要为决策知识单独设计一套检索通路。

## 五、新方案设计

### 5.1 页面结构：`decisions/*.md`

```yaml
---
id: classification-merge-strategy
type: decision
tags: [classification, cost-optimization]
confidence: 0.5        # 决策复盘类知识固定低于规则触发(0.6)与人类纠正(0.7)
status: settled          # settled | revisited | overturned
created: 2026-07-10
links:
  - target: classification-tree
    relation: affects
source_entries: [entry_x1, entry_x2]
---

## 问题
分类树节点如何判断是否应该合并？

## 考虑过的方案
1. 纯规则相似度（字符串匹配）— 快但对语义相近、字面不同的情况容易漏判
2. 纯 LLM 判断所有实体对 — 精度高但组合数会爆炸，成本不可控
3. **规则 + LLM 兜底（已采纳）**：相似度达到高阈值直接判定，中间地带才问 LLM

## 采纳理由
在控制成本的同时兜住规则判断的中间地带。

## 如果要推翻这个决定
需要先验证：LLM 兜底调用频率是否真的可控（当前假设中间地带占比小）。
```

`status` 字段承担了"决策生命周期"的作用：`settled`（尚未被重新审视）→ `revisited`（被重新提起讨论但维持原判）→ `overturned`（被推翻，此时应新增一条指向替代方案的 `links: relation=superseded_by`）。

### 5.2 提取时机：复用 compact 的 LLM 调用，不新增调用次数

`history/triggers.py` 已有的触发器框架决定何时 compact，`LLMSummaryStrategy` 已经在对同一段 turn 历史发起一次 LLM 调用。改造方式是把这次调用的输出从"纯摘要文本"改成结构化 JSON：

```json
{
  "compact_summary": "……（原有的上下文恢复摘要，不变）",
  "decisions": [
    {
      "topic": "分类树合并策略",
      "options_considered": ["纯规则相似度", "纯LLM判断所有实体对", "规则+LLM兜底"],
      "chosen": "规则+LLM兜底",
      "rejected_because": {"纯LLM判断所有实体对": "组合数会爆炸，成本不可控"},
      "related_entities": ["classification-tree"]
    }
  ]
}
```

`decisions` 数组允许为空（大多数 turn 段落里不存在值得记录的决策），只有识别到"讨论了多个方案并做出取舍"的段落才会产生条目。这样**不增加额外的 LLM 调用次数**，只是让本来就要发生的一次调用多输出一点结构化内容。

### 5.3 落盘：命中已有决策页则更新状态，命中不到才新建

提取出的 `decisions` 条目不直接新建页面，先走匹配：

1. 通过 `related_entities` 找到对应的 `entities/*.md`，检查其 `links` 里是否已有指向某个 `decisions/*.md` 的 `affects`/`part_of` 关系。
2. 命中已有决策页且 `chosen` 与已有页面一致 → 只更新 `updated` 时间戳和 `source_entries`，不新建重复内容。
3. 命中已有决策页但 `chosen` 与已有页面**不一致** → 说明决策被重新做过，旧页面 `status` 改为 `overturned`，新建一条决策页并用 `links: relation=supersedes` 指回旧页面，形成决策沿革链条（而不是让新旧决策各自孤立存在）。
4. 未命中任何已有决策页 → 巩固循环批量决定是否新建（避免每次 compact 都新建碎片化的小决策页）。

这一步复用《Wiki 式知识库重构计划》里巩固循环的既有节奏，不新增独立的调度逻辑。

### 5.4 最有价值的利用场景：提案前主动召回

在 agent 准备提出新的架构改动/重构方案之前（可以挂在现有的 reminder 机制上，`evolution/lesson_to_reminder.py` 已经把 lesson 转成 reminder 注入上下文，同样的转换逻辑直接复用给 decision 页面），先按新方案主题去 wiki 检索一遍相关的 `decisions/*.md`：

- 如果查到 `status=settled` 且方向相同的历史决策 → 主动提示"这个方向已经被采纳过，当前实现即基于此"
- 如果查到 `status=overturned` 且新提案正是被否决的旧方案 → 主动提示"这个方案之前被考虑过又被否决，原因是……"，避免重新走一遍相同的论证过程

这一步是决策提炼真正的价值出口，如果没有这一步，决策知识记录得再完整也只是存档，不会真正减少重复劳动。

## 六、具体改进计划

### 阶段一：结构与置信度体系

- [x] 定义 `decisions/*.md` 的 frontmatter schema（`status` 生命周期、独立于 lesson/correction 的 confidence 取值 0.5）
- [x] 在 `_templates/` 下新增决策类型模板，`wiki/validator.py` 增加 `status` 枚举与 `supersedes`/`superseded_by` 关系对的一致性校验（一条页面被标 `superseded_by X` 时，X 必须存在且反向标了 `supersedes`）

### 阶段二：提取改造

- [x] 修改 `history/compression.py` 的 `LLMSummaryStrategy` prompt，输出改为 `{compact_summary, decisions[]}` 结构化 JSON，`decisions` 允许为空数组
- [x] 新增落盘逻辑：解析 `decisions[]`，按 `related_entities` 匹配已有决策页，处理"更新/推翻/新建候选"三种分支
- [ ] 巩固循环增加"决策候选批量成页"步骤，避免碎片化

### 阶段三：图关联与召回

- [x] 决策页与实体页之间的 `links`（`affects`/`part_of`）纳入 Wiki 检索的图扩展一跳范围，确保查实体时自动带出相关决策
- [x] 实现"提案前主动召回"：复用 `lesson_to_reminder.py` 的转换逻辑，在新方案讨论触发时查询相关 `decisions/*.md` 并注入 reminder（`evolution/decision_recall.py`；尚未接入具体触发点，见实现记录）
- [ ] 验证 `overturned` 状态的决策沿革链条在实际重构场景（如后续判断系统的进一步演化）中能否被正确召回，作为本计划的验收标准

## 七、实现记录（本轮）

- **阶段一**：`wiki/parser.py::STATUS_VALUES` 合并进 `settled`/`overturned`（与 entity/topic 沿用的旧词表共用一个字段，按 type 解释语义，不拆分校验）；`_templates/decision.md` 改为新 schema（`status: settled`、`confidence: 0.5`，正文加“如果要推翻这个决定”“复盘”两节）；`wiki/validator.py` 新增 `_check_supersession_pairs()`，校验 `supersedes`/`superseded_by` 是否成对、`overturned` 页面是否有 `superseded_by` 出边。
- **阶段二**：新增 `history/decision_extraction.py::parse_decision_response()`（容错解析 `{compact_summary, decisions[]}`，失败时降级为纯文本摘要）；`history/compression.py::LLMSummaryStrategy.compress()` 复用同一次 LLM 调用同时拿到摘要与决策候选；`prompts/system/compress_summarizer.md` / `prompts/user/compress_summary_request.md` 同步改为要求纯 JSON 输出；新增 `CompressConfig.extract_decisions`（默认开）控制开关；新增 `wiki/decision_writer.py::process_candidates()` 落盘三分支。**未完成**：巩固循环批量节流（当前是逐条即时落盘）。
- **阶段三**：图扩展复用既有 `wiki/search.py::wiki_shelf_search()`，未新增检索通路。新增 `evolution/decision_recall.py::recall_related_decisions()` 实现按 status 分类的召回提醒渲染。**未完成**：未接入具体触发点（比如 agent 生成重构提案前的 prompt 组装位置），也未做 `overturned` 沿革链条在真实场景下的召回验证。

（详见 `docs/wiki-knowledge-base-guide.md` 九·2 节的完整说明。）
