# browser-core 无头浏览器接入指南

面向对象：下一个有条件在真实浏览器环境（能安装 Chromium/Playwright、能
开端口跑 CDP）里工作的实现者。本文档回答"照着 SKILL.md 的契约，具体应该
怎么把真实浏览器接进来"。

沙盒环境（当前完成本文档的环境）没有可用的浏览器/网络出口去安装、启动、
连接一个真实浏览器实例，因此本文档只写到"怎么接、接哪里、有哪些坑"，
不包含跑通后的真实调用记录——这是诚实的边界，不是遗漏。

## 0. 起点：不要从零写，先看两个已有实现

项目里已经有两套可以参考/直接复用的浏览器自动化代码，接入 `browser-core`
时应该优先复用而不是重写：

1. `.claude/skills/browser-cdp/src/core/cdp_client.py` +
   `browser_manager.py` +（可选）`playwright_session.py`——CDP 连接池、
   浏览器进程生命周期管理、反检测（`stealth.py`/`anti_detection.py`）都
   已经实现过一遍，`browser-core` 的连接管理层直接复用这些模块，只是
   在其上包一层符合 `SKILL.md` 契约的窄接口，不要重新发明连接池。
2. `.claude/skills/browser-cdp/src/core/browser_nav.py` /
   `browser_interaction.py` / `browser_extract.py` / `browser_screenshot.py`
   ——这几个文件里已经有跟 `SKILL.md` 里 7 个工具几乎一一对应的函数
   （`navigate`/`click`/`type_text`/`scroll`/`extract_content`/
   `screenshot_annotated` 等，具体函数名以文件内容为准），大概率是"改
   参数名对齐契约 + 包一层返回值格式"的工作量，而不是从零实现。

## 1. 落点：一个新文件，不改调用方代码

```
src/mini_agent/skills/generative_capability/browser_core_impl.py
```

参照 `real_tools.py` 里 `text_transform_apply` 的写法：每个函数签名
`(tool_input: dict) -> dict`，内部做真实浏览器操作，任何异常都要在函数
内部捕获转成 `{"error": "..."}` 返回，不向上抛——探索循环依赖这个约定
把失败信息喂回模型，而不是让整个探索因为一次异常直接崩溃。

伪代码骨架（说明结构，不是可直接运行的代码）：

```python
from .browser-cdp适配层 import get_or_create_session  # 具体路径按实际抽取结果调整

def browser_navigate(tool_input: dict) -> dict:
    url = tool_input.get("url")
    if not url:
        return {"ok": False, "error": "缺少 url 参数"}
    session = get_or_create_session()
    try:
        result = session.navigate(url)  # 复用 browser-cdp 里已有的导航逻辑
        return {"ok": True, "final_url": result.url, "title": result.title}
    except Exception as e:
        return {"ok": False, "error": f"导航失败: {e}"}

# browser_click / browser_type / browser_scroll /
# browser_wait_for_selector / browser_extract_content /
# browser_screenshot_annotated 按同样的模式实现，
# 输入输出结构严格对齐 SKILL.md 的契约表格。
```

## 2. 会话生命周期：谁来开/关浏览器

这是接入时最容易踩坑的一块，SKILL.md 的契约表格里没有覆盖，因为它属于
"实现细节"而非"工具契约"：

- **每次探索调用是否复用同一个浏览器进程？** 建议是——在
  `browser_core_impl.py` 模块级维护一个惰性初始化的会话（类似
  `tool_runtime.py` 用模块级变量存 `_tool_executor` 的做法），第一次调用
  任意 `browser_*` 工具时才真正启动浏览器，避免探索请求命中已有 member
  执行成功的场景（根本不需要浏览器）也白白启动一次。
- **一次探索结束后要不要关闭？** 建议由 `capability_call.py` 或
  `CapabilityEngine.call()` 的调用方在探索结束后显式清理（可以加一个
  `browser_core_impl.close_session()`），不要依赖 Python 垃圾回收——浏览器
  子进程不清理会在长时间运行的 agent 进程里越攒越多。
