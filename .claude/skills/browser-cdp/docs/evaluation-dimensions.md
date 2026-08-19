# 网站操作能力评估维度清单

**版本**: 1.0.0  
**创建日期**: 2026-08-07  
**状态**: 正式版  
**适用范围**: browser-cdp skill 抓取与浏览能力评估  
**关联文档**: [evaluation-standards-v2.md](./evaluation-standards-v2.md)、[website-support-list.md](./website-support-list.md)

---

## 1. 评估维度总览

| 维度 | 权重 | 核心指标数 | 评估工具 | 说明 |
|------|------|-----------|----------|------|
| 可用性 | 25% | 4 | availability_evaluator.py | 页面访问成功率、加载性能、超时处理 |
| 性能 | 20% | 4 | performance_evaluator.py | 响应时间、并发能力、资源消耗 |
| 安全性 | 15% | 4 | security_evaluator.py | 反爬绕过率、验证码通过率、指纹伪装 |
| 兼容性 | 15% | 4 | compatibility_evaluator.py | 浏览器兼容、设备兼容、网络兼容 |
| 稳定性 | 15% | 4 | stability_evaluator.py | 重复一致性、异常恢复、连接稳定性 |
| 数据质量 | 10% | 4 | data_quality_evaluator.py | 提取准确率、字段完整率、数据质量 |

---

## 2. 可用性维度（权重 25%）

### 2.1 指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 页面访问成功率 | `page_access_rate` | 成功加载目标页面的比例 | 成功访问次数 / 总尝试次数 × 100% | ≥ 95% | 40% |
| 首屏加载时间 | `first_contentful_paint` | 从导航到首屏内容可见的时间 | CDP Performance 指标或手动计时 | ≤ 3s | 25% |
| 页面完全加载时间 | `page_load_time` | 从导航到页面完全渲染的时间 | 等待 networkidle 或稳定状态 | ≤ 10s | 20% |
| 超时处理成功率 | `timeout_handling_rate` | 超时后正确处理的概率 | 成功处理超时次数 / 总超时次数 × 100% | ≥ 90% | 15% |

### 2.2 计算方法

```python
# 页面访问成功率
page_access_rate = (successful_visits / total_attempts) * 100

# 首屏加载时间
first_contentful_paint = fcp_timestamp - navigation_start_timestamp

# 页面完全加载时间
page_load_time = page_stable_timestamp - navigation_start_timestamp

# 超时处理成功率
timeout_handling_rate = (handled_timeouts / total_timeouts) * 100
```

### 2.3 评分细则

| 综合得分 | 等级 | 说明 |
|----------|------|------|
| 90-100 | 优秀 | 可用性成熟，可稳定生产使用 |
| 75-89 | 良好 | 基本可用，个别场景需优化 |
| 60-74 | 合格 | 核心功能可用，需持续改进 |
| 40-59 | 待改进 | 存在明显短板，需重点优化 |
| < 40 | 不可用 | 当前能力无法支持该网站 |

### 2.4 测试用例

| 测试项 | 操作 | 预期结果 |
|--------|------|----------|
| 首页访问 | 导航到目标网站首页 | 成功加载，返回 HTTP 200 |
| 搜索查询页 | 输入关键词并提交搜索 | 成功返回搜索结果页，加载时间 ≤ 10s |
| 动态内容页 | 访问含大量 JS 渲染的页面 | 等待 networkidle 后内容完整 |
| 超时场景 | 访问响应缓慢的页面 | 正确触发超时处理，不阻塞后续操作 |

---

## 3. 性能维度（权重 20%）

### 3.1 指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 平均响应时间 | `avg_response_time` | 单次请求的平均响应时间 | 所有请求响应时间之和 / 请求总数 | ≤ 2s | 30% |
| P95 响应时间 | `p95_response_time` | 95% 请求的响应时间上限 | 排序后第 95 百分位的响应时间 | ≤ 5s | 25% |
| 并发处理能力 | `concurrent_capacity` | 同时处理多个请求的能力 | 成功并发数 / 总并发数 × 100% | ≥ 80% | 25% |
| 资源利用率 | `resource_utilization` | CPU/内存使用效率 | 平均资源使用率 | ≤ 70% | 20% |

