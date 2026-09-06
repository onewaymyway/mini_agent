"""
profile.py — 用户画像（profile）存储与生成。

[SYS-PROFILE]

设计目标：
  - 当前为单用户模式：profile 存储于 ~/.agent/profile.json
    （路径由 AgentPaths.profile_path() 决定）。
  - 为后续多用户预留扩展点：
      1. UserProfile 自带 user_id 字段（默认 "default"）。
      2. UserProfileManager 接收可选的 user_id，传给
         AgentPaths.profile_path(user_id)；多用户场景下只需在
         构造 Agent 时传入实际 user_id，无需改动本模块的读写/生成逻辑。
      3. preferences（用户手动设置）与 derived（系统自动生成）分离存储，
         避免自动刷新覆盖用户的显式偏好。

Profile 文件结构：
{
  "user_id": "default",
  "display_name": null,
  "created_at": 1700000000.0,
  "updated_at": 1700000000.0,
  "preferences": {},          # 用户可通过命令显式设置，本模块不会自动覆盖
  "derived": {
    "summary": "...",          # 一段自然语言画像总结
    "tech_stack": [             # [next_doc/memory_backfill_and_profile_update_plan.md
      {"text": "...", "last_confirmed_at": 1700000000.0}, ...
    ],
    "habits": [{"text": "...", "last_confirmed_at": 1700000000.0}, ...],
    "source_entry_count": 5,   # 生成时使用的记忆条目累计数（用于增量刷新判断）
    "updated_at": 1700000000.0
  }
}

[next_doc/memory_backfill_and_profile_update_plan.md 方向二] 画像刷新机制：
从"每次用最近 N 条记忆重新生成、整体覆盖"改为"把上一版画像也喂给 LLM，
要求在此基础上更新"。`tech_stack`/`habits` 因此从纯字符串列表升级为
`{text, last_confirmed_at}` 结构——LLM 只负责给出文本内容的增删，
`last_confirmed_at` 由代码按文本匹配维护，不交给 LLM 生成（时间戳是
客观事实，交给 LLM 容易出现幻觉时间）。旧版纯字符串列表在加载时会被
自动迁移成新结构（见 `_migrate_text_items`），`last_confirmed_at` 无法
回溯，统一取迁移时刻。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.llm.base import LLMClient
    from mini_agent.perception.memory_store import MemoryEntry


# [next_doc/growth_advisor_improvement_plan_v2.md P4-0] `derived` 的命名空间
# 约定：UserProfileManager.generate() 只负责下面这几个 key，其余 key（如
# growth_advisor 写入的 growth_focus_areas / growth_topic_keywords）由各自
# 的模块自行管理，generate() 不会触碰、也不会清空它们。
PROFILE_GENERATED_KEYS = frozenset(
    {"summary", "tech_stack", "habits", "source_entry_count", "updated_at"}
)


def _normalize_text_key(text: str) -> str:
    """[方向二] 文本归一化比较键：去首尾空白 + 折叠内部连续空白 +
    大小写不敏感。只做规则层面的宽松匹配，不做语义模糊匹配——避免把
    本该是两条不同的特征误判合并（见方案 3.4 节的说明）。"""
    return " ".join((text or "").split()).lower()


def _migrate_text_items(raw: list, *, fallback_ts: float) -> list[dict]:
    """把旧版"纯字符串列表"或已经是新结构的数据统一整理成
    `[{"text": ..., "last_confirmed_at": ...}, ...]`。

    - 元素是 str：视为旧格式，`last_confirmed_at` 无法回溯，取
      `fallback_ts`（迁移发生的时刻）。
    - 元素是 dict 且带 text：原样保留（缺失/非法的 last_confirmed_at
      同样回退到 fallback_ts，不让脏数据拖垮整个画像加载）。
    - 其它类型的元素直接丢弃，不让一条脏数据拖垮整份画像。
    """
    out: list[dict] = []
    for item in raw or []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"text": text, "last_confirmed_at": fallback_ts})
        elif isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            try:
                ts = float(item.get("last_confirmed_at", fallback_ts))
            except (TypeError, ValueError):
                ts = fallback_ts
            out.append({"text": text, "last_confirmed_at": ts})
    return out


def _merge_text_items(
    previous_items: list[dict], new_texts: list[str], *, now: float,
) -> list[dict]:
    """[方向二] 用 LLM 本轮输出的纯文本列表，和上一版结构化列表做合并：

    - 新文本能在旧列表里找到归一化匹配的，视为"被再次印证"，
      `last_confirmed_at` 更新为 `now`。
    - 新文本在旧列表里找不到匹配的，视为新增条目，`last_confirmed_at`
      = `now`。
    - 旧列表里存在、但本轮 LLM 输出没有再提到的条目，视为 LLM 判断
      该淘汰，直接丢弃——去留仍然完全由 LLM 决定，这里只维护"新鲜度"
      这个原本对 LLM 不可见的信号。
    """
    old_by_key = {_normalize_text_key(it["text"]): it for it in previous_items}
    merged: list[dict] = []
    seen_keys: set[str] = set()
    for text in new_texts:
        text = str(text).strip()
        if not text:
            continue
        key = _normalize_text_key(text)
        if key in seen_keys:
            continue  # 本轮输出内部去重，避免 LLM 重复输出同一条
        seen_keys.add(key)
        old = old_by_key.get(key)
        merged.append({"text": text, "last_confirmed_at": now})
        del old  # 仅用于表达"找到即算被印证"，时间戳统一取 now，不取旧值
    return merged


# ─────────────────────────────────────────────────────────────────────────
# [next_doc/personal_ai_alignment_upgrade_plan.md 阶段一] 用户侧证据分级
# 扩展：`derived["values"]` / `derived["risk_preference"]` /
# `derived["constraints"]` 三个新命名空间，复用 `tech_stack`/`habits` 的
# "text + last_confirmed_at" 结构范式，额外新增 `source`/`confidence`
# 两个字段。这三个 key 不在 `PROFILE_GENERATED_KEYS` 内，`generate()`
# 不会触碰、也不会清空它们——由 `evolution/user_signal_profile_builder.py`
# （values/risk_preference，AI 归纳）与本模块的
# `UserProfileManager.add_constraint()`（constraints，用户显式声明）
# 各自独立维护，与 growth_advisor 写 `growth_focus_areas` 是同一套
# "各自维护、互不侵入"约定。
#
# `source` 取值：
#   - "user_stated"：用户话里明确说的。
#   - "ai_observation"：从行为直接观察到、无需推测（如"用户拒绝了 N 次
#     自动发送消息的建议"这类计数事实本身）。
#   - "ai_inference"：AI 基于观察推测出的模式。三者中只有这一类在展示时
#     必须带角标区分，且不能被其余子系统当作既定事实直接使用（只能作为
#     参考），避免推测链式放大为"AI 自己认定的用户事实"。
# ─────────────────────────────────────────────────────────────────────────

USER_SIGNAL_KEYS = ("values", "risk_preference", "constraints")

EVIDENCE_SOURCE_USER_STATED = "user_stated"
EVIDENCE_SOURCE_AI_OBSERVATION = "ai_observation"
EVIDENCE_SOURCE_AI_INFERENCE = "ai_inference"
_VALID_EVIDENCE_SOURCES = frozenset(
    {EVIDENCE_SOURCE_USER_STATED, EVIDENCE_SOURCE_AI_OBSERVATION, EVIDENCE_SOURCE_AI_INFERENCE}
)


def _migrate_evidence_items(raw: list, *, fallback_ts: float) -> list[dict]:
    """与 `_migrate_text_items` 同构，额外整理 `source`/`confidence`/
    `evidence_refs` 三个字段：
      - `source` 缺失或非法值一律回退为 `ai_inference`——三者中最谨慎的
        默认值，不能因为脏数据/迁移丢字段就让某条记录被误当成用户原话
        或直接观察。
      - `confidence` 缺失/非法回退为 0.0（未知强度，不臆造）。
      - `evidence_refs` 缺失/非法回退为空列表（纯用户显式声明的
        constraints 天然没有 evidence_refs，这是正常情况，不是脏数据）。
    """
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        try:
            ts = float(item.get("last_confirmed_at", fallback_ts))
        except (TypeError, ValueError):
            ts = fallback_ts
        source = item.get("source")
        if source not in _VALID_EVIDENCE_SOURCES:
            source = EVIDENCE_SOURCE_AI_INFERENCE
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        refs = item.get("evidence_refs")
        refs = [str(r) for r in refs] if isinstance(refs, list) else []
        out.append({
            "text": text,
            "last_confirmed_at": ts,
            "source": source,
            "confidence": confidence,
            "evidence_refs": refs,
        })
    return out


def upsert_user_stated_item(items: list[dict], text: str, *, now: float) -> list[dict]:
    """[阶段一 constraints] 用户显式声明的证据条目 upsert：已存在
    （按 `_normalize_text_key` 归一化匹配）则只刷新 `last_confirmed_at`，
    不存在则新增，`source` 固定为 `user_stated`、`confidence` 固定为
    1.0——用户自己说的话不需要也不应该有"置信度打折"的概念。"""
    text = (text or "").strip()
    if not text:
        return items
    key = _normalize_text_key(text)
    out = list(items or [])
    for it in out:
        if _normalize_text_key(it.get("text", "")) == key:
            it["last_confirmed_at"] = now
            it["source"] = EVIDENCE_SOURCE_USER_STATED
            it["confidence"] = 1.0
            return out
    out.append({
        "text": text,
        "last_confirmed_at": now,
        "source": EVIDENCE_SOURCE_USER_STATED,
        "confidence": 1.0,
        "evidence_refs": [],
    })
    return out


def remove_user_stated_item(items: list[dict], text: str) -> tuple[list[dict], bool]:
    """按归一化文本匹配移除一条记录，返回 (新列表, 是否命中)。"""
    key = _normalize_text_key(text or "")
    if not key:
        return list(items or []), False
    out = [it for it in (items or []) if _normalize_text_key(it.get("text", "")) != key]
    return out, len(out) != len(items or [])


def stale_items(items: list[dict], *, now: float, stale_after_days: int) -> list[str]:
    """挑出"距今超过 stale_after_days 天没有被再次印证"的条目文本，
    供 prompt 渲染时单独标注提醒 LLM 重新评估（见
    prompts/user/profile_update_request.md）。"""
    cutoff = now - stale_after_days * 86400.0
    return [
        it["text"] for it in items
        if float(it.get("last_confirmed_at", now)) < cutoff
    ]


@dataclass
class UserProfile:
    user_id: str = "default"
    display_name: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # 用户显式设置的偏好（语言、模型、语气等），本模块不会自动修改
    preferences: dict = field(default_factory=dict)
    # 系统根据长期记忆自动生成/刷新的画像
    derived: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        known = {f for f in cls.__dataclass_fields__}
        profile = cls(**{k: v for k, v in data.items() if k in known})
        # [方向二：迁移期兼容] 加载时把 tech_stack/habits 统一整理成新结构，
        # 不管磁盘上存的是旧版纯字符串列表还是新结构——保证 generate()
        # 和下游读取方（growth_advisor 诊断面板等）任何时候看到的都是
        # 统一格式，迁移只在"读到旧格式"这一刻发生一次。
        if profile.derived:
            now = time.time()
            for field_name in ("tech_stack", "habits"):
                raw = profile.derived.get(field_name)
                if raw:
                    profile.derived[field_name] = _migrate_text_items(raw, fallback_ts=now)
            # [next_doc/personal_ai_alignment_upgrade_plan.md 阶段一]
            for field_name in USER_SIGNAL_KEYS:
                raw = profile.derived.get(field_name)
                if raw:
                    profile.derived[field_name] = _migrate_evidence_items(raw, fallback_ts=now)
        return profile

    @property
    def is_new(self) -> bool:
        """是否为尚未生成过画像的新用户。"""
        return not self.derived


# ─────────────────────────────────────────────────────────────────────────
# [next_doc/profile_context_sources_completeness_plan.md 方向 E]
# 画像生成的"背景信息块"统一注册机制。
#
# 背景：`UserProfileManager.generate()` 此前对每一个新增信息源都手写一段
# 几乎一样的"try: 拉数据 ... except: 空串 ... 拼进对应的 xxx_block 变量
# ... 传给 pm.render() 的一个具名参数"样板代码——从 goal_tree_block 开始，
# 陆续加到 watchlist_block/preferences_block/growth_focus_block/
# wiki_block 共 5 份，重复到明显影响可维护性（新增一个信息源要同时改
# generate() 内部逻辑 + prompt 模板的具名变量列表两处）。这里统一抽成
# "provider 函数列表 + 一次性收集"，新增信息源只需要在下面注册一个
# `(paths, profile) -> str` 的函数，不需要再碰 generate() 主体逻辑，
# 也不需要再给 prompt 模板加新的具名变量——全部背景块合并成一个
# {{context_blocks}} 传入。
#
# 统一签名为 `(paths, profile) -> str`：
#   - 大多数 provider 只需要 paths（goal_tree/watchlist/wiki 都是读取
#     paths 指向的文件/目录），忽略 profile 参数即可；
#   - preferences/growth_focus 两个 provider 需要读取已经 load() 出来的
#     profile 对象（`profile.preferences` / `profile.derived`），避免
#     再重新 load 一次。
# 每个 provider 内部各自 try/except 兜底为空串——一个信息源的异常不该
# 影响其它信息源，也不该影响画像生成主流程；`_collect_profile_context_
# blocks()` 外层再兜一层，双重保险。
# ─────────────────────────────────────────────────────────────────────────

def _profile_context_goal_tree(paths: "AgentPaths", profile: "UserProfile") -> str:
    """活跃 + 最近完成的目标树快照（方向二 + 方向 B）。"""
    try:
        from mini_agent.perception.goal_tree_report import build_goal_tree_profile_snapshot
        return build_goal_tree_profile_snapshot(paths)
    except Exception:
        return ""


def _profile_context_watchlist(paths: "AgentPaths", profile: "UserProfile") -> str:
    """用户在 watchlist.yaml 里显式配置要关注的话题。"""
    try:
        from mini_agent.external_input.watchlist import build_watchlist_profile_snapshot
        return build_watchlist_profile_snapshot(paths)
    except Exception:
        return ""


def _profile_context_preferences(paths: "AgentPaths", profile: "UserProfile") -> str:
    """`profile.preferences` 是用户通过 `set_preference()`（CLI `/profile
    set` 或看板"✏️ 我的偏好设置"）显式设置的偏好——客观事实，不是需要
    LLM 从会话记忆里"推断"出来的东西，作为独立的"既定事实"区块传入，
    明确告诉模型这是不需要被会话证据推翻的 ground truth。"""
    if not profile.preferences:
        return ""
    lines = [
        "The user has explicitly set these preferences (ground truth — "
        "do not question or contradict them based on session evidence, "
        "just reflect them naturally where relevant):"
    ]
    for k, v in profile.preferences.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _profile_context_growth_focus(paths: "AgentPaths", profile: "UserProfile") -> str:
    """growth_advisor 通过规则扫描（`growth_signal_scan()`）持续把"agent
    认为用户在关注什么"写进 `profile.derived["growth_focus_areas"]`
    （{topic: [entry_id,...]}）。按命中记忆条目数降序取前几个主题名，
    只取主题名不展开命中的 entry_id 列表——那是诊断细节，跟"这个主题
    算不算用户的关注点"这层画像语义无关。（方向 A）"""
    try:
        focus_hits = (profile.derived or {}).get("growth_focus_areas") or {}
        if not isinstance(focus_hits, dict) or not focus_hits:
            return ""
        ranked_topics = sorted(
            focus_hits.items(), key=lambda kv: len(kv[1] or []), reverse=True
        )[:8]
        topic_names = [topic for topic, _hit_ids in ranked_topics if topic]
        if not topic_names:
            return ""
        return (
            "Topics the agent has independently detected the user engaging "
            "with recently (derived from signal-scanning session memory, not "
            "from the user explicitly stating them — treat as a weaker, "
            "corroborating signal rather than ground truth):\n"
            + "\n".join(f"- {t}" for t in topic_names)
        )
    except Exception:
        return ""