- **并发探索怎么办？** 当前引擎设计里探索是同步阻塞的单次调用
  （`explorer_runtime.py` 的决策循环是一个 while 循环，不是并发任务），
  如果宿主 agent 框架本身支持并发跑多个 `capability_call`，需要考虑要
  给每次探索分配独立的浏览器上下文（Playwright 的 `BrowserContext`）而
  不是共享同一个页面，否则不同探索之间会互相踩踏对方的页面状态。

## 3. `browser_extract_content` 是关键中的关键

再强调一遍 SKILL.md 里已经写的一点：探索链路的蒸馏产物很大程度上依赖
"最后一次工具调用（通常就是 `browser_extract_content`）的返回值里直接带
`data`"这条既有约定（见 `distiller.py` 与
`capability.yaml::distill.trust_trace_data` 的说明）。这意味着：

- `browser_extract_content` 的实现质量直接决定蒸馏产物是否可用——如果
  提取逻辑经常拿到空结果或结构对不上 `intent_schema`，即使浏览器操作
  部分全部正确，`distill()` 的沙箱自测也会失败，蒸馏产物不会落盘。
- 建议 `schema_hint`（也就是调用方传入的 `intent_schema`）被真正用起来：
  比如 `intent_schema_template` 要求 `results` 是数组，提取逻辑就应该
  尝试找页面里的列表型结构（如重复出现的卡片/列表项），而不是无差别地
  把整页文本转成一个字符串塞进某个字段——`schema_validator.py` 会严格
  校验类型，糊弄不过去。

## 4. 反检测与验证码：明确不做，不要在这里加

`SKILL.md` 已经说明本契约不提供"绕过反爬/验证码"类工具。接入真实实现时
如果发现某个网站有明显的反爬拦截或验证码墙，正确的做法是让
`browser_navigate`/`browser_extract_content` 如实返回
`{"ok": false, "error": "遇到验证码/登录墙: ..."}`，交给探索子agent按
`explorer/prompt.md` 的既有要求调用 `report_failure`，**不要**在
`browser_core_impl.py` 里悄悄接入 `browser-cdp/src/core/
captcha_handler.py` 或 `cloudflare_bypass.py` 之类的模块去自动绕过——
这类能力即使项目里已经有现成代码，是否应该在"自动探索、无人值守"的场景
下使用也是一个需要单独评估的产品/合规决策，不应该在这次"接入通用浏览器
操作原语"的改动里顺带打开。

## 5. 完成后如何自测（复用已验证过的模式）

参照 `tests/test_generative_capability_real_tools.py`（`text-core` 的
对应测试）与 `test_cases/text-transform-capability-testing-guide.md`
（对话式测试指南）的结构，为 `browser-core` 补两类测试：

1. **纯逻辑/mock 级单测**：不需要真实浏览器，mock 掉底层会话对象，验证
   每个函数在正常/异常输入下的返回值结构符合契约。可以在没有真实浏览器
   的环境（比如当前沙箱）里先写、先跑通这一层。
2. **真实浏览器端到端测试**：需要真实浏览器环境，建议先用一个稳定的
   静态测试页面（不依赖外部网络、不易变化的本地 HTML 或
   `browser-cdp/config/websites/example.com.json` 对应的场景）跑通
   `browser_navigate -> browser_extract_content` 最短链路，再逐步扩展到
   `browser-site-scraper` 的真实探索场景（比如复现阶段十二对
   `text-transform-capability` 做过的"用真实 LLM 决策循环 + 真实工具
   执行器跑通 miss -> explore -> distill -> 落盘 -> 免探索复用"全链路）。

## 6. 完成后需要同步更新的文档（清单）

- `browser-core/SKILL.md`"已知限制"一节——去掉"仍会诚实失败"的描述。
- `browser-site-scraper/explorer/tool_allowlist.json` 的 `note` 字段。
- `browser-site-scraper/SKILL.md`"已知限制"一节。
- `next_doc/generative-capability-skill-plan.md`——按既有格式新增一个
  实施阶段记录（目标/改动文件/验证结果/已知遗留），不要直接覆盖或删除
  之前阶段的记录。
