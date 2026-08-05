# 完整 API 手册

> 本目录存放各数据源的详细 API 文档，包括参数说明、返回字段字典、错误码表、限流策略等。

## 目录结构

- `eastmoney/` - 东方财富 API 文档
- `sina/` - 新浪财经 API 文档
- `akshare/` - AKShare API 文档
- `tushare/` - Tushare API 文档
- `cls/` - 财联社 API 文档
- `wallstcn/` - 华尔街见闻 API 文档
- `xueqiu/` - 雪球 API 文档

## 使用说明

由于 API 文档体量大，建议使用 grep 检索具体片段：

```bash
# 搜索东方财富 API 参数
grep -r "api_key" references/full-api-docs/eastmoney/

# 查看限流策略
grep -r "rate_limit" references/full-api-docs/
```

## 更新记录

- 2026-08-04: 创建目录结构
