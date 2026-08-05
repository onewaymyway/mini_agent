---
name: browser-cdp
description: 通过 Chrome DevTools Protocol (CDP) 控制真实 Chrome/Edge 浏览器：打开网页、抓取网页内容（HTML/纯文本/表单/链接）、截图（含编号标注可交互元素）、模拟点击和输入、执行JS、读取console/网络日志，并支持与用户同时操作同一个浏览器（观察/建议/代劳三种协作模式）。增强版支持：智能等待策略（networkidle/route/stable）、自动重试与熔断、无限滚动加载、Shadow DOM/iframe处理、反检测模式。当用户说"帮我打开网页"、"抓取这个网站"、"帮我填一下这个表单"、"看看我浏览器里这个页面"、"截个图分析一下"时使用。
triggers: 浏览器, 打开网页, 抓取网页, 网页截图, cdp, chrome devtools, 模拟点击, 模拟输入, 网页自动化, 填表单, browser automation, scrape webpage
platforms: windows, macos, linux, pc
resources:
  - id: python-env-detection
    path: references/python-env-detection.md
    description: python/python3 命令可用性检测的完整原因说明与自动检测脚本（本环境结论已在正文给出，一般无需展开，环境变了/检测结果对不上时再加载）
    triggers: python3, python 不是内部或外部命令, 找不到python, ModuleNotFoundError
  - id: browser-launch-scenarios
    path: references/browser-launch-scenarios.md
    description: 三种建立浏览器连接的场景完整说明（专用新实例/连接用户已登录窗口/无GUI headless），含启动失败清理策略细节
    triggers: 连接现有浏览器, 已登录的浏览器, remote-debugging-port, headless, 场景B, 场景C, 调试端口, 启动失败
  - id: workflows
    path: references/workflows.md
    description: 四类典型工作流完整示例——抓取内容、看图点击填表单、与用户协作三种介入程度、调试网页console/网络
    triggers: 表单填写, 看图操作, 协作模式, 观察模式, 代劳模式, 调试网页, console日志, 网络请求
  - id: troubleshooting
    path: references/troubleshooting.md
    description: 路径规则详解（skill 目录为 cwd 基准）与截图/DPR/SPA路由/元素编号失效等常见坑
    triggers: 找不到目录, 路径错误, DPR, SPA, 编号失效, temp_data
  - id: baidu-search
    path: references/baidu-search.md
    description: 百度搜索自动化脚本（baidu_search.py）完整文档：参数、输出格式、核心实现要点
    triggers: 百度搜索, baidu search, baidu_search.py
  - id: bing-search
    path: references/bing-search.md
    description: Bing 搜索自动化脚本（bing_search.py）完整文档：参数、输出格式、核心实现要点
    triggers: bing搜索, bing search, bing_search.py
  - id: zhihu-search
    path: references/zhihu-search.md
    description: 知乎内容搜索自动化脚本（zhihu_search.py），通过百度 site:zhihu.com 获取知乎问答和专栏
    triggers: 知乎搜索, zhihu search, zhihu_search.py
  - id: zhihu-hot
    path: references/zhihu-hot.md
    description: 知乎热榜抓取自动化脚本（zhihu_hot.py），支持免登录发现页和登录态热榜抓取
    triggers: 知乎热榜, zhihu hot, zhihu_hot.py
  - id: zhihu-column-search
    path: references/zhihu-column-search.md
    description: 知乎专栏文章批量搜索与抓取脚本（zhihu_column_search.py），通过百度 site:zhihu.com 搜索专栏文章并抓取详情
    triggers: 知乎专栏, zhihu column, zhihu_column_search.py
  - id: zhihu-publish-answer
    path: references/zhihu-publish-answer.md
    description: 知乎问题回答发布自动化脚本，通过已登录的浏览器实例在知乎问题下撰写并发布回答。
    triggers: 知乎发布, 知乎写回答, zhihu publish answer, 发布知乎回答, 知乎问答发布
  - id: arxiv-search
    path: references/arxiv-search.md
    description: arXiv 论文搜索自动化脚本（arxiv_search.py），按关键词搜索最新论文列表和获取详情
    triggers: arxiv搜索, arxiv论文, arxiv_search.py
  - id: arxiv-multi-search
    path: references/arxiv-multi-search.md
    description: arXiv 多关键词批量搜索脚本（arxiv_multi_search.py），支持合并去重、批量获取详情
    triggers: arxiv多关键词, arxiv批量搜索, arxiv_multi_search.py
  - id: wechat-search
    path: references/wechat-search.md
    description: 微信公众号文章搜索自动化脚本（wechat_search.py），通过搜狗微信搜索获取公众号文章并抓取详情
    triggers: 微信搜索, 微信公众号, wechat search, wechat_search.py, 搜狗微信
  - id: jd-search
    path: references/jd-search.md
    description: 京东商品搜索自动化脚本（jd_search.py），支持关键词搜索、价格/销量/评价提取
    triggers: 京东搜索, jd search, 商品搜索, 价格抓取, jd_search.py
  - id: pdd-search
    path: references/pdd-search.md
    description: 拼多多商品搜索自动化脚本（pdd_search.py），支持关键词搜索、价格/销量提取
    triggers: 拼多多搜索, pdd search, 拼多多, 商品搜索, pdd_search.py
  - id: douban-search
    path: references/douban-search.md
    description: 豆瓣搜索自动化脚本（douban_search.py），支持书籍/电影/音乐搜索，获取评分和评价数
    triggers: 豆瓣搜索, douban search, 豆瓣, 书籍搜索, 电影搜索, douban_search.py
  - id: sina-news
    path: references/sina-news.md
    description: 新浪财经新闻抓取脚本（sina_news.py），支持多分类新闻获取和RSS解析
    triggers: 新浪财经, sina news, 财经新闻, 新闻抓取, sina_news.py
  - id: eastmoney-guba
    path: references/eastmoney-guba.md
    description: 东方财富股吧帖子抓取脚本（eastmoney_guba.py），支持按股票代码搜索帖子和评论树
    triggers: 东方财富股吧, 股吧抓取, 帖子抓取, 评论树, 热度排序, eastmoney_guba.py
  - id: scholar-search
    path: references/scholar-search.md
    description: Google Scholar 学术论文搜索脚本（scholar_search.py），支持标题/作者/摘要/引用数提取
    triggers: Google Scholar, scholar search, 学术论文, 论文搜索, scholar_search.py
  - id: captcha-handling
    path: references/captcha-handling.md
    description: 验证码处理与反检测指南：滑块/点选/文字验证码处理、reCAPTCHA/hCaptcha 应对、反检测模式配置、常见反爬场景策略
    triggers: 验证码, 反爬, 反检测, stealth, captcha, 滑块验证, 点选验证, OCR
  - id: request-headers
    path: references/request-headers.md
    description: 请求头伪装模块（request_headers.py）完整文档：按站点自定义请求头、Sec-Fetch-* 现代浏览器头、预定义站点配置、动态 Referer 生成
    triggers: 请求头, request headers, Sec-Fetch, Referer, 反爬, 伪装
  - id: rate-limiter
    path: references/rate-limiter.md
    description: 请求速率控制模块（rate_limiter.py）完整文档：令牌桶/漏桶/固定窗口算法、指数退避重试、熔断器模式
    triggers: 速率控制, rate limit, 令牌桶, 熔断器, 重试, 反爬
  - id: proxy-pool
    path: references/proxy-pool.md
    description: 代理池管理模块（proxy_pool.py）完整文档：HTTP/SOCKS5 代理轮换、健康检查、自动故障转移、按健康度/轮询/随机策略选择
    triggers: 代理池, proxy pool, 代理轮换, 健康检查, 故障转移, 反爬
  - id: target-domain-research
    path: references/target-domain-research.md
    description: 目标领域技术调研报告：小红书/携程/大众点评反爬机制对比与适配优先级分析
    triggers: 小红书反爬, 携程反爬, 大众点评反爬, 适配优先级, 技术调研
  - id: ctrip-research
    path: references/ctrip-research.md
    description: 携程技术特征专项调研：sign 签名机制、Cookie 管理、验证码处理与 browser-cdp 适配评估
    triggers: 携程搜索, ctrip search, 携程爬虫, sign 签名, 旅游数据抓取
  - id: dianping-research
    path: references/dianping-research.md
    description: 大众点评技术特征专项调研：_token/WEBDFPID/mtgsig 三重验证、动态字体加密与适配策略
    triggers: 大众点评搜索, dianping search, 大众点评爬虫, _token, 字体加密
  - id: xiaohongshu-research
    path: references/xiaohongshu-research.md
    description: 小红书技术特征专项调研：x-s/x-s-common 签名机制、设备指纹、Cookie 时效与适配策略
    triggers: 小红书搜索, xiaohongshu search, 小红书爬虫, x-s 签名, 设备指纹
  - id: bilibili-search
    path: references/bilibili-search.md
    description: B站视频/UP主搜索自动化脚本（bilibili_search.py），支持关键词搜索、视频信息提取
    triggers: B站搜索, bilibili search, bilibili_search.py, B站视频搜索
  - id: boss-zhipin-search
    path: references/boss-zhipin-search.md
    description: BOSS直聘职位搜索自动化脚本（boss_zhipin_search.py），支持关键词搜索、职位信息提取
    triggers: BOSS直聘搜索, boss zhipin search, boss_zhipin_search.py, 职位搜索
  - id: github-search
    path: references/github-search.md
    description: GitHub 代码仓库/Issue/PR/代码/用户搜索自动化脚本（github_search.py），支持多类型搜索和详情抓取
    triggers: GitHub搜索, github search, github_search.py, 代码仓库, Issue搜索, PR搜索
  - id: stackoverflow-search
    path: references/stackoverflow-search.md
    description: Stack Overflow 技术问题搜索自动化脚本（stackoverflow_search.py），支持问题搜索和答案提取
    triggers: Stack Overflow搜索, stackoverflow search, stackoverflow_search.py, 技术问题, 问答搜索
  - id: taobao-search
    path: references/taobao-search.md
    description: 淘宝/天猫商品搜索自动化脚本（taobao_search.py），支持商品搜索、价格/销量/评价提取
    triggers: 淘宝搜索, 天猫搜索, taobao search, tmall search, taobao_search.py, 商品搜索
  - id: searchers-guide
    path: references/searchers-guide.md
    description: 搜索器使用指南：所有搜索器的快速开始、参数说明、输出格式、错误处理、最佳实践
    triggers: 搜索器, 搜索指南, searchers guide, 批量搜索, 结果保存
  - id: lianjia-search
    path: references/lianjia-search.md
    description: 链家房产搜索自动化脚本（lianjia_search.py），支持二手房/租房/小区数据抓取
    triggers: 链家搜索, lianjia search, lianjia_search.py, 房产搜索, 房源抓取
  - id: xueqiu-search
    path: references/xueqiu-search.md
    description: 雪球金融数据搜索自动化脚本（xueqiu_search.py），支持行情/讨论/组合持仓抓取
    triggers: 雪球搜索, xueqiu search, xueqiu_search.py, 股票行情, 金融数据
  - id: cls-news
    path: references/cls-news.md
    description: 财联社新闻搜索自动化脚本（cls_news.py），支持电报流/分类新闻/关键词搜索
    triggers: 财联社搜索, cls news, cls_news.py, 财经新闻, 电报抓取
  - id: thp-news
    path: references/thp-news.md
    description: 澎湃新闻新闻搜索自动化脚本（thp_news.py），支持时政/财经/天下/观察分类搜索
    triggers: 澎湃新闻, thp news, thp_news.py, 时政新闻, 财经新闻
  - id: weibo-search
    path: references/weibo-search.md
    description: 微博搜索自动化脚本（weibo_search.py），支持热搜榜和关键词搜索
    triggers: 微博搜索, weibo search, weibo_search.py, 热搜榜, 微博话题
  - id: lagou-search
    path: references/lagou-search.md
    description: 拉勾网招聘搜索自动化脚本（lagou_search.py），支持职位搜索和详情抓取
    triggers: 拉勾网搜索, lagou search, lagou_search.py, 互联网招聘, 职位搜索
  - id: youku-search
    path: references/youku-search.md
    description: 优酷视频搜索自动化脚本（youku_search.py），支持影视剧/综艺/动漫搜索
    triggers: 优酷搜索, youku search, youku_search.py, 影视剧搜索, 视频搜索
  - id: weather-search
    path: references/weather-search.md
    description: 中国天气网搜索自动化脚本（weather_search.py），支持城市天气预报查询
    triggers: 天气搜索, weather search, weather_search.py, 天气预报, 城市天气
  - id: mooc-search
    path: references/mooc-search.md
    description: 中国大学MOOC搜索自动化脚本（mooc_search.py），支持高校课程搜索
    triggers: MOOC搜索, mooc search, mooc_search.py, 在线课程, 高校课程
  - id: browser-download
    path: references/browser-download.md
    description: 文件下载管理模块（browser_download.py）完整文档：下载事件监听、进度监控、断点续传、下载目录配置
    triggers: 文件下载, download, 下载管理, 断点续传, 下载进度
  - id: browser-form
    path: references/browser-form.md
    description: 复杂表单自动化模块（browser_form.py）完整文档：多步骤表单、动态表单、文件上传、表单验证、状态保存/恢复
    triggers: 表单自动化, 表单填写, 文件上传, 多步骤表单, 表单验证, 动态表单
  - id: browser-tabs
    path: references/browser-tabs.md
    description: 多标签页管理模块（browser_tabs.py）完整文档：标签页列表、切换、批量操作、标签页组管理
    triggers: 标签页管理, 多标签页, 批量操作, 标签页组, 标签页切换
