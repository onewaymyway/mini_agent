        # ===== 风险提示 =====
        risk_factors = []
        # 财务风险
        if financials['debt_to_asset'].iloc[-1] > 0.7:
            risk_factors.append("资产负债率超过 70%，偿债压力大")
        if financials['operating_cash_flow'].iloc[-1] < 0:
            risk_factors.append("经营性现金流为负，现金流恶化")
        # 估值风险
        if valuation_percentile > 90:
            risk_factors.append("估值处于历史高位分位，回调风险大")
        # 技术风险
        if tech_signals['trend'] == 'down' and tech_signals['ma20'] < tech_signals['ma60']:
            risk_factors.append("技术面趋势向下，均线空头排列")
        # 舆情风险
        if news_sentiment['avg_score'] < -0.5:
            risk_factors.append("近期舆情显著偏负面，关注突发利空")
        
        # ===== 生成图表 =====
        charts = []
        charts.append(create_financial_dashboard(financials, dupont, growth))
        charts.append(create_valuation_chart(hist_valuation, dcf_result, peer_valuation))
        charts.append(create_technical_chart(daily_k, weekly_k, tech_signals, support_resistance))
        charts.append(create_flow_chart(main_flow, north_flow, lhb_detail))
        charts.append(create_sentiment_chart(news_sentiment, guba_sentiment))
        charts.append(create_risk_radar(risk_factors))
        
        # ===== 渲染报告 =====
        html = self.template.render(
            report_title=f"{await self.data.get_stock_name(symbol)} ({symbol}) 深度研究报告",
            symbol=symbol,
            as_of=as_of,
            generated_at=datetime.now(),
            # 基本面
            financials=financials,
            dupont=dupont,
            growth=growth,
            cashflow_quality=cashflow_quality,
            # 估值
            hist_valuation=hist_valuation,
            valuation_percentile=valuation_percentile,
            dcf_result=dcf_result,
            peer_valuation=peer_valuation,
            # 技术面
            tech_signals=tech_signals,
            support_resistance=support_resistance,
            # 资金
            main_flow=main_flow,
            north_flow=north_flow,
            lhb_detail=lhb_detail,
            # 舆情
            news_sentiment=news_sentiment,
            guba_sentiment=guba_sentiment,
            # 风险
            risk_factors=risk_factors,
            # 图表
            charts=charts,
            disclaimer="本报告基于公开数据自动生成，仅供参考，不构成投资建议。投资有风险，决策需谨慎。"
        )
        
        return RenderedReport(
            report_type="deep_dive",
            symbol=symbol,
            title=f"{symbol} 深度研究报告 {as_of}",
            html_content=html,
            generated_at=datetime.now(),
            period=f"as_of_{as_of}",
            chart_images=charts,
            dataframes={
                'financials': financials,
                'valuation': peer_valuation,
                'technical': tech_signals,
                'flow': main_flow
            }
        )
