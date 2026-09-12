# browser-site-scraper：同域名重复 member 治理 + 匹配机制修复方案

对应背景：`.claude/skills/browser-site-scraper/SKILL.md`（本 skill 说明）、
`src/mini_agent/skills/generative_capability/capability_engine.py`（通用引擎，
`browser-site-scraper` 只是复用它的众多 generative-capability skill 之一）、
`next_doc/generative-capability-skill-plan.md`（引擎原始方案）。本文档聚焦
用户反馈的一个真实问题：`*.arxiv.org*` 这个 domain_pattern 下积累了 8 个
（`arxiv_13` ~ `arxiv_20`，实际编号说明此前已经发生过至少 12 次）几乎同一
件事的重复 member。用户明确要求"从这类 skill 的机制上去思考解决方案"，
因此本文档不只是修一个正则 bug，而是完整定位并修复 generative-capability
引擎里导致这类重复得以发生、且会在其他站点/其他 skill 上重演的机制缺口。

## 0. 背景与问题复现

用户提供的 `_index.json` 片段里，`arxiv_13` ~ `arxiv_20` 这些 member：

- `match.domain_pattern` 全部是完全相同的字面量 `"*.arxiv.org*"`；
- `match.keyword` 是每次探索时用户原话的一个截断片段（如
  `"抓取arxiv论文完整正文内容，包括所有section标题和"`、
  `"抓取arxiv论文2509.26354的完整HTML页面，提"`），彼此之间几乎不可能
  互相匹配；
- `description` 也是同一句话的重复变体。

即：同一个站点、同一类抓取意图，被反复判定成"这是一个全新能力"，反复
触发探索子agent、反复蒸馏出几乎相同的新脚本，反复落盘成新 member。

### 0.1 已用代码验证的直接原因：`_domain_match` 对裸域名匹配失败

`capability_engine.py::_domain_match` 用最朴素的方式把通配符转正则：

```python
regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
```

实测：

```python
>>> domain_match('*.arxiv.org*', 'https://arxiv.org/abs/2509.26354')
False
>>> domain_match('*.arxiv.org*', 'https://www.arxiv.org/abs/2509.26354')
True
```

`*.arxiv.org*` 转出来的正则要求"arxiv.org"前面必须出现一个字面的点，
但 arxiv 论文页的真实 URL 形态恰恰是**裸域名**
（`https://arxiv.org/abs/...`，没有 `www.` 之类子域名前缀），于是第一级
"域名确定性匹配"对 arxiv 这个站点**永远命中不了**。`baidu`/`zhihu` 两个
人工预置 member 因为真实 URL 里天然带 `www.` 子域名，同样的 bug 没有暴露。

### 0.2 被放大的次要原因：探索蒸馏出的 keyword 过于字面化

一级域名匹配失败后会 fallback 到二级关键词匹配（`resolve()` 内
`_match_deterministic(type="keyword", ...)`），但 `distiller.py::
_infer_match_rule()` 生成新 member 的 keyword 时是：

```python
match["keyword"] = [text[:30]]
```

即直接截断这次探索请求的原始自然语言前 30 个字符。这段文本几乎肯定
带有本次请求特有的措辞、论文编号等，下次哪怕问法/论文号稍有不同也不会
再命中——keyword 匹配这层"兜底"在实践中约等于形同虚设，起不到真正的
兜底作用。

### 0.3 根本的机制缺口：探索/蒸馏没有"同域名去重"这一步

沿着调用链确认（`capability_engine.py::call()` 第 802~809 行、
`explore()` 第 569~633 行、`distiller.py::distill()` 第 654 行
`_generate_member_id()`）：

- `resolve()` 返回 `miss` 时，`call()` 直接 `self.explore(request)`，
  **不传** `reexplore_member_id`；
- `explore()` 收到空的 `reexplore_member_id` 时视为"全新领域"，调用
  `_generate_member_id()` 生成一个新 id；
