# 权限系统指南

本文档详细说明了 mini-agent 的权限系统，包括权限检查、白名单机制、持久化配置等。

## 核心组件

`src/mini_agent/permissions.py` 实现了权限守卫 `PermissionGuard`，控制工具调用是否需要显式授权。

## 工具分类

### 安全工具 (SAFE_TOOLS)

以下工具无需审批，直接执行：

- `read_file`, `list_dir`, `glob`, `grep` — 只读文件操作
- `web_search` — 网络搜索
- `create_plan`, `add_task`, `start_task`, `complete_task`, `fail_task`, `get_plan_status`, `clear_plan` — 计划管理

### 风险工具 (RISKY_TOOLS)

以下工具默认需要审批：

- `bash` — 执行 shell 命令
- `write_file`, `create_file`, `patch_file`, `delete_file` — 文件写入操作

## 权限决策流程

```
工具调用请求
    │
    ▼
┌─────────────────┐
│ 是否在黑名单？   │ ─── 是 ──→ 拒绝 (SESSION_DENIED)
└────────┬────────┘
         │ 否
    ┌────▼────┐
    │沙箱模式？│ ─── 是且为风险工具 ──→ 阻断 (SANDBOX_BLOCKED)
    └────┬────┘
         │ 否
    ┌────▼──────────┐
    │ 是 `git push`？ │ ─── 是 ──→ 见下方「git push 单独管控」，
    └────┬──────────┘        不进入下面几步（不受 auto_approve/
         │ 否                 白名单短路影响）
    ┌────▼─────┐
    │ 安全工具？│ ─── 是 ──→ 允许
    └────┬─────┘
         │ 否
    ┌────▼──────┐
    │auto_approve│ ─── 是 ──→ 允许
    └────┬──────┘
         │ 否
    ┌────▼────────┐
    │ 在白名单？   │ ─── 是 ──→ 允许
    └────┬────────┘
         │ 否
    ┌────▼────────┐
    │ 危险命令？   │ ──→ 标记警告，继续询问
    └────┬────────┘
         │
    ┌────▼────┐
    │ 用户交互 │ ──→ 等待用户选择
    └─────────┘
```

## `git push` 单独管控（2026-08-26 新增）

`git push` 会把本地历史真的推到远端——一旦推错，不是"重来一次"能
解决的（尤其是共享分支/触发 CI 的场景），风险量级和普通 bash 命令
不对等，所以单独判断，判断时机在**沙箱检查之后、`auto_approve`/
安全工具/白名单短路判断之前**（`is_git_push_command()` 识别 `git
push`/`git -C <path> push`/带 `--force` 等常见写法）：

- **`auto_approve=True` 或 headless 模式**（daemon/cron 例行维护、
  无交互终端——没有人能在场审批）→ **一律拒绝**，不发起任何审批
  交互，只打印拦截提示（`permission_labels.md` 的
  `GIT_PUSH_BLOCKED_AUTO` 片段）。这条规则不看白名单、不看
  `auto_approve` 本身——就是要保证"没有用户在场明确指示"的场景下
  agent 绝对不会自行 push。
- **交互场景**（有真人在场的会话）→ 强制走一次人工确认
  （`_prompt`/`_prompt_with_http`，标记为危险操作），且**不检查
  `_is_allowed()` 白名单**——即使用户之前对 `bash` 开过"全部放行"，
  push 仍然每次都要单独确认。用户在确认弹窗里选择允许，就是本次
  push 的"明确指令"。
- 只拦截 `git push` 本身，不影响 `git commit`/`git pull`/`git
  fetch` 等其他 git 子命令——那些仍然走上面的普通决策流程。

背景与本次问题排查过程见
[next_doc/agent_commit_undo_guard_plan.md](../next_doc/agent_commit_undo_guard_plan.md)
变更记录；这个能力和 `agent_commit_guard`（管"要不要提交"）是两回事，
只是恰好都属于"daemon 自动化场景下 agent 自主 git 操作的治理"这个
主题，参见 [agent commit guard 指南](agent-commit-guard-guide.md)。

## 用户交互选项

当工具需要审批时，用户可以选择：

| 选项 | 含义 |
|------|------|
| `y` / `yes` | 本次批准 |
| `a` / `always` | 本次批准并加入白名单 |
| `n` / `no` | 本次拒绝 |
| `d` / `deny-always` | 拒绝并加入黑名单（当前 session 内持续生效） |
| `e` / `edit` | 修改命令后批准（仅 `bash` 工具） |
| `s` / `show` | 显示完整参数后重新选择 |

### `(e)dit` 与 Lesson Memory 的接入（2026-06，Stage 1.5）

用户选择 `(e)dit` 修改命令/参数后批准，这个动作本身就是一条**高质量的人类反馈**——
用户主动纠正了 agent 提议的操作。`PermissionGuard` 检测到编辑发生后会记录到
`last_edit` 属性（`{"tool_name", "original", "edited"}`），调用方（`tool_executor.py`）
在 `check()` 返回后立即查询并消费它：

