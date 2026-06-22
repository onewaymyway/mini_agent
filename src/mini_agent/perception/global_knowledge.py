"""
perception/global_knowledge.py — Global 知识层（W3，对应设计文档 8.3 节 /
self_evolution_stage4plus_plan.md Stage 5）

在 `~/.agent/`（global 根）下维护四个结构化文件，填补"agent 自身的全局认知"
（跨项目）的空白，与 Stage 4 的 Workdir 知识层（W2）同构，但 scope 从单项目
变为跨项目：

  - self_profile.json        — agent 自我模型（5.1）
  - projects_index.json      — workdir 注册表（5.2）
  - cross_project_index.json — 跨项目模式与能力图谱（5.4）
  - activity_log.jsonl       — 全局活动时序，append-only（5.3）

设计取舍（与 perception/workdir_knowledge.py 一致）：
  - 全部纯写入操作不经过 StateRepo——这四个文件是"观察性数据"，定位与
    project.json/work_index.json（W2）一致，不需要 git 历史与 tier 校验。
    self_profile.json 虽然设计文档称其"属于安全网 T1"，但那是指"未来若
    由 evolution-agent 自动改写敏感字段（如 autonomy_level）时才需要走
    StateRepo"；本 Stage 的写入路径全部是 SessionEnd 轻量维护（更新计数器/
    时间戳），性质与 task_manifest.json 的"主动写入但非治理性变更"一致，
    不强行套用 StateRepo 流程。
  - 写入使用 tmp + os.replace 原子写，避免并发/中断导致半截 JSON
  - 本模块只负责"数据结构 + 读写 + 聚合函数"，不负责"什么时候调用"——
    调用时机（SessionEnd hook / context 注入）由 agent.py、
    context_builder.py 各自负责；5.4 跨项目扫描函数本身在本 Stage 完成，
    但"周期性触发"留给 Stage 8（Phase G），本模块只暴露可被手动/未来
    调度器调用的纯函数。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths


# ── 原子写入辅助（与 workdir_knowledge.py 同款实现，独立维护避免跨模块耦合）──

def _atomic_write_text(path: Path, text: str) -> None:
    """原子写入文本文件（tmp + rename），避免读端看到半截内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: object) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _append_jsonl(path: Path, record: dict) -> None:
    """追加一行 JSONL。append 本身不需要原子性保证（单进程顺序写入场景），
    但确保父目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")


def _read_json(path: Path, default: object) -> object:
    """读取 JSON 文件；不存在或解析失败时返回 default（不抛异常——这是
    观察性数据，读取失败不应阻塞 agent 主流程）。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_jsonl(path: Path, limit: Optional[int] = None) -> list[dict]:
    """读取 JSONL 文件的全部（或最近 limit 条）记录，跳过无法解析的行。"""
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    if limit is not None and limit > 0:
        return records[-limit:]
    return records


def project_id_for(project_root: Path) -> str:
    """
    把一个 workdir 绝对路径映射为稳定的 project_id（`proj_<slug>_<hash6>`）。

    设计取舍：不直接用目录名作为 id（不同路径下可能同名，如两个人各自的
    `~/projects/mini_agent` 和 `~/work/mini_agent`），也不用完整路径转义后
    的字符串作为 id（太长，不适合作为 WorkThread/活动日志里频繁出现的短
    引用）。取「目录名 slug + 完整 resolved 路径的短 hash」组合：人类可读
    （从 id 能大致猜出是哪个项目），同时唯一（不同路径即使同名也不冲突）。
    同一路径多次调用始终返回相同 id（hash 基于路径字符串，无随机性）。
    """
    resolved = str(project_root.resolve())
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", project_root.resolve().name).strip("_").lower()
    if not slug:
        slug = "project"
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:6]
    return f"proj_{slug}_{digest}"


# ════════════════════════════════════════════════════════════════════════════
# 5.1 self_profile.json — agent 自我模型
# ════════════════════════════════════════════════════════════════════════════

_VALID_AUTONOMY_LEVELS = ("passive", "assisted", "maintenance", "autonomous")


@dataclass
class SelfIdentity:
    """对应设计文档 8.3 节 self_profile.json.identity。"""
    purpose: str = ""
    core_constraints_ref: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "core_constraints_ref": self.core_constraints_ref,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "SelfIdentity":
        return SelfIdentity(
            purpose=d.get("purpose", ""),
            core_constraints_ref=d.get("core_constraints_ref", ""),
            created_at=float(d.get("created_at", 0.0) or 0.0),
        )


