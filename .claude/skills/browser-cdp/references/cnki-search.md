---
name: cnki-search
skill: browser-cdp
script: cnki_search.py
description: 中国知网搜索自动化脚本，支持学术论文搜索，获取论文标题、作者、期刊、摘要等信息。
triggers: 中国知网, cnki, 知网, 学术论文搜索, cnki_search.py
platforms: windows, macos, linux, pc
---

# 中国知网搜索自动化脚本 (`cnki_search.py`)

## 用途

使用 browser-cdp skill 搜索中国知网，获取学术论文元数据信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://kns.cnki.net/kns8s/search?kw={keyword}`
- **论文详情**：`https://kns.cnki.net/kns8s/detail/detail.aspx?filename={filename}`
- **数据格式**：SSR + AJAX 混合
- **主要 API**：内部 API 需逆向

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐⭐⭐ | 较强频率限制 |
| UA 检测 | ⭐⭐⭐ | 检测非浏览器 UA |
| 验证码 | ⭐⭐⭐ | 频繁触发 |
| 登录态 | ⭐⭐⭐ | 部分功能需登录 |
| 频率限制 | ⭐⭐⭐⭐ | 建议请求间隔 5-10 秒 |

### 抓取策略

```python
# 推荐策略（谨慎使用）
- 必须使用 browser-cdp + --stealth 模式
- 建议登录专用浏览器实例
- 请求间隔 5-10 秒（低频使用）
- 仅用于低频查询，不适合批量抓取
- 准备好应对验证码挑战
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索论文
python src/searchers/cnki_search.py "深度学习" --max-results 10

# 指定数据库
python src/searchers/cnki_search.py "机器学习" --db "SCIDB" --max-results 10

# 使用已登录的浏览器实例
python src/searchers/cnki_search.py "Transformer" --dedicated --name cnki_session
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--db` | 数据库 (SCIDB/CPFD/CCND) | SCIDB |
| `--max-results` | 最大结果数量 | 10 |
| `--output-dir` | 输出目录 | `./search_results/cnki` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--dedicated` | 使用专用浏览器实例 | False |
| `--name` | 浏览器实例名称 | - |
| `--wait-timeout` | 页面等待超时(秒) | 60 |

## 输出格式

```json
{
  "title": "论文标题",
  "url": "https://kns.cnki.net/kns8s/detail/detail.aspx?filename=xxxx",
  "authors": ["作者1", "作者2"],
  "journal": "期刊名称",
  "year": 2024,
  "abstract": "论文摘要...",
  "keywords": ["关键词1", "关键词2"],
  "source": "cnki",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取论文列表中的元数据
- 支持按年份、期刊排序
- 需要处理验证码挑战
- 必须使用 stealth 模式

## 注意事项

- ⚠️ 知网反爬较强，仅建议低频使用
- 高频请求会导致 IP 被封禁
- 建议配合代理池使用
- 准备好手动处理验证码
- 不适合批量抓取场景
- 仅抓取公开可见的元数据
