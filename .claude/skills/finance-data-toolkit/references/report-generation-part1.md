# 研报/周报/月报自动生成模块 (第 1 部分)

覆盖：模板引擎、图表嵌入、多格式导出、定时调度、版本管理。

## 1. 核心架构

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Any
from enum import Enum
from abc import ABC, abstractmethod

class ReportType(Enum):
    DAILY = "daily"           # 日报
    WEEKLY = "weekly"         # 周报
    MONTHLY = "monthly"       # 月报
    QUARTERLY = "quarterly"   # 季报
    EVENT_DRIVEN = "event"    # 事件驱动 (财报发布、重大消息)
    CUSTOM = "custom"         # 定制报告

class OutputFormat(Enum):
    HTML = "html"
    PDF = "pdf"
    MARKDOWN = "md"
    DOCX = "docx"
    EXCEL = "xlsx"
    NOTEBOOK = "ipynb"

@dataclass
class ReportSection:
    title: str
    content: str                    # Markdown/HTML 内容
    charts: List[Dict] = None       # 图表配置
    tables: List[pd.DataFrame] = None
    order: int = 0

@dataclass
class Report:
    report_id: str
    report_type: ReportType
    title: str
    subtitle: str = ""
    generated_at: datetime = field(default_factory=datetime.utcnow)
    period_start: datetime = None
    period_end: datetime = None
    symbols: List[str] = field(default_factory=list)  # 涉及标的
    sections: List[ReportSection] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)      # 版本、数据源、参数等
    
    def add_section(self, section: ReportSection):
        self.sections.append(section)
        self.sections.sort(key=lambda s: s.order)
```

## 2. 模板引擎 (Jinja2)

### 2.1 基础模板系统

```python
from jinja2 import Environment, FileSystemLoader, select_autoescape
import markdown

class ReportTemplateEngine:
    """Jinja2 模板引擎封装"""
    
    def __init__(self, template_dir: str = "templates"):
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # 注册自定义过滤器
        self._register_filters()
    
    def _register_filters(self):
        @self.env.filter
        def format_number(val, decimals=2):
            if val is None: return "-"
            if abs(val) >= 1e8:
                return f"{val/1e8:.{decimals}f}亿"
            if abs(val) >= 1e4:
                return f"{val/1e4:.{decimals}f}万"
            return f"{val:.{decimals}f}"
        
        @self.env.filter
        def format_pct(val, decimals=2):
            if val is None: return "-"
            return f"{val*100:.{decimals}f}%"
        
        @self.env.filter
        def color_pct(val):
            if val is None: return ""
            return "text-red" if val > 0 else ("text-green" if val < 0 else "")
        
        @self.env.filter
        def markdown_to_html(text):
            return markdown.markdown(text, extensions=['tables', 'fenced_code'])
    
    def render(self, template_name: str, context: Dict) -> str:
        template = self.env.get_template(template_name)
        return template.render(**context)
    
    def render_report(self, report: Report, template_name: str = "base.html") -> str:
        """渲染完整报告"""
        context = {
            'report': report,
            'generated_at': report.generated_at.strftime('%Y-%m-%d %H:%M:%S'),
            'period': f"{report.period_start.strftime('%Y-%m-%d')} 至 {report.period_end.strftime('%Y-%m-%d')}" 
                      if report.period_start and report.period_end else "",
        }
        return self.render(template_name, context)
```

### 2.2 模板文件示例

**templates/base.html**
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{{ report.title }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif; line-height: 1.6; max-width: 900px; margin: 0 auto; padding: 20px; }
        h1 { color: #1a1a2e; border-bottom: 2px solid #16213e; padding-bottom: 10px; }
        h2 { color: #16213e; margin-top: 30px; }
        h3 { color: #0f3460; }
        .meta { color: #666; font-size: 0.9em; margin-bottom: 20px; }
        .section { margin-bottom: 30px; page-break-inside: avoid; }
        .chart { text-align: center; margin: 20px 0; }
        .chart img { max-width: 100%; height: auto; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
        th { background: #16213e; color: white; }
        tr:nth-child(even) { background: #f5f5f5; }
        .text-red { color: #e74c3c; }
        .text-green { color: #27ae60; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }
        .badge-bull { background: #ffeaea; color: #e74c3c; }
        .badge-bear { background: #eaffea; color: #27ae60; }
        .footer { margin-top: 50px; padding-top: 20px; border-top: 1px solid #eee; color: #999; font-size: 0.8em; }
    </style>
</head>
<body>
    <h1>{{ report.title }}</h1>
    {% if report.subtitle %}<h3>{{ report.subtitle }}</h3>{% endif %}
    
    <div class="meta">
        生成时间: {{ generated_at }} | 
        覆盖周期: {{ period }} | 
        涉及标的: {{ report.symbols|join(', ') if report.symbols else '全市场' }}
    </div>
    
    {% for section in report.sections %}
    <div class="section">
        <h2>{{ section.title }}</h2>
        {{ section.content|markdown_to_html|safe }}
        
        {% if section.charts %}
        {% for chart in section.charts %}
        <div class="chart">
            <img src="{{ chart.image_path }}" alt="{{ chart.title }}">
            <p style="color: #666; font-size: 0.9em;">{{ chart.title }}</p>
        </div>
        {% endfor %}
        {% endif %}
        
        {% if section.tables %}
        {% for table in section.tables %}
        {{ table.to_html(classes='dataframe', index=False)|safe }}
        {% endfor %}
        {% endif %}
    </div>
    {% endfor %}
    
    <div class="footer">
        <p>数据来源: {{ report.metadata.get('data_sources', '多数据源融合') }}</p>
        <p>免责声明: 本报告仅供参考，不构成投资建议。投资有风险，决策需谨慎。</p>
        <p>报告版本: {{ report.metadata.get('version', '1.0') }}</p>
    </div>
</body>
</html>
```

