"""
perception/goal_tree_decomposer.py — 目标树自动分解机制（阶段二）

见 next_doc/goal_tree_system_plan.md §4.2、§五 分阶段实施规划第 2 项。

职责：给定目标树上任意一个节点，调用 LLM（`LLMHelper.ask()`，独立轻量
调用，不占主 Agent 上下文，与 `default_goal_to_objectives()`/
`GoalBacklog._llm_decompose()` 同一种"纯文本输出、程序侧解析"模式）生成
"把这个节点往下拆成几个子节点"的候选，落进该节点的 `decompose_candidates`
字段，供用户在 CLI/看板上 accept/reject。

本阶段（阶段二）实现的是 §4.2 三种触发时机里的**判断逻辑**本身，只接入
CLI 手动触发（`decompose()` 方法可以被任何调用方直接调用）；真正接入
cron 定时巡检（`sys:goal_tree_decompose_scan`）留到阶段三，那时候会有
一个新的 cron job 定期调用本模块的 `find_stale_nodes_for_scan()` +
`decompose()`，这里先把两者都写成独立可测试的纯函数/方法，阶段三直接
复用，不需要重新设计。

节奏治理（§4.2"节奏治理"一段）：
  - 同一节点两次分解建议之间至少间隔 `MIN_DECOMPOSE_INTERVAL_SECONDS`；
  - 该节点已有未处理候选时跳过本次巡检（避免候选堆积）；
  - reject 后 `REJECTED_TTL_SECONDS`（30 天，与 soft_goal_deriver.py 同一
    量级）内不再对同一节点生成同主题候选。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from mini_agent.perception.goal_backlog import (
    GoalNode,
    LEVEL_ORDER,
    validate_node_hierarchy,
)

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.perception.goal_backlog import GoalBacklog
    from mini_agent.storage.paths import AgentPaths


# ── 常量 ──────────────────────────────────────────────────────────────────────

# 停滞巡检阈值天数：一个非叶子节点没有任何 active 子节点、且超过这么多天
# 没有被 touch 过，才认为它"停滞"了。跟 `compute_aging_boost()` 里
# `stale_days` 同一套口径的量级（见方案文档 §4.2 触发时机 1），阶段三接入
# 真正的 cron 巡检时可以按实际观察到的触发频率再调整。
STALE_DAYS_DEFAULT = 14

# 同一节点两次分解建议之间的最小触发间隔，避免同一停滞节点被反复打扰。
MIN_DECOMPOSE_INTERVAL_SECONDS = 3 * 86400

# reject 后的去重窗口，与 soft_goal_deriver.py 的 REJECTED_TTL_SECONDS 同一
# 量级（30 天）。
REJECTED_TTL_SECONDS = 30 * 86400

# 单次生成的候选数量上限，避免一次巡检把某个节点的候选列表撑爆。
MAX_CANDIDATES_PER_CALL = 5

_STATE_FILENAME = "goal_tree_decompose_state.json"
_REJECTED_FILENAME = "goal_tree_decompose_rejected.json"


def _next_default_level(parent_level: str) -> str:
    """默认子节点 level = 父节点在 `LEVEL_ORDER` 里的下一层，与
    `goal_backlog._next_default_level()` 完全一致——这里独立复制一份而不是
    导入私有函数，因为两处的"默认下一层"分别用于"候选没给出建议时兜底"
    （本模块）和"accept 时最后一道防线兜底"（goal_backlog.py），刻意保持
    实现同步但不建立跨模块的私有依赖。"""
    if parent_level not in LEVEL_ORDER:
        return "objective"
    idx = LEVEL_ORDER.index(parent_level)
    if idx + 1 < len(LEVEL_ORDER):
        return LEVEL_ORDER[idx + 1]
    return "objective"


def _dedupe_title_key(title: str) -> str:
    from mini_agent.evolution.objective_outcome_tracker import normalize_title_key
    return normalize_title_key(title)


def find_stale_nodes_for_scan(
    backlog: "GoalBacklog", *, stale_days: float = STALE_DAYS_DEFAULT,
) -> list[GoalNode]:
    """[§4.2 触发时机 1：停滞巡检] 找出"没有任何子节点，或所有子节点都已
    completed/abandoned"且自身仍 active/paused 的非叶子节点（`ultimate`/
    `domain`/`stage`/`goal`——`goal` 也可能是非叶子，比如 goal 挂 goal 的
    场景），且最后一次被 touch 的时间距今超过 `stale_days` 天。

    只读查询，不加锁——调用方（阶段三的 cron job）通常紧接着要对命中的
    节点逐个调用可能较慢的 `decompose()`（含 LLM 请求），若在锁内做会让
    持有跨进程文件锁的时间从"毫秒级"变成"多次 LLM 请求耗时"。与
    `GoalBacklog.goals_missing_objective()` 同样的取舍，见该方法注释。
    """
    backlog.load()
    now = time.time()
    threshold = stale_days * 86400
    result: list[GoalNode] = []
    for node in backlog.all_nodes():
        if node.status not in ("active", "paused"):
            continue
        if node.level == "objective":
            continue  # 叶子层，不参与分解
        active_children = [
            c for cid in node.children_ids
            if (c := backlog.get(cid)) is not None and c.status not in ("completed", "abandoned")
        ]
        if active_children:
            continue
        last_touched = node.last_touched_at or node.created_at
        if now - last_touched < threshold:
            continue
        result.append(node)
    return sorted(result, key=lambda n: n.priority, reverse=True)


def find_parent_needing_decompose_after_completion(
    backlog: "GoalBacklog", completed_node_id: str,
) -> Optional[GoalNode]:
    """[§4.2 触发时机 2：完成态联动] 一个 `goal`/`stage` 节点被标记
    `completed` 后调用：检查其父节点是否因此"没有其它 active 子节点了"，
    是则返回父节点（调用方据此触发一次分解建议），否则返回 `None`。

    只读查询，不加锁，同 `find_stale_nodes_for_scan()`。本阶段只提供这个
    检测函数——真正"在 `set_status()` 写完 completed 之后自动调这个函数
    再触发 LLM 分解"的接线，留到阶段三跟 cron 巡检一起做（同步接入
    `set_status()` 会让一次状态写入意外挂上一次 LLM 调用，超出"轻量写入"
    的语义，阶段三会评估走同步内联还是走 cron 下一拍捕获，见方案文档
    §六"待实施阶段确认的细节"）。
    """
    backlog.load()
    completed_node = backlog.get(completed_node_id)
    if completed_node is None or completed_node.parent_id is None:
        return None
    parent = backlog.get(completed_node.parent_id)
    if parent is None or parent.status not in ("active", "paused"):
        return None
    active_children = [
        c for cid in parent.children_ids
        if (c := backlog.get(cid)) is not None and c.status not in ("completed", "abandoned")
    ]
    if active_children:
        return None
    return parent


class GoalTreeDecomposer:
    """针对目标树任意节点生成分解候选。用法：

        decomposer = GoalTreeDecomposer(paths, backlog)
        candidates = decomposer.decompose(node_id, llm_helper=helper)
    """

    def __init__(self, paths: "AgentPaths", backlog: "GoalBacklog") -> None:
        self._paths = paths
        self._backlog = backlog

    # ── 节奏治理状态：上次分解时间 ──────────────────────────────────────────

    @property
    def _state_path(self) -> Path:
        return self._paths.workdir_dir / _STATE_FILENAME

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def last_attempt_at(self, node_id: str) -> float:
        return float(self._load_state().get(node_id, 0.0))

    def record_attempt(self, node_id: str) -> None:
        try:
            data = self._load_state()
            data[node_id] = time.time()
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.perception.goal_tree_decomposer.record_attempt")

    # ── 节奏治理状态：reject 去重 ────────────────────────────────────────────

    @property
    def _rejected_path(self) -> Path:
        return self._paths.workdir_dir / _REJECTED_FILENAME

    def _load_rejected_keys(self) -> set[str]:
        try:
            data = json.loads(self._rejected_path.read_text(encoding="utf-8"))
            now = time.time()
            return {k for k, ts in data.items() if now - float(ts) < REJECTED_TTL_SECONDS}
        except Exception:
            return set()

    def record_rejected_topic(self, node_id: str, title: str) -> None:
        """reject 一个候选后调用：30 天内不再对同一节点生成同主题候选。"""
        key = f"{node_id}:{_dedupe_title_key(title)}"
        try:
            data: dict = {}
            if self._rejected_path.exists():
                data = json.loads(self._rejected_path.read_text(encoding="utf-8"))
            data[key] = time.time()
            self._rejected_path.parent.mkdir(parents=True, exist_ok=True)
            self._rejected_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.perception.goal_tree_decomposer.record_rejected_topic")

    def reject_candidate(self, node_id: str, candidate_id: str) -> bool:
        """完整的 reject 流程：从节点的 `decompose_candidates` 移除该候选
        （委托 `GoalBacklog.reject_candidate()`），并记一条 30 天去重记录。
        比直接调用 `GoalBacklog.reject_candidate()` 多做的就是这条去重
        记录，CLI/看板的"忽略"按钮应该走这个方法。"""
        candidate = self._backlog.reject_candidate(node_id, candidate_id)
        if candidate is None:
            return False
        title = candidate.get("title", "")
        if title:
            self.record_rejected_topic(node_id, title)
        return True

    # ── 节奏治理：是否可以触发 ───────────────────────────────────────────────

    def should_decompose(
        self, node: GoalNode, *, min_interval: float = MIN_DECOMPOSE_INTERVAL_SECONDS,
    ) -> Optional[str]:
        """返回 `None` 表示可以触发一次分解；返回字符串是跳过原因（供日志/
        CLI 展示，不是异常）。"""
        if node.decompose_candidates:
            return "该节点已有未处理的分解候选，跳过本次巡检"
        last = self.last_attempt_at(node.id)
        if last and time.time() - last < min_interval:
            remain_days = round((min_interval - (time.time() - last)) / 86400, 1)
            return f"距上次分解建议不足最小间隔，还需等待约 {remain_days} 天"
        return None

    # ── prompt 拼装 ──────────────────────────────────────────────────────────

    def _ancestor_chain(self, node: GoalNode) -> list[GoalNode]:
        """从根到该节点（不含自身）的祖先链，让 LLM 理解"这是在为哪个更大
        目标服务"。存在环（数据异常）时提前截断，避免死循环。"""
        chain: list[GoalNode] = []
        seen: set[str] = set()
        cur = node
        while cur.parent_id and cur.parent_id not in seen:
            parent = self._backlog.get(cur.parent_id)
            if parent is None:
                break
            chain.append(parent)
            seen.add(parent.id)
            cur = parent
        chain.reverse()
        return chain

    def build_prompt(self, node: GoalNode) -> str:
        ancestors = self._ancestor_chain(node)
        ancestor_lines = "\n".join(
            f"{'  ' * i}- [{a.level}] {a.title}" for i, a in enumerate(ancestors)
        ) or "（无，这是根节点）"

        children = [
            c for c in self._backlog.all_nodes() if c.parent_id == node.id
        ]
        children_lines = "\n".join(
            f"- [{c.level}/{c.status}] {c.title}" for c in children
        ) or "（暂无子节点）"

        rejected_keys = self._load_rejected_keys()
        rejected_titles = [
            key.split(":", 1)[1] for key in rejected_keys
            if key.startswith(f"{node.id}:")
        ]
        rejected_lines = "、".join(rejected_titles) if rejected_titles else "（无）"

        default_level = _next_default_level(node.level)

        return f"""你在帮用户维护一棵"人生目标树"，现在要针对树上的一个节点，判断能不能把它
拆成几个更具体的子节点。

祖先链（从根到当前节点的上级，帮助你理解这是在为哪个更大目标服务）：
{ancestor_lines}

当前节点：
- 层级：{node.level}
- 标题：{node.title}
- 说明：{node.description or '（无）'}

当前节点已有的子节点（避免生成重复建议）：
{children_lines}

用户此前明确拒绝过的候选主题，不要再生成类似的（避免重复打扰）：
{rejected_lines}

要求：
1. 每行一个候选，格式严格为「标题｜一句话描述｜层级」，用全角竖线"｜"分隔，
   不要编号、不要多余符号。
2. "层级"填 {default_level}（当前节点的下一层），除非你认为拆成更细/更粗的
   层级更合适，那样可以填 domain/stage/goal/objective 中的其它一个。
3. 候选要具体到"可以单独作为一件事去推进"，不要重复当前节点标题本身，也不要
   跟已有子节点或被拒绝过的主题重复。
4. 如果当前节点已经足够具体、拆不出有意义的子节点，只输出一行也可以；如果
   完全拆不出来，输出空内容即可。
5. 不要输出候选数量之外的任何说明文字。

只输出候选行，每行一个。"""

    # ── 解析 LLM 输出 ────────────────────────────────────────────────────────

    def _parse_candidates(self, text: str, node: GoalNode) -> list[dict]:
        if not text:
            return []
        existing_titles = {
            _dedupe_title_key(c.title) for c in self._backlog.all_nodes()
            if c.parent_id == node.id
        }
        rejected_keys = self._load_rejected_keys()
        seen: set[str] = set()
        candidates: list[dict] = []
        default_level = _next_default_level(node.level)

        for raw_line in text.splitlines():
            line = raw_line.strip(" -•\t")
            if not line:
                continue
            parts = [p.strip() for p in line.replace("|", "｜").split("｜")]
            title = parts[0] if parts else ""
            if not title:
                continue
            description = parts[1] if len(parts) > 1 else ""
            level = parts[2] if len(parts) > 2 else ""
            if validate_node_hierarchy(level, node.level) is not None:
                level = default_level  # LLM 给的层级不合法，拉回默认下一层

            key = _dedupe_title_key(title)
            if key in seen or key in existing_titles:
                continue
            if f"{node.id}:{key}" in rejected_keys:
                continue
            seen.add(key)

            candidates.append({
                "id": f"cand_{uuid.uuid4().hex[:8]}",
                "title": title,
                "description": description,
                "level": level,
                "generated_at": time.time(),
                "reason": f"针对「{node.title}」的自动分解建议",
            })
            if len(candidates) >= MAX_CANDIDATES_PER_CALL:
                break
        return candidates

    # ── 主入口 ───────────────────────────────────────────────────────────────

    def decompose(
        self, node_id: str, llm_helper=None, *, cfg=None, force: bool = False,
    ) -> list[dict]:
        """针对 `node_id` 生成一批分解候选并落盘，返回新生成的候选列表
        （已经写进 `decompose_candidates`，不需要调用方再落盘）。

        `force=True` 跳过 `should_decompose()` 的节奏治理检查（供 CLI 手动
        触发"我知道有候选/间隔不够，但就是想现在跑一次"的场景）。

        `llm_helper` 未传时用 `LLMHelper.from_config(cfg)` 单次构造
        （`cfg` 也未传时用 `load_config()` 兜底），与 `ensemble/judge.py`
        等既有调用点同一种"helper or LLMHelper.from_config(cfg)"约定。

        节点不存在、被节奏治理跳过、LLM 调用失败、或解析不出任何有效候选
        时都返回空列表，不抛异常（与 `default_goal_to_objectives()` 一致
        的"失败返回空列表，不影响主流程"约定）；节点不存在是唯一例外——
        直接返回空列表，不记录 attempt（没有节点可记）。
        """
        node = self._backlog.get(node_id)
        if node is None:
            return []
        if not force:
            skip_reason = self.should_decompose(node)
            if skip_reason:
                return []

        self.record_attempt(node.id)

        helper = llm_helper
        if helper is None:
            from mini_agent.llm.service import LLMHelper
            if cfg is None:
                from mini_agent.config import load_config
                cfg = load_config()
            helper = LLMHelper.from_config(cfg)

        prompt = self.build_prompt(node)
        try:
            text = helper.ask(prompt)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.perception.goal_tree_decomposer.decompose")
            return []

        candidates = self._parse_candidates(text, node)
        if not candidates:
            return []
        self._backlog.append_decompose_candidates(node.id, candidates)
        return candidates


