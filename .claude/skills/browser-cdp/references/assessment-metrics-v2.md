# Browser-CDP 网站操作能力评估指标体系 v2.0

**版本**: 2.0.0  
**创建日期**: 2026-08-06  
**状态**: 正式版  
**关联文档**: [test-case-specification.md](./test-case-specification.md)、[evaluation-standards.md](./evaluation-standards.md)

---

## 1. 评估指标总览

| 指标类别 | 权重 | 核心指标数 | 评估目标 |
|----------|------|-----------|----------|
| 页面加载能力 | 25% | 4 | 页面访问成功率、加载性能、超时处理 |
| 元素定位能力 | 25% | 4 | 定位准确率、交互成功率、动态元素识别 |
| 数据提取能力 | 20% | 4 | 提取准确率、字段完整率、数据质量 |
| 反检测能力 | 15% | 3 | 反爬绕过率、验证码通过率、指纹伪装 |
| 稳定性与恢复 | 15% | 3 | 重复一致性、异常恢复、连接稳定性 |

---

## 2. 页面加载能力指标（权重 25%）

### 2.1 核心指标定义

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

### 2.3 评分标准

| 综合得分 | 等级 | 说明 |
|----------|------|------|
| 90-100 | 优秀 | 页面加载能力成熟，可稳定生产使用 |
| 75-89 | 良好 | 基本可用，个别场景需优化 |
| 60-74 | 合格 | 核心功能可用，需持续改进 |
| 40-59 | 待改进 | 存在明显短板，需重点优化 |
| < 40 | 不可用 | 当前能力无法支持该网站 |

---

## 3. 元素定位能力指标（权重 25%）

### 3.1 核心指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 元素定位成功率 | `element_locate_rate` | 成功定位目标元素的比例 | 成功定位次数 / 总定位尝试次数 × 100% | ≥ 90% | 35% |
| 交互成功率 | `interaction_success_rate` | 成功执行点击/输入等交互的比例 | 成功交互次数 / 总交互尝试次数 × 100% | ≥ 85% | 30% |
| 动态元素识别率 | `dynamic_element_rate` | 动态加载元素的识别能力 | 识别动态元素数 / 总动态元素数 × 100% | ≥ 80% | 20% |
| 定位策略覆盖率 | `locator_strategy_coverage` | 使用的定位策略种类 | 已验证策略数 / 总策略数 × 100% | ≥ 70% | 15% |

### 3.2 定位策略清单

| 策略代码 | 策略名称 | 说明 | 适用场景 |
|----------|----------|------|----------|
| `css` | CSS 选择器 | 使用 class、id、tag 等选择器 | 静态页面、简单结构 |
| `xpath` | XPath 表达式 | 使用路径表达式定位 | 复杂嵌套结构 |
| `text` | 文本匹配 | 根据元素文本内容定位 | 按钮、链接等 |
| `attr` | 属性匹配 | 根据 data-* 等属性定位 | 动态生成的元素 |
| `relative` | 相对定位 | 基于相邻元素定位 | 列表项、卡片等 |
| `scroll` | 滚动定位 | 滚动到元素可见后定位 | 懒加载内容 |
| `js` | JS 执行 | 通过 JavaScript 定位 | 特殊场景 |

### 3.3 计算方法

```python
# 元素定位成功率
element_locate_rate = (successful_locates / total_locate_attempts) * 100

# 交互成功率
interaction_success_rate = (successful_interactions / total_interaction_attempts) * 100

# 动态元素识别率
dynamic_element_rate = (identified_dynamic_elements / total_dynamic_elements) * 100

# 定位策略覆盖率
locator_strategy_coverage = (verified_strategies / total_strategies) * 100
```

---

## 4. 数据提取能力指标（权重 20%）

### 4.1 核心指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 数据提取准确率 | `extraction_accuracy` | 提取数据与真实值一致的比例 | 正确提取条数 / 总提取条数 × 100% | ≥ 85% | 40% |
| 字段完整率 | `field_completeness` | 成功提取的字段数占预期字段数的比例 | 已提取字段数 / 预期字段数 × 100% | ≥ 80% | 30% |
| 数据质量得分 | `data_quality_score` | 提取数据的完整性和准确性综合评分 | 准确率×0.6 + 完整率×0.4 | ≥ 80% | 20% |
| 结构化提取成功率 | `structured_extraction_rate` | 成功提取结构化数据的比例 | 成功提取结构化数据次数 / 总尝试次数 × 100% | ≥ 75% | 10% |

### 4.2 计算方法

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

---

## 5. 反检测能力指标（权重 15%）

### 5.1 核心指标定义

| 指标名称 | 指标代码 | 定义 | 计算方法 | 目标值 | 权重 |
|----------|----------|------|----------|--------|------|
| 反爬绕过率 | `anti_crawl_bypass_rate` | 成功绕过反爬机制的比例 | 绕过成功次数 / 总反爬触发次数 × 100% | ≥ 70% | 40% |
| 验证码通过率 | `captcha_pass_rate` | 验证码识别与处理成功率 | 通过验证码次数 / 总验证码次数 × 100% | ≥ 60% | 30% |
| 指纹伪装有效性 | `fingerprint_evasion_rate` | 浏览器指纹被识别为机器人的比例 | 1 - 被识别比例 | ≥ 80% | 20% |
| 行为模拟自然度 | `behavior_naturalness` | 操作行为被判定为人类的比例 | 人工判定为人类的操作次数 / 总操作次数 × 100% | ≥ 75% | 10% |

### 5.2 反爬机制类型