- `_generate_member_id()` 的去重逻辑只是"如果这个 base 名字的目录已经
  存在，就换成 `base_2`/`base_3`/……"——这正是 `arxiv_13` 之类编号的来源：
  它把"这个域名已经有 member 了"当作"再建一个编号更大的 member"的信号，
  而不是"该复用/合并"的信号；
- `_infer_match_rule()`/`_merge_match_rule()` 的"保留旧 domain_pattern、
  并集 keyword"这套本来写得很仔细的合并逻辑（阶段六新增，见
  `distiller.py` 1365~1406 行注释），**只有在 `reexplore_member_id` 非空
  （即针对一个已经 degraded 的既有 member 重新探索）时才会生效**。
  对"resolve 直接 miss 走到全新探索"这条路径完全没有触达，导致合并逻辑
  形同虚设。

三条线合在一起，就是一台"只要一级匹配出一点系统性偏差（bug、pattern 太窄、
keyword 太窄），就会被无限放大成不断新建同域名分身 member"的机制——这不是
某一次探索的偶然失误，是设计上"检索 miss == 需要一个全新能力"这个假设
在检索层不可靠时必然会出的问题，其他站点/其他 generative-capability skill
理论上同样会撞上。

## 1. 修复目标与设计原则

1. **修对根因**：`_domain_match` 改成基于 host 的语义匹配，裸域名与任意
   子域名都应该命中同一条 `*.domain` 规则，不再要求"前面必须有一个字面
   的点"。
2. **不引入新的误判风险**：绝不能因为"让裸域名也能匹配"而放宽到
   `notarxiv.org`、`arxiv.org.evil.com` 这类拼接域名也被误判成同一个站点
   ——必须基于真正的 URL host 解析 + label 后缀比较，而不是继续在整段
   字符串上做子串式正则。
3. **不做危险的自动合并**：探索/蒸馏是一次性的、针对单个请求的产出，不能
   仅凭"这次探索又落在了一个已有 member 覆盖的域名下"就自动拿新脚本
   覆盖旧的（可能已经 trusted、正在正常工作的）member——那样一次质量
   不高的探索反而可能把一个原本可靠的 member 顶坏。因此新建 member 时
   遇到同域名重叠，采取**如实标记 + 巡检提示，交给人工/巡检流程审查决定
   要不要合并**，而不是静默新建（今天的行为）也不是静默覆盖（更危险的
   行为）。这与本引擎一贯的"如实记录，不静默兜底"风格（见 `distiller.py`
   文件头 trust_trace_data 一节的表述）一致。
4. **提供可审计的存量清理工具**：现网已经生成的 `arxiv_13` ~ `arxiv_20`
   这类历史重复，需要一个独立的、可重复运行、只读默认/显式确认才写入的
   合并工具，而不是让用户手工编辑 JSON。
5. **让"重复膨胀"这件事本身变得可观测**：`health_patrol.py` 现有的巡检
   报告（阶段四）里新增一类 finding，日常巡检就能发现"某个 skill 下同一
   域名有 N 个 member"，不用等到用户偶然翻 `_index.json` 才发现。

## 2. 改动方案（按实施阶段划分）

### 阶段 A —— 修复 `_domain_match`（核心修复，低风险）

- 改动文件：`src/mini_agent/skills/generative_capability/capability_engine.py`。
- 内容：
  - 新增 `_extract_domain_pattern_core(pattern)`：识别形如 `*.example.com`
    / `*.example.com*` 的"纯域名"模式（掐头去尾后不再含通配符或 `/`），
    返回核心域名 `example.com`；模式本身带路径片段（如
    `*.baidu.com/s*`）时返回 `None`，退回旧的整串通配符逻辑，不影响
    baidu/zhihu 现有行为。
  - 新增 `_extract_host(value)`：用 `urllib.parse.urlparse` 解析出 URL 的
    host（无 scheme 时按 `//` 前缀兜底解析，避免把路径误当成 host）。
  - `_domain_match` 优先走"纯域名模式"分支：`host == core or
    host.endswith("." + core)`，语义上就是"host 就是这个域名，或者是它的
    任意子域名"，天然同时覆盖裸域名和子域名，且不会被 `notexample.com`
    这类拼接域名误伤（label 级别的后缀比较，不是子串包含）。非纯域名
    模式或 host 解析失败时，回退到原有的整串通配符正则逻辑，保证向后
    兼容。
