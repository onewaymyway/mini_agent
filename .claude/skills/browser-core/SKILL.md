---
name: browser-core
description: browser-site-scraper 在 capability.yaml 中把本 skill 声明为 explorer.base_tools；本 skill 本身不由探索子agent直接读取正文（探索子agent只读取 explorer/tool_allowlist.json 里的工具名与签名），SKILL.md 的作用是给"实现/维护这些原语的人"一份权威契约文档，静态 skill（skill_type 留空，遵循项目既有约定），不参与 generative-capability 的检索流程。
triggers: browser-core, 浏览器操作原语, 无头浏览器接入
platforms: windows, macos, linux, pc
---

# browser-core（静态 skill：通用浏览器操作原语契约）

## 这是什么

`browser-core` 是 `next_doc/generative-capability-skill-plan.md` 第 10 节
"迁移路径"第 1 步里提到、但至今仍是**占位声明**的静态 skill：把
`browser-site-scraper` 探索子agent会用到的通用浏览器操作（导航/点击/输入/
滚动/提取/等待/截图）从"每个网站各自实现"里抽出来，定义成一份**跨网站
通用**的工具契约。

本次改动（阶段十三）把它从"完全空白的占位"推进为"契约已经写清楚、真实实现
仍缺"的状态：

- ✅ 已完成：本文档 —— 每个工具的输入/输出结构、错误约定、与
  `browser-site-scraper/explorer/tool_allowlist.json` 的对应关系。
- ✅ 已完成：`browser-core/HEADLESS_BROWSER_INTEGRATION.md` —— 给未来
  "真的要接一个无头浏览器"的实现者一份可以直接照做的接入指南。
- ❌ 仍未完成、不在本次范围内：真正可执行的实现（用 Playwright/CDP 之类
  驱动一个真实浏览器进程）。这需要在能够安装/运行浏览器的环境里完成，
  当前沙箱环境不满足这个前提，勉强接入只会得到一个连不上真实浏览器、
  看似实现了但实际仍会诚实失败的空壳，价值不大，所以本次只把"契约"和
  "怎么接"两件事做实，把"真的去接"清楚地留给下一个有真实浏览器环境的
  阶段。

## 为什么现在只做"契约 + 指南"两件事，而不是照抄 browser-cdp

`.claude/skills/browser-cdp/` 目录下已经有一套相当完整的浏览器自动化实现
（`src/core/browser_*.py`、`cdp_client.py`、`playwright_session.py` 等），
理论上可以直接抽取一部分函数当作 `browser-core` 的真实实现。但这次没有
这么做，原因是：

1. **沙箱环境本身没有可用的浏览器/CDP 端口**——即使把代码抽过来，
   `real_tools.py::build_default_tool_executor()` 一调用就会因为连不上
   真实浏览器而返回失败，与"完全没实现、如实报错"在效果上没有区别，却会
   让读代码的人误以为"browser-core 已经接好了"，这正是
   `text-transform-capability` 阶段十二实施记录里反复强调要避免的
   "看似实现完备的误解"。
2. **`browser-cdp` 里的实现是针对具体网站高度定制的**（每个 `*_search.py`
   都有自己的选择器、反爬处理），而 `browser-core` 需要的是与网站无关的
   **通用原语**（"点击一个元素""等一个选择器出现"），两者粒度不同，直接
   抽取容易把网站特定逻辑也带进来，需要认真设计接口边界，不是简单的
   "复制粘贴+改函数名"能做好的，属于本次范围之外的工作量。
3. **`text-core` 已经验证过"先把契约和接线方式定清楚，实现单独放一个
   模块，接入时只加一份实现、不改调用方"这套模式是可行的**
   （见 `real_tools.py`）。本 skill 遵循同样的模式：先把契约钉死，真实
   实现留出一个清晰的插槽（`browser_core_impl.py`，见下方"未来实现落点"），
   下一个阶段接入时不需要改 `capability_call.py`/`capability_engine.py`
   一行代码。

## 工具契约（探索子agent可调用的全部原语）

以下 7 个工具名与 `browser-site-scraper/explorer/tool_allowlist.json` 中
`allowed_tools` 完全一致，是探索子agent（`explorer_runtime.build_llm_explorer`）
真正能看到、能调用的工具集合。每个工具的 `input`/`output` 结构是
**契约**——无论未来用 Playwright、Selenium 还是别的什么驱动真实浏览器，
`tool_executor(tool_name, tool_input) -> dict` 的行为都必须符合这份契约，
`capability_engine.py`/`distiller.py`/探索子agent的 prompt 才不需要跟着改。

### `browser_navigate`

打开一个 URL 并等待页面基本加载完成。

