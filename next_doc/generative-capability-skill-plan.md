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
   → 按 capability.yaml 中 explorer 配置，拉起隔离 context 的探索子agent
   → 仅允许调用 base_tools 声明的底层原语
   → 步数/时间超出预算硬性中止，判定失败
   → 产出必须经过同一份 intent_schema 校验
   ├─ 校验通过 → 进入 distill
   └─ 校验不过 → 返回 {status: fail, error: 具体失败原因}（不编造数据）

4. distill(trace, intent_schema)
   → 把探索动作序列蒸馏为参数化脚本（非原样保存trace）
   → 沙箱内自测该脚本，用 intent_schema 再校验一次
   ├─ 自测通过 → 原子化写入 members/、registry.json（status: probation）、
   │    _index.json（新增摘要，供下次检索命中）
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

1. **工具白名单强制**：探索子agent 只能调用 `capability.yaml` 声明的 `base_tools`，不能任意扩展权限。
2. **步数/时间预算硬上限**：超限直接判失败返回，禁止无限重试。
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
