---
name: cnki-search	skill: browser-cdp
script: cnki_search.py
description: 中国知网论文搜索自动化脚本，支持关键词、作者、期刊筛选，获取论文标题和摘要信息。
triggers: 知网搜索, cnki search, 学术论文, 论文检索, cnki_search.py
platforms: windows, macos, linux, pc
---

# 中国知网论文搜索自动化脚本 (`cnki_search.py`)

## 用途

使用 browser-cdp skill 搜索中国知网(CNKI)学术论文，获取论文标题、作者、期刊、摘要等信息。

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 基础搜索
python src/searchers/cnki_search.py "深度学习" --max-results 20

# 指定作者
python src/searchers/cnki_search.py "人工智能" --author 张明 --output-dir ./cnki_results

# 启用反检测模式
python src/searchers/cnki_search.py "大模型" --journal 计算机学报 --stealth --max-results 15
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--author` | 作者姓名 | 全部 |
| `--journal` | 期刊名称 | 全部 |
| `--year` | 发表年份 | 全部 |
| `--max-results` | 最大结果数量 | 20 |
| `--output-dir` | 输出目录 | `./search_results/cnki` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "基于大语言模型的智能助手研究综述",
  "url": "https://kns.cnki.net/kcms/detail/xxx.html",
  "authors": "张三;李四;王五",
  "journal": "计算机学报",
  "year": 2026,
  "volume": "49",
  "issue": "3",
  "pages": "456-470",
  "abstract": "本文综述了大语言模型在智能助手领域的应用进展...",
  "citation_count": 128,
  "source": "cnki",
  "scraped_at": "2026-08-15 11:58:00"
}
```

## 核心实现要点

- 使用 JS 提取 `.result-item` 或 `.paper-item` 中的论文信息
- 标题从 `.title a` 提取
- 作者从 `.author` 提取，支持多作者分隔符
- 期刊信息从 `.journal` 提取
- 摘要从 `.abstract` 提取（可能被截断）
- 引用次数从 `.citation-count` 提取
- 支持按引用数、下载量排序
- 反检测模式隐藏自动化特征
- 验证码检测：检测 slider 滑块验证

## 注意事项

- 中国知网反爬极严格，**必须启用 `--stealth` 模式**
- 连续搜索会触发验证码和IP封禁
- 部分论文全文需付费下载，摘要可能不完整
- **建议控制搜索频率**，每次搜索间隔至少5秒
- 大量搜索需要登录账号，建议使用代理池轮换

## 技术特征

- **前端框架**: Vue.js + jQuery hybrid
- **反爬等级**: 3（高度）
- **验证码类型**: slider（滑块验证）
- **登录要求**: 否（推荐登录）
- **目标成功率**: 60%

## 相关配置

参见 `config/websites/cnki.net.json` 获取完整的站点配置。