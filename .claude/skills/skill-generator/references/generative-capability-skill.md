# 如何创建一个 generative-capability skill（领域能力包）

> 前提：已经在 `skill-generator` 主文件"第零步"确认了要创建的是这一类
> （模型不需要提前看到具体成员清单，只需要知道"有这个能力，给个目标就能拿
> 到结果或明确失败原因"）。如果还没确认过，先回到主文件重新判断。

## 一句话概括

generative-capability skill 目录下**只放声明式配置和运行时数据**（`SKILL.md`
极简、`capability.yaml`、`explorer/`、`members/`），真正的调度骨架（`resolve
→ execute → explore → distill` 流程、生命周期状态机、检索裁决）是
**平台内置代码**（`src/mini_agent/skills/generative_capability/`），跨所有
这类 skill 复用、不因领域不同而重写——你不需要、也不应该去写这部分代码，只需要
声明"这个领域长什么样"。

## 标准目录结构

```
.claude/skills/<capability-name>/
├── SKILL.md                    # 极简：skill_type + category_summary，不展开成员清单
├── capability.yaml             # 领域配置核心，见下方逐字段说明
├── explorer/
│   ├── prompt.md                # 探索子agent的角色设定（自然语言）
│   └── tool_allowlist.json      # 声明该领域底层原语（可选，见下方"依赖的静态skill"）
├── _index.json                  # 引擎自动维护：成员摘要清单，供检索使用——初始为空即可
├── registry.json                # 引擎自动维护：成员状态机数据——初始为 {"members": {}}
└── members/                     # 可选：人工预置的初始成员（也可以完全为空，靠探索自动补全）
    └── <member-id>/
        ├── script.py             # 统一签名 run(input: dict) -> dict
        └── meta.json             # {source, status, version, intent_schema, stats...}
```

`_index.json`、`registry.json`、以及探索生成的新 member，全部由引擎自动创建
和维护——你只需要初始化成空壳（或完全不创建，引擎首次调用时会自动补齐）。

## 第一步：写 `SKILL.md`（极简，不要展开成员清单）

```yaml
---
name: <capability-name>
skill_type: generative-capability
category_summary: <一句话，说明这个领域能力包解决什么问题——这是主 context 里唯一会展开的文字，务必精炼>
description: <给未识别 skill_type 的旧代码路径/普通 skill 消费方用的兜底文案，写法与静态 skill 一致，可以比 category_summary 详细一些>
triggers: <可选，逗号分隔关键词>
---

# <标题>

<这里可以写给人看的说明文档：预置了哪些 member、有什么已知限制、探索场景设计
等——但这些内容不会被注入主 context（generative-capability skill 的
build_context() 只取 frontmatter 的 category_summary/description，正文
被短路跳过），纯粹是给维护者看的文档，参考 .claude/skills/text-transform-
capability/SKILL.md 的写法。>
```

`skill_type: generative-capability` 是关键标志位，缺失时等价于普通静态
skill（旧格式完全兼容）。`Skill.is_generative_capability` 是引擎侧判断依据。

## 第二步：写 `capability.yaml`（唯一需要认真设计的配置文件）

以最简单的 `text-transform-capability`（纯逻辑、零外部依赖，最适合作为
模板）为参照，逐字段说明：

