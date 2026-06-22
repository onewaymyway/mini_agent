# 观察性系统指南（Stage 6）

> 对应 `next_doc/self_evolution_stage4plus_plan.md` Stage 6，
> 设计依据 `next_doc/self_evolution_design.md` 第 9/10/11 章。

---

## 1. 这是什么

Stage 6 为 mini_agent 添加了一套**观察性（Observability）**基础设施，由四个子系统组成：

| 子系统 | 编号 | 核心文件 | 作用 |
|--------|------|----------|------|
| 时序追踪 | 6.1 | `sessions/<id>/traces.jsonl` | 记录每轮各阶段耗时与 token 分布 |
| 健康诊断端点 | 6.2 | `GET /v1/diagnostics` | 实时聚合健康状态 |
| 异常行为检测 | 6.3 | `perception/observability.py` | 基于历史基线检测异常 session |
| 工具调用因果链 | 6.4 | `traces.jsonl` tool_call 记录 | 追踪失败→修复的工具调用序列 |

所有子系统的核心逻辑在 `src/mini_agent/perception/observability.py`。

---

## 2. 时序追踪（6.1）

### 2.1 traces.jsonl 格式

每次 `run_turn` 结束后，若追踪已启用，会在 session 目录写入若干行 JSONL：

```
.agent/sessions/<session_id>/traces.jsonl
```

每行一条追踪记录，`phase` 字段区分类型：

#### build_system 阶段

```json
{
  "session_id": "abc123",
  "turn_id": 3,
  "phase": "build_system",
  "started_at": 1720000000.123,
  "elapsed_ms": 42.5,
  "context_breakdown": {
    "system_base": 1200,
    "history": 3400,
    "total": 4600
  }
}
```

#### call_llm 阶段

```json
{
  "session_id": "abc123",
  "turn_id": 3,
  "phase": "call_llm",
  "started_at": 1720000000.200,
  "elapsed_ms": 1850.3,
  "input_tokens": 4600,
  "output_tokens": 312
}
```

#### execute_tools 阶段

```json
{
  "session_id": "abc123",
  "turn_id": 3,
  "phase": "execute_tools",
  "elapsed_ms": 280.0,
  "tool_count": 3,
  "tool_error_count": 1
}
```

#### tool_call 记录（6.4 因果链）

```json
{
  "session_id": "abc123",
  "turn_id": 3,
  "phase": "tool_call",
  "sequence_in_turn": 1,
  "tool_name": "bash",
  "is_error": true,
  "error_category": "not_found",
  "resolves_seq": null
}
```

```json
{
  "session_id": "abc123",
  "turn_id": 3,
  "phase": "tool_call",
  "sequence_in_turn": 2,
  "tool_name": "bash",
  "is_error": false,
  "error_category": null,
  "resolves_seq": 1
}
```

`resolves_seq` 指向同一 turn 内被修复的那次失败调用的 `sequence_in_turn`，是**因果链**的关键字段——可用于反思 LLM 理解"哪次重试修复了哪次失败"。

### 2.2 SessionTracer API

```python
from mini_agent.perception.observability import SessionTracer

tracer = SessionTracer(session_dir=Path(".agent/sessions/abc123"), session_id="abc123")

# 用 span() context manager 自动计时并写入
with tracer.span("call_llm", turn_id=3) as sp:
    sp["input_tokens"] = 1000
    sp["output_tokens"] = 300
    result = call_llm(...)

# 获取本 session 的聚合摘要
summary = tracer.get_summary()
# {
#   "turn_count": 5,
#   "avg_call_llm_ms": 1850.3,
#   "total_input_tokens": 12000,
#   "tool_error_rate": 0.12,
#   "error_categories": {"not_found": 3, "timeout": 1},
#   ...
# }
```

### 2.3 开关控制

追踪由 `ObservabilityConfig` 控制（见[配置指南](config-guide.md)）：

```json
{
  "observability": {
    "enabled": true,
    "tracing_enabled": true
  }
}
```

可以独立关闭 `tracing_enabled`（只禁用 traces.jsonl 写入，不影响 `/diagnostics` 聚合与异常检测）。

---

## 3. 工具调用错误分类（6.4）

### 3.1 error_category 枚举

`classify_error(result_str)` 函数把工具调用结果字符串映射到以下枚举值：

| error_category | 触发模式示例 |
|----------------|-------------|
| `permission` | `PermissionError`、`[Permission denied]`、`[DENIED]` |
| `not_found` | `FileNotFoundError`、`NoSuchFile` |
| `timeout` | `TimeoutError`、`timed out` |
| `network` | `ConnectionError`、`ECONNREFUSED` |
| `syntax` | `SyntaxError` |
| `import` | `ModuleNotFoundError`、`ImportError` |
| `parse` | `JSONDecodeError` |
| `encoding` | `UnicodeDecodeError` |
| `process` | `CalledProcessError`、`[exit code: N]` |
| `key_access` | `KeyError`、`AttributeError`、`IndexError` |
| `type_value` | `TypeError`、`ValueError` |
| `io` | `OSError`、`IOError` |
| `runtime` | `RuntimeError` |
| `other` | 其他所有 |

### 3.2 与 Reminder 系统联动（15.2）

`error_category` 可在 reminder 的 `condition` 字段里精确路由，免去正则误匹配：

```yaml
---
name: not_found_hint
trigger_event: tool_error
condition:
  error_category: "not_found"
inject_as: user
priority: 75
enabled: true
---

文件不存在，请先用 `list_dir` 确认路径，或改用相对路径。
```

详见[提示注入系统指南](reminder-system-guide.md)。

---

## 4. /diagnostics 端点（6.2）