```

---

## 5. 模板文件示例

### 5.1 周报模板 (weekly_report.html.j2)
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{{ report_title }}</title>
    <style>
        body { font-family: 'Microsoft YaHei', sans-serif; line-height: 1.6; margin: 0; padding: 20px; color: #333; }
        .container { max-width: 900px; margin: 0 auto; }
        .header { text-align: center; border-bottom: 2px solid #2F5496; padding-bottom: 20px; margin-bottom: 30px; }
        .header h1 { color: #2F5496; margin: 0; }
        .header .meta { color: #666; font-size: 14px; margin-top: 10px; }
        .section { margin-bottom: 40px; page-break-inside: avoid; }
        .section h2 { color: #2F5496; border-left: 4px solid #2F5496; padding-left: 12px; }
        .section h3 { color: #4472C4; }
        .chart { text-align: center; margin: 20px 0; }
        .chart img { max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }
        .chart .caption { color: #666; font-size: 12px; margin-top: 8px; }
        .stock-card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 16px 0; background: #fafafa; }
        .stock-card h3 { margin-top: 0; color: #2F5496; }
        .metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
        .metric { background: white; padding: 12px; border-radius: 4px; border: 1px solid #eee; }
        .metric .label { font-size: 12px; color: #666; }
        .metric .value { font-size: 18px; font-weight: bold; color: #333; }
        .metric .value.up { color: #C00000; }
        .metric .value.down { color: #0070C0; }
        table { width: 100%; border-collapse: collapse; margin: 16px 0; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #2F5496; color: white; }
        tr:hover { background: #f5f5f5; }
        .disclaimer { font-size: 12px; color: #999; text-align: center; margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; }
        .no-print { display: none; }
        @media print { .no-print { display: none; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ report_title }}</h1>
            <div class="meta">周期：{{ period }} | 生成时间：{{ generated_at.strftime('%Y-%m-%d %H:%M') }}</div>
        </div>
        
        <!-- 大盘指数 -->
        <div class="section">
            <h2>📈 大盘指数表现</h2>
            <div class="metric-grid">
                {% for idx in indices %}
                <div class="metric">
                    <div class="label">{{ idx.name }}</div>
                    <div class="value {{ 'up' if idx.change_pct > 0 else 'down' }}">{{ "%+.2f%%"|format(idx.change_pct) }}</div>
                    <div class="label">收盘：{{ idx.close }}</div>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <!-- 板块轮动 -->
        <div class="section">
            <h2>🔥 板块轮动热力图</h2>
            <div class="chart">
                <img src="{{ sector_chart }}" alt="板块轮动">
                <div class="caption">板块涨跌幅热力图（红涨绿跌）</div>
            </div>
            <table>
                <thead><tr><th>板块</th><th>涨跌幅</th><th>换手率</th><th>领涨股</th></tr></thead>
                <tbody>
                    {% for sec in sector_performance[:10] %}
                    <tr>
                        <td>{{ sec.name }}</td>
                        <td class="{{ 'up' if sec.change_pct > 0 else 'down' }}">{{ "%+.2f%%"|format(sec.change_pct) }}</td>
                        <td>{{ "%.2f%%"|format(sec.turnover) }}</td>
                        <td>{{ sec.leader }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        
        <!-- 资金流向 -->
        <div class="section">
            <h2>💰 市场资金流向</h2>
            <div class="chart">
                <img src="{{ flow_chart }}" alt="资金流向">
                <div class="caption">主力/北向/两融资金流向桑基图</div>
            </div>
            <div class="metric-grid">
                <div class="metric"><div class="label">主力净流入</div><div class="value {{ 'up' if flow_data.main_net_inflow > 0 else 'down' }}">{{ "%+.2f亿"|format(flow_data.main_net_inflow/1e8) }}</div></div>
                <div class="metric"><div class="label">北向净流入</div><div class="value {{ 'up' if flow_data.north_net_inflow > 0 else 'down' }}">{{ "%+.2f亿"|format(flow_data.north_net_inflow/1e8) }}</div></div>
                <div class="metric"><div class="label">两融余额变化</div><div class="value">{{ "%+.2f%%"|format(flow_data.margin_change_pct) }}</div></div>
            </div>
        </div>
        
        <!-- 重点标的 -->
        <div class="section">
            <h2>🎯 重点标的跟踪</h2>
            {% for stock in stock_analyses %}
            <div class="stock-card">
                <h3>{{ stock.name }} ({{ stock.symbol }})</h3>
                <div class="metric-grid">
                    <div class="metric"><div class="label">周涨跌幅</div><div class="value {{ 'up' if stock.weekly_change > 0 else 'down' }}">{{ "%+.2f%%"|format(stock.weekly_change*100) }}</div></div>
                    <div class="metric"><div class="label">RSI(14)</div><div class="value">{{ "%.1f"|format(stock.indicators.rsi) }}</div></div>
                    <div class="metric"><div class="label">MACD</div><div class="value">{{ stock.indicators.macd_signal }}</div></div>
                    <div class="metric"><div class="label">北向持仓变化</div><div class="value {{ 'up' if stock.northbound_change > 0 else 'down' }}">{{ "%+.2f%%"|format(stock.northbound_change*100) }}</div></div>
                </div>
                <div class="chart"><img src="{{ stock.chart_path }}" alt="{{ stock.symbol }} 技术面"></div>
            </div>
            {% endfor %}
        </div>
        
        <!-- 重要事件 -->
        <div class="section">
            <h2>📅 下周重要事件日历</h2>
            <table>
                <thead><tr><th>日期</th><th>时间</th><th>事件</th><th>重要性</th><th>前值</th><th>预测值</th></tr></thead>
                <tbody>
                    {% for evt in events %}
                    <tr>
                        <td>{{ evt.date }}</td>
                        <td>{{ evt.time }}</td>
                        <td>{{ evt.event }}</td>
                        <td><span class="badge badge-{{ evt.importance }}">{{ evt.importance }}</span></td>
                        <td>{{ evt.previous }}</td>
                        <td>{{ evt.forecast }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        
        <div class="disclaimer">{{ disclaimer }}</div>
    </div>
</body>
</html>
```

