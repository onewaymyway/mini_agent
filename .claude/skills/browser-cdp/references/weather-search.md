---
name: weather-search
skill: browser-cdp
script: weather_search.py
description: 中国天气网搜索器，支持搜索城市当前天气和未来几天预报，输出 JSON 格式结果。
triggers: 天气搜索, weather search, 天气预报, 中国天气网, weather_search.py
platforms: windows, macos, linux, pc
---

# 中国天气网搜索器 (`weather_search.py`)

## 用途

使用 browser-cdp skill 搜索中国天气网（weather.com.cn），获取指定城市的当前天气状况和未来几天天气预报。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索北京天气
python src/searchers/weather_search.py "北京"

# 搜索上海天气，获取7天预报
python src/searchers/weather_search.py "上海" --days 7

# 指定端口和输出目录
python src/searchers/weather_search.py "广州" --port 9333 --output-dir ./weather_results

# 禁用反检测模式
python src/searchers/weather_search.py "深圳" --no-stealth
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 城市名称（必填） | - |
| `--days` | 预报天数 (1-7) | 7 |
| `--output-dir` | 输出目录 | `./search_results/weather` |
| `--port` | CDP 调试端口 | 9333 |
| `--tab` | Tab ID（可选，不指定则自动创建） | 自动创建 |
| `--stealth` | 启用反检测模式 | True |
| `--no-stealth` | 禁用反检测模式 | - |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "source": "weather_com_cn",
  "city": "北京",
  "city_id": "101010100",
  "current": {
    "current_temp": "25℃",
    "current_weather": "晴",
    "wind": "东南风3-4级",
    "humidity": "湿度: 45%",
    "aqi": "AQI: 65 良",
    "update_time": "更新时间: 2026-08-04 08:00"
  },
  "forecast": [
    {
      "date": "08月04日 周二",
      "weather": "晴",
      "temperature": "25℃ / 15℃",
      "wind": "东南风3-4级"
    },
    {
      "date": "08月05日 周三",
      "weather": "多云",
      "temperature": "27℃ / 16℃",
      "wind": "南风3-4级"
    }
  ],
  "scraped_at": "2026-08-04 08:24:50",
  "url": "https://www.weather.com.cn/weather/101010100.shtml"
}
```

## 核心实现要点

- 继承 `BaseSearcher` 基类，实现 `search()` 和 `get_detail()` 方法
- 使用 `browser_nav.py` 导航到天气页面
- 使用 `browser_console.py` 执行 JS 提取天气数据
- 先搜索城市获取城市ID，再访问城市天气详情页
- 提取当前天气（温度、天气状况、风向、湿度、空气质量）
- 提取未来7天天气预报（日期、天气、温度、风向）
- 输出 JSON 格式结果，支持保存到文件

## 内置城市ID映射

为提升效率，内置了以下常见城市的ID映射（当搜索无结果时自动使用）：

北京、上海、广州、深圳、杭州、南京、成都、武汉、西安、重庆、天津、长沙、郑州、沈阳、哈尔滨、济南、福州、南昌、昆明、贵阳、大连、青岛、厦门、宁波、苏州、无锡、合肥、石家庄、太原、兰州、乌鲁木齐、拉萨、呼和浩特、南宁、海口、长春

## 注意事项

- 首次使用需确保浏览器服务已启动（端口 9333）
- 中国天气网无需登录即可访问天气数据
- 搜索频率不宜过高，避免触发反爬机制
- 部分城市名称可能需要使用标准名称（如"广州市"而非"广州"）
- 天气数据更新频率约为每小时一次
