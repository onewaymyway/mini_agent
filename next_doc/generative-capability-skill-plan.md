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

### 阶段二 —— 未开始
### 阶段三 —— 未开始
### 阶段四 —— 未开始
### 阶段五 —— 未开始