### 3.2 计算方法

```python
# 平均响应时间
avg_response_time = sum(response_times) / len(response_times)

# P95 响应时间
p95_response_time = sorted(response_times)[int(len(response_times) * 0.95)]

# 并发处理能力
concurrent_capacity = (successful_concurrent / total_concurrent) * 100

# 资源利用率
resource_utilization = (cpu_usage + memory_usage) / 2
```

### 3.3 评分细则

| 指标 | 优秀 | 良好 | 合格 | 待改进 |
|------|------|------|------|--------|
| 平均响应时间 | ≤ 1s | ≤ 2s | ≤ 3s | > 3s |
| P95 响应时间 | ≤ 3s | ≤ 5s | ≤ 8s | > 8s |
| 并发处理能力 | ≥ 90% | ≥ 80% | ≥ 70% | < 70% |
| 资源利用率 | ≤ 50% | ≤ 70% | ≤ 85% | > 85% |

---

## 4. 安全性维度（权重 15%）

### 4.1 指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 反爬绕过率 | `anti_crawl_bypass_rate` | 成功绕过反爬机制的比例 | 绕过成功次数 / 总反爬触发次数 × 100% | ≥ 70% | 40% |
| 验证码通过率 | `captcha_pass_rate` | 验证码识别与处理成功率 | 通过验证码次数 / 总验证码次数 × 100% | ≥ 60% | 30% |
| 指纹伪装有效性 | `fingerprint_evasion_rate` | 浏览器指纹被识别为机器人的比例 | 1 - 被识别比例 | ≥ 80% | 20% |
| 行为模拟自然度 | `behavior_naturalness` | 操作行为被判定为人类的比例 | 人工判定为人类的操作次数 / 总操作次数 × 100% | ≥ 75% | 10% |

### 4.2 反爬机制类型

| 机制类型 | 检测方式 | 应对策略 |
|----------|----------|----------|
| User-Agent 检测 | 检测浏览器标识 | 随机化 UA 池 |
| 请求频率限制 | 限制单位时间请求数 | 限速器、随机延迟 |
| IP 封禁 | 封禁异常 IP | 代理池轮换 |
| Cookie 验证 | 要求有效 Cookie | 会话管理 |
| JavaScript 挑战 | 执行 JS 验证 | CDP 执行 JS |
| 行为分析 | 分析鼠标轨迹、点击模式 | 模拟人类行为 |
| 验证码 | 人机验证 | 验证码处理模块 |

### 4.3 计算方法

```python
# 反爬绕过率
anti_crawl_bypass_rate = (successful_bypasses / total_crawl_triggers) * 100

# 验证码通过率
captcha_pass_rate = (passed_captchas / total_captchas) * 100

# 指纹伪装有效性
fingerprint_evasion_rate = (1 - identified_as_bot / total_checks) * 100

# 行为模拟自然度
behavior_naturalness = (human_like_operations / total_operations) * 100
```

### 4.4 评分细则

| 指标 | 优秀 | 良好 | 合格 | 待改进 |
|------|------|------|------|--------|
| 反爬绕过率 | ≥ 85% | ≥ 70% | ≥ 55% | < 55% |
| 验证码通过率 | ≥ 75% | ≥ 60% | ≥ 45% | < 45% |
| 指纹伪装有效性 | ≥ 90% | ≥ 80% | ≥ 70% | < 70% |
| 行为模拟自然度 | ≥ 85% | ≥ 75% | ≥ 65% | < 65% |

---

## 5. 兼容性维度（权重 15%）

### 5.1 指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 浏览器兼容性 | `browser_compatibility` | 在多种浏览器上的表现一致性 | 兼容浏览器数 / 测试浏览器数 × 100% | ≥ 90% | 30% |
| 设备兼容性 | `device_compatibility` | 在不同设备尺寸上的表现 | 兼容设备数 / 测试设备数 × 100% | ≥ 85% | 25% |
| 网络兼容性 | `network_compatibility` | 在不同网络环境下的表现 | 兼容网络数 / 测试网络数 × 100% | ≥ 80% | 25% |
| 版本兼容性 | `version_compatibility` | 与不同浏览器版本的兼容性 | 兼容版本数 / 测试版本数 × 100% | ≥ 75% | 20% |

