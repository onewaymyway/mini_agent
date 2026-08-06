# 12306 铁路购票搜索器

## 概述

12306（www.12306.cn）是中国铁路客户服务中心官方网站，提供火车票查询、购买等服务。

## 技术特征

- **技术栈**: 动态加载，需验证码处理
- **搜索功能**: 车次查询、余票查询
- **数据格式**: JSON API
- **反爬强度**: ⭐⭐⭐（有验证码）
- **登录需求**: 查询无需登录，购票需登录

## 使用方法

```bash
# 查询北京到上海的车次
python train_search.py "北京" "上海" --date 2026-08-10

# 查询高铁
python train_search.py "广州" "深圳" --type G

# 保存到指定目录
python train_search.py "北京" "上海" --output-dir ./train_results
```

## API 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| from_station | str | - | 出发站 |
| to_station | str | - | 到达站 |
| date | str | None | 出发日期（YYYY-MM-DD） |
| train_type | str | None | 车次类型（G/D/C/Z/T/K） |
| max_results | int | 30 | 最大结果数 |
| port | int | 9333 | 浏览器调试端口 |
| tab_id | str | None | Tab ID |
| stealth | bool | True | 是否启用反检测模式 |
| output_dir | str | None | 输出目录 |
| wait_timeout | int | 30 | 等待超时时间（秒） |

## 返回数据结构

```json
{
  "train_no": "G1",
  "from_station": "北京南",
  "to_station": "上海虹桥",
  "depart_time": "09:00",
  "arrive_time": "13:30",
  "duration": "4小时30分",
  "second_class": "有票",
  "first_class": "有票",
  "business": "5张"
}
```

## 注意事项

1. 12306 有严格的反爬机制，请求间隔建议 5-10 秒
2. 高频查询可能触发验证码，需人工处理
3. 车次信息实时性高，建议查询当天或次日车次
4. 车站名称需使用标准名称（如"北京"而非"北京南"）

## 车站拼音码映射

搜索器内置常见车站拼音码映射，支持中文站名自动转换。

## 相关文档

- [website-analysis.md](./website-analysis.md) - 网站结构分析
- [captcha-handling.md](./captcha-handling.md) - 验证码处理