---

# Browser CDP Skill

通过 CDP（Chrome DevTools Protocol）直接控制 Chrome 系浏览器，**不依赖 Playwright/Selenium**。
核心优势：可以连接**用户正在使用的、已登录的真实浏览器窗口**，与用户协同操作，而不是每次都起一个
干净的自动化浏览器丢失登录态。同时也支持在无 GUI 的服务器/沙盒环境里跑一个无头实例，专门做抓取。

脚本目录：`.claude/skills/browser-cdp/src/`

| 脚本 | 用途 |
|---|---|
| `core/cdp_client.py` | 底层库，其他脚本导入用，一般不直接调用 |
| `core/utils.py` | 底层库，公共辅助函数 |
| `core/browser_launch.py` | 确保/建立浏览器连接，管理 tab（列表/新建/关闭/激活） |
| `core/browser_nav.py` | 打开网址、前进后退刷新、等待元素出现 |
| `core/browser_extract.py` | 抓取内容：html/text/elements/forms/links/meta |
| `core/browser_screenshot.py` | 截图，支持整页/元素级/编号标注 |
| `core/browser_input.py` | 模拟点击、输入文字、按键、滚动、悬停 |
| `core/browser_console.py` | 执行任意 JS、抓取 console 日志、抓取网络请求 |
| `core/browser_watch.py` | 协作场景：轮询判断用户是否已完成某个操作（URL/标题变化） |
| `core/stealth.py` | 反检测模式：webdriver 移除、指纹模拟、人类化行为 |
| `core/request_headers.py` | 请求头伪装：Sec-Fetch-*、站点自定义、动态 Referer |
| `core/rate_limiter.py` | 请求速率控制：令牌桶/漏桶/固定窗口、熔断器 |
| `core/proxy_pool.py` | 代理池管理：HTTP/SOCKS5 轮换、健康检查、故障转移 |
| `core/browser_download.py` | 文件下载管理：下载事件监听、进度监控、断点续传、下载目录配置 |
| `core/browser_form.py` | 复杂表单自动化：多步骤表单、动态表单、文件上传、表单验证、状态保存/恢复 |
| `core/browser_tabs.py` | 多标签页管理：标签页列表、切换、批量操作、标签页组管理 |
| `searchers/baidu_search.py` / `searchers/bing_search.py` | 搜索引擎自动化，见下方对应子资源 |
| `searchers/zhihu_search.py` / `searchers/zhihu_hot.py` | 知乎内容/热榜抓取，见下方对应子资源 |
| `searchers/zhihu_column_search.py` | 知乎专栏文章批量搜索与抓取，见下方对应子资源 |
| `searchers/zhihu_publish_answer.py` | 知乎问题回答发布自动化脚本，通过已登录的浏览器实例在知乎问题下撰写并发布回答 |
| `searchers/arxiv_search.py` / `searchers/arxiv_multi_search.py` | arXiv 论文搜索，见下方对应子资源 |
| `searchers/wechat_search.py` | 微信公众号文章搜索（搜狗微信），见下方对应子资源 |
| `searchers/jd_search.py` | 京东商品搜索，见下方对应子资源 |
| `searchers/pdd_search.py` | 拼多多商品搜索，见下方对应子资源 |
| `searchers/douban_search.py` | 豆瓣搜索，见下方对应子资源 |
| `searchers/sina_news.py` | 新浪财经新闻抓取，见下方对应子资源 |
| `searchers/eastmoney_guba.py` | 东方财富股吧帖子抓取，见下方对应子资源 |
| `searchers/scholar_search.py` | Google Scholar 论文搜索，见下方对应子资源 |
| `searchers/bilibili_search.py` | B站视频/UP主搜索，见下方对应子资源 |
| `searchers/boss_zhipin_search.py` | BOSS直聘职位搜索，见下方对应子资源 |
| `searchers/thp_news.py` | 澎湃新闻新闻搜索，见下方对应子资源 |
| `searchers/weibo_search.py` | 微博热搜/关键词搜索，见下方对应子资源 |
| `searchers/lagou_search.py` | 拉勾网职位搜索，见下方对应子资源 |
| `searchers/youku_search.py` | 优酷视频/影视剧搜索，见下方对应子资源 |
| `searchers/weather_search.py` | 中国天气网天气预报，见下方对应子资源 |
| `searchers/mooc_search.py` | 中国大学MOOC课程搜索，见下方对应子资源 |

