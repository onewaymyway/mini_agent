# 用户画像（Profile）功能测试案例

## 功能概述

测试 mini-agent 的自动用户画像（Profile）系统，验证画像生成、注入、偏好设置和跨 session 共享能力。

## 前置条件

1. **启用记忆系统**：`memory_enabled = true`（画像基于长期记忆生成）
2. **配置画像参数**：
   ```json
   {
     "profile_min_entries": 3,
     "profile_refresh_interval_entries": 5,
     "profile_max_entries": 20
   }
   ```
3. **清理旧数据**（可选）：删除 `~/.agent/profile.json` 以从头开始测试

---

## 测试场景

### 场景一：新用户画像首次生成

**目的**：验证新用户（无画像）在满足条件时自动触发画像生成

**测试步骤**：
1. 清理旧画像文件：
   ```bash
   del ~/.agent/profile.json  # Windows
   rm ~/.agent/profile.json   # Linux/Mac
   ```
2. 启动 agent：`python -m mini_agent`
3. 进行多个技术主题的多轮对话（至少 3 个不同主题，每主题 2-3 轮对话）

**测试输入**：
```
主题 1（Python 开发）：
1. 帮我写一个 Python 的装饰器，用于记录函数执行时间
2. 这个装饰器如何支持异步函数？
3. 如果要对类方法使用这个装饰器需要注意什么？

主题 2（数据库）：
1. 如何用 SQLAlchemy 实现数据库连接池？
2. 连接池大小如何设置比较合适？
3. 遇到连接泄露问题如何排查？

主题 3（测试）：
1. 写一个 pytest 的 fixture，用于设置测试数据库
2. 如何用 mock 替换外部 API 调用？
3. 测试覆盖率如何配置和查看？
```

**预期结果**：
- 记忆条目数达到 `profile_min_entries`（默认 10 条）后，系统应在后台触发画像生成
- `.agent/profile.json` 文件应被创建
- 文件中的 `derived` 字段应包含：
  - `summary`：用户的技术背景总结
  - `tech_stack`：应包含 ["Python", "SQLAlchemy", "pytest"] 等相关技术
  - `habits`：用户的工作习惯推断

**验证方法**：
```bash
# 查看画像文件内容
cat ~/.agent/profile.json
# 或（Windows）
type ~/.agent/profile.json

# 或使用 Python 检查
python -c "import json; print(json.dumps(json.load(open('~/.agent/profile.json')), indent=2))"
```

---

### 场景二：画像注入 System Prompt

**目的**：验证画像生成后能正确注入到后续对话的 system prompt

**测试步骤**：
1. 完成场景一后，退出当前 agent
2. 重新启动 agent（同一项目目录）
3. 启用 debug 模式查看 system prompt

**测试输入**：
```
开启 debug_llm 模式
你好，我们继续之前的工作
```

**预期结果**：
- System prompt 中应包含 "## User profile (from past sessions)" 部分
- 画像内容应与之前对话的技术主题相关
- Agent 的回复风格应与用户的技术背景匹配

**验证方法**：
- 观察 debug 日志中打印的完整 system prompt
- 检查是否包含画像注入内容

---

### 场景三：画像增量刷新

**目的**：验证画像能根据新的记忆条目增量更新

**测试步骤**：
1. 完成场景一和场景二
2. 记录当前画像的 `source_entry_count` 值
3. 继续新的对话（新增至少 `profile_refresh_interval_entries` 条记忆，默认 5 条）
4. 使用不同的技术主题

**测试输入**：
```
主题 4（前端开发）：
1. 用 React 写一个自定义 hook，用于管理表单状态
2. 这个 hook 如何支持验证和错误处理？
3. 如何与 TypeScript 类型系统结合？

主题 5（DevOps）：
1. 写一个 Dockerfile，优化 Python 应用的构建速度
2. 如何使用多阶段构建减小镜像体积？
3. 在 Kubernetes 中如何配置资源限制？
```

**预期结果**：
- 记忆条目数增加后，系统应再次触发画像刷新
- 画像中的 `tech_stack` 应新增 ["React", "TypeScript", "Docker", "Kubernetes"]
- `source_entry_count` 应更新为新的记忆条目总数
- `updated_at` 时间戳应更新

