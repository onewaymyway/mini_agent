---
name: zhihu-hot
skill: browser-cdp
script: zhihu_hot.py
description: 知乎热榜抓取自动化脚本，支持免登录发现页抓取和登录态热榜抓取，自动检测登录状态并降级。
triggers: 知乎热榜，知乎热点，zhihu hot, 抓取热榜，知乎 trending
platforms: windows, macos, linux, pc
---

# 知乎热榜抓取自动化脚本 (`zhihu_hot.py`)

## 用途

通过浏览器 CDP 抓取知乎热榜内容，支持两种模式：

- **免登录模式**：抓取知乎"发现"页面（/explore），获取近期热点、潜力好问题、热门专题等
- **登录模式**：抓取知乎热榜页面（/hot），需要浏览器已登录知乎账号，获取完整热榜（含排名、热度值）
- **自动模式**：优先尝试热榜页面，检测到未登录时自动降级到发现页

支持功能：
- 自动检测登录状态并智能降级
- 提取热点问题标题、URL、元数据（热度、回答数等）
- 结果保存为 JSON（结构化）和 Markdown（人类可读）格式
- 支持无头模式、自定义端口、实例复用
- 可配置最大提取条目数

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 自动模式（推荐，优先热榜，失败则降级发现页）
python zhihu_hot.py

# 免登录发现页模式
python zhihu_hot.py --mode discover

# 登录态热榜模式（需先登录知乎）
python zhihu_hot.py --mode hot

# 提取 50 条热点
python zhihu_hot.py --max-items 50

# 无头模式 + 自定义输出目录
python zhihu_hot.py --headless --output-dir ./zhihu_results

# 仅输出列表，不保存详细报告
python zhihu_hot.py --no-detail

# 复用已有浏览器实例
python zhihu_hot.py --port 9333 --name my_browser
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--mode` | 抓取模式：discover(发现页), hot(热榜), auto(自动) | `auto` |
| `--max-items` | 最大提取条目数 | 30 |
| `--output-dir` | 输出目录 | `./search_results` |
| `--port` | CDP 调试端口 | 9333 |
| `--name` | 浏览器实例名称 | `zhihu_hot` |
| `--headless` | 无头模式运行 | False |
| `--wait-timeout` | 页面等待超时 (秒) | 20 |
| `--no-detail` | 仅输出列表，不保存详细报告 | False |

## 输出文件

运行后会在 `--output-dir` 生成：
- `zhihu_hot_<mode>_<timestamp>.json` — 完整结构化数据（含提取时间、模式、条目列表）
- `zhihu_hot_<mode>_<timestamp>.md` — 人类可读的 Markdown 报告（含表格摘要、详细内容）

## 核心实现要点（供参考/二次开发）

### 1. 免登录发现页抓取

知乎发现页面（`/explore`）不需要登录即可查看，包含：
- 近期热点（Recent Hot）
- 潜力好问题（Potential Questions）
- 最新专题（Special Topics）
- 圆桌讨论（Roundtable Discussions）

**提取策略**：
```javascript
// 提取所有包含 /question/ 的链接，去重后获取热点列表
const links = Array.from(document.querySelectorAll('a'))
  .filter(a => a.href.includes('/question/'));
```

**经验**：
- 通过 `a.closest('div')` 获取父容器，提取元数据（浏览数、关注数等）
- 使用 `Set` 去重，避免重复提取
- 限制最大提取数量，避免页面过大

### 2. 登录态热榜抓取

知乎热榜页面（`/hot`）需要登录才能访问：
- 未登录会跳转到 `/signin?next=/hot`
- 登录后可见完整热榜（含排名、热度值、回答数）

**登录检测**：
```javascript
(() => {
  return window.location.pathname === '/signin';
})()
```

**提取策略**：
```javascript
// 热榜项选择器：[class*=HotItem] 或 .HotItem-card
const items = Array.from(document.querySelectorAll('[class*=HotItem]'));