@dataclass
class SelfAssessment:
    """对应设计文档 8.3 节 self_profile.json.self_assessment。

    confidence_by_domain 是 capability_map（6.6）的 global scope 版本——
    本 Stage 只声明字段与读写函数，真正的"从各 workdir capability_map
    汇总写回这里"的闭环属于 Stage 8（Phase G），本 Stage 留空或保守默认值
    （首次创建时），不在此处臆造数据。
    """
    strengths: list[str] = field(default_factory=list)
    weak_areas: list[str] = field(default_factory=list)
    confidence_by_domain: dict = field(default_factory=dict)
    last_assessed_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strengths": list(self.strengths),
            "weak_areas": list(self.weak_areas),
            "confidence_by_domain": dict(self.confidence_by_domain),
            "last_assessed_at": self.last_assessed_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "SelfAssessment":
        return SelfAssessment(
            strengths=list(d.get("strengths", []) or []),
            weak_areas=list(d.get("weak_areas", []) or []),
            confidence_by_domain=dict(d.get("confidence_by_domain", {}) or {}),
            last_assessed_at=float(d.get("last_assessed_at", 0.0) or 0.0),
        )

    def to_prompt_block(self) -> str:
        """注入 system prompt 的精简文本块（context_builder.py 8.4 节 always-on，
        精简注入——只给"我历史上在哪些领域强/弱"，不展开全部细节）。"""
        if not self.strengths and not self.weak_areas and not self.confidence_by_domain:
            return ""
        lines = ["## Self-assessment (across past sessions)"]
        if self.strengths:
            lines.append(f"- Strengths: {', '.join(self.strengths[:6])}")
        if self.weak_areas:
            lines.append(f"- Weak areas: {', '.join(self.weak_areas[:6])}")
        if self.confidence_by_domain:
            top = sorted(self.confidence_by_domain.items(), key=lambda kv: -kv[1])[:5]
            conf_text = "; ".join(f"{k}: {v:.2f}" for k, v in top)
            lines.append(f"- Confidence by domain: {conf_text}")
        return "\n".join(lines)


@dataclass
class OperatingState:
    """对应设计文档 8.3 节 self_profile.json.operating_state。"""
    autonomy_level: str = "passive"
    active_project: str = ""
    last_active_at: float = 0.0
    total_sessions_lifetime: int = 0
    total_projects_worked: int = 0

    def to_dict(self) -> dict:
        return {
            "autonomy_level": self.autonomy_level,
            "active_project": self.active_project,
            "last_active_at": self.last_active_at,
            "total_sessions_lifetime": self.total_sessions_lifetime,
            "total_projects_worked": self.total_projects_worked,
        }

    @staticmethod
    def from_dict(d: dict) -> "OperatingState":
        level = d.get("autonomy_level", "passive")
        if level not in _VALID_AUTONOMY_LEVELS:
            level = "passive"
        return OperatingState(
            autonomy_level=level,
            active_project=d.get("active_project", ""),
            last_active_at=float(d.get("last_active_at", 0.0) or 0.0),
            total_sessions_lifetime=int(d.get("total_sessions_lifetime", 0) or 0),
            total_projects_worked=int(d.get("total_projects_worked", 0) or 0),
        )


