"""
src/adapters/mixins/chart_extractor.py

图表数据提取器：从 ECharts / AntV G2 / Chart.js 实例中提取数据。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ChartExtractor:
    """
    通用图表数据提取器，支持多种前端图表库。
    
    支持的图表库：
    - ECharts (echarts.getInstanceByDom)
    - AntV G2 (element.chart)
    - Chart.js (element.chart)
    - D3.js (直接遍历 SVG/Canvas)
    """
    
    # 各图表库的全局实例查询模式
    CHART_INSTANCES = {
        "echarts": "echarts.getInstanceByDom(el)",
        "antv_g2": "el.chart || el.__chart__",
        "chartjs": "el.chart",
    }
    
    def __init__(self, **kwargs):
        self._timeout_ms = kwargs.get("timeout_ms", 10000)
    
    async def extract_from_page(self, page, container_selector: str = ".echarts-container") -> List[Dict]:
        """
        从页面提取所有图表数据。
        
        Args:
            page: Playwright page 对象
            container_selector: 图表容器选择器
        
        Returns:
            图表数据列表，每项包含 chart_type, title, series, xAxis, yAxis
        """
        try:
            data = await page.evaluate(f'''
                (selector) => {{
                    const containers = document.querySelectorAll(selector);
                    const results = [];
                    
                    for (const el of containers) {{
                        let chartData = null;
                        let chartType = 'unknown';
                        
                        // 尝试 ECharts
                        if (typeof echarts !== 'undefined') {{
                            const instance = echarts.getInstanceByDom(el);
                            if (instance) {{
                                chartType = 'echarts';
                                chartData = this._extractECharts(instance);
                            }}
                        }}
                        
                        // 尝试 AntV G2
                        if (!chartData) {{
                            const g2Chart = el.chart || el.__chart__;
                            if (g2Chart) {{
                                chartType = 'antv_g2';
                                chartData = this._extractG2(g2Chart);
                            }}
                        }}
                        
                        // 尝试 Chart.js
                        if (!chartData) {{
                            const jsChart = el.chart;
                            if (jsChart && jsChart.config) {{
                                chartType = 'chartjs';
                                chartData = this._extractChartJS(jsChart);
                            }}
                        }}
                        
                        if (chartData) {{
                            chartData.containerSelector = selector;
                            chartData.chartType = chartType;
                            results.push(chartData);
                        }}
                    }}
                    
                    return results;
                }}
            ''', container_selector)
            return data or []
        except Exception as e:
            logger.warning(f"图表数据提取失败: {e}")
            return []
    
    async def _extract_echarts_option(self, page, selector: str) -> Optional[Dict]:
        """直接从 ECharts 选项对象提取"""
        try:
            option = await page.evaluate(f'''
                () => {{
                    const el = document.querySelector('{selector}');
                    if (!el || typeof echarts === 'undefined') return null;
                    const instance = echarts.getInstanceByDom(el);
                    if (!instance) return null;
                    return instance.getOption();
                }}
            ''')
            return option if option else None
        except Exception as e:
            logger.debug(f"ECharts option 提取失败: {e}")
            return None
    
    async def extract_table_from_chart(self, page, selector: str = ".echarts-container") -> List[Dict]:
        """
        将图表数据转换为表格格式。
        适用于饼图/柱状图/折线图的数据导出。
        """
        option = await self._extract_echarts_option(page, selector)
        if not option:
            return []
        
        series = option.get("series", [])
        xAxis = option.get("xAxis", {})
        categories = []
        
        # 提取类目轴
        if isinstance(xAxis, list):
            for axis in xAxis:
                if axis.get("type") == "category" and axis.get("data"):
                    categories = axis["data"]
        elif isinstance(xAxis, dict):
            if xAxis.get("data"):
                categories = xAxis["data"]
        
        # 提取系列数据
        tables = []
        for si, series_item in enumerate(series):
            if series_item.get("type") in ["bar", "line", "pie", "scatter"]:
                table_row = {
                    "series_name": series_item.get("name", f"series_{si}"),
                    "series_type": series_item.get("type"),
                    "data": series_item.get("data", []),
                }
                tables.append(table_row)
        
        return {"categories": categories, "series": tables}
    
    def parse_echarts_option(self, option: Dict) -> Dict:
        """
        解析 ECharts option 为结构化数据。
        """
        result = {
            "title": option.get("title", {}).get("text", ""),
            "tooltip": option.get("tooltip", {}).get("trigger", "item"),
            "legend": [],
            "xAxis": [],
            "yAxis": [],
            "series": [],
        }
        
        # 图例
        legend = option.get("legend", {})
        if isinstance(legend, dict):
            result["legend"] = legend.get("data", [])
        elif isinstance(legend, list):
            result["legend"] = legend
        
        # X轴
        xAxis = option.get("xAxis", [])
        if isinstance(xAxis, list):
            for ax in xAxis:
                result["xAxis"].append({
                    "type": ax.get("type", "category"),
                    "data": ax.get("data", []),
                })
        elif isinstance(xAxis, dict):
            result["xAxis"].append({
                "type": xAxis.get("type", "category"),
                "data": xAxis.get("data", []),
            })
        
        # Y轴
        yAxis = option.get("yAxis", [])
        if isinstance(yAxis, list):
            for ay in yAxis:
                result["yAxis"].append({
                    "type": ay.get("type", "value"),
                    "name": ay.get("name", ""),
                })
        
        # 系列
        for s in option.get("series", []):
            result["series"].append({
                "name": s.get("name", ""),
                "type": s.get("type", ""),
                "data": s.get("data", []),
            })
        
        return result
    
    def to_dataframe(self, option: Dict) -> Optional[List[Dict]]:
        """
        将 ECharts 数据转换为 DataFrame 格式（列表字典）。
        方便 pandas 导入。
        """
        parsed = self.parse_echarts_option(option)
        categories = parsed["xAxis"][0].get("data", []) if parsed["xAxis"] else []
        
        if not categories:
            return None
        
        rows = []
        for series in parsed["series"]:
            data = series.get("data", [])
            for i, val in enumerate(data):
                row = {cat: "" for cat in categories}
                if i < len(categories):
                    row[categories[i]] = val
                row["__series__"] = series["name"]
                row["__type__"] = series["type"]
                rows.append(row)
        
        return rows


__all__ = ["ChartExtractor"]