### 5.2 兼容性测试矩阵

| 浏览器 | Chrome | Firefox | Safari | Edge |
|--------|--------|---------|--------|------|
| 版本 | 120+ | 120+ | 17+ | 120+ |
| 兼容性 | ✅ | ✅ | ✅ | ✅ |

| 设备类型 | 桌面 | 平板 | 手机 |
|----------|------|------|------|
| 分辨率 | 1920x1080 | 1024x768 | 375x667 |
| 兼容性 | ✅ | ✅ | ✅ |

### 5.3 计算方法

```python
# 浏览器兼容性
browser_compatibility = (compatible_browsers / total_browsers) * 100

# 设备兼容性
device_compatibility = (compatible_devices / total_devices) * 100

# 网络兼容性
network_compatibility = (compatible_networks / total_networks) * 100

# 版本兼容性
version_compatibility = (compatible_versions / total_versions) * 100
```

### 5.4 评分细则

| 指标 | 优秀 | 良好 | 合格 | 待改进 |
|------|------|------|------|--------|
| 浏览器兼容性 | ≥ 95% | ≥ 90% | ≥ 80% | < 80% |
| 设备兼容性 | ≥ 90% | ≥ 85% | ≥ 75% | < 75% |
| 网络兼容性 | ≥ 85% | ≥ 80% | ≥ 70% | < 70% |
| 版本兼容性 | ≥ 80% | ≥ 75% | ≥ 65% | < 65% |

---

## 6. 稳定性维度（权重 15%）

### 6.1 指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 重复执行一致性 | `execution_consistency` | 多次执行结果的一致性 | 结果一致次数 / 总执行次数 × 100% | ≥ 90% | 35% |
| 异常恢复率 | `error_recovery_rate` | 异常后自动恢复的比例 | 成功恢复次数 / 总异常次数 × 100% | ≥ 80% | 30% |
| 连接稳定性 | `connection_stability` | CDP 连接保持率 | 连接保持时间 / 总运行时间 × 100% | ≥ 95% | 20% |
| 内存稳定性 | `memory_stability` | 长时间运行的内存泄漏检测 | 内存增长速率 | ≤ 5MB/h | 15% |

### 6.2 计算方法

```python
# 重复执行一致性
execution_consistency = (consistent_executions / total_executions) * 100

# 异常恢复率
error_recovery_rate = (successful_recoveries / total_errors) * 100

# 连接稳定性
connection_stability = (connected_time / total_runtime) * 100

# 内存稳定性
memory_stability = (end_memory - start_memory) / runtime_hours
```

### 6.3 稳定性测试方案

| 测试项 | 执行次数 | 观察指标 |
|--------|----------|----------|
| 重复搜索 | 10 次 | 结果一致性 |
| 长时间运行 | 30 分钟 | 内存增长、连接保持 |
| 异常注入 | 5 次 | 恢复成功率 |
| 并发测试 | 3 个实例 | 资源竞争、崩溃率 |

### 6.4 评分细则

| 指标 | 优秀 | 良好 | 合格 | 待改进 |
|------|------|------|------|--------|
| 重复执行一致性 | ≥ 95% | ≥ 90% | ≥ 80% | < 80% |
| 异常恢复率 | ≥ 90% | ≥ 80% | ≥ 70% | < 70% |
| 连接稳定性 | ≥ 98% | ≥ 95% | ≥ 90% | < 90% |
| 内存稳定性 | ≤ 2MB/h | ≤ 5MB/h | ≤ 10MB/h | > 10MB/h |

---

## 7. 数据质量维度（权重 10%）

### 7.1 指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 数据提取准确率 | `extraction_accuracy` | 提取数据与真实值一致的比例 | 正确提取条数 / 总提取条数 × 100% | ≥ 85% | 40% |
| 字段完整率 | `field_completeness` | 成功提取的字段数占预期字段数的比例 | 已提取字段数 / 预期字段数 × 100% | ≥ 80% | 30% |
| 数据质量得分 | `data_quality_score` | 提取数据的完整性和准确性综合评分 | 准确率×0.6 + 完整率×0.4 | ≥ 80% | 20% |
| 结构化提取成功率 | `structured_extraction_rate` | 成功提取结构化数据的比例 | 成功提取结构化数据次数 / 总尝试次数 × 100% | ≥ 75% | 10% |