## 子资源（渐进式加载）

本 skill 遵循渐进式加载规范：正文只保留高频必读内容，长尾细节放在 `references/*.md`，
已在 frontmatter `resources` 中登记，激活本 skill 后可在资源清单里看到全部条目及加载状态。
命中对应 `triggers` 会自动加载；也可以不依赖关键词，主动调用
`skill_resource_load(skill_name="browser-cdp", resource_id="<id>", reason=...)` 按需加载：

| id | 内容 |
|---|---|
| `python-env-detection` | python/python3 命令检测的完整原因与自动检测脚本 |
| `browser-launch-scenarios` | 三种建立浏览器连接场景的完整细节（专用实例/连接已登录窗口/headless） |
| `workflows` | 抓取内容、看图填表单、协作模式、调试网页四类工作流的完整示例 |
| `troubleshooting` | 路径规则详解 + 截图/DPR/SPA/编号失效等常见坑 |
| `baidu-search` / `bing-search` | 对应搜索引擎自动化脚本完整文档 |
| `zhihu-search` / `zhihu-hot` | 知乎内容搜索 / 热榜抓取脚本完整文档 |
| `zhihu-column-search` | 知乎专栏文章批量搜索与抓取脚本完整文档 |
| `zhihu-publish-answer` | 知乎问题回答发布自动化脚本，通过已登录的浏览器实例在知乎问题下撰写并发布回答 |
| `arxiv-search` / `arxiv-multi-search` | arXiv 单关键词 / 多关键词批量搜索脚本完整文档 |
| `wechat-search` | 微信公众号文章搜索自动化脚本完整文档 |
| `jd-search` | 京东商品搜索自动化脚本完整文档 |
| `pdd-search` | 拼多多商品搜索自动化脚本完整文档 |
| `douban-search` | 豆瓣搜索自动化脚本完整文档 |
| `sina-news` | 新浪财经新闻抓取脚本完整文档 |
| `eastmoney-guba` | 东方财富股吧帖子抓取脚本完整文档 |
| `scholar-search` | Google Scholar 学术论文搜索脚本完整文档 |
| `bilibili-search` | B站视频/UP主搜索自动化脚本完整文档 |
| `boss-zhipin-search` | BOSS直聘职位搜索自动化脚本完整文档 |
| `thp-news` | 澎湃新闻新闻搜索自动化脚本完整文档 |
| `weibo-search` | 微博搜索自动化脚本完整文档 |
| `lagou-search` | 拉勾网招聘搜索自动化脚本完整文档 |
| `youku-search` | 优酷视频搜索自动化脚本完整文档 |
| `weather-search` | 中国天气网天气预报自动化脚本完整文档 |
| `mooc-search` | 中国大学MOOC课程搜索自动化脚本完整文档 |
| `captcha-handling` | 验证码处理与反检测指南：滑块/点选/文字验证码处理、reCAPTCHA/hCaptcha 应对、反检测模式配置、常见反爬场景策略 |
| `request-headers` | 请求头伪装模块完整文档：按站点自定义请求头、Sec-Fetch-* 现代浏览器头、预定义站点配置、动态 Referer 生成 |
| `rate-limiter` | 请求速率控制模块完整文档：令牌桶/漏桶/固定窗口算法、指数退避重试、熔断器模式 |
| `proxy-pool` | 代理池管理模块完整文档：HTTP/SOCKS5 代理轮换、健康检查、自动故障转移、按健康度/轮询/随机策略选择 |
| `searchers-guide` | 搜索器使用指南：所有搜索器的快速开始、参数说明、输出格式、错误处理、最佳实践 |
| `browser-download` | 文件下载管理模块完整文档：下载事件监听、进度监控、断点续传、下载目录配置 |
| `browser-form` | 复杂表单自动化模块完整文档：多步骤表单、动态表单、文件上传、表单验证、状态保存/恢复 |
| `browser-tabs` | 多标签页管理模块完整文档：标签页列表、切换、批量操作、标签页组管理 |