@dataclass
class ResourceBudget:
    """对应设计文档 8.3 节 self_profile.json.resource_budget。

    本 Stage 只做"记录"（字段先加进 schema），不做"仲裁"（超预算拒绝执行）——
    仲裁逻辑没有 daemon 调度器无从挂载，属于 Phase H 7.5 节范畴，过早实现
    等于无效代码（计划文档 5.1 节横向加固机会原话）。
    """
    daily_token_budget: int = 200_000
    used_today: int = 0
    budget_reset_at: str = "00:00"

    def to_dict(self) -> dict:
        return {
            "daily_token_budget": self.daily_token_budget,
            "used_today": self.used_today,
            "budget_reset_at": self.budget_reset_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "ResourceBudget":
        return ResourceBudget(
            daily_token_budget=int(d.get("daily_token_budget", 200_000) or 200_000),
            used_today=int(d.get("used_today", 0) or 0),
            budget_reset_at=d.get("budget_reset_at", "00:00"),
        )


@dataclass
class EvolutionState:
    """对应设计文档 8.3 节 self_profile.json.evolution_state。"""
    pending_evolve_branches: list[str] = field(default_factory=list)
    last_reflection_at: float = 0.0
    lifetime_lessons_generated: int = 0
    lifetime_skills_proposed: int = 0
    lifetime_skills_approved: int = 0

    def to_dict(self) -> dict:
        return {
            "pending_evolve_branches": list(self.pending_evolve_branches),
            "last_reflection_at": self.last_reflection_at,
            "lifetime_lessons_generated": self.lifetime_lessons_generated,
            "lifetime_skills_proposed": self.lifetime_skills_proposed,
            "lifetime_skills_approved": self.lifetime_skills_approved,
        }

    @staticmethod
    def from_dict(d: dict) -> "EvolutionState":
        return EvolutionState(
            pending_evolve_branches=list(d.get("pending_evolve_branches", []) or []),
            last_reflection_at=float(d.get("last_reflection_at", 0.0) or 0.0),
            lifetime_lessons_generated=int(d.get("lifetime_lessons_generated", 0) or 0),
            lifetime_skills_proposed=int(d.get("lifetime_skills_proposed", 0) or 0),
            lifetime_skills_approved=int(d.get("lifetime_skills_approved", 0) or 0),
        )


@dataclass
class SelfProfile:
    """对应设计文档 8.3 节 self_profile.json 完整 schema（顶层容器）。"""
    version: int = 1
    identity: SelfIdentity = field(default_factory=SelfIdentity)
    self_assessment: SelfAssessment = field(default_factory=SelfAssessment)
    operating_state: OperatingState = field(default_factory=OperatingState)
    resource_budget: ResourceBudget = field(default_factory=ResourceBudget)
    evolution_state: EvolutionState = field(default_factory=EvolutionState)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "identity": self.identity.to_dict(),
            "self_assessment": self.self_assessment.to_dict(),
            "operating_state": self.operating_state.to_dict(),
            "resource_budget": self.resource_budget.to_dict(),
            "evolution_state": self.evolution_state.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> "SelfProfile":
        return SelfProfile(
            version=int(d.get("version", 1) or 1),
            identity=SelfIdentity.from_dict(d.get("identity", {}) or {}),
            self_assessment=SelfAssessment.from_dict(d.get("self_assessment", {}) or {}),
            operating_state=OperatingState.from_dict(d.get("operating_state", {}) or {}),
            resource_budget=ResourceBudget.from_dict(d.get("resource_budget", {}) or {}),
            evolution_state=EvolutionState.from_dict(d.get("evolution_state", {}) or {}),
        )


def load_self_profile(paths: AgentPaths) -> Optional[SelfProfile]:
    """读取 self_profile.json；不存在时返回 None（由调用方决定是否调用
    ensure_self_profile 创建初始版本，与 load_project_meta 同构）。"""
    raw = _read_json(paths.global_self_profile, None)
    if raw is None or not isinstance(raw, dict):
        return None
    return SelfProfile.from_dict(raw)


def save_self_profile(paths: AgentPaths, profile: SelfProfile) -> None:
    _atomic_write_json(paths.global_self_profile, profile.to_dict())


def ensure_self_profile(paths: AgentPaths) -> SelfProfile:
    """
    首次创建时返回保守默认值（5.1 节：identity/self_assessment 留空，
    operating_state.autonomy_level 默认 "passive"——呼应 7.9 节"默认建议
    passive 起步"）；已存在时直接返回当前内容，不做任何修改（"更新
    operating_state"是 SessionEnd 维护机制的职责，见
    update_self_profile_on_session_end，不在 ensure_xxx 这一层做）。
    """
    existing = load_self_profile(paths)
    if existing is not None:
        return existing

    now = time.time()
    profile = SelfProfile(
        version=1,
        identity=SelfIdentity(
            purpose="",
            core_constraints_ref="",
            created_at=now,
        ),
        self_assessment=SelfAssessment(),
        operating_state=OperatingState(autonomy_level="passive"),
        resource_budget=ResourceBudget(),
        evolution_state=EvolutionState(),
    )
    save_self_profile(paths, profile)
    return profile


def update_self_profile_on_session_end(
    paths: AgentPaths,
    active_project: str,
    tokens_used: int = 0,
) -> SelfProfile:
    """
    SessionEnd hook 轻量路径（5.5 节）：更新 operating_state（
    last_active_at / total_sessions_lifetime / active_project）与
    resource_budget.used_today（跨自然日时清零，按 UTC 日历日比较，不
    依赖 budget_reset_at 精确到分钟——5.1 节明确"本 Stage 只做记录不做
    仲裁"，重置只需要近似正确，不需要引入额外的"上次重置时间"持久化
    字段）。

    纯计数器更新，无 LLM 依赖，每次无条件执行。total_projects_worked
    不在这里维护——它由 register_or_touch_project（5.2）在"项目数量"
    本身发生变化时同步更新，避免两处各自维护同一个统计口径产生分歧。
    """
    profile = ensure_self_profile(paths)
    now = time.time()
    previous_last_active = profile.operating_state.last_active_at

    profile.operating_state.last_active_at = now
    profile.operating_state.total_sessions_lifetime += 1
    if active_project:
        profile.operating_state.active_project = active_project

    if tokens_used:
        if _is_new_calendar_day(previous_last_active, now):
            profile.resource_budget.used_today = 0
        profile.resource_budget.used_today += max(0, int(tokens_used))

    save_self_profile(paths, profile)
    return profile


def _is_new_calendar_day(previous_ts: float, now: float) -> bool:
    """
    判断 now 与 previous_ts 是否落在不同的 UTC 日历日——用于
    resource_budget.used_today 的近似每日重置（精确到分钟的
    budget_reset_at 仲裁属于 Phase H 范畴，这里只需要"过了一天"这个
    粗粒度信号）。previous_ts<=0（从未记录过，例如 self_profile 刚创建）
    时视为"新的一天"，确保首次产生 tokens_used 时不会带着陈旧的
    used_today 基线起步。
    """
    if previous_ts <= 0:
        return True
    import datetime as _dt
    prev_date = _dt.datetime.fromtimestamp(previous_ts, tz=_dt.timezone.utc).date()
    now_date = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).date()
    return now_date != prev_date