### 7.2 计算方法

```python
# 数据提取准确率
extraction_accuracy = (correct_extractions / total_extractions) * 100

# 字段完整率
field_completeness = (extracted_fields / expected_fields) * 100

# 数据质量得分
data_quality_score = extraction_accuracy * 0.6 + field_completeness * 0.4

# 结构化提取成功率
structured_extraction_rate = (successful_structured / total_structured_attempts) * 100
```

### 7.3 评分细则

| 综合得分 | 等级 | 说明 |
|----------|------|------|
| 90-100 | 优秀 | 数据提取能力成熟，可稳定生产使用 |
| 75-89 | 良好 | 基本可用，个别场景需优化 |
| 60-74 | 合格 | 核心功能可用，需持续改进 |
| 40-59 | 待改进 | 存在明显短板，需重点优化 |
| < 40 | 不可用 | 当前能力无法支持该网站 |

---

## 8. 综合评分计算

### 8.1 计算公式

```python
综合评分 = (
    可用性得分 × 0.25 +
    性能得分 × 0.20 +
    安全性得分 × 0.15 +
    兼容性得分 × 0.15 +
    稳定性得分 × 0.15 +
    数据质量得分 × 0.10
)
```

### 8.2 各维度得分计算

```python
# 可用性得分
availability_score = (
    page_access_rate × 0.40 +
    (100 - min(fcp_time / 3 * 100, 100)) × 0.25 +
    (100 - min(page_load_time / 10 * 100, 100)) × 0.20 +
    timeout_handling_rate × 0.15
)

# 性能得分
performance_score = (
    max(0, 100 - avg_response_time / 2 * 100) × 0.30 +
    max(0, 100 - p95_response_time / 5 * 100) × 0.25 +
    concurrent_capacity × 0.25 +
    max(0, 100 - resource_utilization) × 0.20
)

# 安全性得分
security_score = (
    anti_crawl_bypass_rate × 0.40 +
    captcha_pass_rate × 0.30 +
    fingerprint_evasion_rate × 0.20 +
    behavior_naturalness × 0.10
)

# 兼容性得分
compatibility_score = (
    browser_compatibility × 0.30 +
    device_compatibility × 0.25 +
    network_compatibility × 0.25 +
    version_compatibility × 0.20
)

# 稳定性得分
stability_score = (
    execution_consistency × 0.35 +
    error_recovery_rate × 0.30 +
    connection_stability × 0.20 +
    max(0, 100 - memory_stability / 5) × 0.15
)

# 数据质量得分
data_quality_score = (
    extraction_accuracy × 0.40 +
    field_completeness × 0.30 +
    data_quality_score × 0.20 +
    structured_extraction_rate × 0.10
)
```

### 8.3 评分等级标准

| 综合得分 | 等级 | 说明 | 建议操作 |
|----------|------|------|----------|
| 90-100 | 优秀 (A) | 抓取能力成熟，可稳定生产使用 | 直接部署 |
| 75-89 | 良好 (B) | 基本可用，个别场景需优化 | 针对性优化 |
| 60-74 | 合格 (C) | 核心功能可用，需持续改进 | 制定改进计划 |
| 40-59 | 待改进 (D) | 存在明显短板，需重点优化 | 优先修复关键问题 |
| < 40 | 不可用 (F) | 当前能力无法支持该网站 | 重新评估可行性 |

### 8.4 通过标准

| 优先级 | 综合评分 | 抓取成功率 | 说明 |
|--------|----------|-----------|------|
| P0 | ≥ 75 | ≥ 80% | 核心网站，必须达标 |
| P1 | ≥ 65 | ≥ 70% | 重要网站，基本可用 |
| P2 | ≥ 55 | ≥ 60% | 扩展网站，核心功能可用 |
| P3 | ≥ 50 | ≥ 50% | 探索网站，最低可用标准 |

