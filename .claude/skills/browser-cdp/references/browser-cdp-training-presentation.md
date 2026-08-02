# Browser CDP 技能培训演示文稿

## 目录

1. [技能概述](#1-技能概述)
2. [核心优势](#2-核心优势)
3. [安装与配置](#3-安装与配置)
4. [浏览器连接管理](#4-浏览器连接管理)
5. [页面导航与控制](#5-页面导航与控制)
6. [内容抓取](#6-内容抓取)
7. [截图与标注](#7-截图与标注)
8. [用户交互](#8-用户交互)
9. [调试与监控](#9-调试与监控)
10. [常见坑与解决方案](#10-常见坑与解决方案)
11. [测试用例库](#11-测试用例库)
12. [总结与下一步](#12-总结与下一步)

---

## 1. 技能概述

### 什么是 Browser CDP？

Browser CDP 是通过 **Chrome DevTools Protocol (CDP)** 直接控制 Chrome/Edge 浏览器的技能，**不依赖 Playwright/Selenium**。

### 核心功能

- ✅ 打开网页、抓取内容（HTML/文本）
- ✅ 截图（含编号标注可交互元素）
- ✅ 模拟点击和输入
- ✅ 执行 JavaScript
- ✅ 读取 console/网络日志
- ✅ 支持与用户协同操作（观察/建议/代劳三种模式）

---

## 2. 核心优势

### 🌟 最大优势：连接真实浏览器

| 传统自动化框架 | Browser CDP |
|---------------|-------------|
| 每次启动新浏览器，丢失登录态 | **连接用户已登录的真实浏览器** |
| 需要复杂的代理配置 | 直接使用现有浏览器窗口 |
| 难以处理需要登录的场景 | **无缝复用用户的登录态** |

### 💡 其他优势

- **灵活的工作模式**：专用实例 / 连接已有浏览器 / Headless 模式
- **协作能力**：支持观察、建议、代劳三种介入程度
- **轻量级**：不依赖大型框架，只需 websocket-client, requests, pillow

---

## 3. 安装与配置

### 前置依赖

```bash
cd .claude/skills/browser-cdp
pip install websocket-client requests pillow
```

### Python 环境检测

本环境使用 `python`（Anaconda），`python3` 不可用。

```bash
python --version  # 确认可用
```

---

## 4. 浏览器连接管理

### 4.1 启动专用实例（推荐）

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
```

输出示例：`--port 9333 --tab abc123`

### 4.2 常用管理命令

| 命令 | 说明 |
|------|------|
| `--list-dedicated` | 查看已创建的专用实例 |
| `--stop-dedicated <name>` | 关闭指定实例 |
| `--list-running` | 检测系统中运行的调试浏览器 |

### 4.3 ⚠️ 重要提示：实例名固定

同一个任务内所有步骤都要用**同一个固定的实例名**（如 `--name zhihu_session`），否则登录态会丢失！

---

## 5. 页面导航与控制

### 5.1 基本导航

```bash
# 打开网址
python browser_nav.py --port 9333 --tab <id> --goto "https://example.com"

# 前进/后退/刷新
python browser_nav.py --port 9333 --tab <id> --forward/back/refresh
```

### 5.2 等待元素出现

```bash
python browser_nav.py --port 9333 --tab <id> --wait-selector ".loading-complete"
```
> 默认超时 30 秒，动态页面必备！

---

## 6. 内容抓取

### 6.1 文本抓取（最常用）

```bash
python browser_extract.py --port 9333 --tab <id> --mode text --max-chars 20000
```

### 6.2 其他抓取模式

| 模式 | 用途 |
|------|------|
| `--mode html` | 获取完整 HTML 源代码 |
| `--mode forms` | 提取表单字段 |
| `--mode links` | 获取所有链接 |
| `--mode meta` | 获取 title/description 等元数据 |

### 6.3 保存内容到文件

```bash
python browser_extract.py --port 9333 --tab <id> --mode text --save output.txt
```

---

## 7. 截图与标注

### 7.1 带编号标注的截图（看图操作必备）

```bash
python browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate
```

![截图示例](screenshot-example.png)
*截图上用红色框标出可交互元素，并显示编号*

### 7.2 其他截图模式

| 模式 | 说明 |
|------|------|
| `--full-page` | 整页截图（滚动拼接） |
| `--selector` | 元素级截图（只截取指定区域） |

---

## 8. 用户交互

### 8.1 看图点击（使用编号）

```bash
# 先截图标注
python browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate

# 根据截图中的编号点击元素
python browser_input.py --port 9333 --tab <id> --click 1
```

### 8.2 直接点击（使用 CSS 选择器）

```bash
python browser_input.py --port 9333 --tab <id> --click "#submit-button"
```

### 8.3 其他交互操作

| 操作 | 命令 |
|------|------|
| 文字输入 | `--input "#search-box" --text "关键词"` |
| 按键操作 | `--press "Enter"` |
| 滚动页面 | `--scroll 500` |
| 悬停操作 | `--hover "#menu-item"` |

---

## 9. 调试与监控

### 9.1 执行任意 JavaScript

```bash
python browser_console.py --port 9333 --tab <id> --js "document.title"
```

### 9.2 读取 Console 日志

```bash
python browser_console.py --port 9333 --tab <id> --console-log
```

### 9.3 抓取网络请求

```bash
python browser_console.py --port 9333 --tab <id> --network-log
```

---

## 10. 常见坑与解决方案

### 🔴 路径规则（极易踩坑！）

**错误做法：**
```bash
cd /project/root
python .claude/skills/browser-cdp/browser_launch.py ...  # ❌ 相对路径会错！
```

**正确做法：**
```bash
cd .claude/skills/browser-cdp
python browser_launch.py ...  # ✅ 正确
```

### 🔴 元素编号失效

- 元素编号依赖当次 DOM 扫描顺序
- **页面有明显变化后务必先重新截图/扫描再操作**
- 不要跨多次调用使用旧编号

### 🔴 SPA 路由跳转

- 页面是 SPA 时不要死等 load 事件
- 改用 `browser_watch.py --wait-url-contains` 判断路由跳转

### 🔴 登录态丢失

- 确保同一任务使用固定的 `--name` 参数
- 第一次使用时如果未登录，手动登录后同名字复用会保留登录态

---

## 11. 测试用例库

### 11.1 测试覆盖范围

| 模块 | 测试类型 | 状态 |
|------|----------|------|
| browser_launch.py | 单元测试 | ✅ 已有 2 个测试文件 |
| browser_nav.py | 单元测试 | ⚠️ 待添加 |
| browser_extract.py | 单元测试 | ⚠️ 待添加 |
| browser_screenshot.py | 单元测试 | ⚠️ 待添加 |
| browser_input.py | 单元测试 | ⚠️ 待添加 |
| browser_console.py | 单元测试 | ⚠️ 待添加 |

### 11.2 测试用例分类

#### 单元测试 (Unit Tests)
- 不启动真实浏览器，使用 mock 模拟依赖
- 测试单个函数的逻辑

#### 集成测试 (Integration Tests)
- 启动真实浏览器，测试多个模块协同
- 如：启动 -> 导航 -> 抓取 -> 关闭

#### 端到端测试 (End-to-End Tests)
- 模拟真实用户使用场景
- 如：搜索 -> 查看详情 -> 填写表单

### 11.3 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 生成覆盖率报告
pytest --cov=src --cov-report=html tests/
```

---

## 12. 总结与下一步

### 关键要点回顾

1. ✅ 始终 `cd` 到 skill 目录再运行脚本
2. ✅ 同一任务使用固定的 `--name` 保持登录态
3. ✅ 页面变化后重新截图获取新编号
4. ✅ SPA 页面用 `browser_watch.py` 判断跳转
5. ✅ 使用 `--mode text` 获取适合 AI 分析的文本

### 下一步学习建议

- 📖 阅读 `references/troubleshooting.md` 获取完整故障排查指南
- 📖 阅读 `references/workflows.md` 获取典型工作流示例
- 🧪 尝试编写自己的测试用例（参考 `browser-cdp-test-template.py`）
- 🚀 开始你的第一个自动化任务！

---

## 附录：快速参考卡

```bash
# 启动浏览器
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name my-work --start-url "https://example.com"

# 导航
cd .claude/skills/browser-cdp
python browser_nav.py --port 9333 --tab <id> --goto "https://example.com"

# 抓取文本
cd .claude/skills/browser-cdp
python browser_extract.py --port 9333 --tab <id> --mode text

# 截图标注
cd .claude/skills/browser-cdp
python browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate

# 点击元素
cd .claude/skills/browser-cdp
python browser_input.py --port 9333 --tab <id> --click 1

# 执行 JS
cd .claude/skills/browser-cdp
python browser_console.py --port 9333 --tab <id> --js "document.title"

# 关闭浏览器
cd .claude/skills/browser-cdp
python browser_launch.py --stop-dedicated my-work
```