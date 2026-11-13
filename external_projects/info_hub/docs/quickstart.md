# 快速开始

## 1. 环境
- Python 3.10 或更高版本（开发与测试使用 3.12）。
- 依赖见 `requirements.txt`：PyYAML、beautifulsoup4、streamlit（1.37 或更高，开发使用 1.67）、playwright（仅登录与浏览器渲染的源使用）、psutil。

```bash
cd external_projects/info_hub
pip install -r requirements.txt
```

仅抓取 RSS、JSON 接口与脚本类信息源时，不需要 playwright。需要浏览器的源（如知乎、小红书、36 氪首页）会在运行时提示缺少浏览器。

## 2. 启动看板
```bash
streamlit run app.py --server.port 8506
```
浏览器打开 http://localhost:8506 。

## 3. 第一次抓取
1. 打开“信息源”页签，选择一个信息源（例如 `hacker_news` 或 `bangumi_calendar`，它们不需要浏览器）。
2. 在“立即抓取”中点击“抓取一次”。成功会显示条数与耗时，失败会显示错误与调试信息（见 [运行与运维](operations.md)）。
3. 打开“抓取结果”页签查看原始列表，或打开“推荐”页签查看整理后的内容。

也可以在终端执行：
```bash
python cli.py validate          # 校验全部信息源配置
python cli.py run hacker_news   # 抓取一个信息源
python cli.py list              # 列出信息源
```

## 4. 定时调度
看板进程启动后会在后台每 5 分钟检查一次到期的信息源与报告（`defaults.tick_interval_minutes`）。不需要额外的 cron。若希望由外部调度器运行，见 [运行与运维](operations.md#定时调度)。

## 5. 常用操作
- 不想看到某个信息源：在“信息源”页签中停用，或在推荐卡片上点 🚫。
- 推荐太少：在推荐页顶部的“⚙ 显示数量设置”中调大数量。
- 想要浏览器里的站点：在“设置与管理”中启动专用浏览器，然后再抓取。
