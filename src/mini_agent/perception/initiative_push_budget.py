"""perception/initiative_push_budget.py — 跨系统"今日主动推送预算"总闸。

[next_doc/initiative_systems_unification_plan.md §4.6 / 阶段二]

背景：`growth_advisor`（`notification_max_per_day`）、`capability_learning`
（同名字段，独立配置、独立状态文件）此前各自按天节流，互不感知对方；
`watchlist` 通知节流又是另一套。用户当天实际收到的主动消息条数是三者
简单叠加，没有一个"今天总共该给用户推几条"的总闸。

设计取舍（对齐方案 §5/§6"不重写已经跑通的核心算法逻辑"的一贯风格）：

- **叠加式第二层节流，不替代任何一方现有实现**：`growth_advisor`/
  `capability_learning` 各自的 `notification_max_per_day` +
  `notification_frequency` 判断逻辑完全不变，本模块只是在"确定要发"
  之后、真正调用 `NotificationDispatcher.dispatch()` 之前，再多问一句
  "跨系统预算还有名额吗"。任何一方原有的行为在
  `initiative_push_budget_enabled=False`（默认值）时与改动前完全一致。
- **默认关闭**：`AppConfig.initiative_push_budget_enabled` 默认
  `False`，不影响存量部署/测试；用户在 `agent_config.json` 里显式打开
  后才生效，符合本仓库"改动用户可感知行为需要显式开启"的一贯取向。
- **按 `source` 记账，但共享同一个每日总额**：`spent_by_source` 只用于
  可观测性（调试/看板展示"今天各来源各占用了几条"），预算判定只看
  `spent_today` 这个总数是否达到 `max_per_day`，不做按来源的子配额
  切分——子配额切分（按 confidence/urgency 抢占）留给未来需要时再加，
  当前先用最简单的"先到先得、共享同一个池子"跑通总闸机制本身。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


def _today_str() -> str:
    """本地时区自然日字符串，风格对齐 growth_advisor.py/capability_learning.py
    各自的 `_today_str()`——故意不导入复用，三份节流状态本来就要各自独立
    判定"今天"，避免耦合。"""
    return time.strftime("%Y-%m-%d", time.localtime())


def _read_state(paths: "AgentPaths") -> dict:
    """独立实现一份最小 json 读写（不依赖 growth_advisor.py/
    capability_learning.py 内部的私有辅助函数），避免本模块跟那两个模块
    产生不必要的 import 耦合——本模块的定位是被它们依赖，而不是反过来
    依赖它们的实现细节。"""
    p = paths.initiative_push_budget_path
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(paths: "AgentPaths", state: dict) -> None:
    p = paths.initiative_push_budget_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_reset_if_new_day(paths: "AgentPaths") -> dict:
    state = _read_state(paths)
    today = _today_str()
    if state.get("date") != today:
        state = {"date": today, "spent_today": 0, "spent_by_source": {}}
    return state


def remaining_budget(paths: "AgentPaths", max_per_day: int) -> int:
    """只读查询：今天跨系统共享预算还剩多少名额，不消耗、不落盘。

    调用方在展示"Agent 今天还能推几条"这类只读信息（比如看板）时使用，
    与 `try_consume()` 的"消耗一个名额"是两个独立动作，互不影响彼此的
    正确性——多次调用本函数不会误耗预算。
    """
    try:
        state = _load_reset_if_new_day(paths)
        spent = int(state.get("spent_today", 0) or 0)
        return max(0, int(max_per_day) - spent)
    except Exception:
        # 状态文件读取异常时保守地"当作还有预算"，不应该因为一个可观测性
        # 查询失败就误伤正常推送——真正的节流判断以 try_consume() 为准。
        return max(0, int(max_per_day))


def try_consume(paths: "AgentPaths", source: str, max_per_day: int) -> bool:
    """尝试为 `source`（如 "growth_advisor"/"capability_learning"/
    "watchlist"）消耗一个跨系统推送名额。

    返回 `True` 表示预算充足、已扣减、调用方可以继续走原有的
    `NotificationDispatcher.dispatch()`；返回 `False` 表示今天的共享
    预算已耗尽，调用方应该放弃本次推送（等价于被各自原有节流拦下的
    效果，只是拦截来源变成了跨系统总闸）。

    `max_per_day <= 0` 视为"关闭跨系统总闸但仍记账"是没有意义的用法，
    这里直接按"预算为 0"处理，一律返回 `False`——真正的开关应该用
    `AppConfig.initiative_push_budget_enabled=False`，不建议通过把
    `max_per_day` 设成 0 来"软关闭"。

    任何读写异常都不应该打断调用方的推送主流程：异常时保守地放行
    （返回 `True`，等同于总闸暂时失效但不额外拦截）——跨系统总闸是叠加
    的第二层节流，它自身故障不应该导致本该发出的通知因此丢失。
    """
    try:
        state = _load_reset_if_new_day(paths)
        spent = int(state.get("spent_today", 0) or 0)
        if spent >= int(max_per_day):
            return False
        state["spent_today"] = spent + 1
        by_source = state.setdefault("spent_by_source", {})
        by_source[source] = int(by_source.get(source, 0) or 0) + 1
        _write_state(paths, state)
        return True
    except Exception:
        from mini_agent.errors import log_exception
        log_exception(
            RuntimeError("initiative_push_budget try_consume failed"),
            where="mini_agent.perception.initiative_push_budget.try_consume",
        )
        return True


def _read_agent_config_flags(paths: "AgentPaths") -> tuple[bool, int]:
    """轻量直接读取 `<project_root>/agent_config.json` 里的两个开关，
    不走完整的 `config.loader.load_config()`（那条路径会顺带处理 CLI
    参数覆盖/环境变量兜底/providers.json 合并等一整套逻辑，对"只是想知道
    这两个 bool/int 字段"的场景太重，也不是这里需要的语义——推送节流
    这个场景只关心项目配置文件里写了什么，不关心 CLI 覆盖）。

    文件不存在/解析失败/字段缺失时一律返回默认值 `(False, 3)`，与
    `AppConfig` 的 dataclass 默认值保持一致。
    """
    try:
        cfg_path = paths.project_root / "agent_config.json"
        if not cfg_path.exists():
            return False, 3
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False, 3
        enabled = bool(data.get("initiative_push_budget_enabled", False))
        max_per_day = int(data.get("initiative_push_budget_max_per_day", 3) or 3)
        return enabled, max_per_day
    except Exception:
        return False, 3


def check_and_consume_for_project(paths: "AgentPaths", source: str) -> bool:
    """便捷封装：调用方（`growth_advisor.py`/`capability_learning.py` 的
    推送节流函数）通常手上只有 `AgentPaths`，拿不到已加载好的
    `AppConfig`——这里直接从项目的 `agent_config.json` 读两个开关字段，
    语义等价于 `check_and_consume(paths, app_cfg, source)`。"""
    enabled, max_per_day = _read_agent_config_flags(paths)
    if not enabled:
        return True
    return try_consume(paths, source, max_per_day)


def check_and_consume(paths: "AgentPaths", cfg, source: str) -> bool:
    """便捷封装：先看 `cfg.initiative_push_budget_enabled` 是否开启——
    关闭（默认）时直接放行（`True`），不读写任何状态文件，等价于本模块
    完全不存在，保证默认行为与改动前一致；开启时才真正调用
    `try_consume()`。

    `cfg` 预期是 `AppConfig`（或任意有这两个同名属性的对象/`None`）；
    拿不到属性时按"关闭"处理，容错方式对齐仓库里其它调用方的
    `getattr(..., default)` 惯例。
    """
    enabled = bool(getattr(cfg, "initiative_push_budget_enabled", False))
    if not enabled:
        return True
    max_per_day = int(getattr(cfg, "initiative_push_budget_max_per_day", 3) or 3)
    return try_consume(paths, source, max_per_day)
