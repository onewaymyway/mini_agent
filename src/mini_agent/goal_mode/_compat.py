"""
goal_mode/_compat.py — 兼容层

`history/entry.py` 里的 `HType.GOAL_CONTEXT` / `make_goal_context()` 是
Goal 模式所需的"钉住"消息类型。如果项目里的 `history/entry.py` 还没有
这两个定义（比如只应用了 goal_mode 的核心代码，没有同步更新 entry.py），
直接 `from mini_agent.history.entry import make_goal_context` 会
`ImportError` 而不是优雅降级。

这里提供一个降级实现：优先使用 `history/entry.py` 里的正式版本（如果存在），
否则退回到一个行为等价的本地实现（`_type` 用同样的字符串值
`"goal_context"`，只是不经过 `HType` 枚举）。降级模式下唯一的差异是：
`history/compression.py` 里针对 `HType.GOAL_CONTEXT` 的权重保留规则不会生效
（因为压缩策略按枚举值识别类型），但 GoalRunner 自身"每轮结束后无条件
重新钉一次"的兜底机制不受影响，目标信息依然不会丢失。

建议：尽快把 `history/entry.py` 补上正式定义（见 docs/goal-mode-guide.md
"目标上下文的钉住机制"一节），以获得压缩策略层面的额外保护。
"""

from __future__ import annotations


def make_goal_context(content: str) -> dict:
    """构造一条 goal_context 类型的"钉住"消息。

    优先复用 `history/entry.py` 的正式实现；不存在时使用本地降级实现。
    """
    try:
        from mini_agent.history.entry import make_goal_context as _real_make_goal_context
        return _real_make_goal_context(content)
    except ImportError:
        return {"role": "user", "content": content, "_type": "goal_context"}
