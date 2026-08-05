---
name: liepin-search
skill: browser-cdp
script: liepin_search.py
description: 猎聘搜索自动化脚本，支持职位搜索，获取职位名称、公司、薪资、地点等信息。
triggers: 猎聘, liepin, 招聘搜索, 高端职位, liepin_search.py
platforms: windows, macos, linux, pc
---

# 猎聘搜索自动化脚本 (`liepin_search.py`)

## 用途

使用 browser-cdp skill 搜索猎聘，获取高端职位信息。

## 技术特征分析

### 网站结构

- **搜索接口**：`https://www.liepin.com/zhaopin/?key={keyword}&city={city}`
- **职位详情**：`https://www.liepin.com/job/{job_id}.shtml`
- **数据格式**：SSR + AJAX 混合
- **主要 API**：内部 API 需逆向

### 反爬机制

| 机制 | 强度 | 说明 |
|------|------|------|
| IP 频率限制 | ⭐⭐⭐ | 中等频率限制 |
| UA 检测 | ⭐⭐ | 检测非浏览器 UA |
| 验证码 | ⭐⭐ | 偶尔触发 |
| 登录态 | ⭐⭐ | 部分功能需登录 |
| 频率限制 | ⭐⭐⭐ | 建议请求间隔 3-5 秒 |

### 抓取策略

```python
# 推荐策略
- 使用 browser-cdp + --stealth 模式
- 职位列表页 SSR，可直接解析
- 详情页需 AJAX 加载
- 请求间隔 3-5 秒
```

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 搜索职位
python src/searchers/liepin_search.py "产品经理" --city "北京" --max-results 20

# 指定薪资范围
python src/searchers/liepin_search.py "技术总监" --city "上海" --min-salary 50000 --max-results 10

# 指定输出目录
python src/searchers/liepin_search.py "运营" --city "深圳" --output-dir ./liepin_results --port 9333
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `query` | 搜索关键词（必填） | - |
| `--city` | 城市名称（必填） | - |
| `--min-salary` | 最低薪资（元/月） | 0 |
| `--max-salary` | 最高薪资（元/月） | 99999 |
| `--max-results` | 最大结果数量 | 20 |
| `--output-dir` | 输出目录 | `./search_results/liepin` |
| `--port` | CDP 调试端口 | 9333 |
| `--stealth` | 启用反检测模式 | True |
| `--wait-timeout` | 页面等待超时(秒) | 30 |

## 输出格式

```json
{
  "title": "职位名称",
  "url": "https://www.liepin.com/job/123456789.shtml",
  "company": "公司名称",
  "company_size": "1000-9999人",
  "city": "北京",
  "district": "朝阳区",
  "salary": "30K-50K",
  "experience": "5-10年",
  "education": "本科",
  "source": "liepin",
  "scraped_at": "2026-08-05 10:30:00"
}
```

## 核心实现要点

- 使用 JS 提取职位列表中的核心信息
- 薪资从 `.salary` 类提取
- 支持分页加载
- 反检测模式隐藏自动化特征

## 注意事项

- 猎聘定位高端人才，职位质量较高
- 反爬中等，建议启用 stealth 模式
- 仅抓取公开可见的元数据
