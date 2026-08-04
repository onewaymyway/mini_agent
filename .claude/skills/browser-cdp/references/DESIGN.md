# Browser CDP Skill 目录结构设计方案

## 当前状态（已迁移完成）

### 文件统计
- **总文件数**: 11,198 个
- **总大小**: 6,470.4 MB
- **核心代码**: 21 个 Python 文件
- **搜索器**: 24 个 Python 文件
- **工具函数**: 7 个 Python 文件
- **测试文件**: 37 个 Python 文件
- **参考文档**: 43 个 Markdown 文件
- **示例代码**: 8 个文件

---

## 完整目录树

```
browser-cdp/
│
├── SKILL.md                          # 主技能定义（36.8 KB）
├── ZHIHU_SEARCH_GUIDE.md             # 知乎搜索指南（4.6 KB）
├── DESIGN.md                         # 本设计文档
│
├── src/                              # 源代码根目录
│   ├── __init__.py
│   │
│   ├── core/                         # 核心模块（21 files）
│   │   ├── __init__.py
│   │   ├── cdp_client.py             # CDP 底层库
│   │   ├── cdp_connection_pool.py    # 连接池管理
│   │   ├── enhanced_cdp_session.py   # 增强 CDP 会话
│   │   ├── utils.py                  # 公共辅助函数
│   │   ├── browser_launch.py         # 浏览器启动/连接管理
│   │   ├── browser_nav.py            # 导航控制
│   │   ├── browser_extract.py        # 内容抓取
│   │   ├── browser_screenshot.py     # 截图功能
│   │   ├── browser_input.py          # 输入模拟
│   │   ├── browser_console.py        # Console/网络调试
│   │   ├── browser_watch.py          # 协作监控
│   │   ├── stealth.py                # 反检测模式
│   │   ├── request_headers.py        # 请求头伪装
│   │   ├── rate_limiter.py           # 速率控制
│   │   ├── proxy_pool.py             # 代理池管理
│   │   ├── captcha_handler.py        # 验证码处理
│   │   ├── smart_wait.py             # 智能等待
│   │   ├── dynamic_loader.py         # 动态加载
│   │   ├── complex_dom.py            # 复杂 DOM 处理
│   │   └── retry_handler.py          # 重试处理
│   │
│   ├── searchers/                    # 搜索器模块（24 files）
│   │   ├── __init__.py
│   │   ├── base.py                   # 搜索器基类
│   │   ├── utils.py                  # 搜索器工具函数
│   │   ├── baidu_search.py           # 百度搜索
│   │   ├── bing_search.py            # Bing 搜索
│   │   ├── zhihu_search.py           # 知乎内容搜索
│   │   ├── zhihu_search_simple.py    # 知乎简化搜索
│   │   ├── zhihu_search_with_login.py # 知乎登录态搜索
│   │   ├── zhihu_hot.py              # 知乎热榜
│   │   ├── zhihu_column_search.py    # 知乎专栏搜索
│   │   ├── zhihu_publish_answer.py   # 知乎回答发布
│   │   ├── batch_zhihu_search_all.py # 知乎批量搜索
│   │   ├── arxiv_search.py           # arXiv 搜索
│   │   ├── arxiv_multi_search.py     # arXiv 批量搜索
│   │   ├── wechat_search.py          # 微信搜索
│   │   ├── jd_search.py              # 京东搜索
│   │   ├── pdd_search.py             # 拼多多搜索
│   │   ├── douban_search.py          # 豆瓣搜索
│   │   ├── sina_news.py              # 新浪财经
│   │   ├── eastmoney_guba.py         # 东方财富股吧
│   │   ├── scholar_search.py         # Google Scholar
│   │   ├── bilibili_search.py        # B站搜索
│   │   ├── boss_zhipin_search.py     # BOSS直聘
│   │   └── run_real_search_with_logged_in_browser.py # 真实登录态搜索
│   │
│   ├── utilities/                    # 工具函数（7 files）
│   │   ├── __init__.py
│   │   ├── analyze_elements.py       # 元素分析
│   │   ├── cleanup_instances.py      # 实例清理
│   │   ├── debug_regex.py            # 正则调试
│   │   ├── detail_cleaner.py         # 详情清洗
│   │   ├── launch_zhihu_logged_in.py # 知乎登录启动
│   │   └── run_zhihu_search_auto.py  # 知乎自动搜索
│   │
│   ├── browser_ops/                  # 浏览器操作（预留空目录）
│   │   └── __init__.py
│   │
│   ├── parsers/                      # 解析器（预留空目录）
│   │   └── __init__.py
│   │
│   └── scrapers/                     # 爬虫（预留空目录）
│       └── __init__.py
│
├── references/                       # 参考文档（43 files）
│   ├── python-env-detection.md       # Python 环境检测
│   ├── browser-launch-scenarios.md   # 浏览器启动场景
│   ├── workflows.md                  # 工作流示例
│   ├── troubleshooting.md            # 故障排查
│   │
│   ├── baidu-search.md               # 百度搜索文档
│   ├── bing-search.md                # Bing 搜索文档
│   ├── zhihu-search.md               # 知乎搜索文档
│   ├── zhihu-hot.md                  # 知乎热榜文档
│   ├── zhihu-column-search.md        # 知乎专栏搜索文档
│   ├── zhihu-publish-answer.md       # 知乎回答发布文档
│   ├── arxiv-search.md               # arXiv 搜索文档
│   ├── arxiv-multi-search.md         # arXiv 批量搜索文档
│   ├── wechat-search.md              # 微信搜索文档
│   ├── jd-search.md                  # 京东搜索文档
│   ├── pdd-search.md                 # 拼多多搜索文档
│   ├── douban-search.md              # 豆瓣搜索文档
│   ├── sina-news.md                  # 新浪财经文档
│   ├── eastmoney-guba.md             # 东方财富股吧文档
│   ├── scholar-search.md             # Google Scholar 文档
│   ├── bilibili-search.md            # B站搜索文档
│   ├── boss-zhipin-search.md         # BOSS直聘文档
│   │
│   ├── captcha-handling.md           # 验证码处理指南
│   ├── request-headers.md            # 请求头伪装文档
│   ├── rate-limiter.md               # 速率控制文档
│   ├── proxy-pool.md                 # 代理池文档
│   └── searchers-guide.md            # 搜索器使用指南
│
├── tests/                           # 测试套件（37 files）
│   ├── __init__.py
│   ├── conftest.py                  # pytest 配置
│   │
│   ├── unit/                        # 单元测试（9 files）
│   │   ├── test_cdp_client.py
│   │   ├── test_browser_launch.py
│   │   ├── test_browser_nav.py
│   │   ├── test_browser_extract.py
│   │   ├── test_browser_screenshot.py
│   │   ├── test_browser_input.py
│   │   ├── test_browser_console.py
│   │   ├── test_browser_watch.py
│   │   └── test_utils.py
│   │
│   ├── integration/                 # 集成测试（2 files）
│   │   ├── test_ecommerce_flow.py
│   │   └── test_social_media.py
│   │
│   ├── e2e/                         # 端到端测试（1 file）
│   │   └── test_full_workflow.py
│   │
│   ├── templates/                   # 模板测试（12 files）
│   │   ├── base_test_template.py
│   │   ├── test_anti_crawl.py
│   │   ├── test_bilibili_search.py
│   │   ├── test_boss_zhipin_search.py
│   │   ├── test_browser_console_template.py
│   │   ├── test_browser_watch_template.py
│   │   ├── test_dynamic_content.py
│   │   ├── test_ecommerce_flow_template.py
│   │   ├── test_form_submission.py
│   │   ├── test_news_extraction.py
│   │   ├── test_search_engine.py
│   │   └── test_social_media_template.py
│   │
│   ├── fixtures/                    # 测试数据（预留）
│   │
│   └── support/                     # 测试支持（4 files）
│       ├── __init__.py
│       ├── exception_handler.py
│       ├── test_logger.py
│       └── test_reporter.py
│   │
│   ├── test_browser_cdp_dedicated_port_fallback.py
│   ├── test_browser_cdp_detect_running.py
│   ├── test_captcha_handler.py
│   ├── test_edge_cases.py
│   ├── test_enhanced_modules.py
│   ├── test_logging_demo.py
│   ├── test_searchers.py
│   └── test_website_types.py
│
├── examples/                        # 示例代码（8 files）
│   ├── basic_usage.py               # 基础使用示例
│   ├── search_examples.py           # 搜索器示例
│   ├── screenshot_examples.py       # 截图示例
│   ├── form_fill_examples.py        # 表单填写示例
│   ├── collaboration_examples.py    # 协作模式示例
│   ├── debug_examples.py            # 调试示例
│   └── browser_extension_example/   # 浏览器扩展示例
│       ├── manifest.json
│       ├── background.js
│       ├── popup.html
│       └── popup.js
│
├── temp/                            # 临时文件（运行时生成，166 files, 55.3 MB）
├── temp_cdp/                        # CDP 浏览器数据（运行时生成，8276 files, 1661.8 MB）
├── temp_data/                       # 数据缓存（运行时生成，2577 files, 4751.8 MB）
├── test_reports/                    # 测试报告（运行时生成，30 files, 89.5 KB）
└── search_results/                  # 搜索结果（运行时生成，3 files, 57.9 KB）
```

