# Memory 功能测试案例

## 功能概述

测试 mini-agent 的长期记忆（Memory）功能，验证记忆保存、检索和跨 session 共享能力。

## 前置条件

1. **启用 memory 功能**：确保配置文件中 `memory_enabled = true`
2. **Session 摘要功能**：`session_summary_enabled = true` 且 `session_summary_min_turns` 设置合理值（如 3）
3. **清理旧数据**（可选）：删除 `.agent/memory.jsonl` 以从头开始测试

## 测试场景

### 场景一：记忆保存触发

**目的**：验证 session 达到指定回合数后自动保存记忆

**测试步骤**：
1. 启动 agent：`python -m mini_agent`
2. 依次发送以下消息（每发送一条等待 agent 回复）：

**测试输入**：
```
1. 帮我写一个 Python 函数，计算斐波那契数列的第 n 项
2. 这个函数如何用递归方式实现？
3. 递归实现有什么性能问题？如何优化？
```

**预期结果**：
- 第 3 条消息回复后，agent 应自动触发 session 摘要生成
- 控制台应显示摘要生成相关日志
- `.agent/memory.jsonl` 文件中应新增一条记忆条目

**验证方法**：
```bash
# 查看记忆文件内容
cat .agent/memory.jsonl

# 或（Windows）
type .agent\memory.jsonl
```

---

### 场景二：记忆检索注入

**目的**：验证新 session 能够检索并注入相关记忆

**测试步骤**：
1. 完成场景一后，退出当前 agent session
2. 重新启动 agent（在同一项目目录下）
3. 发送与之前相关的问题

**测试输入**：
```
之前我们讨论的斐波那契数列优化方法是什么？
```

**预期结果**：
- System prompt 中应包含检索到的相关记忆
- agent 能够回答之前讨论的内容

**验证方法**：
- 观察 agent 回复是否引用了之前的讨论
- 启用 `debug_llm = true` 查看完整的 system prompt

---

### 场景三：记忆标签提取

**目的**：验证记忆自动标签提取功能

**测试步骤**：
1. 发送一个技术主题的问题
2. 检查生成的记忆条目的 tags 字段

**测试输入**：
```
请帮我解释一下什么是 REST API，以及如何用 Python Flask 框架实现一个 RESTful 接口
```

**预期结果**：
- 记忆条目应包含自动提取的标签（如：REST、API、Flask、Python 等）
- 标签应为 3 个以上的中文字或英文单词

---

### 场景四：记忆时效衰减

**目的**：验证记忆检索的时间衰减机制

**测试步骤**：
1. 创建一条关于特定主题的记忆（如"数据库连接"）
2. 等待一段时间（或修改记忆文件的 created_at 时间戳模拟旧记忆）
3. 搜索相关主题

**测试输入**：
```
如何在 Python 中优化数据库连接性能？
```

**预期结果**：
- 较新的相关记忆优先级更高
- 旧记忆仍然存在但评分降低

---

### 场景五：项目记忆 vs 全局记忆

**目的**：验证两级记忆系统的分流机制

**测试步骤**：
1. 在当前项目中完成一个项目特定的任务
2. 切换到另一个项目目录启动 agent
3. 发送相同类型的问题

**预期结果**：
- 当前项目应优先使用项目级记忆（`<project>/.agent/memory.jsonl`）
- 项目没有的内容可从全局记忆（`~/.agent/memory.jsonl`）检索

---

## 配置示例

```python
# .agent/agent_config.json 或环境变量
{
    "memory_enabled": true,
    "memory": {
        "backend": "local",
        "max_entries": 500,
        "decay_half_life_days": 30,
        "global_enabled": true
    },
    "session_summary_enabled": true,
    "session_summary_min_turns": 3,
    "memory_top_k": 5
}
```

## 故障排查

1. **记忆未保存**：检查 `session_summary_enabled` 和 `session_summary_min_turns` 配置
2. **记忆未检索**：确认 `memory_enabled = true`，检查 `memory_top_k` 设置
3. **文件写入失败**：确保 `.agent/` 目录有写入权限

## 相关文件

- 核心代码：`src/mini_agent/perception/memory_store.py`
- 工厂模式：`src/mini_agent/perception/memory_factory.py`
- 上下文注入：`src/mini_agent/context_builder.py`
- 记忆保存：`src/mini_agent/agent.py` (_generate_and_save_summary)