**templates/weekly_market.html** (周报专用)
```html
{% extends "base.html" %}

{% block extra_sections %}
<div class="section">
    <h2>📈 大盘概览</h2>
    <table>
        <tr><th>指数</th><th>收盘</th><th>涨跌幅</th><th>成交额</th><th>技术面</th></tr>
        {% for idx in market_overview %}
        <tr>
            <td>{{ idx.name }}</td>
            <td>{{ idx.close|format_number }}</td>
            <td class="{{ idx.pct_chg|color_pct }}">{{ idx.pct_chg|format_pct }}</td>
            <td>{{ idx.amount|format_number }}</td>
            <td>{{ idx.tech_signal }}</td>
        </tr>
        {% endfor %}
    </table>
</div>

<div class="section">
    <h2>🔥 板块轮动</h2>
    <table>
        <tr><th>板块</th><th>涨跌幅</th><th>换手率</th><th>领涨股</th><th>资金流向</th></tr>
        {% for sec in sector_rotation %}
        <tr>
            <td>{{ sec.name }}</td>
            <td class="{{ sec.pct_chg|color_pct }}">{{ sec.pct_chg|format_pct }}</td>
            <td>{{ sec.turnover|format_pct }}</td>
            <td>{{ sec.leader }}</td>
            <td class="{{ sec.flow|color_pct }}">{{ sec.flow|format_number }}亿</td>
        </tr>
        {% endfor %}
    </table>
</div>

<div class="section">
    <h2>📰 重大事件</h2>
    <ul>
    {% for event in major_events %}
        <li><strong>{{ event.date }}</strong> {{ event.title }} - {{ event.impact }}</li>
    {% endfor %}
    </ul>
</div>

<div class="section">
    <h2>🎯 重点关注标的</h2>
    <table>
        <tr><th>代码</th><th>名称</th><th>推荐理由</th><th>目标价</th><th>止损价</th><th>评级</th></tr>
        {% for stock in focus_stocks %}
        <tr>
            <td>{{ stock.symbol }}</td>
            <td>{{ stock.name }}</td>
            <td>{{ stock.reason }}</td>
            <td>{{ stock.target_price|format_number }}</td>
            <td>{{ stock.stop_loss|format_number }}</td>
            <td><span class="badge badge-{{ 'bull' if stock.rating=='买入' else 'bear' }}">{{ stock.rating }}</span></td>
        </tr>
        {% endfor %}
    </table>
</div>
{% endblock %}
```

## 3. 图表生成与嵌入

