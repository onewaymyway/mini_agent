# 闲鱼搜索器

## 概述

闲鱼（www.goofish.com）是阿里巴巴旗下的二手交易平台，提供二手商品搜索、购买等服务。

## 技术特征

- **技术栈**: SPA，需登录态
- **搜索功能**: 商品搜索
- **数据格式**: JSON API
- **反爬强度**: ⭐⭐⭐（需登录）
- **登录需求**: 必须登录

## 使用方法

```bash
# 搜索商品
python xianyu_search.py "iPhone 15"

# 搜索指定成色
python xianyu_search.py "机械键盘" --condition 95新

# 保存到指定目录
python xianyu_search.py "笔记本电脑" --output-dir ./xianyu_results
```

## API 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| condition | str | None | 成色要求（如：95新、全新） |
| max_results | int | 20 | 最大结果数 |
| port | int | 9333 | 浏览器调试端口 |
| tab_id | str | None | Tab ID |
| stealth | bool | True | 是否启用反检测模式 |
| output_dir | str | None | 输出目录 |
| wait_timeout | int | 30 | 等待超时时间（秒） |

## 返回数据结构

```json
{
  "title": "iPhone 15 Pro Max 256G 蓝色",
  "price": "6500",
  "location": "北京",
  "seller": "用户123456",
  "url": "https://www.goofish.com/item/xxx",
  "condition": "95新"
}
```

## 注意事项

1. 闲鱼需要登录态才能搜索，建议使用 dedicated session
2. 商品详情页包含卖家信息、商品描述、图片等
3. 建议每次搜索间隔 3-5 秒
4. 高频访问可能触发验证码

## 相关文档

- [website-analysis.md](./website-analysis.md) - 网站结构分析
- [anti-detection.md](./anti-detection.md) - 反检测策略
