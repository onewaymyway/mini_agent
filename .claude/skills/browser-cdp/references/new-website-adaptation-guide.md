# 新网站适配指南

> 生成时间：2026-08-08
> 目标：提供新网站适配的完整指南，帮助快速扩展支持范围

---

## 一、适配流程概览

```mermaid
graph LR
    A[需求分析] --> B[技术调研]
    B --> C[适配器开发]
    C --> D[测试验证]
    D --> E[评估打分]
    E --> F[文档更新]
    F --> G[提交审核]
```

---

## 二、步骤详解

### 2.1 需求分析

**目标**：确定新网站的优先级和接入价值

**分析维度**：

| 维度 | 评估内容 | 权重 |
|------|----------|------|
| 数据价值 | 网站数据的稀缺性和实用性 | 30% |
| 用户频率 | 用户请求该网站的频率 | 25% |
| 技术难度 | 反爬难度和适配复杂度 | 20% |
| 覆盖范围 | 网站类型覆盖的完整性 | 15% |
| 竞争差异 | 与其他适配器的差异化 | 10% |

**优先级判定**：

| 优先级 | 判定标准 | 示例 |
|--------|----------|------|
| P0 | 高频需求 + 高数据价值 + 低难度 | 百度、知乎 |
| P1 | 中频需求 + 中数据价值 + 中难度 | 豆瓣、B站 |
| P2 | 低频需求 + 低数据价值 + 高难度 | 小众网站 |
| P3 | 实验性需求 | 新技术栈网站 |

---

### 2.2 技术调研

**调研方法**：

1. **页面结构分析**
   ```bash
   # 获取页面 HTML
   python src/core/browser_extract.py --mode html --out page.html
   
   # 分析页面结构
   python -c "from bs4 import BeautifulSoup; soup = BeautifulSoup(open('page.html'), 'html.parser'); print(soup.prettify()[:5000])"
   ```

2. **网络请求分析**
   ```bash
   # 监听网络请求
   python src/core/browser_console.py --network --tab <tab_id>
   ```

3. **反爬机制测试**
   ```bash
   # 测试 stealth 模式
   python src/core/browser_nav.py --stealth --goto "https://target.com"
   
   # 测试请求频率限制
   for i in {1..10}; do
     python src/core/browser_nav.py --goto "https://target.com/search?q=test$i"
   done
   ```

**调研输出模板**：

```markdown
# <网站名称> 技术调研报告

## 基本信息
- 域名：
- 首页 URL：
- 分类：
- 反爬等级：

## 技术特征
- 前端框架：
- 数据加载方式：
- 认证机制：

## 反爬机制
- IP 限制：
- 请求头检测：
- 指纹检测：
- 验证码类型：

## 适配策略
- 是否需要 stealth 模式：
- 是否需要代理池：
- 是否需要登录态：
- 预估难度：
```

---

### 2.3 适配器开发

**开发步骤**：

#### 步骤 1：创建配置文件

复制模板 `config/websites/template.json`，修改为：

```json
{
  "name": "网站名称",
  "domain": "domain.com",
  "url": "https://www.domain.com",
  "category": "CATEGORY",
  "subcategory": "SUBCATEGORY",
  "frontend_framework": "React/Vue/SSR/None",
  "anti_crawl_level": 2,
  "login_required": false,
  "captcha_type": "none",
  "priority": "P1",
  "timeout": 30,
  "retry_count": 3,
  "stealth_mode": true,
  "target_success_rate": 0.85,
  "target_accuracy": 0.80,
  "custom_config": {
    "search_url": "https://www.domain.com/search?q={query}",
    "result_selector": ".result-item",
    "title_selector": ".title",
    "url_selector": "a[href]"
  },
  "tags": ["标签1", "标签2"],
  "created_at": "2026-08-08T00:00:00.000Z",
  "updated_at": "2026-08-08T00:00:00.000Z"
}
```

#### 步骤 2：创建适配器类

