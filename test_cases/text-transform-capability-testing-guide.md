# text-transform-capability 测试指南（在 agent 里对话验证）

> 对应 `.claude/skills/text-transform-capability/`（一个刻意做得很小、零外部
> 依赖的 `generative-capability` skill）。
>
> 目的：验证 `generative-capability` 这套新 skill 机制**在真实 agent 对话
> 场景里可用**——不是走代码直接调用引擎（那部分回归覆盖见
> `tests/test_generative_capability_engine.py`），而是像真实用户一样在
> mini-agent 的对话界面里输入内容，检查 agent 是否会：
> 1. 通过 `skill_list` 发现这是一个 `generative-capability` 类型的 skill；
> 2. 调用 `capability_call` 工具而不是 `skill_activate`；
> 3. 拿到正确的转换结果，并如实转述给你（成功就是成功，做不到就说明原因，
>    不会编造一个看似合理的答案）。
>
> 需要一个能实际对话的 mini-agent 会话（配置好任意一个 provider 的 API
> key）。下面每一步都给出：**该在会话里输入什么** + **预期看到什么**。

---

## 前置条件

1. 已经能正常启动 mini-agent CLI 并进行对话（`mini-agent` 或项目里对应的
   启动方式），且配置了可用的 LLM provider。
2. `.claude/skills/text-transform-capability/` 在当前项目目录下存在（随
   仓库自带，无需额外安装）。
3. 不需要 `ANTHROPIC_API_KEY` 之外的任何东西——本 skill 的两个预置 member
   （`upper`/`reverse`）是纯 Python 逻辑，不发起网络请求；触发探索路径时
   （见步骤 4）也不需要真的探索成功，重点是验证 agent 会如实报告限制。

> 每一步都在**同一个会话**里连续输入即可，不需要每次重启。

---

## 步骤 1：确认 agent 能发现这个 skill

**输入：**

```
看一下项目里有哪些 generative-capability 类型的 skill，分别是做什么的？
```

**预期效果：**

- agent 应该调用 `skill_list` 工具（你可能会在工具调用日志/详情里看到），
  返回的 skill 目录里 `text-transform-capability` 这一项会带
  `"skill_type": "generative-capability"` 和 `"category_summary"` 字段。
- agent 的回复里应该提到 `text-transform-capability`（连同
  `browser-site-scraper`、`doc-template-generation`），并用
  `category_summary` 里的话描述它是"对一段文本做简单确定性变换（大写/
  反转等），用于验证机制本身，不建议作为真实业务能力使用"这类说法，而不是
  把它当成一个真实能力来推荐。

这一步验证的是：`generative-capability` skill 不会把 member 清单（`upper`/
`reverse` 具体代码）泄漏进 agent 的主 context，agent 只能看到一行摘要——
这正是方案设计的"按需检索"，而不是"全量加载"。

---

## 步骤 2：确定性匹配命中 + 真实执行成功（大写）

**输入：**

```
用 text-transform-capability 这个 skill，把 "hello world" 转成大写
```

**预期效果：**

- agent 调用 `capability_call(skill_name="text-transform-capability",
  request={...})` 工具（不会调用 `skill_activate`——`category_summary`
  里已经告诉过它这类 skill 不需要激活）。
- 工具返回类似：

```json
{
  "status": "success",
  "data": { "result": { "text": "HELLO WORLD" } },
  "error": null,
  "member_id": "upper",
  "resolve_reason": "keyword_match"
}
```

- agent 的最终回复里应该明确给出 **`HELLO WORLD`** 这个结果。

**这一步验证了什么**：请求文本里包含 `upper` 相关关键词，触发的是引擎的
**第一级免 LLM 确定性匹配**（`resolve_reason: keyword_match`），命中已有
`upper` member 并真实执行成功——这是全流程里成本最低、也是最常见的一条
路径。

---

## 步骤 3：确定性匹配命中 + 真实执行成功（反转）

**输入：**

```
再用 text-transform-capability 把 "abcdef" 反转一下
```

