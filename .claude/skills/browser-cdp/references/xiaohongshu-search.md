---
name: xiaohongshu-search
skill: browser-cdp
script: xiaohongshu_search.py
description: 小红书搜索器，支持笔记/商品/用户/话题搜索，需登录态绕过 x-s 签名验证。
triggers: 小红书搜索, xiaohongshu search, 小红书笔记, xiaohongshu_search.py
platforms: windows, macos, linux, pc
---

# 小红书搜索器 (`xiaohongshu_search.py`)

## 用途

使用 browser-cdp skill 搜索小红书笔记、商品、用户和话题内容。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 笔记搜索（需登录态）
python src/searchers/xiaohongshu_search.py "护肤推荐" --type note --max-results 20

# 用户搜索
python src/searchers/xiaohongshu_search.py "美妆博主" --type user --max-results 10

# 话题搜索
python src/searchers/xiaohongshu_search.py "旅行" --type topic

# 商品搜索
python src/searchers/xiaohongshu_search.py "口红" --type product
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--type` | 搜索类型 (note/user/product/topic) | note |
| `--max-results` | 最大结果数 | 20 |
| `--output-dir` | 输出目录 | `./search_results/xiaohongshu` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |

## 输出格式

```json
{
  "source": "xiaohongshu",
  "title": "笔记标题",
  "url": "https://www.xiaohongshu.com/explore/xxxxx",
  "snippet": "作者: xxx | 点赞: 1234",
  "metadata": {
    "author": "博主名称",
    "likes": "1234",
    "cover": "https://xn--x...",
    "type": "note"
  },
  "scraped_at": "2026-08-02T10:30:00Z"
}
```

## 核心实现要点

- 优先使用已登录浏览器会话绕过 x-s/x-s-common 签名验证
- 使用 stealth 模式降低检测风险
- 模拟人类行为：随机滚动、页面停留
- Cookie 10 分钟过期，需定期刷新

## 注意事项

- **必须使用已登录的浏览器会话**，否则无法获取搜索结果
- 使用 `--dedicated --name xiaohongshu_session` 保持登录态
- 小红书反爬较强，建议控制搜索频率（每次间隔 3-5 秒）
- 遇到滑块验证码需手动处理

## 技术特征

| 维度 | 难度 |
|------|------|
| 反爬绕过 | ⭐⭐⭐⭐ |
| 动态渲染 | ⭐⭐⭐ |
| 登录处理 | ⭐⭐⭐⭐ |
| **综合** | **⭐⭐⭐⭐** |
