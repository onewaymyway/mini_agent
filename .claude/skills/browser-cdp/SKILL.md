---
name: browser-cdp
description: 通过 Chrome DevTools Protocol (CDP) 控制真实 Chrome/Edge 浏览器：打开网页、抓取网页内容（HTML/纯文本/表单/链接）、截图（含编号标注可交互元素）、模拟点击和输入、执行JS、读取console/网络日志，并支持与用户同时操作同一个浏览器（观察/建议/代劳三种协作模式）。当用户说"帮我打开网页"、"抓取这个网站"、"帮我填一下这个表单"、"看看我浏览器里这个页面"、"截个图分析一下"时使用。
triggers: 浏览器, 打开网页, 抓取网页, 网页截图, cdp, chrome devtools, 模拟点击, 模拟输入, 网页自动化, 填表单, browser automation, scrape webpage
platforms: windows, macos, linux, pc
resources:
  - id: python-env-detection
    path: references/python-env-detection.md
    description: python/python3 命令可用性检测的完整原因说明与自动检测脚本（本环境结论已在正文给出，一般无需展开，环境变了/检测结果对不上时再加载）
    triggers: python3, python 不是内部或外部命令, 找不到python, ModuleNotFoundError
  - id: browser-launch-scenarios
    path: references/browser-launch-scenarios.md
    description: 三种建立浏览器连接的场景完整说明（专用新实例/连接用户已登录窗口/无GUI headless），含启动失败清理策略细节
    triggers: 连接现有浏览器, 已登录的浏览器, remote-debugging-port, headless, 场景B, 场景C, 调试端口, 启动失败
  - id: workflows
    path: references/workflows.md
    description: 四类典型工作流完整示例——抓取内容、看图点击填表单、与用户协作三种介入程度、调试网页console/网络
    triggers: 表单填写, 看图操作, 协作模式, 观察模式, 代劳模式, 调试网页, console日志, 网络请求
  - id: troubleshooting
    path: references/troubleshooting.md
    description: 路径规则详解（skill 目录为 cwd 基准）与截图/DPR/SPA路由/元素编号失效等常见坑
    triggers: 找不到目录, 路径错误, DPR, SPA, 编号失效, temp_data
  - id: baidu-search
    path: references/baidu-search.md
    description: 百度搜索自动化脚本（baidu_search.py）完整文档：参数、输出格式、核心实现要点
    triggers: 百度搜索, baidu search, baidu_search.py
  - id: bing-search
    path: references/bing-search.md
    description: Bing 搜索自动化脚本（bing_search.py）完整文档：参数、输出格式、核心实现要点
    triggers: bing搜索, bing search, bing_search.py
  - id: zhihu-search
    path: references/zhihu-search.md
    description: 知乎内容搜索自动化脚本（zhihu_search.py），通过百度 site:zhihu.com 获取知乎问答和专栏
    triggers: 知乎搜索, zhihu search, zhihu_search.py
  - id: zhihu-hot
    path: references/zhihu-hot.md
    description: 知乎热榜抓取自动化脚本（zhihu_hot.py），支持免登录发现页和登录态热榜抓取
    triggers: 知乎热榜, zhihu hot, zhihu_hot.py
  - id: zhihu-column-search
    path: references/zhihu-column-search.md
    description: 知乎专栏文章批量搜索与抓取脚本（zhihu_column_search.py），通过百度 site:zhihu.com 搜索专栏文章并抓取详情
    triggers: 知乎专栏, zhihu column, zhihu_column_search.py
  - id: arxiv-search
    path: references/arxiv-search.md
    description: arXiv 论文搜索自动化脚本（arxiv_search.py），按关键词搜索最新论文列表和获取详情
    triggers: arxiv搜索, arxiv论文, arxiv_search.py
  - id: arxiv-multi-search
    path: references/arxiv-multi-search.md
    description: arXiv 多关键词批量搜索脚本（arxiv_multi_search.py），支持合并去重、批量获取详情
    triggers: arxiv多关键词, arxiv批量搜索, arxiv_multi_search.py
  - id: wechat-search
    path: references/wechat-search.md
    description: 微信公众号文章搜索自动化脚本（wechat_search.py），通过搜狗微信搜索获取公众号文章并抓取详情
    triggers: 微信搜索, 微信公众号, wechat search, wechat_search.py, 搜狗微信
---

# Browser CDP Skill

