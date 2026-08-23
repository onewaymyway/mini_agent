# Generative-Capability Skill 机制方案

## 一份可落地的 Agent Skill 系统扩展设计

---

## 0. 背景与目标

现有 skill 系统的加载机制分两类：

- **静态全量加载**：所有 skill 的说明在主 context 中常驻或按 trigger 匹配整体加载，适合数量少、变化慢的能力（如 docx / pptx / pdf 处理）。
- 但对于像 `browser-cdp` 这类"**领域功能包**"——内部包含几十上百个细分成员（如几十个网站的定制抓取脚本）——静态加载会导致：
  1. SKILL.md 与 references 无限膨胀，维护困难；
  2. 长尾场景（新网站/新目标）永远覆盖不全，只能靠人工持续补充；
  3. 主 context 被大量用不上的成员说明占满。

本方案的目标：把"领域功能包"抽象为 agent 系统的一种**内置 skill 类型**——`generative-capability`，具备：

- **按需检索**：不把成员清单塞进主 context，需要时才检索、加载。
- **自动生成**：检索不到对应能力时，自动探索并把探索结果固化为可复用的新成员。
- **通用引擎**：检索、调度、执行、探索触发、固化、健康度管理全部是平台内置的通用代码，任何领域的 skill 复用同一套引擎，skill 作者只需提供**声明式配置**，不需要重复实现流程逻辑。

---

## 1. 核心设计原则

1. **流程与领域分离**：调度骨架（怎么找、怎么执行、怎么探索、怎么固化）是平台能力，一次实现，处处复用；领域差异（匹配规则、schema、探索工具白名单、探索角色设定）收窄为纯声明式配置。
2. **Member 接口统一**：不论具体做什么（抓取网页、调用 API、生成文档模板……），每个 member 只需实现同一个函数签名 `run(input) -> dict`，引擎不关心内部实现。
3. **确定性与不确定性分离**：能用代码确定完成的部分（匹配、执行、校验、固化）绝不占用模型推理；只有真正需要理解/判断的部分（语义检索裁决、探索阶段的操作决策）才调用 LLM。
4. **不自我认定成功**：无论是命中已有 member 执行，还是探索生成新 member，产出结果必须经过独立的 `intent_schema` 校验，校验不通过一律判定失败，不允许"看起来差不多就算数"。
5. **信任是挣来的**：探索生成的 member 默认低信任等级，需连续成功一定次数才能"转正"；持续失败会被降级、重新探索或清理，避免脆弱路径被长期依赖，避免检索池被"看似存在实则失效"的僵尸成员污染。
6. **检索不用 embedding，只用 LLM，但要分级触发**：第一级用零成本的确定性匹配（如 domain/keyword 精确匹配）过滤大部分请求；只有匹配失败或存在歧义时，才对候选摘要清单发起一次独立、单一职责的 LLM 调用做裁决，避免每次请求都产生模型调用开销。

---

## 2. Skill 类型声明

在 skill 的 frontmatter 中新增 `skill_type` 字段，区分静态 skill 与生成式能力包：

```yaml
skill_type: generative-capability
category_summary: 针对具体网站的网页抓取能力，支持自动扩展新网站
```

主 agent 在主 context 中看到的仅是这一行摘要，**不会**看到该 skill 内部有多少 member、分别是什么。主 agent 只需要知道："遇到这类需求，调这个 skill，给出目标与期望结果结构，会得到结果或明确的失败原因。"

---

## 3. 标准目录规范

```
skills/<capability-name>/
  SKILL.md                  # 极简：skill_type + 一句话摘要 + 调用方式说明
  capability.yaml           # 声明式配置（领域差异全部收敛在此，见第4节）
  explorer/
    prompt.md               # 探索子agent的角色设定（自然语言，非代码）
    tool_allowlist.json     # 探索阶段允许调用的底层原语白名单
  _index.json                # 引擎自动维护：成员摘要清单，供检索使用
  registry.json               # 引擎自动维护：成员状态机数据（信任等级/版本/统计）
  members/
    <member-id>/
      script.py              # 统一签名 run(input) -> dict
      meta.json               # {source, status, version, intent_schema, stats}
```

skill 作者只需要手写 `SKILL.md`、`capability.yaml`、`explorer/` 目录、以及可选的若干预置 member 脚本。`_index.json`、`registry.json`、以及探索生成的 member，全部由引擎自动创建和维护。

---

## 4. capability.yaml（skill 作者唯一需要编写的配置核心）

```yaml
skill_type: generative-capability
name: browser-site-scraper

# 第一级免LLM匹配规则（零成本，命中优先）
domain_matchers:
  - type: domain_pattern
    field: target.url
  - type: keyword
    field: request_text

# 该领域所有 member 输出必须满足的 schema 结构约束模板
# 每个具体 member 可在 meta.json 中细化自己的 intent_schema，
# 但必须是本模板的合法子集/扩展
intent_schema_template:
  type: object
  required: [data]
  properties:
    data:
      type: object

# 探索阶段配置
explorer:
  base_tools: [browser-core]      # 只允许调用的底层通用能力
  prompt: explorer/prompt.md       # 探索子agent的角色设定
  max_steps: 40                    # 步数硬上限
  max_seconds: 180                 # 时间硬上限

# member 的统一函数入口约定（引擎据此调用，通常无需改动）
member_interface:
  entrypoint: "run(input: dict) -> dict"

# 转正/降级/清理的阈值（可覆盖引擎默认值）
lifecycle:
  probation_success_threshold: 3     # 连续成功N次转正
  degrade_failure_threshold: 3       # 连续失败N次降级重探索
  dead_after_reexplore_fail: true    # 重探索仍失败则标记dead
```

---

## 5. Member 统一接口规范

所有 member（无论人工预置还是探索生成）必须且只需实现：

```python
def run(input: dict) -> dict:
    """
    input: 引擎按该 skill 的调用约定传入，例如
           {"target": "https://...", "intent": {...}}
    返回: {
        "status": "success" | "fail",
        "data": {...} | None,
        "error": str | None
    }
    """
```

`meta.json` 统一结构（跨领域一致，引擎状态机据此运作，与具体领域无关）：

```json
{
  "source": "human" | "explored",
  "status": "probation" | "trusted" | "degraded" | "dead",
  "version": 1,
  "intent_schema": {...},
  "success_count": 0,
  "fail_count": 0,
  "last_success": null,
  "last_failure": null
}
```

---

## 6. 通用引擎：调度流程（平台内置，跨 skill 复用）

引擎对外只暴露一个入口：

```
capability_call(skill_name, request) -> {status, data, error}
```

内部执行标准流程：

```
1. resolve(request)
   ├─ 第一级：domain_matchers / keyword 精确匹配 _index.json
   │    命中 → 进入 execute
   └─ 未命中或歧义 → 第二级：LLM 检索裁决
        （把 _index.json 中的成员摘要清单 + request 一起交给
         一次独立、轻量、单一职责的 LLM 调用，只做"选出0-N个候选id"
         这一件事，不携带主对话历史）
        └─ 有结果 → 进入 execute
        └─ 无结果 → 进入 explore

2. execute(member_id, request)
   → 调用对应 member 的 run(input)
   → 用 intent_schema 校验返回的 data
   ├─ 校验通过 → 更新 registry（success_count+1，可能触发转正）
   │    → 返回 {status: success, data}
   └─ 校验不过 / 抛异常 / 超时
        → 更新 registry（fail_count+1，可能触发降级）
        → 进入 explore

3. explore(request, intent_schema)
   → 按 capability.yaml 中 explorer 配置，构造一个真实 SubAgent（独立
     context/session，装配系统全部已注册通用工具 + 领域声明的底层原语）
     跑一次完整 agent turn（阶段二十起，取代早期"手写决策循环"实现）
   → 安全边界交给 PermissionGuard/sandbox/task.allowed_tools（不再由
     generative-capability 自造一份平行白名单）
   → 步数预算复用 task.max_turns，超出直接判定失败（不再自造计时循环）
   → 探索子agent通过 finish(data, script_source?) / report_failure(reason)
     两个工具收尾；finish 的 data 必须经过同一份 intent_schema 校验
   ├─ 校验通过 → 进入 distill
   └─ 校验不过 → 返回 {status: fail, error: 具体失败原因}（不编造数据）

4. distill(trace, intent_schema)
   → 若探索子agent在 finish 时一并提交了 script_source（自己判断"这段解法
     可参数化复用"），优先校验并落盘这份自带脚本；否则退回把探索动作序列
     蒸馏为参数化脚本（trace-replay 兜底路径，阶段二十前的原有策略）
   → 沙箱内自测该脚本，用 intent_schema 再校验一次
   ├─ 自测通过 → 原子化写入 members/、registry.json（status: probation）、
   │    _index.json（新增摘要，供下次检索命中）；meta.json 记录
   │    distill_source_kind（"script_source" | "trace_replay"）
   │    → 返回 {status: success, data}
   └─ 自测不通过 → 丢弃，不落盘，不污染检索池
      → 返回 {status: fail, error: "探索未能生成可靠方案"}
```

四步流程、检索两级过滤、状态机全部是引擎内置代码，**不因领域不同而重写**。skill 作者不实现任何流程代码。

---

## 7. Member 生命周期状态机（引擎内置，跨领域统一）

```
                 探索成功并自测通过
                        │
                        ▼
                  [probation]  ← 低信任度，允许被检索命中并执行
                    │       │
        连续成功N次 │       │ 任意一次执行失败
                    ▼       ▼
               [trusted]  [dead]（不再进入检索候选，registry保留供人工审查）
                    │
        连续失败M次 │
                    ▼
               [degraded] → 触发重新探索
                    │
          ┌─────────┴─────────┐
     探索成功              探索失败
          ▼                     ▼
   新版本(probation)         [dead]
```

---

## 8. 安全与成本边界（引擎强制约束，非skill可选项）

1. **工具范围受限，非自造白名单**：探索子agent的可用工具面由系统统一的
   `task.allowed_tools`/`allowed_tool_groups`（PermissionGuard/sandbox）决
   定，领域可通过 `capability.yaml` 声明希望额外桥接的底层原语（历史字段
   `explorer.tool_allowlist`/新字段 `explorer.allowed_tools`），但这只是
   "范围声明"，不再是 generative-capability 自造的一套平行安全体系
   （阶段二十，详见 `next_doc/generative_capability_explorer_rearch_plan.md`）。
2. **步数预算硬上限**：复用 `task.max_turns`，超限直接判失败返回，禁止无限重试。
3. **产物强制 schema 校验**：命中执行与探索生成，输出都必须过同一份 intent_schema 校验，不允许自我认定成功。
4. **蒸馏产物强制沙箱自测**：探索 trace 不能直接当脚本使用，必须先蒸馏、自测通过才允许落盘。
5. **原子化写入**：`distill` 落盘时必须同时更新 `registry.json` 与 `_index.json`，避免"脚本能跑但检索不到"或"检索能到但脚本已被清理"的不一致状态。
6. **定期健康巡检（低频后台任务）**：扫描长期未调用或长期 `dead` 的 member，清理或提示人工审查，防止检索池腐化膨胀。

---

## 9. 与静态 skill 系统的关系

| | 静态 skill | generative-capability skill |
|---|---|---|
| 适用场景 | 成员少、变化慢、通用性强（如 docx/pptx 处理） | 成员多、长尾、持续增长（如按站点/按API的定制能力） |
| 主 context 占用 | SKILL.md + 命中的 reference | 仅一行 category 摘要 |
| 新增能力方式 | 人工新增 reference | 人工预置 member，或探索自动生成 |
| 维护方式 | 人工维护文档一致性 | 引擎自动维护 registry/index + 定期健康巡检 |
| 可复用性 | 每个 skill 各自实现 | 调度引擎、状态机、检索逻辑全平台复用 |

两种类型可以在同一个 agent 系统中并存；`generative-capability` 的底层探索原语，往往正是依赖某个静态 skill（例如 `browser-core`）提供的通用操作能力。

---

## 10. 迁移路径：以 browser-cdp 为例

1. 从现有 `browser-cdp` 中拆出通用操作原语，独立为静态 skill `browser-core`（导航/截图标注/点击/输入/等待策略/反检测等，跨所有网站通用）。
2. 现有 172 个 `references/*-search.md` 及对应脚本，逐一转换为 `browser-site-scraper` 这个 `generative-capability` skill 下的 member：脚本改造为统一 `run(input)->dict` 签名，补充 `meta.json`（`source: human`，直接标记 `trusted`）。
3. 编写 `capability.yaml`：`domain_matchers` 基于各站点域名生成、`intent_schema_template` 定义抓取结果通用结构、`explorer` 指向 `browser-core` 作为 `base_tools`。
4. `tests/`、`scripts/eval_*.py`、`scripts/monitoring_*.py` 等开发期工具从 skill 目录中移出，归入项目工程目录，不进入 skill 索引。
5. 上线后，新网站抓取需求不再需要人工新增 reference 文档，由引擎的 resolve→execute→explore→distill 闭环自动补全；原有 172 个人工脚本作为高信任度的初始 member 集合直接复用。

---

## 11. 可复用性验证：泛化到其他领域

同一套引擎、同一套 member 接口规范，替换 `capability.yaml` 与 `explorer` 配置即可用于其他领域，无需改动引擎代码：

- **api-integration**：intent 为"调用某第三方 API 完成某操作"，member 为已知 API 调用脚本；未知 API 时探索分支尝试读取文档/试探 endpoint，蒸馏为新调用脚本。
- **doc-template-generation**：intent 为"按某公司特定格式生成文档"，member 为已知模板生成脚本；遇到新格式时探索分支解析样例文档，蒸馏出新模板脚本。

两者与 `browser-site-scraper` 的差异仅在于 `explorer.base_tools`（调用的底层原语不同）与 `intent_schema_template`（领域数据结构不同），调度骨架、状态机、检索两级过滤逻辑完全不变，验证了该方案作为"agent 系统通用扩展机制"的可复用性。

---

## 12. 实施优先级建议（分阶段）

1. **阶段一**：实现通用引擎骨架（resolve/execute 两步 + 确定性匹配 + 基础 registry/index 读写），先不接入探索能力，用现有人工脚本验证检索加载链路。
2. **阶段二**：接入 LLM 二级检索裁决，替代/补充关键词匹配的召回盲区。
3. **阶段三**：接入探索子agent（explore + distill），从单一领域（如 browser-site-scraper）试点，验证蒸馏产物质量与自测机制的可靠性。
4. **阶段四**：接入完整生命周期状态机（probation/trusted/degraded/dead）与定期健康巡检。
5. **阶段五**：抽象出跨领域复用的引擎 SDK，支持第二个 `generative-capability` skill 落地，验证方案泛化性。

---

## 13. 实施记录

### 阶段一 —— 已完成

**目标**：实现通用引擎骨架（resolve/execute 两步 + 确定性匹配 + 基础 registry/index 读写），
先不接入探索能力，用现有人工脚本验证检索加载链路。

**新增/修改文件**：

```
.claude/skills/_engine/capability_engine.py        # 新增：通用调度引擎
.claude/skills/browser-site-scraper/                # 新增：第一个 generative-capability skill
  SKILL.md
  capability.yaml
  _index.json
  registry.json
  explorer/prompt.md                                 # 占位，阶段三生效
  explorer/tool_allowlist.json                        # 占位，阶段三生效
  members/baidu/script.py                              # 包装既有 src/searchers/baidu_search.py
  members/baidu/meta.json
  members/zhihu/script.py                               # 包装既有 src/searchers/zhihu_search.py
  members/zhihu/meta.json
next_doc/generative-capability-skill-plan.md          # 本文档（重命名为全英文文件名）
```

**已实现能力**：

- `CapabilityEngine.resolve()`：第一级确定性匹配（`domain_pattern` / `keyword`），
  未命中且未注入 `llm_resolver` 时直接判定为 `miss`（LLM 裁决留待阶段二接入）。
- `CapabilityEngine.execute()`：动态加载 `members/<id>/script.py` 的 `run(input)->dict`，
  做最基础的 `intent_schema` 必填字段校验，并原子化更新 `registry.json` 的成功/失败计数
  与 `consecutive_failures`，触发 `probation -> trusted` / `-> degraded` 的状态流转。
- `CapabilityEngine.explore()` / `distill()`：按方案要求提供明确占位，未接入前统一返回
  `not_implemented` 及可读的原因说明，不允许静默失败或伪造成功。
- `capability_engine.py` 提供命令行自测入口，可直接对 `browser-site-scraper` 验证
  hit+执行失败、miss 两条路径。

**验证结果**（沙盒环境无可用真实浏览器/CDP 连接，属预期情况）：

1. `--url https://www.baidu.com/s?wd=test` → `resolve` 命中 `baidu`（`domain_pattern_match`）
   → `execute` 因无可用浏览器返回失败 → `registry.json` 中 `baidu.fail_count` 正确 +1，
   `consecutive_failures` 正确 +1 → 落入 `explore()` 占位，返回 `not_implemented`。
2. `--url https://www.some-unknown-site.com/...` → `resolve` 未命中任何 member
   （`no_match`）→ 落入 `explore()` 占位，返回 `not_implemented`。

两条路径均符合方案第 6 节设计的调度流程，`registry.json` 的原子写入与状态记录逻辑验证通过。

**已知遗留（留给后续阶段）**：

- `browser-core` 尚未从 `browser-cdp` 正式独立拆分为静态 skill，`members/*/script.py`
  当前直接依赖 `browser-cdp/src/searchers/*`，属于过渡期写法，阶段五附近应回头清理。
- `_validate_schema` 仅做必填字段存在性校验，未接入完整 JSON Schema 校验（类型/结构），
  待阶段三上线后一并补齐，避免探索蒸馏产物被浅层校验放过。
- LLM 二级检索尚未接入（阶段二）；探索子agent 与蒸馏逻辑尚未接入（阶段三）；
  生命周期状态机中 `degraded -> dead` 的重新探索判定依赖阶段三完成后才能触发。

### 阶段二 —— 已完成

**目标**：接入 LLM 二级检索裁决，替代/补充关键词匹配的召回盲区。

**新增/修改文件**：

```
.claude/skills/_engine/llm_resolver.py               # 新增：LLM 二级检索裁决器
.claude/skills/_engine/capability_engine.py           # 修改：resolve() 捕获 llm_resolver
                                                        # 异常并与 no_match 语义区分；
                                                        # __main__ 增加 --stub-llm-hit 调试参数
.claude/skills/browser-site-scraper/SKILL.md          # 更新阶段说明
```

**已实现能力**：

- `build_llm_resolver()`：真实调用 Anthropic Messages API 的检索裁决器。系统提示词职责
  单一——只做"从候选清单里选出匹配的 member_id"，输入仅为请求文本 + 候选摘要（不含
  member_id 之外的字段、不含主对话历史），输出强制要求纯 JSON，解析失败会显式抛异常
  而不是静默返回空列表。
- `build_stub_resolver()`：用于离线自测/CI 的桩实现，验证引擎与 resolver 的接线逻辑，
  不代表真实语义裁决质量，命令行通过 `--stub-llm-hit <member_id>` 触发。
