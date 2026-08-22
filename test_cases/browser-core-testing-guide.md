# browser-core 测试指南（在 agent 里对话验证）

> 对应 `.claude/skills/browser-core/`（`browser-site-scraper` 依赖的通用
> 浏览器操作原语静态 skill，阶段十四起提供真实实现，见
> `.claude/skills/browser-core/impl/` 与 `HEADLESS_BROWSER_INTEGRATION.md`）。
>
> 目的：验证 `browser-core` 的真实实现**在真实 agent 对话场景里可用**——
> 不是走代码直接调用 `real_tools.py`/`session_manager.py`（那部分回归
> 覆盖见 `tests/test_generative_capability_real_tools.py::
> TestSkillLocalToolImplementationLoading`），而是像真实用户一样在
> mini-agent 的对话界面里发起一次抓取请求，观察 `browser-site-scraper`
> 探索子agent是否会：
> 1. 命中已有 member（baidu/zhihu 等）时正常执行，不经过 `browser-core`；
> 2. 命中不了、需要探索新站点时，真的调用 `browser_navigate`/
>    `browser_click`/`browser_extract_content` 等工具，且这些工具**真的
>    执行**（不再是阶段十三时"占位声明，未接入真实执行器"的诚实拒绝）；
> 3. 在没有可用浏览器的环境下，得到的是**浏览器层面的诚实失败**（连接
>    被拒/找不到 Chrome 等具体错误），而不是伪造成功，也不是笼统的
>    "未实现"；
> 4. 在需要登录的场景下，能正确使用 `attach` 会话模式连接一个使用者提前
>    手动登录好的浏览器（这是本次改动要验证的核心诉求）。
>
> 与 `text-transform-capability-testing-guide.md` 的关系：那份指南验证的
> 是 `generative-capability` 引擎骨架本身（resolve/execute/探索/蒸馏
> 全链路）在真实对话里可行；本指南假定骨架已经验证过，专注验证
> **`browser-core` 这一个具体的静态 skill 实现**是否真的接上了、真的能
> 驱动一个浏览器。两份指南可以独立使用，不需要按顺序执行。

---

## 前置条件

1. 已经能正常启动 mini-agent CLI 并进行对话，且配置了可用的 LLM
   provider。
2. `.claude/skills/browser-site-scraper/` 与 `.claude/skills/browser-core/`
   在当前项目目录下存在（随仓库自带）。
3. **额外依赖**（`text-transform-capability` 不需要，`browser-core`
   需要）：
   ```bash
   pip install websocket-client --break-system-packages
   ```
   （`requests`/`PyYAML` 项目本身已依赖，不用额外装。）
4. 按你想验证的场景，准备好下列**其中一种**环境（对应下面步骤 3/4 会分别
   用到）：
   - **场景 A（不需要登录的纯抓取）**：本机/服务器装好任意一个
     Chrome/Chromium/Edge 即可，不需要手动做任何事——`browser-core` 会在
     `auto` 模式下自己拉起一个 headless 实例。
   - **场景 B（需要登录）**：手动执行
     ```bash
     google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/browser-core-manual
     ```
     打开一个有界面的 Chrome，手动登录目标网站，**先不要关掉这个窗口**，
     再开始下面的对话测试。
   - **场景 C（沙盒/CI，完全没有可用浏览器）**：不需要准备任何东西，本指南
     步骤 5 专门验证这种环境下的诚实失败行为。

> 三种环境对应的是**同一份代码**的三条不同路径，不需要都测；如果你只想
> 验证"接线是否正确"，跑步骤 2 + 5 即可（不依赖任何浏览器）；如果本机真的
> 有 Chrome，再补跑步骤 3/4。

---

## 步骤 1：确认 agent 能发现 browser-site-scraper 这个 skill

**输入：**

```
看一下项目里有哪些 generative-capability 类型的 skill，browser-site-scraper 是做什么的？
```

**预期效果：**

