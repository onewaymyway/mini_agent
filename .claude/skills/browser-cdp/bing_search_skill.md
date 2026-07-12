---
name: bing-search
skill: browser-cdp
script: bing_search.py
description: Bing 搜索自动化脚本，支持关键词搜索、结构化结果提取、详情页内容获取、结果保存为JSON/Markdown、截图标注。
triggers: Bing搜索, bing search, 必应搜索, 自动搜索, 搜索自动化
---

# Bing 搜索自动化脚本 (`bing_search.py`)

## 用途

使用 browser-cdp skill 进行 Bing 搜索并获取详细内容。支持：
- 关键词搜索并提取结构化结果（标题、URL、摘要）
- 可选获取每个结果的详情页完整内容
- 结果保存为 JSON 和 Markdown 格式
- 可选截图搜索结果页（带编号标注）
- 支持无头模式、自定义端口、实例复用

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索（只获取结果列表，不抓取详情页）
python bing_search.py "自主进化Agent" --max-results 5 --no-detail

# 完整搜索（获取结果列表 + 每个结果的详情页内容）
python bing_search.py "自主进化Agent" --max-results 10

# 无头模式 + 自定义输出目录 + 截图结果页
python bing_search.py "自主进化Agent" --max-results 5 --headless --output-dir ./my_results --screenshot

# 复用已有浏览器实例（指定 --name 和 --port）
python bing_search.py "自主进化Agent" --name my_search --port 9333 --max-results 5
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results` |
| `--port` | CDP 调试端口 | 9333 |
| `--name` | 浏览器实例名称（用于复用） | `bing_search` |
| `--headless` | 无头模式运行 | False |
| `--wait-timeout` | 页面等待超时(秒) | 15 |
| `--max-chars` | 详情页最大字符数 | 5000 |
| `--no-detail` | 不获取详情页内容，仅结果列表 | False |
| `--screenshot` | 搜索结果页截图（带编号标注） | False |

## 输出文件

运行后会在 `--output-dir` 生成：
- `bing_search_<query>.json` — 完整结构化数据（含标题、URL、摘要、详情页内容、成功状态）
- `bing_search_<query>.md` — 人类可读的 Markdown 摘要
- `search_<query>.png` — 可选，搜索结果页截图（带红框编号）

## 核心实现要点（供参考/二次开发）

### 1. 直接访问搜索结果 URL 而非模拟点击搜索按钮

```python
search_url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
```

避免了搜索按钮点击可能无效的问题。

### 2. 使用 JavaScript 精准提取搜索结果

```javascript
// 选择器：li.b_algo (Bing 当前搜索结果容器)
// 标题/链接：h2 a
// 摘要：.b_caption p
```

通过 `browser_console.py --eval` 执行，一次性获取结构化数组，避免 `browser_extract.py --mode links` 返回大量噪声链接。

注意：选择器 `li.b_algo` 比 `#b_results` 更稳健，headless 模式下 `#b_results` 可能不可见。

### 3. 浏览器实例复用

使用 `--dedicated --name <name>` 机制，自动管理专用 Chrome 实例（独立 profile、端口 9333），避免重复启动和登录态干扰。

### 4. 验证码/难题页面检测

Headless 模式下 Bing 可能返回验证码页面（"请解决以下难题以继续")，脚本内置检测逻辑：

```javascript
document.body.innerText.includes('请解决以下难题') ||
document.body.innerText.includes('验证码') ||
document.body.innerText.includes('captcha') ||
document.body.innerText.includes('challenge')
```

检测到时自动提示用户使用非 headless 模式运行。

### 5. Bing 重定向链接解析

Bing 搜索结果链接通常是重定向格式：
```
https://www.bing.com/ck/a?!...&u=a1aHR0cHM6Ly96aHVhbmxhbi56aGlodS5jb20vcC8yMDQ2NDg1NzgwNjE3NTA2ODYx&...
```
其中 `u` 参数包含 URL-safe base64 编码的真实 URL，且带有 2 字符前缀（如 `a1`、`a2`）。解析逻辑：

```javascript
if (url.includes('bing.com/ck/') && url.includes('&u=')) {
  const uParam = url.split('&u=')[1].split('&')[0];
  // 去掉前缀（通常是 a1, a2 等 2 字符）
  const base64Part = uParam.length > 2 ? uParam.substring(2) : uParam;
  // URL-safe base64 解码
  let decoded = base64Part.replace(/-/g, '+').replace(/_/g, '/');
  while (decoded.length % 4) decoded += '=';
  const realUrl = atob(decoded);
  if (realUrl.startsWith('http')) url = realUrl;
}
```

通过 `browser_console.py --eval` 在浏览器端直接执行，一次性获取解析后的真实 URL。

## 常见问题

- **搜索结果为空**：Bing 页面结构可能变更，需更新 JS 选择器
- **详情页内容提取不全**：通用的 CSS/JS 过滤规则对复杂页面效果一般，可针对主流站点添加专用清理规则
- **截图超时**：`browser_screenshot.py --annotate` 在某些页面超时，需调整参数或分块截图
- **反爬虫限制**：高频请求可能触发验证码或 IP 封禁，建议增加随机延迟

## 依赖脚本

- `browser_launch.py` — 启动/复用浏览器实例
- `browser_nav.py` — 导航到搜索结果页和详情页
- `browser_console.py` — 执行 JS 提取搜索结果
- `browser_extract.py` — 提取详情页文本内容
- `browser_screenshot.py` — 可选，截图搜索结果页
- `browser_input.py` — 已修复输入重复问题，供其他场景使用

## 文件结构

```
.claude/skills/browser-cdp/
├── bing_search.py          # 主脚本
├── bing_search_skill.md    # 本文档
├── search_results/         # 默认输出目录
│   ├── bing_search_<query>.json
│   └── bing_search_<query>.md
└── temp_data/              # 临时文件（截图、元素分析等）
```