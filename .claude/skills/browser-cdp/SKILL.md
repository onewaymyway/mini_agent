---
name: browser-cdp
description: 通过 Chrome DevTools Protocol (CDP) 控制真实 Chrome/Edge 浏览器：打开网页、抓取网页内容（HTML/纯文本/表单/链接）、截图（含编号标注可交互元素）、模拟点击和输入、执行JS、读取console/网络日志，并支持与用户同时操作同一个浏览器（观察/建议/代劳三种协作模式）。当用户说"帮我打开网页"、"抓取这个网站"、"帮我填一下这个表单"、"看看我浏览器里这个页面"、"截个图分析一下"时使用。
triggers: 浏览器, 打开网页, 抓取网页, 网页截图, cdp, chrome devtools, 模拟点击, 模拟输入, 网页自动化, 填表单, browser automation, scrape webpage
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

所有脚本都需要先 `cd` 到本目录再运行（用了相对导入 `cdp_client`/`utils`），或者用
`python .claude/skills/browser-cdp/xxx.py` 的相对路径调用同时保证 cwd 里能 import 到，
最稳妥方式是：

```bash
cd .claude/skills/browser-cdp && python3 browser_xxx.py ...
```

## 前置依赖

```bash
pip install websocket-client requests pillow
```

`pillow` 只有 `browser_screenshot.py --annotate` 需要，用于在截图上画编号框。

## 第一步：确保有可连接的浏览器

### 场景 A：用户本机 Windows，希望和用户共享同一个浏览器窗口（可以看到用户已登录的站点）

Chrome 不允许对一个"已经在跑、没开调试端口"的实例远程接管，所以需要用户重新用调试端口打开一次：

1. 完全退出 Chrome（包括后台/托盘图标）
2. 用户运行（或让用户创建一个桌面快捷方式，目标改成下面这样）：
   ```
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
   ```
   这样打开的还是用户的默认 profile（保留登录态、书签等），只是多开了调试端口。
3. 之后正常用 `browser_launch.py --list` 之类命令连接 `127.0.0.1:9222` 即可。

告知用户：调试端口只监听本机 127.0.0.1，不会被外部网络访问，但仍建议用完后关闭该模式。

### 场景 B：无 GUI 服务器/沙盒环境，只做抓取，不需要用户观察

```bash
cd .claude/skills/browser-cdp
python3 browser_launch.py --ensure --spawn --headless
```

会自动探测 Chrome/Chromium 可执行文件，在独立的 `~/.cdp_skill_profile` 目录下起一个无头实例，
不影响用户任何现有浏览器窗口。第一次探测失败时会提示用 `--binary` 指定路径。

### 检查/复用已有连接

```bash
python3 browser_launch.py --ensure     # 端口已通 -> 直接打印版本信息；不通 -> 报错并给出上面两种指引
python3 browser_launch.py --list       # 列出所有 tab，拿到 --tab 用的 id
```

## 典型工作流

### 1. 打开网页并抓取内容

```bash
python3 browser_launch.py --new "https://example.com"        # 拿到新 tab 的 id
python3 browser_nav.py --tab <id> --goto "https://example.com"
python3 browser_extract.py --tab <id> --mode text             # 纯文本正文，适合直接喂给模型分析
python3 browser_extract.py --tab <id> --mode links            # 所有链接
python3 browser_extract.py --tab <id> --mode meta             # 标题/描述/h1
```

大页面注意 `--max-chars`（默认 20000）截断，需要完整内容时用 `--save out.txt` 写文件后自己分段读。

### 2. "看图操作"式的表单填写/点击（computer-use 风格）

```bash
python3 browser_screenshot.py --tab <id> --out shot.png --annotate
# 产出 shot.png（带编号红框）+ shot.elements.json（编号 -> 元素信息，tag/text/rect等）
# 把 shot.png 发给用户看/自己视觉分析，确定要操作第几号元素
python3 browser_input.py --tab <id> --type-index 5 --text "张三" --clear-first
python3 browser_input.py --tab <id> --click-index 8
python3 browser_screenshot.py --tab <id> --out after.png --annotate   # 操作后再截一次确认结果
```

也可以不截图，直接用 CSS 选择器：
```bash
python3 browser_input.py --tab <id> --click-selector "#submit-btn"
python3 browser_input.py --tab <id> --type-selector "input[name=username]" --text "abc"
```

### 3. 与用户协作（不同介入程度）

- **观察模式**（只看不动）：`browser_extract.py --mode text` / `browser_screenshot.py` 直接读取
  用户当前 tab（用 `--url-contains`/`--title-contains` 定位到用户正在看的那个 tab，不要用 `--new`
  开新 tab，否则不是用户正在看的页面）。
- **建议模式**：观察后只用文字描述"你可以点击左上角的登录按钮"，不调用 `browser_input.py`。
- **代劳模式**：用户明确同意后才调用 `browser_input.py` 实际操作；操作前后各截一次图，把结果给用户确认，
  不要连续做多步高风险操作（比如"提交订单""转账确认"）而不中途反馈。
- **等待用户完成某步**（比如让用户自己输入验证码/完成支付，Agent 等结果）：
  ```bash
  python3 browser_watch.py --tab <id> --wait-url-contains "/success" --timeout 120 --interval 2
  ```

### 4. 调试网页问题

```bash
python3 browser_console.py --tab <id> --eval "document.querySelectorAll('.item').length"
python3 browser_console.py --tab <id> --watch-console --duration 5   # 抓最近5秒的console报错
python3 browser_console.py --tab <id> --watch-network --duration 5   # 抓最近5秒的请求（url/status）
```

## 安全与边界

- 不用于绕过验证码、自动登录他人账号、批量注册等滥用场景；涉及登录态操作时，让用户自己完成
  账号密码/验证码相关的输入。
- 涉及"提交""支付""删除""发送"等不可逆操作时，先用截图向用户确认，再执行。
- headless spawn 模式默认使用独立 profile，不会读取用户真实 Chrome 的 cookie/密码，
  纯抓取公开页面场景优先用这个模式，减少对用户账号的接触面。
- 调试端口只在需要时开启，用完可以提示用户关闭该 Chrome 窗口恢复正常模式。

## 常见坑

- `Page.captureScreenshot` 的 `clip` 坐标是 CSS 像素，跟设备像素比无关，直接用
  `getBoundingClientRect()` 的值即可，不需要额外乘 DPR。
- 无头模式下 `window.innerHeight/innerWidth` 依赖 `--window-size`，元素扫描的 `inViewport`
  判断会受此影响，必要时调整 `browser_launch.py --spawn` 里的 `--window-size` 参数。
- 页面是 SPA（前端路由）时 `Page.loadEventFired` 可能只在首次加载触发，路由跳转后要靠
  `browser_watch.py --wait-url-contains` 或 `browser_nav.py --wait-selector` 判断状态，
  不要死等 load 事件。
- 编号（`--click-index` 等）依赖当次 DOM 扫描顺序，如果页面在两次调用之间发生了明显变化
  （异步加载、用户自己操作了），编号可能失效，务必先重新截图/扫描再操作。