在 `src/adapters/` 目录下创建 `<domain>_adapter.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
<domain>_adapter.py - <网站名称> 网站适配器
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.adapters.base import (
    BaseWebsiteAdapter,
    WebsiteConfig,
    AdapterResult,
    AntiCrawlLevel,
)
from src.core.browser_launch import launch_browser
from src.core.browser_nav import navigate_to
from src.core.browser_extract import extract_content
from src.core.browser_screenshot import take_screenshot


@dataclass
class <Domain>Config(WebsiteConfig):
    """<网站名称> 配置"""
    search_url: str = "https://www.<domain>.com/search?q={query}"
    result_selector: str = ".result-item"
    title_selector: str = ".title"


class <Domain>Adapter(BaseWebsiteAdapter):
    """<网站名称> 适配器"""
    
    def __init__(self, config: <Domain>Config = None):
        self._config = config or <Domain>Config(
            name="<网站名称>",
            domain="<domain>.com",
            url="https://www.<domain>.com",
            category="CATEGORY",
            subcategory="SUBCATEGORY",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            stealth_mode=True,
        )
    
    @property
    def config(self) -> WebsiteConfig:
        return self._config
    
    async def navigate(self, url: str) -> AdapterResult:
        """导航到指定 URL"""
        try:
            # 实现导航逻辑
            pass
        except Exception as e:
            return AdapterResult(success=False, error=str(e))
    
    async def search(self, query: str, **kwargs) -> AdapterResult:
        """执行搜索"""
        try:
            # 实现搜索逻辑
            pass
        except Exception as e:
            return AdapterResult(success=False, error=str(e))
    
    async def extract(self, selector: str, **kwargs) -> AdapterResult:
        """提取页面数据"""
        try:
            # 实现提取逻辑
            pass
        except Exception as e:
            return AdapterResult(success=False, error=str(e))
    
    async def screenshot(self, path: str, annotate: bool = False) -> AdapterResult:
        """截图"""
        try:
            # 实现截图逻辑
            pass
        except Exception as e:
            return AdapterResult(success=False, error=str(e))
```

#### 步骤 3：注册适配器

在 `src/registry/registry.py` 中添加：

```python
from src.adapters.<domain>_adapter import <Domain>Adapter

registry.register_adapter(<Domain>Adapter())
```

---

### 2.4 测试验证

**测试内容**：

| 测试项 | 方法 | 预期结果 |
|--------|------|----------|
| 页面加载 | `navigate()` | 成功加载首页 |
| 搜索功能 | `search("关键词")` | 返回搜索结果 |
| 数据提取 | `extract("selector")` | 提取到有效数据 |
| 截图功能 | `screenshot("path")` | 生成截图文件 |
| 反检测 | 启用 stealth 模式 | 不被封禁 |
| 错误处理 | 输入无效参数 | 返回错误信息 |

**测试命令**：
```bash
cd .claude/skills/browser-cdp
python -m pytest tests/compatibility/test_<domain>.py -v
```

---

### 2.5 评估打分

**评估维度**：

| 维度 | 权重 | 评估内容 |
|------|------|----------|
| 页面加载能力 | 20% | 加载速度、成功率 |
| 元素定位能力 | 25% | 选择器稳定性、元素可见性 |
| 数据提取能力 | 25% | 提取准确率、完整性 |
| 反检测能力 | 15% | stealth 模式效果 |
| 稳定性与恢复 | 15% | 重试机制、错误恢复 |

**评估命令**：
```bash
cd .claude/skills/browser-cdp
python scripts/run_evaluation.py --website <domain>
```

---

### 2.6 文档更新

**需要更新的文档**：

1. **SKILL.md** - 在 resources 中添加新子资源
   ```yaml
   - id: <domain>-search
     path: references/<domain>-search.md
     description: <网站名称>搜索自动化脚本完整文档
     triggers: <网站名称>搜索, <domain> search, <domain>_search.py
   ```