新增子功能脚本时：在 `.claude/skills/browser-cdp/references/` 下新建 `<name>.md`，并在本文件
frontmatter 的 `resources` 里登记 `id`/`path`/`description`/`triggers`——不登记就不会被加载机制发现。

## 运行前必做：Python 命令检测

**本环境同时存在 `python` 和 `python3`，只有一个可用。本环境结论：用 `python`（Anaconda），
`python3` 不可用（会弹出应用商店安装提示）。**

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name work --start-url "https://example.com"
```

若换了新环境、命令报错，或需要检测脚本，加载 `python-env-detection` 子资源。

## 前置依赖

```bash
pip install websocket-client requests pillow
```

`pillow` 只有 `browser_screenshot.py --annotate` 需要，用于在截图上画编号框。

## 第一步：确保有可连接的浏览器

**默认场景（推荐）**：Agent 拉起一个独立的专用 Chrome 实例，独立 profile + 独立调试端口
（默认 9333），不碰用户真实登录态：

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name work --start-url "https://example.com"
# 输出里会给出 port 和首个 tab id，例如 --port 9333 --tab <id>
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com"
```

常用管理：`python src/core/browser_launch.py --list-dedicated`（查看已建实例）、
`python src/core/browser_launch.py --stop-dedicated work`（用完关闭）。

**⚠️ 登录状态要跨多次调用保留，`--name` 必须每次固定不变**：`--dedicated` 的登录态持久化
依赖同一个 `--name` 对应同一个 profile 目录。同一个任务/workflow 内所有涉及浏览器的步骤
都要用**同一个固定的实例名**（比如做知乎相关任务统一用 `--name zhihu_session`），不要每次
调用都用默认值或临时想一个名字——用不同名字等于每次都是全新登录态。第一次用某个 `--name`
跑的时候如果页面显示未登录，提示用户在这个专用窗口里手动登录一次即可，之后同名字复用会
保留登录态（profile 目录固定存放在 `temp_cdp/cdp_brower_data/<name>/`）。

如果同名字复用后登录态仍然看起来"丢了"，大概率不是目录/文件被删，而是上次浏览器非正常退出
（被强制杀进程/崩溃）后残留的 Chrome 单实例锁文件（`SingletonLock` 等）阻止了这次启动正确
加载该 profile；`browser_launch.py` 每次启动新进程前都会自动清理这些锁文件，遇到这种情况
不需要手动处理，直接重新 `--dedicated --name <同一个名字>` 即可。

需要连接用户已登录的真实浏览器窗口（共享登录态），或无 GUI 服务器环境用 headless，
加载 `browser-launch-scenarios` 子资源查看完整步骤。

**先检测再启动**：`--ensure` 模式在请求的端口不通时，不会直接判定"没有可用浏览器"，会先
扫一遍系统进程找有没有其它已经在跑的、带调试端口的 chrome/edge（不限于本技能自己启动的，
包括用户手动开的、或者之前会话遗留的），找到就直接复用并提示应该用哪个端口，而不是又启动
一个新的。想单独看一下当前系统里有哪些调试浏览器在跑，用：

```bash
python src/core/browser_launch.py --list-running
```

`--dedicated` 模式对"是否已有可用实例"的判断范围是"指定 `--name` 对应的那一个专用实例"
（先查 registry.json，查不到再查 profile 目录下的锁文件兜底），不会去匹配系统里任意其它
无关的调试浏览器——这是有意为之：`--dedicated` 的语义是"这个固定名字对应固定的 profile"，
如果为了"复用一个已有浏览器"而误连到一个 profile/登录态完全不相关的实例，反而会造成更隐蔽
的问题。

## 典型工作流速览