# ════════════════════════════════════════════════════════════════════════════
# 5.2 projects_index.json — workdir 注册表
# ════════════════════════════════════════════════════════════════════════════

_DORMANT_AFTER_DAYS = 30.0


@dataclass
class ProjectIndexEntry:
    """对应设计文档 8.3 节 projects_index.json.projects[] schema。"""
    id: str
    path: str
    name: str
    first_seen: float = 0.0
    last_active: float = 0.0
    total_sessions: int = 0
    status: str = "active"      # "active" | "dormant"
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "first_seen": self.first_seen,
            "last_active": self.last_active,
            "total_sessions": self.total_sessions,
            "status": self.status,
            "description": self.description,
            "tags": list(self.tags),
        }

    @staticmethod
    def from_dict(d: dict) -> "ProjectIndexEntry":
        return ProjectIndexEntry(
            id=d.get("id", ""),
            path=d.get("path", ""),
            name=d.get("name", ""),
            first_seen=float(d.get("first_seen", 0.0) or 0.0),
            last_active=float(d.get("last_active", 0.0) or 0.0),
            total_sessions=int(d.get("total_sessions", 0) or 0),
            status=d.get("status", "active"),
            description=d.get("description", ""),
            tags=list(d.get("tags", []) or []),
        )


@dataclass
class ProjectsIndex:
    """projects_index.json 顶层容器（projects[] + active_project_id）。"""
    projects: list[ProjectIndexEntry] = field(default_factory=list)
    active_project_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "projects": [p.to_dict() for p in self.projects],
            "active_project_id": self.active_project_id,
        }

    @staticmethod
    def from_dict(d: dict) -> "ProjectsIndex":
        items = d.get("projects", []) or []
        return ProjectsIndex(
            projects=[ProjectIndexEntry.from_dict(p) for p in items if isinstance(p, dict)],
            active_project_id=d.get("active_project_id"),
        )


def load_projects_index(paths: AgentPaths) -> ProjectsIndex:
    raw = _read_json(paths.global_projects_index, {"projects": [], "active_project_id": None})
    if not isinstance(raw, dict):
        return ProjectsIndex()
    return ProjectsIndex.from_dict(raw)


def save_projects_index(paths: AgentPaths, index: ProjectsIndex) -> None:
    _atomic_write_json(paths.global_projects_index, index.to_dict())


