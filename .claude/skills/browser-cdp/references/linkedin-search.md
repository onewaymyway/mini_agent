# LinkedIn 搜索与内容抓取指南

## 概述

LinkedIn 是全球最大职业社交平台，反爬机制中等，部分公开信息无需登录。

## 网站配置

- **域名**: linkedin.com
- **URL**: https://www.linkedin.com
- **Category**: social / professional_network
- **Anti-crawl Level**: 3
- **Login Required**: false（部分功能需要）
- **Captcha Type**: none
- **Frontend Framework**: React

## 核心功能模块

### 1. 搜索人员/公司/职位

```
搜索URL: https://www.linkedin.com/search/results/all/?keywords={query}
搜索框: input[placeholder*='Search']
人员结果: .result-item, .search-result__result
公司信息: .company-result, .result-card
职位结果: .job-result, .search-jobs-result
```

### 2. 获取人员资料

```
个人主页: https://www.linkedin.com/in/{username}/
姓名: .text-heading-xlarge, h1
职位: .text-body-small, .member-card__job-title
公司: .company-name, .entity-shell
location: .location, .member-about__location
Education: .education-entry
技能: .skill-item, .skill-name
```

### 3. 获取公司动态

```
公司主页: https://www.linkedin.com/company/{company-name}/
公司简介: .about-us, .description
员工数: .employee-count
行业: .industry
公司动态: .company-update, .feed-item
```

### 4. 获取职位信息

```
职位列表: .jobs-search-results-list
职位标题: .job-card-title, .jobs-search__job-title
公司名称: .company-name
地点: .job-location
描述: .job-card-description
```

## 反爬策略

1. **匿名访问限制**: 未登录用户只能看到有限信息
2. **速率控制**: 每次请求间隔 3-8 秒
3. **User-Agent**: 使用主流浏览器UA
4. **Stealth模式**: 启用反检测插件
5. **Cookie**: 建议登录态获取完整信息

## 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 信息被遮挡 | 未登录 | 添加Cookie或登录 |
| 触发验证 | 频率过高 | 降低频率 |
| 部分数据缺失 | 权限限制 | 使用登录态 |
| 搜索结果为空 | 关键词不匹配 | 尝试英文关键词 |
| 加载缓慢 | 网络延迟 | 增加超时时间 |

## 输出格式示例

```json
{
  "search_type": "people",
  "query": "Python Developer",
  "total_results": 30,
  "results": [
    {
      "name": "张三",
      "title": "Senior Software Engineer",
      "company": "Google",
      "location": "Mountain View, CA",
      "url": "https://www.linkedin.com/in/zhangsan/",
      "scraped_at": "2026-08-15T12:00:00Z"
    }
  ]
}
```

## 注意事项

1. LinkedIn 对自动化访问非常敏感
2. 公开个人资料信息有限，需要登录才能查看详细信息
3. 建议使用headless Chrome + stealth插件
4. 部分高级搜索功能需要LinkedIn Premium账号