### 5.2 深度研报模板 (deep_dive_report.html.j2)
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{{ report_title }}</title>
    <style>
        /* 复用周报样式，新增： */
        .toc { background: #f5f5f5; padding: 20px; border-radius: 8px; margin-bottom: 30px; }
        .toc ul { list-style: none; padding-left: 0; }
        .toc li { margin: 8px 0; }
        .toc a { color: #2F5496; text-decoration: none; }
        .toc a:hover { text-decoration: underline; }
        .valuation-table { width: 100%; }
        .valuation-table td { padding: 8px; }
        .risk-high { border-left: 4px solid #C00000; background: #fff0f0; padding: 12px; margin: 8px 0; border-radius: 4px; }
        .risk-medium { border-left: 4px solid #ED7D31; background: #fff8f0; padding: 12px; margin: 8px 0; border-radius: 4px; }
        .risk-low { border-left: 4px solid #548235; background: #f0fff0; padding: 12px; margin: 8px 0; border-radius: 4px; }
        .kpi-card { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 16px; text-align: center; }
        .kpi-value { font-size: 28px; font-weight: bold; color: #2F5496; }
        .kpi-label { font-size: 14px; color: #666; margin-top: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ report_title }}</h1>
            <div class="meta">基准日期：{{ as_of }} | 生成时间：{{ generated_at.strftime('%Y-%m-%d %H:%M') }}</div>
        </div>
        
        <!-- 目录 -->
        <div class="toc">
            <h3>📋 目录</h3>
            <ul>
                <li><a href="#fundamental">1. 基本面分析</a></li>
                <li><a href="#valuation">2. 估值分析</a></li>
                <li><a href="#technical">3. 技术面分析</a></li>
                <li><a href="#flow">4. 资金流向</a></li>
                <li><a href="#sentiment">5. 舆情分析</a></li>
                <li><a href="#risk">6. 风险提示</a></li>
            </ul>
        </div>
        
        <!-- 1. 基本面 -->
        <div class="section" id="fundamental">
            <h2>📊 基本面分析</h2>
            <div class="metric-grid">
                <div class="kpi-card"><div class="kpi-value">{{ "%.2f"|format(financials.roe.iloc[-1]*100) }}%</div><div class="kpi-label">ROE (最新)</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ "%.2f"|format(growth.revenue_yoy*100) }}%</div><div class="kpi-label">营收同比</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ "%.2f"|format(growth.profit_yoy*100) }}%</div><div class="kpi-label">净利同比</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ "%.2f"|format(cashflow_quality.ocf_to_net_profit) }}</div><div class="kpi-label">经营现金流/净利润</div></div>
            </div>
            
            <h3>杜邦分析</h3>
            <table class="valuation-table">
                <tr><th>指标</th><th>最新</th><th>同比</th><th>环比</th></tr>
                <tr><td>净利率</td><td>{{ "%.2f%%"|format(dupont.net_margin*100) }}</td><td>{{ "%+.2f%%"|format(dupont.net_margin_yoy*100) }}</td><td>{{ "%+.2f%%"|format(dupont.net_margin_qoq*100) }}</td></tr>
                <tr><td>资产周转率</td><td>{{ "%.2f"|format(dupont.asset_turnover) }}</td><td>{{ "%+.2f%%"|format(dupont.asset_turnover_yoy*100) }}</td><td>{{ "%+.2f%%"|format(dupont.asset_turnover_qoq*100) }}</td></tr>
                <tr><td>权益乘数</td><td>{{ "%.2f"|format(dupont.equity_multiplier) }}</td><td>{{ "%+.2f%%"|format(dupont.equity_multiplier_yoy*100) }}</td><td>{{ "%+.2f%%"|format(dupont.equity_multiplier_qoq*100) }}</td></tr>
            </table>
            
            <div class="chart"><img src="{{ charts[0] }}" alt="财务仪表盘"><div class="caption">财务核心指标趋势（最近8季）</div></div>
        </div>
        
        <!-- 2. 估值 -->
        <div class="section" id="valuation">
            <h2>💎 估值分析</h2>
            <div class="metric-grid">
                <div class="kpi-card"><div class="kpi-value">{{ "%.1f"|format(valuation_percentile.pe_percentile) }}%</div><div class="kpi-label">PE 历史分位</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ "%.1f"|format(valuation_percentile.pb_percentile) }}%</div><div class="kpi-label">PB 历史分位</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ "%.2f"|format(dcf_result.fair_value) }}</div><div class="kpi-label">DCF 合理价值</div></div>
                <div class="kpi-card"><div class="kpi-value {{ 'up' if dcf_result.upside > 0 else 'down' }}">{{ "%+.1f%%"|format(dcf_result.upside*100) }}</div><div class="kpi-label">上涨/下跌空间</div></div>
            </div>
            
            <h3>相对估值 (同行业对比)</h3>
            <table>
                <thead><tr><th>代码</th><th>名称</th><th>PE(TTM)</th><th>PB</th><th>ROE</th><th>市值(亿)</th></tr></thead>
                <tbody>
                    {% for peer in peer_valuation %}
                    <tr>
                        <td>{{ peer.symbol }}</td>
                        <td>{{ peer.name }}</td>
                        <td>{{ "%.1f"|format(peer.pe_ttm) if peer.pe_ttm else '-' }}</td>
                        <td>{{ "%.1f"|format(peer.pb) if peer.pb else '-' }}</td>
                        <td>{{ "%.1f%%"|format(peer.roe*100) if peer.roe else '-' }}</td>
                        <td>{{ "%.1f"|format(peer.market_cap/1e8) if peer.market_cap else '-' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            
            <div class="chart"><img src="{{ charts[1] }}" alt="估值图表"><div class="caption">历史估值分位 + DCF 敏感性分析 + 同行业对比</div></div>
        </div>
        
        <!-- 3. 技术面 -->
        <div class="section" id="technical">
            <h2>📉 技术面分析</h2>
            <div class="metric-grid">
                <div class="kpi-card"><div class="kpi-value">{{ tech_signals.trend }}</div><div class="kpi-label">趋势判断</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ "%.1f"|format(tech_signals.rsi) }}</div><div class="kpi-label">RSI(14)</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ tech_signals.macd_signal }}</div><div class="kpi-label">MACD 信号</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ "%.2f"|format(support_resistance.resistance) }}</div><div class="kpi-label">关键压力位</div></div>
            </div>
            <div class="chart"><img src="{{ charts[2] }}" alt="技术面图表"><div class="caption">日线/周线叠加 + 均线系统 + MACD/KDJ + 支撑压力位</div></div>
        </div>
        
        <!-- 4. 资金流向 -->
        <div class="section" id="flow">
            <h2>💹 资金流向</h2>
            <div class="chart"><img src="{{ charts[3] }}" alt="资金流向"><div class="caption">主力/北向/两融/龙虎榜 资金流向时序图</div></div>
            <table>
                <thead><tr><th>指标</th><th>近5日</th><th>近10日</th><th>近20日</th></tr></thead>
                <tbody>
                    <tr><td>主力净流入(亿)</td><td>{{ "%+.2f"|format(main_flow.net_5d/1e8) }}</td><td>{{ "%+.2f"|format(main_flow.net_10d/1e8) }}</td><td>{{ "%+.2f"|format(main_flow.net_20d/1e8) }}</td></tr>
                    <tr><td>北向净流入(亿)</td><td>{{ "%+.2f"|format(north_flow.net_5d/1e8) }}</td><td>{{ "%+.2f"|format(north_flow.net_10d/1e8) }}</td><td>{{ "%+.2f"|format(north_flow.net_20d/1e8) }}</td></tr>
                    <tr><td>龙虎榜上榜次数</td><td colspan="3">{{ lhb_detail.count }}</td></tr>
                </tbody>
            </table>
        </div>
        
        <!-- 5. 舆情 -->
        <div class="section" id="sentiment">
            <h2>🗣️ 舆情分析</h2>
            <div class="metric-grid">
                <div class="kpi-card"><div class="kpi-value {{ 'up' if news_sentiment.avg_score > 0 else 'down' }}">{{ "%.2f"|format(news_sentiment.avg_score) }}</div><div class="kpi-label">新闻平均情感</div></div>
                <div class="kpi-card"><div class="kpi-value {{ 'up' if guba_sentiment.avg_score > 0 else 'down' }}">{{ "%.2f"|format(guba_sentiment.avg_score) }}</div><div class="kpi-label">股吧平均情感</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ news_sentiment.article_count }}</div><div class="kpi-label">新闻文章数</div></div>
                <div class="kpi-card"><div class="kpi-value">{{ guba_sentiment.post_count }}</div><div class="kpi-label">股吧帖子数</div></div>
            </div>
            <div class="chart"><img src="{{ charts[4] }}" alt="舆情图表"><div class="caption">情感极性分布 + 关键词云 + 热度趋势</div></div>
        </div>
        
        <!-- 6. 风险 -->
        <div class="section" id="risk">
            <h2>⚠️ 风险提示</h2>
            {% for risk in risk_factors %}
            <div class="risk-{{ 'high' if loop.index <= 2 else 'medium' if loop.index <= 4 else 'low' }}">
                <strong>{{ loop.index }}. </strong>{{ risk }}
            </div>
            {% endfor %}
            <div class="chart"><img src="{{ charts[5] }}" alt="风险雷达"><div class="caption">多维风险雷达图</div></div>
        </div>
        
        <div class="disclaimer">{{ disclaimer }}</div>
    </div>