- `CapabilityEngine.resolve()`：明确区分两种"没有命中"的语义——
  ① `no_match`：确定性匹配和 LLM 均未找到匹配（或未注入 resolver）；
  ② `llm_error: ...`：LLM 调用本身失败（网络/未配置 API key 等环境问题）。
  两者都会导致后续走向 `explore()`，但 reason 字段保留了可诊断的区分度，
  避免把"环境配置问题"误判成"语义上确实没有这个能力"。
- 引擎对 LLM 返回的 `member_ids` 做候选集合过滤，防止模型幻觉出清单之外不存在的 id
  被当作命中结果传递给 `execute()`。

**验证结果**（沙盒环境未配置 `ANTHROPIC_API_KEY`，属预期情况）：

1. 真实 `build_llm_resolver()`，对一个确定性匹配（domain/keyword）都不命中的请求
   （`https://random-forum.example/...`）调用 `resolve()` → 正确捕获异常并返回
   `status=miss, reason="llm_error: 未配置 ANTHROPIC_API_KEY..."`，未被误判为 `no_match`。
2. 桩实现 `--stub-llm-hit baidu`：对同一请求触发 `call()` 全流程 → `resolve_reason` 正确
   显示为 `llm_match`，随后进入 `execute(baidu, ...)`（因沙盒无可用浏览器而执行失败，
   属预期），验证了"确定性匹配未命中 → LLM 裁决命中 → 执行"这条链路接线正确。
3. 桩实现返回一个候选集合外的虚构 id（`nonexistent_id`）→ 引擎正确过滤，
   最终 `resolve_reason` 回落为 `no_match`，验证了防幻觉过滤生效。

**已知遗留（留给后续阶段）**：

- 真实语义裁决质量（LLM 是否能准确从几十/上百候选中选对）尚未在有效 API key 环境下
  做实际调用验证，仅验证了工程接线；建议在阶段五做真实场景下的准确率评估。
- 候选摘要清单规模变大后（比如 `_index.json` 增长到几百个 member）的 prompt 体量与
  裁决延迟尚未做压测，超过一定规模可能需要引入分批裁决或先按大类目再二次检索的策略。

### 阶段三 —— 已完成

**目标**：接入探索子agent（explore + distill），从单一领域（`browser-site-scraper`）
试点，验证蒸馏产物质量与自测机制的可靠性。

**新增/修改文件**：

```
.claude/skills/_engine/explorer_runtime.py           # 新增：探索子agent决策循环
.claude/skills/_engine/distiller.py                  # 新增：trace -> 参数化脚本蒸馏器
.claude/skills/_engine/tool_runtime.py                # 新增：蒸馏脚本运行时工具执行器注入点
.claude/skills/_engine/capability_engine.py           # 修改：接入 explore_runner/tool_executor
                                                        # 构造参数；explore()/_distill() 实现；
                                                        # execute() 执行前注入 tool_runtime；
                                                        # call() 打通 degraded->重新探索->
                                                        # trusted(probation)/dead 闭环；
                                                        # __main__ 增加 --stub-explore-success/
                                                        # --stub-explore-fail 调试参数
.claude/skills/browser-site-scraper/SKILL.md          # 更新阶段说明
```

**已实现能力**：

- `explorer_runtime.build_llm_explorer()`：真实调用 Anthropic Messages API 的探索循环。
  只接受 `request/intent_schema/explorer_config` 三样输入，不携带主对话历史；通过
  `tools` 参数把 `capability.yaml -> explorer.tool_allowlist` 中的工具名暴露给模型，
  并额外注入两个决策元工具 `finish`（提交最终数据）与 `report_failure`（如实报告
  失败原因）。模型试图调用白名单之外的工具会被引擎拒绝执行，并把拒绝原因作为一次
  失败的工具调用结果反馈给模型（不静默放行、不越权执行）；循环受 `max_steps`/
  `max_seconds` 硬预算约束，超出直接终止并判定失败。
- `distiller.distill()`：把 `ExploreTrace` 中的动作序列蒸馏为参数化脚本——把与本次
  `request` 相同的 `target.url`/`query` 值替换为占位符，其余步骤原样固化为对
  `tool_runtime` 注入的执行器的重放序列（而非把 trace 原样保存当脚本用）。蒸馏产物
  必须先在独立的沙箱临时目录中自测（重新跑一遍生成的 `run()` 并用 `intent_schema`
  再校验一次数据），自测通过后才把 `script.py`/`meta.json`/`registry.json`/
  `_index.json` 一起原子化落盘（先写各自的 `.tmp` 临时文件，全部写完后再逐一
  `replace()`，缩小"部分文件已提交、部分未提交"的窗口）；自测不通过则直接丢弃临时
  目录，不落盘、不污染检索池，按方案文档第 6 节要求返回明确的失败原因。
- `tool_runtime.py`：一个模块级的执行器注入点（`set_tool_executor`/
  `get_tool_executor`），蒸馏脚本本身不实现任何具体浏览器控制逻辑，只保存"调用哪个
  工具、传什么参数"的动作序列，真正执行交给调用方注入的执行器；`execute()` 在加载
  任意 member 脚本前都会先注入当前引擎持有的 `tool_executor`，保证探索蒸馏出的
  member 与人工手写的 member 在同一套统一接口下运行。
- `CapabilityEngine.explore(request, reexplore_member_id=None)`：区分"全新领域探索"
  与"针对已 degraded member 的重新探索"两种场景。前者蒸馏成功后按目标域名/请求文本
  生成新的 `member_id` 落盘为新 member（`status: probation`, `version: 1`）；后者
  蒸馏成功则原地复用同一个 `member_id`、版本号 +1、状态回到 `probation`，蒸馏失败则
  按 `capability.yaml` 中 `dead_after_reexplore_fail` 配置把该 member 标记为 `dead`
  （对应方案文档第 7 节生命周期状态机 `degraded -> 重新探索 -> trusted/dead` 的完整
  闭环）。`explore_runner`/`tool_executor` 未注入时返回明确的错误信息，不伪造成功。
- `CapabilityEngine.call()`：命中的候选全部执行失败时，若该 member 当前状态已是
  `degraded`，自动带上 `reexplore_member_id` 触发针对该 member 的重新探索；否则走
  全新探索路径。miss 场景保持不变，直接走全新探索。

**验证结果**（沙盒环境无可用真实浏览器/CDP 连接、未配置 `ANTHROPIC_API_KEY`，
均通过桩探索器/桩工具执行器验证接线逻辑，属预期情况）：

1. 全新领域探索：对一个确定性匹配未命中的新域名请求，注入桩探索器（模拟
   `browser_navigate` + `browser_extract_content` 两步后 `finish`）与桩工具执行器
   → `resolve` 返回 `no_match` → `explore()` 成功 → `distill()` 沙箱自测通过 → 原子
   落盘新 member（`members/some-new-site/`，`registry.json` 新增
   `status: probation`，`_index.json` 新增摘要及基于域名自动推断的
   `domain_pattern`）→ 最终 `call()` 返回 `status: success`。
2. 落盘后免探索复用：不注入 `explore_runner`，仅注入 `tool_executor`，对同一请求
   再次 `call()` → `resolve` 通过 `domain_pattern_match` 直接命中刚蒸馏出的 member
   → `execute()` 成功，验证了蒸馏产物本身可被后续请求直接检索命中执行，无需每次
   都重新探索。
3. 连续失败触发 degraded：对 `baidu`（沙盒无可用浏览器，执行必然失败）连续调用
   3 次且不注入 `explore_runner` → 每次都在 `execute()` 失败后尝试 `explore()`，
   因未注入而返回 `not_implemented`；`registry.json` 中 `baidu` 的
   `consecutive_failures` 正确累积，第 3 次后状态正确流转为 `degraded`。
4. degraded 重新探索失败 -> dead：对已 `degraded` 的 `baidu` 再次 `call()`，注入
   一个固定失败的桩探索器 → `execute()` 失败 → 检测到状态为 `degraded`，带
   `reexplore_member_id="baidu"` 调用 `explore()` → 桩探索器失败 →
   `_handle_reexplore_failure()` 按 `dead_after_reexplore_fail: true` 把
   `baidu` 标记为 `dead`，验证通过。
5. degraded 重新探索成功 -> probation + 版本升级：同样从 `degraded` 状态出发，改为
   注入一个固定成功的桩探索器与桩工具执行器 → `distill()` 复用同一个
   `member_id="baidu"` 落盘 → `registry.json` 中 `baidu.status` 回到
   `probation`，`meta.json` 中 `version` 从 1 变为 2，验证了"原地升版本号"而非
   "生成一个新的重复 member" 的设计。

**已知遗留（留给后续阶段）**：

- `explorer_runtime.build_llm_explorer()` 与真实 `browser-core` 工具集合的对接
  （即真正可执行的 `tool_executor` 实现）尚未完成——这依赖 `browser-core` 从
  `browser-cdp` 独立拆分为静态 skill（迁移路径第 1 步），当前仍是本文档"已知遗留"
  中标注的过渡期写法。在此之前，探索能力只能通过桩实现验证接线逻辑，无法在生产
  环境中真正抓取新网站。
- 蒸馏策略目前是"逐步骤参数化重放"，还没有处理更复杂的情况：探索过程中出现的
  条件分支（如"如果出现弹窗则先关闭"）、循环滚动加载等非线性动作序列，当前会被
  原样按顺序固化，遇到这类场景蒸馏出的脚本鲁棒性可能不足，建议阶段五做真实场景
  下的蒸馏质量评估时一并覆盖。
- `_validate_schema` 仍只做必填字段存在性校验，未接入完整 JSON Schema 校验
  （类型/结构/嵌套），阶段一遗留问题延续到本阶段，建议在完整生命周期状态机与
  健康巡检（阶段四）之前一并补齐，避免探索蒸馏产物被浅层校验放过。

### 阶段四 —— 已完成

**目标**：接入完整生命周期状态机（probation/trusted/degraded/dead）与定期健康巡检。

**新增/修改文件**：

```
.claude/skills/_engine/schema_validator.py            # 新增：无第三方依赖的最小 JSON Schema 校验器
.claude/skills/_engine/health_patrol.py               # 新增：定期健康巡检（低频后台任务）
.claude/skills/_engine/capability_engine.py            # 修改：execute() 改用 schema_validator 做
                                                         # 完整校验（不再是浅层必填字段检查）
.claude/skills/_engine/distiller.py                    # 修改：蒸馏产物自测同样改用 schema_validator
.claude/skills/browser-site-scraper/capability.yaml     # 修改：新增 health_patrol_stale_days /
                                                         # health_patrol_dead_retention_days 配置
.claude/skills/browser-site-scraper/SKILL.md           # 更新阶段说明
```

**已实现能力**：

- `schema_validator.validate(data, schema) -> list[str]`：覆盖 intent_schema_template
  实际会用到的关键字子集（`type`/`required`/`properties`/`items`/`enum`），递归校验
  嵌套的 object/array 结构，类型不匹配时携带具体路径（如 `$.results[0].title`）与
  期望/实际类型的错误信息，而不只是布尔值。刻意不引入 `jsonschema` 第三方依赖，
  保持引擎自身零外部依赖、可在任意沙箱环境直接运行；不支持的高级关键字
  （`oneOf`/`pattern` 等）会被忽略而非报错，避免过度拒绝。
- `CapabilityEngine.execute()` 与 `distiller.distill()` 现在共用同一套校验逻辑，
  解决了阶段一/阶段三"已知遗留"中明确记录的问题——此前"字段存在但类型/结构不对"
  的数据会被浅层校验放过，现在会被正确判定为失败并计入 `fail_count`/触发降级，
  探索蒸馏产物同理，类型不对的探索结果不会被误固化为新 member。
- `health_patrol.run_patrol(skill_dir, fix_inconsistencies=False, apply_cleanup=False)`：
  - **一致性检查**（只读）：交叉比对 `_index.json` / `registry.json` /
    `members/` 目录三者的 member id 集合，识别出四类不一致
    （`index_without_registry` / `registry_without_index` /
    `member_dir_without_registry` / `registry_without_member_dir`），对应方案
    文档第 8 节明确点名的"脚本能跑但检索不到"与"检索能到但脚本已被清理"两种
    腐化状态，以及新增的"registry 有状态但脚本已丢失"情形。
  - **不一致修复**（`fix_inconsistencies=True` 才生效）：以 `registry.json`
    （member 真实生命周期状态的唯一权威来源）为准做最小修复——移除 index 中
    找不到对应状态记录的孤立摘要；为 registry 中存在但 index 缺失摘要的
    member 补一条最小摘要，确保能被检索到。`members/` 目录与 registry 之间
    的不一致不自动修复（脚本文件本身无法凭空补出来），只提示人工审查。
  - **长期未调用检测**（只读）：对超过 `health_patrol_stale_days`
    （默认 30 天）没有任何成功/失败记录的非 dead member 生成 `stale` 提示；
    对自建立以来从未被检索命中执行过一次的 member 单独提示，不计入清理候选。
  - **dead 过期检测与清理**：对已进入 `dead` 状态且超过
    `health_patrol_dead_retention_days`（默认 14 天）保留期的 member 生成
    `dead_expired` 提示；仅当显式传入 `apply_cleanup=True` 时才真正删除
    `members/<id>/` 目录并从 `registry.json`/`_index.json` 中移除，删除前会把
    该 member 的 `meta.json` 内容记入报告，保证清理动作可审计、不是"静默丢弃"。
    默认（不传 `apply_cleanup`）只生成"建议清理"清单，符合方案文档"清理或提示
    人工审查"里"提示"优先于"清理"的表述，避免自动化巡检误删还在被低频使用的
    能力。
  - 提供命令行入口，支持 `--fix-inconsistencies` / `--apply-cleanup` 两个显式
    开关，默认两者都不开启（纯只读巡检），便于先接入定时任务观察报告，再决定
    要不要打开自动修复/清理。

**验证结果**：

1. `schema_validator`：对 `{"results": [...]}` 结构做类型正确/必填字段缺失/
   字段类型错误/嵌套元素类型错误/无 schema 时兜底为"非 None 即通过" 五种场景
   分别验证，返回的错误信息均携带正确的路径与类型描述。
2. `execute()`/`distill()` 接入完整校验后回归测试：对一次成功的探索
   （`{"results": [...]}` 结构合法）验证仍能正常蒸馏落盘；额外构造一次
   `results` 字段类型为字符串（不满足 `type: array`）的探索结果，验证
   `distill()` 正确在 `_validate_schema` 阶段拒绝蒸馏，不再像浅层校验那样
   因为字段存在就误判通过。
3. `health_patrol`：构造一个人为不一致的沙盒环境
   （`_index.json` 中的孤立摘要 `orphan_in_index`、`registry.json` 中缺失
   摘要且缺脚本目录的 `ghost_member`、`registry.json` 中一个时间戳造假为
   2020 年、状态为 `dead` 的 `old_dead` 且带完整 `members/old_dead/` 目录），
   先以只读模式跑一遍 `run_patrol()`，确认四类不一致 + `stale` + `dead_expired`
   均被正确识别且未发生任何写操作；再以
   `fix_inconsistencies=True, apply_cleanup=True` 跑一遍，确认
   `orphan_in_index` 被移除、`ghost_member`/`old_dead` 被补全摘要、
   `old_dead` 被真正清理（`members/old_dead/` 目录删除、`registry.json`/
   `_index.json` 中对应条目移除，且报告中带有清理前的 `meta.json` 备份内容），
   而没有脚本目录但非 `dead` 状态的 `ghost_member` 被正确保留供人工审查、
   未被误删。

**已知遗留（留给后续阶段）**：

- `health_patrol.py` 目前用 `last_success`/`last_failure` 的较大值近似"进入
  dead 状态的时间点"（`_dead_since()`），registry.json 尚未单独记录状态流转
  时间戳；如果一个 member 长期 degraded 后才被判 dead，且此前很久没有失败记录
  更新，这个近似值可能偏早，导致保留期计算不够精确。更准确的做法是在
  `_apply_lifecycle()`/`_handle_reexplore_failure()` 写入 `registry.json` 时
  额外记录 `status_changed_at` 时间戳，建议阶段五结合真实运行数据一并补齐。
- `health_patrol.py` 尚未接入任何调度器（cron/定时任务），当前只是一个可被
  低频调用的独立脚本/函数，实际接入自动化调度属于宿主 agent 框架
  （`mini_agent` 自身的 cron/goal 系统）的集成工作，不在本方案引擎职责范围内，
  仅在文档中给出命令行入口。
- `schema_validator` 仍是"实际会用到的子集"而非完整 JSON Schema 规范
  （不支持 `oneOf`/`anyOf`/`pattern`/数值范围等），如果后续领域
  （第 11 节 `api-integration`/`doc-template-generation`）的 `intent_schema`
  需要更严格的约束，需要按需扩展而非现在就引入完整实现。

### 阶段五 —— 已完成

**目标**：抽象出跨领域复用的引擎 SDK，支持第二个 `generative-capability` skill
落地，验证方案泛化性。

**新增/修改文件**：

```
.claude/skills/_engine/__init__.py                    # 新增：SDK 打包入口，
                                                         # re-export 精简公开 API，
                                                         # 内部各模块不改一行
.claude/skills/browser-site-scraper/SKILL.md            # 更新阶段说明
.claude/skills/doc-template-generation/                 # 新增：第二个 generative-
                                                         # capability skill（对应
                                                         # 第 11 节 "doc-template-
                                                         # generation" 示例）
  SKILL.md
  capability.yaml
  _index.json
  registry.json
  explorer/prompt.md                                     # 占位，doc-core 尚未实现
  explorer/tool_allowlist.json                            # 占位，doc-core 尚未实现
  members/standard_report/script.py                        # 纯逻辑 member，不依赖
                                                            # 任何底层原语 skill
  members/standard_report/meta.json
next_doc/generative-capability-skill-plan.md              # 本文档（阶段五记录）
```

**已实现能力**：

- **SDK 打包**（不改动任何内部调度/状态机逻辑）：`_engine/__init__.py` 把
  `_engine` 目录变成一个可被 `from _engine import CapabilityEngine, ...`
  的包。做法是先把自身目录插入 `sys.path`（保证包内各模块原有的 flat import
  语句——如 `capability_engine.py` 里的 `from explorer_runtime import ...`——
  不用改一行，避免为了"打包"而引入行为变化风险），再统一 re-export 一份精简
  的公开 API：`CapabilityEngine`/`ResolveResult`/`ExecuteResult`/
  `CapabilityCallResult`、`build_llm_resolver`/`build_stub_resolver`、
  `ExploreStep`/`ExploreTrace`/`build_llm_explorer`/`build_stub_explorer`、
  `distill`/`DistillResult`、`validate_schema`、`run_patrol`/`PatrolReport`/
  `PatrolFinding`、`set_tool_executor`/`get_tool_executor`。调用方从此只需要
  认识这一份接口，不需要知道引擎内部具体拆成了哪几个文件。