**预期效果：** agent 应回复反转结果 **`fedcba`**，`capability_call` 返回
`member_id: "reverse"`、`resolve_reason: "keyword_match"`。

---

## 步骤 4：语义相近但不含关键词 —— 验证第二级 LLM 裁决

**输入：**

```
用 text-transform-capability 帮我把这段话弄得更醒目一点："hello"
```

这句话故意不含 `upper`/`转大写`/`uppercase` 等关键词，第一级确定性匹配大概率
会 miss。

**预期效果：**

- `capability_call` 返回的 `resolve_reason` 应为 **`llm_match`**（而不是
  `keyword_match`）——说明引擎在第一级没命中后，真的发起了一次独立的、
  轻量的 LLM 调用去裁决"这句话该匹配哪个 member"，并选中了 `upper`（把
  文字变大写是让文字"更醒目"最直接的方式）。
- 结果应为成功，`data.result.text == "HELLO"`。

> 这一步的具体判断（选中 `upper` 还是判定完全不匹配）最终由真实模型的语义
> 理解决定，不是硬编码规则，因此结果可能因所用模型而略有差异。重点看
> `resolve_reason` 是否变成了 `llm_match`（证明第二级裁决确实被触发），而
> 不必纠结选中的具体是不是 `upper`。

**这一步验证了什么**：这是本次改造（阶段九）真正要验证的重点——第二级检索
裁决现在通过框架统一的 `LLMHelper` 发起，跟随当前 agent 正在用的
provider/model（如果你在会话里 `/model` 切换过 provider，这次裁决用的也是
切换后的那个，而不是写死的某个模型）。如果这一步的 `resolve_reason` 变成
`llm_match` 且结果正确，说明"检索裁决改接框架 LLM 调用基础设施"这件事在真实
对话链路里确实生效了，不只是单元测试里能过。

> 如果这一步 agent 没有触发工具调用、而是直接自己回答"HELLO"——说明它绕过了
> skill 机制自己算了答案，不算通过；请在提示里更明确地要求"必须调用
> text-transform-capability 这个 skill 来做"，再重新观察一次 `resolve_reason`。

---

## 步骤 5：诚实失败 —— 探索能力尚未接线时如实报告，不编造结果

**输入：**

```
用 text-transform-capability 帮我把这段文字变成喊叫的语气，末尾加感叹号："hi"
```

这是一个两个预置 member（`upper`/`reverse`）都覆盖不到的全新变换（对应方案
文档里"探索出 `shout` 变换"的场景）。

**预期效果：**

- `capability_call` 返回 `status: "not_implemented"`，并在 `note` 字段里
  说明"当前运行环境尚未接入真正的底层操作原语执行器（explore_runner/
  tool_executor）"。
- agent 的最终回复应该**如实告诉你它做不到**，说明原因是这个 skill 的
  "自动探索新变换"能力在当前环境里还没有真正接上执行器，而不是：
  - 假装成功、编造一个"HI!"之类的答案；也不是
  - 静默换一种方式（比如自己在对话里直接算出结果）糊弄过去。

**这一步验证了什么**：`generative-capability` 机制的一条核心设计原则是
"不自我认定成功"——命中已有能力就老老实实执行，做不到就老老实实说做不到，
永远不伪造数据。这一步专门验证的是"做不到"这条路径在真实 agent 对话里
也遵守这个原则，而不只是引擎内部代码层面的约定。

---

## 步骤 6（可选）：缺参数导致的执行失败

**输入：**

```
用 text-transform-capability 把这段文字转大写（不用给具体文字，就测试一下缺内容会怎样）
```

**预期效果**：如果 agent 真的用一个缺 `content.text` 的 `request` 去调用
工具，`capability_call` 会返回 `status: "not_implemented"`（`execute()`
因 schema 校验失败而失败，进而尝试触发探索，同样因未接入探索能力而如实
返回 `not_implemented`）。这一步不是必测项——agent 很可能会先反问你要转换
的文字是什么，而不会真的拿空内容去调用工具，属于正常且更好的行为；只有当
你确实想验证"schema 校验能拦住残缺请求"这条路径时才需要坚持发出这个模糊
指令。