```yaml
skill_type: generative-capability
name: <capability-name>          # 必须与目录名/SKILL.md 的 name 一致

# 第一级免LLM匹配规则（零成本，命中优先于第二级LLM裁决）。
# 具体 pattern/keyword 值来自各 member 目录下 _index.json 的 "match" 字段，
# 这里只声明"匹配哪些字段"。
domain_matchers:
  - type: keyword          # 或 domain_pattern（专门给URL类字段用，见 browser-site-scraper）
    field: text             # request 里哪个字段参与匹配
  - type: keyword
    field: target.op        # 可以声明多条，逐个尝试

# 零成本形状校验：request 不满足任何一种声明格式时，直接返回
# invalid_request + example，不占用探索预算，也不会被误判成"需要探索新领域"。
# 这是容易被忽略但强烈建议写的一段——没有它，参数写错时会浪费一次探索。
request_formats:
  - name: <格式名，随意取>
    description: <这种格式用于什么场景>
    required_fields: [text, target.op]   # request dict 必须包含的字段路径
    example:                              # 可直接照抄的完整示例，供 agent 参考
      text: "帮我把这段文字转大写"
      target: {op: "upper"}
      content: {text: "hello world"}

# 该领域所有 member 输出必须满足的 schema 结构约束模板。每个具体 member 可以
# 在自己的 meta.json 里进一步细化，但必须是本模板的合法子集/扩展。
intent_schema_template:
  type: object
  required: [result]
  properties:
    result:
      type: object

# 探索阶段配置——没有预置 member、或预置 member 全部未命中时，引擎会构造一个
# 探索子agent（真实 SubAgent，会真的调用 LLM）尝试解决这次请求。
explorer:
  depends_skills: [<某个静态skill名>]   # 该领域依赖哪些静态 skill 提供的底层
                                          # 原语（见下方"依赖的静态skill"一节）。
                                          # 没有可复用原语、纯粹靠通用工具
                                          # （bash/python）也能完成的领域，可以
                                          # 声明一个占位名字，此时自动派生结果
                                          # 为空，探索子agent仍然拥有 bash/
                                          # python 等系统通用工具兜底。
  prompt: explorer/prompt.md
  tool_allowlist: explorer/tool_allowlist.json   # 可选，见下方说明
  max_turns: 40                                    # 回合预算，没有强制要求，
                                                     # 40 是项目里其它两个
                                                     # skill 的常见取值

# member 的统一函数入口约定，引擎按此调用，几乎不需要改动。
member_interface:
  entrypoint: "run(input: dict) -> dict"

# 生命周期阈值（可选，不写则用引擎默认值）。
lifecycle:
  probation_success_threshold: 3     # 连续成功N次，从 probation 转正为 trusted
  degrade_failure_threshold: 3       # 连续失败N次，从 trusted 降级重新探索
  dead_after_reexplore_fail: true    # 重新探索仍失败则标记 dead
  # 以下两项供 skill 升级到 script 用（generative_capability_three_tier_
  # improvement_plan.md 第5节，可选，不写则用 agent_config.json ->
  # generative_capability 的全局默认值，见 config-guide.md）：
  # skill_tier:
  #   max_turns: 40
  #   enable_upgrade: true
  #   upgrade_success_threshold: 3
```

**关于 `depends_skills`（原语从哪来）**：如果这个领域需要驱动浏览器、调用
某个内部 API 之类"通用工具做不到"的操作，需要先有一个（或引用已有的）静态
skill 在自己的 `impl/tools_impl.py::TOOL_IMPLEMENTATIONS` 里提供真实实现
（如 `browser-core` 提供 `browser_navigate`/`browser_click` 等），
`depends_skills` 里声明这个静态 skill 的名字，引擎会自动派生出可用原语名单，
不需要手写一份清单去维护。如果这个领域纯粹是逻辑处理（不需要外部原语，
`bash`/`python`/`read_file` 等系统通用工具就够用），可以只声明一个占位名，
探索子agent仍然可以正常工作（只是没有额外的领域专属工具）——参考
`text-transform-capability` 声明 `depends_skills: [text-core]` 但
`text-core` 目前仍是占位这一实际案例。

**`explorer/tool_allowlist.json`**（可选，仅在需要额外声明领域专属工具时才
需要）：

```json
{
  "base_tools": ["<某个静态skill名>"],
  "tools": [
    {
      "name": "<工具名>",
      "description": "<给探索子agent看的工具说明，包含input/output格式>",
      "status": "implemented"
    }
  ]
}
```

## 第三步：写 `explorer/prompt.md`（探索子agent的角色设定）

自然语言，不是代码。核心要交代清楚：

1. 任务是什么、期望产出的数据结构（与 `intent_schema_template` 对应）
2. 有哪些工具可用（`tool_allowlist.json` 里声明的 + bash/python 等通用工具）
3. 回合预算限制、失败时如何用 `report_failure` 如实说明而不是编造数据
4. **强烈建议加上"禁止把观察到的具体结果写死进返回值"这条约束**——这是
   `text-transform-capability/explorer/prompt.md` 里验证过有效的一条防呆
   提示，探索子agent很容易图省事把这次看到的具体数据直接硬编码进
   `script_source`，导致换个输入就返回错误结果。