通过 CDP（Chrome DevTools Protocol）直接控制 Chrome 系浏览器，**不依赖 Playwright/Selenium**。
核心优势：可以连接**用户正在使用的、已登录的真实浏览器窗口**，与用户协同操作，而不是每次都起一个
干净的自动化浏览器丢失登录态。同时也支持在无 GUI 的服务器/沙盒环境里跑一个无头实例，专门做抓取。

脚本目录：`.claude/skills/browser-cdp/`

| 脚本 | 用途 |
|---|---|
| `cdp_client.py` | 底层库，其他脚本导入用，一般不直接调用 |
| `utils.py` | 底层库，公共辅助函数 |
| `browser_launch.py` | 确保/建立浏览器连接，管理 tab（列表/新建/关闭/激活） |
| `browser_nav.py` | 打开网址、前进后退刷新、等待元素出现 |
| `browser_extract.py` | 抓取内容：html/text/elements/forms/links/meta |
| `browser_screenshot.py` | 截图，支持整页/元素级/编号标注 |
| `browser_input.py` | 模拟点击、输入文字、按键、滚动、悬停 |
| `browser_console.py` | 执行任意 JS、抓取 console 日志、抓取网络请求 |
| `browser_watch.py` | 协作场景：轮询判断用户是否已完成某个操作（URL/标题变化） |
| `baidu_search.py` / `bing_search.py` | 搜索引擎自动化，见下方对应子资源 |
| `zhihu_search.py` / `zhihu_hot.py` | 知乎内容/热榜抓取，见下方对应子资源 |
| `zhihu_column_search.py` | 知乎专栏文章批量搜索与抓取，见下方对应子资源 |
| `arxiv_search.py` / `arxiv_multi_search.py` | arXiv 论文搜索，见下方对应子资源 |
| `wechat_search.py` | 微信公众号文章搜索（搜狗微信），见下方对应子资源 |

## 子资源（渐进式加载）

本 skill 遵循渐进式加载规范：正文只保留高频必读内容，长尾细节放在 `references/*.md`，
已在 frontmatter `resources` 中登记，激活本 skill 后可在资源清单里看到全部条目及加载状态。
命中对应 `triggers` 会自动加载；也可以不依赖关键词，主动调用
`skill_resource_load(skill_name="browser-cdp", resource_id="<id>", reason=...)` 按需加载：

| id | 内容 |
|---|---|
| `python-env-detection` | python/python3 命令检测的完整原因与自动检测脚本 |
| `browser-launch-scenarios` | 三种建立浏览器连接场景的完整细节（专用实例/连接已登录窗口/headless） |
| `workflows` | 抓取内容、看图填表单、协作模式、调试网页四类工作流的完整示例 |
| `troubleshooting` | 路径规则详解 + 截图/DPR/SPA/编号失效等常见坑 |
| `baidu-search` / `bing-search` | 对应搜索引擎自动化脚本完整文档 |
| `zhihu-search` / `zhihu-hot` | 知乎内容搜索 / 热榜抓取脚本完整文档 |
| `zhihu-column-search` | 知乎专栏文章批量搜索与抓取脚本完整文档 |
| `arxiv-search` / `arxiv-multi-search` | arXiv 单关键词 / 多关键词批量搜索脚本完整文档 |
| `wechat-search` | 微信公众号文章搜索自动化脚本完整文档 |

新增子功能脚本时：在 `.claude/skills/browser-cdp/references/` 下新建 `<name>.md`，并在本文件
frontmatter 的 `resources` 里登记 `id`/`path`/`description`/`triggers`——不登记就不会被加载机制发现。

## 运行前必做：Python 命令检测

**本环境同时存在 `python` 和 `python3`，只有一个可用。本环境结论：用 `python`（Anaconda），
`python3` 不可用（会弹出应用商店安装提示）。**

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
```

若换了新环境、命令报错，或需要检测脚本，加载 `python-env-detection` 子资源。

## 前置依赖

```bash
pip install websocket-client requests pillow
```

`pillow` 只有 `browser_screenshot.py --annotate` 需要，用于在截图上画编号框。

## 第一步：确保有可连接的浏览器

**默认场景（推荐）**：Agent 拉起一个独立的专用 Chrome 实例，独立 profile + 独立调试端口
（默认 9333），不碰用户真实登录态：

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
# 输出里会给出 port 和首个 tab id，例如 --port 9333 --tab <id>
python browser_nav.py --port 9333 --tab <id> --goto "https://example.com"
```

常用管理：`python browser_launch.py --list-dedicated`（查看已建实例）、
`python browser_launch.py --stop-dedicated work`（用完关闭）。