---

## 小结：本指南覆盖的机制点对照表

| 步骤 | 覆盖的机制点 | 对应方案文档章节 |
|---|---|---|
| 1 | `skill_list` 对 generative-capability skill 的特殊呈现（只给摘要，不泄漏 member 清单） | 第 2 节 Skill 类型声明 |
| 2/3 | 第一级确定性 keyword 匹配 + member 执行 + intent_schema 校验通过 | 第 6 节 resolve/execute |
| 4 | 第二级 LLM 裁决 fallback（阶段九：改接框架 `LLMHelper`，跟随 `/model` 切换） | 第 6 节、阶段二、阶段九 |
| 5 | 探索能力未接线时如实返回 `not_implemented`，不伪造成功 | 第 1 节原则 4、阶段七"已知遗留" |
| 6（可选）| intent_schema 校验拦截缺参数请求 | 第 6 节 execute |

如果步骤 1–5 都符合预期（尤其是步骤 4 的 `resolve_reason` 确实变成
`llm_match`、步骤 5 确实得到诚实的失败说明而不是编造结果），说明
`generative-capability` 机制在真实 agent 对话场景里是可行的：agent 能
正确发现、调用这类 skill，检索裁决走的是框架统一的 LLM 调用基础设施，
且"命中执行成功 / 命中但失败 / 未命中需要探索"三种结果都被如实呈现，
没有被伪造成一个看起来还行的假答案。

---

## 附：无法进行真人对话测试时，如何验证同样的调用链路

如果暂时没有可用的 LLM/API key 做真人对话测试，可以用下面的脚本直接调用
**真实的 `capability_call` 工具函数本身**（不是绕开它直接调引擎）——这就是
agent 在对话里会执行的同一段代码，只是用一个固定行为的假 `LLMHelper` 代替
真实模型，因此可以在离线环境里验证"工具注册、`skill_list`/`capability_call`
接线、`get_current_llm_helper()` 取值"这些集成点是否正常，但不能验证"模型
会不会主动选择调用这个工具"这一层（这一层只有真人对话或真实模型驱动的测试
才能验证，是本指南步骤 1-6 存在的原因）：

```python
import sys, json, shutil, tempfile
from pathlib import Path
sys.path.insert(0, "src")

from mini_agent.skills import SkillLoader
from mini_agent.tools import ToolRegistry
from mini_agent.tools.capability_call import register_capability_tools
from mini_agent.tools.orchestration import set_current_llm_helper_provider

tmp = Path(tempfile.mkdtemp())
skills_root = tmp / "skills"
skills_root.mkdir()
shutil.copytree(".claude/skills/text-transform-capability", skills_root / "text-transform-capability")

loader = SkillLoader([skills_root])
registry = ToolRegistry()
register_capability_tools(registry, loader)
tool = registry.get("capability_call")

# 模拟"当前有一个正在跑的 Agent 实例"（真实场景由 Agent.__init__ 自动注册）
class FakeLLMHelper:
    def ask(self, prompt, **kwargs):
        return json.dumps({"member_ids": []})
    def chat(self, *a, **k):
        raise AssertionError("不应该走到探索循环")

set_current_llm_helper_provider(lambda: FakeLLMHelper())

result = json.loads(tool.fn(skill_name="text-transform-capability", request={
    "text": "把 hello world 转成大写", "target": {"op": "upper"}, "content": {"text": "hello world"},
}))
print(json.dumps(result, ensure_ascii=False, indent=2))
# 预期: {"status": "success", "data": {"result": {"text": "HELLO WORLD"}}, ...}
```

这段脚本已经用真实的 `.claude/skills/text-transform-capability` 目录副本
实测通过（见方案文档阶段九验证记录），可以作为"真人对话测试跑不通时"的
兜底排查手段：如果连这一层都失败，说明问题出在工具接线本身；如果这一层
正常但真人对话测试没有触发工具调用，说明问题在于模型没有被正确引导去使用
这个工具，而不是机制本身坏了。