### 4.1 请求

```
GET /v1/diagnostics
Authorization: Bearer <token>
```

### 4.2 响应结构

```json
{
  "performance": {
    "turn_count": 12,
    "total_elapsed_ms": 45820.0,
    "avg_call_llm_ms": 1930.5,
    "avg_build_system_ms": 38.2,
    "avg_execute_tools_ms": 290.1,
    "total_input_tokens": 58000,
    "total_output_tokens": 9200,
    "context_breakdown_avg": {
      "system_base": 1100,
      "history": 3500,
      "total": 4600
    },
    "tool_error_rate": 0.08,
    "error_categories": {
      "not_found": 4,
      "timeout": 2,
      "permission": 1
    }
  },
  "memory": {
    "total_entries": 47,
    "by_type": {
      "summary": 28,
      "lesson": 15,
      "capability_map": 4
    }
  },
  "skills": {
    "active_count": 3,
    "active": ["python-expert", "bash-safety", "git-workflow"],
    "usage_scores": {}
  },
  "evolution": {
    "pending_evolve_branches": ["evolve/20260620-skill-bash-safety"],
    "pending_branches_count": 1,
    "open_threads_high_count": 2,
    "open_threads_high": [...]
  },
  "anomaly_flags": []
}
```

### 4.3 各分组说明

| 分组 | 数据来源 | 说明 |
|------|---------|------|
| `performance` | 当前 session `traces.jsonl` | 聚合摘要，session 开始时清零 |
| `memory` | `workdir memory.jsonl` | 按 `entry_type` 计数 |
| `skills` | 当前 `SkillLoader` 实例 | 实时激活状态 |
| `evolution` | `self_profile.json` + `open_threads.json` | 演化流水线状态 |
| `anomaly_flags` | `activity_log.jsonl` 基线 + 当前 session 统计 | 异常标记列表 |

任何分组读取失败都会静默降级（返回空对象/空数组），不影响其他分组。

---

## 5. 异常行为检测（6.3）

### 5.1 原理

使用 **k-σ 统计检测**：从 `activity_log.jsonl` 中读取历史 `session_metrics` 记录（每次 session 结束时自动写入），计算各指标的均值和标准差，若当前 session 的某指标超过 `mean + k * std` 则告警。

```
anomaly flag 触发条件：value > mean + k_sigma × std
默认 k_sigma = 3.0（保守，避免频繁误报）
```

### 5.2 检测指标

| flag_type | 数据字段 | 说明 |
|-----------|---------|------|
| `tool_call_spike` | `tool_count` | 当前 session 工具调用总次数异常高 |
| `token_spike` | `total_tokens` | 输入+输出 token 总量异常高 |
| `session_duration_spike` | `duration_min` | session 时长异常长 |

### 5.3 session_metrics 记录

每次 `trigger_session_end()` 时自动追加到 `activity_log.jsonl`：

```json
{
  "ts": 1720000000.0,
  "record_type": "session_metrics",
  "session_id": "abc123",
  "tool_count": 24,
  "total_tokens": 58000,
  "duration_min": 8.3
}
```

> **注意**：检测需要至少 `anomaly_min_samples`（默认 10）条历史记录才会生效，新项目初期不会触发误报。

### 5.4 配置

```json
{
  "observability": {
    "anomaly_k_sigma": 3.0,
    "anomaly_min_samples": 10
  }
}
```

### 5.5 flag 结构

```json
{
  "flag_type": "tool_call_spike",
  "value": 87.0,
  "baseline": 18.2,
  "threshold": 42.5,
  "session_id": "abc123",
  "detected_at": 1720000000.0
}
```

---

## 6. 存储成本说明

| 文件 | 生命周期 | 预期大小 |
|------|---------|---------|
| `traces.jsonl` | session 级，随 session 保留 | 每 turn ~3-5 行，约 1-5 KB/session |
| `phase_g_rhythm.json` | workdir 级，长期保留 | 极小，仅记录时间戳 |
| `session_metrics` 行（in `activity_log.jsonl`）| global，长期积累 | 每 session 1 行，约 100-200 bytes |

traces.jsonl 不会自动清理——长期项目可按需用 `git gc` 或定期归档脚本处理。`/diagnostics` 端点只读当前 session 的 traces，不扫描历史文件。

---

## 7. 代码入口速查

| 功能 | 位置 |
|------|------|
| `classify_error()` | `src/mini_agent/perception/observability.py` |
| `SessionTracer` | `src/mini_agent/perception/observability.py` |
| `detect_anomalies()` | `src/mini_agent/perception/observability.py` |
| tracer 初始化 | `agent.py → _init_tracer()` |
| tracer 打点 | `agent.py → _agentic_loop()` (call_llm/execute_tools/build_system) |
| 因果链记录 | `agent.py → _execute_tools()` |
| SessionEnd 写入 | `agent.py → _run_observability_on_session_end()` |
| `/diagnostics` 路由 | `src/mini_agent/api/routes.py → get_diagnostics()` |
| `ObservabilityConfig` | `src/mini_agent/config/models.py` |
| session_traces 路径 | `src/mini_agent/storage/paths.py → session_traces()` |

---

## 8. 相关文档

- [配置指南](config-guide.md) — `ObservabilityConfig` 详细参数
- [HTTP API 指南](http-api-guide.md) — `/diagnostics` 端点完整说明
- [提示注入系统指南](reminder-system-guide.md) — `error_category` 精确路由
- [Phase G 后台循环指南](self-evolution-phase-g-guide.md) — 消费 `/diagnostics` 数据的 8.2 剪枝扫描
- [存储设计](storage-design.md) — `traces.jsonl` 路径约定
