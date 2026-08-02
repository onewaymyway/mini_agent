# 目标领域网站结构分析与反爬机制调研报告

> 生成时间：2026-08-02
> 目的：为 browser-cdp skill 拓展抓取能力提供差异化策略依据

---

## 目录

1. [电商领域](#1-电商领域)
2. [新闻领域](#2-新闻领域)
3. [社交领域](#3-社交领域)
4. [金融领域](#4-金融领域)
5. [学术领域](#5-学术领域)
6. [招聘领域](#6-招聘领域)
7. [房产领域](#7-房产领域)
8. [旅游领域](#8-旅游领域)
9. [视频领域](#9-视频领域)
10. [音乐领域](#10-音乐领域)
11. [综合对比与优先级建议](#11-综合对比与优先级建议)

---

## 1. 电商领域

### 1.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| 淘宝 | taobao.com | ⭐⭐⭐⭐⭐ | 中 |
| 京东 | jd.com | ⭐⭐⭐⭐ | 高 |
| 拼多多 | pinduoduo.com | ⭐⭐⭐ | 高 |
| 天猫 | tmall.com | ⭐⭐⭐⭐⭐ | 低 |

### 1.2 网站结构分析

**京东**
- URL 模式：`https://item.jd.com/{sku_id}.html`
- 搜索：`https://search.jd.com/Search?keyword={keyword}&enc=utf-8`
- 商品列表：JSONP 格式，通过 `window.jQuery` 回调返回
- 详情页：服务端渲染（SSR），HTML 包含完整商品信息
- 价格/库存：部分需登录态，未登录显示"暂无评价"

**拼多多**
- URL 模式：`https://mobile.yangkeduo.com/proxy/api/goods/detail?goods_id={id}`
- 搜索：`https://mobile.yangkeduo.com/proxy/api/search?keyword={keyword}`
- 商品列表：移动端 API 返回 JSON
- 详情页：移动端 H5，部分数据需登录
- 反爬相对较弱，适合优先实现

**淘宝**
- URL 模式：`https://item.taobao.com/item.htm?id={item_id}`
- 搜索：`https://s.taobao.com/search?q={keyword}`
- 商品列表：动态加载，需解析 JS 渲染后的 DOM
- 详情页：SSR + 大量 JS 动态渲染
- 反爬最强，需高匿代理 + JS 逆向

### 1.3 反爬机制

| 机制 | 淘宝 | 京东 | 拼多多 |
|------|------|------|--------|
| IP 限制 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| UA 检测 | ⭐⭐⭐ | ⭐⭐ | ⭐ |
| 验证码 | 滑块+点选 | 滑块 | 较少 |
| Cookie 验证 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| 行为检测 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| 字体加密 | ⭐⭐⭐⭐ | ⭐⭐ | ⭐ |
| 设备指纹 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |

### 1.4 抓取策略

```python
# 京东推荐策略
- 使用 --stealth 反检测模式
- 随机 UA 轮换（每 5 次请求切换）
- 请求间隔 2-5 秒随机延迟
- 优先抓取详情页（SSR），列表页用 API
- 价格数据从 API 获取（更稳定）

# 拼多多推荐策略
- 直接调用移动端 API（无需浏览器）
- 使用 browser-cdp 处理需要登录的接口
- 请求间隔 3-8 秒
- 适合批量抓取商品列表

# 淘宝推荐策略
- 必须使用已登录的专用浏览器实例
- 高匿代理池（每请求轮换）
- 模拟人类行为（随机滚动、点击）
- 建议仅用于低频、小规模抓取
```

---

## 2. 新闻领域

### 2.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| 新浪财经 | finance.sina.com.cn | ⭐⭐ | 高 |
| 网易新闻 | news.163.com | ⭐⭐ | 高 |
| 腾讯新闻 | news.qq.com | ⭐⭐ | 中 |
| 财联社 | cls.cn | ⭐⭐⭐ | 中 |
| 华尔街见闻 | wsj.com | ⭐⭐ | 低 |

### 2.2 网站结构分析

**新浪财经**
- 列表页：`https://finance.sina.com.cn/stock/`（RSS 格式）
- 详情页：`https://finance.sina.com.cn/stock/.../...shtml`
- 数据格式：HTML + JSON 混合，部分数据通过 AJAX 加载
- 反爬较弱，可直接 requests 抓取

**网易新闻**
- 列表页：`https://news.163.com/`（RSS + 动态加载）
- 详情页：`https://news.163.com/.../...html`
- 评论系统：独立 API，需解析 JSON
- 反爬中等，有 IP 频率限制

**财联社**
- 电报流：`https://www.cls.cn/telegraph`
- 详情页：`https://www.cls.cn/detail/{id}`
- 数据格式：纯 JSON API，无 SSR
- 反爬较强，需模拟浏览器行为

### 2.3 反爬机制

| 机制 | 新浪 | 网易 | 财联社 |
|------|------|------|--------|
| IP 限制 | ⭐ | ⭐⭐ | ⭐⭐⭐ |
| UA 检测 | ⭐ | ⭐ | ⭐⭐ |
| 验证码 | 极少 | 偶尔 | 较少 |
| 请求频率 | 宽松 | 中等 | 较严 |
| 内容加密 | 无 | 无 | 无 |

### 2.4 抓取策略

```python
# 新浪财经推荐策略
- 优先使用 requests + lxml 直接抓取（无需浏览器）
- 列表页 RSS 格式稳定，解析简单
- 详情页 SSR，HTML 包含完整内容
- 适合批量抓取历史新闻

# 网易新闻推荐策略
- 列表页可用 requests，详情页用 browser-cdp
- 评论数据通过独立 API 获取
- 注意请求间隔 1-3 秒

# 财联社推荐策略
- 必须使用 browser-cdp（API 需浏览器环境）
- 使用 --stealth 模式
- 电报流实时性高，适合监控场景
- 建议增量更新（按时间戳去重）
```

---

## 3. 社交领域

### 3.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| 知乎 | zhihu.com | ⭐⭐⭐ | 已覆盖 |
| 豆瓣 | douban.com | ⭐⭐⭐ | 高 |
| 微博 | weibo.com | ⭐⭐⭐⭐ | 中 |
| 小红书 | xiaohongshu.com | ⭐⭐⭐⭐⭐ | 低 |

### 3.2 网站结构分析

**豆瓣**
- 搜索：`https://www.douban.com/search?q={keyword}`
- 书籍/电影：`https://book.douban.com/subject/{id}/` / `https://movie.douban.com/subject/{id}/`
- 评论：`https://movie.douban.com/subject/{id}/comments`
- 数据格式：SSR + 部分 AJAX
- 反爬：需登录态，有滑块验证码

**微博**
- 搜索：`https://s.weibo.com/weibo?q={keyword}`
- 用户主页：`https://weibo.com/{uid}`
- 数据格式：大量 AJAX，需解析 JSON
- 反爬：强，需登录 + 设备指纹

### 3.3 反爬机制

| 机制 | 豆瓣 | 微博 |
|------|------|------|
| IP 限制 | ⭐⭐ | ⭐⭐⭐⭐ |
| UA 检测 | ⭐⭐ | ⭐⭐⭐ |
| 验证码 | 滑块 | 滑块+点选 |
| 登录态 | 必须 | 必须 |
| 设备指纹 | ⭐⭐ | ⭐⭐⭐⭐⭐ |

### 3.4 抓取策略

```python
# 豆瓣推荐策略
- 使用 --dedicated --name douban_session 保留登录态
- 搜索页可用 browser-cdp，详情页 SSR 可直接解析
- 评论数据需登录，建议用户手动登录
- 请求间隔 3-5 秒

# 微博推荐策略
- 必须使用已登录的专用浏览器实例
- 搜索 API 需解析 JSON（非 HTML）
- 建议仅用于低频监控，不适合批量抓取
- 注意微博的反爬升级频繁
```

---

## 4. 金融领域

### 4.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| 东方财富 | eastmoney.com | ⭐⭐⭐ | 高 |
| 同花顺 | 10jqka.com.cn | ⭐⭐⭐⭐ | 中 |
| 雪球 | xueqiu.com | ⭐⭐⭐ | 高 |
| 新浪财经 | finance.sina.com.cn | ⭐⭐ | 已覆盖 |

### 4.2 网站结构分析

**东方财富**
- 行情：`https://quote.eastmoney.com/{code}.html`
- 股吧：`https://guba.eastmoney.com/list/{stock_code}.html`
- 数据格式：JSON API + SSR 混合
- 股吧帖子：分页加载，需解析 DOM
- 反爬：中等，有频率限制

**雪球**
- 行情：`https://xueqiu.com/{symbol}`
- 组合：`https://xueqiu.com/p/{portfolio_id}`
- 数据格式：纯 JSON API
- 反爬：较强，需登录态

### 4.3 反爬机制

| 机制 | 东方财富 | 雪球 |
|------|----------|------|
| IP 限制 | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| UA 检测 | ⭐⭐ | ⭐⭐⭐ |
| 验证码 | 较少 | 偶尔 |
| 登录态 | 可选 | 必须 |
| 频率限制 | ⭐⭐⭐ | ⭐⭐⭐⭐ |

### 4.4 抓取策略

```python
# 东方财富推荐策略
- 行情数据：直接调用 API（无需浏览器）
- 股吧帖子：使用 browser-cdp + --stealth
- 股吧评论：需解析嵌套 DOM
- 建议增量更新（按时间戳去重）

# 雪球推荐策略
- 必须使用 --dedicated --name xueqiu_session
- 行情数据通过 API 获取
- 组合数据需登录态
- 请求间隔 2-4 秒
```

---

## 5. 学术领域

### 5.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| arXiv | arxiv.org | ⭐ | 已覆盖 |
| Google Scholar | scholar.google.com | ⭐⭐⭐ | 高 |
| Semantic Scholar | sematicscholar.org | ⭐⭐ | 中 |
| CNKI | cnki.net | ⭐⭐⭐⭐ | 低 |

### 5.2 网站结构分析

**Google Scholar**
- 搜索：`https://scholar.google.com/scholar?q={query}`
- 结果：HTML 渲染，需解析 DOM
- 反爬：中等，有验证码
- 建议：使用 browser-cdp + --stealth

**Semantic Scholar**
- API：`https://api.semanticscholar.org/graph/v1/paper/search`
- 无需浏览器，直接 HTTP 请求
- 反爬：弱，有速率限制

### 5.3 抓取策略

```python
# Google Scholar 推荐策略
- 使用 browser-cdp + --stealth
- 随机 UA 轮换
- 请求间隔 5-10 秒
- 避免高频搜索（易触发验证码）

# Semantic Scholar 推荐策略
- 直接调用 API（无需浏览器）
- 适合批量论文搜索
- 注意 API 速率限制（100 次/分钟）
```

---

## 6. 招聘领域

### 6.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| BOSS直聘 | zhipin.com | ⭐⭐⭐⭐ | 高 |
| 拉勾网 | lagou.com | ⭐⭐⭐ | 中 |
| 智联招聘 | zhilian.com | ⭐⭐⭐ | 低 |
| 猎聘 | liepin.com | ⭐⭐⭐ | 低 |

### 6.2 网站结构分析

**BOSS直聘**
- 搜索：`https://www.zhipin.com/web/geek/job?query={keyword}&city={city}`
- 职位详情：`https://www.zhipin.com/web/geek/job/{job_id}`
- 数据格式：JSON API + SSR 混合
- 反爬：强，字体加密 + 滑块验证码
- 特点：薪资数据字体加密，需逆向

**拉勾网**
- 搜索：`https://www.lagou.com/jobs/list_{keyword}.html`
- 职位详情：`https://www.lagou.com/jobs/{job_id}.html`
- 数据格式：SSR + AJAX
- 反爬：中等

### 6.3 反爬机制

| 机制 | BOSS直聘 | 拉勾网 |
|------|----------|--------|
| IP 限制 | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| UA 检测 | ⭐⭐⭐ | ⭐⭐ |
| 验证码 | 滑块 | 滑块 |
| 字体加密 | ⭐⭐⭐⭐⭐ | ⭐ |
| 登录态 | 必须 | 可选 |

### 6.4 抓取策略

```python
# BOSS直聘推荐策略
- 必须使用已登录的专用浏览器实例
- 薪资数据需处理字体加密（建议用 API 获取明文）
- 使用 --stealth 模式
- 请求间隔 5-10 秒
- 建议仅用于低频监控

# 拉勾网推荐策略
- 可使用 browser-cdp + --stealth
- 职位列表页 SSR，可直接解析
- 详情页需 AJAX 加载
- 请求间隔 3-5 秒
```

---

## 7. 房产领域

### 7.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| 链家 | lianjia.com | ⭐⭐⭐ | 高 |
| 安居客 | anjuke.com | ⭐⭐⭐ | 中 |
| 贝壳 | beike.com | ⭐⭐⭐⭐ | 中 |
| 房天下 | house.soufun.com | ⭐⭐ | 低 |

### 7.2 网站结构分析

**链家**
- 小区：`https://{city}.lianjia.com/xiaoqu/{district}/`
- 房源：`https://{city}.lianjia.com/ershoufang/`
- 数据格式：SSR + 大量 AJAX
- 反爬：中等，有 IP 频率限制

**安居客**
- 小区：`https://{city}.anjuke.com/xiaoqu/`
- 房源：`https://{city}.anjuke.com/sale/`
- 数据格式：SSR + JSON API
- 反爬：较弱

### 7.3 反爬机制

| 机制 | 链家 | 安居客 |
|------|------|--------|
| IP 限制 | ⭐⭐⭐ | ⭐⭐ |
| UA 检测 | ⭐⭐ | ⭐ |
| 验证码 | 较少 | 极少 |
| 登录态 | 可选 | 可选 |
| 频率限制 | ⭐⭐⭐ | ⭐⭐ |

### 7.4 抓取策略

```python
# 链家推荐策略
- 使用 browser-cdp + --stealth
- 小区列表页 SSR，可直接解析
- 房源详情需 AJAX 加载
- 请求间隔 3-5 秒
- 注意"幽灵房"假数据过滤

# 安居客推荐策略
- 可直接 requests 抓取（反爬较弱）
- 使用 browser-cdp 处理动态内容
- 适合批量抓取小区数据
```

---

## 8. 旅游领域

### 8.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| 携程 | ctrip.com | ⭐⭐⭐⭐ | 高 |
| 去哪儿 | qunar.com | ⭐⭐⭐ | 中 |
| 飞猪 | fliggy.com | ⭐⭐⭐ | 低 |
| 马蜂窝 | mafengwo.cn | ⭐⭐ | 中 |

### 8.2 网站结构分析

**携程**
- 酒店：`https://hotels.ctrip.com/hotel/{city_id}`
- 机票：`https://flights.ctrip.com/online/list/oneway-{from}-{to}`
- 数据格式：JSON API + SSR 混合
- 反爬：强，sign 参数动态生成

**去哪儿**
- 酒店：`https://hotels.qunar.com/city/{city_id}`
- 数据格式：JSON API
- 反爬：中等

### 8.3 反爬机制

| 机制 | 携程 | 去哪儿 |
|------|------|--------|
| IP 限制 | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| UA 检测 | ⭐⭐⭐ | ⭐⭐ |
| 验证码 | 滑块 | 较少 |
| 签名验证 | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| 登录态 | 可选 | 可选 |

### 8.4 抓取策略

```python
# 携程推荐策略
- 必须使用 browser-cdp + --stealth
- 酒店列表页 SSR，可直接解析
- 价格数据需 API 获取（含 sign 参数）
- 请求间隔 5-10 秒
- 建议仅用于低频监控

# 去哪儿推荐策略
- 可使用 browser-cdp + --stealth
- API 接口较稳定
- 适合批量抓取酒店数据
```

---

## 9. 视频领域

### 9.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| B站 | bilibili.com | ⭐⭐⭐⭐ | 高 |
| 抖音 | douyin.com | ⭐⭐⭐⭐⭐ | 低 |
| 快手 | kuaishou.com | ⭐⭐⭐⭐⭐ | 低 |
| 西瓜视频 |.ixigua.com | ⭐⭐⭐ | 低 |

### 9.2 网站结构分析

**B站**
- 搜索：`https://search.bilibili.com/all?keyword={keyword}`
- 视频详情：`https://www.bilibili.com/video/{bv_id}`
- 数据格式：JSON API + SSR 混合
- 反爬：中等，有频率限制

### 9.3 反爬机制

| 机制 | B站 |
|------|-----|
| IP 限制 | ⭐⭐⭐ |
| UA 检测 | ⭐⭐ |
| 验证码 | 较少 |
| 登录态 | 可选 |
| 频率限制 | ⭐⭐⭐ |

### 9.4 抓取策略

```python
# B站推荐策略
- 使用 browser-cdp + --stealth
- 搜索列表页 SSR，可直接解析
- 视频详情需 API 获取（含播放量、弹幕数）
- 请求间隔 3-5 秒
- 适合批量抓取视频元数据
```

---

## 10. 音乐领域

### 10.1 目标网站

| 网站 | 域名 | 难度 | 优先级 |
|------|------|------|--------|
| 网易云音乐 | music.163.com | ⭐⭐⭐ | 中 |
| QQ音乐 | y.qq.com | ⭐⭐⭐ | 低 |
| 酷狗音乐 | kugou.com | ⭐⭐ | 低 |

### 10.2 网站结构分析

**网易云音乐**
- 搜索：`https://music.163.com/search?type=1&s={keyword}`
- 歌曲详情：`https://music.163.com/#/song?id={song_id}`
- 数据格式：JSON API
- 反爬：中等，有频率限制

### 10.3 反爬机制

| 机制 | 网易云 |
|------|--------|
| IP 限制 | ⭐⭐⭐ |
| UA 检测 | ⭐⭐ |
| 验证码 | 较少 |
| 登录态 | 可选 |
| 频率限制 | ⭐⭐⭐ |

### 10.4 抓取策略

```python
# 网易云音乐推荐策略
- 使用 browser-cdp + --stealth
- 搜索列表页 SSR，可直接解析
- 歌曲详情需 API 获取
- 请求间隔 3-5 秒
- 注意版权限制，仅抓取元数据
```

---

## 11. 综合对比与优先级建议

### 11.1 难度评级汇总

| 领域 | 网站 | 难度 | 优先级 | 建议实现方式 |
|------|------|------|--------|-------------|
| 电商 | 京东 | ⭐⭐⭐⭐ | 高 | browser-cdp + stealth |
| 电商 | 拼多多 | ⭐⭐⭐ | 高 | API + browser-cdp |
| 新闻 | 新浪财经 | ⭐⭐ | 高 | requests（无需浏览器） |
| 新闻 | 财联社 | ⭐⭐⭐ | 中 | browser-cdp + stealth |
| 社交 | 豆瓣 | ⭐⭐⭐ | 高 | browser-cdp + 登录态 |
| 金融 | 东方财富 | ⭐⭐⭐ | 高 | API + browser-cdp |
| 金融 | 雪球 | ⭐⭐⭐ | 高 | browser-cdp + 登录态 |
| 学术 | Google Scholar | ⭐⭐⭐ | 高 | browser-cdp + stealth |
| 招聘 | BOSS直聘 | ⭐⭐⭐⭐ | 高 | browser-cdp + 登录态 |
| 招聘 | 拉勾网 | ⭐⭐⭐ | 中 | browser-cdp + stealth |
| 房产 | 链家 | ⭐⭐⭐ | 高 | browser-cdp + stealth |
| 房产 | 安居客 | ⭐⭐⭐ | 中 | requests + browser-cdp |
| 旅游 | 携程 | ⭐⭐⭐⭐ | 高 | browser-cdp + stealth |
| 视频 | B站 | ⭐⭐⭐⭐ | 高 | browser-cdp + stealth |
| 音乐 | 网易云 | ⭐⭐⭐ | 中 | browser-cdp + stealth |

### 11.2 推荐实现顺序

**第一阶段（高优先级，易实现）**
1. 拼多多电商搜索器（反爬弱，API 稳定）
2. 新浪财经新闻抓取器（requests 即可）
3. 豆瓣社交搜索器（SSR 为主）
4. 东方财富金融数据（API + browser-cdp）

**第二阶段（中优先级，需登录态）**
5. 京东电商搜索器（需 stealth 模式）
6. 财联社新闻抓取器（API 需浏览器）
7. 雪球金融数据（需登录态）
8. Google Scholar 学术搜索（需 stealth）

**第三阶段（高难度，低频使用）**
9. BOSS直聘招聘搜索器（字体加密）
10. 链家房产搜索器（需 stealth）
11. 携程旅游搜索器（签名验证）
12. B站视频搜索器（需登录态）

### 11.3 通用反爬应对策略

```python
# 1. 反检测模式
--stealth  # 移除 navigator.webdriver，模拟真实浏览器

# 2. 请求频率控制
随机延迟 2-10 秒
降低并发数（≤5）

# 3. 登录态管理
--dedicated --name <固定名称>
首次使用时提示用户手动登录

# 4. UA 轮换
每 5 次请求切换 User-Agent

# 5. 代理池（可选）
高匿代理轮换

# 6. 数据去重
基于 URL + 时间戳去重
增量更新策略
```

---

## 附录：现有能力覆盖情况

### 已覆盖领域
- ✅ 搜索引擎（百度、Bing）
- ✅ 知乎（内容搜索、热榜、专栏、问答发布）
- ✅ arXiv（单关键词、多关键词批量搜索）
- ✅ 微信公众号（搜狗微信搜索）

### 待拓展领域
- ⏳ 电商（京东、拼多多）
- ⏳ 新闻（新浪、财联社）
- ⏳ 社交（豆瓣）
- ⏳ 金融（东方财富、雪球）
- ⏳ 学术（Google Scholar）
- ⏳ 招聘（BOSS直聘、拉勾）
- ⏳ 房产（链家、安居客）
- ⏳ 旅游（携程、去哪儿）
- ⏳ 视频（B站）
- ⏳ 音乐（网易云）

---

*本报告为 browser-cdp skill 拓展计划提供决策依据，具体实现细节见后续步骤。*