# ── 阶段三：cron 巡检接线 ─────────────────────────────────────────────────────
#
# [goal_tree_system_plan.md §4.2/§五 阶段三] 把阶段二写好的
# find_stale_nodes_for_scan() / find_parent_needing_decompose_after_
# completion() 两个检测函数，接进一个真正定时跑的 cron job
# （sys:goal_tree_decompose_scan）。
#
# §六"待实施阶段确认的细节"里遗留的两个问题在本阶段给出结论：
#   1. 触发时机 2（完成态联动）到底走"同步内联进 set_status()"还是"走
#      cron 下一拍捕获"——选择后者：run_decompose_scan_cycle() 每次运行时
#      额外扫一遍"最近一个巡检间隔内被标记 completed 的非叶子节点"，对每
#      个这样的节点调用 find_parent_needing_decompose_after_completion()，
#      命中的父节点跟停滞巡检命中的节点合并去重后一起触发分解。这样
#      set_status() 本身继续保持"毫秒级临界区写入"的语义不变，付出的代价
#      是完成态联动最多要等一个巡检周期（默认 24 小时）才会生效，而不是
#      立即触发——方案原文"不断更新现阶段目标"本来就是持续巡检的效果，不
#      是强实时通知，这个延迟可以接受。
#   2. sys:goal_tree_decompose_scan 是"轻量规则内部执行"还是"提交一个
#      task_template 走完整 Agent 轮次"——选择前者（本地回调 handler，同
#      `sys:wiki_quarantine_repair` 的取舍）：分解建议本身只是"调一次
#      LLMHelper.ask() 落一份候选"，不需要工具调用/多轮 Agent 决策，走完整
#      Agent 轮次是过度设计，且会占用主 Agent 的 InputQueue 轮次预算。


