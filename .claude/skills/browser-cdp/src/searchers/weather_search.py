#!/usr/bin/env python
"""
weather_search.py - 中国天气网搜索器

使用 browser-cdp skill 搜索中国天气网，获取城市当前天气和未来几天预报。

用法:
    python weather_search.py "北京"
    python weather_search.py "上海" --days 7
    python weather_search.py "广州" --port 9333 --output-dir ./weather_results

示例:
    python weather_search.py "北京" --days 7
    python weather_search.py "深圳" --output-dir ./weather_results
"""

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import random_delay, save_results, dedup_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 中国天气网专用配置 ==========
WEATHER_BASE = "https://www.weather.com.cn"
WEATHER_SEARCH_URL = f"{WEATHER_BASE}/search.jsp?search=1&dataset=city&word={quote('{city}')}"  # 占位符，实际拼接
WEATHER_FORECAST_URL = f"{WEATHER_BASE}/weather/{{city_id}}.shtml"  # 城市详情页

# 默认输出目录
WEATHER_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "weather"


class WeatherSearcher(BaseSearcher):
    """中国天气网搜索器"""

    @property
    def source_name(self) -> str:
        return "weather_com_cn"

    @property
    def supported_types(self) -> List[str]:
        return ["weather_search", "weather_forecast", "city_weather"]

    def search(
        self,
        query: str,
        days: int = 7,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索城市天气

        Args:
            query: 城市名称
            days: 预报天数 (1-7)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            天气结果列表
        """
        print(f"[中国天气网] 正在搜索城市天气: {query}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 步骤1: 搜索城市，获取城市ID
        search_url = f"{WEATHER_BASE}/search.jsp?search=1&dataset=city&word={quote(query)}"
        print(f"  [URL] 搜索城市: {search_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-result-list, .s-result-list, .city-list",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(1.5)

        # 使用 JS 提取城市搜索结果
        js_city_search = r"""
(() => {
  const results = [];
  // 中国天气网搜索列表选择器
  const items = document.querySelectorAll('.search-result-list li, .s-result-list li, .city-list li, .result-item');
  items.forEach((item, i) => {
    if (i >= 10) return;
    const linkEl = item.querySelector('a');
    const titleEl = item.querySelector('a, .city-name, .name');
    const title = titleEl ? titleEl.innerText.trim() : '';
    const href = linkEl ? linkEl.href : '';
    // 提取城市ID（从URL中解析）
    let cityId = '';
    if (href) {
      const match = href.match(/weather\/(\d+)\.shtml/);
      if (match) cityId = match[1];
    }
    if (title && cityId) {
      results.push({ title, city_id: cityId, url: href });
    }
  });
  return results;
})()
"""
        city_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_city_search,
        ])

        if city_result.returncode != 0:
            print(f"[错误] 城市搜索提取失败: {city_result.stderr[:200]}")
            return []

        try:
            cities = json.loads(city_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {city_result.stdout[:200]}")
            return []

        if not cities:
            print(f"[提示] 未找到城市 '{query}' 的天气数据，尝试直接访问天气页面...")
            # 尝试直接访问城市天气页
            return self._fetch_city_weather(query, port, tab_id, days, stealth, output_dir, wait_timeout)

        # 取第一个匹配城市
        city = cities[0]
        city_id = city.get("city_id")
        city_name = city.get("title", query)
        print(f"  [城市] 匹配到: {city_name} (ID: {city_id})")

        # 步骤2: 导航到城市天气详情页
        detail_url = f"{WEATHER_BASE}/weather/{city_id}.shtml"
        print(f"  [URL] 天气详情: {detail_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", detail_url,
            "--wait-selector", ".t clearfix, .forecast, .weather-widget",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 步骤3: 提取天气数据
        weather_data = self._extract_weather_data(port, tab_id, city_name, city_id)

        if not weather_data:
            print("[错误] 天气数据提取失败")
            return []

        print(f"  [结果] 成功提取 {city_name} 的天气数据")

        # 保存结果
        if output_dir:
            path = save_results(
                [weather_data],
                output_dir,
                f"weather_{city_name}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return [weather_data]

    def _fetch_city_weather(
        self,
        query: str,
        port: int,
        tab_id: str,
        days: int,
        stealth: bool,
        output_dir: Optional[str],
        wait_timeout: int,
    ) -> List[Dict]:
        """直接访问城市天气页面（无搜索结果时）"""
        # 尝试常见城市ID映射
        city_id_map = {
            "北京": "101010100",
            "上海": "101020100",
            "广州": "101280101",
            "深圳": "101280601",
            "杭州": "101210101",
            "南京": "101190101",
            "成都": "101270101",
            "武汉": "101200101",
            "西安": "101110101",
            "重庆": "101040100",
            "天津": "101030100",
            "长沙": "101250101",
            "郑州": "101180101",
            "沈阳": "101070101",
            "哈尔滨": "101050101",
            "济南": "101120101",
            "福州": "101230101",
            "南昌": "101240101",
            "昆明": "101290101",
            "贵阳": "101260101",
            "大连": "101070501",
            "青岛": "101120301",
            "厦门": "101230301",
            "宁波": "101210501",
            "苏州": "101190401",
            "无锡": "101190601",
            "合肥": "101220101",
            "石家庄": "101090101",
            "太原": "101100101",
            "兰州": "101160101",
            "乌鲁木齐": "101130101",
            "拉萨": "101310101",
            "呼和浩特": "101080101",
            "南宁": "101300101",
            "海口": "101310101",
            "长春": "101060101",
            "长春": "101060101",
        }

        city_id = city_id_map.get(query)
        if not city_id:
            print(f"[错误] 未找到城市 '{query}' 的天气数据，请确认城市名称是否正确")
            return []

        detail_url = f"{WEATHER_BASE}/weather/{city_id}.shtml"
        print(f"  [URL] 直接访问: {detail_url}")

        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", detail_url,
            "--wait-selector", ".t clearfix, .forecast",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)
        weather_data = self._extract_weather_data(port, tab_id, query, city_id)

        if not weather_data:
            return []

        if output_dir:
            path = save_results(
                [weather_data],
                output_dir,
                f"weather_{query}_{int(time.time())}.json"
            )
            print(f"  [保存] {path}")

        return [weather_data]

    def _extract_weather_data(
        self,
        port: int,
        tab_id: str,
        city_name: str,
        city_id: str,
    ) -> Optional[Dict]:
        """提取天气数据（当前天气 + 未来预报）"""
        # 提取当前天气
        js_current = r"""
(() => {
  const result = {};
  // 当前天气
  const tempEl = document.querySelector('.tem');
  if (tempEl) {
    const span = tempEl.querySelector('span');
    const i = tempEl.querySelector('i');
    result.current_temp = (span ? span.innerText.trim() : '') + (i ? i.innerText.trim() : '');
  }
  // 天气状况
  const weaEl = document.querySelector('.wea');
  result.current_weather = weaEl ? weaEl.innerText.trim() : '';
  // 风向
  const windEl = document.querySelector('.wind');
  result.wind = windEl ? windEl.innerText.trim() : '';
  // 湿度
  const humiEl = document.querySelector('.humi');
  result.humidity = humiEl ? humiEl.innerText.trim() : '';
  // 空气质量
  const aqiEl = document.querySelector('.aqi');
  result.aqi = aqiEl ? aqiEl.innerText.trim() : '';
  // 更新时间
  const timeEl = document.querySelector('.time');
  result.update_time = timeEl ? timeEl.innerText.trim() : '';
  return result;
})()
"""
        current_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_current,
        ])

        current_weather = {}
        if current_result.returncode == 0:
            try:
                current_weather = json.loads(current_result.stdout)
            except json.JSONDecodeError:
                print(f"[警告] 当前天气解析失败: {current_result.stdout[:100]}")

        # 提取未来预报（7日）
        js_forecast = r"""
(() => {
  const forecasts = [];
  // 7日预报容器
  const forecastItems = document.querySelectorAll('.t clearfix li, .forecast li, .weather li');
  forecastItems.forEach((item, i) => {
    if (i >= 7) return;
    const dateEl = item.querySelector('h1, .date, .day');
    const weaEl = item.querySelector('p.wea, .weather-desc');
    const tempEl = item.querySelector('p.tem, .temperature');
    const windEl = item.querySelector('p.wind, .wind');

    const date = dateEl ? dateEl.innerText.trim() : '';
    const weather = weaEl ? weaEl.innerText.trim() : '';
    const temp = tempEl ? tempEl.innerText.trim() : '';
    const wind = windEl ? windEl.innerText.trim() : '';

    if (date) {
      forecasts.push({
        date: date,
        weather: weather,
        temperature: temp,
        wind: wind,
      });
    }
  });
  return forecasts;
})()
"""
        forecast_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_forecast,
        ])

        forecasts = []
        if forecast_result.returncode == 0:
            try:
                forecasts = json.loads(forecast_result.stdout)
            except json.JSONDecodeError:
                print(f"[警告] 预报解析失败: {forecast_result.stdout[:100]}")

        # 组装结果
        result = {
            "source": "weather_com_cn",
            "city": city_name,
            "city_id": city_id,
            "current": current_weather,
            "forecast": forecasts,
            "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "url": f"{WEATHER_BASE}/weather/{city_id}.shtml",
        }

        return result

    def get_detail(
        self,
        city: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        days: int = 7,
    ) -> Dict:
        """获取指定城市天气详情"""
        results = self.search(
            query=city,
            days=days,
            port=port,
            tab_id=tab_id,
            stealth=stealth,
        )
        if results:
            return results[0]
        return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="中国天气网搜索器 - 获取城市天气预报",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python weather_search.py "北京"
    python weather_search.py "上海" --days 7
    python weather_search.py "广州" --port 9333 --output-dir ./weather_results
"""
    )

    parser.add_argument("query", help="城市名称（如：北京、上海、广州）")
    parser.add_argument("--days", type=int, default=7, choices=range(1, 8),
                        help="预报天数 (1-7, 默认: 7)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = WeatherSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        days=args.days,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共获取 {len(results)} 个城市天气数据")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未获取到天气数据")


if __name__ == "__main__":
    main()