```
input:  {"url": "https://example.com/search?q=xxx"}
output: {"ok": true, "final_url": "...", "title": "..."}
      | {"ok": false, "error": "导航失败的具体原因（超时/DNS失败/证书错误等）"}
```

### `browser_click`

点击页面上的一个元素。

```
input:  {"selector": "css选择器或语义化描述", "index": 0}
        # index 可选，多个匹配元素时选第几个，默认 0
output: {"ok": true} | {"ok": false, "error": "元素未找到/不可点击/被遮挡等"}
```

### `browser_type`

向一个输入框输入文本（不含回车，回车是否触发由 `submit` 参数控制）。

```
input:  {"selector": "...", "text": "...", "submit": false}
output: {"ok": true} | {"ok": false, "error": "..."}
```

### `browser_scroll`

滚动页面或某个可滚动容器，用于触发懒加载/无限滚动。

```
input:  {"selector": null, "direction": "down", "amount": "page"}
        # selector 为 null 表示滚动整个页面；amount 支持 "page"（一屏）
        # 或具体像素数
output: {"ok": true, "reached_bottom": false}
```

### `browser_wait_for_selector`

等待某个选择器出现/消失，用于处理异步渲染内容。

```
input:  {"selector": "...", "state": "visible", "timeout_ms": 8000}
        # state: visible | hidden | attached | detached
output: {"ok": true} | {"ok": false, "error": "超时未等到目标状态"}
```

### `browser_extract_content`

从当前页面提取结构化数据——这是 `browser-site-scraper` 探索链路里最关键
的一步：**最后一次调用本工具的返回值通常直接构成蒸馏产物的 `data` 字段**
（见 `distiller.py` 关于"重放最后一步工具输出"的既有约定，以及
`capability.yaml` 里 `distill.trust_trace_data` 兜底开关的适用场景）。

```
input:  {"schema_hint": {...}, "selector": null}
        # schema_hint 通常直接传 intent_schema，帮助提取逻辑知道要什么结构；
        # selector 可选，限定只从某个容器内提取
output: {"ok": true, "data": {"results": [...]}}
      | {"ok": false, "error": "页面结构与预期不符/内容为空等"}
```

### `browser_screenshot_annotated`

截图并标注可交互元素编号，供探索子agent在纯 DOM 信息不够用时"看"页面。

```
input:  {"full_page": false}
output: {"ok": true, "image_ref": "供后续多模态消息引用的图片句柄/路径",
         "elements": [{"index": 1, "selector": "...", "role": "button", "text": "..."}]}
      | {"ok": false, "error": "截图失败原因"}
```

## 反爬/登录墙的处理原则（沿用 explorer/prompt.md 的既有约束）

`browser-site-scraper/explorer/prompt.md` 已明确要求探索子agent"遇到
验证码/登录墙/明显反爬拦截时，如实报告拦截类型，不尝试绕过认证机制"。
本契约不新增任何"反检测"类工具（不提供 `browser_bypass_captcha` 之类的
原语），这是刻意的：`browser-core` 的职责边界是"通用浏览器操作"，不是
"绕过网站的访问控制"，与方案文档第 8 节"安全与成本边界"的精神一致。

## 未来实现落点（供下一个阶段接入时参照）

按照 `real_tools.py` 已经验证过的模式，真实实现应该：

1. 新增 `src/mini_agent/skills/generative_capability/browser_core_impl.py`，
   内部维护一个真实浏览器会话（建议直接复用 `browser-cdp/src/core/
   cdp_client.py` 或 `playwright_session.py` 里已经写好的连接/生命周期
   管理代码，而不是重新发明），对外暴露 7 个与上表一一对应的函数。
2. 在 `real_tools.py::REAL_TOOL_IMPLEMENTATIONS` 里补上这 7 个工具名到
   对应函数的映射（`build_default_tool_executor()` 本身不需要改一行）。
3. 把 `browser-site-scraper/explorer/tool_allowlist.json` 里的
   `"note"` 字段从"仍是占位声明"更新为"已接入真实执行器"（参照
   `text-transform-capability` 阶段十二的改法）。
4. 详细步骤、依赖选型建议、常见坑，见同目录下的
   `HEADLESS_BROWSER_INTEGRATION.md`。

## 已知限制

- 本 skill 当前只提供契约文档，不提供任何可执行代码；`tool_executor` 命中
  上述 7 个工具名时，`real_tools.py::build_default_tool_executor()` 仍会
  如实返回"该工具仍是占位声明，尚未接入真实执行器"，不会伪造成功。
- 依赖本契约的 `browser-site-scraper` 因此探索路径也仍会诚实失败，见
  `browser-site-scraper/SKILL.md`"已知限制"一节。