def register_or_touch_project(
    paths: AgentPaths,
    project_root: Path,
    fallback_name: Optional[str] = None,
) -> ProjectIndexEntry:
    """
    session 启动时调用（5.2 节）：若当前 workdir 不在索引里则注册
    （first_seen/status="active"）；若已存在则更新 last_active/
    total_sessions，并把 status 重新置回 "active"（重新活跃的 dormant
    项目应该立刻"复活"，不需要等下一轮 30 天巡检）。

    同时把本次访问的项目设为 active_project_id，并在"项目数量真正增加"
    时同步更新 self_profile.json 的 total_projects_worked（与
    update_self_profile_on_session_end 各自维护不同的统计时机，
    互不重复计数：这里只在"新建项目"分支触达 self_profile，session 计数
    递增交给 update_self_profile_on_session_end）。
    """
    now = time.time()
    pid = project_id_for(project_root)
    index = load_projects_index(paths)

    for entry in index.projects:
        if entry.id == pid:
            entry.last_active = now
            entry.total_sessions += 1
            entry.status = "active"
            index.active_project_id = pid
            save_projects_index(paths, index)
            return entry

    entry = ProjectIndexEntry(
        id=pid,
        path=str(project_root.resolve()),
        name=fallback_name or project_root.resolve().name,
        first_seen=now,
        last_active=now,
        total_sessions=1,
        status="active",
    )
    index.projects.append(entry)
    index.active_project_id = pid
    save_projects_index(paths, index)

    # 项目数量增加：同步 self_profile.total_projects_worked
    try:
        profile = ensure_self_profile(paths)
        profile.operating_state.total_projects_worked = len(index.projects)
        save_self_profile(paths, profile)
    except Exception:
        pass

    return entry


def refresh_dormant_status(paths: AgentPaths, dormant_after_days: float = _DORMANT_AFTER_DAYS) -> int:
    """
    5.2 节"定期检查"：遍历全部已注册项目，`last_active` 超过
    dormant_after_days 天未更新的标记为 "dormant"。设计上不需要专门的
    后台任务——任意 session 启动时顺手跑一遍即可（O(项目数) 量级，足够
    轻量），调用时机由 agent.py 决定（与 register_or_touch_project 在
    同一次 SessionStart 路径里先后调用）。

    返回本次调用新标记为 dormant 的项目数量（供调用方/测试断言）。
    """
    now = time.time()
    threshold = dormant_after_days * 86400
    index = load_projects_index(paths)
    changed = 0
    for entry in index.projects:
        if entry.status == "active" and entry.last_active > 0 and (now - entry.last_active) > threshold:
            entry.status = "dormant"
            changed += 1
    if changed:
        save_projects_index(paths, index)
    return changed


# ════════════════════════════════════════════════════════════════════════════
# 5.3 activity_log.jsonl — 全局活动时序
# ════════════════════════════════════════════════════════════════════════════

def append_activity_log(
    paths: AgentPaths,
    project_id: str,
    session_id: str,
    theme: str,
    duration_min: float,
) -> None:
    """SessionEnd hook 调用：追加一行全局活动记录（设计文档 8.3 节 schema）。

    与 Stage 4.2 的 workdir_knowledge.append_timeline_entry 在同一处
    agent.py 代码路径里一起调用，避免两次遍历 session 数据（计划文档
    5.3 节要求）——本函数本身只负责"写一行"，不重复计算 theme/duration，
    调用方应该传入与 timeline.jsonl 同一次反思/计算得到的值。
    """
    record = {
        "at": time.time(),
        "project_id": project_id,
        "sid": session_id,
        "theme": theme,
        "duration_min": round(duration_min, 1),
    }
    _append_jsonl(paths.global_activity_log, record)


def load_recent_activity(paths: AgentPaths, limit: int = 10) -> list[dict]:
    """读取最近 limit 条全局活动记录（context 注入 / daemon 跨项目切换重建上下文用）。"""
    return _read_jsonl(paths.global_activity_log, limit=limit)


# ════════════════════════════════════════════════════════════════════════════
# 5.4 cross_project_index.json — 跨项目模式与能力图谱
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class CrossProjectPattern:
    """对应设计文档 8.3 节 cross_project_index.json.cross_project_patterns[] schema。"""
    id: str
    title: str
    observed_in_projects: list[str] = field(default_factory=list)
    occurrence_count: int = 0
    confidence: float = 0.0
    pattern_type: str = "best_practice"   # "risk" | "best_practice" | 其他自由分类
    derived_from_lessons: list[str] = field(default_factory=list)
    global_skill_candidate: bool = False
    promoted_to_skill: Optional[str] = None
    promoted_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "observed_in_projects": list(self.observed_in_projects),
            "occurrence_count": self.occurrence_count,
            "confidence": self.confidence,
            "pattern_type": self.pattern_type,
            "derived_from_lessons": list(self.derived_from_lessons),
            "global_skill_candidate": self.global_skill_candidate,
            "promoted_to_skill": self.promoted_to_skill,
            "promoted_at": self.promoted_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "CrossProjectPattern":
        return CrossProjectPattern(
            id=d.get("id", ""),
            title=d.get("title", ""),
            observed_in_projects=list(d.get("observed_in_projects", []) or []),
            occurrence_count=int(d.get("occurrence_count", 0) or 0),
            confidence=float(d.get("confidence", 0.0) or 0.0),
            pattern_type=d.get("pattern_type", "best_practice"),
            derived_from_lessons=list(d.get("derived_from_lessons", []) or []),
            global_skill_candidate=bool(d.get("global_skill_candidate", False)),
            promoted_to_skill=d.get("promoted_to_skill"),
            promoted_at=d.get("promoted_at"),
        )


