# 单元测试指南

本文档介绍 mini-agent 项目的单元测试机制，包括测试结构、编写规范和运行方式。

## 测试目录结构

```
tests/
├── test_concurrency.py          # 并发控制层测试
├── test_llm.py                  # LLM 客户端测试
├── test_nvidia.py               # NVIDIA API 测试
├── test_orchestrator.py         # 编排器测试
├── test_prompts.py              # Prompt 管理测试
├── test_retry.py                # 重试策略测试
├── test_session.py              # 会话管理测试
├── test_skill_cli.py            # Skill CLI 命令测试
├── test_skill_compact.py        # Skill 压缩测试
├── test_skill_manager.py        # Skill 管理工具测试
├── test_skill_usage_detector.py # Skill 使用检测器测试
├── test_system_tool_call_and_debug.py  # 系统工具调用测试
├── test_undo.py                 # 撤销功能测试
└── test_nvida.py                # NVIDIA 相关测试
```

## 测试框架

项目使用 `pytest` 作为测试框架，部分测试使用 `unittest` 风格。

### 依赖安装

```bash
pip install pytest
```

## 测试编写规范

### 1. 文件头部注释

每个测试文件应在开头包含说明注释，描述测试覆盖的功能点：

```python
"""
tests/test_skill_manager.py — Skill 动态管理工具测试

覆盖：
  - SkillLoader.get_catalog()       — 目录格式正确
  - SkillLoader.get_active_catalog() — 仅含激活项
  - skill_list 工具                 — 返回完整目录 JSON
  - skill_activate 工具             — 激活成功/已激活/不存在 三种路径
"""
```

### 2. 路径配置

测试文件需要正确设置 `sys.path` 以导入项目模块：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
```

### 3. 测试类组织

使用 pytest 的 class 组织测试，每个类负责一个功能模块：

```python
class TestRetryPolicy:
    def setup_method(self):
        # 每个测试前的准备工作
        self.policy = default_retry_policy(max_retries=3)

    def test_retries_on_empty_output(self):
        # 测试用例
        pass
```

### 4. Mock 使用模式

对于外部依赖（如 LLM API、文件系统），使用 Mock 进行测试：

```python
from unittest.mock import MagicMock, patch

# Mock 工厂函数
def make_loader(skill_defs: list[dict]) -> SkillLoader:
    """构造一个不依赖文件系统的 SkillLoader。"""
    loader = SkillLoader.__new__(SkillLoader)
    loader._dirs = []
    loader._all = {}
    # ... 构造最小可用对象
    return loader

# 使用 patch 装饰器
class TestSkillListTool:
    def test_returns_all_skills(self):
        with patch("mini_agent.ui.renderer.print_skill_loaded"):
            result = self._call()
        assert len(result["skills"]) == 2
```

### 5. 边界条件测试

应覆盖正常路径、错误路径和边界情况：

```python
class TestSkillActivateTool:
    def test_activate_already_active(self):
        """测试已经激活的技能。"""
        self.loader.activate("docx")
        result = self._call(["docx"])
        assert result["results"][0]["status"] == "already_active"

    def test_activate_nonexistent_skill(self):
        """测试不存在的技能。"""
        result = self._call(["nonexistent"])
        assert result["results"][0]["status"] == "not_found"
```

## 并发测试

对于涉及多线程的测试，需要使用线程同步原语：

```python
import threading
import time

class TestConcurrentLimit:
    def test_never_exceeds_limit(self):
        sem = CountingSemaphore(limit=2, kind="test")
        max_observed = [0]
        lock = threading.Lock()

        def worker():
            with sem.acquire("w"):
                current = sem.active_count
                with lock:
                    if current > max_observed[0]:
                        max_observed[0] = current
                time.sleep(0.01)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)

        assert max_observed[0] <= 2  # 验证并发限制未突破
```

## 运行测试

### 运行单个测试文件

```bash
pytest tests/test_retry.py -v
```

### 运行单个测试用例

```bash
pytest tests/test_retry.py::TestRetryPolicy::test_retries_on_empty_output -v
```

### 运行所有测试

```bash
pytest tests/ -v
```

### 带覆盖率运行

```bash
pytest tests/ --cov=mini_agent --cov-report=html
```

### 常用参数

| 参数 | 说明 |
|------|------|
| `-v` | 详细输出 |
| `-x` | 失败即停止 |
| `-k <expr>` | 按名称过滤测试 |
| `--tb=short` | 简化追踪信息 |
| `--cov=MODULE` | 生成覆盖率报告 |

## 测试最佳实践

### 1. 独立性

每个测试应该独立运行，不依赖其他测试的状态：

```python
class TestCounter:
    def setup_method(self):
        # 每个测试前重置状态
        self.counter = Counter()

    def test_increment(self):
        self.counter.increment()
        assert self.counter.value == 1

    def test_reset(self):
        # 不依赖 test_increment 的结果
        self.counter.reset()
        assert self.counter.value == 0
```

### 2. 测试命名

使用描述性的测试名称，说明测试的场景：

```python
# 好
def test_activate_nonexistent_skill_returns_not_found(self):
def test_concurrent_calls_exceeds_limit_blocks_others(self):

# 避免
def test_activate_1(self):
def test_case_3(self):
```

### 3. 断言明确

使用清晰的断言，必要时添加说明：

```python
# 好
assert result["status"] == "activated", "Expected skill to be activated"

# 简洁
def make_response(text="", tool_calls=None, stop_reason="end_turn"):
    """构造最小 LLMResponse 用于测试。"""
```

### 4. 集成测试

对于涉及多个组件交互的场景，编写集成测试：

```python
class TestSystemPromptSkillCatalog:
    """验证 _build_system 正确注入技能目录。"""

    def test_active_skills_shown_as_active(self):
        agent = self._make_agent_with_skills([{"name": "docx"}])
        agent.skill_loader.activate("docx")

        with patch("mini_agent.config.build_system_prompt", return_value="BASE"):
            result = agent._build_system()

        assert "Currently active" in result
        assert "docx" in result
```

## 添加新测试

当添加新功能时，应同时添加对应的测试：

1. 在 `tests/` 目录下创建新测试文件或添加到现有文件
2. 遵循上述规范编写测试
3. 确保测试能够独立运行
4. 运行测试验证通过

```bash
# 运行新添加的测试
pytest tests/ -v -k <new_feature>
```

## 常见问题

### Q: 测试导入失败

确保 `sys.path` 正确设置：

```python
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
```

### Q: 并发测试不稳定

增加超时时间和适当的 `time.sleep()` 确保同步点：

```python
t.join(timeout=10)  # 足够长的超时
time.sleep(0.05)    # 给线程启动时间
```

### Q: Mock 对象行为不符合预期

使用 `side_effect` 模拟复杂行为：

```python
mock_fn = MagicMock(side_effect=[value1, value2, Exception()])
```