- 测试：`tests/test_generative_capability_engine.py` 新增用例，覆盖裸域名
  命中、子域名命中、拼接域名不应误命中、含路径的 pattern（baidu 场景）
  行为不变四类断言。

### 阶段 B —— 新建 member 时检测"同域名重叠"，如实标记（中风险，遵循"只报告不擅自动"原则）

- 改动文件：`src/mini_agent/skills/generative_capability/distiller.py`
  （`_atomic_persist`）。
- 内容：在 `_atomic_persist` 已经加载好当前 `index`/`registry`、且确认
  这是一个"全新 member"（即调用方传入的 `existing_entry is None`，
  说明这次不是针对既有 id 的重新探索）的位置，用阶段 A 修好的
  `CapabilityEngine._extract_domain_pattern_core` 对本次新生成的
  `match.domain_pattern` 与其余处于 `trusted`/`probation`/`degraded`
  （即 `_is_active` 判定为真）状态的 sibling member 做一次归一化比较；
  一旦发现重叠：
  - 在这次新落盘的 index 摘要里加一个 `possible_duplicate_of` 字段，
    记录重叠到的 member_id 列表；
  - 同步写进 `meta.json`，保证不需要重新跑一次巡检也能在磁盘上直接
    看到这条"存疑"记录；
  - 通过 `capability_debug_log` 记一条
    `distill_new_member_overlaps_existing_domain` 事件，理由与巡检
    finding 保持同一份措辞，避免两处文案漂移。
  - **不阻断这次探索的落盘**：探索子agent确实可能在同一个域名下发现了
    另一种合法的、独立的抓取意图（比如百度搜索 vs 百度地图），把"域名
    重叠"直接当成"一定是重复"会误伤这种合法场景；因此这里只标记、不
    拒绝，是否真的重复交给后续人工/巡检判断。
- 测试：`tests/test_generative_capability_engine.py` 或新增
  `tests/test_generative_capability_distiller_dedup.py`，构造"index 中
  已有一个 domain_pattern=`*.arxiv.org*` 的 active member，再蒸馏一个新
  member 命中同一个域名"的场景，断言新条目带上了 `possible_duplicate_of`。

### 阶段 C —— `health_patrol.py` 新增"同域名重复 member"巡检项

- 改动文件：`src/mini_agent/skills/generative_capability/health_patrol.py`。
- 内容：
  - `run_patrol()` 新增一类扫描：按阶段 A 的归一化规则给所有 active
    member 的 `match.domain_pattern` 分组，组内成员数 > 1 时追加一条
    `PatrolFinding(kind="duplicate_domain_pattern")`，`detail` 里列出
    重叠的全部 member_id，方便人工/上层 agent 一次性看到"这个域名下
    到底攒了几个"。
  - 新增可选参数 `merge_duplicates: bool = False`（与现有 `apply_cleanup`
    同样的"默认只报告，显式传参才真正写入"风格）：为真时，对每一组
    重复，挑选 `success_count` 最高（并列时取字典序最小的 member_id，
    保证结果确定性）的一个作为 canonical，把其余组内 member 的
    `match.keyword` 并集补进 canonical 的 index 条目（复用与
    `distiller._merge_match_rule` 一致的并集去重逻辑），其余 member 的
    registry 状态改为 `dead`（写 `status_changed_at`）、从 `_index.json`
    移除检索摘要——**但不删除 `members/` 目录下的脚本文件**，与现有
    `dead` 生命周期的语义保持一致，真正的物理删除仍然只交给已有的
    `apply_cleanup` 长期保留期机制处理，可审计、可回滚。
  - CLI 入口 (`__main__`) 增加 `--merge-duplicates` 开关，与
    `--fix-inconsistencies`/`--apply-cleanup` 并列。
- 测试：`tests/test_generative_capability_health_patrol.py`（若不存在则
  新建）覆盖"发现重复"与"合并重复"两种场景。