```python
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # 无头模式
import plotly.graph_objects as go
import plotly.io as pio
from typing import Union
import base64
from io import BytesIO

class ChartGenerator:
    """图表生成器：支持 matplotlib (静态) 和 plotly (交互)"""
    
    def __init__(self, output_dir: str = "charts", dpi: int = 150):
        self.output_dir = output_dir
        self.dpi = dpi
        import os
        os.makedirs(output_dir, exist_ok=True)
    
    # === Matplotlib 静态图表 (用于 PDF/Word) ===
    
    def kline_chart(self, df: pd.DataFrame, symbol: str,
                    mas: List[int] = [5, 20, 60],
                    volume: bool = True) -> str:
        """K 线图 + 均线 + 成交量"""
        fig, axes = plt.subplots(2, 1, figsize=(12, 8), 
                                  gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
        
        # K 线
        from mplfinance.original_flavor import candlestick_ohlc
        import matplotlib.dates as mdates
        
        ohlc = df[['open', 'high', 'low', 'close']].reset_index()
        ohlc['date'] = ohlc['date'].map(mdates.date2num)
        candlestick_ohlc(axes[0], ohlc.values, width=0.6, 
                         colorup='red', colordown='green', alpha=0.8)
        
        # 均线
        for ma in mas:
            if f'MA{ma}' in df.columns:
                axes[0].plot(df.index, df[f'MA{ma}'], label=f'MA{ma}', linewidth=1)
        axes[0].legend()
        axes[0].set_title(f'{symbol} K线图')
        axes[0].grid(True, alpha=0.3)
        
        # 成交量
        colors = ['red' if c >= o else 'green' for c, o in zip(df['close'], df['open'])]
        axes[1].bar(df.index, df['volume'], color=colors, alpha=0.6, width=0.8)
        axes[1].set_ylabel('Volume')
        axes[1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        path = f"{self.output_dir}/{symbol}_kline_{datetime.now().strftime('%Y%m%d')}.png"
        plt.savefig(path, dpi=self.dpi, bbox_inches='tight')
        plt.close()
        return path
    
    def factor_heatmap(self, factor_matrix: pd.DataFrame, title: str = "因子相关性热力图") -> str:
        """因子相关性/IC 热力图"""
        fig, ax = plt.subplots(figsize=(10, 8))
        im = ax.imshow(factor_matrix.corr(), cmap='RdBu_r', vmin=-1, vmax=1)
        ax.set_xticks(range(len(factor_matrix.columns)))
        ax.set_yticks(range(len(factor_matrix.columns)))
        ax.set_xticklabels(factor_matrix.columns, rotation=45, ha='right')
        ax.set_yticklabels(factor_matrix.columns)
        plt.colorbar(im, ax=ax)
        ax.set_title(title)
        
        path = f"{self.output_dir}/factor_heatmap_{datetime.now().strftime('%Y%m%d')}.png"
        plt.savefig(path, dpi=self.dpi, bbox_inches='tight')
        plt.close()
        return path
    
    def performance_chart(self, returns: pd.Series, benchmark: pd.Series = None) -> str:
        """净值曲线 + 基准对比"""
        fig, ax = plt.subplots(figsize=(12, 6))
        
        nav = (1 + returns).cumprod()
        ax.plot(nav.index, nav.values, label='策略', linewidth=2, color='#16213e')
        
        if benchmark is not None:
            bench_nav = (1 + benchmark).cumprod()
            ax.plot(bench_nav.index, bench_nav.values, label='基准', linewidth=1.5, color='#e74c3c', alpha=0.8)
        
        ax.legend()
        ax.set_title('策略净值曲线')
        ax.grid(True, alpha=0.3)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.2f}'))
        
        path = f"{self.output_dir}/performance_{datetime.now().strftime('%Y%m%d')}.png"
        plt.savefig(path, dpi=self.dpi, bbox_inches='tight')
        plt.close()
        return path
    
    # === Plotly 交互图表 (用于 HTML/Notebook) ===
    
    def interactive_kline(self, df: pd.DataFrame, symbol: str) -> str:
        """交互式 K 线图"""
        fig = go.Figure()
        
        fig.add_trace(go.Candlestick(
            x=df.index,
            open=df['open'], high=df['high'],
            low=df['low'], close=df['close'],
            name='K线',
            increasing_line_color='red',
            decreasing_line_color='green',
        ))
        
        # 均线
        for ma in [5, 20, 60]:
            col = f'MA{ma}'
            if col in df.columns:
                fig.add_trace(go.Scatter(x=df.index, y=df[col], name=col, line=dict(width=1)))
        
        fig.update_layout(
            title=f'{symbol} 交互式 K线',
            xaxis_rangeslider_visible=False,
            height=600,
            template='plotly_white',
        )
        
        path = f"{self.output_dir}/{symbol}_kline_interactive.html"
        pio.write_html(fig, path)
        return path
    
    def factor_ic_timeseries(self, ic_df: pd.DataFrame) -> str:
        """因子 IC 时间序列"""
        fig = go.Figure()
        for col in ic_df.columns:
            fig.add_trace(go.Scatter(x=ic_df.index, y=ic_df[col], name=col, mode='lines'))
        fig.add_hline(y=0, line_dash='dash', line_color='gray')
        fig.add_hline(y=0.05, line_dash='dot', line_color='green', annotation_text='IC>0.05')
        fig.add_hline(y=-0.05, line_dash='dot', line_color='red', annotation_text='IC<-0.05')
        fig.update_layout(title='因子 IC 时间序列', height=500, template='plotly_white')
        
        path = f"{self.output_dir}/factor_ic_ts.html"
        pio.write_html(fig, path)
        return path
    
    def portfolio_allocation_pie(self, weights: pd.Series) -> str:
        """持仓饼图"""
        fig = go.Figure(data=[go.Pie(
            labels=weights.index,
            values=weights.values,
            hole=0.3,
            textinfo='label+percent',
        )])
        fig.update_layout(title='组合持仓分布', height=400)
        
        path = f"{self.output_dir}/allocation_pie.html"
        pio.write_html(fig, path)
        return path
    
    # === 通用：图片转 base64 (内嵌 HTML) ===
    def image_to_base64(self, image_path: str) -> str:
        with open(image_path, 'rb') as f:
            return base64.b64encode(f.read()).decode()
```