| 机制类型 | 检测方式 | 应对策略 |
|----------|----------|----------|
| User-Agent 检测 | 检测浏览器标识 | 随机化 UA 池 |
| 请求频率限制 | 限制单位时间请求数 | 限速器、随机延迟 |
| IP 封禁 | 封禁异常 IP | 代理池轮换 |
| Cookie 验证 | 要求有效 Cookie | 会话管理 |
| JavaScript 挑战 | 执行 JS 验证 | CDP 执行 JS |
| 行为分析 | 分析鼠标轨迹、点击模式 | 模拟人类行为 |
| 验证码 | 人机验证 | 验证码处理模块 |

### 5.3 计算方法

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

---

## 6. 稳定性与恢复能力指标（权重 15%）

### 6.1 核心指标定义

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

---

## 7. 综合评分计算

### 7.1 计算公式

```python
综合评分 = (
    页面加载能力得分 × 0.25 +
    元素定位能力得分 × 0.25 +
    数据提取能力得分 × 0.20 +
    反检测能力得分 × 0.15 +
    稳定性与恢复能力得分 × 0.15
)
```

### 7.2 各维度得分计算

```python
# 页面加载能力得分
page_loading_score = (
    page_access_rate × 0.40 +
    (100 - min(fcp_time / 3 * 100, 100)) × 0.25 +
    (100 - min(page_load_time / 10 * 100, 100)) × 0.20 +
    timeout_handling_rate × 0.15
)

# 元素定位能力得分
element_locate_score = (
    element_locate_rate × 0.35 +
    interaction_success_rate × 0.30 +
    dynamic_element_rate × 0.20 +
    locator_strategy_coverage × 0.15
)

# 数据提取能力得分
extraction_score = (
    extraction_accuracy × 0.40 +
    field_completeness × 0.30 +
    data_quality_score × 0.20 +
    structured_extraction_rate × 0.10
)

# 反检测能力得分
anti_detection_score = (
    anti_crawl_bypass_rate × 0.40 +
    captcha_pass_rate × 0.30 +
    fingerprint_evasion_rate × 0.20 +
    behavior_naturalness × 0.10
)

# 稳定性与恢复能力得分
stability_score = (
    execution_consistency × 0.35 +
    error_recovery_rate × 0.30 +
    connection_stability × 0.20 +
    max(0, 100 - memory_stability / 5) × 0.15
)
```

### 7.3 评分等级标准

| 综合得分 | 等级 | 说明 | 建议操作 |
|----------|------|------|----------|
| 90-100 | 优秀 (A) | 抓取能力成熟，可稳定生产使用 | 直接部署 |
| 75-89 | 良好 (B) | 基本可用，个别场景需优化 | 针对性优化 |
| 60-74 | 合格 (C) | 核心功能可用，需持续改进 | 制定改进计划 |
| 40-59 | 待改进 (D) | 存在明显短板，需重点优化 | 优先修复关键问题 |
| < 40 | 不可用 (F) | 当前能力无法支持该网站 | 重新评估可行性 |

---

## 8. 评估执行流程

### 8.1 评估步骤

1. **准备阶段**: 配置测试环境，准备测试数据
2. **执行阶段**: 按测试用例集执行评估
3. **收集阶段**: 收集各项指标数据
4. **计算阶段**: 计算各维度得分和综合评分
5. **报告阶段**: 生成评估报告和改进建议

### 8.2 评估周期

| 评估类型 | 频率 | 覆盖范围 | 执行方式 |
|----------|------|----------|----------|
| 全量评估 | 每周 | 所有 P0/P1 网站 | 自动化执行 |
| 增量评估 | 每日 | 新增/变更网站 | 自动化执行 |
| 专项评估 | 按需 | 特定场景 | 手动+自动化 |

---

## 9. 指标监控与告警

### 9.1 监控阈值

| 指标 | 警告阈值 | 严重阈值 | 告警方式 |
|------|----------|----------|----------|
| 页面访问成功率 | < 90% | < 80% | 邮件+钉钉 |
| 元素定位成功率 | < 85% | < 75% | 邮件+钉钉 |
| 数据提取准确率 | < 80% | < 70% | 邮件+钉钉 |
| 反爬绕过率 | < 60% | < 50% | 邮件+钉钉 |
| 异常恢复率 | < 70% | < 60% | 邮件+钉钉 |

### 9.2 趋势分析

- **周趋势**: 对比上周同期数据，识别退化趋势
- **月趋势**: 对比上月数据，评估整体改进效果
- **网站趋势**: 跟踪单个网站的指标变化

---

## 10. 附录

### 10.1 指标缩写对照表

| 缩写 | 全称 | 说明 |
|------|------|------|
| PAR | Page Access Rate | 页面访问成功率 |
| FCP | First Contentful Paint | 首屏加载时间 |
| PLT | Page Load Time | 页面完全加载时间 |
| ELR | Element Locate Rate | 元素定位成功率 |
| ISR | Interaction Success Rate | 交互成功率 |
| DER | Dynamic Element Rate | 动态元素识别率 |
| EA | Extraction Accuracy | 数据提取准确率 |
| FC | Field Completeness | 字段完整率 |
| ACBR | Anti-Crawl Bypass Rate | 反爬绕过率 |
| CPR | Captcha Pass Rate | 验证码通过率 |
| EC | Execution Consistency | 重复执行一致性 |
| ERR | Error Recovery Rate | 异常恢复率 |

### 10.2 参考文档

- [evaluation-standards.md](./evaluation-standards.md) - 旧版评估标准
- [test-case-specification.md](./test-case-specification.md) - 测试用例规格
- [website-evaluation-framework.md](./website-evaluation-framework.md) - 网站评估框架

---

*本评估指标体系为 browser-cdp skill 能力建设的核心文档，请随 skill 演进持续更新*
