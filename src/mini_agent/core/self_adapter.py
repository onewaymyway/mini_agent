"""core/self_adapter.py — SelfAdapter：实现 `Adapter[Old, New]` 协议。

Old = `mini_agent.perception.self_model.AgentSelfModel`
New = `mini_agent.core.self.SelfState`

按 `03-sprint1.5-memory-perception-coupling-assessment.md` 的迁移优先级
建议，本模块**只**在 `agent/lifecycle.py` 里 `AgentSelfModelBuilder().build()`
之后的一个调用点被使用（唯一接入点，与 Sprint 1 Goal 迁移链的模式一致），
不改动 `perception/self_model.py` 内部逻辑，也不在其它任何地方直接
互相构造 `AgentSelfModel`/`SelfState`。

只做 `capability_snapshot`/`active_skill_count`/`session_start_at`
三个字段的单向转换（`AgentSelfModel → SelfState`），`to_old` 方向
在 Self 迁移链当前阶段还没有实际调用方需要（`AgentSelfModel` 是
`perception/` 内部对象，创建它需要 `AffordanceMap`/`AgentInternalState`
等尚未定义 core 版本的类型），先只实现 `to_new`，`to_old` 抛出
`NotImplementedError` 并注明原因，不假装支持一个还没有被任何代码路径
用到、也没被测试验证过的方向（呼应"不留没有说明的空实现"的文档写作
规范）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapter import Adapter
from .self import SelfState

if TYPE_CHECKING:
    from mini_agent.perception.self_model import AgentSelfModel


class SelfAdapter(Adapter["AgentSelfModel", SelfState]):
    """`AgentSelfModel` -> `SelfState` 的转换（当前只支持这一个方向）。"""

    @staticmethod
    def to_new(old: "AgentSelfModel") -> SelfState:
        return SelfState(
            capability_snapshot=dict(old.capability_snapshot),
            active_skill_count=old.active_skill_count,
            session_start_at=old.session_start_at,
        )

    @staticmethod
    def to_old(new: SelfState) -> "AgentSelfModel":
        # TODO(Self 迁移链下一步)：一旦需要从 SelfState 反向构造
        # AgentSelfModel（例如未来某个新调用方只持有 core.SelfState，
        # 需要喂给仍然依赖 AgentSelfModel 的旧代码），再实现这个方向，
        # 并补充对应的往返转换测试。当前没有任何调用方需要它。
        raise NotImplementedError(
            "SelfAdapter.to_old 尚未实现：当前 Self 迁移链只有 "
            "AgentSelfModel → SelfState 单向转换的实际调用方，"
            "见 core/self_adapter.py 模块文档字符串。"
        )