2. **website_support_list.json** - 添加网站支持信息
   ```json
   "<Domain>": {
     "name": "<网站名称>",
     "url": "https://www.<domain>.com",
     "category": "category",
     "priority": "P1",
     "status": "supported",
     "last_evaluated": "2026-08-08 18:00:00",
     "overall_score": 85.5,
     "dimensions": {
       "页面加载能力": 90.0,
       "元素定位能力": 85.0,
       "数据提取能力": 82.0,
       "反检测能力": 80.0,
       "稳定性与恢复": 88.0
     },
     "notes": ""
   }
   ```

3. **references/<domain>-search.md** - 创建使用文档

---

### 2.7 提交审核

**提交检查清单**：
- [ ] 适配器代码符合规范
- [ ] 测试用例通过
- [ ] 文档已更新
- [ ] 配置文件正确
- [ ] 无敏感信息泄露

**提交命令**：
```bash
cd .claude/skills/browser-cdp
git add .
git commit -m "feat: add <domain> adapter"
```

---

## 三、分类代码对照表

| 代码 | 分类 | 示例 |
|------|------|------|
| SEARCH | 搜索引擎 | Baidu, Google |
| ECOM | 电商平台 | Taobao, JD |
| NEWS | 新闻资讯 | Sina, Thp |
| SOCIAL | 社交网络 | Zhihu, Weibo |
| FINANCE | 金融数据 | EastMoney, Xueqiu |
| GOV | 政务服务 | Gov.cn |
| EDU | 教育学习 | MOOC, XuetangX |
| TRAVEL | 旅游出行 | Ctrip, Qunar |
| JOB | 招聘求职 | BossZhipin, Lagou |
| VIDEO | 视频平台 | Bilibili, Youku |
| MAP | 地图服务 | Amap, BaiduMap |
| HEALTH | 医疗健康 | Haodf, Dxy |
| AUTO | 汽车服务 | Autohome, Dongchedi |
| FOOD | 美食服务 | Meituan, Eleme |
| HOUSE | 房产服务 | Lianjia, Beike |

---

## 四、常见问题

### Q1：如何判断反爬等级？

| 等级 | 特征 | 示例 |
|------|------|------|
| 0 | 无反爬 | 大部分新闻网站 |
| 1 | 轻度 | 基础请求头检测 |
| 2 | 中度 | IP 频率限制 |
| 3 | 高度 | 指纹检测 + 验证码 |
| 4 | 极高度 | 行为分析 + 设备指纹 |
| 5 | 封锁 | 需要特殊手段 |

### Q2：如何处理登录态？

1. 使用 `--dedicated --name <session>` 启动专用浏览器
2. 手动登录一次，保存登录态
3. 后续复用同一 session

### Q3：如何优化评估得分？

1. 调整选择器，提高元素定位稳定性
2. 增加智能等待，提高页面加载成功率
3. 优化反检测配置，提高绕过成功率
4. 增加重试机制，提高错误恢复能力

---

## 五、最佳实践

### 5.1 选择器设计

- 使用稳定的 CSS 选择器，避免使用动态 ID
- 提供多个备选选择器
- 使用语义化选择器（如 `.result-item` 而非 `.div:nth-child(3)`）

### 5.2 错误处理

- 捕获所有异常，返回 `AdapterResult` 而非抛出
- 记录详细的错误信息
- 实现重试机制

### 5.3 性能优化

- 使用连接池复用浏览器实例
- 实现智能等待，避免不必要的延迟
- 批量操作减少网络请求

### 5.4 代码规范

- 遵循 PEP 8 编码规范
- 添加完整的类型注解
- 编写详细的文档字符串
- 添加单元测试

---

## 六、附录

### A. 适配器开发模板

参见 `src/adapters/base.py`

### B. 评估器开发模板

参见 `src/evaluators/base.py`

### C. 测试用例模板

参见 `tests/compatibility/test_template.py`

### D. 配置文件模板

参见 `config/websites/template.json`