---

## 模块职责说明

### src/core/ - 核心模块（21 files）

| 文件 | 职责 |
|------|------|
| `cdp_client.py` | CDP 协议底层封装，WebSocket 连接管理 |
| `cdp_connection_pool.py` | 连接池管理，复用 CDP 连接 |
| `enhanced_cdp_session.py` | 增强 CDP 会话，封装常用操作 |
| `utils.py` | 公共辅助函数（等待、选择器、日志等） |
| `browser_launch.py` | 浏览器启动、连接、实例管理 |
| `browser_nav.py` | 页面导航、前进后退、刷新 |
| `browser_extract.py` | 内容抓取（HTML/文本/元素/表单/链接） |
| `browser_screenshot.py` | 截图功能（整页/元素/标注） |
| `browser_input.py` | 输入模拟（点击/输入/按键/滚动/悬停） |
| `browser_console.py` | Console 日志、网络请求抓取 |
| `browser_watch.py` | 协作监控（URL/标题变化检测） |
| `stealth.py` | 反检测模式（webdriver 移除、指纹模拟） |
| `request_headers.py` | 请求头伪装（Sec-Fetch-*、动态 Referer） |
| `rate_limiter.py` | 请求速率控制（令牌桶/漏桶/熔断器） |
| `proxy_pool.py` | 代理池管理（健康检查、故障转移） |
| `captcha_handler.py` | 验证码处理（滑块/点选/文字） |
| `smart_wait.py` | 智能等待策略（networkidle/stable/selector） |
| `dynamic_loader.py` | 动态内容加载（无限滚动） |
| `complex_dom.py` | 复杂 DOM 处理（Shadow DOM/iframe） |
| `retry_handler.py` | 重试处理（指数退避、熔断器） |

