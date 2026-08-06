# 网站兼容性测试框架

## 概述

本测试框架覆盖电商、新闻、社交、政务四类主流网站，验证 browser-cdp skill 的抓取和浏览能力。

## 测试覆盖范围

### 电商平台 (3个站点)
- **淘宝** (taobao.com) - P1, SPA应用, L2难度
- **京东** (jd.com) - P1, SPA应用, L2难度
- **拼多多** (pinduoduo.com) - P1, SPA应用, L2难度

### 新闻资讯 (3个站点)
- **新浪新闻** (news.sina.com.cn) - P0, 动态页面, L1难度
- **网易新闻** (news.163.com) - P0, 动态页面, L1难度
- **财联社** (cls.cn) - P1, 动态页面, L1-L2难度

### 社交内容 (3个站点)
- **知乎** (zhihu.com) - P0, SPA应用, L2难度
- **微博** (weibo.com) - P1, SPA应用, L2-L3难度
- **小红书** (xiaohongshu.com) - P1, SPA应用, L2-L3难度

### 政务服务 (3个站点)
- **国家政务服务平台** (gjzwfw.www.gov.cn) - P0, 动态页面, L2难度
- **中国政府网** (gov.cn) - P0, 动态页面, L1难度
- **中国裁判文书网** (wenshu.court.gov.cn) - P1, 动态页面, L2难度

## 测试场景

每个网站包含以下核心场景：

| 场景类型 | 说明 | 评估维度 |
|----------|------|----------|
| 首页访问 | 导航到目标网站 | 页面访问成功率 |
| 搜索查询 | 输入关键词搜索 | 元素定位准确率 |
| 列表提取 | 提取搜索结果列表 | 抓取成功率 |
| 详情页访问 | 点击进入详情页 | 元素定位准确率 |
| 内容提取 | 提取页面核心内容 | 抓取成功率 |
| 分页浏览 | 翻页操作 | 稳定性 |
| 登录态检测 | 检测是否需要登录 | 反检测能力 |

## 运行测试

```bash
# 运行所有兼容性测试
pytest .claude/skills/browser-cdp/tests/compatibility/ -v

# 运行特定类别测试
pytest .claude/skills/browser-cdp/tests/compatibility/test_ecommerce.py -v
pytest .claude/skills/browser-cdp/tests/compatibility/test_news.py -v
pytest .claude/skills/browser-cdp/tests/compatibility/test_social.py -v
pytest .claude/skills/browser-cdp/tests/compatibility/test_gov.py -v

# Mock模式（默认，无需真实浏览器）
pytest .claude/skills/browser-cdp/tests/compatibility/ -v --mock

# 真实浏览器模式（需要Chrome调试端口）
pytest .claude/skills/browser-cdp/tests/compatibility/ -v --no-mock
```

## 通过标准

| 优先级 | 评分阈值 | 场景成功率 |
|--------|---------|-----------|
| P0 | >= 75 | >= 80% |
| P1 | >= 65 | >= 70% |
| P2 | >= 55 | >= 60% |
| P3 | >= 50 | >= 50% |

## 技术栈差异

| 维度 | 电商平台 | 新闻资讯 | 社交内容 | 政务服务 |
|------|---------|---------|---------|---------|
| 前端框架 | React/Vue SPA | 混合渲染 | React/Vue SPA | 传统服务端渲染 |
| 数据加载 | AJAX + WebSocket | 懒加载 + 分页 | 无限滚动 + 实时推送 | 表单提交 + 分页 |
| 反爬机制 | ⭐⭐⭐⭐ 极强 | ⭐⭐ 中等 | ⭐⭐⭐ 较强 | ⭐ 弱 |
| 登录要求 | 可选 | 无需 | 常见 | 无需 |
| 动态内容 | 高 | 中 | 极高 | 低 |
| 验证码频率 | 高频 | 低频 | 中频 | 极低 |

## 文件结构

```
tests/compatibility/
├── __init__.py
├── README.md
├── test_ecommerce.py    # 电商平台测试
├── test_news.py         # 新闻资讯测试
├── test_social.py       # 社交内容测试
└── test_gov.py          # 政务服务测试
```

## 评估配置

网站配置定义在 `scripts/eval_config.py` 的 `WEBSITE_CONFIGS` 列表中。

## 扩展新网站

1. 在 `scripts/eval_config.py` 中添加 `WebsiteConfig`
2. 创建对应的测试文件 `test_<category>.py`
3. 实现各场景测试方法
4. 运行测试验证