// 提取排名、标题、热度、回答数
const rankEl = item.querySelector('[class*=rank]');
const titleEl = item.querySelector('a[href*=question]');
const hotValueEl = item.querySelector('[class*=hotValue]');
const answerEl = item.querySelector('[class*=answerCount]');
```

### 3. 自动降级机制

```python
if mode == 'auto':
    # 先尝试热榜
    session.navigate("https://www.zhihu.com/hot")
    time.sleep(3)
    
    # 检测是否跳转到登录页
    is_signin = check_if_signin_page()
    
    if is_signin:
        # 降级到发现页
        mode = 'discover'
        session.navigate("https://www.zhihu.com/explore")
```

**经验**：
- 自动模式是最推荐的用法，无需关心登录状态
- 降级后自动切换目标页面，用户无感知
- 可以在输出中明确告知用户当前使用的模式

### 4. 结果保存格式

**JSON 格式**：
```json
{
  "mode": "discover",
  "extract_time": "2026-07-13 09:30:00",
  "total_items": 30,
  "items": [
    {
      "title": "2026 年 7 月 9 日，福建晋江辉腾鞋厂...",
      "url": "https://www.zhihu.com/question/2058900143710246282",
      "meta": "2734 万浏览 · 5949 关注 · 2728 回答"
    }
  ]
}
```

**Markdown 格式**：
```
# 知乎热榜抓取报告

> 抓取时间：2026-07-13 09:30:00
> 模式：discover
> 总条目数：30

## 热榜内容

| # | 问题 | 元数据 |
|---|------|--------|
| 1 | [2026 年 7 月 9 日，福建晋江...] | 2734 万浏览 · 5949 关注 |
```

### 5. 页面结构变化应对

知乎页面结构可能随时变化，建议：
- 使用通配符选择器：`[class*=HotItem]` 而非 `.HotItem-card`
- 多个备选选择器：`[class*=rank], .Rank, .rank`
- 提取失败时检查浏览器控制台是否有 JS 错误
- 定期更新选择器以适配页面改版

## 常见问题

- **热榜模式获取不到内容**：知乎热榜需要登录才能访问，使用 `--mode discover` 免登录抓取发现页
- **自动模式总是降级**：检查浏览器是否已登录知乎，或尝试用 `--mode hot` 强制热榜模式
- **提取条目太少**：增加 `--max-items` 参数，或检查页面是否完全加载
- **内容为空或选择器失效**：知乎页面结构可能变更，需要更新 JS 选择器
- **无头模式无法登录**：无头模式通常没有登录态，建议使用非 headless 模式或先用 `--mode discover`

## 依赖脚本

- `browser_launch.py` — 启动/复用浏览器实例（通过 `ensure_browser`）
- `browser_nav.py` — 导航到目标页面
- `browser_console.py` — 执行 JS 提取页面内容
- `baidu_search.py` — 复用 `ensure_browser`、`get_random_ua` 等函数
- `cdp_client.py` — CDP 连接和 JS 执行

## 文件结构

```
.claude/skills/browser-cdp/
├── zhihu_hot.py             # 主脚本
├── zhihu_hot_skill.md       # 本文档
├── baidu_search.py          # 依赖（复用浏览器管理）
├── cdp_client.py            # 依赖（CDP 连接）
├── search_results/          # 默认输出目录
│   ├── zhihu_hot_discover_20260713_093000.json
│   └── zhihu_hot_discover_20260713_093000.md
└── temp_data/               # 临时文件
```

## 与 zhihu_search.py 的区别

| 特性 | zhihu_hot.py | zhihu_search.py |
|------|--------------|-----------------|
| 用途 | 抓取热榜/发现页热点 | 通过百度搜索知乎内容 |
| 登录要求 | 发现页免登录，热榜需登录 | 通常不需要登录 |
| 数据来源 | 知乎官方页面 | 百度搜索 site:zhihu.com |
| 输出内容 | 热点列表（无分类） | 问答 + 专栏分类 |
| 适用场景 | 了解当前热门话题 | 搜索特定主题的知乎内容 |
