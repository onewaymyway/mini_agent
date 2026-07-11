---
name: nvidia-build-scraper
description: 抓取 NVIDIA Build (build.nvidia.com) 网站上的模型列表，支持筛选条件、自动翻页、详情页深度抓取，获取完整模型 ID（如 nvidia/nemotron-3-ultra-550b-a55b）和规格信息。当用户说"抓取 NVIDIA 模型"、"NVIDIA Build 模型列表"、"获取 NIM 模型"时使用。
triggers: nvidia, build.nvidia.com, nim, nvidia build, nvidia模型, nvidia模型列表, nvidia nim, 模型抓取, nvidia build scraper
---

# NVIDIA Build 模型抓取 Skill

从 NVIDIA Build 网站 (https://build.nvidia.com/models) 抓取模型列表和详情信息，获取完整的模型标识符（`publisher/model-name` 格式，可直接用于 NVIDIA API 调用）。

## 前置条件

确保已安装 Playwright 及浏览器依赖：

```bash
pip install playwright
playwright install chromium
```

## 脚本说明

本 skill 包含 3 个脚本，位于 `.claude/skills/nvidia-build-scraper/` 目录：

| 脚本 | 用途 |
|------|------|
| `scrape_nvidia_models.py` | 抓取模型列表页（自动翻页） |
| `scrape_model_details.py` | 抓取模型详情页（获取完整 ID 和规格） |
| `generate_providers_config.py` | 将模型 ID 生成为 providers.json 格式配置 |
| `scrape_all.py` | 一键执行完整流程（列表 + 详情 + 报告 + providers 配置） |

## 使用方法

### 方式一：一键完整流程（推荐）

```bash
# 抓取 Preview + Upgrade Available 模型（默认筛选）
python .claude/skills/nvidia-build-scraper/scrape_all.py --output-dir ./output

# 抓取所有模型
python .claude/skills/nvidia-build-scraper/scrape_all.py \
  --url "https://build.nvidia.com/models" \
  --output-dir ./output

# 自定义参数
python .claude/skills/nvidia-build-scraper/scrape_all.py \
  --url "https://build.nvidia.com/models?filters=..." \
  --output-dir ./output \
  --max-pages 10 \
  --batch-size 5
```

输出文件：
- `output/nvidia_models.json` - 列表数据
- `output/nvidia_models_full.json` - 完整数据（含详情）
- `output/nvidia_models_report.md` - Markdown 报告
- `output/providers_nvidia.json` - providers.json 格式配置（可直接复制使用）

### 方式二：分步执行

```bash
# Step 1: 抓取列表
python .claude/skills/nvidia-build-scraper/scrape_nvidia_models.py \
  --url "https://build.nvidia.com/models?filters=nimType%3Anim_type_preview%2CnimType%3Anim_type_upgrade_available" \
  --output ./nvidia_models.json

# Step 2: 抓取详情（可分批）
python .claude/skills/nvidia-build-scraper/scrape_model_details.py \
  --input ./nvidia_models.json \
  --output ./nvidia_models_full.json

# 分批抓取（避免超时）
python .claude/skills/nvidia-build-scraper/scrape_model_details.py \
  --input ./nvidia_models.json \
  --output ./batch_0_10.json \
  --start 0 --end 10

# Step 3: 生成 providers.json 配置
python .claude/skills/nvidia-build-scraper/generate_providers_config.py \
  --input ./nvidia_models_full.json \
  --output ./providers_nvidia.json

# 只生成 Free Endpoint 类型的模型
python .claude/skills/nvidia-build-scraper/generate_providers_config.py \
  --input ./nvidia_models_full.json \
  --output ./providers_nvidia.json \
  --filter-type "Free Endpoint"

# 只生成带 coding 标签的模型
python .claude/skills/nvidia-build-scraper/generate_providers_config.py \
  --input ./nvidia_models_full.json \
  --output ./providers_nvidia.json \
  --filter-tags coding,reasoning

# 指定 API key 占位符
python .claude/skills/nvidia-build-scraper/generate_providers_config.py \
  --input ./nvidia_models_full.json \
  --output ./providers_nvidia.json \
  --api-key "{{NVIDIA_API_KEY}}"
```

## URL 筛选参数

NVIDIA Build 网站通过 URL query 参数筛选模型：

| 筛选条件 | URL 参数 | 说明 |
|----------|----------|------|
| Preview 模型 | `filters=nimType%3Anim_type_preview` | 预览版模型 |
| Upgrade Available | `filters=nimType%3Anim_type_upgrade_available` | 可升级模型 |
| Free Endpoint | `filters=endpointTier%3Aendpoint_tier_free` | 免费端点 |
| Partner Endpoint | `filters=endpointTier%3Aendpoint_tier_partner` | 合作伙伴端点 |
| Downloadable | `filters=nimType%3Anim_type_upgrade_available` | 可下载模型 |

多个筛选条件用逗号分隔：`filters=nimType%3Anim_type_preview%2CnimType%3Anim_type_upgrade_available`

## 技术细节

### 页面结构

- **模型卡片**: 使用 NVIDIA 设计系统组件 `nv-card-root`
- **分页组件**: `nv-pagination-page-list`，页码按钮显示为 '11','22','33','44' 格式（实际对应第 1-4 页）
- **Cookie 横幅**: OneTrust 同意横幅会阻挡点击，需通过 JS 移除
- **详情页 URL**: `https://build.nvidia.com/{publisher-slug}/{model-slug}`

### 发布者 Slug 映射

发布者显示名称与 URL slug 不完全一致，内置映射表：

| 显示名称 | URL Slug |
|----------|----------|
| Z.ai | z-ai |
| Mistral AI | mistralai |
| DeepSeek AI | deepseek-ai |
| Abacus.AI | abacusai |
| NVIDIA | nvidia |
| Meta | meta |
| Google | google |
| Qwen | qwen |

### 提取的数据字段

**列表页**:
- `name`: 模型名称
- `publisher`: 发布者
- `description`: 描述
- `tags`: 标签列表
- `modelType`: 端点类型 (Free Endpoint / Downloadable)
- `downloads`: 下载量
- `updated`: 更新时间

**详情页**:
- `full_model_id`: 完整模型 ID (如 `nvidia/nemotron-3-ultra-550b-a55b`)
- `detail_fields`: 规格字段 (Provider, Parameters, Context Length 等)
- `capabilities`: 能力信息
- `availability`: 可用性信息

## 常见问题

### Q: 抓取超时怎么办？
A: 使用分批模式，减小 `--batch-size` 或 `--end` 参数。详情页抓取建议每批 10 个以内。

### Q: 某些模型详情页 404？
A: 发布者 slug 可能需要更新。检查 `scrape_model_details.py` 中的 `PUBLISHER_SLUG_MAP`，添加正确的映射。

### Q: 翻页不工作？
A: 确保已移除 Cookie 横幅。脚本已内置 `REMOVE_COOKIE_BANNER_JS` 处理。如果页面结构变化，可能需要更新选择器。

### Q: 如何获取所有模型（不筛选）？
A: 使用 `--url "https://build.nvidia.com/models"` 不带 filters 参数。
