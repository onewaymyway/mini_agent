# Browser-CDP Skill - 通用爬虫系统

## 概述

Browser-CDP Skill 是一个基于 Chrome DevTools Protocol (CDP) 的浏览器自动化工具集，支持网站搜索、数据抓取和内容提取。

## 新增模块

### 1. 认证管理 (`src/core/auth_module.py`)

**AuthManager** - 会话认证管理器
- 支持多种认证方式（Cookie、Token、OAuth）
- 会话状态追踪与恢复
- 自动刷新过期Token
- 多账户并发支持

**核心API：**
```python
from core.auth_module import AuthManager

# 创建认证管理器
auth = AuthManager(storage_dir="./sessions")

# 保存会话
await auth.save_session("zhihu", cookies_dict, token="xxx")

# 加载会话
session = await auth.load_session("zhihu")

# 检查有效性
is_valid = await auth.is_session_valid("zhihu")
```

### 2. 内容服务 (`src/core/content_service.py`)

**ContentDetailService** - 内容详情提取服务
- 文章/新闻内容提取
- 结构化数据解析
- 内容质量评估
- 去重与摘要生成

**核心API：**
```python
from core.content_service import ContentDetailService

service = ContentDetailService()
result = await service.extract_content(url, content_type="article")
print(result.title)
print(result.content[:500])
```

### 3. 通用爬虫 (`src/searchers/universal_crawler.py`)

**UniversalCrawler** - 通用页面爬虫
- 自动检测内容类型（文章、商品、图片等）
- 基于CSS选择器的内容提取
- 批量爬取与速率控制
- 结果持久化存储

**支持的内容类型：**
- `ContentType.ARTICLE` - 文章/博客
- `ContentType.PRODUCT` - 商品/电商
- `ContentType.IMAGE` - 图片集
- `ContentType.VIDEO` - 视频
- `ContentType.JOB` - 招聘
- `ContentType.NEWS` - 新闻

**核心API：**
```python
from searchers.universal_crawler import UniversalCrawler, ContentType
from pathlib import Path

# 创建爬虫实例
crawler = UniversalCrawler(
    browser=browser_instance,
    storage_dir=Path("./crawl_results")
)

# 爬取单个页面
result = await crawler.crawl(
    url="https://example.com/article",
    content_type=ContentType.ARTICLE
)
print(f"标题: {result.title}")
print(f"作者: {result.author}")
print(f"图片数: {len(result.images)}")

# 批量爬取
urls = ["https://example.com/a", "https://example.com/b"]
results = await crawler.batch_crawl(urls, delay=1.0)

# 查看统计
stats = crawler.get_stats()
print(f"成功率: {stats['success']}/{stats['total']}")
```

### 4. 数据存储 (`src/data/pipeline.py`)

**DataPipeline** - 数据处理管道
- SQLite持久化存储
- 数据质量校验
- 增量更新支持
- 查询与分析接口

### 5. Web接口 (`src/core/web_interface.py`)

**FastAPI REST API** - 程序化调用接口
- RESTful API设计
- 批量任务处理
- 实时监控面板
- 数据导出功能

**启动服务：**
```bash
# 启动API服务
python -m uvicorn src.core.web_interface:app --reload --port 8000

# API端点
GET  /api/health          # 健康检查
POST /api/crawl           # 执行爬取
GET  /api/results         # 查询结果
GET  /api/stats           # 统计信息
```

## 项目结构

```
src/
├── __init__.py
├── test_imports.py      # 模块导入测试
├── core/
│   ├── __init__.py
│   ├── auth_module.py   # 认证管理
│   ├── content_service.py # 内容服务
│   ├── web_interface.py  # Web API
│   └── ... (其他核心模块)
├── data/
│   ├── __init__.py
│   └── pipeline.py       # 数据存储管道
├── searchers/
│   ├── __init__.py
│   └── universal_crawler.py  # 通用爬虫
└── ... (其他模块)
```

## 快速开始

### 1. 环境准备

```bash
# 进入browser-cdp目录
cd .claude/skills/browser-cdp

# 安装依赖
pip install -e .

# 运行导入测试
python src/test_imports.py
```

### 2. 基本使用

```python
import asyncio
from src.searchers.universal_crawler import UniversalCrawler, ContentType
from src.core.auth_module import AuthManager
from pathlib import Path

async def main():
    # 初始化认证管理器
    auth = AuthManager(storage_dir="./sessions")
    
    # 初始化爬虫
    crawler = UniversalCrawler(
        browser=browser,  # 已初始化的浏览器实例
        storage_dir=Path("./results")
    )
    
    # 爬取内容
    result = await crawler.crawl(
        url="https://example.com/article",
        content_type=ContentType.ARTICLE
    )
    
    print(f"标题: {result.title}")
    print(f"内容长度: {len(result.content)}")

asyncio.run(main())
```

### 3. 启动Web服务

```bash
# 开发模式
uvicorn src.core.web_interface:app --reload --port 8000

# 生产模式
gunicorn src.core.web_interface:app -w 4 -k uvicorn.workers.UvicornWorker
```

## 技术特点

### 智能内容检测
- 基于URL模式识别内容类型
- 基于HTML特征二次验证
- 自动选择最优提取策略

### 容错设计
- 超时自动重试
- 网络错误降级处理
- 部分成功容忍机制

### 性能优化
- 批量任务并行处理
- 请求间隔智能控制
- 本地缓存加速查询

### 数据质量
- HTML清洗与规范化
- 重复内容检测
- 内容完整性校验

## 测试

```bash
# 运行所有测试
pytest tests/ -v

# 仅运行导入测试
python src/test_imports.py

# 单元测试
pytest tests/unit/ -v

# 集成测试
pytest tests/integration/ -v
```

## 配置

```python
# 爬虫配置
crawler_config = {
    "timeout": 30,           # 页面加载超时(秒)
    "wait_time": 2.0,        # 页面等待时间(秒)
    "delay": 1.0,            # 批量爬取间隔(秒)
    "max_retries": 3,        # 最大重试次数
    "storage_format": "json", # 存储格式
}

# 认证配置
auth_config = {
    "session_ttl": 3600,     # 会话有效期(秒)
    "auto_refresh": True,    # 自动刷新
    "max_concurrent": 5,     # 最大并发数
}
```

## 扩展开发

### 添加新的内容类型

```python
# 在 ContentType 枚举中添加
class ContentType(Enum):
    # ... 现有类型 ...
    REVIEW = "review"
    COMMENT = "comment"

# 在 ContentExtractor 中添加提取逻辑
@classmethod
def extract_review(cls, html: str, domain: str) -> Dict[str, Any]:
    # 实现提取逻辑
    pass
```

### 自定义数据存储

```python
from data.pipeline import DataStorage

class MyCustomStorage(DataStorage):
    def save_result(self, result, format="json"):
        # 自定义保存逻辑
        pass
```

## 注意事项

1. **遵守robots.txt** - 爬虫应尊重网站的爬取规则
2. **控制请求速率** - 避免对目标服务器造成压力
3. **合法使用** - 仅用于数据研究和分析目的
4. **隐私保护** - 不存储敏感个人信息

## 许可证

本项目遵循项目整体许可证。

## 贡献

欢迎提交Issue和Pull Request！