## 第四步：初始化 `_index.json`/`registry.json`（可选，留空也可以）

```json
// _index.json
{"members": []}
```

```json
// registry.json
{"members": {}}
```

不创建这两个文件也完全可以——引擎首次调用 `capability_call` 时会自动创建。
只有当你想**同时预置若干 member** 时才需要手写这两个文件（把每个预置 member
的 `_index.json.members[].match.keyword` 和 `registry.json.members.<id>`
的初始状态都补齐），此时更推荐照抄 `text-transform-capability` 或
`browser-site-scraper` 现成的文件直接改字段，而不是从空白开始写。

## 第五步（可选）：预置初始 member

`members/<member-id>/script.py`：

```python
"""统一接口: run(input: dict) -> dict
input 约定: {"text": "...", "target": {...}, "content": {...}}（与 capability.yaml
里 request_formats 声明的形状对应）
返回: {"status": "success"|"fail", "data": {...} | None, "error": str | None}
"""

def run(input: dict) -> dict:
    # 校验必要字段，缺失时显式返回 fail，不要抛未捕获异常
    ...
    return {"status": "success", "data": {"result": {...}}, "error": None}
```

`members/<member-id>/meta.json`：

```json
{
  "source": "human",
  "status": "trusted",
  "version": 1,
  "intent_schema": { "...": "与 intent_schema_template 兼容的具体schema" },
  "success_count": 0,
  "fail_count": 0,
  "last_success": null,
  "last_failure": null,
  "migrated_from": null
}
```

没有预置 member 完全没问题——第一次真实调用会自动触发探索，探索成功后
`distill()` 会自动把结果蒸馏成新 member 落盘（三条蒸馏路径见
`skill-system-guide.md` 3.8 节），这正是这类 skill 设计成"从零开始也能
自我生长"的核心机制，不需要你提前想好所有成员。

## 创建后如何验证

1. 用 `skill_list` 工具确认新 skill 被发现，`skill_type`/`category_summary`
   显示正确。
2. 在对话里用 `capability_call(skill_name="<capability-name>", request={...})`
   构造一次符合 `request_formats` 声明的请求，验证能走到 `resolve`（如果
   预置了 member）或触发探索（如果没有预置 member）。
3. 故意构造一次不满足 `request_formats` 的请求，验证会得到
   `status: invalid_request` 而不是被误判成"需要探索"。
4. 参考 `test_cases/text-transform-capability-testing-guide.md`（基础机制）
   和 `test_cases/browser-site-scraper-three-tier-testing-guide.md`（三档
   机制）里的对话式测试步骤，照着同样的思路给新 skill 写一份配套测试指南。

## 参考实现

- `.claude/skills/text-transform-capability/`：**从零开始的最佳模板**——
  纯逻辑、零外部依赖，`capability.yaml`/`explorer/prompt.md`/
  `explorer/tool_allowlist.json` 全部字段都有完整示例，且专门设计成可以
  在任意沙箱/CI 环境跑通完整链路。**新建 generative-capability skill 时，
  照抄这个目录改字段是最快的起点，不要从空白开始写。**
- `.claude/skills/browser-site-scraper/`：真实业务能力，展示了如何
  `depends_skills` 一个真正提供浏览器操作原语的静态 skill（`browser-core`），
  以及 `domain_pattern` 类型的 `domain_matchers`（针对 URL 匹配）、
  `lifecycle -> skill_tier` 覆盖全局默认值的写法。

## 引擎内部行为不需要你关心的部分（了解即可，不需要照着实现）

三档执行机制（script→skill→explore）、SKILL 档（人类可读 playbook 兜底）、
自动升级蒸馏为脚本、生命周期状态机、检索两级过滤、raw_result 落盘化等，
全部是平台内置代码的行为，你只需要按上面的字段声明配置，不需要（也不应该）
在 skill 目录里重新实现任何调度逻辑。完整设计文档：

- 整体机制设计：`next_doc/generative-capability-skill-plan.md`
- 三档机制（script→skill→explore）：
  `next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md` 第3节
- 三档机制的后续改进（`available_tiers`/升级节流/`allow_tiers`/SKILL档默认
  启用）：`next_doc/generative_capability_three_tier_improvement_plan.md`
- 完整字段索引与调用入口代码：`docs/skill-system-guide.md` 3.8 节
