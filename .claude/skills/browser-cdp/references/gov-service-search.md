# 国家政务服务平台搜索器

## 概述

国家政务服务平台（gjzwfw.www.gov.cn）是中华人民共和国国务院主办的综合性政务服务平台，提供各类政务服务事项的查询和办理。

## 技术特征

- **技术栈**: SSR + AJAX，轻度反爬
- **搜索功能**: 政务服务事项搜索
- **数据格式**: HTML + JSON 混合
- **反爬强度**: ⭐⭐（政府网站，反爬较弱）
- **登录需求**: 部分服务需登录

## 使用方法

```bash
# 搜索政务服务事项
python gov_service_search.py "营业执照"

# 搜索身份证办理
python gov_service_search.py "身份证办理" --max-results 20

# 保存到指定目录
python gov_service_search.py "企业注册" --output-dir ./gov_results
```

## API 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词 |
| max_results | int | 20 | 最大结果数 |
| port | int | 9333 | 浏览器调试端口 |
| tab_id | str | None | Tab ID |
| stealth | bool | True | 是否启用反检测模式 |
| output_dir | str | None | 输出目录 |
| wait_timeout | int | 30 | 等待超时时间（秒） |

## 返回数据结构

```json
{
  "source": "gov_service",
  "title": "营业执照办理",
  "description": "个体工商户营业执照办理流程及所需材料",
  "department": "市场监督管理局",
  "url": "https://gjzwfw.www.gov.cn/business/xxx",
  "scraped_at": "2026-08-06 10:00:00"
}
```

## 注意事项

1. 政府网站访问相对稳定，但仍建议控制请求频率
2. 部分服务需要登录才能查看详细信息
3. 搜索结果可能包含多个地区的相同事项
4. 建议每次搜索间隔 3-5 秒

## 相关文档

- [website-analysis.md](./website-analysis.md) - 网站结构分析
- [anti-detection.md](./anti-detection.md) - 反检测策略