**验证方法**：
```bash
# 比较刷新前后的画像
python -c "
import json
with open('~/.agent/profile.json') as f:
    p = json.load(f)
print('技术栈:', p.get('derived', {}).get('tech_stack', []))
print('记忆条目数:', p.get('derived', {}).get('source_entry_count', 0))
print('更新时间:', p.get('derived', {}).get('updated_at', 0))
"
```

---

### 场景四：用户偏好设置

**目的**：验证用户可以手动设置偏好，且不会被自动覆盖

**测试步骤**：
1. 启动 agent
2. 使用 `/profile` 命令设置偏好（如果实现了 CLI 命令）
3. 或直接在代码中调用 `set_preference()` 方法

**测试输入**：
```python
# 通过 Python 脚本设置
from mini_agent.profile import UserProfileManager
from mini_agent.storage.paths import AgentPaths

paths = AgentPaths(project_root=Path("."))
profile_mgr = UserProfileManager(paths)
profile_mgr.set_preference("language", "中文")
profile_mgr.set_preference("tone", "简洁直接")
profile_mgr.set_preference("preferred_model", "claude-opus-4-5")
profile_mgr.set_display_name("开发者 Alice")
profile_mgr.save()
```

**预期结果**：
- `preferences` 字段应包含设置的键值对
- `display_name` 应更新为 "开发者 Alice"
- 即使画像刷新，`preferences` 内容保持不变

**验证方法**：
```bash
# 查看画像文件
python -c "
import json
with open('~/.agent/profile.json') as f:
    p = json.load(f)
print('偏好设置:', p.get('preferences', {}))
print('显示名称:', p.get('display_name', ''))
"
```

---

### 场景五：画像持久化和跨 session 共享

**目的**：验证画像在不同 session 间持久化存在

**测试步骤**：
1. 完成场景一的对话，等待画像生成
2. 退出 agent
3. 重新启动 agent（同一项目目录，不同进程）
4. 发送与之前相关的问题

**测试输入**：
```
我之前讨论过的 Python 装饰器支持异步函数的实现方式是什么？
```

**预期结果**：
- Agent 应该能回答之前讨论的内容
- System prompt 中包含之前生成的画像
- 画像内容与新 session 的技术方向一致

---

### 场景六：画像生成失败处理

**目的**：验证画像生成失败时的容错机制

**测试步骤**：
1. 修改 `profile_summarizer.md` prompt，使 LLM 返回无效 JSON
2. 或模拟 LLM 调用超时
3. 触发画像生成

**预期结果**：
- 系统不应崩溃
- 画像文件应仍被创建，但 `derived` 可能为空或使用降级内容
- 如果 LLM 返回文本，`summary` 字段应包含该文本（最多 500 字符）

**验证方法**：
- 检查程序是否正常运行
- 查看画像文件内容

---

### 场景七：多用户路径隔离（预留测试）

**目的**：验证多用户模式下不同用户的画像隔离

**测试步骤**：
1. 构造两个不同 `user_id` 的 `UserProfileManager` 实例
2. 分别为两个用户生成画像

**测试代码**：
```python
from mini_agent.profile import UserProfileManager
from mini_agent.storage.paths import AgentPaths

paths = AgentPaths(project_root=Path("."))

# 用户 A
mgr_a = UserProfileManager(paths, user_id="user_a")
mgr_a.load()
# ... 生成用户 A 的画像

# 用户 B
mgr_b = UserProfileManager(paths, user_id="user_b")
mgr_b.load()
# ... 生成用户 B 的画像
```

**预期结果**：
- 用户 A 的画像存储在 `~/.agent/users/user_a/profile.json`
- 用户 B 的画像存储在 `~/.agent/users/user_b/profile.json`
- 两个画像内容相互独立

---

## 配置示例

```json
{
  "memory_enabled": true,
  "memory_top_k": 5,
  "profile_min_entries": 3,
  "profile_refresh_interval_entries": 5,
  "profile_max_entries": 20
}
```