@dataclass
class SkillPromotionRecord:
    """对应设计文档 8.3 节 cross_project_index.json.skill_promotion_history[] schema。"""
    skill_name: str
    promoted_from: str
    promoted_at: float
    trigger_pattern: str
    status: str = "active_global"

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "promoted_from": self.promoted_from,
            "promoted_at": self.promoted_at,
            "trigger_pattern": self.trigger_pattern,
            "status": self.status,
        }

    @staticmethod
    def from_dict(d: dict) -> "SkillPromotionRecord":
        return SkillPromotionRecord(
            skill_name=d.get("skill_name", ""),
            promoted_from=d.get("promoted_from", ""),
            promoted_at=float(d.get("promoted_at", 0.0) or 0.0),
            trigger_pattern=d.get("trigger_pattern", ""),
            status=d.get("status", "active_global"),
        )


@dataclass
class CrossProjectIndex:
    """cross_project_index.json 顶层容器。"""
    last_updated: float = 0.0
    cross_project_patterns: list[CrossProjectPattern] = field(default_factory=list)
    skill_promotion_history: list[SkillPromotionRecord] = field(default_factory=list)
    cross_project_capability_map: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "last_updated": self.last_updated,
            "cross_project_patterns": [p.to_dict() for p in self.cross_project_patterns],
            "skill_promotion_history": [r.to_dict() for r in self.skill_promotion_history],
            "cross_project_capability_map": dict(self.cross_project_capability_map),
        }

    @staticmethod
    def from_dict(d: dict) -> "CrossProjectIndex":
        patterns = d.get("cross_project_patterns", []) or []
        history = d.get("skill_promotion_history", []) or []
        return CrossProjectIndex(
            last_updated=float(d.get("last_updated", 0.0) or 0.0),
            cross_project_patterns=[
                CrossProjectPattern.from_dict(p) for p in patterns if isinstance(p, dict)
            ],
            skill_promotion_history=[
                SkillPromotionRecord.from_dict(r) for r in history if isinstance(r, dict)
            ],
            cross_project_capability_map=dict(d.get("cross_project_capability_map", {}) or {}),
        )


def load_cross_project_index(paths: AgentPaths) -> CrossProjectIndex:
    raw = _read_json(paths.global_cross_project_index, {})
    if not isinstance(raw, dict):
        return CrossProjectIndex()
    return CrossProjectIndex.from_dict(raw)


def save_cross_project_index(paths: AgentPaths, index: CrossProjectIndex) -> None:
    index.last_updated = time.time()
    _atomic_write_json(paths.global_cross_project_index, index.to_dict())


def _next_cross_project_pattern_id(patterns: list[CrossProjectPattern]) -> str:
    n = 1 + sum(1 for p in patterns if p.id.startswith("cpp_"))
    candidate = f"cpp_{n:03d}"
    existing_ids = {p.id for p in patterns}
    while candidate in existing_ids:
        n += 1
        candidate = f"cpp_{n:03d}"
    return candidate