```bash
python src/core/browser_launch.py --new "https://example.com"        # 拿到新 tab 的 id
python src/core/browser_nav.py --tab <id> --goto "https://example.com"
python src/core/browser_extract.py --tab <id> --mode text             # 纯文本正文，适合直接喂给模型分析
python src/core/browser_screenshot.py --tab <id> --out shot.png --annotate   # 编号标注截图，用于看图点击/填表单
```

大页面注意 `--max-chars`（默认 20000）截断，需要完整内容时用 `--save out.txt` 写文件。

表单填写/点击的完整"看图操作"流程、与用户协作的三种介入程度（观察/建议/代劳）、
调试网页 console/网络请求，加载 `workflows` 子资源查看完整示例。

## ⚠️ 路径规则（极易踩坑）

**所有脚本都必须先 `cd` 到 skill 目录再运行**（导入路径已更新为 `src.core.*`），这意味着
所有相对路径（包括 `./temp_data/`）都是**相对于 skill 目录**解析的，不是项目根目录。
优先使用绝对路径输出产出文件。完整规则和错误示例见 `troubleshooting` 子资源。

## 反爬机制处理

### 验证码处理

本 skill 支持检测和处理常见验证码类型：

| 验证码类型 | 支持程度 | 处理方式 |
|-----------|---------|---------|
| 滑块验证码 | ✅ 自动 | 自动计算滑动距离并执行滑动 |
| 点选验证码 | ⚠️ 部分 | 尝试识别指令并点击，失败时提示用户 |
| 文字验证码 | ⚠️ 需配置 | 需要配置 OCR API（百度/腾讯/阿里云） |
| reCAPTCHA | ❌ 手动 | 提示用户手动完成 |
| hCaptcha | ❌ 手动 | 提示用户手动完成 |
| 短信/邮箱验证码 | ❌ 手动 | 提示用户提供验证码 |

**使用示例：**

```bash
# 自动检测并处理验证码
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --handle-captcha

# 启用反检测模式 + 验证码处理
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --stealth --handle-captcha
```

**验证码处理流程：**

1. **检测阶段**：扫描页面元素、URL、文本，识别验证码类型
2. **处理阶段**：
   - 滑块验证码：自动计算缺口位置并执行滑动
   - 点选验证码：识别指令文本，点击对应元素
   - 文字验证码：调用 OCR API 识别（需配置）
3. **提示阶段**：无法自动处理时，输出明确提示供用户手动操作

### 反检测模式

启用 `--stealth` 参数可隐藏自动化特征：

```bash
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --stealth
```

**反检测功能包括：**

- 移除 `navigator.webdriver` 属性
- 模拟真实 Chrome runtime 对象
- 模拟浏览器插件信息
- 模拟真实语言设置
- 模拟真实平台信息
- 随机 User-Agent 轮换
- 人类化鼠标轨迹和打字节奏
- 请求间隔随机化

### 常见反爬场景应对

| 场景 | 症状 | 应对策略 |
|-----|------|---------|
| 请求频率过高 | 429 错误、临时封禁 | 使用 `--stealth` + 速率控制 |
| 指纹检测 | 页面异常、验证码弹出 | 启用 stealth 模式 |
| IP 封禁 | 无法访问、重定向 | 使用代理池 |
| JavaScript 检测 | 页面无法加载 | 等待 JS 执行完成后再操作 |
| 行为分析 | 验证码、封号 | 模拟人类行为（点击、滚动、打字） |
| 请求头异常 | 403/444 响应 | 使用请求头伪装模块 |

### 请求头伪装

启用 `--stealth` 时自动应用现代浏览器请求头（Sec-Fetch-*、Accept 等），也可通过 `RequestHeaderManager` 按站点自定义：

```python
from src.core.request_headers import get_header_manager, HeaderConfig

# 获取管理器
mgr = get_header_manager()

# 为特定站点配置请求头
mgr.update_config("bilibili", HeaderConfig(
    custom_headers={"X-Requested-With": "XMLHttpRequest"}
))

# 获取带 Sec-Fetch-* 的请求头
headers = mgr.get_headers("https://www.bilibili.com")
```

预定义站点配置：B站、知乎、京东、淘宝、微博。加载 `request-headers` 子资源查看完整文档。

### 请求速率控制

使用 `RateLimiter` 控制请求频率，支持令牌桶/漏桶/固定窗口三种算法：

```python
from src.core.rate_limiter import get_rate_limiter, RateLimitAlgorithm

limiter = get_rate_limiter()
limiter.set_algorithm(RateLimitAlgorithm.TOKEN_BUCKET, rate=2.0, burst=5)

# 执行请求前获取令牌
if limiter.acquire():
    await do_request()
else:
    await asyncio.sleep(limiter.get_retry_after())
```

支持指数退避重试和熔断器模式。加载 `rate-limiter` 子资源查看完整文档。

### 代理池管理

使用 `ProxyPool` 管理代理轮换，支持健康检查和自动故障转移：

```python
from src.core.proxy_pool import get_proxy_pool, ProxyInfo, ProxyType

pool = get_proxy_pool()
pool.add_proxy(ProxyInfo(host="127.0.0.1", port=8080, proxy_type=ProxyType.HTTP))

# 按健康度选择代理
proxy = pool.get_proxy_by_health_score()
```

支持 HTTP/SOCKS5 代理，按健康度/轮询/随机策略选择。加载 `proxy-pool` 子资源查看完整文档。

### 文件下载管理

使用 `browser_download.py` 管理文件下载：

```bash
# 启动下载监听
python src/core/browser_download.py --port 9333 --tab <id> --start-listener

# 触发下载
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "a.download"

# 等待下载完成
python src/core/browser_download.py --port 9333 --wait --timeout 60

# 查看下载状态
python src/core/browser_download.py --port 9333 --list
```

支持断点续传、下载进度监控、自定义下载目录。加载 `browser-download` 子资源查看完整文档。

### 复杂表单自动化

使用 `browser_form.py` 处理复杂表单：

```bash
# 填写单个字段
python src/core/browser_form.py --port 9333 --tab <id> --fill-selector "input[name='username']" --text "john"

# 文件上传
python src/core/browser_form.py --port 9333 --tab <id> --upload-file --fill-selector "input[type='file']" --file "/path/to/file.pdf"

# 批量填写表单
python src/core/browser_form.py --port 9333 --tab <id> --fill-form form_def.json

# 提交表单
python src/core/browser_form.py --port 9333 --tab <id> --submit-form --wait-for networkidle

# 保存/恢复表单状态
python src/core/browser_form.py --port 9333 --tab <id> --save-form --out saved.json
python src/core/browser_form.py --port 9333 --tab <id> --restore-form --in saved.json
```

