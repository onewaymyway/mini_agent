---
name: browser-site-scraper
skill_type: generative-capability
description: 针对具体网站的网页抓取能力集合（百度/知乎/淘宝/京东等）。不在主 context 中展开具体网站列表，按需通过 capability_engine 检索并加载对应 member；未命中或已有 member 失效时触发探索（阶段三接入）自动补全并沉淀为新 member。
triggers: 抓取某个网站, 网站搜索, 网页数据提取, site scraper
platforms: windows, macos, linux, pc
---

# browser-site-scraper（generative-capability skill）

本 skill 不通过静态加载 references 的方式暴露内容，而是通过通用引擎
`.claude/skills/_engine/capability_engine.py` 按需检索、执行、（后续阶段）探索与固化。

## 调用方式

```python
from capability_engine import CapabilityEngine

engine = CapabilityEngine("path/to/browser-site-scraper")
result = engine.call({
    "text": "帮我用百度搜一下 xxx",
    "target": {"url": "https://www.baidu.com/s?wd=xxx"},
    "query": "xxx",
})
```

`result.status` 为 `success` / `fail` / `not_implemented`（探索未接入时的未命中场景）。

## 依赖

- 底层通用浏览器操作能力: `browser-core`（本次拆分自原 `browser-cdp`，尚待独立拆分，
  当前阶段 member 直接复用 `browser-cdp/src/searchers/*`，见 `capability.yaml`）。

## 当前阶段说明

本 skill 处于方案实施 **阶段三**：在阶段一（resolve/execute 两步骨架）、阶段二
（LLM 二级检索裁决）基础上，已接入探索子agent与蒸馏固化：

- `_engine/explorer_runtime.py`：探索子agent的决策循环。仅接受
  `request/intent_schema/explorer_config` 三样输入（不携带主对话历史），
  只能调用 `capability.yaml -> explorer.tool_allowlist` 中声明的工具，
  越权调用会被引擎拒绝并作为失败结果反馈给模型；受 `max_steps`/`max_seconds`
  硬预算约束，超出直接判定失败。模型通过调用内置的 `finish` 工具提交最终
  数据，或调用 `report_failure` 如实报告失败原因（如遇到验证码/登录墙）。
- `_engine/distiller.py`：把探索得到的动作序列蒸馏为参数化脚本（把
  `target.url`/`query` 等与本次请求相同的值替换为占位符，而不是原样保存
  trace），蒸馏产物必须先在沙箱内自测（重新跑一遍 `run()` 并用
  `intent_schema` 再校验一次），自测通过才允许原子化落盘
  （`members/`、`registry.json`、`_index.json` 一起更新），不通过则丢弃、
  不污染检索池。
- `_engine/tool_runtime.py`：蒸馏脚本运行时的工具执行器注入点，蒸馏脚本
  本身不实现任何具体浏览器控制逻辑，只保存"调用哪个工具、传什么参数"。
- 生命周期状态机的 `degraded -> 重新探索 -> trusted(probation)/dead` 闭环
  已在 `CapabilityEngine.call()` 中打通：命中的候选全部执行失败且该 member
  已处于 `degraded` 时，会触发针对同一个 member_id 的重新探索；探索+蒸馏
  成功则原地升版本号回到 `probation`，失败则按 `capability.yaml` 的
  `dead_after_reexplore_fail` 标记为 `dead`。

探索能力默认不启用（未注入 `explore_runner`/`tool_executor` 时 `explore()`
会明确返回未注入的错误信息，不会伪造成功），需要宿主 agent 框架接入真实的
`browser-core` 工具执行器后才能在生产环境生效；`capability_engine.py` 提供
了 `--stub-explore-success` / `--stub-explore-fail` 命令行参数用于离线验证
接线逻辑。

详见 `next_doc/generative-capability-skill-plan.md`。
