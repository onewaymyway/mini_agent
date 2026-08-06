# 好大夫在线搜索器

## 概述

好大夫在线（www.haodf.com）是中国领先的在线医疗平台，提供医生搜索、医院查询、在线问诊等服务。

## 技术特征

- **技术栈**: SSR + AJAX，需登录态
- **搜索功能**: 医生搜索、医院搜索
- **数据格式**: HTML + JSON 混合
- **反爬强度**: ⭐⭐⭐（需登录）
- **登录需求**: 必须登录

## 使用方法

```bash
# 搜索医生
python haodf_search.py "心血管" "北京"

# 搜索医院
python haodf_search.py "眼科" "上海" --hospital

# 保存到指定目录
python haodf_search.py "儿科" --output-dir ./haodf_results
```

## API 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | - | 搜索关键词（科室、疾病、医生姓名） |
| city | str | None | 城市（可选） |
| hospital | bool | False | 是否搜索医院 |
| max_results | int | 20 | 最大结果数 |
| port | int | 9333 | 浏览器调试端口 |
| tab_id | str | None | Tab ID |
| stealth | bool | True | 是否启用反检测模式 |
| output_dir | str | None | 输出目录 |
| wait_timeout | int | 30 | 等待超时时间（秒） |

## 返回数据结构

医生结果：
```json
{
  "name": "张三",
  "hospital": "北京协和医院",
  "title": "主任医师",
  "rating": "4.9分",
  "visits": "已服务10000+患者",
  "url": "https://www.haodf.com/wangyu/xxx",
  "type": "doctor"
}
```

医院结果：
```json
{
  "name": "北京协和医院",
  "level": "三级甲等",
  "departments": "内科、外科、妇产科...",
  "url": "https://www.haodf.com/hospital/xxx",
  "type": "hospital"
}
```

## 注意事项

1. 好大夫在线需要登录态，建议使用 dedicated session
2. 医生详情页包含详细履历、患者评价等信息
3. 建议每次搜索间隔 3-5 秒
4. 高频访问可能触发验证码

## 相关文档

- [website-analysis.md](./website-analysis.md) - 网站结构分析
- [anti-detection.md](./anti-detection.md) - 反检测策略