def _profile_context_wiki(paths: "AgentPaths", profile: "UserProfile") -> str:
    """wiki 里 research/growth 两个命名空间最近更新的条目标题（方向 C，
    第一步：零成本版本，只取标题+更新时间）。"""
    try:
        from mini_agent.wiki.stats import build_wiki_recent_updates_snapshot
        return build_wiki_recent_updates_snapshot(paths)
    except Exception:
        return ""


# 注册表：新增信息源时，在这里加一行即可，不需要再碰 generate() 主体。
_PROFILE_CONTEXT_PROVIDERS: list[Callable[["AgentPaths", "UserProfile"], str]] = [
    _profile_context_goal_tree,
    _profile_context_watchlist,
    _profile_context_preferences,
    _profile_context_growth_focus,
    _profile_context_wiki,
]


def _collect_profile_context_blocks(paths: "AgentPaths", profile: "UserProfile") -> str:
    """依次调用所有已注册的 provider，把非空结果拼成一段文本，整体作为
    跟 memory_text 并列的独立输入（不是"上一版画像"的一部分，而是每次
    生成时都重新拉取的当前状态快照）。任一 provider 异常不影响其它
    provider，也不影响画像生成主流程；全部为空时返回空串（模板里对应
    的 {{context_blocks}} 位置就是空白，不会留下多余的空行标题）。
    """
    blocks = []
    for provider in _PROFILE_CONTEXT_PROVIDERS:
        try:
            snippet = provider(paths, profile)
        except Exception:
            snippet = ""
        if snippet:
            blocks.append(snippet)
    return ("\n\n".join(blocks) + "\n\n") if blocks else ""