def _tokenize_for_clustering(text: str) -> set[str]:
    """
    极简分词集合（用于 lesson 相似度聚类的 Jaccard 相似度计算），独立于
    memory_store.py 的 TF-IDF 分词——5.4 节的目标不是"检索排序"而是
    "判断两条 lesson 是否描述同一个模式"，用更简单的 token 集合重叠度
    （Jaccard）即可，不需要 IDF 权重（IDF 依赖一个固定语料库的统计量，
    跨项目聚类场景下"语料库"本身就是正在被构建的东西，引入 IDF 反而
    增加不必要的耦合）。中文按双字 n-gram 切分（与 memory_store.py
    的中文处理思路一致，避免逐字切分丢失语义），英文按单词。
    """
    text = (text or "").lower()
    tokens: set[str] = set()
    # 英文/数字单词
    for w in re.findall(r"[a-z0-9_]+", text):
        if len(w) >= 2:
            tokens.add(w)
    # 中文双字 n-gram
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        if len(run) == 1:
            tokens.add(run)
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    return tokens


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def scan_cross_project_patterns(
    project_roots: list[Path],
    similarity_threshold: float = 0.35,
    min_projects_for_candidate: int = 2,
    confidence_threshold_for_candidate: float = 0.7,
) -> list[CrossProjectPattern]:
    """
    5.4 节核心聚合函数：扫描各 workdir 的 memory.jsonl，按 lesson 的
    trigger 文本 Jaccard 相似度聚类，识别跨项目重复出现的模式。

    本 Stage 范围（计划文档 5.4 节）：只做"读取 + 聚合 + 落盘"，不做
    "自动触发 skill 晋升提案"（那一步需要 evolution-agent 的周期性扫描，
    属于 Phase G，留给 Stage 8）——因此本函数是纯函数，不接调度触发，
    调用时机由 Stage 8 的后台循环或人工手动调用决定。

    算法：
      1. 收集每个 workdir 的 memory.jsonl 里 entry_type="lesson" 的条目
      2. 用 trigger 文本的 token 集合做贪心聚类：与已有 cluster 代表条目
         的 Jaccard 相似度 >= similarity_threshold 时归入该 cluster，
         否则新建 cluster（代表条目 = 该 cluster 第一条加入的条目）
      3. 每个 cluster 只有 observed_in_projects（不同 workdir 数）>= 2 时
         才算"跨项目模式"（单项目内的重复不算跨项目，那是 workdir 内部
         的 occurrence_count 该解决的问题，不应该在这里重复建模）
      4. confidence 取 cluster 内全部 lesson confidence 的均值；
         global_skill_candidate = observed_in_projects 数量达到
         min_projects_for_candidate 且 confidence 超过
         confidence_threshold_for_candidate（呼应设计文档"晋升判据：
         observed_in_projects >= 2 且 confidence 超阈值"）

    返回值不包含 cluster 内只观察到 1 个项目的情况——这些不是"跨项目模式"，
    调用方若需要也可以直接读取各 workdir 自己的 memory.jsonl。
    """
    from mini_agent.perception.memory_store import MemoryEntry  # 延迟导入，避免循环依赖

    # ── 收集各 workdir 的 lesson 条目，记录其来源 project_id ────────────────
    @dataclass
    class _LessonRef:
        project_id: str
        entry: "MemoryEntry"

    lesson_refs: list[_LessonRef] = []
    for root in project_roots:
        pid = project_id_for(root)
        memory_path = AgentPaths(root).workdir_memory
        if not memory_path.is_file():
            continue
        try:
            for line in memory_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if data.get("entry_type") != "lesson":
                    continue
                try:
                    entry = MemoryEntry(**data)
                except Exception:
                    continue
                lesson_refs.append(_LessonRef(project_id=pid, entry=entry))
        except Exception:
            continue

    if not lesson_refs:
        return []

    # ── 贪心聚类（按 trigger 文本 Jaccard 相似度）───────────────────────────
    clusters: list[list[_LessonRef]] = []
    cluster_tokens: list[set[str]] = []
    for ref in lesson_refs:
        trigger_text = ref.entry.trigger or ref.entry.summary or ""
        tokens = _tokenize_for_clustering(trigger_text)
        if not tokens:
            continue
        placed = False
        for i, rep_tokens in enumerate(cluster_tokens):
            if _jaccard_similarity(tokens, rep_tokens) >= similarity_threshold:
                clusters[i].append(ref)
                placed = True
                break
        if not placed:
            clusters.append([ref])
            cluster_tokens.append(tokens)

    # ── 落地为 CrossProjectPattern（只保留跨 >=2 个项目的 cluster）──────────
    patterns: list[CrossProjectPattern] = []
    existing_ids: set[str] = set()
    for cluster in clusters:
        project_ids = sorted({r.project_id for r in cluster})
        if len(project_ids) < 2:
            continue
        confidences = [r.entry.confidence for r in cluster]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        # 用 cluster 里第一条 lesson 的 trigger（或 outcome 兜底）作为 title
        first = cluster[0].entry
        title = (first.trigger or first.outcome or first.summary or "未命名跨项目模式")[:120]

        n = 1 + len(existing_ids)
        pid = f"cpp_{n:03d}"
        while pid in existing_ids:
            n += 1
            pid = f"cpp_{n:03d}"
        existing_ids.add(pid)

        patterns.append(CrossProjectPattern(
            id=pid,
            title=title,
            observed_in_projects=project_ids,
            occurrence_count=len(cluster),
            confidence=round(avg_confidence, 4),
            pattern_type="risk" if any("risk" in (r.entry.tags or []) for r in cluster) else "best_practice",
            derived_from_lessons=[r.entry.entry_id for r in cluster],
            global_skill_candidate=(
                len(project_ids) >= min_projects_for_candidate
                and avg_confidence >= confidence_threshold_for_candidate
            ),
        ))

    return patterns


