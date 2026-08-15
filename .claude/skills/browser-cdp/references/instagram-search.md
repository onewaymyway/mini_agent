# Instagram 搜索与内容抓取指南

## 概述

Instagram 是全球领先的图片/短视频社交平台，Meta旗下产品，反爬机制强，需要登录态。

## 网站配置

- **域名**: instagram.com
- **URL**: https://www.instagram.com
- **Category**: social / photo_sharing
- **Anti-crawl Level**: 4
- **Login Required**: true
- **Captcha Type**: none（需Cookie）
- **Frontend Framework**: React

## 核心功能模块

### 1. 搜索用户/标签/地点

```
搜索URL: https://www.instagram.com/explore/tags/{tag}/
或: https://www.instagram.com/web/search/topsearch/?query={query}
搜索框选择器: input[placeholder='Search']
结果容器: .search-result-item, [class*='result-item']
用户结果: .user-item, .user-result
媒体结果: .media-item, .grid-item
```

### 2. 获取用户主页

```
主页URL: https://www.instagram.com/{username}/
用户信息: .profile-details, .biography
头像: img.profile-picture, img[userAvatar]
帖子数: .posts-count, [data-testid='count']
粉丝数: .followers-count
关注数: .following-count
```

### 3. 获取动态/帖子

```
动态容器: .article, .post-item
帖子网格: .grid-item, .xcarousel-item
图片URL: img[src*='/media/'], .photo-img
文案: ._ap3a, [class*='caption']
时间戳: time[class*='Date']
点赞数: .likes-count, [data-testid='like-count']
评论数: .comments-count
```

### 4. 标签页抓取

```
标签页URL: https://www.instagram.com/explore/tags/{tag}/
标签动态: .grid-item, .x1lliihq
图片流: img[srcset], .coreSpriteGridPhoto
下一页按钮: a[rel='next'], .fpkah .
```

## 反爬策略

1. **必须登录**: 未登录用户只能看到极少内容
2. **Cookie管理**: 从浏览器导出完整Cookie
3. **速率控制**: 间隔 5-15 秒，模拟人类行为
4. **Stealth模式**: 使用Playwright stealth插件
5. **移动端UA**: 切换mobile UA降低检测概率
6. **设备指纹**: 模拟真实设备特征

## 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 404/页面空白 | Cookie过期 | 重新获取Cookie |
| 跳转到登录页 | 未登录 | 添加session cookies |
| 仅显示部分帖子 | 需滚动加载 | JS执行scrollToEnd |
| 图片无法下载 | 需要登录 | 使用登录态Cookie |
| 触发限制 | 请求过于频繁 | 降低频率，增加随机间隔 |

## 输出格式示例

```json
{
  "search_type": "hashtag",
  "tag": "python",
  "total_posts": 20,
  "posts": [
    {
      "shortcode": "CxYz123",
      "url": "https://www.instagram.com/p/CxYz123/",
      "caption": "学习Python的一天...",
      "likes": 150,
      "comments": 12,
      "image_url": "https://scontent.cdninstagram.com/...",
      "timestamp": "2026-08-15T10:30:00Z"
    }
  ]
}
```

## 注意事项

1. Instagram API 对第三方访问限制非常严格
2. 建议使用浏览器自动化工具而非HTTP请求
3. 移动端页面结构比PC端更稳定
4. GraphQL接口可作为备选方案