class UserProfileManager:
    """
    用户 profile 的加载 / 保存 / 生成。

    Args:
        paths: AgentPaths 实例，用于解析存储路径。
        user_id: 预留参数。当前传 None 即可（单用户模式，
            对应 ~/.agent/profile.json）。多用户支持落地后，
            调用方传入实际 user_id 即可自动切换到
            ~/.agent/users/<user_id>/profile.json。
    """

    def __init__(self, paths, user_id: Optional[str] = None) -> None:
        self._paths = paths
        self._user_id = user_id
        self._path = paths.profile_path(user_id)
        self._profile: Optional[UserProfile] = None

    @property
    def path(self) -> Path:
        return self._path

    # ── 加载 / 保存 ────────────────────────────────────────────────────────

    def load(self) -> UserProfile:
        """加载 profile；不存在则返回一个新建的空 profile（不落盘）。"""
        if self._profile is not None:
            return self._profile

        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._profile = UserProfile.from_dict(data)
            except (json.JSONDecodeError, OSError, TypeError):
                self._profile = UserProfile(user_id=self._user_id or "default")
        else:
            self._profile = UserProfile(user_id=self._user_id or "default")

        return self._profile

    def save(self) -> None:
        """原子写回 profile 文件。"""
        if self._profile is None:
            return
        self._profile.updated_at = time.time()
        from mini_agent.session import _atomic_write_json
        _atomic_write_json(self._path, self._profile.to_dict())

    # ── 偏好（用户显式设置）──────────────────────────────────────────────────

    def set_preference(self, key: str, value) -> None:
        profile = self.load()
        profile.preferences[key] = value
        self.save()

    def set_display_name(self, name: str) -> None:
        profile = self.load()
        profile.display_name = name
        self.save()

    # ── 用户侧证据分级扩展（constraints，用户显式声明）───────────────────────
    # [next_doc/personal_ai_alignment_upgrade_plan.md 阶段一] `values`/
    # `risk_preference` 由 `evolution/user_signal_profile_builder.py`
    # （AI 归纳，source=ai_inference）维护，不在本类提供写入口；这里只
    # 负责 `constraints`——这一维度按方案定义必须是"用户明确说过的"，
    # 不应该、也不需要走 LLM 归纳，直接由调用方（CLI/API）在用户明确
    # 表达约束时调用本方法落盘。

    def add_constraint(self, text: str) -> UserProfile:
        profile = self.load()
        now = time.time()
        items = profile.derived.get("constraints") or []
        profile.derived["constraints"] = upsert_user_stated_item(items, text, now=now)
        self.save()
        return profile

    def remove_constraint(self, text: str) -> bool:
        profile = self.load()
        items = profile.derived.get("constraints") or []
        new_items, hit = remove_user_stated_item(items, text)
        if hit:
            profile.derived["constraints"] = new_items
            self.save()
        return hit

    def list_constraints(self) -> list[dict]:
        return list(self.load().derived.get("constraints") or [])

    # ── 自动画像生成 ──────────────────────────────────────────────────────

    def should_refresh(self, current_entry_count: int, cfg) -> bool:
        """
        判断是否需要(重新)生成 derived 画像。

        触发条件：
          - 记忆条目数 >= cfg.profile_min_entries，且
          - 尚未生成过画像（is_new），或
          - 自上次生成以来新增的条目数 >= cfg.profile_refresh_interval_entries，或
          - [next_doc/profile_staleness_and_goal_tree_gap_plan.md 方向一 A]
            距上次刷新已超过 cfg.profile_force_refresh_after_days 天，且
            期间至少有 1 条新记忆（纯粹"完全没有新记忆"时刷新也没有新
            信息可更新，不做无意义的强制刷新）。

        这条时间兜底解决的是"记忆缓慢累积、增量门槛一直跨不过"导致画像
        长期停在很久以前的问题；它不解决"完全没有新记忆"的情况——那种
        情况下即使强制刷新，LLM 也没有新证据可用。
        """
        if current_entry_count < cfg.profile_min_entries:
            return False

        profile = self.load()
        if profile.is_new:
            return True

        last_count = profile.derived.get("source_entry_count", 0)
        if (current_entry_count - last_count) >= cfg.profile_refresh_interval_entries:
            return True

        if current_entry_count <= last_count:
            return False  # 完全没有新记忆，强制刷新也无意义

        force_after_days = getattr(cfg, "profile_force_refresh_after_days", 14)
        last_updated = float(profile.derived.get("updated_at", 0) or 0)
        if last_updated and force_after_days:
            if (time.time() - last_updated) >= force_after_days * 86400.0:
                return True

        return False

    def generate(
        self,
        llm_client: "LLMClient",
        entries: list["MemoryEntry"],
        *,
        max_entries_for_profile: int = 20,
        stale_after_days: int = 90,
        rebuild: bool = False,
    ) -> UserProfile:
        """
        基于长期记忆条目，调用 LLM 生成/刷新 derived 画像。

        [next_doc/memory_backfill_and_profile_update_plan.md 方向二]
        `entries` 传入的是全部（按 created_at 升序排序过的）候选记忆，
        本方法内部自己决定实际喂给 LLM 的是哪一段：

        - `rebuild=True`（对应显式的"全量重建"入口，如 `/profile rebuild`）
          或画像此前从未生成过：退化为旧行为，只取最近
          `max_entries_for_profile` 条，不参考上一版画像。
        - 否则走增量更新：只取"自上次生成以来新增"的条目（用
          `source_entry_count` 做差集，与 `should_refresh()` 用的是
          同一套计数口径），并把上一版 `summary/tech_stack/habits`
          一并放进 prompt，要求 LLM 在此基础上更新而不是从零重写。
          `max_entries_for_profile` 在这个分支里退化为"新增条目数
          万一异常多时的兜底上限"（例如记忆回填一次性补了大量历史
          记忆，见方向一），不再是"每次固定只看这么多"。

        本方法只做 LLM 调用 + 解析 + 落盘，不做线程调度；
        调用方（agent/profile.py）负责在后台线程中调用它。
        """
        from mini_agent.prompts import pm

        profile = self.load()
        now = time.time()

        prev_derived = dict(profile.derived or {})
        prev_summary = str(prev_derived.get("summary", "") or "")
        prev_tech_items = _migrate_text_items(prev_derived.get("tech_stack") or [], fallback_ts=now)
        prev_habit_items = _migrate_text_items(prev_derived.get("habits") or [], fallback_ts=now)
        last_count = int(prev_derived.get("source_entry_count", 0) or 0)

        incremental = (not rebuild) and (not profile.is_new) and last_count > 0
        if incremental:
            # entries 已按 created_at 升序排列（调用方保证），取上次生成
            # 之后新增的那一段；如果因为记忆被删除/顺序变化导致算出的
            # delta 异常（比如为空却明明触发了 should_refresh），退化为
            # 全量重建分支，不让一次异常直接卡死画像刷新。
            delta_entries = entries[last_count:] if last_count < len(entries) else []
            if not delta_entries:
                incremental = False
                delta_entries = entries[-max_entries_for_profile:]
            elif len(delta_entries) > max_entries_for_profile:
                # 兜底上限：新增条目数异常多时（如记忆回填一次性补了
                # 大量历史），只取最近的一批，避免 prompt 无限增长。
                delta_entries = delta_entries[-max_entries_for_profile:]
        else:
            delta_entries = entries[-max_entries_for_profile:]

        memory_text = "\n".join(
            f"- {e.summary}" + (f"（标签: {', '.join(e.tags)}）" if e.tags else "")
            for e in delta_entries
        )

        # [next_doc/growth_advisor_diagnostics_and_language_fix_plan.md
        # 方向二] 显式检测用户常用语言并落盘，不再靠"跟记忆条目同语言"
        # 这条弱约束隐式传递——如果 delta_entries 里的摘要本身已经被上游
        # 转成了英文，靠"同语言"这条指令是没有基准可跟的。这里直接对本
        # 轮参与 prompt 的记忆摘要文本做检测；没有新增记忆时（增量分支
        # delta 为空退化的情况理论上不会发生，因为上面已经兜底成
        # entries[-N:]）保留上一版检测结果，避免因为这一轮没有有效文本
        # 就被冲回默认英文。
        from mini_agent.utils.lang_detect import detect_primary_language, DEFAULT_LANGUAGE
        detected_language = detect_primary_language([e.summary for e in delta_entries])
        prev_language = str(prev_derived.get("preferred_language", "") or "")
        preferred_language = detected_language if detected_language != DEFAULT_LANGUAGE or not prev_language else prev_language

        previous_profile_text = ""
        stale_tech = stale_items(prev_tech_items, now=now, stale_after_days=stale_after_days)
        stale_habits = stale_items(prev_habit_items, now=now, stale_after_days=stale_after_days)
        if incremental:
            lines = ["Here is the previous profile, built from earlier sessions:"]
            if prev_summary:
                lines.append(f"Summary: {prev_summary}")
            if prev_tech_items:
                lines.append("Tech stack: " + "; ".join(it["text"] for it in prev_tech_items))
            if prev_habit_items:
                lines.append("Habits: " + "; ".join(it["text"] for it in prev_habit_items))
            if stale_tech or stale_habits:
                lines.append(
                    "The following items have not been reconfirmed by any evidence in a "
                    "long time — please reconsider whether they still hold:"
                )
                for t in stale_tech:
                    lines.append(f"  - [tech_stack] {t}")
                for h in stale_habits:
                    lines.append(f"  - [habits] {h}")
            lines.append(
                "Update the profile above based on the new session summaries below: keep "
                "what still holds, adjust or remove what the new evidence contradicts or "
                "what is flagged as stale, and add what's genuinely new. Do not simply "
                "summarize only the new sessions in isolation."
            )
            lines.append("")  # 与下面的 Session summaries 之间留一个空行
            previous_profile_text = "\n".join(lines) + "\n\n"

        # [next_doc/profile_context_sources_completeness_plan.md 方向 E]
        # 5 段几乎一样的"try: 拉数据 ... except: 空串 ... 拼进具名变量"
        # 样板代码统一收敛成一次调用——新增信息源只需要在模块级的
        # `_PROFILE_CONTEXT_PROVIDERS` 里注册一个函数，不需要再改这里。
        context_blocks = _collect_profile_context_blocks(self._paths, profile)

        prompt = pm.render(
            "user/profile_update_request",
            memory_text=memory_text,
            previous_profile_block=previous_profile_text,
            context_blocks=context_blocks,
        )

        resp = llm_client.chat_with_retry(
            messages=[{"role": "user", "content": prompt}],
            system=pm.render(
                "system/profile_summarizer",
                preferred_language=preferred_language,
            ),
            tools=[],
            max_retries=10,
        )
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"summary": raw[:500]}

        new_tech_texts = [str(t) for t in list(parsed.get("tech_stack", []))[:20]]
        new_habit_texts = [str(t) for t in list(parsed.get("habits", []))[:20]]

        if incremental:
            tech_items = _merge_text_items(prev_tech_items, new_tech_texts, now=now)
            habit_items = _merge_text_items(prev_habit_items, new_habit_texts, now=now)
        else:
            tech_items = [{"text": t, "last_confirmed_at": now} for t in new_tech_texts if t.strip()]
            habit_items = [{"text": h, "last_confirmed_at": now} for h in new_habit_texts if h.strip()]

        new_fields = {
            "summary": str(parsed.get("summary", ""))[:1000],
            "tech_stack": tech_items[:20],
            "habits": habit_items[:20],
            # source_entry_count 记录的是"累计处理过的记忆条目数"（用于
            # should_refresh 的增量判断和下一次 generate 的 delta 计算），
            # 不是"本次喂给 LLM 的条目数"——增量分支下两者不相等。
            "source_entry_count": len(entries),
            "updated_at": now,
            # [next_doc/growth_advisor_diagnostics_and_language_fix_plan.md
            # 方向二] 供其它生成类 prompt（成长顾问报告/月度复盘等）复用，
            # 避免各自重复实现语言检测。
            "preferred_language": preferred_language,
        }
        # [next_doc/growth_advisor_improvement_plan_v2.md P4-0] 合并式更新：
        # 只覆盖本方法自己负责的固定字段集合（PROFILE_GENERATED_KEYS），
        # 保留 derived 里其他模块（如 growth_advisor）写入的 key（例如
        # growth_focus_areas / growth_topic_keywords）。
        merged = dict(profile.derived or {})
        merged.update(new_fields)
        profile.derived = merged
        self.save()
        return profile

