# mini-agent 改进待办

> 本文档从代码结构、功能增强、代码质量等角度梳理 mini_agent 当前的改进方向。

---

## P1：高优先级改进

### 1. 拆分配置模块 (config.py)

当前 `config.py` 承担了过多职责：
- 数据模型定义 (AppConfig, SessionStats)
- 配置加载逻辑 (load_config)
- CLAUDE.md 读取
- System prompt 构建入口

**建议拆分**：
```
src/mini_agent/config/
  __init__.py
  schema.py        # AppConfig, SessionStats 数据模型
  loader.py        # 配置加载逻辑
  prompt_builders.py  # prompt 构建逻辑
```

### 2. 存储层独立

`session.py` 目前仍在 `src/mini_agent/` 根目录，建议：
- 创建 `storage/` 目录
- 将 session 相关逻辑移入
- 考虑引入统一的持久化接口

```
src/mini_agent/storage/
  __init__.py
  session.py
  memory.py
```

### 3. 安全/权限层独立

`permissions.py` 独立为 `security/permissions.py`：
```
src/mini_agent/security/
  __init__.py
  permissions.py
  sandbox.py  # 未来可扩展
```

### 4. status_bar 移动到 ui 层

`orchestrator/status_bar.py` 属于 UI 展示层，应该移动到 `ui/status_bar.py`。

---

## P2：中等优先级改进

### 5. 测试结构优化

当前 `tests/` 目录未按包结构组织，建议：
```
tests/
  unit/
    cli/
    config/
    llm/
    tools/
    orchestrator/
    storage/
    security/
    ui/
  integration/
    test_agent_tool_loop.py
    test_cli_smoke.py
    test_session_resume.py
```

### 6. 代码质量工具链

- 新增 `ruff`/`black` 格式化配置
- 新增 `mypy`/`pyright` 类型检查
- 统一 import 顺序和 lint 规则
- 在 `pyproject.toml` 中添加相关配置

### 7. 文档同步

- README 中的项目结构描述已部分过时
- 确认 `mini_claude_code` / `mini_agent` 命名一致性
- 补充各子模块的详细文档（context_builder, tool_executor, history_manager）

### 8. 错误处理改进

- 当前多处使用 `except Exception` 后静默失败
- Session 加载失败时给用户更明确的提示
- 工具执行错误时返回更详细的错误信息

### 9. 并发控制优化

- `max_llm_calls` 和 `workers` 参数的关系不够清晰
- Sub-Agent 失败时的错误传播机制可以更完善

---

## P3：低优先级/长期演进

### 10. 技能系统增强

- 技能语义匹配 (`skill_semantic_enabled`) 默认关闭，可考虑改为默认开启
- 技能内容裁剪 (`skill_chunking_enabled`) 实现未完成
- 技能使用统计可以更详细

### 11. 工具缓存优化

- `ToolResultCache` 未实现过期机制
- 缓存键可能产生冲突（复杂输入）
- 可考虑引入 LRU 淘汰策略

### 12. 文件监听完善

- `file_watcher.py` 实现未完成，只列出了 TODO
- 跨平台文件监控可能需要使用 `watchdog` 库
- 当前基于哈希轮询的方式效率较低

### 13. 持久化模型统一

- 任务、计划、Session、Memory 持久化模型进一步统一
- 考虑引入统一的 persistence 层

### 14. 端到端测试

- 建立端到端 CLI smoke test
- 增加 provider mock contract test
- 模拟 LLM 响应进行集成测试

### 15. 性能优化

- Token 估算不够准确
- 大文件读取时内存占用较高
- 并发调用时的资源竞争问题

### 16. 用户交互改进

- 权限确认时的快捷键支持
- 进度条显示长时间操作
- 更丰富的错误提示和恢复建议

---

## 已完成项

以下改进已完成：

- ✅ **阶段 1**: 引入 `src/mini_agent` 包布局
- ✅ **阶段 2**: 拆分 `main.py` 为 cli/* 模块
- ✅ **阶段 3**: UI 模块统一到 `ui/` 目录
- ✅ **阶段 4**: Agent 拆分为 context_builder, tool_executor, history_manager
- ✅ **感知与记忆**: perception/ 目录完整实现
- ✅ **并发编排**: orchestrator/ 目录包含 task, plan, status_bar 等

---

## 备注

- P1 改进建议拆分成多个独立的 PR，每个 PR 有明确的验收标准
- P2 改进可根据实际需求优先级调整
- P3 改进属于长期演进方向，可在项目稳定后逐步实现