### src/searchers/ - 搜索器模块（24 files）

| 文件 | 职责 |
|------|------|
| `base.py` | 搜索器抽象基类，定义统一接口 |
| `utils.py` | 搜索器工具函数（去重、结果格式化） |
| `baidu_search.py` | 百度搜索自动化 |
| `bing_search.py` | Bing 搜索自动化 |
| `zhihu_search.py` | 知乎内容搜索 |
| `zhihu_search_simple.py` | 知乎简化搜索 |
| `zhihu_search_with_login.py` | 知乎登录态搜索 |
| `zhihu_hot.py` | 知乎热榜抓取 |
| `zhihu_column_search.py` | 知乎专栏文章批量搜索 |
| `zhihu_publish_answer.py` | 知乎问题回答发布 |
| `batch_zhihu_search_all.py` | 知乎批量搜索 |
| `arxiv_search.py` | arXiv 论文搜索 |
| `arxiv_multi_search.py` | arXiv 多关键词批量搜索 |
| `wechat_search.py` | 微信公众号文章搜索 |
| `jd_search.py` | 京东商品搜索 |
| `pdd_search.py` | 拼多多商品搜索 |
| `douban_search.py` | 豆瓣书籍/电影/音乐搜索 |
| `sina_news.py` | 新浪财经新闻抓取 |
| `eastmoney_guba.py` | 东方财富股吧帖子抓取 |
| `scholar_search.py` | Google Scholar 论文搜索 |
| `bilibili_search.py` | B站视频/UP主搜索 |
| `boss_zhipin_search.py` | BOSS直聘职位搜索 |
| `run_real_search_with_logged_in_browser.py` | 真实登录态搜索运行器 |

### src/utilities/ - 工具函数（7 files）

| 文件 | 职责 |
|------|------|
| `analyze_elements.py` | 元素分析工具 |
| `cleanup_instances.py` | 实例清理工具 |
| `debug_regex.py` | 正则调试工具 |
| `detail_cleaner.py` | 详情清洗工具 |
| `launch_zhihu_logged_in.py` | 知乎登录态启动工具 |
| `run_zhihu_search_auto.py` | 知乎自动搜索运行器 |

---

## 测试覆盖统计

| 测试类型 | 文件数 | 状态 |
|---------|--------|------|
| 单元测试 | 9 | ✅ 已迁移 |
| 集成测试 | 2 | ✅ 已迁移 |
| 端到端测试 | 1 | ✅ 已迁移 |
| 模板测试 | 12 | ✅ 已迁移 |
| 测试支持 | 4 | ✅ 已迁移 |
| 根目录测试 | 9 | ✅ 已迁移 |
| **合计** | **37** | **✅ 全部迁移** |

---

## 空目录说明

以下目录当前为空，保留用于未来扩展：

- `src/browser_ops/` - 浏览器操作（预留）
- `src/parsers/` - 解析器（预留）
- `src/scrapers/` - 爬虫（预留）
- `tests/fixtures/` - 测试数据（预留）

---

## 迁移完成确认

### 核心模块
- ✅ 21 个 Python 文件已迁移至 `src/core/`
- ✅ 导入路径已更新为 `src.core.*`

### 搜索器模块
- ✅ 24 个 Python 文件已迁移至 `src/searchers/`
- ✅ 导入路径已更新为 `src.searchers.*`

### 工具函数
- ✅ 7 个 Python 文件已迁移至 `src/utilities/`
- ✅ 导入路径已更新为 `src.utilities.*`

### 测试套件
- ✅ 37 个 Python 文件已迁移至 `tests/`
- ✅ 测试目录结构已优化

### 参考文档
- ✅ 43 个 Markdown 文件已迁移至 `references/`
- ✅ SKILL.md 中资源引用已更新

---

## 下一步建议

1. **清理临时文件** - 清理 `temp/`, `temp_cdp/`, `temp_data/` 目录
2. **创建 README.md** - 添加项目说明文档
3. **运行完整测试** - 验证所有测试通过
4. **更新 SKILL.md** - 确保所有路径引用正确

---

**设计完成时间**: 2026-08-03
**设计者**: Agnes (Sapiens AI)