- agent 调用 `skill_list`，回复里提到 `browser-site-scraper`，并用
  `category_summary` 描述"抓取指定网站内容"这类能力，不会把
  `browser-core`/内部 member 实现细节泄漏进主 context。

这一步复用 `text-transform-capability-testing-guide.md` 步骤 1 验证过的
同一条机制（skill 摘要按需检索），此处不重复展开，主要用来确认环境正常。

---

## 步骤 2：确定性匹配命中已有 member —— 不经过 browser-core

**输入：**

```
用 browser-site-scraper 帮我抓一下百度搜索 "test" 的结果，target.url 用 https://www.baidu.com/s?wd=test
```

**预期效果：**

- `target.url` 命中已有 `baidu` member 的 domain_matcher，`capability_call`
  走 `resolve` -> `execute`，**不会**进入探索、**不会**调用任何
  `browser_*` 工具。
- 若本机确实有可用浏览器/网络，`baidu` member 内部复用的是
  `browser-cdp/src/searchers/*`（与本次改动无关的既有代码路径），返回
  抓取结果；若没有可用浏览器，`baidu` member 执行失败，`capability_call`
  会因为"命中但执行失败"触发**探索**兜底——此时会用到 `browser-core`，
  直接跳到步骤 5 观察即可。

**这一步验证了什么**：确认"命中已有能力时走确定性路径，不浪费探索预算"
这条既有行为没有被本次改动影响——`browser-core` 只在探索路径上被调用。

---

## 步骤 3（需要场景 A：本机有可用浏览器）：探索新站点，真实驱动浏览器

**输入：**

```
用 browser-site-scraper 帮我抓一下 example.com 首页的标题和正文，target.url 用 https://example.com/
```

`example.com` 不在任何已有 member 的 domain_matcher 里，会触发探索。

**预期效果：**

- `capability_call` 进入 `explore()`，探索子agent依次尝试
  `browser_navigate` -> （可能 `browser_wait_for_selector`） ->
  `browser_extract_content`。
- 如果这台机器确实有可用的 Chrome/Chromium，`browser_navigate` 会返回
  `{"ok": true, "final_url": "https://example.com/", "title": "Example
  Domain"}`，`browser_extract_content` 会返回包含
  `"text_excerpt"` 字段、内容里能看到 example.com 页面文案的结构。
- 探索成功后应触发**蒸馏**，agent 的最终回复里应该给出正确提取到的标题/
  正文内容，`capability_call` 返回 `status: "success"`，且注意观察日志/
  返回里 `member_id` 是否是一个新生成的 member（而不是 `baidu`/`zhihu`
  这三个已有的之一）——说明蒸馏产物真的落盘了。
- 可选：**在同一个会话里再问一次一样的问题**，这次应该直接命中刚落盘的
  新 member、不再重新探索（`resolve_reason` 应为 domain 匹配，执行速度
  明显更快）——验证"探索一次、之后免探索复用"这条链路在 `browser-core`
  接入真实实现后依然成立。

**这一步验证了什么**：这是本次改动最核心的验证目标——`browser-core` 的
7 个工具在真实对话触发的探索循环里，是**真的**在驱动一个浏览器进程完成
导航和内容提取，不再是"占位声明，未接入真实执行器"的诚实拒绝。

> 如果这一步 `browser_navigate` 返回的 `error` 里提到"未找到可用的
> Chrome/Chromium/Edge 可执行文件"，说明这台机器本身没有装浏览器，不是
> 代码问题——回到前置条件准备场景 A，或者跳到步骤 5 把这当成预期中的
> 诚实失败来验证。

---

## 步骤 4（需要场景 B：手动准备好一个已登录的浏览器）：`attach` 模式验证登录场景

这一步验证**本次改动的核心诉求**：不应局限于无头浏览器，需要登录的网站
应该由使用者手动登录好、再交给探索子agent。