---

## 自动化测试脚本示例

### 单元测试

```python
# tests/test_profile.py
import pytest
import json
import time
from pathlib import Path
from mini_agent.profile import UserProfile, UserProfileManager
from mini_agent.storage.paths import AgentPaths

def test_user_profile_serialization(tmp_path):
    """测试 UserProfile 的序列化和反序列化"""
    paths = AgentPaths(project_root=tmp_path)
    mgr = UserProfileManager(paths)

    profile = mgr.load()
    profile.derived = {
        "summary": "测试用户",
        "tech_stack": ["Python"],
        "habits": ["喜欢简洁的代码"],
        "source_entry_count": 10,
        "updated_at": time.time()
    }
    mgr.save()

    # 重新加载
    mgr2 = UserProfileManager(paths)
    profile2 = mgr2.load()

    assert profile2.derived["summary"] == "测试用户"
    assert "Python" in profile2.derived["tech_stack"]

def test_preferences_not_overwritten(tmp_path):
    """测试用户偏好不会被自动覆盖"""
    paths = AgentPaths(project_root=tmp_path)
    mgr = UserProfileManager(paths)

    profile = mgr.load()
    profile.preferences["language"] = "中文"
    profile.preferences["tone"] = "简洁"
    mgr.save()

    # 模拟画像刷新（只更新 derived）
    profile.derived = {
        "summary": "新画像",
        "tech_stack": ["Rust"],
        "habits": ["喜欢函数式编程"],
        "source_entry_count": 20,
        "updated_at": time.time()
    }
    mgr.save()

    # 验证偏好仍然存在
    profile2 = mgr.load()
    assert profile2.preferences.get("language") == "中文"
    assert profile2.preferences.get("tone") == "简洁"

def test_should_refresh_logic(tmp_path):
    """测试画像刷新判断逻辑"""
    paths = AgentPaths(project_root=tmp_path)
    mgr = UserProfileManager(paths)

    class MockCfg:
        profile_min_entries = 10
        profile_refresh_interval_entries = 20

    cfg = MockCfg()

    # 记忆条目不足
    assert not mgr.should_refresh(5, cfg)

    # 首次达到最小条目
    assert mgr.should_refresh(10, cfg)  # is_new = True

    # 模拟已有画像
    profile = mgr.load()
    profile.derived = {
        "source_entry_count": 10,
        "updated_at": time.time()
    }
    mgr.save()

    # 新增条目不足
    assert not mgr.should_refresh(15, cfg)  # 只增加了 5 条

    # 新增条目足够
    assert mgr.should_refresh(30, cfg)  # 增加了 20 条
```

---

## 故障排查

1. **画像未生成**：
   - 检查记忆条目数是否达到 `profile_min_entries`
   - 确认 `memory_enabled = true`
   - 查看 debug 日志中的 LLM 调用

2. **画像内容不准确**：
   - 检查记忆条目的质量（`summary` 字段是否清晰）
   - 增加 `profile_max_entries` 获取更多记忆
   - 调整 `profile_summarizer.md` 的 prompt

3. **画像文件写入失败**：
   - 确保 `~/.agent/` 目录有写入权限
   - 检查磁盘空间

4. **LLM 返回格式错误**：
   - 检查 `profile_summarizer.md` 的 system prompt
   - 确认模型支持 JSON 输出

---

## 相关文件

- 核心代码：`src/mini_agent/profile.py`
- 画像注入：`src/mini_agent/prompts/system/user_profile.md`
- 画像生成 prompt：`src/mini_agent/prompts/system/profile_summarizer.md`
- 画像更新 prompt：`src/mini_agent/prompts/user/profile_update_request.md`
- 记忆存储：`src/mini_agent/perception/memory_store.py`

---

## 与 Memory 功能的集成测试

由于画像系统依赖记忆系统，建议与 [memory_test.md](memory_test.md) 结合测试：

1. 先运行 Memory 测试场景，生成足够的记忆条目
2. 在此基础上验证画像生成
3. 验证画像注入对新 session 的影响

---

*最后更新：2026-06（新增用户画像系统测试案例）*