1. 把编辑前后内容追加为一条 `_type="user_correction"` 的 history 消息
   （详见 [history 类型化设计](history-typed-design.md#3-类型枚举htype)），
   让 LLM 在后续对话中能看到用户做了什么修改
2. 同时生成一条 `entry_type="lesson"`、`source="human_feedback"`、
   `confidence=0.85` 的记忆条目（详见 [记忆管理指南](memory-management-guide.md#lesson-memory)）

这个机制覆盖三处 `(e)dit` 触发点：CLI 简单审批、HTTP 双路审批的 CLI 端、HTTP 端
（HTTP 端编辑可能涉及任意字段，不限于 bash 的 `command`）。检测逻辑实现在
`PermissionGuard.pop_last_edit()`，每次编辑事件只被消费一次。

## 白名单机制

### 路径规范化

为了防止路径格式不一致导致的匹配失败，系统会自动规范化路径：

- `./test_result/` 和 `test_result/` 被视为相同路径
- 规范化方法：去掉路径开头的 `./` 前缀（可能多个）

示例：
```python
_normalize_path("./test_result/file.md")  # → "test_result/file.md"
_normalize_path("test_result/file.md")    # → "test_result/file.md"
```

### 白名单条目格式

白名单按 `tool_name` + `path_prefix` 精细化管理：

```json
{
  "allow_list": [
    {
      "tool_name": "create_file",
      "path_prefix": "test_result/"
    },
    {
      "tool_name": "bash",
      "path_prefix": ""
    }
  ],
  "denied_tools": []
}
```

- `path_prefix` 为空字符串表示对该工具的所有调用放行
- 文件路径匹配使用前缀匹配，`test_result/` 会匹配 `test_result/file.md`

### 白名单自动添加

当用户选择 `always` 时：

1. 提取文件路径（如 `./test_result/file.md`）
2. 计算父目录作为前缀（如 `test_result/`）
3. 添加到白名单并持久化

## 权限持久化

### 配置文件位置

权限配置保存在工作目录下的 `agent_permissions.json`：

```bash
cwd/agent_permissions.json
```

### 加载时机

`PermissionGuard` 在 `__post_init__` 时自动加载持久化配置：

```python
def __post_init__(self) -> None:
    """构造完成后自动从配置文件加载持久化权限。"""
    self._load_permissions()
```

### 保存时机

以下操作会触发权限保存：

1. 添加白名单条目（用户选择 `always`）
2. 添加黑名单条目（用户选择 `deny-always`）

保存路径：

```python
def _save_permissions(self) -> None:
    """将当前 allow/deny 列表持久化到工作目录的 agent_permissions.json。"""
```

## 沙箱模式

当 `sandbox=True` 时，所有风险工具都会被阻断，无需用户确认。

标签输出：

```
🏖️  Sandbox mode — {tool_name} was blocked
  Would have executed: {summary}
```

## 危险命令检测

以下 shell 命令模式会被标记为危险：

| 模式 | 危险命令示例 |
|------|-------------|
| `rm -rf` | `rm -rf /` |
| `dd` | `dd if=/dev/zero` |
| `mkfs` | `mkfs.ext4 /dev/sda` |
| `> /dev/` | `> /dev/null` |
| `sudo` | `sudo apt install` |
| `curl | bash` | `curl ... \| sh` |
| `chmod 777` | `chmod 777 /` |

## 代码示例

### 创建 PermissionGuard

```python
from mini_agent.permissions import PermissionGuard

guard = PermissionGuard(
    auto_approve=False,
    sandbox=False,
    project_root=Path.cwd()
)
```

### 检查权限

```python
if guard.check("create_file", {"path": "./test_result/file.md", "content": "..."}):
    # 允许执行
    pass
else:
    # 拒绝执行
    pass
```

## 相关代码

- [`permissions.py`](../src/mini_agent/permissions.py) — 权限守卫核心实现
- [`permission_labels.md`](../src/mini_agent/prompts/fragments/permission_labels.md) — 交互界面文本片段
- [`perception/correction_detector.py`](../src/mini_agent/perception/correction_detector.py) — `(e)dit` 编辑内容转 lesson 的字段生成逻辑

## 更新日志

### 2026-08-26

- 新增 `is_git_push_command()` + `PermissionGuard.check()` 专门分支：
  `git push` 在 auto_approve/headless（daemon 例行维护、无交互终端）
  场景下一律拒绝，不再像普通 bash 命令一样被无声放行；交互场景强制
  走一次人工确认，不受白名单短路影响。详见「git push 单独管控」一节。
- `permission_labels.md` 新增 `GIT_PUSH_BLOCKED_AUTO` 文本片段。
- 新增回归测试 `tests/test_git_push_guard.py`（7 条）。

### 2026-06（Stage 1.5）

- `PermissionGuard` 新增 `last_edit` / `pop_last_edit()` / `_edit_repr()`，三处 `(e)dit` 分支统一记录编辑事件
- `(e)dit` 编辑现在会同时产生 `_type="user_correction"` history 消息和 `source="human_feedback"` 记忆条目

### 2026-06-06

- 添加路径规范化功能，解决 `./test_result/` 和 `test_result/` 无法匹配的问题
- 引入 `_normalize_path()` 函数，统一去掉 `./` 前缀