**⚠️ 登录状态要跨多次调用保留，`--name` 必须每次固定不变**：`--dedicated` 的登录态持久化
依赖同一个 `--name` 对应同一个 profile 目录。同一个任务/workflow 内所有涉及浏览器的步骤
都要用**同一个固定的实例名**（比如做知乎相关任务统一用 `--name zhihu_session`），不要每次
调用都用默认值或临时想一个名字——用不同名字等于每次都是全新登录态。第一次用某个 `--name`
跑的时候如果页面显示未登录，提示用户在这个专用窗口里手动登录一次即可，之后同名字复用会
保留登录态（profile 目录固定存放在 `temp_cdp/cdp_brower_data/<name>/`）。

如果同名字复用后登录态仍然看起来"丢了"，大概率不是目录/文件被删，而是上次浏览器非正常退出
（被强制杀进程/崩溃）后残留的 Chrome 单实例锁文件（`SingletonLock` 等）阻止了这次启动正确
加载该 profile；`browser_launch.py` 每次启动新进程前都会自动清理这些锁文件，遇到这种情况
不需要手动处理，直接重新 `--dedicated --name <同一个名字>` 即可。

需要连接用户已登录的真实浏览器窗口（共享登录态），或无 GUI 服务器环境用 headless，
加载 `browser-launch-scenarios` 子资源查看完整步骤。

**先检测再启动**：`--ensure` 模式在请求的端口不通时，不会直接判定"没有可用浏览器"，会先
扫一遍系统进程找有没有其它已经在跑的、带调试端口的 chrome/edge（不限于本技能自己启动的，
包括用户手动开的、或者之前会话遗留的），找到就直接复用并提示应该用哪个端口，而不是又启动
一个新的。想单独看一下当前系统里有哪些调试浏览器在跑，用：

```bash
python browser_launch.py --list-running
```

`--dedicated` 模式对"是否已有可用实例"的判断范围是"指定 `--name` 对应的那一个专用实例"
（先查 registry.json，查不到再查 profile 目录下的锁文件兜底），不会去匹配系统里任意其它
无关的调试浏览器——这是有意为之：`--dedicated` 的语义是"这个固定名字对应固定的 profile"，
如果为了"复用一个已有浏览器"而误连到一个 profile/登录态完全不相关的实例，反而会造成更隐蔽
的问题。

## 典型工作流速览

```bash
python browser_launch.py --new "https://example.com"        # 拿到新 tab 的 id
python browser_nav.py --tab <id> --goto "https://example.com"
python browser_extract.py --tab <id> --mode text             # 纯文本正文，适合直接喂给模型分析
python browser_screenshot.py --tab <id> --out shot.png --annotate   # 编号标注截图，用于看图点击/填表单
```

大页面注意 `--max-chars`（默认 20000）截断，需要完整内容时用 `--save out.txt` 写文件。

表单填写/点击的完整"看图操作"流程、与用户协作的三种介入程度（观察/建议/代劳）、
调试网页 console/网络请求，加载 `workflows` 子资源查看完整示例。

## ⚠️ 路径规则（极易踩坑）

**所有脚本都必须先 `cd` 到 skill 目录再运行**（相对导入 `cdp_client`/`utils`），这意味着
所有相对路径（包括 `./temp_data/`）都是**相对于 skill 目录**解析的，不是项目根目录。
优先使用绝对路径输出产出文件。完整规则和错误示例见 `troubleshooting` 子资源。

## 安全与边界

- 不用于绕过验证码、自动登录他人账号、批量注册等滥用场景；涉及登录态操作时，让用户自己完成
  账号密码/验证码相关的输入。
- 涉及"提交""支付""删除""发送"等不可逆操作时，先用截图向用户确认，再执行。
- headless spawn 模式默认使用独立 profile，不会读取用户真实 Chrome 的 cookie/密码，
  纯抓取公开页面场景优先用这个模式，减少对用户账号的接触面。
- 调试端口只在需要时开启，用完可以提示用户关闭该 Chrome 窗口恢复正常模式。

## 常见坑（速查，完整版见 `troubleshooting` 子资源）

- `Page.captureScreenshot` 的 `clip` 坐标是 CSS 像素，不需要额外乘 DPR。
- 页面是 SPA 时不要死等 load 事件，用 `browser_watch.py --wait-url-contains` 判断路由跳转。
- 元素编号依赖当次 DOM 扫描顺序，页面有明显变化后务必先重新截图/扫描再操作。
