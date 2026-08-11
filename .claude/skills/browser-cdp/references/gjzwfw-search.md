# 国家政务服务平台搜索器使用指南

> 生成时间：2026-08-09
> 脚本：gjzwfw_search.py
> 目标网站：https://gjzwfw.www.gov.cn

---

## 一、功能概述

国家政务服务平台搜索器用于抓取国家政务服务平台的政务服务信息，包括：

- **政务服务事项**：事项名称、办理部门、服务类型、行政层级
- **办事指南**：所需材料、办理时限、办理流程
- **办理进度**：申请状态、更新时间
- **省份服务**：按省份筛选的政务服务

---

## 二、安装依赖

```bash
cd .claude/skills/browser-cdp
pip install websocket-client requests pillow
```

---

## 三、快速开始

### 3.1 启动浏览器

```bash
# 启动专用浏览器实例
python src/core/browser_launch.py --dedicated --name gjzwfw --start-url "https://gjzwfw.www.gov.cn"
```

### 3.2 执行搜索

```bash
# 搜索政务服务事项
python src/searchers/gjzwfw_search.py "营业执照"

# 搜索办事指南
python src/searchers/gjzwfw_search.py "社保查询" --type guide

# 搜索指定省份的服务
python src/searchers/gjzwfw_search.py "企业开办" --province 北京市

# 保存结果到文件
python src/searchers/gjzwfw_search.py "不动产登记" --output-dir ./results
```

---

## 四、命令行参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | str | - | 搜索关键词（必填） |
| `--type` | str | all | 搜索类型：service/guide/progress/province/all |
| `--province` | str | None | 省份名称（可选） |
| `--output-dir` | str | None | 输出目录 |
| `--port` | int | 9333 | 浏览器调试端口 |
| `--tab` | str | None | Tab ID |
| `--stealth` | bool | True | 启用反检测模式 |
| `--no-stealth` | bool | False | 禁用反检测模式 |
| `--wait-timeout` | int | 30 | 等待超时时间（秒） |
| `--max-results` | int | 20 | 最大结果数 |

---

## 五、输出格式

### 5.1 政务服务事项

```json
{
  "title": "企业营业执照核发",
  "url": "https://gjzwfw.www.gov.cn/bsfw/item/xxx",
  "department": "市场监督管理局",
  "service_type": "行政许可",
  "admin_level": "省级",
  "type": "service",
  "source_site": "gjzwfw"
}
```

### 5.2 办事指南

```json
{
  "title": "企业营业执照核发办事指南",
  "url": "https://gjzwfw.www.gov.cn/bsfw/guide/xxx",
  "department": "市场监督管理局",
  "required_materials": "身份证、营业执照申请表",
  "handle_time": "5个工作日",
  "type": "guide",
  "source_site": "gjzwfw"
}
```

### 5.3 办理进度

```json
{
  "title": "企业营业执照核发申请",
  "url": "https://gjzwfw.www.gov.cn/bsfw/progress/xxx",
  "status": "办理中",
  "update_date": "2026-08-09",
  "type": "progress",
  "source_site": "gjzwfw"
}
```

### 5.4 省份服务

```json
{
  "title": "北京市企业开办服务",
  "url": "https://gjzwfw.www.gov.cn/bsfw/province/xxx",
  "city": "北京市",
  "service_type": "企业开办",
  "province": "北京市",
  "type": "province_service",
  "source_site": "gjzwfw"
}
```

---

## 六、Python API 使用

### 6.1 基本搜索

```python
from src.searchers.gjzwfw_search import GjzwfwSearcher

searcher = GjzwfwSearcher()
results = searcher.search(
    query="营业执照",
    search_type="service",
    port=9333,
    stealth=True,
    max_results=20
)

for item in results:
    print(f"{item['title']} - {item['department']}")
```

### 6.2 获取详情

```python
detail = searcher.get_detail(
    url="https://gjzwfw.www.gov.cn/bsfw/item/xxx",
    port=9333
)

print(f"标题：{detail['title']}")
print(f"部门：{detail['department']}")
print(f"材料：{detail['materials']}")
```

### 6.3 获取省份列表

```python
provinces = searcher.get_province_list(port=9333)

for p in provinces:
    print(f"{p['name']}: {p['url']}")
```

---

## 七、技术要点

### 7.1 反检测策略

- 启用 `--stealth` 模式隐藏自动化特征
- 随机延迟 1-2 秒避免频率过高
- 模拟真实浏览器行为

### 7.2 等待策略

- 使用 `--wait-selector` 等待关键元素出现
- 超时时间默认 30 秒
- 页面加载完成后等待 2 秒再提取

### 7.3 数据提取

- 使用 JavaScript 在页面上下文中提取数据
- 支持多种选择器适配不同页面结构
- 提取后保存为 JSON 格式

---

## 八、注意事项

1. **登录态**：部分功能需要登录，建议使用 `--dedicated --name` 保留登录态
2. **频率控制**：控制请求频率，避免被封 IP
3. **数据时效**：政务数据更新有延迟，通常为 T+1
4. **法律合规**：仅抓取公开数据，遵守 robots.txt

---

## 九、常见问题

### Q1: 搜索结果为空？

检查关键词是否正确，尝试使用更通用的关键词。

### Q2: 页面加载超时？

增加 `--wait-timeout` 参数，或检查网络连接。

### Q3: 被要求验证码？

启用 `--stealth` 模式，或手动完成验证码。

---

**文档版本**：v1.0  
**最后更新**：2026-08-09