**准备**：确认按前置条件场景 B 手动启动了一个带
`--remote-debugging-port=9222` 的有界面 Chrome，并已经手动登录好你打算
测试的网站（选一个你自己有账号、且内容确实需要登录才能看到的网站）。

**输入：**（以某个需要登录的站点为例，把 URL 换成你实际登录了的那个）

```
用 browser-site-scraper 帮我抓一下 <你登录了的网站 URL> 上的内容，
调用 browser 工具时用 session.mode="attach"、session.port=9222 连接我已经手动打开并登录好的浏览器
```

> 当前 `explorer/prompt.md` 还没有专门教探索子agent"什么时候该主动选择
> `attach` 模式"（这是留给后续阶段的优化点，见下方"已知限制"），所以现阶段
> 需要**在对话里明确指出**要用 `attach` 模式 + 具体端口，模型才会把
> `session: {"mode": "attach", "port": 9222}` 放进工具调用的 `input`
> 里。如果不明确指出，探索子agent大概率会走默认的 `auto` 模式，自己拉起
> 一个全新的、未登录的浏览器，抓到的会是登录墙页面而不是真实内容。

**预期效果：**

- `browser_navigate` 等工具调用的 `input` 里应该带上你指定的
  `session.mode="attach"`、`session.port=9222`。
- 由于连接的是你手动登录好的浏览器会话，`browser_extract_content` 应该
  能拿到登录后才可见的真实内容，而不是登录页/验证码页的内容。
- 探索子agent**不会**尝试自己填写用户名密码、不会尝试点击"登录"按钮走
  完整登录流程——它只是复用了一个已经处于登录状态的会话。

**如果没有准备场景 B（跳过这一步的替代验证）**：可以只验证"`attach`
模式在没有对应端口监听时会得到具体、可操作的错误提示"这条边界行为——
输入：

```
用 browser-site-scraper 抓 https://example.com/，调用 browser 工具时用 session.mode="attach"、session.port=19222（这个端口上没有任何浏览器在监听）
```

预期 `browser_navigate` 返回类似：

```json
{"ok": false, "error": "mode='attach' 但 127.0.0.1:19222 上没有可连接的浏览器调试端口。请先手动启动一个带 --remote-debugging-port=19222 的浏览器（如果这个抓取目标需要登录，应该在这一步手动登录好），再重试。"}
```

agent 应该把这条具体的错误原因转述给你（而不是笼统地说"失败了"），并且
不会伪造抓取结果。

**这一步验证了什么**：`attach` 模式无论连接成功还是失败，都是一条边界
清晰、错误信息具体到"该怎么解决"的路径——这正是"把登录这件事从探索子agent
的职责里挪走，交给人工提前准备"这个设计目标在真实对话里的体现。

---

## 步骤 5（场景 C，也是最容易复现的一步）：完全没有可用浏览器时的诚实失败

不需要任何准备，在任何环境下都能跑（尤其适合当前这类沙盒/CI 环境）。

**输入：**

```
用 browser-site-scraper 帮我抓一下 https://a-site-that-is-definitely-not-in-any-existing-member.example/ 上的内容
```

（换成任何一个确定不在 `baidu`/`zhihu` 等已有 member domain_matcher 里
的 URL 即可，触发探索。）

**预期效果**（分两种情况，取决于这台机器有没有装浏览器）：

- **没有装任何 Chrome/Chromium/Edge**：`browser_navigate` 返回
  `{"ok": false, "error": "未找到可用的 Chrome/Chromium/Edge 可执行文件。
  请安装浏览器，或改用 attach 模式..."}`，探索子agent按
  `explorer/prompt.md` 的既有要求调用 `report_failure`，
  `capability_call` 最终返回 `status: "not_implemented"`（或对应的失败
  状态），agent 的回复应该**如实说明**是浏览器环境不可用，而不是假装
  抓到了内容、也不是笼统地说"这个功能还没做"（阶段十三时才是这个笼统
  状态，阶段十四之后应该是具体到"缺浏览器"这个原因）。
