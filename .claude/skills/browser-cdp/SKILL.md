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

## ⚠️ 运行前必做：Python 命令检测（极易踩坑，务必遵守）

**本环境同时存在 `python` 和 `python3` 两个命令，但只有一个是真正可用的。**

### 第一步：检测哪个 Python 可用

在调用任何浏览器 CDP 脚本之前，**必须先检测哪个命令可用**，然后后续所有调用都使用那个可用的命令。

```bash
# 先测试 python 是否可用
python --version 2>&1 | head -1
# 如果输出类似 "Python 3.x.x"，则用 python
# 如果报错 "不是内部或外部命令"，则用 python3

# 再测试 python3 是否可用（作为备选）
python3 --version 2>&1 | head -1
```

### 本环境的检测结果

- **`python`** ✅ 可用（指向 Anaconda 的 Python，路径如 `D:\ProgramData\anaconda3\python.exe`）
- **`python3`** ❌ 不可用（指向 Windows 应用商店的重定向器，会弹出安装提示）

**因此，本环境中所有浏览器 CDP 脚本都必须使用 `python` 而不是 `python3` 来调用！**

### 正确用法示例

```bash
# ✅ 正确：使用 python（Anaconda 版本）
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
python browser_nav.py --port 9333 --tab <id> --goto "https://example.com"
python browser_extract.py --tab <id> --mode text --save ./temp_data/page_content.txt

# ❌ 错误：使用 python3（会失败）
python3 browser_launch.py --dedicated --name work  # 报错！
```

### 为什么必须检测？

不同环境的 Python 命令可用性不同：
- **Windows + Anaconda**：通常只有 `python` 可用，`python3` 不存在或重定向
- **Linux/macOS**：通常 `python3` 可用，`python` 可能不存在（Python 2 已移除）
- **某些 Docker 容器**：可能两者都有或都没有

**每次在新环境中使用时，必须先运行检测命令确认，然后统一使用那个可用的命令。**

### 检测脚本（可选）

如果不确定当前环境，可以运行以下命令自动检测：

```bash
if command -v python &> /dev/null && python --version &> /dev/null; then
    echo "USE: python"
elif command -v python3 &> /dev/null && python3 --version &> /dev/null; then
    echo "USE: python3"
else
    echo "ERROR: No Python found!"
fi
```

## 前置依赖

```bash
pip install websocket-client requests pillow
```

`pillow` 只有 `browser_screenshot.py --annotate` 需要，用于在截图上画编号框。

## 第一步：确保有可连接的浏览器

### 场景 A（推荐用于"后续一系列自动化操作"）：打开一个专门的新 Chrome 实例

不依赖用户手动改快捷方式，直接由 Agent 拉起一个**独立的、专门供后续操作使用**的 Chrome：
独立 profile（不碰用户真实登录态）、独立调试端口（默认 9333，不与场景 B 的 9222 冲突）、
默认可见窗口（也可以 `--headless` 用于服务器场景），并会把实例信息记到本地注册表，
之后脚本随时用同一个 `--port` 复用它，不用每次重新启动。

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
# 输出里会给出 port 和首个 tab id，例如 --port 9333 --tab <id>
python browser_nav.py --port 9333 --tab <id> --goto "https://example.com"
```

常用管理命令：
```bash
python browser_launch.py --list-dedicated          # 查看已创建的专用实例（含是否存活）
python browser_launch.py --stop-dedicated work     # 用完关闭并从注册表移除
```

同一次任务里可以按需要开多个（用不同 --name），比如一个用来登录A站点、一个用来查B站点，互不干扰。
默认可见（非 headless），方便用户随时瞄一眼 Agent 在干什么；纯后台抓取不需要用户看时加 `--headless`。

### 场景 B：连接用户本机正在用的浏览器窗口（共享登录态）

Chrome 不允许对一个"已经在跑、没开调试端口"的实例远程接管，所以需要用户重新用调试端口打开一次：

1. 完全退出 Chrome（包括后台/托盘图标）
2. 用户运行（或让用户创建一个桌面快捷方式，目标改成下面这样）：
   ```
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
   ```
   这样打开的还是用户的默认 profile（保留登录态、书签等），只是多开了调试端口。
3. 之后正常用 `browser_launch.py --list` 之类命令连接 `127.0.0.1:9222` 即可。

告知用户：调试端口只监听本机 127.0.0.1，不会被外部网络访问，但仍建议用完后关闭该模式。

### 场景 C：无 GUI 服务器/沙盒环境，只做抓取，不需要可见窗口

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --headless          # 等价于场景A但不弹窗口
# 或临时用一次不留注册记录：
python browser_launch.py --ensure --spawn --headless
```

会自动探测 Chrome/Chromium 可执行文件。第一次探测失败时会提示用 `--binary` 指定路径。

### 检查/复用已有连接

```bash
python browser_launch.py --ensure     # 端口已通（默认9222）-> 直接打印版本信息；不通 -> 报错并给出上面几种指引
python browser_launch.py --list --port 9333   # 列出指定端口下的所有 tab，拿到 --tab 用的 id
```

## 典型工作流

### 1. 打开网页并抓取内容

```bash
python browser_launch.py --new "https://example.com"        # 拿到新 tab 的 id
python browser_nav.py --tab <id> --goto "https://example.com"
python browser_extract.py --tab <id> --mode text             # 纯文本正文，适合直接喂给模型分析
python browser_extract.py --tab <id> --mode links            # 所有链接
python browser_extract.py --tab <id> --mode meta             # 标题/描述/h1
```