---

## 9. 评估执行规范

### 9.1 测试环境

- **浏览器**: Chrome 120+ 或 Edge 120+
- **CDP 端口**: 随机分配，避免冲突
- **网络**: 使用代理池，避免 IP 封禁
- **执行顺序**: 串行执行，避免触发反爬

### 9.2 测试数据

- **搜索关键词**: 每个网站测试 3-5 个关键词
- **数据规模**: 单次评估不超过 100 条数据
- **执行时长**: 单次评估不超过 5 分钟
- **重试次数**: 每个操作最多重试 3 次

### 9.3 数据收集

| 数据类型 | 收集方式 | 存储位置 |
|----------|----------|----------|
| 执行日志 | CDP 日志 | logs/eval_*.jsonl |
| 性能数据 | CDP Performance | logs/perf_*.json |
| 截图 | 关键步骤截图 | output/screenshots/ |
| 评估结果 | JSON 格式 | output/eval_*.json |
| 报告 | Markdown 格式 | output/reports/ |

---

## 10. 指标监控与告警

### 10.1 监控阈值

| 指标 | 警告阈值 | 严重阈值 | 告警方式 |
|------|----------|----------|----------|
| 页面访问成功率 | < 90% | < 80% | 邮件+钉钉 |
| 平均响应时间 | > 3s | > 5s | 邮件+钉钉 |
| 反爬绕过率 | < 60% | < 50% | 邮件+钉钉 |
| 浏览器兼容性 | < 80% | < 70% | 邮件+钉钉 |
| 异常恢复率 | < 70% | < 60% | 邮件+钉钉 |
| 内存稳定性 | > 10MB/h | > 20MB/h | 邮件+钉钉 |

### 10.2 趋势分析

- **周趋势**: 对比上周同期数据，识别退化趋势
- **月趋势**: 对比上月数据，评估整体改进效果
- **网站趋势**: 跟踪单个网站的指标变化

---

## 11. 评估周期

| 评估类型 | 频率 | 覆盖范围 | 执行方式 |
|----------|------|----------|----------|
| 全量评估 | 每周 | 所有 P0/P1 网站 | 自动化执行 |
| 增量评估 | 每日 | 新增/变更网站 | 自动化执行 |
| 专项评估 | 按需 | 特定场景 | 手动+自动化 |
| 版本评估 | 每次发布 | 全量 P0/P1 网站 | 自动化执行 |

---

## 12. 附录

### 12.1 指标缩写对照表

| 缩写 | 全称 | 说明 |
|------|------|------|
| PAR | Page Access Rate | 页面访问成功率 |
| FCP | First Contentful Paint | 首屏加载时间 |
| PLT | Page Load Time | 页面完全加载时间 |
| ART | Average Response Time | 平均响应时间 |
| P95RT | P95 Response Time | P95 响应时间 |
| ACBR | Anti-Crawl Bypass Rate | 反爬绕过率 |
| CPR | Captcha Pass Rate | 验证码通过率 |
| FER | Fingerprint Evasion Rate | 指纹伪装有效性 |
| BC | Browser Compatibility | 浏览器兼容性 |
| DC | Device Compatibility | 设备兼容性 |
| NC | Network Compatibility | 网络兼容性 |
| VC | Version Compatibility | 版本兼容性 |
| EC | Execution Consistency | 重复执行一致性 |
| ERR | Error Recovery Rate | 异常恢复率 |
| CS | Connection Stability | 连接稳定性 |
| MS | Memory Stability | 内存稳定性 |
| EA | Extraction Accuracy | 数据提取准确率 |
| FC | Field Completeness | 字段完整率 |

### 12.2 参考文档

- [evaluation-standards-v2.md](./evaluation-standards-v2.md) - 评估标准 v2.0
- [website-support-list.md](./website-support-list.md) - 支持网站列表
- [assessment-metrics-v2.md](../references/assessment-metrics-v2.md) - 评估指标体系 v2.0
- [evaluation-target.md](../references/evaluation-target.md) - 评估目标说明

---

*本文件为评估体系的核心维度清单，请随 skill 演进持续更新*