- **第二个 generative-capability skill**：`doc-template-generation`，对应方案
  文档第 11 节"可复用性验证"里提到的示例（intent 为"按某公司特定格式生成文档"）。
  仅通过 `capability.yaml`（`domain_matchers` 换成基于 `target.template_name`
  的关键词匹配、`intent_schema_template` 换成 `{document: {format, sections}}`、
  `explorer.base_tools` 换成占位的 `doc-core`）与预置 member `standard_report`
  （纯逻辑实现：把 `{title, body_sections}` 渲染为 markdown 分段结构，不依赖
  任何外部 skill）落地，`capability_engine.py`/`distiller.py`/
  `explorer_runtime.py`/`schema_validator.py`/`health_patrol.py` 全部零改动。

**验证结果**：

1. 通过 `from _engine import CapabilityEngine, ...` 直接 import 成功（无需
   调用方手写 `sys.path` hack），验证 SDK 打包本身接线正确。
2. `doc-template-generation` 命中路径：`{"target": {"template_name":
   "standard_report"}, ...}` → `resolve()` 通过 `keyword_match` 命中
   `standard_report` → `execute()` 成功，返回渲染后的 `document.sections`。
3. `doc-template-generation` 命中但执行失败（`content` 缺字段）→
   `execute()` 因 `run()` 内部校验失败而返回 fail → 未注入 `explore_runner`
   时正确落回 `not_implemented`，与 `browser-site-scraper` 行为一致（因为这是
   引擎的通用行为，不是本 skill 自己实现的）。
4. `doc-template-generation` 全新领域探索（miss → 桩探索器成功 → 蒸馏落盘 →
   新 member `weekly_update` 以 `probation` 状态写入 `registry.json`/
   `_index.json`）→ 用相同请求文本再次调用（不注入 `explore_runner`），
   `resolve_reason` 正确变为 `keyword_match`（而非 `explored`），确认蒸馏产物
   无需每次重新探索即可被后续请求直接检索命中执行——与阶段三对
   `browser-site-scraper` 验证过的行为一致，证明同一套 `explore`/`distill`
   闭环在第二个领域下同样成立。
5. 用 `browser-site-scraper` 阶段三实施记录里原样记录的桩探索场景（单步
   `browser_navigate`、`tool_executor` 为 `lambda name, inp: {"ok": True,
   "echo": inp}`）重新跑了一遍，**未能复现"探索成功→蒸馏落盘"的结果**，
   而是在蒸馏自测阶段失败（`重放完成但未获得可用数据`）。逐行核对
   `distiller.py::SCRIPT_TEMPLATE` 后确认原因：模板里 `_FINAL_DATA_TEMPLATE`
   在 `distill()` 生成脚本代码时会被无条件 `.replace(..., "None")`，即蒸馏
   出的脚本从不把探索阶段拿到的 `trace.data` 直接固化进去，完全依赖"重放
   动作序列后最后一步工具调用的返回值里恰好带有形状正确的 `data` 字段"——
   这要求自测/生产环境注入的 `tool_executor` 在被重放的最后一步就返回最终
   数据（例如真实 `browser_extract_content`/`doc_render` 这类"提取/产出"
   类工具通常会这样），而不是任意回显式的桩执行器。这不是本阶段引入的
   回归，是阶段三代码自身一直如此；阶段三实施记录里"验证通过"的表述与
   当前代码行为对不上，本阶段如实记录这一发现，不做静默掩盖，也不在阶段五
   范围内改动 `distiller.py` 的这部分行为（属于蒸馏策略本身的设计取舍，
   见下方"已知遗留"）。改用一个"最后一步工具返回带 `data` 字段"的
   `tool_executor`（在 `doc-template-generation` 上用 `doc_render` 模拟）
   后，完整 explore→distill→复用 闭环按预期跑通（即上面第 4 条）。

**已知遗留（留给后续阶段）**：

- 上一条发现的蒸馏产物"依赖重放最后一步工具输出里的 `data` 字段"这一设计，
  对真实浏览器/文档场景是合理的（提取类工具的返回值本来就应该是最终数据），
  但对自测/CI 场景不太友好——任何不精心构造"最后一步返回正确 `data`"的
  桩 `tool_executor`，蒸馏都会在自测阶段失败，容易被误判为"探索失败"而非
  "自测环境没配对"。建议后续阶段要么在 `ExploreTrace.data`（探索阶段已经
  拿到的、经过 `intent_schema` 校验的最终数据）与"重放推导出的 data"之间
  提供一个可选的一致性兜底（如两者不一致时报警而非直接判失败，或允许
  `capability.yaml` 声明"信任 trace.data"的领域级开关），要么在文档/测试
  辅助函数层面把"桩 `tool_executor` 必须让最后一步返回 `data`"这个约束
  写得更显式，避免下一个接入 `generative-capability` 的 skill 作者重复
  踩同一个坑。
- `doc-template-generation` 的 `explorer/tool_allowlist.json` 中 `doc-core`
  仍是占位声明，真正的文档解析/写入原语尚未实现（与 `browser-site-scraper`
  当年对 `browser-core` 的处理方式一致），因此该 skill 的探索能力目前也
  只能通过桩探索器验证接线逻辑，不代表已具备"学会一种全新公司文档格式"的
  生产能力——这不影响阶段五本身的验证目标（验证调度骨架能否零改动跨领域
  复用），特此说明避免造成能力已完备的误解。
- SDK 目前仍以 `_engine` 为包名对外暴露（`from _engine import ...`），
  没有做成本地可独立 `pip install` 的分发包（无 `pyproject.toml`/`setup.py`），
  因为当前唯二两个调用方（`browser-site-scraper`、`doc-template-generation`）
  都在同一个 `.claude/skills/` 目录下，用同级目录 `sys.path` 约定已经够用；
  如果未来 `generative-capability` skill 需要被仓库之外的项目复用，再考虑
  拆成独立可安装包。

### 阶段六 —— 已完成

**目标**：第 12 节列出的五个阶段均已完成，本阶段不新增流程能力，而是回应
阶段四、阶段五实施记录中明确标注、且已经给出解决方向的两条"已知遗留"，
避免它们无限期悬而不决。

**新增/修改文件**：

```
.claude/skills/_engine/capability_engine.py             # 修改：_apply_lifecycle() /
                                                           # _handle_reexplore_failure()
                                                           # 状态流转时写入 status_changed_at
.claude/skills/_engine/distiller.py                      # 修改：新增 distill.trust_trace_data
                                                           # 一致性兜底；_atomic_persist() 写入
                                                           # status_changed_at 与
                                                           # distill_used_trace_data_fallback
.claude/skills/_engine/health_patrol.py                  # 修改：_dead_since() 优先读取
                                                           # status_changed_at，存量数据缺失时
                                                           # 退化为原近似算法
.claude/skills/browser-site-scraper/capability.yaml      # 修改：新增 distill.trust_trace_data
                                                           # 开关（默认 false）
.claude/skills/browser-site-scraper/SKILL.md             # 更新阶段说明
```

**已实现能力**：

- **`status_changed_at` 精确时间戳**（回应阶段四"已知遗留"第 1 条）：
  `CapabilityEngine._apply_lifecycle()` 在 `probation -> trusted`、
  `(trusted|probation) -> degraded` 发生时写入该字段；
  `_handle_reexplore_failure()` 在标记 `dead` 时写入；`distiller._atomic_persist()`
  在新建/重探索成功回到 `probation` 落盘时同样写入。`health_patrol._dead_since()`
  改为优先读取这个精确值，只有 registry.json 中缺该字段的存量数据才退化为
  原来"用 `last_failure` 或 `meta.json` mtime 近似"的算法，新旧数据都能被
  正确处理，不需要一次性迁移存量 `registry.json`。
- **`distill.trust_trace_data` 一致性兜底开关**（回应阶段五"已知遗留"第 1
  条）：`capability.yaml` 新增可选的 `distill: {trust_trace_data: true}`
  领域级声明。`distiller.distill()` 在蒸馏时先取"探索阶段最后一个真实工具
  步骤"（跳过 `finish`/`report_failure` 决策元步骤）的输出，只有该输出确实
  取不到可用 `data`、且该开关显式打开时，才把探索阶段已经拿到、且已通过
  `intent_schema` 校验的 `trace.data` 作为蒸馏脚本 `_TRACE_DATA_FALLBACK`
  常量嵌入；重放能正常取到 `data` 时，兜底常量恒为 `None`，不影响"能参数化
  复用就参数化复用"的默认优先级。是否用到兜底会如实记入新 member 的
  `meta.json -> distill_used_trace_data_fallback` 字段，保持可审计——这不是
  放宽校验标准，而是把"探索阶段已经验证过的数据"当最后一道防线，而不是让
  同一份数据的可靠性判断在探索阶段和蒸馏自测阶段用两套不一致的标准。
  `browser-site-scraper` 的 `capability.yaml` 默认关闭该开关（真实
  `browser-core` 提取类工具通常最后一步就会直接返回 `data`，不需要开启），
  仅在文档中说明该开关主要面向自测/CI 场景。

**验证结果**：

1. 复现阶段五实施记录第 5 条描述的失败场景（沙盒 `browser-site-scraper` 副本，
   `trust_trace_data: false`，单步 `browser_navigate` + 桩执行器
   `lambda name, inp: {"ok": True, "echo": inp}`）→ 确认与阶段五记录一致，
   仍在蒸馏自测阶段失败（`重放完成但未获得可用数据`），验证本阶段修改前
   问题确实存在、未被静默掩盖。
2. 将同一份沙盒 `capability.yaml` 的 `trust_trace_data` 改为 `true`，
   其余条件不变，重新调用 `capability_engine.py --stub-explore-success` →
   `call()` 返回 `status: success`，新 member `some-new-site` 的
   `meta.json` 中 `distill_used_trace_data_fallback: true`，`registry.json`
   对应条目 `status: probation` 且带 `status_changed_at`，证明兜底开关按
   预期生效、且不影响原有蒸馏落盘流程（脚本/meta/registry/index 仍然
   原子化一起写入）。
3. 对同一新 member，不注入 `explore_runner`（只注入 `tool_executor`）用
   相同请求再次 `call()` → 通过 `domain_pattern_match` 直接命中并执行成功，
   证明开启该开关落盘的 member 与普通蒸馏产物一样可被后续请求直接检索
   复用，不需要每次都重新走兜底逻辑。
4. 连续 3 次调用 `baidu`（沙盒无可用浏览器且不注入 `explore_runner`）→
   状态如预期流转为 `degraded`，`registry.json` 中新增的
   `status_changed_at` 与流转发生的时刻一致。
5. 人为把 `baidu` 标记为 `dead` 且设置 `status_changed_at` 为 2020 年初
   （模拟"很久以前进入 dead"），运行 `health_patrol.run_patrol()` → 正确
   识别为 `dead_expired` 且天数计算精确对应设置的时间戳，而不是像旧近似
   算法那样可能受 `last_failure` 时间偏差影响，验证 `_dead_since()` 优先级
   切换生效。

**已知遗留（留给后续阶段）**：

- `trust_trace_data` 兜底目前是"探索最后一步无 data 就整体信任
  `trace.data`"的粗粒度判断，没有做"重放出的 `last_output.data` 与
  `trace.data` 都存在但内容不一致"时的报警机制（阶段五遗留里提到的另一个
  可选方向）；如果未来某个领域的探索/重放之间容易出现语义漂移（如目标页面
  内容随时间变化），建议在此基础上补充"两者都存在但不一致"的显式报警分支，
  而不是简单地"重放优先，兜底止步于重放为空的情况"。
- 本方案第 12 节列出的五个实施阶段、以及阶段四/阶段五各自记录的"已知遗留"
  中偏架构性的两项——`browser-core`/`doc-core` 从领域 skill 中真正独立拆分
  为静态 skill、`schema_validator` 扩展到完整 JSON Schema 关键字集合——仍待
  后续阶段推进，本阶段范围内不涉及，避免在没有真实业务驱动前过度设计。

### 阶段七 —— 已完成

**目标**：回应"引擎代码放在 `.claude/skills/_engine`（skill 内容目录）
是否合理"的架构复核。结论是不合理，原因不是风格问题，是三个可验证的实际
后果：(1) `src/mini_agent` 里没有任何地方引用这套引擎代码，agent 运行时
完全没有工具能触发 `capability_call()`，阶段一到阶段六的"验证结果"全部
只能靠手动跑 CLI 自测入口完成；(2) 没有进 `tests/` 目录的 pytest 回归
覆盖；(3) 模块间互相 import 要靠 `sys.path.insert(...)` 手工塞目录，这正是
"这坨代码不该待在 skill 空间里"的直接症状。方案文档本身在第 1 条设计原则
（"流程与领域分离"）、第 9 节表格（"调度引擎、状态机、检索逻辑全平台复用"）、
第 10 节第 4 点（"`tests/`、`scripts/eval_*.py` 等开发期工具从 skill 目录中
移出，归入项目工程目录"）里已经隐含了这个结论，本阶段是把这个结论落到实处。

**改动范围**：两层拆分，不是整体搬家——引擎"代码"迁入主项目，各 skill 的
"声明式配置与运行时数据"留在原地。

```
迁移（新增，主项目正常子包，模块间改为相对 import，不再需要 sys.path hack）：
src/mini_agent/skills/generative_capability/__init__.py         # 新增：公开 API re-export
src/mini_agent/skills/generative_capability/capability_engine.py # 从 .claude/skills/_engine/ 迁入
src/mini_agent/skills/generative_capability/distiller.py         # 同上
src/mini_agent/skills/generative_capability/explorer_runtime.py  # 同上
src/mini_agent/skills/generative_capability/health_patrol.py     # 同上
src/mini_agent/skills/generative_capability/llm_resolver.py      # 同上
src/mini_agent/skills/generative_capability/schema_validator.py  # 同上
src/mini_agent/skills/generative_capability/tool_runtime.py      # 同上

删除：
.claude/skills/_engine/                                          # 整个目录删除，内容已迁移

新增（接线，让 agent 真正能调用到引擎）：
src/mini_agent/tools/capability_call.py         # 新增：capability_call 工具
src/mini_agent/agent/core.py                    # 修改：Agent.__init__ 注册 capability_call 工具
src/mini_agent/skills/__init__.py               # 修改：Skill 增加 skill_type/category_summary/
                                                   # is_generative_capability；_parse_skill() 解析这两个
                                                   # frontmatter 字段；build_context() 对这类 skill 只注入
                                                   # 一行摘要不整段注入正文；get_catalog() 附带标记

新增（回归测试，替代此前"手动 CLI 验证"）：
tests/test_generative_capability_engine.py      # 新增：11 个测试用例，覆盖包 import、
                                                   # resolve/execute/explore/distill 全流程、
                                                   # 第二领域复用、SkillLoader 特殊处理、
                                                   # capability_call 工具边界条件

保留原地不变（声明式配置与运行时数据，本阶段未改动内容，仅是路径引用不再
指向已删除的 `_engine`）：
.claude/skills/browser-site-scraper/{SKILL.md, capability.yaml, explorer/, _index.json, registry.json, members/}
.claude/skills/doc-template-generation/{同上结构}
```

**已实现能力**：

1. **引擎从 skill 空间迁入主项目正常子包**：`mini_agent.skills.
   generative_capability` 现在可以被正常 `import`，不需要任何 `sys.path`
   操作；包内 `capability_engine.py`/`distiller.py`/`explorer_runtime.py`
   等互相引用改为 `from .xxx import yyy` 相对 import。唯一保留的
   `sys.path` 用法是 `CapabilityEngine.execute()` 与
   `distiller._sandbox_run()` 加载/自测 member 脚本文件——这些是运行时
   按路径动态生成、`importlib` 加载的独立文件，不属于本包，脚本内部对
   `tool_runtime` 的引用仍是 flat import（`SCRIPT_TEMPLATE` 里写死的），
   保留这一处 `sys.path.insert` 是必要的运行时机制，不是图省事的遗留写法，
   已在 `capability_engine.py`/`__init__.py` 头部注释中说明区别。
2. **agent 现在真的能调用到引擎**：新增 `tools/capability_call.py`，注册
   `capability_call(skill_name, request)` 工具，在 `Agent.__init__` 里与
   `skill_list`/`skill_activate` 等工具一起注册（复用同样的
   `override=True` 约定，兼容 SubAgent 持有独立 `skill_loader` 的场景）。
   工具只接受 `skill_type: generative-capability` 的 skill，对普通静态
   skill 和不存在的 skill 名都给出明确的错误信息与正确的下一步提示，不
   静默尝试或伪造结果。默认注入 `build_llm_resolver()`（真实调用第二级
   LLM 检索裁决，这一步不依赖任何领域特定的底层工具，因此可以无条件开启）；
   **默认不注入 `explore_runner`/`tool_executor`**——底层操作原语
   （`browser-core` 等）仍是阶段四/阶段五"已知遗留"里明确记录、尚未从
   各领域 skill 独立拆分出来的部分，本工具没有能力代为实现，命中 miss
   或需要探索的请求会如实返回 `not_implemented` 并在 `note` 字段说明原因，
   不会被伪造成功——这是诚实暴露"阶段十仍未完成"这件事，而不是掩盖它。
3. **`SkillLoader` 认得 `skill_type: generative-capability`**：`_parse_skill()`
   新增解析 `skill_type`/`category_summary` 两个 frontmatter 字段（这正是
   方案文档第 2 节从一开始就定义好的格式，本阶段是第一次真正实现它的解析
   与消费）；`Skill.is_generative_capability` 属性、`build_context()` 对这
   类 skill 只注入一行摘要 + 一句"调用 capability_call"的引导，不整段注入
   `SKILL.md` 正文（即使作者不小心在正文里写了 member 清单细节，也不会
   泄漏进主 context）；`get_catalog()` 附带 `skill_type`/`category_summary`
   字段，让 `skill_list` 工具的返回结果里能看出这是一个"按需调用"而非
   "需要 skill_activate 加载正文"的 skill。
4. **两个既有 generative-capability skill 的 `SKILL.md`/`capability.yaml`/
   `registry.json`/`members/` 内容本身未改动**，只是文档里"引擎代码在哪"
   这一句描述从 `.claude/skills/_engine` 更新为
   `src/mini_agent/skills/generative_capability`。
5. **补齐 `tests/` 目录的正常 pytest 回归覆盖**：新增
   `tests/test_generative_capability_engine.py`，11 个用例覆盖：包能否
   正常 import、`resolve()`/`execute()` 命中已有 trusted member 执行成功、
   未命中且未注入探索器时如实返回 `not_implemented`（不伪造成功）、LLM
   桩解析器命中、完整 explore→distill→落盘→免探索复用闭环（用桩探索器，
   不依赖真实网络）、第二个 generative-capability skill（`doc-template-
   generation`）复用同一套引擎、`SkillLoader` 对 `skill_type`/
   `category_summary` 的解析与 `build_context()` 摘要注入行为、
   `capability_call` 工具对静态 skill 和未知 skill 名的边界条件。此前
   阶段一到阶段六的"验证结果"全部依赖临时构造的沙盒场景手动跑一次，本次
   之后这些场景成为可重复执行的自动化回归测试。

