# 常见坑与路径规则

## 工作目录与路径规则（极易踩坑，务必遵守）

**所有浏览器 CDP 脚本都必须 `cd` 到 skill 目录再运行**（因为脚本内部用了相对导入 `cdp_client`/`utils`），
这意味着命令执行时的 `cwd` 是 **skill 目录**（`.claude/skills/browser-cdp/`），**不是项目根目录**。

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