def merge_cross_project_patterns(
    paths: AgentPaths,
    new_patterns: list[CrossProjectPattern],
) -> CrossProjectIndex:
    """
    把 scan_cross_project_patterns() 的扫描结果合并进已有的
    cross_project_index.json：按 derived_from_lessons 集合的重叠度匹配
    已有 pattern（同一组 lesson 再次被扫描到时应该更新而非重复创建），
    不匹配时新建。已有 pattern 若被打上 promoted_to_skill（人工晋升
    审核通过后的产物），合并时保留该字段不被覆盖——扫描函数本身不知道
    "是否已晋升"，这个状态只能由晋升流程（Stage 8 范畴）写入，合并逻辑
    不应该把它冲掉。
    """
    index = load_cross_project_index(paths)
    existing_by_lessons: dict[frozenset, CrossProjectPattern] = {
        frozenset(p.derived_from_lessons): p for p in index.cross_project_patterns
    }

    merged: list[CrossProjectPattern] = []
    seen_keys: set[frozenset] = set()
    for new_p in new_patterns:
        key = frozenset(new_p.derived_from_lessons)
        seen_keys.add(key)
        existing = existing_by_lessons.get(key)
        if existing is not None:
            # 更新统计字段，保留晋升状态字段
            existing.title = new_p.title
            existing.observed_in_projects = new_p.observed_in_projects
            existing.occurrence_count = new_p.occurrence_count
            existing.confidence = new_p.confidence
            existing.pattern_type = new_p.pattern_type
            existing.global_skill_candidate = new_p.global_skill_candidate
            merged.append(existing)
        else:
            merged.append(new_p)

    # 保留扫描结果里没有覆盖到的旧条目（例如某次扫描时某个 workdir 暂时
    # 不可达，不应该让它凭空消失）
    for old_p in index.cross_project_patterns:
        key = frozenset(old_p.derived_from_lessons)
        if key not in seen_keys:
            merged.append(old_p)

    index.cross_project_patterns = merged
    save_cross_project_index(paths, index)
    return index


def update_cross_project_capability_map(
    paths: AgentPaths,
    capability_map: dict,
) -> CrossProjectIndex:
    """
    更新 cross_project_capability_map 并同步写回 self_profile.json 的
    confidence_by_domain（设计文档 8.3 节"形成闭环"）。本 Stage 暴露这个
    函数本身可被调用，但数据来源（各 workdir capability_map 的汇总）属于
    Stage 8（6.6 节）的产出，本 Stage 不臆造调用方——若调用方传入空字典，
    本函数仍然正确地"什么都不做"（不清空已有数据）。
    """
    index = load_cross_project_index(paths)
    if capability_map:
        index.cross_project_capability_map.update(capability_map)
        save_cross_project_index(paths, index)

        try:
            profile = ensure_self_profile(paths)
            profile.self_assessment.confidence_by_domain.update({
                k: v.get("confidence", 0.0) if isinstance(v, dict) else v
                for k, v in capability_map.items()
            })
            profile.self_assessment.last_assessed_at = time.time()
            save_self_profile(paths, profile)
        except Exception:
            pass
    return index


__all__ = [
    "project_id_for",
    "SelfIdentity",
    "SelfAssessment",
    "OperatingState",
    "ResourceBudget",
    "EvolutionState",
    "SelfProfile",
    "load_self_profile",
    "save_self_profile",
    "ensure_self_profile",
    "update_self_profile_on_session_end",
    "ProjectIndexEntry",
    "ProjectsIndex",
    "load_projects_index",
    "save_projects_index",
    "register_or_touch_project",
    "refresh_dormant_status",
    "append_activity_log",
    "load_recent_activity",
    "CrossProjectPattern",
    "SkillPromotionRecord",
    "CrossProjectIndex",
    "load_cross_project_index",
    "save_cross_project_index",
    "scan_cross_project_patterns",
    "merge_cross_project_patterns",
    "update_cross_project_capability_map",
]