**验证结果**：

1. `pytest tests/test_generative_capability_engine.py -v` → 11 passed。
2. 对比修改前后：把项目原始压缩包解到独立目录，跑同一批既有 skill 相关
   测试（`test_skill_cli.py`/`test_skill_compact.py`/`test_skill_manager.py`/
   `test_skill_propose.py`/`test_skill_usage_detector.py`）→ 修改前后结果
   完全一致，均为 `42 failed, 120 passed, 12 errors`（失败原因是这些测试
   文件里的 `make_loader()` 辅助函数用 `SkillLoader.__new__()` 手工构造
   实例、未同步设置 `_auto_activate_blocked` 等属性，是项目原始压缩包里
   已经存在的、与本次改动完全无关的既有缺陷，不在本次任务范围内），确认
   本阶段改动没有引入任何新的测试回归。
3. `pytest tests/test_capability_cmd.py tests/test_capability_notification_v021.py
   tests/test_capability_outline_suggestions_v021.py
   tests/test_external_trend_capability_link.py
   tests/test_capability_persona_wiki_scopes_binding.py
   tests/test_capability_learning_empty_retrieval_fix.py
   tests/test_capability_learning_p1.py tests/test_capability_wiki_freshness.py
   tests/test_capability_routes_mount.py tests/test_external_input_watch.py`
   → 140 passed，确认与项目里另一套同名前缀但完全不相关的
   `capability_learning`/`capability_routes` 子系统（persona 能力学习，
   与 generative-capability skill 引擎是两回事，只是碰巧都叫
   "capability"）没有任何相互影响。
4. 用沙盒副本重新跑一遍阶段一到阶段六实施记录里手工验证过的关键路径
   （domain_pattern_match 命中执行失败、no_match 无探索器返回
   not_implemented、LLM 桩解析器命中、完整 explore→distill→复用闭环、
   `doc-template-generation` 复用同一引擎）→ 结果与之前的实施记录完全
   一致，确认迁移过程中行为没有发生任何变化，纯粹是"代码挪了位置、补上了
   之前缺失的接线和测试"，不是一次隐藏了行为改动的重构。

**已知遗留（留给后续阶段）**：

- `capability_call` 工具目前默认不注入 `explore_runner`/`tool_executor`，
  这意味着通过真实运行的 agent 调用时，只有命中已有 trusted/probation
  member 并执行成功的路径完全可用；需要触发探索的路径仍会如实返回
  `not_implemented`。要让探索路径也在真实 agent 里跑通，需要先完成方案
  文档第 10 节"迁移路径"里提到的 `browser-core`/`doc-core` 独立拆分为
  静态 skill 这一步，拿到一个可以被安全注入进 `tool_executor` 的、有明确
  权限边界的底层操作原语执行器；这本身工作量不小，且涉及到探索子agent的
  真实网络/浏览器操作权限模型设计，留给专门的后续阶段处理，不在本次范围
  内草率接线。
- `explorer_runtime.build_llm_explorer()` 目前仍是自己用 `urllib` 手写的
  一套 Anthropic Messages API 调用，没有复用 `src/mini_agent/llm/*` 已有的
  客户端池、重试策略、debug 日志等基础设施；这两套调用链路的鉴权/配置/
  模型选择目前是彼此独立的。本阶段迁移只解决了"引擎代码放在哪个目录"这
  一层问题，"引擎内部该不该复用项目已有的 LLM 调用基础设施"是下一层可以
  继续挖的优化点，因为改动面会涉及 `explorer_runtime.py` 内部实现细节
  （而不只是文件挪位置），本阶段范围内不做，留待后续阶段视实际需要推进。

### 阶段八 —— 已完成

**目标**：阶段一到阶段七验证的都是引擎骨架本身和两个具体业务 skill
（`browser-site-scraper`/`doc-template-generation`），但两者要么依赖真实
浏览器（无法在无网络沙箱里跑通完整成功路径），要么探索链路依赖较复杂的
桩数据构造。本阶段新增一个刻意做得很小、零外部依赖（不需要浏览器/API
key/网络）的第三个 generative-capability skill——`text-transform-
capability`——专门用来在任意环境下快速验证机制本身（而不是验证某个具体
业务能力）是否可用，并配套一份可独立执行的测试方法文档。

**新增文件**：

```
.claude/skills/text-transform-capability/
  SKILL.md                        # skill_type: generative-capability 声明 +
                                    # 与另外两个 skill 的差异对照表
  capability.yaml                  # keyword matchers；intent_schema_template
                                    # 为 {result: {text: string}}；lifecycle
                                    # 阈值调小为 2，便于测试快速触发 degraded；
                                    # distill.trust_trace_data: true，便于用
                                    # 简单桩 tool_executor 验证 explore/distill
  explorer/prompt.md               # 占位角色设定（同 browser-core/doc-core
                                    # 的处理方式，text-core 尚未实现）
  explorer/tool_allowlist.json     # 占位工具白名单
  _index.json                      # upper/reverse 两个 member 的检索摘要
  registry.json                    # 两个 member 初始状态均为 trusted（人工
                                    # 预置、纯逻辑实现，等同 doc-template-
                                    # generation 对 standard_report 的处理）
  members/upper/{script.py,meta.json}    # content.text 转大写，纯 Python
  members/reverse/{script.py,meta.json}  # content.text 反转，纯 Python
test_cases/text-transform-capability-testing-guide.md
                                    # 配套测试方法文档，见下方说明（阶段十已
                                    # 将其重写为对话式测试指南，见阶段十记录）
next_doc/generative-capability-skill-plan.md   # 本文档（阶段八记录）
```

**设计要点**：

- `upper`/`reverse` 两个预置 member 是纯 Python 字符串操作，不发起任何
  网络请求、不读写文件、不依赖第三方库，因此"确定性匹配命中 + 执行成功"
  这条路径在任意环境（包括完全离线的沙箱）里都能得到真实的 `success`
  结果，不像 `browser-site-scraper` 那样受限于"沙箱无可用浏览器"而只能
  验证到"命中后执行失败"为止。
- 两个 member 都保留了"缺少 `content.text` 时显式返回 `fail`"的分支，
  用于验证 `intent_schema` 校验与 `fail_count`/`consecutive_failures`
  计数路径；`lifecycle.degrade_failure_threshold` 特意调小为 2（而非另
  外两个 skill 用的 3），使"连续失败触发 degraded"这个场景只需 2 次调用
  即可复现，降低测试成本。
- 探索链路设计了一个不在预置 member 里的第三种变换（`shout`，测试文档里
  给文本末尾加感叹号）来触发 `miss -> explore -> distill -> 落盘 -> 免
  探索复用` 完整闭环；`capability.yaml` 显式打开
  `distill.trust_trace_data: true`，这样测试文档里可以用一个不刻意在
  "重放最后一步"精心构造 `data` 字段的简单桩 `tool_executor`
  （`lambda name, inp: {"ok": True, "data": {...}}`），复现阶段五/阶段六
  实施记录中提到的"该开关主要面向自测/CI 场景"的用法，而不需要像
  `browser-site-scraper` 测试那样专门构造两组桩数据来绕开这个坑。
- `text-transform-capability` 与 `browser-site-scraper`/`doc-template-
  generation` 复用同一套引擎代码（`mini_agent.skills.
  generative_capability`），本阶段未修改引擎任何一行代码，纯粹是新增一份
  声明式配置 + 两个预置 member 脚本，这本身也是对"流程与领域分离"这条
  核心设计原则的又一次验证。

**验证结果**（均在解压后的沙箱环境、离线、不依赖 `ANTHROPIC_API_KEY` 完成，
可复现步骤见 `test_cases/text-transform-capability-testing-guide.md`）：

1. `CapabilityEngine(".claude/skills/text-transform-capability").call(...)`
   对 `target.op="upper"` 与 `target.op="reverse"` 两个请求均正确通过
   `keyword_match` 命中对应 member 并执行成功，返回的
   `data == {"result": {"text": "..."}}` 与预期完全一致——这是本方案迄今
   为止第一次在没有任何浏览器/网络的情况下，端到端跑出真实 `success`
   结果（而非"命中但执行失败，因为缺浏览器"）。
2. 对 `content` 缺少 `text` 字段的请求，`execute()` 正确返回失败并计入
   `fail_count`；连续调用 2 次后 `registry.json` 中对应 member 的
   `status` 正确从 `trusted` 流转为 `degraded`，`status_changed_at`
   字段被正确写入（阶段六新增能力在第三个 skill 上同样成立）。
3. 对一个三个预置 member 都无法匹配的 `target.op="shout"` 请求，注入桩
   探索器（模拟调用一次 `text_transform_apply` 并在这一步的输出里直接带
   `data`）与桩工具执行器 → `resolve()` 返回 `no_match` →
   `explore()`/`distill()` 成功 → 沙箱自测通过 → 原子化落盘为新 member
   （`members/shout_this_text/`，member_id 由请求文本自动推断，
   `status: probation`）→ 用相同请求再次调用
   （不注入 `explore_runner`，但需要保留 `tool_executor`，因为蒸馏产物
   仍需要通过 `tool_runtime` 重放动作序列）→ 正确通过 `keyword_match`
   命中并执行成功，验证了"免探索复用"链路。
4. `run_patrol(skill_dir)` 对本 skill 目录跑一致性巡检，初始状态下
   （两个 member 目录、`registry.json`、`_index.json` 三者一致）返回
   0 条 finding，符合预期；测试文档里额外给出了"人为制造不一致后巡检能
   正确识别"的可选步骤，复用阶段四已经验证过的 `health_patrol` 能力，
   不在本 skill 里重复造轮子验证同一件事。

**踩坑记录（写入测试文档，避免后来者重复踩坑）**：

- 验证过程中发现，若直接对仓库里的真实 skill 目录
  （`.claude/skills/text-transform-capability`，而非临时复制出的副本）
  调用 `CapabilityEngine(...).call(...)`，`execute()` 成功/失败都会真实
  写回该目录下的 `registry.json`（原子化写入，符合方案文档第 8 节要求），
  这会导致仓库里的"初始状态"文件被测试过程污染，影响下一次测试或误导
  阅读者以为这是最初状态。`test_cases/text-transform-capability-testing-
  guide.md` 第一步就明确要求"先复制一份到临时目录再测试"，并说明了这个
  坑的成因，而不是简单地说"记得复制"。

**已知遗留（留给后续阶段）**：

- 与另外两个 skill 一样，`text-core` 仍是占位声明，没有真正可执行的
  实现；这是刻意的设计选择而非遗漏——本 skill 的目的是验证机制通用性，
  不是新增一个真实可用的文本处理能力。若未来确有"探索出全新文本变换"的
  真实业务需求，应该重新评估是否需要把 `text-core` 实现为一个真正的静态
  skill，而不是直接在本 skill 基础上扩展。
- 测试文档目前是 `test_cases/` 下的一份可手动/脚本执行的 markdown 指南，
  未封装为 `tests/` 目录下的 pytest 用例（`tests/
  test_generative_capability_engine.py` 已经用 `browser-site-scraper`/
  `doc-template-generation` 覆盖了引擎层面的回归测试，本 skill 主要定位
  是"给人看的、可独立复现的机制验证手册"，与自动化回归测试的定位不同，
  两者不冲突；如果后续需要把它也纳入 CI，可以参照
  `tests/test_generative_capability_engine.py` 的写法新增一个对应的
  `tests/test_text_transform_capability.py`，本阶段范围内不做）。

### 阶段九 —— 已完成

**目标**：`llm_resolver.py`（第二级检索裁决）与 `explorer_runtime.py`（探索
子agent决策循环）此前各自用 `urllib` 直连 Anthropic Messages API，是整个
引擎里仅有的两处没有走框架统一 LLM 调用基础设施
（`llm/service.py::LLMHelper`，见 `next_doc/llm_helper_unification_plan.md`）
的地方——固定写死 `provider=anthropic`、不跟随 `/model` 切换、不复用
`LLMClientPool` 的多 key/多配置 fallback 与统一 `RetryPolicy`、也不产生
`call_stats` 调用计数。本阶段把这两处改接 `LLMHelper`。

**新增/修改文件**：

```
src/mini_agent/skills/generative_capability/llm_resolver.py     # 修改：build_llm_resolver()
                                                                    # 改为接收 llm_helper/cfg，
                                                                    # 通过 helper.ask() 调用，
                                                                    # 删除 urllib 实现
src/mini_agent/skills/generative_capability/explorer_runtime.py  # 修改：build_llm_explorer()
                                                                    # 改为接收 llm_helper/cfg，
                                                                    # 通过 helper.chat() 驱动决策
                                                                    # 循环，消息历史改用与
                                                                    # history_manager.py 一致的
                                                                    # 内部约定，删除
                                                                    # _call_messages_api()
src/mini_agent/skills/generative_capability/__init__.py           # 修改：模块 docstring 调用
                                                                    # 示例与阶段九说明同步更新
src/mini_agent/tools/orchestration.py                              # 修改：新增
                                                                    # get_current_llm_helper()
                                                                    # 公开别名，供
                                                                    # tools/capability_call.py
                                                                    # 等模块外调用方复用同一套
                                                                    # thread-local 机制
src/mini_agent/tools/capability_call.py                            # 修改：构造 CapabilityEngine
                                                                    # 前先取 get_current_llm_helper()，
                                                                    # 取不到时如实报错而非静默退化
.claude/skills/browser-site-scraper/SKILL.md                       # 更新阶段说明
.claude/skills/doc-template-generation/SKILL.md                    # 更新阶段说明
.claude/skills/text-transform-capability/SKILL.md                  # 更新阶段说明
next_doc/generative-capability-skill-plan.md                       # 本文档（阶段九记录）
```

**已实现能力**：

- `build_llm_resolver(llm_helper=None, *, cfg=None, override_model=None,
  override_provider=None, max_retries=2)`：签名从"`model`/`api_key_env`/
  `timeout_seconds`"改为"`llm_helper`/`cfg`/`override_*`/`max_retries`"，
  与 `ensemble/judge.py::judge_llm(llm_helper=...)` 的既有约定对齐——优先用
  传入的 `llm_helper`（跟随 `/model` 切换），否则退化为
  `LLMHelper.from_config(cfg)`；`override_model`/`override_provider` 仍可
  覆盖裁决用的模型/provider（走 `LLMHelper` 的 override 分支，不污染主
  agent 当前配置）。内部改为调用 `helper.ask(...)` 单轮取文本，删除了所有
  `urllib.request`/API key 环境变量读取逻辑。两者都未传时在**调用时**（而
  非构造时）抛出 `RuntimeError`，语义与此前"未配置 API key 时抛异常，不
  静默返回空列表"一致，`CapabilityEngine.resolve()` 无需改动即可继续正确
  区分 `no_match` 与 `llm_error: ...`。
- `build_llm_explorer(tool_executor, llm_helper=None, *, cfg=None,
  override_model=None, override_provider=None, max_retries=2)`：签名同样
  从"`model`/`api_key_env`/`timeout_seconds`"改为"`llm_helper`/`cfg`/
  `override_*`/`max_retries`"。决策循环内部改为调用 `helper.chat(messages=,
  system=, tools=, ...)`，`tools` 从"手写的 Anthropic tools JSON"改为
  `mini_agent.llm.base.ToolSchema` 实例列表（与主 agent 对话循环用的是
  同一套类型）；工具调用结果不再依赖手工解析 Anthropic 原始 `content`
  block，而是直接读取 `LLMResponse.tool_calls`（`ToolCall.id/name/input`），
  再按 `history_manager.py::HistoryManager.append_assistant()` 完全相同的
  内部消息约定（`{"type":"tool_use","id","name","input"}` /
  `{"type":"tool_result","tool_use_id","content"}`）拼回 `messages` 列表
  维持多轮对话——这套约定本来就是 provider 无关的，各 provider 的 client
  内部各自负责转换成自己的 wire 格式，因此探索循环现在天然适配任何已接入
  的 provider，不再只能是 Anthropic。`FINISH_TOOL`/`REPORT_FAILURE_TOOL`/
  工具白名单强制/步数与时间预算硬上限等既有安全约束全部原样保留，未做任何
  放松。两者都未传时不抛异常，而是返回
  `ExploreTrace(success=False, stop_reason="llm_error", error=...)`——遵循
  探索循环"失败是一等公民"的既有约定（此前"未配置 API key"分支就是这么
  处理的），不会让调用方遭遇意料之外的异常类型。
- `tools/orchestration.py` 新增 `get_current_llm_helper()`：`_get_current_
  llm_helper()`（私有，`run_ensemble_llm`/`run_ensemble_subagents` 内部用）
  的公开别名，语义与返回值完全一致（拿不到时返回 `None`），供模块之外的
  调用方复用同一套"从当前线程拿 `Agent.llm_helper`"的 thread-local 机制，
  不必各自重新实现一遍或导入私有名字。
- `tools/capability_call.py`：构造 `CapabilityEngine` 前先调用
  `get_current_llm_helper()`；拿到后传给 `build_llm_resolver(current_llm_
  helper)`（`explore_runner` 仍按阶段七的既有设计不默认注入，见其"已知
  遗留"）。取不到时（理论上只会发生在本工具被 Agent 主流程之外调用的
  异常场景）返回明确的 `status: error`，不静默退化成某个写死模型——与该
  文件一贯"不伪造成功/不掩盖限制"的风格一致。

**验证结果**：

1. `pytest tests/test_generative_capability_engine.py -v` → 11 passed，
   确认阶段七迁移时补齐的回归测试在改造后依然全部通过（这些测试用
   `build_stub_resolver`/`build_stub_explorer`，本就不触碰真实 LLM 调用
   路径，因此不受本阶段改造影响，属预期）。
2. 用一个自制的 `FakeLLMHelper`（记录每次 `ask()`/`chat()` 调用参数，
   `ask()` 固定返回 `{"member_ids": ["upper"]}`，`chat()` 固定返回一次
   `finish` 工具调用）分别驱动 `build_llm_resolver(helper, override_model=
   "claude-sonnet-5")` 与 `build_llm_explorer(tool_executor, helper,
   override_model="claude-sonnet-5")` → 两者均返回预期结果
   （`member_ids == ["upper"]`；`ExploreTrace(success=True, data={"result":
   {"text": "OK"}})`），且 `FakeLLMHelper.ask_calls`/`chat_calls` 均被正确
   记录、`override_model` 被正确透传——证明两个函数现在真的是通过传入的
   `LLMHelper` 接口驱动，而不是仍在内部悄悄拼 HTTP 请求。
3. 分别对 `build_llm_resolver()`（不传参）与 `build_llm_explorer(lambda n,
   i: {})`（不传 `llm_helper`/`cfg`）发起调用 → `resolver(...)` 正确抛出
   `RuntimeError`（消息明确指出"既未传入 llm_helper 也未传入 cfg"）；
   `explorer(...)` 正确返回 `ExploreTrace(success=False,
   stop_reason="llm_error")` 而非抛异常，验证两者对"完全没有可用 LLM 调用
   方式"这一环境配置问题的处理符合各自既有的错误处理约定（resolver 抛
   异常、explorer 返回失败 trace）。
4. 全文 `grep -n "urllib\|api_key_env\|os\\.environ"` 确认
   `llm_resolver.py`/`explorer_runtime.py` 中除模块 docstring 里"此前如何
   如何"的历史说明文字外，代码本体不再有任何遗留的 `urllib`/API key 环境
   变量直连逻辑。

**已知遗留（留给后续阶段）**：

- `capability_call.py` 目前仍只注入 `llm_resolver`，`explore_runner`/
  `tool_executor` 依旧不默认注入——这是阶段七就明确记录、依赖
  `browser-core`/`doc-core`/`text-core` 从各领域 skill 独立拆分为静态
  skill 才能推进的架构性遗留，本阶段范围内不涉及，不重复记录细节（见阶段
  七"已知遗留"）。
- `LLMHelper.chat()` 的 `max_retries` 默认策略（`EmptyOutputCondition` +
  `retry_on_exception=True`）此前在探索子agent里完全没有——旧实现里网络
  异常会直接向上抛出、终止整个探索循环。现在探索循环里的每一次 LLM 调用
  都会先在 `LLMHelper` 内部重试 `max_retries` 次才失败，这是行为上的一处
  实质改进（更不容易被单次网络抖动打断整条探索），但也意味着探索循环单步
  的最坏延迟有所上升；`max_retries` 默认给了较保守的 `2`（小于主对话循环
  常用的 `3`），如果后续实测发现探索场景需要不同的取值，应在
  `capability.yaml` 层面暴露成可配置项，而不是在代码里硬编码调整。

### 阶段十 —— 已完成

**目标**：`test_cases/text-transform-capability-testing-guide.md`（阶段八
产物）此前写的是"如何用代码直接调用 `CapabilityEngine`/`build_stub_*`
去验证效果"，验证的是引擎本身的行为是否符合设计，但没有回答一个更贴近
实际使用场景的问题：**这套 `generative-capability` 机制在真实 agent 对话
里是否真的可行**——模型会不会正确发现这类 skill、会不会调用
`capability_call` 而不是 `skill_activate`、检索裁决与"诚实失败"这两条
关键行为在真人对话链路里是否也成立。本阶段把该测试文档重写为对话式指南：
每一步给出"该在 agent 会话里输入什么"和"预期看到什么"，而不是一段可以
直接跑的 Python 脚本。同时补充了三个 SKILL.md 里被移除的阶段历史记录该往
何处放的说明（详见下方"顺带修复"）。

**修改文件**：

```
test_cases/text-transform-capability-testing-guide.md   # 重写：从"代码调用
                                                            # 引擎的验证脚本"
                                                            # 改为"agent 对话
                                                            # 输入/预期输出"
                                                            # 的测试指南；文末
                                                            # 附录保留一份可
                                                            # 离线执行的
                                                            # capability_call
                                                            # 工具级验证脚本，
                                                            # 用于"没有真人
                                                            # 对话条件时的
                                                            # 排查兜底"，与
                                                            # 主体的对话式
                                                            # 步骤角色不同