</body>
</html>
```

---

## 6. 部署与运维清单

| 项 | 建议配置 | 说明 |
|----|----------|------|
| **Python 版本** | 3.10+ | 支持 `match-case`、更好的类型提示 |
| **依赖管理** | `poetry` / `uv` | 锁定版本、可复现构建 |
| **定时调度** | `APScheduler` + `systemd` / `supervisor` | 生产环境建议用 systemd 管理进程 |
| **进程守护** | `gunicorn` + `uvicorn` (FastAPI) / `systemd` | 多进程、自动重启 |
| **日志** | `loguru` + `ELK` / `Loki` | 结构化日志、集中收集 |
| **监控告警** | `Prometheus` + `Grafana` + `Alertmanager` | 任务成功率、耗时、报表生成量 |
| **存储** | 报表文件：MinIO/S3 + PostgreSQL(元数据) | 版本管理、检索、下载 |
| **通知** | Webhook(钉钉/飞书/Slack/Email) | 生成成功/失败/异常实时推送 |
| **浏览器池** | `browser-cdp` + 固定版本 Chrome | 避免自动更新导致 CDP 协议不兼容 |
| **代理池** | 自建/购买住宅代理 + 健康检查 | 反爬、IP 轮换 |

---

## 7. 完整项目结构参考

```
finance-report-system/
├── config/
│   ├── report_schedule.yaml      # 调度配置
│   ├── exporters.yaml            # 导出器配置
│   └── templates.yaml            # 模板映射
├── src/
│   ├── generators/
│   │   ├── __init__.py
│   │   ├── base.py               # ReportGenerator 基类
│   │   ├── weekly.py             # WeeklyReportGenerator
│   │   └── deep_dive.py          # DeepDiveReportGenerator
│   ├── exporters/
│   │   ├── __init__.py
│   │   ├── base.py               # BaseExporter
│   │   ├── html.py
│   │   ├── pdf.py
│   │   ├── docx.py
│   │   ├── excel.py
│   │   └── markdown.py
│   ├── templates/
│   │   ├── weekly_report.html.j2
│   │   └── deep_dive_report.html.j2
│   ├── charts/
│   │   ├── __init__.py
│   │   ├── financial.py
│   │   ├── valuation.py
│   │   ├── technical.py
│   │   ├── flow.py
│   │   ├── sentiment.py
│   │   └── risk.py
│   ├── scheduler.py              # ReportScheduler
│   ├── versioning.py             # VersionStore, VersionManager
│   └── data_provider.py          # 数据提供者抽象
├── reports/
│   ├── output/                   # 生成的报表文件
│   └── versions.db               # 版本元数据 SQLite
├── tests/
│   ├── test_generators.py
│   ├── test_exporters.py
│   └── test_scheduler.py
├── pyproject.toml
├── README.md
└── main.py                       # 入口：启动调度器 / 手动生成
```

---

> **完整示例代码**请查阅 `references/example-notebooks/` 目录下的 Jupyter Notebook：
> - `01_weekly_report_generation.ipynb`
> - `02_deep_dive_report_generation.ipynb`
> - `03_multi_format_export.ipynb`
> - `04_scheduler_and_versioning.ipynb`