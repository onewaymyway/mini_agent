# Twitter/X 搜索与内容抓取指南

## 概述

Twitter/X 是全球最大的微社交平台之一，反爬机制强，登录后可获取更完整数据。

## 网站配置

- **域名**: twitter.com（现亦可用 x.com）
- **URL**: https://twitter.com / https://x.com
- **Category**: social / microblog
- **Anti-crawl Level**: 4
- **Login Required**: false（公开推文可访问）
- **Captcha Type**: none
- **Frontend Framework**: React

## 核心功能模块

### 1. 搜索推文/用户/话题

```
搜索URL: https://twitter.com/search?q={query}&f=live
或: https://x.com/search?q={query}
搜索框: input[placeholder*='Search'], textarea[placeholder*='Search']
搜索结果: .stream-item, .tweet, .css-175oi2r
分页: a[data-testid='pagination-next'], .load-more
```

### 2. 获取用户主页

```
用户主页: https://twitter.com/{username}
用户名: [data-testid='UserName'], .fullname
简介: .ProfileBio, [data-testid='UserDescription']
粉丝数: .ProfileNav-item, [data-testid='followers']
关注数: [data-testid='following']
推文数: [data-testid='tweetCount']
```

### 3. 获取时间线/推文

```
时间线容器: .stream, .feed
推文项: .tweet, .stream-item, .css-175oi2r
推文内容: .tweet-text, [data-testid='tweetText']
发布时间: time, .timestamp
互动数据: .like-action, .retweet-action, .reply-action
图片: img.media-image, img[data-testid='tweetImage']
```

### 4. 获取话题/趋势

```
热搜URL: https://twitter.com/i/events
趋势容器: .trends, .stream-component
趋势项: .trend-item, .trend-name
```

## 反爬策略

1. **速率控制**: 每次请求间隔 5-15 秒
2. **User-Agent轮换**: 使用真实浏览器UA
3. **Stealth模式**: 启用Playwright stealth
4. **API备选**: 考虑使用Twitter API v2（需申请）
5. **移动端适配**: 使用mobile.twitter.com降低检测

## 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 返回空内容 | 需要登录 | 添加Cookie或使用API |
| 触发验证 | 频率过高 | 降低频率，增加随机间隔 |
| 部分内容缺失 | 权限限制 | 使用登录态Cookie |
| 推文加载不全 | JS渲染 | 等待页面完全加载 |
| 图片无法下载 | 需要登录 | 添加session Cookie |

## 输出格式示例

```json
{
  "search_type": "keyword",
  "query": "AI Agent",
  "total_results": 50,
  "tweets": [
    {
      "tweet_id": "1234567890",
      "author": "username",
      "content": "正在研究AI Agent的最新进展...",
      "likes": 150,
      "retweets": 30,
      "replies": 12,
      "created_at": "2026-08-15T10:30:00Z",
      "url": "https://twitter.com/username/status/1234567890"
    }
  ]
}
```

## 注意事项

1. Twitter 对自动化访问限制严格，建议合规使用
2. 公开推文可以匿名抓取，但频率受限
3. 建议使用 Playwright + stealth 插件组合
4. 商业级需求建议使用 Twitter API v2
5. x.com 和 twitter.com 数据一致，可互相访问