next_doc/generative-capability-skill-plan.md              # 本文档（阶段十记录）
```

**已实现能力**：

- 新指南分六步，覆盖：(1) `skill_list` 能否正确呈现
  `generative-capability` skill 的一行摘要而不泄漏 member 清单；
  (2)(3) 关键词命中已有 member 并真实执行成功（大写/反转）；
  (4) 请求文本不含关键词时是否真的触发第二级 LLM 裁决
  （`resolve_reason: llm_match`）——这正是阶段九"检索裁决改接框架
  `LLMHelper`"改造后最值得在真实对话里复核的一点，因为它现在跟随当前
  agent 正在用的 provider/model，而不是写死的模型；(5) 触发一个三个
  预置 member 都覆盖不到的全新变换，验证 agent 会如实转述
  `not_implemented` 及原因，而不是编造一个看似合理的结果；(6，可选)
  缺参数请求触发 schema 校验失败。每一步都给出了具体输入文案与预期的
  `capability_call` 返回结构/`resolve_reason` 取值，而不是笼统地说"应该
  能用"。
- 文末保留一个"附：无法进行真人对话测试时，如何验证同样的调用链路"的
  兜底脚本——不是走 `CapabilityEngine` 直接调用（那样绕开了
  `capability_call` 工具本身的封装逻辑），而是通过
  `register_capability_tools()` 注册出真实的 `capability_call` 工具，
  用 `tools/orchestration.set_current_llm_helper_provider()` 模拟"当前有
  一个正在跑的 Agent 实例"（真实场景下这一步由 `Agent.__init__` 自动完成），
  再调用工具函数本身。这段脚本验证的是"工具注册、`skill_list`/
  `capability_call` 接线、`get_current_llm_helper()` 取值"这些集成点，
  明确说明它不能替代真人对话验证"模型会不会主动选择调用这个工具"这一层，
  避免读者把兜底脚本的通过等同于"对话式测试已经做过了"。

**验证结果**：

用文档里附录脚本的原文（逐字复制，未做任何调整）离线跑了一遍，确认输出与
文档中给出的预期完全一致：

```json
{
  "status": "success",
  "data": {"result": {"text": "HELLO WORLD"}},
  "error": null,
  "member_id": "upper",
  "resolve_reason": "keyword_match"
}
```

此外用同一套真实 `capability_call` 工具（而非直接调引擎）额外验证了指南
主体步骤 2/3/5 对应的三个场景，确认工具级输出与指南文档中给出的预期完全
一致：

- 大写/反转命中：`status: success`，`resolve_reason: keyword_match`，
  `data` 分别为 `HELLO WORLD`/`fedcba`。
- 未知变换（`shout`）：`status: not_implemented`，`resolve_reason:
  no_match`，`note` 字段准确说明"当前运行环境尚未接入真正的底层操作原语
  执行器"，不会伪造成功。
- 额外验证了"完全没有注册 `llm_helper`"这种异常场景（对应真人对话测试里
  不会出现、但脚本化排查时可能误配置的情况）：工具正确返回
  `status: error` 并说明原因，而不是静默用某个写死的模型继续跑。

**已知遗留**：

- 指南步骤 4（触发第二级 LLM 裁决）的具体裁决结果依赖真实模型的语义判断，
  不是硬编码规则，因此"选中哪个 member"在不同模型下可能有出入；指南里已
  提示读者应关注 `resolve_reason` 是否变为 `llm_match`，而不必纠结具体
  选中的是不是 `upper`，避免把模型的正常差异误判为机制故障。
- 指南目前仍是给人读、人工在真实 agent 会话里逐步操作的 markdown 文档，
  没有做成可以自动跑的对话级回归测试（这需要一个能驱动真实 `Agent` 实例
  收发消息、且能对模型的自然语言回复做语义断言的测试框架，成本和现有
  `tests/test_generative_capability_engine.py`、附录脚本的定位都不一样）；
  如果后续需要把"agent 会不会正确使用这个 skill"也纳入自动化 CI，应该
  作为独立的对话级集成测试基础设施来做，不建议现在就往本指南里塞。

### 阶段十一 —— 已完成

**触发场景**：真实对话里用 `text-transform-capability` 测试大写变换时，
调用方（agent）把 `request` 构造成了 `{"text": "hello world", "transformation":
"uppercase"}`——待转换内容误放进 `text`（`text` 实际语义是"意图描述"，
用于一级关键词匹配/二级 LLM 裁决），`transformation` 又是个 schema 里根本
不存在的字段。结果一级、二级 resolve 都 `no_match`，一路滑进 `explore()`，
因为测试环境没注入 `explore_runner`，最终看到 `status: not_implemented`。
这个反馈具有很强的误导性：`upper` member 明明是 `trusted` 状态、纯 Python
实现，只要 `request` 形状对了应该秒回成功，`not_implemented` 会让人误以为
是"探索能力没接线"，而看不出真正原因是"参数传错了"。

**改动**：

- `capability.yaml` 新增可选字段 `request_formats`：声明该 skill 接受的
  一种或多种 `request` 形状，每种给出 `required_fields`（点号路径列表，
  如 `target.op`/`content.text`）+ 一份可直接照抄的 `example`。
- `CapabilityEngine.call()` 在 `resolve()` 之前先调用新增的
  `_check_request_format()`：只要 `request` 满足声明的任意一种格式的
  `required_fields`（字段存在且非空，不做类型/语义校验）就放行，交给
  原有 resolve/execute/explore 逻辑处理；一种都不满足则直接短路返回新增
  的 `CapabilityCallResult(status="invalid_request", ...)`，`data` 里带上
  全部声明格式的 `name`/`description`/`required_fields`/`example`，**不
  消耗探索预算**。
- 未声明 `request_formats` 的 skill（存量/忘了加）直接跳过这层检查，行为
  与阶段十之前完全一致，见 `tests/test_generative_capability_engine.py::
  TestRequestFormatBackwardCompat`。
- `tools/capability_call.py` 透传新增的 `invalid_request` 状态，并在
  `note` 字段里明确告诉调用方"去看 `data.expected_formats`，照着
  `example` 重新构造 `request` 后重试"，工具描述里的 status 枚举同步更新。
- 已给 `browser-site-scraper`、`doc-template-generation`、
  `text-transform-capability` 三个 skill 的 `capability.yaml` 都补上了各自
  的 `request_formats` 声明。

**测试**：`tests/test_generative_capability_engine.py` 新增
`TestRequestFormatValidation`（复现真实转录里的错误调用形状，验证返回
`invalid_request` 而非 `not_implemented`，且 `example` 与正确形状确实能
成功执行）、`TestCapabilityEngineResolveExecute::
test_request_missing_required_field_returns_invalid_request`（browser-site-
scraper 场景）、`TestRequestFormatBackwardCompat`（未声明 `request_formats`
的 skill 完全不受影响）。全量 15 个用例通过。

**已知遗留（留给后续阶段）**：

- `required_fields` 目前只做"存在且非空"检查，不校验字段类型（如
  `target.op` 是不是字符串）；如果调用方传了正确的字段名但类型错误
  （如 `target.op` 传成数字），仍然会被判定为形状合法，放行到 resolve()，
  可能因为一级关键词匹配用 `str` 操作报错或静默 no_match。如果后续发现
  这也是常见的误用模式，可以在 `_check_request_format` 里加一层轻量类型
  检查，思路与 `schema_validator.py` 现有的"常用关键字子集"校验风格保持
  一致，不必现在就做。

### 阶段十二 —— 已完成

**触发场景**：阶段十一修好"请求格式"这一层之后，真实对话里按正确格式调用
`text-transform-capability` 探索一个未知变换（`shout`：转大写再加感叹号）
时，因为 `capability_call.py` 一直"故意不注入 `explore_runner`/
`tool_executor`"，探索直接返回 `not_implemented`。用户追问"怎么真正接入
`explore_runner`"。

**分析**：`explorer_runtime.build_llm_explorer()` 本身早就是真实可用的
LLM 决策循环（阶段九已改接框架统一 `LLMHelper`），之所以一直没被注入，是
因为各领域声明的底层操作原语（`browser-core`/`doc-core`/`text-core`）全部
还是占位声明，注入了也是空转。但 `text-transform-capability` 声明的
`text-core`（只有一个 `text_transform_apply`）本质是纯字符串操作，跟
`browser-core`/`doc-core` 不同——**它是唯一一个真的能不依赖任何外部服务
就实现出来的底层原语**，值得单独打通。

**改动**：

- 新增 `src/mini_agent/skills/generative_capability/real_tools.py`：
  - `text_transform_apply(tool_input)`：真实实现，支持
    `upper/lower/reverse/strip/title/capitalize/swapcase/append/prepend/
    replace`，探索子agent可以多次调用组合出复杂变换（如 shout = upper +
    append "!"），不需要为每种可能的变换单独声明工具。
  - `build_default_tool_executor()`：返回一个通用 `tool_executor`，命中
    `REAL_TOOL_IMPLEMENTATIONS` 表里的工具名会真的执行；未命中的（当前是
    `browser-core`/`doc-core` 下的全部工具）如实返回"仍是占位声明，未接入
    真实执行器"的 `{"error": ...}`，不抛异常、不伪造成功——后续要接
    `browser-core`/`doc-core`，只需要往这个表里加实现，调用方代码不用改。
  - 从包 `__init__.py` 导出 `build_default_tool_executor` /
    `REAL_TOOL_IMPLEMENTATIONS`。
- `tools/capability_call.py`：不再"默认不注入"，改为默认构造
  `tool_executor = build_default_tool_executor()`，同时注入
  `explore_runner=build_llm_explorer(tool_executor, llm_helper=...)` 和
  `tool_executor=tool_executor`。命中真实实现的领域（目前只有
  `text-transform-capability`）现在能在真实对话里跑通完整
  `resolve -> miss -> explore(真实 LLM 决策循环 + 真实工具执行) -> distill ->
  落盘 -> 免探索复用` 全链路；`browser-site-scraper`/
  `doc-template-generation` 因为它们的原语仍是占位，探索路径依旧诚实返回
  `not_implemented`（工具执行阶段会明确报"未接入真实执行器"，探索子agent
  据此调用 `report_failure`），不会因为这次改动而被误判成"能用了"。
- 同步更新 `text-transform-capability` 的
  `explorer/tool_allowlist.json`（`status: implemented`，写清楚真实的
  input/output 结构与组合调用方式）和 `explorer/prompt.md`（不再说
  "占位/测试场景不会被真实 LLM 读取"），以及 `SKILL.md`"已知限制"一节。

**测试**：

- `tests/test_generative_capability_real_tools.py`（新文件）：
  `text_transform_apply` 各 op 的纯逻辑正确性、参数校验分支、
  `build_default_tool_executor` 对已知/未知工具的分发行为、内部异常不会
  向上抛出。
- `tests/test_generative_capability_engine.py::TestRealTextCoreExploreEndToEnd`：
  用一个脚本化的假 `LLMHelper`（duck-type `.chat()`，按顺序吐出预先写好
  的 `LLMResponse`，不发真实网络请求）驱动真实的 `build_llm_explorer()`
  决策循环 + 真实的 `build_default_tool_executor()`，完整验证
  探索出新变换 -> 蒸馏落盘 -> 免探索复用（`resolve_reason: keyword_match`）
  这条此前只能靠 `build_stub_explorer` 绕过循环本身来验证接线的链路，现在
  连"决策循环怎么调用工具"这一层也是真的在跑。全量测试通过（不含这两个
  新文件的既有 15 + 6 个用例，加上新增用例）。

**已知遗留（留给后续阶段）**：

- `browser-core`/`doc-core` 仍是占位声明，`browser-site-scraper`/
  `doc-template-generation` 两个 skill 的探索路径依旧只能拿到
  `not_implemented`；要打通它们需要真的接一个无头浏览器 /
  文档生成库作为底层原语实现，工作量和风险都远大于 `text-core`，不在本
  阶段范围内，思路已经在 `real_tools.py` 文件头注释里写清楚（往
  `REAL_TOOL_IMPLEMENTATIONS` 加实现即可，调用方不用改）。
- `text_transform_apply` 目前只覆盖常见字符串操作，遇到探索子agent认为
  "组合现有 op 也做不到"的变换需求（如需要正则/语言学分析的变换）会如实
  `report_failure`，这是预期行为，不是 bug。

### 阶段十三 —— 已完成

**目标**：`browser-core`/`doc-core` 此前一直只是 `explorer/
tool_allowlist.json` 里的一行占位声明，没有任何文档说明这两个原语具体
应该长什么样、由谁来实现、怎么接进现有引擎。本阶段推进 `browser-core`：
把它从"完全空白的占位"变成"契约已写清楚、真实实现仍缺"，并留下一份
可直接照做的接入指南，方便后续在能接无头浏览器的环境下真正实现探索
能力。`browser-site-scraper` 本身的探索链路诚实失败——这是预期行为，
本阶段不改变这一点（真正实现需要真的接一个无头浏览器，工作量和风险
都远大于 `text-core`，明确不在本次范围内，`doc-core` 同理，留给后续
有需要时再做）。

**新增/修改文件**：

```
.claude/skills/browser-core/SKILL.md                            # 新增：
                                                                    # 静态
                                                                    # skill，
                                                                    # 定义
                                                                    # browser-
                                                                    # site-
                                                                    # scraper
                                                                    # 探索子
                                                                    # agent
                                                                    # 会调用
                                                                    # 的 7 个
                                                                    # 通用
                                                                    # 浏览器
                                                                    # 操作
                                                                    # 原语的
                                                                    # 输入/
                                                                    # 输出
                                                                    # 契约
.claude/skills/browser-core/HEADLESS_BROWSER_INTEGRATION.md     # 新增：
                                                                    # 给未来
                                                                    # 实现者
                                                                    # 的接入
                                                                    # 指南
                                                                    # （复用
                                                                    # 哪些
                                                                    # browser-
                                                                    # cdp 代码
                                                                    # /落点/
                                                                    # 会话
                                                                    # 生命
                                                                    # 周期/
                                                                    # 反爬
                                                                    # 边界/
                                                                    # 自测
                                                                    # 方法/
                                                                    # 完成后
                                                                    # 要同步
                                                                    # 更新的
                                                                    # 文档
                                                                    # 清单）
.claude/skills/browser-site-scraper/SKILL.md                     # 修改：
                                                                    # "依赖"
                                                                    # /"已知
                                                                    # 限制"
                                                                    # 两节
                                                                    # 更新为
                                                                    # 指向
                                                                    # browser-
                                                                    # core 的
                                                                    # 契约与
                                                                    # 接入
                                                                    # 指南，
                                                                    # 并纠正
                                                                    # 此前
                                                                    # "capability_
                                                                    # call 默认
                                                                    # 不注入
                                                                    # tool_
                                                                    # executor"
                                                                    # 的过时
                                                                    # 描述
                                                                    # （阶段
                                                                    # 十二已
                                                                    # 改为
                                                                    # 默认
                                                                    # 注入
                                                                    # 通用
                                                                    # tool_
                                                                    # executor，
                                                                    # 只是
                                                                    # browser-
                                                                    # core 下
                                                                    # 的工具
                                                                    # 名仍未
                                                                    # 命中
                                                                    # 任何
                                                                    # 真实
                                                                    # 实现）
.claude/skills/browser-site-scraper/explorer/tool_allowlist.json  # 修改：
                                                                     # note
                                                                     # 字段
                                                                     # 补充
                                                                     # 指向
                                                                     # 新增
                                                                     # 的
                                                                     # browser-
                                                                     # core
                                                                     # 契约
                                                                     # 与接入
                                                                     # 指南
next_doc/generative-capability-skill-plan.md                      # 本文档
                                                                      # （阶段
                                                                      # 十三
                                                                      # 记录）