支持多步骤表单、动态表单（AJAX 加载）、表单验证。加载 `browser-form` 子资源查看完整文档。

### 多标签页管理

使用 `browser_tabs.py` 管理多个标签页：

```bash
# 列出所有标签页
python src/core/browser_tabs.py --port 9333 --list

# 批量导航
python src/core/browser_tabs.py --port 9333 --batch-goto "url1,url2,url3"

# 批量截图
python src/core/browser_tabs.py --port 9333 --batch-screenshot --out-dir ./screenshots

# 批量提取内容
python src/core/browser_tabs.py --port 9333 --batch-extract --mode text --out-dir ./extracted

# 关闭所有标签页（保留 1 个）
python src/core/browser_tabs.py --port 9333 --close-all --keep 1
```

支持标签页组管理、批量操作。加载 `browser-tabs` 子资源查看完整文档。

## 安全与边界

- 不用于绕过验证码、自动登录他人账号、批量注册等滥用场景；涉及登录态操作时，让用户自己完成
  账号密码/验证码相关的输入。
- 涉及"提交""支付""删除""发送"等不可逆操作时，先用截图向用户确认，再执行。
- headless spawn 模式默认使用独立 profile，不会读取用户真实 Chrome 的 cookie/密码，
  纯抓取公开页面场景优先用这个模式，减少对用户账号的接触面。
- 调试端口只在需要时开启，用完可以提示用户关闭该 Chrome 窗口恢复正常模式。

## 常见坑（速查，完整版见 `troubleshooting` 子资源）

- `Page.captureScreenshot` 的 `clip` 坐标是 CSS 像素，不需要额外乘 DPR。
- 页面是 SPA 时不要死等 load 事件，用 `browser_watch.py --wait-url-contains` 判断路由跳转。
- 元素编号依赖当次 DOM 扫描顺序，页面有明显变化后务必先重新截图/扫描再操作。

## 网站类型支持矩阵

### 支持类型概览

| 网站类型 | 核心能力 | 推荐配置 | 测试状态 |
|---------|---------|---------|---------|
| 电商网站 | 商品列表、详情、搜索、分页 | `--stealth` + `networkidle` | ✅ 已验证 |
| 新闻网站 | 文章列表、内容、评论、动态加载 | `--wait-for stable` | ✅ 已验证 |
| 社交网站 | 动态流、无限滚动、Shadow DOM | `--wait-for networkidle` + `DynamicLoader` | ✅ 已验证 |
| 后台系统 | 表格、表单、AJAX、分页 | `--wait-for ajax` | ✅ 已验证 |

### 电商网站（E-commerce）

**典型特征**：商品列表分页、搜索筛选、详情页、购物车

**推荐配置**：
```bash
# 商品列表抓取
python src/core/browser_nav.py --tab <id> --goto "https://shop.example.com/products" \
    --wait-for networkidle --stealth

# 商品详情抓取
python src/core/browser_extract.py --tab <id> --mode text --max-chars 50000

# 搜索功能
python src/core/browser_input.py --tab <id> --type "关键词" --selector "input[name='q']"
python src/core/browser_input.py --tab <id> --click "button[type='submit']"
```

**已知限制**：
- 需要登录态的商品价格/库存可能无法抓取
- 动态加载的商品列表需配合 `DynamicLoader` 滚动

### 新闻网站（News）

**典型特征**：文章列表、分页、评论、RSS

**推荐配置**：
```bash
# 文章列表抓取
python src/core/browser_nav.py --tab <id> --goto "https://news.example.com" \
    --wait-for stable

# 文章内容抓取
python src/core/browser_extract.py --tab <id> --mode text --selector "article.content"

# 评论抓取
python src/core/browser_extract.py --tab <id> --mode elements --selector ".comment"
```

**已知限制**：
- 付费墙内容无法抓取
- 需要 JS 渲染的评论可能加载较慢

### 社交网站（Social）

**典型特征**：动态流、无限滚动、评论嵌套、Shadow DOM

**推荐配置**：
```bash
# 动态流抓取（含无限滚动）
python src/core/browser_nav.py --tab <id> --goto "https://social.example.com" \
    --wait-for networkidle --stealth

# 使用 DynamicLoader 滚动加载
python src/core/dynamic_loader.py --tab <id> --max-scrolls 10 --scroll-delay 0.8

# Shadow DOM 内容抓取
python src/core/browser_extract.py --tab <id> --mode elements --include-shadow
```

**已知限制**：
- 大量 Shadow DOM 嵌套可能影响抓取效率
- 需要登录态的内容无法访问
- 反爬机制较强，建议启用 `--stealth`

### 后台系统（Admin/Backend）

**典型特征**：数据表格、表单、权限控制、AJAX 加载

**推荐配置**：
```bash
# 等待 AJAX 完成
python src/core/browser_nav.py --tab <id> --goto "https://admin.example.com/dashboard" \
    --wait-for ajax --timeout 30

# 表格数据抓取
python src/core/browser_extract.py --tab <id> --mode elements --selector "table.data-grid"

# 表单填写
python src/core/browser_input.py --tab <id> --type "值" --selector "input[name='field']"
python src/core/browser_input.py --tab <id> --click "button[type='submit']"
```

**已知限制**：
- 需要登录认证，需使用 `--dedicated --name` 保留登录态
- 权限控制可能导致部分数据不可见
- CSRF Token 需要特殊处理

## 配置指南

### 快速开始

```bash
# 1. 启动浏览器
python src/core/browser_launch.py --dedicated --name my_task --start-url "https://example.com"

# 2. 导航（启用反检测）
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --stealth --handle-captcha

# 3. 抓取内容
python src/core/browser_extract.py --tab <id> --mode text

# 4. 关闭浏览器
python src/core/browser_launch.py --stop-dedicated my_task
```

### 高级配置

#### 自定义 User-Agent
```bash
python src/core/browser_nav.py --tab <id> --goto "https://example.com" \
    --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
```

#### 配置 OCR API（文字验证码）
```python
from src.core.captcha_handler import CaptchaHandler

def my_ocr(image_bytes: bytes) -> str:
    # 调用你的 OCR API
    return "识别结果"

handler = CaptchaHandler(session, ocr_api=my_ocr)
result = await handler.handle_captcha()
```