大页面注意 `--max-chars`（默认 20000）截断，需要完整内容时用 `--save out.txt` 写文件后自己分段读。

### 2. "看图操作"式的表单填写/点击（computer-use 风格）

```bash
python browser_screenshot.py --tab <id> --out shot.png --annotate
# 产出 shot.png（带编号红框）+ shot.elements.json（编号 -> 元素信息，tag/text/rect等）
# 把 shot.png 发给用户看/自己视觉分析，确定要操作第几号元素
python browser_input.py --tab <id> --type-index 5 --text "张三" --clear-first
python browser_input.py --tab <id> --click-index 8
python browser_screenshot.py --tab <id> --out after.png --annotate   # 操作后再截一次确认结果
```

也可以不截图，直接用 CSS 选择器：
```bash
python browser_input.py --tab <id> --click-selector "#submit-btn"
python browser_input.py --tab <id> --type-selector "input[name=username]" --text "abc"
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
  python browser_watch.py --tab <id> --wait-url-contains "/success" --timeout 120 --interval 2
  ```

### 4. 调试网页问题

```bash
python browser_console.py --tab <id> --eval "document.querySelectorAll('.item').length"
python browser_console.py --tab <id> --watch-console --duration 5   # 抓最近5秒的console报错
python browser_console.py --tab <id> --watch-network --duration 5   # 抓最近5秒的请求（url/status）
```

## 启动失败/进程清理策略

- `--dedicated`/`--ensure --spawn` 启动失败（调试端口超时未就绪）时，脚本**只会杀掉本次自己刚拉起的
  那一个进程**（`Popen` 返回的 pid），绝不会去扫描或杀死任何其他 Chrome/Edge 进程，不会影响用户
  已经在用的浏览器窗口。
- 若某个 `--name` 对应的专用实例此前异常退出（进程崩了但没走 `--stop-dedicated` 清理），下次
  `--dedicated --name <同名>` 会先检查 registry 里记录的那个旧 pid 是否还活着——**只处理这一个
  被本技能记录过的 pid**，健在则先关闭它，再清理 profile 目录里的单例锁文件，然后才重新启动，
  避免"新旧两个进程抢同一个 profile 目录，实际生效的是旧进程"这种状态不一致问题。
- `--dedicated` 启动成功后不会只凭"调试端口通了"就报告成功，而是会**真正连上第一个 tab、
  轮询读取 `document.readyState/location.href/document.title`**，直到页面 `complete` 或超时，
  把读到的真实状态打印出来。判断"网页是否打开成功"应该看这行 `当前页面: url=... readyState=...`，
  而不是只看进程有没有报错。

## 安全与边界

- 不用于绕过验证码、自动登录他人账号、批量注册等滥用场景；涉及登录态操作时，让用户自己完成
  账号密码/验证码相关的输入。
- 涉及"提交""支付""删除""发送"等不可逆操作时，先用截图向用户确认，再执行。
- headless spawn 模式默认使用独立 profile，不会读取用户真实 Chrome 的 cookie/密码，
  纯抓取公开页面场景优先用这个模式，减少对用户账号的接触面。
- 调试端口只在需要时开启，用完可以提示用户关闭该 Chrome 窗口恢复正常模式。

## ⚠️ 工作目录与路径规则（极易踩坑，务必遵守）

**所有浏览器 CDP 脚本都必须 `cd` 到 skill 目录再运行**（因为脚本内部用了相对导入 `cdp_client`/`utils`），这意味着命令执行时的 `cwd` 是 **skill 目录**（`.claude/skills/browser-cdp/`），**不是项目根目录**。

因此，**所有相对路径（包括 `./temp_data/`）都是相对于 skill 目录解析的**，不是相对于项目根目录。

**正确做法 — 使用绝对路径或 skill 目录下的相对路径：**
```bash
# 方法一：用 skill 目录下的相对路径（skill 目录下创建 temp_data 子目录）
mkdir -p .claude/skills/browser-cdp/temp_data
python browser_screenshot.py --tab <id> --out .claude/skills/browser-cdp/temp_data/shot.png --annotate
python browser_extract.py --tab <id> --mode text --save .claude/skills/browser-cdp/temp_data/page_content.txt

# 方法二：直接用绝对路径（推荐，最稳妥）
python browser_screenshot.py --tab <id> --out E:/codes/mini_claude_code/.claude/skills/browser-cdp/temp_data/shot.png --annotate
python browser_extract.py --tab <id> --mode text --save E:/codes/mini_claude_code/.claude/skills/browser-cdp/temp_data/page_content.txt
```

**错误做法（会找不到目录或写入错误位置）：**
```bash
# ❌ 以为 ./temp_data 在项目根目录——实际在 skill 目录下！
mkdir -p ./temp_data          # 这会在 skill 目录下创建 temp_data，不是项目根目录的
python browser_screenshot.py --tab <id> --out ./temp_data/shot.png   # 写入 skill 目录下的 temp_data

# ❌ 以为 cd 到 skill 目录后 ./temp_data 还是项目根目录的
# 事实：cd .claude/skills/browser-cdp 后，./temp_data = .claude/skills/browser-cdp/temp_data
```

**总结：cd 到 skill 目录后，所有 `./xxx` 路径都以 skill 目录为基准。**

任务完成后可清理：`rm -rf .claude/skills/browser-cdp/temp_data/*`（或按需保留产出物）。

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
- **忘记指定 `./temp_data/` 路径导致临时文件散落在项目各处** —— 始终显式指定输出路径为 `./temp_data/xxx`。