```

**已实现能力**：

- **`browser-core` 契约文档**（`SKILL.md`）：把 `browser-site-scraper/
  explorer/tool_allowlist.json` 里 `allowed_tools` 声明的 7 个工具名
  （`browser_navigate`/`browser_click`/`browser_type`/`browser_scroll`/
  `browser_wait_for_selector`/`browser_extract_content`/
  `browser_screenshot_annotated`）逐一写清楚 `input`/`output` 结构与
  错误约定，这是此前唯一存在但从未被文档化的"隐含契约"（探索子agent的
  prompt 里提到这些工具名，但没有任何地方说明每个工具具体接受什么参数、
  返回什么结构）。文档同时说明了为什么这次不直接从 `browser-cdp` 抽取
  代码当作"已实现"——沙盒环境没有可用浏览器，勉强接入只会得到一个连不上
  真实浏览器、看似实现了但仍会诚实失败的空壳，反而会造成"已经接好了"的
  误解，与 `real_tools.py` 文件头一贯坚持的"不制造虚假完备感"原则相悖。
- **无头浏览器接入指南**（`HEADLESS_BROWSER_INTEGRATION.md`）：给下一个
  有真实浏览器环境的实现者一份具体到"改哪个文件、复用哪些已有代码、
  会话生命周期怎么管理、`browser_extract_content` 为什么是关键中的关键、
  反爬边界在哪里、完成后怎么自测、完成后要同步更新哪些文档"的清单式
  指南，遵循 `real_tools.py`/`text_transform_apply` 已经验证过的"新增
  一个实现文件 + 补一条分发表映射，调用方代码不用改"的接入模式，明确
  指向 `browser_core_impl.py` 作为落点（尚未创建，属于下一阶段的工作）。
- **纠正 `browser-site-scraper/SKILL.md` 的一处过时描述**："已知限制"
  一节此前仍写着阶段七时代"`capability_call` 默认不注入 explore_runner/
  tool_executor"的表述，但阶段十二已经改为默认注入
  `build_default_tool_executor()`；本阶段核对 `tools/capability_call.py`
  当前实现后更正为准确描述——工具**会**注入通用执行器，但该执行器对
  `browser-core` 下的工具名仍如实返回"未接入真实执行器"，最终效果
  （得到 `not_implemented`）不变，但过程描述现在准确了，避免读者以为
  阶段十二的改动对本 skill 完全没有影响。

**验证结果**：

1. `pytest tests/test_generative_capability_engine.py
   tests/test_generative_capability_real_tools.py -q` → 26 passed，确认
   本阶段是纯文档新增/更新（`SKILL.md`/`HEADLESS_BROWSER_INTEGRATION.md`
   /`tool_allowlist.json` 的 `note` 字段/两个 skill 的 `SKILL.md`
   正文），没有改动任何 `.py` 文件或 `capability.yaml`/`registry.json`/
   `_index.json` 等引擎读取的结构化配置，因此引擎行为与阶段十二完全
   一致，不需要也不应该有测试结果变化。
2. 用真实 `CapabilityEngine("browser-site-scraper").call({"text": "...",
   "target": {"url": "https://www.baidu.com/s?wd=test"}, "query":
   "test"}, ...)`（注入 `build_default_tool_executor()` +
   `build_llm_explorer(...)`，与 `capability_call.py` 当前逻辑一致）
   重新走一遍 `resolve` 命中 `baidu` → 执行失败（沙盒无可用浏览器）→
   进入 `explore()`，确认桩场景之外、用本阶段实际会被注入的真实执行器
   跑一遍时，探索子agent调用任意 `browser_*` 工具都会收到
   `real_tools.py` 里"该工具仍是占位声明，尚未接入真实执行器"的
   如实错误，最终 `report_failure`，`call()` 返回
   `status: not_implemented`——与更新后的 `SKILL.md`"已知限制"一节描述
   完全一致，确认文档没有夸大或掩盖当前的真实能力边界。

**已知遗留（留给后续阶段）**：

- `browser_core_impl.py` 尚未创建，`browser-core` 目前仍是纯文档、零
  可执行代码的状态；`doc-core`（`doc-template-generation` 依赖的底层
  原语）本阶段完全未涉及，仍是阶段五遗留下来的占位状态，理由同
  `browser-core`——真正实现需要接入一个文档生成库，工作量和优先级需要
  单独评估，不在本次"先把 browser-core 契约和指南写清楚"的范围内。
- `HEADLESS_BROWSER_INTEGRATION.md` 里给出的接入方式（复用
  `browser-cdp` 现有代码、模块级会话管理、并发探索的上下文隔离建议等）
  是基于阅读现有代码得出的设计建议，尚未经过真实浏览器环境的验证——
  真正接入时大概率会发现指南里遗漏的细节（比如 `browser-cdp` 里具体
  函数签名与本文档假设的契约存在出入），这是预期中的情况，接入者应该
  以真实调试结果为准，发现出入时更新契约文档而不是削足适履。

### 阶段十四 —— 已完成

**目标**：把 `browser-core` 从"契约文档 + 接入指南"推进为**真正可执行**
的实现，并纠正一处设计判断——不应局限于无头浏览器，因为部分网站的抓取
前提是登录，而登录往往需要人工操作，探索子agent的步数/时间预算不适合在
探索中途等待人工介入。同时，本阶段严格执行"skill 具体功能代码留在 skill
目录下，项目代码只保留通用机制"这条原则，所有浏览器/CDP 相关代码都新增
在 `.claude/skills/browser-core/impl/` 下，项目工程目录只新增了一处**与
浏览器无关**的通用动态加载机制。

**改动内容**：

- **新增 `.claude/skills/browser-core/impl/`**（skill 目录内，共 5 个
  文件）：
  - `cdp_client.py` —— 极简 CDP 客户端（HTTP `/json/*` tab 发现 +
    WebSocket 发命令），风格与 `browser-cdp/src/core/cdp_client.py`
    一致但大幅精简，**不 import `browser-cdp` 的任何代码**（原因见
    `browser-core/SKILL.md`"实现与依赖"一节：两者定位边界不同——通用
    原语 vs 网站定制脚本；且 impl 脚本理论上应该能被原样复制到别的项目
    独立使用，不应依赖同仓库另一个 skill 目录的内部实现细节）。
  - `browser_launch.py` —— 拉起一个带 `--remote-debugging-port` 的
    Chrome/Chromium 进程，`headless`/`headed` 走同一份代码只是参数不同。
  - `session_manager.py` —— **本阶段的核心新增**：`attach` /
    `launch_headless` / `launch_headed` / `auto` 四种会话模式。`attach`
    模式连接一个使用者已经手动启动、且可能已经手动登录好的浏览器实例，
    直接解决了"不应局限于无头浏览器"的诉求——探索子agent自身不处理任何
    登录逻辑，登录这件事从一开始就不在它的职责范围内，与
    `explorer/prompt.md` 一直要求的"遇到登录墙如实报告，不尝试绕过"完全
    兼容，只是多了一条"人先登录好、再交给它"的路径。
  - `browser_core_impl.py` —— 7 个工具（`browser_navigate`/
    `browser_click`/`browser_type`/`browser_scroll`/
    `browser_wait_for_selector`/`browser_extract_content`/
    `browser_screenshot_annotated`）的真实实现，均基于 `Runtime.evaluate`
    执行 JS 完成 DOM 查询/操作，`Page.captureScreenshot` 完成截图；任何
    异常都在函数内部捕获转成契约要求的 `{"ok": false, "error": "..."}`，
    不向调用方抛出未捕获异常。`browser_extract_content` 采用通用（非网站
    定制）提取策略，理由见 `browser-core/SKILL.md`"实现说明"一节。
  - `tools_impl.py` —— 导出 `TOOL_IMPLEMENTATIONS: dict[str, Callable]`，
    是本 skill 与项目侧通用引擎之间**唯一**的接口。
- **项目代码新增一处通用机制**（`src/mini_agent/skills/generative_
  capability/real_tools.py`）：新增
  `load_skill_local_tool_implementations(explorer_base_tools,
  skills_root)`，按 `capability.yaml -> explorer.base_tools` 声明的静态
  skill 名，动态加载各自 `impl/tools_impl.py` 里的 `TOOL_IMPLEMENTATIONS`
  并合并；`build_default_tool_executor()` 新增可选参数 `skill_dir`，传入
  时会自动完成这一层叠加（不传时行为与阶段十二完全一致）。这是**纯粹的
  约定路径 + 动态 import 机制**，不包含任何浏览器/CDP 相关代码，与
  `capability_engine.py::_load_member_run()` 动态加载 member 脚本是同一
  设计风格——项目代码完全不知道、也不需要知道 `browser-core` 内部具体
  怎么实现。
- `src/mini_agent/tools/capability_call.py`：`build_default_tool_
  executor()` 调用处改为传入 `skill_dir=skill_dir`，一行改动，接好上面
  新增的可选参数。
- 文档同步更新：`browser-core/SKILL.md`（从"契约 + 指南"改写为"契约 +
  实现说明 + 已知限制"）、`browser-core/HEADLESS_BROWSER_INTEGRATION.md`
  （从"接入指南"改写为"实现记录 + 验证记录 + 给未来验证者的自测步骤"）、
  `browser-site-scraper/SKILL.md`"已知限制"一节、
  `browser-site-scraper/explorer/tool_allowlist.json` 的 `note` 字段。
- 新增测试 `tests/test_generative_capability_real_tools.py::
  TestSkillLocalToolImplementationLoading`（3 个用例）：验证
  `build_default_tool_executor(skill_dir=...)` 能正确加载
  `browser-core` 的真实实现、不传 `skill_dir` 时行为与阶段十二一致、
  目标 skill 声明的某个 base tool 尚未提供 `impl/tools_impl.py`（如
  `doc-core`）时能安静跳过、不影响其余分发表条目。

**验证结果**：

1. `pytest tests/test_generative_capability_engine.py
   tests/test_generative_capability_real_tools.py -q` → 29 passed
   （阶段十三的 26 个既有用例全部保持通过 + 本阶段新增 3 个），确认
   `real_tools.py`/`capability_call.py` 的改动没有破坏
   `text-transform-capability`/引擎骨架/阶段十三既有行为。
2. 手工验证 `build_default_tool_executor(skill_dir=Path(".claude/skills/
   browser-site-scraper"))`：调用 `browser_navigate` 且 `session.mode=
   "attach"` 指向一个确定没有监听的端口，返回具体到"该怎么解决"的
   错误信息（而不是笼统的"未实现"），确认 `browser-core` 的真实实现
   已经生效、且失败是浏览器连接层面的诚实失败，不是占位声明。
3. 手工验证沙盒环境（无 Chrome/Chromium）下 `auto`/`launch_headless`
   模式：`browser_launch._find_chrome_binary()` 正确返回 `None`，
   `browser_navigate`/`browser_click` 等工具返回带明确解决建议的
   `{"ok": false, "error": "未找到可用的 Chrome/Chromium/Edge..."}"`，
   没有抛出未捕获异常，探索循环不会因此崩溃。
4. 手工验证未命中工具名（如一个不存在的工具名）仍然得到项目侧既有的
   "占位声明，尚未接入真实执行器"提示，证明本阶段的改动没有影响对
   `doc-core` 等仍未实现领域的既有诚实失败行为。
5. `python3 -c "...text_transform_apply..."` 在叠加了 `browser-core`
   实现的分发表里仍正确返回 `{"result": "HI"}`，确认叠加是纯粹的
   "在内置表之上追加"，不会覆盖或影响项目内置的原语。

**已知遗留（留给后续阶段，详见 `browser-core/HEADLESS_BROWSER_
INTEGRATION.md` 第 6-7 节）**：

- **未在真实浏览器环境下做过端到端验证**：本阶段完成时的沙盒环境没有
  可安装/运行的 Chrome，所有验证都停留在"连接失败/依赖缺失时的错误处理
  逻辑正确"这一层，没有验证过"真的打开一个网页、真的点击/输入/提取成功"。
  这是诚实的状态说明——`browser-core/HEADLESS_BROWSER_INTEGRATION.md`
  第 6 节给出了具体的自测步骤（含 `attach` 模式登录场景的验证方法），
  留给下一个有真实浏览器环境的人执行并把结果补充进该文档。
- `session_manager.py` 的会话管理是**进程级**的（模块级字典按
  `(host, port)` 复用），如果宿主 agent 框架未来支持并发跑多个
  `capability_call`，需要补充按调用方/探索会话 id 隔离 session key，
  本阶段暂不涉及（当前引擎设计里探索本身就是同步调用）。
- `doc-core`（`doc-template-generation` 依赖的底层原语）本阶段完全未
  涉及，仍是阶段五遗留下来的占位状态；不过本阶段新增的
  `load_skill_local_tool_implementations` 机制对它同样适用——未来接入
  时只需要在 `.claude/skills/doc-core/impl/tools_impl.py` 提供
  `TOOL_IMPLEMENTATIONS`，不需要改动任何项目代码。
- `browser_extract_content` 的通用（非网站定制）提取策略在复杂页面结构
  下可能拿不到符合 `intent_schema` 的数据，这是预期行为，见
  `browser-core/SKILL.md`"已知限制"一节，不在本阶段解决范围内。

### 阶段十五 —— 已完成

**目标**：明确 `browser-core` + `browser-site-scraper` 是 `browser-cdp`
的替代方案（`browser-cdp` 即将从项目移除），把 `browser-site-scraper`
现存的、仍直接依赖 `browser-cdp/src/searchers/*` 的两个人工预置 member
（`baidu`/`zhihu`）改为直接依赖 `browser-core`；同时纠正 `launch_headed`
（普通/有界面浏览器）的默认行为——打开普通浏览器时应默认使用同一个数据
目录，让使用者手动登录过的数据能一直复用，而不是每次都是一个全新的、
未登录的浏览器窗口。

**改动内容**：

- **`.claude/skills/browser-core/impl/session_manager.py`**：新增模块级
  常量 `DEFAULT_PERSISTENT_PROFILE_DIR`（默认 `~/.mini_agent/browser-core/
  profile`，可用环境变量 `BROWSER_CORE_PROFILE_DIR` 整体覆盖）。
  `get_or_create_session()` 在 `mode="launch_headed"` 且调用方没有显式传
  `session.user_data_dir` 时，自动把这个持久化目录作为
  `spawn_browser(user_data_dir=...)` 的参数；`launch_headless` 不受影响，
  仍沿用原来"缺省时按端口区分的临时目录"（headless 场景通常是无状态的
  一次性抓取任务，不需要强行持久化，保持原样）。这是本阶段最核心的改动，
  直接对应"打开普通浏览器时默认应该使用同一个数据目录，让用户登录的数据
  可以一直使用"这条要求。
- **`.claude/skills/browser-site-scraper/members/baidu/script.py`**（整体
  重写）：不再 `sys.path.insert` 指向 `browser-cdp` 目录、不再 `import
  src.searchers.baidu_search`，改为 `sys.path.insert` 指向
  `browser-core/impl`、直接 `import session_manager`，用
  `session_manager.get_or_create_session(input.get("session"))` 拿到一个
  `CDPSession`，自己调用 `session.navigate()` + `session.eval_js()` 完成
  导航和提取——**提取用的 JS 逻辑本身完整保留自原
  `browser-cdp/src/searchers/baidu_search.py::_extract_results_js()`**，
  没有重写，只是换了执行载体（从 browser-cdp 自己的 CDP 封装换成
  browser-core 提供的 `CDPSession`）。额外新增了一段"检测页面是否命中
  验证码/风控关键词"的 JS 检查——这是本阶段顺带修复的一个真实 bug（此前
  某次真实对话里被观察到：百度返回验证码页时，提取 JS 找不到任何结果
  容器、返回空数组，外层却把这当成"搜索成功但没有结果"，误导性地报告
  `status: success`；现在会明确识别并报告"疑似验证码/风控拦截"，同时
  建议改用 `attach` 模式）。
- **`.claude/skills/browser-site-scraper/members/zhihu/script.py`**（整体
  重写）：同样的改法，提取 JS 逻辑完整保留自原
  `browser-cdp/src/searchers/zhihu_search.py::_extract_results_js()`，
  新增登录墙检测（知乎对未登录访问限制更明显，检测到登录墙关键词且没有
  提取到任何结果时，明确建议改用 `attach` 模式连接一个已登录的浏览器）。
- 两个 member 的 `meta.json` 的 `migrated_from` 字段补充说明依赖关系的
  变化，避免这份"迁移来源"记录显得像是仍然依赖 `browser-cdp`。
- 文档同步更新：`browser-core/SKILL.md`（新增"`browser-core` +
  `browser-site-scraper` 是 `browser-cdp` 替代方案"的明确说明、
  `launch_headed` 持久化 profile 的行为说明、`browser-cdp` 尚未真正物理
  删除的已知限制）、`browser-site-scraper/SKILL.md`"依赖"与"已知限制"
  两节（明确"现在唯一的浏览器操作依赖是 browser-core"、两个 member 已经
  改造完成、验证码/登录墙检测的行为说明）。
- 新增测试 `tests/test_browser_core_session_manager.py`（4 个用例，
  monkeypatch 掉 `spawn_browser`/`is_debug_port_alive` 等，不需要真实
  Chrome）：验证 `launch_headed` 缺省时确实使用
  `DEFAULT_PERSISTENT_PROFILE_DIR`、`launch_headless` 不受这条默认值
  影响、显式传 `user_data_dir` 时能正确覆盖默认值、`attach` 模式在没有
  监听端口时的报错信息具体可操作。

**验证结果**：

1. `pytest tests/test_generative_capability_engine.py
   tests/test_generative_capability_real_tools.py
   tests/test_browser_core_session_manager.py -q` → 33 passed（阶段十三
   26 个 + 阶段十四新增 3 个 + 本阶段新增 4 个，全部保持通过）。
2. `grep -rn "browser-cdp" .claude/skills/browser-site-scraper/members/`
   确认只剩注释/`meta.json` 里的历史沿革说明（"迁移自 XXX，现已改为依赖
   browser-core"这类文字），没有任何 `sys.path`/`import` 语句还指向
   `browser-cdp` 目录，即"browser-site-scraper 不应该再依赖 browser-cdp"
   这条要求在代码层面成立。
3. 手工运行改写后的 `baidu`/`zhihu` member `run()` 函数（不依赖真实
   Chrome）：均正确走到 `session_manager.get_or_create_session()` 尝试
   拉起浏览器这一步，返回诚实的失败信息（`未找到可用的 Chrome...`/浏览器
   进程提前退出等，取决于沙盒当时是否装了浏览器），证明依赖切换后调用链路
   本身是通的，失败仅仅是因为沙盒没有可用的完整 Chrome/Chromium。
4. `tests/test_browser_core_session_manager.py` 里的 4 个用例分别确认了
   `launch_headed`/`launch_headless`/显式覆盖/`attach` 报错四条路径的
   `user_data_dir` 取值符合预期，不依赖真实浏览器即可回归。

**已知遗留（留给后续阶段）**：

- `.claude/skills/browser-cdp/` 目录本身**尚未物理删除**——本阶段只保证
  `browser-site-scraper` 不再需要它存在，真正删除前需要单独确认仓库里
  没有其他脚本/文档/测试还在引用它（比如 `test_cases/browser_cdp_test.txt`
  这类既有测试用例），这项确认工作量独立于本次改动，不在阶段十五范围内。
- `DEFAULT_PERSISTENT_PROFILE_DIR` 目前没有任何自动清理/大小限制机制，
  长期使用会不断积累浏览器数据，需要使用者自己按需清理，本 skill 不会
  主动处理，见 `browser-core/SKILL.md`"已知限制"一节。
- `browser-site-scraper` 目前仍只有 `baidu`/`zhihu` 两个人工预置
  member（`SKILL.md` frontmatter 里的 `category_summary` 提到"淘宝/京东"
  是既有的、比实际落盘 member 数量更宽泛的摘要性描述，本阶段未涉及，
  留给后续需要新增这些 member 时一并核实修正）。
- 本阶段沿用阶段十四"未在真实浏览器环境下做过端到端验证"的已知限制，
  改写后的 `baidu`/`zhihu` member 同样只验证到"调用链路正确、失败信息
  诚实"这一层，没有验证过"真的抓到了百度/知乎的真实搜索结果"，需要在有
  真实浏览器的环境下补验证。

### 阶段十六 —— 已完成

**背景**：`browser-site-scraper` 实际运行时经常失败，需要优化机制让 agent
能根据错误信息自主修复 skill 脚本；同时确认 `browser-core`/`browser-site-scraper`
是 `browser-cdp` 的最终替代（`browser-cdp` 后续将被移除）。

**诊断到的问题**：

1. **热更新不完整**：`members/<id>/script.py` 每次都是 `importlib` 重新读文件
   执行，天然热更新；但 `browser-core/impl/*.py`（`browser_core_impl.py` /
   `session_manager.py` / `cdp_client.py` / `browser_launch.py`）内部互相用
   普通 flat `import`，第一次加载后被缓存进 `sys.modules`，之后哪怕重新调用
   `capability_call`，这几个文件仍是内存里的旧版本——这是"改了 browser-core
   脚本但执行的还是旧版本"的根因。
2. **默认无头浏览器**：`session_manager._resolve_config` 默认 `mode=auto`，
   attach 不到端口时退化为 `launch_headless`，不便于需要登录的场景。
3. **失败信息薄**：所有工具失败只返回一句 `{"ok": false, "error": "..."}"`，
   没有页面快照（HTML/截图/console），排查困难。
4. **缺少面向"改脚本再验证"的调试入口**：只有 `capability_engine.py
   --stub-...` 这种命令行自测，不面向真实网站调试。

**改动方案（全部落在 skill 目录内，或纯粹的通用加载机制，不含浏览器逻辑）**：

- **A. 热更新**（`real_tools.py`）：`load_skill_local_tool_implementations`
  加载 `tools_impl.py` 前先清掉该 skill impl 目录下已缓存的旧模块（按目录下
  实际文件名动态识别，不写死列表），再重新 import。纯通用机制，所有
  `impl/tools_impl.py` 类 skill 都受益。
- **B. 默认有头浏览器**（`browser-core/impl/session_manager.py`）：`auto`
  模式兜底从 `launch_headless` 改为 `launch_headed`（复用阶段十五已加的
  持久化 profile），保留显式 `launch_headless` 的纯后台抓取路径。
- **C. 更详细的错误信息 + 调试原语**（`browser-core/impl/`）：现有工具失败
  分支补充 `url`/`title`/正文摘要等上下文；新增 `browser_get_page_source`、
  `browser_get_debug_snapshot`（html 摘要 + 截图 + console）两个调试工具。
- **D. 调试循环支持**：`browser-site-scraper/dev/debug_run.py`（开发期工具，
  不进 `_index.json` 检索，遵循第 10 节"开发期工具不进 skill 索引"的约定），
  失败时打印结构化调试快照；文档给出"跑 debug_run → 看结构化错误 → 改脚本 →
  因为 A 项热更新，无需重启进程直接重跑验证"的自助修复流程。

**实施顺序**：A → B → C → D，每完成一步更新本文档与相关 `SKILL.md`，并打包
该步骤新增/修改的文件（保持目录结构）供下载。

**实际改动文件**：

```
src/mini_agent/skills/generative_capability/real_tools.py   # 修改：A 热更新
.claude/skills/browser-core/impl/session_manager.py         # 修改：B 默认有头
.claude/skills/browser-core/impl/browser_core_impl.py       # 修改：C 更详细错误信息 + 新增
                                                               # browser_get_page_source /
                                                               # browser_get_debug_snapshot
.claude/skills/browser-core/impl/tools_impl.py               # 修改：导出两个新调试工具
.claude/skills/browser-core/SKILL.md                          # 修改：契约 + 会话模式 + 已知限制更新
.claude/skills/browser-site-scraper/explorer/tool_allowlist.json  # 修改：加入两个新调试工具
.claude/skills/browser-site-scraper/SKILL.md                  # 修改：新增"调试"一节
.claude/skills/browser-site-scraper/dev/debug_run.py          # 新增：D 调试循环入口（开发期工具，
                                                               # 不进 _index.json 检索）
tests/test_browser_core_session_manager.py                    # 修改：新增 auto->headed 回归用例
tests/test_generative_capability_real_tools.py                # 修改：新增热更新回归用例
next_doc/generative-capability-skill-plan.md                  # 本文档（阶段十六记录）
```

**已实现能力**：

- **A. 热更新**：`load_skill_local_tool_implementations` 在加载
  `tools_impl.py` 前，先按 impl 目录下实际存在的 `.py` 文件名清掉
  `sys.modules` 里对应的缓存（不写死具体文件名），再重新加载——保证
  `browser_core_impl.py`/`session_manager.py`/`cdp_client.py`/
  `browser_launch.py` 这类被 flat import 引用的文件，修改后下一次
  `capability_call` 调用就是最新代码，不需要重启 agent 进程。副作用（会话
  复用字典 `_sessions` 跟着清空重建，浏览器进程/登录态不受影响）记入
  `browser-core/SKILL.md`"已知限制"一节，不掩盖。
- **B. 默认有头浏览器**：`session_manager.py` 的 `auto` 模式在 attach 不到
  已有浏览器时，退化目标从 `launch_headless` 改为等价于 `launch_headed`
  的行为（`headless=False` + 复用 `DEFAULT_PERSISTENT_PROFILE_DIR`），
  方便需要登录的场景直接在弹出窗口里手动登录；`launch_headless` 仍可通过
  显式指定 `session.mode` 使用。
- **C. 更详细的错误信息**：`browser_core_impl.py` 新增 `_debug_context()`/
  `_fail()` 两个内部辅助，原有 7 个工具的所有失败分支统一改用 `_fail()`，
  只要当时浏览器会话还能响应就尽力附带 `debug`（url/title/正文摘要）字段；
  取调试信息本身失败时安静省略，不掩盖原始错误。新增
  `browser_get_page_source`（取当前页 HTML，可选按 selector 限定）与
  `browser_get_debug_snapshot`（一次性打包 url/title/正文摘要/HTML摘要/
  截图路径），两者都加入 `browser-site-scraper/explorer/tool_allowlist.json`，
  供探索子agent在失败时主动排查。
- **D. 调试循环**：新增 `browser-site-scraper/dev/debug_run.py`（放在
  `dev/` 而非 `members/`，不进 `_index.json` 检索，遵循第 10 节既有约定），
  对指定 member+request 跑一次真实 `execute()`，失败时打印带 `debug` 字段
  的结构化错误；`SKILL.md` 给出"改脚本 → 因 A 项热更新直接重跑 → 无需重启"
  的自助修复流程说明。

**验证结果**：

1. `pytest tests/test_generative_capability_engine.py
   tests/test_generative_capability_real_tools.py
   tests/test_browser_core_session_manager.py -q` → 35 passed（阶段十五
   33 个既有用例全部保持通过 + 本阶段新增 2 个：`auto` 模式退化为有界面
   浏览器的回归用例、`impl/` 目录下文件编辑后免重启立即生效的热更新回归
   用例——后者用临时构造的假 skill 验证，不污染仓库文件）。
2. 手工调用改造后的 `browser_get_page_source({})`（沙盒无可用 Chrome）→
   如实返回"未找到可用的 Chrome/Chromium/Edge..."的具体错误，未抛出未捕获
   异常，验证新工具的失败路径与既有 7 个工具风格一致（诚实失败，不伪造
   成功）。
3. `grep -c '_fail(' browser_core_impl.py` 确认原有 7 个工具的全部 13 处
   失败分支（除 `browser_navigate` 单独手工替换的 1 处外，其余 12 处由
   脚本批量替换）都已切换到统一的 `_fail()` helper，语法检查
   （`ast.parse`）全部通过。

**已知遗留（留给后续阶段）**：

- 热更新的代价是 `session_manager._sessions` 复用字典每次调用都被清空
  重建，同一次 `capability_call` 内部虽然依旧复用同一个会话（构造顺序上
  `load_skill_local_tool_implementations` 只在最外层 `build_default_tool_
  executor()` 调用一次），但跨越两次独立的 `capability_call`/`debug_run.py`
  调用后，Python 层面的会话对象不再是同一个（会重新 attach、可能是不同
  tab）。这是"调试时脚本必须能热更新"与"同进程内长期复用同一 tab"之间
  刻意的取舍，后续如果需要两者兼得，需要把"是否清缓存"做成可选开关（如
  只在检测到文件 mtime 变化时才清），本阶段未做这个优化。
- `browser_get_page_source`/`browser_get_debug_snapshot` 与其余 7 个工具
  一样，仍未在真实浏览器环境下做过端到端验证（沙盒无可用 Chrome），只验证
  到"无浏览器时失败路径正确、诚实"这一层，延续阶段十四/十五已如实记录的
  同一项已知限制。
- 本阶段未改动 `explore()`/`distill()` 状态机本身、未新增"agent 根据错误
  自动改写 member 脚本"的自动化机制——`dev/debug_run.py` 只是把"人工/agent
  手动改脚本再验证"这条路径变得顺手，是否要做成完全自动的"看到失败 →
  自动生成补丁 → 自动验证"闭环，留给有真实失败样本积累之后再评估，避免
  在没有真实数据支撑前过度设计。

### 阶段十七 —— 已完成

**触发**：真实使用中观察到一个具体反例——对百度搜索"test"这样必然有结果
的查询，`capability_call` 返回 `{"status": "success", "data": {"results":
[]}}"`，看起来像是调用成功了，实际上什么有用信息都没有，也没有任何调试
线索能判断是"真的没结果"还是"哪一步出了问题"。这正是"没有用的结果同样
是有问题的，但缺少调试信息"这条反馈指出的问题。

**根因**：`baidu`/`zhihu` 两个人工预置 member 只在命中已知反爬/登录墙
关键词时才判失败；提取脚本因为选择器过期/页面结构变化/内容尚未渲染完成
等**未被预料到的原因**找不到任何结果容器时，会落到"没有异常、也没有命中
已知关键词"的默认路径，直接返回 `status: success, results: []`——这是一个
真实的逻辑漏洞，不是概率性的偶发失败。

**改动内容**：

```
.claude/skills/browser-core/impl/browser_core_impl.py           # 修改：_debug_context()
                                                                    # 改为调用新公开的
                                                                    # capture_debug_context()
.claude/skills/browser-site-scraper/members/baidu/script.py      # 修改：0 条结果且未命中
                                                                    # 已知关键词时改为 fail +
                                                                    # 附带调试快照
.claude/skills/browser-site-scraper/members/zhihu/script.py      # 修改：同上
.claude/skills/browser-core/SKILL.md                              # 修改：说明 capture_debug_context
                                                                    # 可被 member 复用
.claude/skills/browser-site-scraper/SKILL.md                      # 修改："已知限制"补充说明
tests/test_browser_site_scraper_members.py                        # 新增：5 个用例
next_doc/generative-capability-skill-plan.md                      # 本文档（阶段十七记录）
```

**已实现能力**：

- `browser_core_impl.py` 新增公开函数 `capture_debug_context(session)`
  （原 `_debug_context()` 改为直接调用它，行为不变，向后兼容），供不经过
  `tool_executor` 通用分发层的 member（`baidu`/`zhihu` 直接 `import
  session_manager` 自己调用 `session.navigate`/`eval_js`）复用同一份"取
  url/title/正文摘要"的调试快照逻辑，不用重复实现。
- `baidu`/`zhihu` 的 `run()`：提取到 0 条结果、且未命中各自已有的反爬/
  登录墙关键词检测时，不再直接返回成功，而是调用 `capture_debug_context()`
  附带调试快照后返回 `status: fail`，错误信息里明确说明"更可能是选择器
  过期/页面结构变化/内容未渲染完成，而不是真的没有搜索结果"——这个失败
  会正常计入 `registry.json` 的 `fail_count`/`consecutive_failures`，达到
  阈值后触发既有的 `degraded → 重新探索` 生命周期流转（第 6/7 节既有机制），
  而不是像此前那样因为"看起来成功"而完全绕过这条自愈路径。
- 已知的反爬关键词命中路径不受影响（`baidu` 检测到验证码/风控关键词、
  `zhihu` 检测到登录墙关键词时，仍然按原有逻辑返回带具体原因的失败）。

**验证结果**：

1. `pytest tests/test_browser_site_scraper_members.py -q` → 5 passed：
   - `baidu`/`zhihu` 各自验证"0 条结果+无已知关键词 → fail 且 error 里带
     调试信息（含页面 url）"；
   - `baidu`/`zhihu` 各自验证"非空结果 → 仍然 success"（确认修复没有把
     正常场景也判失败）；
   - `baidu` 额外验证"命中已知验证码关键词 → fail 且原因具体"这条既有
     路径未被破坏。
   - 测试通过 monkeypatch `sys.modules['session_manager']`（而非误以为
     `session_manager` 是 script 模块的属性——它其实是 `run()` 函数内部
     的局部 flat import）验证：局部 import 在 `sys.modules` 已有同名模块
     时只是绑定同一个模块对象的引用，因此打桩模块对象本身即可让 member
     内部的局部 import 生效，不需要真实浏览器/网络。
2. `pytest tests/test_generative_capability_engine.py
   tests/test_generative_capability_real_tools.py
   tests/test_browser_core_session_manager.py
   tests/test_browser_site_scraper_members.py -q` → 40 passed（阶段十六
   35 个既有用例全部保持通过 + 本阶段新增 5 个），确认改动没有影响
   `capability_engine`/`real_tools`/`session_manager` 其余既有行为。

**已知遗留（留给后续阶段）**：

- "0 条结果一律判失败"是一个启发式，对某些查询词真实结果本来就是 0 条
  的场景（理论上存在，但对 `baidu`/`zhihu` 这类通用搜索场景极其罕见）会
  产生误判——判断"这是不是一个真的会有结果的查询"需要更多上下文（比如
  历史同类查询的结果分布），本阶段没有做这类统计判断，只是把"明显可疑的
  空结果"从"静默成功"改为"如实失败+带调试信息"，这本身已经是更诚实的
  默认行为，比此前的"一律成功"更安全。
- 调试信息目前是拼进 `error` 字符串（`f"...: {debug!r}"`），可读但不是
  结构化字段；`capability_call.py` 的 payload 目前也没有单独的 `debug`
  顶层字段（`ExecuteResult`/`CapabilityCallResult` 只有
  `status/data/error/member_id/resolve_reason`）。如果后续需要更精细地
  展示调试信息（比如单独渲染 url/title，而不是整段塞进错误文案），需要
  扩展 `ExecuteResult`/`CapabilityCallResult` 数据结构，本阶段为了不影响
  既有调用方解析 `error` 字段的方式，选择了侵入性最小的字符串拼接方案。
- 探索链路（`explorer_runtime`/`distiller`）目前不受本次改动影响——本次
  只修了两个**人工预置** member 自己的判断逻辑；未来蒸馏产生的新 member
  是否也该有类似"结果为空时不轻易判成功"的保护，取决于具体领域的
  `intent_schema` 语义，留给以后接入更多真实站点时按需处理，不在本阶段
  强行推广成引擎级别的通用规则（"结果数组为空"不一定对所有领域都是可疑
  信号，比如某些查询场景下"确实没有匹配项"就是正确答案）。

### 阶段十八 —— 已完成

**触发**：真实使用 `browser-site-scraper` 抓百度搜索"test"时，`capability_call`
返回 `status: not_implemented`，`note` 字段却固定写着"当前运行环境尚未接入
真正的底层操作原语执行器（explore_runner/tool_executor）"——但实际上阶段
十二/十四早已经把这条链路接好了（`build_default_tool_executor(skill_dir=...)`
会加载 `browser-core/impl/tools_impl.py`），这次的真实原因是 `baidu` member
执行 + 重新探索都撞上了百度自己的验证码/反爬拦截。用户据此误判为"接线没
做"，排查方向被带偏。同时用户报告"感觉还是打开的无头浏览器"、看不到调试
用的有界面窗口。

**根因（两个独立问题）**：

1. `tools/capability_call.py` 里 `if result.status == "not_implemented":`
   分支下的 `note` 文案是阶段十二时写的固定字符串，只要状态是
   `not_implemented` 就无条件贴上去，从未随后续阶段（十二起真的注入了
   `tool_executor`）更新过——文案描述的是阶段十二**之前**的状态，阶段十二
   之后就已经过期，但没人去改这段提示语。
2. `session_manager.py` 的 `auto` 模式（阶段十六起默认 `launch_headed`）
   只有在目标端口**没有**浏览器监听时才会拉起新的有界面浏览器；如果端口
   上已经有浏览器在监听（不管是不是无头的、是不是本 skill 自己之前拉起的、
   是不是这次调用还记得它），会直接复用，不会弹出新窗口。而
   `real_tools.py::load_skill_local_tool_implementations` 为了支持热更新，
   每次 `capability_call` 顶层调用都会强制清空 `session_manager.py` 模块
   缓存重新加载一次，导致 `_sessions` 字典每次调用都从空开始——即使上一次
   调用确实是本 skill 自己拉起的有界面浏览器，这次调用也已经"不认得"它了，
   只知道端口上有东西在监听，于是走 attach 分支，静默复用（可能是无头的）
   旧进程，用户看不到任何新窗口，误以为"又开了无头浏览器"。这不是一个
   会抛异常的 bug，是"设计上刻意为了登录态延续而复用端口"与"用户想清楚
   看到这次到底连的是谁、是不是有头"这两个诉求之间此前没有工具弥合的
   缺口。

**改动内容**：

```
src/mini_agent/tools/capability_call.py                          # 修改：note 改为据实检测
src/mini_agent/skills/generative_capability/real_tools.py         # 修改：拆出 build_dispatch_table()
src/mini_agent/skills/generative_capability/__init__.py           # 修改：导出 build_dispatch_table
.claude/skills/browser-core/impl/session_manager.py                # 修改：会话记录 mode/headless 字段；
                                                                     # 新增 detect_headless_hint()/
                                                                     # list_sessions()/close_session()/
                                                                     # close_all_sessions()
.claude/skills/browser-core/impl/cdp_client.py                     # 新增：get_browser_version()
.claude/skills/browser-core/impl/browser_core_impl.py               # 新增：browser_list_sessions()/
                                                                     # browser_close_session()
.claude/skills/browser-core/impl/tools_impl.py                      # 修改：导出上面两个新工具
.claude/skills/browser-core/manage.py                               # 新增：独立 CLI，不经过
                                                                     # capability_call/探索子agent
.claude/skills/browser-site-scraper/explorer/tool_allowlist.json    # 修改：加入两个新工具名 + 说明
docs/skill-system-guide.md                                          # 修改：更正"默认不注入
                                                                     # explore_runner/tool_executor"
                                                                     # 的过期表述
next_doc/generative-capability-skill-plan.md                        # 本文档（阶段十八记录）
```

**已实现能力**：

- **note 文案据实化**：`real_tools.py` 拆出 `build_dispatch_table(skill_dir=...)`
  （只构造 `tool_name -> 实现函数` 的字典本身，不包装成 executor 闭包，纯
  查表不调用），`capability_call.py` 新增 `_find_unwired_tools(skill_dir)`：
  读 `explorer/tool_allowlist.json` 声明的工具名，逐个查分发表里有没有对应
  实现。`not_implemented` 时：有工具确实缺实现 → note 里列出具体工具名；
  全部已接线 → note 改为"接线没问题，这是真实探索/执行失败，看 error"。
  刻意不直接调用 `tool_executor(tool_name, {})` 来探测，因为 `browser_
  navigate` 这类工具一调用就会触发真实的 `get_or_create_session()`（可能
  拉起浏览器进程），拿有副作用的调用做"是否已接线"的诊断不合理。
- **会话可见性**：`session_manager.py` 的 `_SessionEntry` 新增 `mode`
  （`attach`/`launch_headless`/`launch_headed`/`attach(auto)`/
  `launch_headed(auto)`）与 `headless`（`True`/`False`/`None`）字段——
  我们自己 `spawn_browser` 拉起的会话这个字段是确定已知的；`attach` 到的
  会话不知道，留 `None`，交给新增的 `detect_headless_hint(host, port,
  session=...)` 做启发式判断：优先看 `/json/version` 的 `Browser` 字段是否
  含 `Headless`（只对旧版 `--headless` 有效，本 skill 默认用的
  `--headless=new` 命中不了这个信号，命中时可信、没命中不能反推为有头）；
  命中不了再退化为探测 `window.chrome` 对象是否存在（无头 Chrome 不管新旧
  版本默认都不注入这个对象，覆盖面更广但不是协议承诺的特性，标 `medium`
  置信度）；两个信号都拿不到时如实返回 `headless: None`，不瞎猜。
  `list_sessions()` 汇总当前进程已知的会话及上述判断结果。
- **列举/关闭工具**：`browser_list_sessions(tool_input)`——列出
  `list_sessions()` 的结果，并默认额外探测标准端口 9222 是否有浏览器在
  监听（不需要显式传参，因为热更新会清空 `_sessions`，"探测标准端口"是
  唯一能跨越这个限制看到"9222 现在有没有东西"的办法；也支持
  `tool_input.probe` 探测其他 host/port）。`browser_close_session(tool_input)`
  ——关闭指定 `host`/`port`（默认 `127.0.0.1:9222`）或 `tool_input.all=True`
  关闭全部已知会话；`kill_process`（默认 `True`）控制是否真的终止底层进程，
  但只对本 skill 自己 `spawn_browser` 拉起的会话生效，`attach` 到的用户
  自己启动的浏览器永远不会被杀（与既有约定一致）；对没有会话记录的
  `host`/`port`（常见于热更新清空了记录，但进程其实还在跑）如实返回
  `closed_our_session: False` 并在 `note` 里给出系统层面手动关闭的具体
  命令（Windows: `netstat -ano | findstr <port>` + `taskkill /PID <pid> /F`）。
  两个工具已加进 `browser-site-scraper/explorer/tool_allowlist.json`，探索
  子agent遇到"怀疑连错了浏览器"时也能自己排查。
- **独立管理 CLI**（`.claude/skills/browser-core/manage.py`）：`python
  manage.py list` / `close --port 9222` / `close --all` / `close --port
  9222 --no-kill`，不经过 `CapabilityEngine`/探索子agent，不消耗探索预算，
  也不依赖 `Agent.llm_helper`。因为是独立一次性进程，`list` 同样默认探测
  9222；`close` 在没有会话记录但目标端口确实存活时，会比 `browser_close_
  session` 工具原语多做一步——先临时 `attach` 一次建立会话记录，再关闭
  ——这一步只有本脚本会做（工具原语出于"不擅自发起未声明的连接"的原则不
  这样做），方便用户直接从 PowerShell 清理遗留进程而不用去翻 `netstat`。
- **文档纠偏**：`docs/skill-system-guide.md` "已知限制"一节此前仍写着
  "`capability_call` 默认不注入 `explore_runner`/`tool_executor`"，是阶段
  七时代的表述，阶段十二起已经不成立（与阶段十三"核对确认"时留下的同类
  纠偏注释是同一类问题——阶段推进后，早前小节里的过期表述没有被同步更新）。
  本阶段一并更正，改为准确描述阶段十二/十四之后的真实状态：`text-core`/
  `browser-core` 已接入真实执行器，`doc-core` 仍是占位。

**验证结果**：

1. `python3 -m py_compile` 通过本阶段修改/新增的全部 `.py` 文件（项目代码
   + `browser-core/impl/` + `browser-core/manage.py`），确认无语法错误。
2. 人工核对 `capability_call.py` 的 `_find_unwired_tools()` 调用链：
   `build_dispatch_table(skill_dir=...)` 返回的分发表 key 集合与
   `tools_impl.py::TOOL_IMPLEMENTATIONS` 的 key 集合一致（`text_transform_
   apply` + `browser_navigate` 等 9 个 browser-core 工具），`doc-core` 相关
   工具（`doc-template-generation` 用）不在任何分发表里，仍会被判定为
   "未接线"，符合预期。

**已知遗留（留给后续阶段）**：

- `session_manager.py` 的会话记录仍然是**单进程内存字典**，`list_sessions()`
  只能看到"本次 `capability_call` 调用内已经建立过的会话"，看不到跨调用/
  跨进程的历史（根因是阶段十六为了支持热更新引入的"每次强制清模块缓存"，
  见 `load_skill_local_tool_implementations` 文件头的"已知限制"说明，本
  阶段没有改变这个设计取舍，只是用"默认探测标准端口"这个变通办法缓解了
  它对"列举/关闭"这两个新功能可用性的影响）。如果后续需要真正跨进程/跨
  调用的会话registry（比如展示"过去 24 小时内启动过哪些调试浏览器"），
  需要把会话元信息落盘（比如一个 JSON 文件），而不是继续依赖进程内内存
  字典，这是一个更大的改动，本阶段未做。
- `detect_headless_hint()` 的两个信号都是启发式，`--headless=new` 之后
  Chrome 团队本身就是在刻意抹平有头/无头的可观测差异（用于反"网站按 UA
  拒绝无头浏览器"检测），未来如果 Chrome 进一步收紧 `window.chrome` 这类
  行为差异，`medium` 置信度的判断也可能失效，届时需要找新的信号（比如
  `Browser.getWindowBounds` 返回的窗口尺寸是否等于不可见的默认值，或者
  直接检测系统层面是否有对应的可见窗口句柄——后者需要平台特定代码，超出
  当前"跨平台 CDP 客户端"的定位，留给以后按需评估）。

### 阶段十九 —— 已完成

**触发**：抓取知乎搜索页时，`capability_call` 返回
`error: "探索超出时间预算(max_seconds)"`，除此之外没有任何别的线索——不
知道命中的 `zhihu` member 自己第一次执行到底为什么失败（大概率是登录墙，
`members/zhihu/script.py` 本身有检测逻辑，但那次失败原因没有出现在最终
结果里），也不知道探索子agent 180 秒里到底卡在哪一步。

**根因（两个独立问题，和阶段十八是同一类"信息在链路中间被丢弃/参数没有
随真实执行场景更新"的问题）**：

1. `capability_engine.py::call()` 在"命中 member 执行失败 → 触发探索 →
   探索也失败"这条路径上，只把 `explore_result.error` 塞进最终结果，
   `exec_result.error`（trusted member 自己那次执行失败的具体原因，比如
   `members/zhihu/script.py` 检测到的"登录知乎"关键词）在 `for member_id
   in resolved.member_ids:` 循环里赋值给 `last_failed_member_id` 之后就
   再也没被读取过，直接丢了。对用户来说，member 自己的失败原因往往比
   探索子agent最后的失败原因更有诊断价值（前者是精确的"为什么"，后者
   经常只是"没来得及/超预算"这种表面原因）。
2. `explorer/tool_allowlist.json`/`capability.yaml` 里的 `max_seconds:
   180` 是阶段三接入配置占位、阶段十二之前用桩探索器（`build_stub_
   explorer`，没有真实网络/LLM延迟）验证接线逻辑时定的数值，阶段十二起
   `explore_runner`/`tool_executor` 换成真实实现后，这个数值没有跟着重新
   评估过。真实探索一步 = 一次 LLM 网络往返（数秒到十几秒，视 provider/
   网络状况）+ 一次真实浏览器操作（`browser_navigate` 默认 20s 超时，
   `browser_wait_for_selector` 默认 8s 超时），零反爬拦截的站点通常几步
   就能走完，但像知乎这类有登录墙的站点，模型需要多试几步（导航→提取→
   发现异常→用 `browser_get_page_source`/`browser_get_debug_snapshot` 确认
   →才敢下判断调用 `report_failure`）才能给出诚实的失败原因，180s 经常
   不够用，模型还没来得及诚实收尾就被 `max_seconds` 强制掐断，最终只剩下
   一句没有信息量的"超出时间预算"。

**改动内容**：

```
src/mini_agent/skills/generative_capability/capability_engine.py   # 修改：
                                                                      # call() 不再丢弃 member 自身的执行失败原因
src/mini_agent/skills/generative_capability/explorer_runtime.py     # 修改：
                                                                      # 剩余时间 <30s 时提醒模型尽快 finish/report_failure
.claude/skills/browser-site-scraper/capability.yaml                  # 修改：
                                                                      # max_seconds 180 -> 300
next_doc/generative-capability-skill-plan.md                         # 本文档（阶段十九记录）
```

**已实现能力**：

- `CapabilityEngine.call()` 新增 `last_exec_error` 跟踪最后一个命中 member
  的执行失败原因；命中 member 执行失败 + 随后探索也失败的路径，最终
  `error` 会把两段原因都如实拼在一起：`"已有能力(member=zhihu)执行失败:
  ...；随后触发的探索子agent也失败: ..."`，不再只剩探索侧一句话。只有
  member 执行本身成功、或者一开始就是 `resolve() miss`（没有命中任何
  member，无 `exec_result` 可言）时，行为与之前一致。
- `explorer_runtime.py` 的探索主循环每一步都会重新计算 `remaining =
  max_seconds - elapsed`；`remaining < 30` 时，只在**当轮**发给 LLM 的
  消息末尾（不写回持久化的 `messages` 历史、也不改 `system_prompt`）追加
  一条提醒："时间预算只剩 N 秒，接下来可能只够再发起 1 次工具调用，能
  finish 就 finish，走不通就立刻 report_failure，不要再尝试新操作"。这样
  真的快超时的场景下，模型有机会用最后一步给出一个有信息量的诚实失败
  原因，而不是被下一轮循环开头的时间检查直接判"超出时间预算"。
- `browser-site-scraper/capability.yaml` 的 `explorer.max_seconds` 从
  180 调到 300，`max_steps` 保持 40 不变（步数预算本身没有暴露出问题）。
  `text-transform-capability`（纯本地逻辑，无网络）与
  `doc-template-generation`（`doc-core` 仍是占位，探索还跑不起来）的
  `max_seconds` 未改动，这两个场景不存在"真实网络延迟"这个变量。

**验证结果**：`python3 -m py_compile` 通过两个修改的 `.py` 文件；
`yaml.safe_load` 确认 `capability.yaml` 改动后仍是合法 YAML。

**已知遗留（留给后续阶段）**：

- 300s 仍是一个经验值，不是理论推导出来的上界——如果目标站点反爬拦截
  更重（比如每次导航都触发验证码，模型需要反复重试才能确认"这条路真的
  走不通"），仍有可能不够用。更彻底的方案是把 `max_seconds`
  做成可以按站点/请求覆盖的参数（比如 `request.explore_budget_seconds`），
  而不是写死在 `capability.yaml` 里对所有请求一视同仁，这个改动涉及
  `request_formats` 校验规则联动，本阶段未做，留给后续按需评估。
- "命中 member 执行失败"这一步的原因目前只有文本 `error` 字符串，没有
  结构化的失败类型（比如 `login_wall`/`captcha`/`selector_not_found`）。
  如果未来想让上层（比如 Growth Advisor 或告警）能自动区分"这是该换
  `session.mode=attach` 的信号"还是"这是选择器该修了"，需要把
  `members/*/script.py` 的失败返回从纯文本 `error` 升级成带分类字段的
  结构体，这会影响所有已有 member 脚本的返回约定，是一次更大的改动，
  本阶段未做。

### 阶段二十 —— 已完成

**触发**：用户在实测中发现，探索子agent受限于自造的工具白名单/步数预算
机制，能力天花板过低（浏览器操作型探索子agent在需要识别公开 API/处理签名
请求的站点上天然到顶，与安全无关）。完整问题分析、方案设计、五个子阶段的
实施记录见独立文档
`next_doc/generative_capability_explorer_rearch_plan.md`（不在本文档重复，
避免两份文档各自维护一份容易漂移的细节）。

**摘要**（详见上述独立文档第 6 节"实施记录"）：

1. `explore()` 从手写多轮 LLM 决策循环改为构造真实 `SubAgent` 驱动，隔离
   性/安全边界/预算全部复用系统既有的 `SubAgent`/`PermissionGuard`/
   `task.max_turns` 基础设施，不再自造平行机制；领域声明的底层原语（如
   `browser_navigate`）通过桥接 `ToolDef` 转发给 `tool_executor`，与系统
   通用工具（bash/python 等）共存于探索子agent的工具集，不再互斥。
2. `distiller.py` 新增"探索者自带 `script_source`"这一优先蒸馏路径：探索
   子agent在 `finish` 时可自行判断解法是否可参数化复用，提交符合
   `run(input)->dict` 约定的脚本源码，交给与原有 trace-replay 路径完全
   相同的"沙箱自测→intent_schema 校验→原子化落盘"流程校验；无
   `script_source` 时原样退回 trace-replay 兜底，不回归。`meta.json` 新增
   `distill_source_kind` 字段如实记录蒸馏来源。
3. `browser-site-scraper`/`text-transform-capability`/
   `doc-template-generation` 三个 skill 的 `capability.yaml`/`prompt.md`
   完成配置迁移（`max_steps`→`max_turns`，移除不再生效的 `max_seconds`，
   `tool_allowlist.json` 继续承担领域工具桥接职责）。
4. 新增 `tests/test_explorer_runtime_subagent.py`、
   `tests/test_distiller_script_source.py`、
   `tests/test_explorer_subagent_engine_e2e.py` 三个测试文件，其中最后一个
   补齐了"`build_subagent_explorer()` 通过 `CapabilityEngine.call()` 跑一次
   完整 miss→explore→distill→落盘→免探索复用闭环"这一此前测试矩阵里缺失
   的组合场景（脚本自带路径 + 预算耗尽不伪造成功两种终态）。与既有
   `test_generative_capability_engine.py`/
   `test_generative_capability_real_tools.py`/`test_orchestrator.py`/
   `test_subagent_inheritance.py` 合并运行共 137 个用例全部通过，无回归。

**本文档同步更新**：第 6 节 explore()/distill() 流程描述、第 8 节安全边界
第 1/2 条措辞，已按上述改动对齐（不再提"base_tools 白名单"这一已废弃概念，
改为准确描述 `task.allowed_tools`/`task.max_turns` 复用）。

### 阶段二十一 —— 已完成（全链路调试日志开关）

**触发**：真实使用中排查一次 `browser_navigate` 报 `Unknown tool` 的问题时，
只能靠肉眼读 stdout/traceback 猜测"探索子agent的 registry 到底有没有被
换成私有副本、`_tool_executor.registry` 有没有同步跟上、每个 domain 工具
是否真的注册成功"——这些原本都需要在代码里加临时 print 才能看到。用户要求
新增一个由 `agent_config.json` 控制的调试开关，打开后详细记录
generative-capability 全链路的调用关系，落盘到独立日志文件；且要求 skill
自己的实现代码（如 `browser-core/impl/*.py`）也能直接写调试信息，开关判断
统一收口在项目通用代码里，skill 代码不需要关心开关状态。

**实施**：

1. 新增 `mini_agent.skills.generative_capability.capability_debug` 模块
   （项目代码，通用机制，不属于任何具体 skill）：`capability_debug_log(event,
   details, where=...)` 供任意调用方直接调用；`configure_capability_debug()`
   同步开关状态（与 `errors.py::configure_tool_executor_log_saving()` 是
   同一种组织方式）；关闭时提前返回，零开销。
2. 日志路径：`~/.agent/logs/capability_debug.jsonl`，与 `error.jsonl` 同
   目录、同轮转策略（`storage/paths.py::AgentPaths.global_capability_debug_log`）。
3. 配置：`agent_config.json` 新增 `debug.capability_enabled`（默认
   `false`），兼容旧 flat key `debug_capability` 和环境变量
   `CAPABILITY_DEBUG`，解析优先级与 `debug.llm_enabled` 一致；
   `config/loader.py::load_config()` 加载完成后调用
   `configure_capability_debug()` 同步模块开关。
4. 项目通用代码埋点：`tools/capability_call.py`（调用入口/返回）、
   `capability_engine.py::call()`（resolve 结果、每个 member 的 execute
   尝试结果）、`explorer_runtime.py::build_subagent_explorer()`（**registry
   是否被替换为私有副本、`_tool_executor.registry` 是否同步、finish/
   report_failure/每个 domain 工具的注册结果、探索步骤序列、探索终态**——
   这正是上次真实复现问题时缺失的可观测性）、`real_tools.py`（分发表构建
   结果、每次工具 dispatch 命中/未接线/异常）。
5. skill 侧示例：`browser-core/impl/browser_core_impl.py` 的 `_session()`
   与 `browser_navigate()` 直接 `import capability_debug_log` 调用，作为
   "skill 实现代码可以直接写、不需要自己判断开关"这条约定的示范；其余
   `browser_click`/`browser_extract_content` 等函数未加（不是必须每个都加，
   按需在排查时补即可）。
6. 全部改动跑通既有测试（`test_explorer_runtime_subagent.py`/
   `test_explorer_subagent_engine_e2e.py`/`test_generative_capability_engine.py`/
   `test_generative_capability_real_tools.py`/`test_capability_cmd.py`，
   共 60 用例）无回归；另外做了三个独立 smoke test 验证：默认关闭时不产生
   任何文件、打开后正确落盘到 `~/.agent/logs/capability_debug.jsonl`、
   `agent_config.json -> debug.capability_enabled` 到模块开关的端到端同步。
