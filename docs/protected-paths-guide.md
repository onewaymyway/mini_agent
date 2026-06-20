# 受保护路径清单（T3 治理红线）

> 说明 `scripts/protected_paths.py` 的设计动机、使用方式与扩展规则。
> 对应 `next_doc/self_evolution_implementation_plan.md` Stage 0.1。

---

## 1. 这是什么

`scripts/protected_paths.py` 是一份**独立于业务代码**的文件路径清单，标记出"无论自我演化机制多么自动化，都不允许 agent 自主改动"的安全关键文件。

它是 mini_agent 自我演化能力（让 agent 能够分析、提议、甚至直接修改自己的代码）的**治理红线**之一。在自我演化语境下，对改动的风险通常分级（T1/T2/T3，等级越高代表越需要人工把关），而本清单标记的文件**始终强制判定为 T3**——不论调用方传入了什么 tier，命中清单即升级。

---

## 2. 为什么不放在 `src/mini_agent/` 包内

这是本文件最重要的设计决策，值得单独说明：

如果"受保护路径清单"本身也是 agent 在自我演化过程中可以自由改动的代码，那么它作为安全红线就毫无意义——agent 理论上可以先悄悄把清单改空，再去改真正受保护的文件，整个机制形同虚设。

因此 `scripts/protected_paths.py`：

1. **不放在 `src/mini_agent/` 包内**，避免被当作"普通源码"卷入批量重构、格式化、或者被自我演化流程当作可改动目标
2. **不 import 任何 `mini_agent.*` 模块**，保持判定逻辑自包含——即使 `mini_agent` 包本身在演化中被改坏（语法错误、循环 import），这份红线依然能独立工作
3. **本文件自身也在清单中**（见下方"防止绕过"），防止"先放宽清单、再改受保护文件"这种两步绕过

---

## 3. 当前覆盖范围

```python
PROTECTED_PATHS = (
    "src/mini_agent/agent.py",        # agentic loop 主循环
    "src/mini_agent/permissions.py",  # 权限/审批门控
    "src/mini_agent/hooks/",          # 生命周期钩子（整个目录）
    "scripts/protected_paths.py",     # 清单自身
)

PROTECTED_PATTERNS = (
    r"src/mini_agent/evolution/.*",   # 预留：Stage 2 的 StateRepo 所在目录
)
```

| 条目 | 类型 | 为什么受保护 |
|------|------|-------------|
| `agent.py` | 精确文件 | agentic loop 主循环，是整个系统行为的核心 |
| `permissions.py` | 精确文件 | 权限/审批门控，是安全机制本身 |
| `hooks/` | 目录（含子文件） | 生命周期钩子加载与执行，决定"什么时候会运行额外代码" |
| `scripts/protected_paths.py` | 精确文件 | 清单自身，防止绕过 |
| `src/mini_agent/evolution/.*` | 正则 | Stage 2 将在这里新建 `StateRepo`，提前画好红线，避免它"改没了自己" |

> `evolution/` 目前还不存在，这条规则是**提前生效**的——等 Stage 2 落地后，新增的 `StateRepo` 等核心文件天然就在保护范围内，不需要再补一次清单。

---

## 4. 使用方式

```python
from scripts.protected_paths import PROTECTED_PATHS, is_protected_path

if is_protected_path("src/mini_agent/agent.py"):
    tier = "T3"  # 强制升级，即使调用方原本传入了 T1/T2
```

`is_protected_path()` 支持三种路径形式：

- **精确文件路径**：完全相等才命中
- **目录路径**（清单里以 `/` 结尾）：传入路径以该目录为前缀即命中，目录下任意子文件都受保护
- **正则模式**：对 `PROTECTED_PATTERNS` 中的规则做 `fullmatch`

输入可以是字符串或 `Path` 对象，相对路径（推荐）或带 `./` 前缀都会被正确归一化；绝对路径不保证准确匹配，调用方应尽量传入**相对仓库根目录**的路径。

```python
is_protected_path("src/mini_agent/agent.py")              # True（精确匹配）
is_protected_path("./src/mini_agent/agent.py")             # True（自动去掉 ./ 前缀）
is_protected_path("src/mini_agent/hooks/loader.py")        # True（目录前缀匹配）
is_protected_path("src/mini_agent/evolution/state_repo.py")# True（正则匹配，预留规则）
is_protected_path("src/mini_agent/tools/builtin.py")       # False
```

其他辅助函数：

```python
from scripts.protected_paths import list_protected_paths

list_protected_paths()  # 返回当前生效的全部静态受保护路径（不含正则规则），供 CLI/审计展示
```

---

## 5. 未来接入点：Stage 2 StateRepo 的 T3 强制判定

按照 `self_evolution_implementation_plan.md` 的规划，Stage 2 会落地一个 `StateRepo`（状态仓库），负责管理自我演化提案的生命周期（提议 → 审批 → 应用 → 回滚）。`StateRepo` 在判定某个改动提案的风险等级时，会直接 `import scripts.protected_paths`：

```python
# Stage 2 设想中的伪代码
from scripts.protected_paths import is_protected_path

def determine_tier(proposed_change, declared_tier):
    if any(is_protected_path(f) for f in proposed_change.touched_files):
        return "T3"  # 强制覆盖，不接受声明的更低 tier
    return declared_tier
```

这也是为什么本文件强调"判定逻辑本身要在 agent 可写范围之外"——它不是一份文档约定，而是会被实际代码 import 并强制执行的运行时红线。

---

## 6. 扩展规则

随着系统演进，如果出现新的安全关键模块，应当：

1. 追加到 `PROTECTED_PATHS`（精确文件/目录）或 `PROTECTED_PATTERNS`（正则）
2. **不要**新建另一份清单——所有受保护路径应该汇聚在这一个文件里，否则会重新引入"判定逻辑分散、容易被绕过"的问题
3. 修改本文件时，请同步更新 `tests/test_protected_paths.py` 中的对应断言

判断一个文件是否应该加入清单，可以问自己：**"如果 agent 在自我演化中把这个文件改坏了，安全机制本身会不会随之失效？"** 如果答案是"会"，它就应该进清单。

---

## 7. 测试

```bash
pytest tests/test_protected_paths.py -v
```

测试覆盖：清单非空、覆盖关键文件/目录、`is_protected_path()` 对精确文件/目录前缀/正则/非受保护路径的判定、`Path` 对象与 `./` 前缀的归一化、以及"清单文件不 import `mini_agent` 包"这一自包含性约束。

---

## 8. 相关文档

- [自我演化实施计划](../next_doc/self_evolution_implementation_plan.md) — Stage 0.1 的完整需求背景
- [自我演化设计文档](../next_doc/self_evolution_design.md) — T1/T2/T3 风险分级的整体设计

---

*创建时间：2026-06（self_evolution_implementation_plan.md Stage 0.1）*