### 阶段 D —— 存量清理：一次性合并现网已产生的 `arxiv_13` ~ `arxiv_20`

- 不新增代码——阶段 C 做完之后，`health_patrol.run_patrol(skill_dir,
  merge_duplicates=True)` 本身就是这个"一次性清理工具"，不需要再单独
  写一个脚本维护两套重复逻辑。
- 操作方式：在用户自己的项目里对
  `.claude/skills/browser-site-scraper` 目录跑一次
  `python -m mini_agent.skills.generative_capability.health_patrol \
  .claude/skills/browser-site-scraper --merge-duplicates`（或直接调用
  `run_patrol(...)`），输出的 `PatrolReport` 会如实列出合并前的重复分组
  与合并后的结果，供确认。
- 注意：本仓库当前快照里 `browser-site-scraper/_index.json` 只有
  `baidu`/`zhihu` 两个人工预置 member（用户反馈的 `arxiv_13` ~
  `arxiv_20` 是在用户自己的运行环境里探索生成的，未包含在这份代码快照
  里），因此阶段 D 的"合并"这一步需要用户在自己的环境里执行，本文档
  与配套代码改动只负责把工具做对、做好。

### 阶段 E —— 文档更新

- `.claude/skills/browser-site-scraper/SKILL.md`"已知限制"一节补充一条：
  同域名下出现多个重复 member 的历史成因、已修复的匹配 bug、以及如何用
  `health_patrol.py --merge-duplicates` 清理。
- 本文档在实施完成后补一节"实施记录"，如实登记每个阶段的落地状态与
  改动文件清单，不重开新文档（遵循 `generative_capability_trace_replay_
  and_allowlist_plan.md` 开头引用到的项目惯例：后续独立方案不来回改动
  历史方案文档，但一份方案自己的实施记录允许直接追加在文末）。

## 3. 明确不做的事情（避免过度设计）

- 不引入"探索前先问一次已有 member 能不能扩展覆盖"的**自动**分支
  （即不会让 `resolve()` miss 时自动把 `reexplore_member_id` 设成
  domain 重叠到的既有 member，强行复用/覆盖它的脚本）。这类自动合并
  一旦域名重叠但实际意图不同（同域名下两种抓取需求），会把两个本该
  独立的能力错误地绞在一起，且可能用一次未经充分验证的探索结果覆盖掉
  一个已经 trusted 的旧脚本，风险明显高于收益。阶段 B/C 的"标记 + 巡检
  提示 + 显式合并"已经能覆盖用户反馈的问题场景，且更安全、更可控。
- 不改动 `_infer_match_rule()` 生成 `domain_pattern` 的格式
  （`f"*.{group(1)}*"`）——阶段 A 修好 `_domain_match` 的解释语义后，
  这个既有格式本身就能被正确匹配，不需要跟着改，减少改动面。
- keyword 生成过于字面化（0.2 节）在阶段 A 落地后影响已大幅降低（因为
  同域名请求会先在一级域名匹配命中，不再需要依赖二级关键词兜底），
  本方案不专门为此引入语义泛化（如接入 LLM 生成更通用的 keyword），
  留作后续如果实践中发现二级兜底命中率仍然是问题时再单独立项。

## 4. 实施记录

阶段 A/B/C/E 已按上述方案实现，阶段 D（对用户自己环境里已生成的
`arxiv_13` ~ `arxiv_20` 做实际合并）需要用户在自己的运行环境里执行下面的
命令完成，本仓库快照中没有这些历史数据可供直接操作。

### 改动文件清单

- `src/mini_agent/skills/generative_capability/capability_engine.py`
  - `_domain_match` 改为优先按"纯域名模式"做 host 语义比较，新增
    `_extract_domain_pattern_core`/`_extract_host` 两个静态辅助方法；
    含路径的模式（如 baidu 的 `*.baidu.com/s*`）保持走原有整串通配符
    逻辑，行为不变。