# 完成态联动扫描的回看窗口：略大于巡检间隔本身（默认 24 小时对应
# 86400 秒），避免"一个节点恰好在两次巡检之间的边界时刻完成"被漏判。
COMPLETION_LINK_LOOKBACK_SECONDS_DEFAULT = 90000  # 25 小时

# sys:goal_tree_decompose_scan 的 job id。
JOB_ID_DECOMPOSE_SCAN = "sys:goal_tree_decompose_scan"


@dataclass
class DecomposeScanSummary:
    """`run_decompose_scan_cycle()` 的返回值，供 cron handler 判定
    ok/日志展示，也方便测试断言。"""

    ok: bool = True
    # 命中的停滞节点数（§4.2 触发时机 1）。
    stale_node_count: int = 0
    # 命中的"完成态联动"父节点数（§4.2 触发时机 2，去重后）。
    completion_linked_count: int = 0
    # 实际尝试调用 decompose() 的节点数（stale + completion_linked 去重
    # 合并后的总数；节奏治理/no-op 情况仍计入，只是 candidate_count 为 0）。
    scanned_count: int = 0
    # 本轮新生成的候选总数（跨全部命中节点求和）。
    candidate_count: int = 0
    node_ids: list[str] = field(default_factory=list)


def run_decompose_scan_cycle(
    paths: "AgentPaths",
    backlog: "GoalBacklog",
    *,
    llm_helper=None,
    stale_days: float = STALE_DAYS_DEFAULT,
    completion_lookback_seconds: float = COMPLETION_LINK_LOOKBACK_SECONDS_DEFAULT,
) -> DecomposeScanSummary:
    """[goal_tree_system_plan.md §4.2 触发时机 1/2，§五 阶段三]
    `sys:goal_tree_decompose_scan` cron job 的核心逻辑，单次调用完成一整
    轮巡检 + 分解：

    1. `find_stale_nodes_for_scan()` 找出全部停滞节点；
    2. 扫描最近 `completion_lookback_seconds` 内被标记 `completed` 的非
       `objective` 节点，逐个调用
       `find_parent_needing_decompose_after_completion()`，收集命中的
       父节点（跟停滞节点集合去重合并）；
    3. 对合并后的目标节点逐个调用 `GoalTreeDecomposer.decompose()`——
       节奏治理（间隔/已有未处理候选）仍由 `decompose()` 内部的
       `should_decompose()` 负责，本函数不重复判断，也不传 `force=True`
       （巡检场景应该尊重节奏治理，不该绕过）。

    `llm_helper` 未传时委托 `GoalTreeDecomposer.decompose()` 自己按
    `LLMHelper.from_config()` 兜底构造——但调用方（daemon 侧
    `ensure_goal_tree_decompose_scan_job()`）通常会做成"没有可用
    llm_helper 时整个 job 直接跳过"，避免巡检本身触发一次隐式的默认模型
    调用，见该函数说明。

    不抛异常：内部逐节点调用已经由 `decompose()` 自己吞掉 LLM 异常，本
    函数只是遍历汇总，`ok` 恒为 `True`（保留字段是为了跟其它 `ensure_*_job`
    handler 返回 bool 的既有风格对齐，未来如果需要区分"部分失败"可以
    在这里扩展）。
    """
    stale_nodes = find_stale_nodes_for_scan(backlog, stale_days=stale_days)

    now = time.time()
    completed_recent = [
        n for n in backlog.all_nodes()
        if n.status == "completed"
        and n.level != "objective"
        and (n.last_touched_at or n.created_at or 0) >= now - completion_lookback_seconds
    ]
    stale_ids = {n.id for n in stale_nodes}
    linked_parents: list[GoalNode] = []
    linked_seen: set[str] = set()
    for completed_node in completed_recent:
        parent = find_parent_needing_decompose_after_completion(backlog, completed_node.id)
        if parent is None or parent.id in stale_ids or parent.id in linked_seen:
            continue
        linked_seen.add(parent.id)
        linked_parents.append(parent)

    targets = list(stale_nodes) + linked_parents

    decomposer = GoalTreeDecomposer(paths, backlog)
    candidate_count = 0
    for node in targets:
        candidates = decomposer.decompose(node.id, llm_helper=llm_helper)
        candidate_count += len(candidates)

    return DecomposeScanSummary(
        ok=True,
        stale_node_count=len(stale_nodes),
        completion_linked_count=len(linked_parents),
        scanned_count=len(targets),
        candidate_count=candidate_count,
        node_ids=[n.id for n in targets],
    )


