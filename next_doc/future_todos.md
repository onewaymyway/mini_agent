# 代码结构改进建议

> 本文档从代码组织结构角度梳理 mini_agent 当前的问题、推荐目录布局和后续演进方向。

---

## P2：较高风险、适合单独 PR

### 拆分配置、存储与安全

将基础设施模块细分：

```
src/mini_agent/config/schema.py   # AppConfig, SessionStats
src/mini_agent/config/loader.py   # load_config, env/json/CLAUDE.md 加载
src/mini_agent/storage/session.py # Session, SessionManager
src/mini_agent/security/permissions.py
```

验收标准：

- `AppConfig` 等纯数据结构不依赖 CLI。
- 配置加载逻辑可用临时目录和 mock 环境变量单独测试。
- 权限策略可独立测试，不依赖真实终端输入。

### 进一步细分 orchestrator 模块

当前 `orchestrator/` 目录下的模块职责可以进一步优化：
- `status_bar.py` 可以移到 `ui/` 目录，因为它属于 UI 展示层
- 考虑将 `plan.py` 和 `task.py` 的数据模型与调度逻辑分离

---

## P3：长期演进

### 持久化模型统一

- 任务、计划、Session、Memory 持久化模型进一步统一。
- 考虑引入统一的 persistence 层来处理所有持久化需求。

### 代码质量工具链

- 引入更严格的类型检查（mypy/pyright）
- 配置格式化工具（ruff/black）
- 统一 import 顺序和 lint 规则

### 测试覆盖

- 建立端到端 CLI smoke test
- 增加 provider mock contract test
- 测试用例按包结构组织：
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

---

## 当前待办

### 1. 配置模块进一步细分

当前 `config.py` 仍承担多个职责，建议拆分为：
- `config/schema.py` — 数据模型定义（AppConfig 等）
- `config/loader.py` — 配置加载逻辑
- `config/prompt_builders.py` — prompt 相关构建逻辑（如果从 context_builder 拆分出来）

### 2. 存储层独立

建议创建 `storage/` 目录，将 session 相关逻辑移入：
```
src/mini_agent/storage/
  __init__.py
  session.py
  memory.py  # 未来可扩展
```

### 3. 安全/权限层独立

将 `permissions.py` 独立为 `security/permissions.py`，便于后续扩展更多安全策略：
```
src/mini_agent/security/
  __init__.py
  permissions.py
  sandbox.py  # 未来可扩展
```

### 4. status_bar 移动到 ui 层

`orchestrator/status_bar.py` 属于 UI 展示层，应该移动到 `ui/status_bar.py`。

### 5. 文档同步

- README 中的项目结构描述需要同步更新
- 确认 `mini_claude_code` / `mini_agent` 命名一致性
- 补充各子模块的详细文档