- `src/mini_agent/skills/generative_capability/distiller.py`
  - `_atomic_persist` 新增：只在落盘一个真正全新的 member（非
    reexplore）时，用 `_find_active_domain_overlaps`（新增函数）检查
    是否与其余 active sibling member 的 domain_pattern 重叠；重叠时在
    index 摘要与 `meta.json` 里记 `possible_duplicate_of`，并打
    `distill_new_member_overlaps_existing_domain` 调试日志；不阻断落盘。
- `src/mini_agent/skills/generative_capability/health_patrol.py`
  - `PatrolFinding.kind` 新增 `"duplicate_domain_pattern"`；
    `PatrolReport` 新增 `merged_members` 字段。
  - `run_patrol` 新增 `merge_duplicates` 参数；新增
    `_group_active_members_by_domain`/`_find_duplicate_domain_groups`/
    `_merge_duplicate_group` 三个内部函数，实现"默认只报告、显式传参
    才真正合并"的巡检 + 合并逻辑，合并只改状态/检索摘要，不删除脚本
    文件。
  - CLI 入口新增 `--merge-duplicates` 开关。
- `.claude/skills/browser-site-scraper/SKILL.md`
  - "已知限制"新增一节，说明历史成因、已修复的根因、以及如何用
    `health_patrol.py --merge-duplicates` 清理存量重复。
- `tests/test_generative_capability_engine.py`
  - 新增 `TestDomainPatternMatchFix`（裸域名命中、子域名命中、拼接域名
    不应误命中、含路径模式行为不回归）与
    `TestNewMemberDomainOverlapFlagging`（新建 member 时的重叠检测：
    命中同域名标记、不同域名不标记、dead 状态 sibling 不参与比较）。
- `tests/test_generative_capability_health_patrol.py`（新增文件）
  - 覆盖"仅报告不写入"、"合并保留 success_count 最高者为 canonical 且
    keyword 并集"、"baidu 这类含路径模式不被误判为重复"、"域名不同不
    产生 finding"四类场景。

### 验证情况

- 上述新增测试与既有
  `tests/test_generative_capability_engine.py`、
  `tests/test_generative_capability_real_tools.py`、
  `tests/test_generative_capability_skill_tier.py`、
  `tests/test_generative_capability_available_tiers.py`、
  `tests/test_generative_capability_skill_upgrade.py`
  一并跑过，全部通过（`test_generative_capability_real_tools.py` 里一个
  依赖 `websocket-client` 的用例在缺少该依赖的沙盒环境下会失败，安装
  依赖后通过，与本次改动无关，是既有的环境依赖缺口）。
- 额外手工构造了一次完整的"两个同域名重复 member + 一个 baidu 式含路径
  member"场景，验证 `run_patrol()` 默认只报告、`merge_duplicates=True`
  时正确合并且不删除脚本文件、baidu 不被误判为重复。

### 存量清理操作（阶段 D，需在用户自己的环境执行）

```
python -m mini_agent.skills.generative_capability.health_patrol \
  .claude/skills/browser-site-scraper --merge-duplicates
```

建议先不加 `--merge-duplicates` 跑一次，确认报告里 `duplicate_domain_
pattern` finding 列出的分组符合预期，再加上该开关真正执行合并。

### 已知遗留

- 0.2 节提到的"探索蒸馏出的 keyword 过于字面化"问题本身没有修复（见
  第 3 节"明确不做的事情"），阶段 A 落地后其影响已大幅降低，如果后续
  实践中发现二级关键词兜底命中率仍然是个问题，需要单独立项（大概率要
  引入 LLM 做语义泛化，而不是纯规则能解决）。
- `_find_active_domain_overlaps`/`_group_active_members_by_domain` 目前
  只识别"纯域名"模式（`_extract_domain_pattern_core` 返回非 None），
  带路径的 domain_pattern（如 `*.baidu.com/s*`）不参与去重判断——这是
  刻意的（同域名不同路径通常是合法的独立能力），但也意味着如果未来
  真的出现"同一路径前缀下的重复 member"（如反复探索出多个
  `*.baidu.com/s*`），本次改动不会自动发现，需要另外扩展匹配粒度。