def ensure_goal_tree_decompose_scan_job(
    paths: "AgentPaths",
    backlog: "GoalBacklog",
    cron_scheduler: "CronScheduler",
    *,
    llm_helper_provider: Optional[Callable[[], Any]] = None,
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:goal_tree_decompose_scan`
    （§4.2 触发时机 1/2 的巡检 + 分解，每 24 小时一次，参考 `sys:goal_review`
    的量级）。

    `llm_helper_provider` 可选（opt-in，默认 `None`，与
    `wiki_quarantine_repair.ensure_wiki_quarantine_repair_job()` 同一种
    "调用方决定是否愿意为这个 job 花 LLM 调用"的约定）：不传或调用返回
    `None` 时，本 job 每次触发都直接跳过（返回 `True`，不算失败）—— 分解
    建议是"锦上添花"的主动巡检，不该在没有配置/没有可用 LLM 的环境下
    产生噪音日志或意外报错。
    """
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID_DECOMPOSE_SCAN not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID_DECOMPOSE_SCAN,
        name="目标树停滞巡检与自动分解",
        schedule="interval:86400",
        description=(
            "扫描目标树上停滞（无 active 子节点且超过 14 天未 touch）的"
            "非叶子节点，以及最近因子节点全部完成而失去 active 子节点的"
            "节点，各自触发一次 GoalTreeDecomposer 分解建议（受节奏治理"
            "限制，不会重复打扰）；未配置可用 llm_helper 时静默跳过，不"
            "算失败。"
        ),
        tags=["maintenance", "goal_tree"],
    )

    def _handler(job: "CronJob") -> bool:
        helper = llm_helper_provider() if llm_helper_provider else None
        if helper is None:
            return True  # 没有可用 LLM，静默跳过，不算失败
        result = run_decompose_scan_cycle(paths, backlog, llm_helper=helper)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID_DECOMPOSE_SCAN, _handler)
    return newly_added


__all__ = [
    "GoalTreeDecomposer",
    "find_stale_nodes_for_scan",
    "find_parent_needing_decompose_after_completion",
    "STALE_DAYS_DEFAULT",
    "MIN_DECOMPOSE_INTERVAL_SECONDS",
    "REJECTED_TTL_SECONDS",
    "DecomposeScanSummary",
    "run_decompose_scan_cycle",
    "ensure_goal_tree_decompose_scan_job",
    "JOB_ID_DECOMPOSE_SCAN",
    "COMPLETION_LINK_LOOKBACK_SECONDS_DEFAULT",
]
