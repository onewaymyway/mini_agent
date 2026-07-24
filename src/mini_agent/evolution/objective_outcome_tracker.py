"""
evolution/objective_outcome_tracker.py — [看板与自主性改进方案 Track H]
效果回填闭环到目标推导优先级。

对应 next_doc/kanban_and_autonomy_improvement_plan.md Track H（P12）：
`soft_goal_deriver.py` 反复 derive 出同类目标，即便这类目标过去已经失败过
很多次——因为 `ObjectiveExecutor` 的完成/失败结果此前只写 GoalNode.status
（Track B）和 activity_digest（供人看），没有反过来影响 `derive()` 本身的
候选排序/取舍。本模块补上这条闭环。

"同一主题"如何界定（对应方案原文"待确认/待细化项 4"）：
  `soft_goal_deriver.py` 的三路信号（capability_map / work_index /
  lesson_review）在各自的 `_from_*()` 里各有自己的 ID 体系（capability_id /
  WorkThread.id / LessonGroup.key），但这些 ID **不会**跟着候选一起写进
  `GoalBacklog`——`commit_goals()` 只写 `title`/`description`/`source_tag`
  三个字段到 `GoalNode`，原始来源 ID 在写入的那一刻就丢失了。`GoalNode`
  本身也没有预留任何"主题标签"字段。
  与其现在就去改 `GoalNode` schema（涉及看板展示、序列化兼容等一圈改动，
  超出本 Track 范围），本模块选择复用 `soft_goal_deriver._DeriveCandidate
  .dedupe_key()` 已经在用的"标题归一化"作为主题标识——这个函数本来就是
  用来判断"这个候选是不是已经 derive 过的同一个东西"，语义上就是当前代码
  库里"同一主题"的事实标准，`existing_titles`/`rejected_keys` 两处去重都
  依赖它。本模块把这个归一化函数原样搬到这里（`normalize_title_key()`），
  `soft_goal_deriver.py` 改为从这里导入，保证两处主题 key 的计算方式
  永远一致，不会出现"derive 时用一种归一化，回填时用另一种"的偏差。

  局限（据实记录）：Objective 的标题在极少数情况下可能被后续拆解/重命名
  改写过，与最初 derive 出的 Goal 标题不完全一致，这种情况下主题匹配会
  失效（退化为"查不到历史，当作新主题处理"，不会误判，只是少一次参考
  信号）——这是选择"标题归一化"这一相对粗粒度方案的已知代价，换来的是
  不需要改动 GoalNode 持久化 schema。

存储设计：
  - `<workdir>/objective_theme_outcomes.json`，按 theme_key 分桶，每个桶
    只保留最近 `MAX_HISTORY_PER_THEME` 条结果（滚动窗口，避免无限增长）。
  - 与 `outcome_tracker.py`（skill_propose commit 效果回填）是两条独立的
    存储/职责：那个模块回答"这次自我修改有没有解决问题"，本模块回答
    "这类自主执行目标过去完成得顺不顺利"，两者的判定对象不同（commit vs
    Objective 主题），不合并。
  - 失败静默降级：任何读写异常都不应阻断调用方（ObjectiveExecutor 收尾
    回调 / SoftGoalDeriver.derive_candidates()）的主流程。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

_STORE_FILENAME = "objective_theme_outcomes.json"

# 每个主题最多保留的历史结果条数（滚动窗口）。
MAX_HISTORY_PER_THEME = 10
# 参与失败率判定所需的最小样本数（样本太少不下结论，避免小样本噪声）。
MIN_SAMPLES_FOR_JUDGEMENT = 3
# 失败率达到/超过此比例 → 建议本轮 derive 直接跳过该主题。
SKIP_FAILURE_RATIO = 0.66
# 失败率达到/超过此比例（但未到 SKIP 阈值）→ 降权而非跳过。
DOWNWEIGHT_FAILURE_RATIO = 0.34
# 降权时的乘法系数（与 soft_goal_deriver.py 里"高风险域"0.4、"负面回填域"
# 0.15 同一量级，这里取两者之间：确凿的同主题历史失败率比"域名关键词重叠"
# 更可信，但还没到"一定不该再试"的地步，交给 urgency 排序自然靠后即可）。
DOWNWEIGHT_FACTOR = 0.25

_VALID_OUTCOMES = ("completed", "failed")


def normalize_title_key(title: str) -> str:
    """
    与 `soft_goal_deriver._DeriveCandidate.dedupe_key()` 完全一致的归一化
    规则：小写、去标点、按空格切分后排序重连——两处必须永远保持一致，
    因此把实现集中放在这里，`soft_goal_deriver.py` 从此处导入复用。
    """
    s = (title or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(sorted(s.split()))


def _store_path(paths) -> Path:
    return paths.workdir_dir / _STORE_FILENAME


def _load_all(paths) -> dict:
    p = _store_path(paths)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_outcome_tracker._load_all')
        return {}


def _save_all(paths, data: dict) -> None:
    p = _store_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_outcome_tracker._save_all')
        tmp.unlink(missing_ok=True)


def record_outcome(paths, title: str, outcome: str) -> None:
    """
    [接入点] `ObjectiveExecutor._on_objective_completed()` /
    `_on_objective_failed()` 各调用一次。`outcome` 只接受 "completed" /
    "failed" —— "cancelled"（用户主动终止，见 Track D）不代表这个主题
    "做不到"，不计入统计，调用方无需/不应传入 cancelled。

    失败静默：写入失败不应影响 ObjectiveExecutor 收尾流程本身。
    """
    if outcome not in _VALID_OUTCOMES:
        return
    try:
        key = normalize_title_key(title)
        if not key:
            return
        data = _load_all(paths)
        bucket = list(data.get(key, []))
        bucket.append({"outcome": outcome, "at": time.time()})
        bucket = bucket[-MAX_HISTORY_PER_THEME:]
        data[key] = bucket
        _save_all(paths, data)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_outcome_tracker.record_outcome')


def theme_failure_stats(paths, title_or_key: str, *, already_normalized: bool = False) -> Optional[tuple[int, int]]:
    """
    返回 `(总样本数, 失败样本数)`；该主题没有任何历史记录时返回 `None`
    （区别于"有记录但从未失败"，调用方应把 `None` 当作"没有信号，不参与
    判定"处理，而不是"失败率 0"）。

    `already_normalized` — `soft_goal_deriver.py` 调用时候选的 `title` 已经
    是原始标题（未归一化），传 `False`（默认）由本函数负责归一化；如果
    调用方已经算好 key，可传 `True` 跳过重复计算。
    """
    try:
        key = title_or_key if already_normalized else normalize_title_key(title_or_key)
        if not key:
            return None
        data = _load_all(paths)
        bucket = data.get(key)
        if not bucket:
            return None
        total = len(bucket)
        failed = sum(1 for r in bucket if r.get("outcome") == "failed")
        return total, failed
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.objective_outcome_tracker.theme_failure_stats')
        return None


def judge_theme(paths, title: str) -> str:
    """
    供 `soft_goal_deriver.derive_candidates()` 调用的便捷判定：
      - "skip"       — 样本数达标且失败率 ≥ SKIP_FAILURE_RATIO，建议本轮跳过
      - "downweight" — 样本数达标且失败率 ≥ DOWNWEIGHT_FAILURE_RATIO，建议降权
      - "ok"         — 样本不足或失败率不高，不做任何调整
    失败静默降级为 "ok"（不影响候选，等同于"没有这个 Track"时的行为）。
    """
    stats = theme_failure_stats(paths, title)
    if stats is None:
        return "ok"
    total, failed = stats
    if total < MIN_SAMPLES_FOR_JUDGEMENT:
        return "ok"
    ratio = failed / total
    if ratio >= SKIP_FAILURE_RATIO:
        return "skip"
    if ratio >= DOWNWEIGHT_FAILURE_RATIO:
        return "downweight"
    return "ok"


__all__ = [
    "normalize_title_key",
    "record_outcome",
    "theme_failure_stats",
    "judge_theme",
    "MAX_HISTORY_PER_THEME",
    "MIN_SAMPLES_FOR_JUDGEMENT",
    "SKIP_FAILURE_RATIO",
    "DOWNWEIGHT_FAILURE_RATIO",
    "DOWNWEIGHT_FACTOR",
]
