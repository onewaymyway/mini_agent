# Browser Web Operation Skill

通用网页操作技能，基于 browser-cdp 核心能力，提供开箱即用的网页自动化解决方案。

## 核心能力

- **页面导航**：打开 URL、前进后退、刷新
- **元素交互**：点击、输入、选择、悬停、拖拽
- **表单提交**：填写表单、上传文件、提交
- **数据提取**：HTML/文本/表格/链接提取
- **截图分析**：编号标注、元素定位、视觉验证
- **多标签页**：标签管理、批量操作
- **文件下载**：下载监听、进度监控

## 快速开始

```bash
# 1. 启动浏览器
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name web_op --start-url "https://example.com"

# 2. 导航
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com/page" --wait-for networkidle

# 3. 截图标注
python src/core/browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate

# 4. 交互操作
python src/core/browser_input.py --port 9333 --tab <id> --click-index 3
python src/core/browser_input.py --port 9333 --tab <id> --type-index 5 --text "hello"

# 5. 提取数据
python src/core/browser_extract.py --port 9333 --tab <id> --mode text --save content.txt

# 6. 关闭浏览器
python src/core/browser_launch.py --stop-dedicated web_op
```

## 目录结构

```
browser-web-operation/
├── SKILL.md              # 主技能文件
├── README.md             # 本文件
├── references/           # 参考文档
│   ├── quick-start.md    # 快速开始指南
│   ├── element-interaction.md  # 元素交互详解
│   ├── form-automation.md      # 表单自动化
│   ├── data-extraction.md      # 数据提取
│   ├── screenshot-analysis.md  # 截图分析
│   └── workflow-patterns.md    # 工作流模式
└── examples/             # 示例脚本
    └── login_and_scrape.py
```

## 依赖

本 skill 依赖 browser-cdp skill，无需额外安装依赖。

```bash
pip install websocket-client requests pillow
```

## 使用场景

- 填写并提交网页表单
- 登录网站并抓取数据
- 批量操作多个页面
- 自动化测试网页功能
- 数据抓取和监控

## 参考文档

- [快速开始](references/quick-start.md)
- [元素交互](references/element-interaction.md)
- [表单自动化](references/form-automation.md)
- [数据提取](references/data-extraction.md)
- [截图分析](references/screenshot-analysis.md)
- [工作流模式](references/workflow-patterns.md)