#### 自定义等待策略
```python
from src.core.smart_wait import SmartWait, WaitConfig

config = WaitConfig(
    timeout=30,
    poll_interval=0.5,
    strategies=["networkidle", "stable", "selector"]
)
wait = SmartWait(session, config)
await wait.wait_for("networkidle")
```

### 故障排查

| 问题 | 可能原因 | 解决方案 |
|-----|---------|---------|
| 页面加载超时 | 网络慢/反爬检测 | 增加 `--timeout`，启用 `--stealth` |
| 验证码无法处理 | 复杂验证码类型 | 使用 `--handle-captcha` 查看提示，手动处理 |
| 内容抓取为空 | SPA 未渲染完成 | 使用 `--wait-for networkidle` 或 `stable` |
| 元素找不到 | 动态加载/Shadow DOM | 使用 `ComplexDOMHandler` 或增加等待时间 |
| IP 被封禁 | 请求频率过高 | 降低频率，使用代理池 |

## 已知限制

### 技术限制

1. **OCR 识别**：文字验证码需要外部 OCR API，当前不提供内置 OCR
2. **reCAPTCHA/hCaptcha**：无法自动处理，需手动完成或使用专业服务
3. **图像对比**：滑块验证码距离计算为简化算法，复杂场景可能不准确
4. **Shadow DOM**：深层嵌套可能影响抓取效率

### 法律与道德边界

1. 不得用于绕过安全验证的恶意用途
2. 不得用于批量注册、刷量等滥用场景
3. 遵守目标网站的 robots.txt 和服务条款
4. 控制请求频率，避免对目标服务器造成压力

### 性能限制

1. 单次抓取建议不超过 50000 字符（可通过 `--max-chars` 调整）
2. 无限滚动建议限制最大滚动次数（默认 10 次）
3. 并发请求需自行控制，避免触发反爬机制

## 搜索器模块

搜索器模块（`src/searchers/`）提供结构化的网站数据抓取能力，所有搜索器继承 `BaseSearcher` 抽象基类，统一配置接口和结果格式。

### 架构

```python
from src.searchers.base import BaseSearcher, SearcherConfig
from src.searchers.jd_search import JDSearcher
from src.searchers.utils import SearchResults

# 配置
config = SearcherConfig(
    wait_timeout=30,
    max_results=20,
    retry_count=3,
)

# 搜索
searcher = JDSearcher(config=config)
results: SearchResults = searcher.search('iPhone 15')

# 批量搜索
results = searcher.search_batch(['iPhone 15', 'MacBook Pro'])

# 保存结果
results.save_json('output/jd_results.json')
results.save_csv('output/jd_results.csv')

# 健康检查
healthy = searcher.health_check()
searcher.close()
```

### 支持的搜索器

| 搜索器 | 用途 | 参考文档 |
|--------|------|----------|
| `JDSearcher` | 京东商品搜索 | `jd-search` |
| `PDDSearcher` | 拼多多商品搜索 | `pdd-search` |
| `DoubanSearcher` | 豆瓣书籍/电影/音乐搜索 | `douban-search` |
| `SinaNewsSearcher` | 新浪财经新闻抓取 | `sina-news` |
| `EastmoneyGubaSearcher` | 东方财富股吧帖子抓取 | `eastmoney-guba` |
| `ScholarSearcher` | Google Scholar 论文搜索 | `scholar-search` |
| `BaiduSearcher` | 百度搜索 | `baidu-search` |
| `BingSearcher` | Bing 搜索 | `bing-search` |
| `ZhihuSearcher` | 知乎内容搜索 | `zhihu-search` |
| `ZhihuHotSearcher` | 知乎热榜抓取 | `zhihu-hot` |
| `ArxivSearcher` | arXiv 论文搜索 | `arxiv-search` |
| `WechatSearcher` | 微信公众号文章搜索 | `wechat-search` |
| `BilibiliSearcher` | B站视频/UP主搜索 | `bilibili-search` |
| `BossZhipinSearcher` | BOSS直聘职位搜索 | `boss-zhipin-search` |
| `GitHubSearcher` | GitHub 代码仓库/Issue/PR/代码搜索 | `github-search` |
| `StackOverflowSearcher` | Stack Overflow 技术问题搜索 | `stackoverflow-search` |
| `TaobaoSearcher` | 淘宝/天猫商品搜索 | `taobao-search` |
| `RESTAPISearcher` | 通用 REST API 搜索（GitHub/Twitter/Reddit 等） | `api-search` |
| `GraphQLSearcher` | GraphQL API 搜索 | `api-search` |
| `StockSearcher` | 股票行情搜索（东方财富） | `realtime-search` |
| `CryptoSearcher` | 加密货币搜索（CoinMarketCap） | `realtime-search` |
| `NewsSearcher` | 实时新闻搜索 | `realtime-search` |
| `CnkSearcher` | 中国知网论文搜索 | `cnki-search` |

详细使用指南见 `searchers-guide` 子资源。

### 新增模块（v0.9.5）

| 模块 | 用途 | 文件路径 |
|------|------|----------|
| `CloudflareBypass` | Cloudflare 反检测绕过 | `src/core/cloudflare_bypass.py` |
| `RESTAPISearcher` | 通用 REST API 搜索器 | `src/searchers/api_searcher.py` |
| `GraphQLSearcher` | GraphQL API 搜索器 | `src/searchers/api_searcher.py` |
| `StockSearcher` | 股票行情实时搜索 | `src/searchers/realtime_searcher.py` |
| `CryptoSearcher` | 加密货币实时搜索 | `src/searchers/realtime_searcher.py` |
| `NewsSearcher` | 实时新闻搜索 | `src/searchers/realtime_searcher.py` |

**Cloudflare 绕过特性**：
- JS 挑战自动检测与绕过
- 指纹伪装（navigator、webdriver 等）
- 指数退避重试机制
- 可配置最大重试次数和超时

**API 搜索器特性**：
- 支持 REST API 和 GraphQL API
- 自动处理认证（Bearer Token、API Key）
- 请求缓存和去重
- 支持分页和批量请求

**实时数据搜索器特性**：
- 股票：东方财富实时行情、涨跌幅、成交量
- 加密货币：CoinMarketCap 数据、价格、市值
- 新闻：百度新闻搜索、实时热点
- 内置缓存机制，减少重复请求

## 测试与可靠性

### 测试套件

本 skill 包含完整的测试套件，位于 `tests/` 目录：

