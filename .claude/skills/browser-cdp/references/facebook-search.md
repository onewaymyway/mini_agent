# Facebook 搜索与内容抓取指南

## 概述

Facebook 是全球最大的社交网络平台，反爬机制较强，需要登录态才能获取大部分数据。

## 网站配置

- **域名**: facebook.com
- **URL**: https://www.facebook.com
- **Category**: social / social_network
- **Anti-crawl Level**: 4
- **Login Required**: true
- **Captcha Type**: none（但需要有效Cookie）
- **Frontend Framework**: React

## 核心功能模块

### 1. 搜索用户/页面/话题

```
搜索URL: https://www.facebook.com/search/results?q={query}
搜索框选择器: input[placeholder*='Search'], input[name='q']
搜索结果容器: .search-result, ._liq
结果项: ._liq, .search-result-item
标题: ._liq a, .search-result-title
内容: ._liq span, .search-result-content
分页: a[aria-label='Next'], .load-more, .page-num
```

### 2. 获取用户资料

```
个人资料URL: https://www.facebook.com/{username}
用户信息选择器: .profile-header, .user-profile
头像: img.profile-avatar
关注数: .follow-count, [data-testid='friends']
动态数: .post-count
```

### 3. 获取动态流

```
动态流URL: https://www.facebook.com/{username}/feed
动态容器: .timeline-item, .feed-item
动态内容: .userContentWrapper, [data-ad-preview-text]
时间戳: .timestamp, .UFIPhrase
互动按钮: button[data-testid='like'], button[data-testid='comment']
```

## 反爬策略

1. **登录态管理**: 必须使用有效Cookie，建议通过浏览器登录后导出Cookie
2. **速率控制**: 每次请求间隔 5-10 秒，避免高频操作
3. **Stealth模式**: 启用 Stealth 插件减少检测风险
4. **User-Agent轮换**: 模拟不同浏览器的User-Agent
5. **请求指纹**: 避免特征性请求（如缺少某些Header）

## 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 返回空内容 | Cookie过期 | 重新登录获取新Cookie |
| 返回登录页面 | 未登录 | 添加登录态Cookie |
| 触发验证 | 频率过高 | 降低请求频率，增加间隔 |
| 部分内容不可见 | 需要滚动加载 | 使用智能等待(JavaScript执行) |
| 搜索结果不完整 | 分页限制 | 使用pagination选择器翻页 |

## 输出格式示例

```json
{
  "search_type": "keyword",
  "query": "AI Agent",
  "total_results": 50,
  "results": [
    {
      "type": "user",
      "title": "张三",
      "url": "https://www.facebook.com/zhangsan",
      "followers": 1200,
      "scraped_at": "2026-08-15T12:00:00Z"
    }
  ]
}
```

## 注意事项

1. Facebook 对未登录用户的访问限制越来越严格
2. 建议使用 playwright stealth 模式 + 持久化Cookie
3. 部分国家/地区的Facebook访问受限，需配置代理
4. 动态内容需要等待页面完全加载后再提取