- **装了浏览器但目标 URL 本身无法访问**（DNS 解析失败等）：
  `browser_navigate` 返回 `{"ok": false, "error": "导航失败: ..."}`，
  同样应如实报告，不伪造。

**这一步验证了什么**：`browser-core` 接入真实实现之后，"失败"这件事的
**颗粒度变细了**——阶段十三时所有 `browser_*` 工具无论什么情况都返回同一
句"占位声明，未接入真实执行器"；阶段十四之后，失败原因会具体到"没装
浏览器"/"连不上调试端口"/"目标站点本身打不开"等，这是本次改动应该带来的
可观测的行为差异，也是判断"是不是真的接上了"最直接的信号——如果你看到的
错误信息还是"占位声明"字样，说明 `browser-core/impl/tools_impl.py` 没有
被正确加载（检查 `real_tools.py::build_default_tool_executor()` 是否
传入了 `skill_dir`，以及 `websocket-client` 是否已安装）。

---

## 小结：本指南覆盖的机制点对照表

| 步骤 | 覆盖的机制点 | 对应文档 |
|---|---|---|
| 1 | skill 发现（复用既有机制，仅确认环境正常） | `text-transform-capability-testing-guide.md` |
| 2 | 命中已有 member 时不经过 browser-core | `browser-site-scraper/SKILL.md` |
| 3 | 探索新站点，真实驱动浏览器完成导航+提取，探索成功后蒸馏落盘、下次免探索复用 | `browser-core/SKILL.md`、`HEADLESS_BROWSER_INTEGRATION.md` 第 6 节 |
| 4 | `attach` 会话模式——登录场景的核心验证，及其失败时的具体错误提示 | `browser-core/SKILL.md`"会话模式"一节 |
| 5 | 无可用浏览器时的诚实失败，且失败原因具体化（区别于阶段十三的笼统占位提示） | `next_doc/generative-capability-skill-plan.md` 阶段十四 |

如果步骤 2/5 符合预期（这两步不依赖任何浏览器，任何环境都能跑），说明
`browser-core` 的动态加载接线是正确的；如果本机确实有 Chrome 且步骤 3
也符合预期，说明真实的浏览器驱动链路本身是通的；如果额外验证了步骤 4，
说明本次改动的核心诉求（不局限于无头浏览器、支持登录场景）在真实对话里
也站得住。

---

## 已知限制（测试时请留意，不是 bug）

- `explorer/prompt.md` 目前**没有**教探索子agent"遇到登录墙应该主动建议
  使用者用 `attach` 模式重试"——它只会按既有要求如实报告"这里需要登录"
  并 `report_failure`，不会自己去猜测、也不会自动切换会话模式。步骤 4
  需要在对话里明确指出使用 `attach` 模式，这是当前版本的真实行为，不是
  测试步骤写错了。这一点可以作为后续阶段的一个优化方向记录下来。
- `browser_extract_content` 是通用提取（不针对具体网站定制选择器），
  结构复杂的页面可能拿不到完全符合 `intent_schema` 的数据，蒸馏可能因此
  失败或产出一个提取不够精确的 member——这是 `browser-core/SKILL.md`
  "已知限制"一节里记录过的预期行为，不代表接线本身有问题。
- 本指南在编写时所在的沙盒环境**没有**可安装/运行的 Chrome（详见与
  Anthropic 的对话记录：网络出口白名单不含 Chrome 下载源，且容器没有
  `snapd` 无法通过 snap 安装），因此步骤 3/4 的"预期效果"是基于代码
  逻辑推导、尚未在这个特定沙盒里实测通过；步骤 2/5 不依赖浏览器，已经
  可以在任何环境（含当前沙盒）复现。如果你在一个有真实浏览器的环境里
  跑完步骤 3/4，建议把结果补充进
  `.claude/skills/browser-core/HEADLESS_BROWSER_INTEGRATION.md` 第 5 节
  "验证记录"，让文档反映真实验证过的状态，而不是停留在推导层面。