| 测试文件 | 覆盖范围 | 状态 |
|---------|---------|------|
| `test_website_types.py` | 电商/新闻/社交/后台网站类型 | ✅ 32 通过 |
| `test_edge_cases.py` | 网络异常/页面跳转/动态内容/重试机制 | ✅ 17 通过 |
| `test_searchers.py` | 搜索器架构/配置/工具函数/集成测试 | ✅ 53 通过 |
| `test_captcha_handler.py` | 验证码处理逻辑 | ✅ 已验证 |
| `test_enhanced_modules.py` | 增强模块（智能等待/动态加载等） | ✅ 已验证 |
| `templates/test_anti_crawl.py` | 反爬机制（stealth/请求头/速率控制/代理池） | ✅ 32 通过 |
| `templates/test_bilibili_search.py` | B站视频/UP主搜索 | ✅ 23 通过 |
| `templates/test_boss_zhipin_search.py` | BOSS直聘职位搜索 | ✅ 31 通过 |
| `templates/test_browser_console_template.py` | 浏览器控制台调试 | ✅ 4 通过 |
| `templates/test_browser_watch_template.py` | 浏览器协作监控 | ✅ 5 通过 |
| `test_new_modules.py` | 新增模块（Cloudflare/API/实时数据） | ✅ 全部通过 |

运行测试：
```bash
cd .claude/skills/browser-cdp
python -m pytest tests/ -v
```

**注意**：`tests/templates/` 目录下部分测试文件（如 `test_dynamic_content.py`、`test_ecommerce_flow_template.py` 等）引用了不存在的函数，这些是占位符模板，尚未实现。核心测试（`tests/unit/`、`tests/integration/`、`tests/e2e/` 和 `tests/test_*.py`）全部通过（194 passed, 10 skipped）。

### 边界场景处理

**网络异常**：
- `CDPError` 异常被 `SmartWait._get_pending_requests()` 捕获，返回 0 而非崩溃
- 超时后 `wait_for()` 返回 `False` 而非抛出异常

**动态内容**：
- `DynamicLoader.scroll_to_load()` 在高度不变时自动停止
- 虚拟列表自动去重（基于元素文本）

**重试机制**：
- 指数退避：`base_delay * 2^attempt + jitter`
- 熔断器：连续失败 N 次后暂停，超时后恢复
- 可配置 `retry_on` 只重试特定错误类型

### 已知测试行为

1. **timeout 模式**：`MockSession(fail_mode="timeout")` 的异常会被 `_get_pending_requests` 捕获，返回 0，网络被视为空闲
2. **熔断器计数**：`execute()` 在所有重试耗尽后调用一次 `record_failure()`，`failure_count` 至少为 1
3. **快速导航**：mock 每次返回不同 URL 时，`change_count` 会在 timeout 内达到，返回 True
4. **滚动停止**：第一次滚动后高度不变时停止，`loaded_pages=1` 是正确行为

## 可靠性改进说明与最佳实践

### 可靠性改进要点

**智能等待策略**：
- 采用 `networkidle`、`stable`、`selector` 三重等待策略，根据页面类型自动选择最优策略
- `networkidle` 适用于动态加载页面，等待网络请求空闲后操作
- `stable` 适用于内容渲染页面，等待页面稳定后操作
- `selector` 适用于元素级操作，等待目标元素出现后操作

**自动重试与熔断机制**：
- 指数退避重试：`base_delay * 2^attempt + jitter`，最大重试次数可配置
- 熔断器保护：连续失败 N 次后暂停，超时后自动恢复
- 可配置 `retry_on` 只重试特定错误类型，避免无效重试

**动态内容处理**：
- `DynamicLoader` 支持无限滚动加载，自动检测页面高度变化
- 虚拟列表自动去重，基于元素文本相似度过滤重复内容
- Shadow DOM 和 iframe 内容自动穿透抓取

**反检测模式**：
- 移除 `navigator.webdriver` 属性，模拟真实浏览器指纹
- 随机 User-Agent 轮换，降低被识别风险
- 人类化鼠标轨迹和打字节奏，模拟真实用户行为
- 请求间隔随机化，避免固定频率触发反爬机制

### 使用最佳实践

**搜索器使用最佳实践**：
1. **配置优化**：根据目标网站特性调整 `wait_timeout`、`max_results`、`retry_count` 参数
2. **批量搜索**：使用 `search_batch` 方法批量搜索多个关键词，自动合并去重
3. **结果保存**：使用 `save_json`、`save_csv`、`save_markdown` 方法保存结果
4. **健康检查**：定期调用 `health_check` 方法检查搜索器状态
5. **资源释放**：使用完毕后调用 `close` 方法释放浏览器资源

**浏览器操作最佳实践**：
1. **实例管理**：固定使用 `--name` 参数保持登录态，避免每次新建实例
2. **等待策略**：SPA 页面使用 `--wait-for networkidle`，传统页面使用 `--wait-for stable`
3. **反检测**：敏感网站启用 `--stealth` 参数，降低被封禁风险
4. **验证码处理**：启用 `--handle-captcha` 参数，自动检测并处理常见验证码
5. **内容抓取**：大页面使用 `--max-chars` 控制输出长度，完整内容使用 `--save` 写文件

**协作模式最佳实践**：
1. **观察模式**：轮询判断用户操作完成情况，不干预用户操作
2. **建议模式**：分析页面状态，提供操作建议供用户决策
3. **代劳模式**：在用户确认后自动执行操作，提高操作效率

### 常见问题与解决方案

| 问题 | 原因 | 解决方案 |
|-----|------|---------|
| 页面加载超时 | 网络慢/反爬检测 | 增加 `--timeout`，启用 `--stealth` |
| 验证码无法处理 | 复杂验证码类型 | 使用 `--handle-captcha` 查看提示，手动处理 |
| 内容抓取为空 | SPA 未渲染完成 | 使用 `--wait-for networkidle` 或 `stable` |
| 元素找不到 | 动态加载/Shadow DOM | 使用 `ComplexDOMHandler` 或增加等待时间 |
| IP 被封禁 | 请求频率过高 | 降低频率，使用代理池 |
| 搜索结果重复 | 去重策略未启用 | 启用 `dedup_by_title` 或 `dedup_by_url` 去重 |
| 批量搜索失败 | 关键词过多 | 分批搜索，每批不超过 10 个关键词 |
| 结果保存失败 | 路径不存在 | 创建输出目录或使用绝对路径 |

