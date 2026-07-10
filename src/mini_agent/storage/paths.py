"""
storage/paths.py — 统一路径管理

所有文件路径都从这里取，不在各模块中硬编码。

作用域层次：
  Global（用户级）     ~/.agent/
  Workdir（项目级）    <project_root>/.agent/
  Session（会话级）    <project_root>/.agent/sessions/<session_id>/
  Task（任务级）       <project_root>/.agent/sessions/<session_id>/tasks/<task_id>/

使用方式：
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root=Path.cwd())

    # Workdir 级
    paths.workdir_memory          # .agent/memory.jsonl
    paths.permissions             # .agent/permissions.json
    paths.sessions_dir            # .agent/sessions/
    paths.cache_dir               # .agent/cache/

    # Workdir 知识层（W2，设计文档 8.2 节）
    paths.workdir_project_meta    # .agent/project.json
    paths.workdir_timeline        # .agent/timeline.jsonl
    paths.workdir_work_index      # .agent/work_index.json
    paths.workdir_open_threads    # .agent/open_threads.json
    paths.workdir_knowledge_md    # .agent/knowledge.md
    paths.workdir_knowledge_index # .agent/knowledge_index.json

    # Global 知识层（W3，设计文档 8.3 节）
    paths.global_self_profile        # ~/.agent/self_profile.json
    paths.global_projects_index      # ~/.agent/projects_index.json
    paths.global_cross_project_index # ~/.agent/cross_project_index.json
    paths.global_activity_log        # ~/.agent/activity_log.jsonl

    # Session 级（需要 session_id）
    paths.session_dir(sid)        # .agent/sessions/<sid>/
    paths.session_history(sid)    # .agent/sessions/<sid>/history.json
    paths.session_meta(sid)       # .agent/sessions/<sid>/meta.json
    paths.session_llm_debug(sid)  # .agent/sessions/<sid>/llm_debug.jsonl
    paths.session_memory_delta(sid) # .agent/sessions/<sid>/memory_delta.jsonl
    paths.session_plan_snapshot(sid) # .agent/sessions/<sid>/plan_snapshot.json

    # Task 级（需要 session_id + task_id）
    paths.task_dir(sid, tid)      # .agent/sessions/<sid>/tasks/<tid>/
    paths.task_output(sid, tid)   # .agent/sessions/<sid>/tasks/<tid>/output.log
    paths.task_events(sid, tid)   # .agent/sessions/<sid>/tasks/<tid>/events.jsonl
    paths.task_result(sid, tid)   # .agent/sessions/<sid>/tasks/<tid>/result.json
    paths.task_manifest(sid, tid) # .agent/sessions/<sid>/tasks/<tid>/manifest.json

    # Global 级
    paths.global_memory           # ~/.agent/memory.jsonl
    paths.global_dir              # ~/.agent/
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


# Global 目录名
_GLOBAL_DIR = ".agent"

# Workdir 目录名
_WORKDIR_DIR = ".agent"


class AgentPaths:
    """
    项目路径管理器。

    实例化时传入 project_root，之后所有路径都通过属性/方法获取，
    不再在各模块中拼接硬编码字符串。

    所有路径属性/方法只返回 Path 对象，不创建目录。
    需要确保目录存在时，调用 ensure_*() 方法，或直接 mkdir(parents=True, exist_ok=True)。
    """

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = (project_root or Path.cwd()).resolve()

    # ── Global 级 ──────────────────────────────────────────────────────────

    @property
    def global_dir(self) -> Path:
        """~/.agent/"""
        return Path.home() / _GLOBAL_DIR

    @property
    def global_memory(self) -> Path:
        """~/.agent/memory.jsonl — 全局记忆（跨项目通用经验）"""
        return self.global_dir / "memory.jsonl"

    @property
    def global_skills_dir(self) -> Path:
        """~/.agent/skills/ — 全局技能库"""
        return self.global_dir / "skills"

    @property
    def global_prompts_dir(self) -> Path:
        """~/.agent/prompts/ — 全局自定义 prompt 目录"""
        return self.global_dir / "prompts"

    # ── Global 知识层（W3，对应设计文档 8.3 节）──────────────────────────────
    # 命名延续 global_xxx 惯例（对齐已有的 global_memory / global_skills_dir）。

    @property
    def global_self_profile(self) -> Path:
        """~/.agent/self_profile.json — agent 自我模型（5.1，主语=agent 自己，
        与 profile.json 主语=用户 平行）"""
        return self.global_dir / "self_profile.json"

    @property
    def global_projects_index(self) -> Path:
        """~/.agent/projects_index.json — 曾经工作过的所有 workdir 注册表（5.2）"""
        return self.global_dir / "projects_index.json"

    @property
    def global_cross_project_index(self) -> Path:
        """~/.agent/cross_project_index.json — 跨项目模式与能力图谱（5.4）"""
        return self.global_dir / "cross_project_index.json"

    @property
    def global_activity_log(self) -> Path:
        """~/.agent/activity_log.jsonl — 全局活动时序流水（5.3）"""
        return self.global_dir / "activity_log.jsonl"

    # ── 全局错误日志 ───────────────────────────────────────────────────────
    # 与 session/task 级日志不同：错误日志与"当前在哪个项目/session"无关，
    # 需要跨项目、跨进程统一汇总到一处，便于事后排查，因此固定挂在 global_dir 下。

    @property
    def global_logs_dir(self) -> Path:
        """~/.agent/logs/ — 全局日志目录"""
        return self.global_dir / "logs"

    @property
    def global_error_log(self) -> Path:
        """~/.agent/logs/error.jsonl — 全局异常日志（JSON Lines）。
        每一行是一条独立的 JSON 记录，包含时间戳、pid、线程名、发生位置、
        异常类型、异常信息与完整堆栈，见 mini_agent.errors.log_exception()。
        按 10MB 一个文件轮转，保留最近 5 个（RotatingFileHandler）。"""
        return self.global_logs_dir / "error.jsonl"

    def ensure_global_logs_dir(self) -> Path:
        """确保 ~/.agent/logs/ 目录存在并返回路径。"""
        d = self.global_logs_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    def profile_path(self, user_id: Optional[str] = None) -> Path:
        """
        用户 profile 文件路径。

        当前为单用户模式：user_id=None 时返回 ~/.agent/profile.json。

        为后续多用户预留：传入 user_id 时返回
        ~/.agent/users/<user_id>/profile.json。届时只需在调用处传入
        实际的 user_id，无需改动 profile 的读写逻辑。
        """
        if user_id:
            return self.global_dir / "users" / user_id / "profile.json"
        return self.global_dir / "profile.json"

    # ── Workdir 级 ─────────────────────────────────────────────────────────

    @property
    def workdir_dir(self) -> Path:
        """<project_root>/.agent/"""
        return self.project_root / _WORKDIR_DIR

    @property
    def workdir_memory(self) -> Path:
        """<project_root>/.agent/memory.jsonl — 项目级记忆"""
        return self.workdir_dir / "memory.jsonl"

    @property
    def workdir_prompts_dir(self) -> Path:
        """<project_root>/.agent/prompts/ — 项目级自定义 prompt 目录"""
        return self.workdir_dir / "prompts"

    @property
    def permissions(self) -> Path:
        """<project_root>/.agent/permissions.json — 权限白名单/黑名单"""
        return self.workdir_dir / "permissions.json"

    # ── Workdir 知识层（W2，对应设计文档 8.2 节）────────────────────────────
    # 命名延续 workdir_xxx 惯例（对齐已有的 workdir_memory / workdir_prompts_dir）。

    @property
    def workdir_project_meta(self) -> Path:
        """<project_root>/.agent/project.json — 项目身份证（4.1）"""
        return self.workdir_dir / "project.json"

    @property
    def workdir_timeline(self) -> Path:
        """<project_root>/.agent/timeline.jsonl — session 时序骨架（4.2）"""
        return self.workdir_dir / "timeline.jsonl"

    @property
    def workdir_work_index(self) -> Path:
        """<project_root>/.agent/work_index.json — 跨 session WorkThread 聚合（4.3）"""
        return self.workdir_dir / "work_index.json"

    @property
    def workdir_open_threads(self) -> Path:
        """<project_root>/.agent/open_threads.json — 跨 session 待处理线索池（4.4）"""
        return self.workdir_dir / "open_threads.json"

    @property
    def workdir_knowledge_md(self) -> Path:
        """<project_root>/.agent/knowledge.md — 项目软知识积累（4.5，T1，走 StateRepo.apply()）"""
        return self.workdir_dir / "knowledge.md"

    @property
    def workdir_knowledge_index(self) -> Path:
        """<project_root>/.agent/knowledge_index.json — knowledge.md 的结构化索引
        （14.1 横向加固，与 4.5 同批完成；由 update_knowledge() 在写 Markdown 时
        顺手维护，不等待尚不存在的 evolution-agent 周期扫描）"""
        return self.workdir_dir / "knowledge_index.json"

    # ── 图书馆式知识索引（perception/classification.py, entity_index.py, catalog.py）──
    # 与 W2 的 project.json/work_index.json 同一定位：观察性数据，可重建，
    # 不经 StateRepo。命名延续 workdir_xxx 惯例。

    @property
    def workdir_classification_tree(self) -> Path:
        """.agent/classification_tree.json — 自动生长的分类树（书架结构）"""
        return self.workdir_dir / "classification_tree.json"

    @property
    def workdir_unclassified_candidates(self) -> Path:
        """.agent/unclassified_candidates.jsonl — 待归类候选（Phase G 批量处理后清空/归档）"""
        return self.workdir_dir / "unclassified_candidates.jsonl"

    @property
    def workdir_entity_index(self) -> Path:
        """.agent/entities.json — 实体卡片（模块/bug模式/概念等），含滚动摘要"""
        return self.workdir_dir / "entities.json"

    @property
    def workdir_category_catalog(self) -> Path:
        """.agent/category_catalog.json — 分类号 → entry_id 列表（指针索引，可重建）"""
        return self.workdir_dir / "category_catalog.json"

    @property
    def workdir_knowledge_timeline(self) -> Path:
        """.agent/knowledge_timeline.jsonl — 知识生命周期编年目录（发现→生成→巩固/推翻）"""
        return self.workdir_dir / "knowledge_timeline.jsonl"
    @property
    def workdir_cognitive_anchor(self) -> Path:
        """<project_root>/.agent/cognitive_anchor.md — 认知锚点文件（具身改进
        v3 C3）。任务被用户明确打断时（Ctrl-C / /stop）生成的"思维状态重建
        指南"——记录的是"当时在想什么"而不是"做了什么"（后者是 session
        历史的职责）。单文件、被新内容覆盖（不追加历史）：这是工作台上的
        便条，不是日志，只需要最新的一份。"""
        return self.workdir_dir / "cognitive_anchor.md"

    @property
    def sessions_dir(self) -> Path:
        """<project_root>/.agent/sessions/ — 所有 session 的根目录"""
        return self.workdir_dir / "sessions"

    @property
    def cache_dir(self) -> Path:
        """<project_root>/.agent/cache/ — 可安全删除的缓存"""
        return self.workdir_dir / "cache"

    @property
    def tool_cache(self) -> Path:
        """<project_root>/.agent/cache/tool_cache.json"""
        return self.cache_dir / "tool_cache.json"

    # ── Session 级 ─────────────────────────────────────────────────────────

    def session_dir(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/"""
        return self.sessions_dir / session_id

    def session_history(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/history.json
        完整对话历史（messages 数组）"""
        return self.session_dir(session_id) / "history.json"

    def session_meta(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/meta.json
        session 元信息（id, provider, model, started_at, ended_at, summary, stats）"""
        return self.session_dir(session_id) / "meta.json"

    def session_llm_debug(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/llm_debug.jsonl
        本 session 的 LLM 请求/响应调试日志"""
        return self.session_dir(session_id) / "llm_debug.jsonl"

    def session_memory_delta(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/memory_delta.jsonl
        本 session 产生的记忆条目（审计用）"""
        return self.session_dir(session_id) / "memory_delta.jsonl"

    def session_plan_snapshot(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/plan_snapshot.json
        ExecutionPlan 的持久化快照（W1，对应设计文档 8.1 节）。
        每次 PlanTask 状态变更时同步写入，session 意外中断后可据此恢复。"""
        return self.session_dir(session_id) / "plan_snapshot.json"

    def session_goal_state(self, session_id: str) -> Path:
        """[SYS-GOAL-MODE] .agent/sessions/<sid>/goal_state.json —— Goal 模式运行状态，
        用于进程异常中断后恢复（详见 goal_mode/state.py）。"""
        return self.session_dir(session_id) / "goal_state.json"

    def session_traces(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/traces.jsonl
        Stage 6.1：session 内各阶段时序追踪记录（build_system/call_llm/execute_tools/tool_call）。"""
        return self.session_dir(session_id) / "traces.jsonl"

    def tasks_dir(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/tasks/"""
        return self.session_dir(session_id) / "tasks"

    # ── 产出物 Manifest（Artifacts）────────────────────────────────────────
    # 用于「产出物看板」：命令行不便展示的文档/图片类产出，统一以 JSON 清单
    # 登记，看板据此渲染，而不是靠遍历目录猜测。详见 storage/artifacts.py。

    def session_artifacts_dir(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/artifacts/
        存放该 session 产生的每份产出物 manifest（manifest_<ts>_<slug>.json）。"""
        return self.session_dir(session_id) / "artifacts"

    def artifacts_index(self) -> Path:
        """<project_root>/.agent/artifacts_index.jsonl
        全局产出物索引，每行一条 manifest 摘要（追加写），供看板做
        「全部产出 / 最近产出」的快速聚合查询，避免遍历所有 session 目录。"""
        return self.workdir_dir / "artifacts_index.jsonl"

    # ── Task 级 ────────────────────────────────────────────────────────────

    def task_dir(self, session_id: str, task_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/tasks/<task_id>/"""
        return self.tasks_dir(session_id) / task_id

    def task_output(self, session_id: str, task_id: str) -> Path:
        """…/tasks/<task_id>/output.log
        SubAgent 实时输出流（tab 切换用）"""
        return self.task_dir(session_id, task_id) / "output.log"

    def task_events(self, session_id: str, task_id: str) -> Path:
        """…/tasks/<task_id>/events.jsonl
        SubAgent 生命周期事件（状态变更、重试等）"""
        return self.task_dir(session_id, task_id) / "events.jsonl"

    def task_result(self, session_id: str, task_id: str) -> Path:
        """…/tasks/<task_id>/result.json
        任务完成结果（token 统计、输出文本等）"""
        return self.task_dir(session_id, task_id) / "result.json"

    def task_manifest(self, session_id: str, task_id: str) -> Path:
        """…/tasks/<task_id>/manifest.json
        任务全生命周期的结构化叙事文件（W1，对应设计文档 8.1 节）。
        包含 goal/acceptance_criteria/progress/decision_log/outcome 等字段，
        由 agent 主动写入（update_task_progress 工具），不是从 events.jsonl 被动推导。"""
        return self.task_dir(session_id, task_id) / "manifest.json"

    # ── 便捷方法 ───────────────────────────────────────────────────────────

    def ensure_session_dir(self, session_id: str) -> Path:
        """确保 session 目录存在并返回路径。"""
        d = self.session_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ensure_task_dir(self, session_id: str, task_id: str) -> Path:
        """确保 task 目录存在并返回路径。"""
        d = self.task_dir(session_id, task_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ensure_workdir(self) -> Path:
        """确保 .agent/ 目录存在并返回路径。"""
        d = self.workdir_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ensure_global_dir(self) -> Path:
        """确保 ~/.agent/ 目录存在并返回路径。"""
        d = self.global_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Hooks / 自定义子 agent ────────────────────────────────────────────

    @property
    def global_hooks_config(self) -> Path:
        """~/.agent/hooks.json — 全局 hooks 配置"""
        return self.global_dir / "hooks.json"

    @property
    def project_hooks_config(self) -> Path:
        """<project_root>/.agent/hooks.json — 项目级 hooks 配置"""
        return self.workdir_dir / "hooks.json"

    @property
    def global_agents_dir(self) -> Path:
        """~/.agent/agents/ — 全局自定义子 agent 配置目录"""
        return self.global_dir / "agents"

    @property
    def project_agents_dir(self) -> Path:
        """<project_root>/.agent/agents/ — 项目级自定义子 agent 配置目录"""
        return self.workdir_dir / "agents"

    @property
    def global_personas_dir(self) -> Path:
        """~/.agent/personas/ — 全局角色扮演（persona）配置目录"""
        return self.global_dir / "personas"

    @property
    def global_persona_usage_log(self) -> Path:
        """~/.agent/persona_usage.jsonl — 角色扮演激活事件日志（跨项目全局统计）"""
        return self.global_dir / "persona_usage.jsonl"

    @property
    def project_personas_dir(self) -> Path:
        """<project_root>/.agent/personas/ — 项目级角色扮演（persona）配置目录"""
        return self.workdir_dir / "personas"

    @property
    def workdir_proxy_dir(self) -> Path:
        """<project_root>/.agent/proxy/ — 代理池状态目录(项目本地,不同项目/不同机器互不影响)"""
        return self.workdir_dir / "proxy"

    @property
    def workdir_proxy_sources_config(self) -> Path:
        """<project_root>/.agent/proxy/sources.json — 订阅源配置（可插拔多个订阅源）"""
        return self.workdir_proxy_dir / "sources.json"

    @property
    def workdir_proxy_available_list(self) -> Path:
        """<project_root>/.agent/proxy/available.json — 上一次 refresh 后验证通过的可用节点列表"""
        return self.workdir_proxy_dir / "available.json"

    @property
    def workdir_proxy_all_nodes_list(self) -> Path:
        """<project_root>/.agent/proxy/all_nodes.json — 订阅里解析出的全部节点(不管是否验证通过)"""
        return self.workdir_proxy_dir / "all_nodes.json"

    @property
    def workdir_proxy_log(self) -> Path:
        """<project_root>/.agent/proxy/proxy.log — 代理池刷新/验证过程日志"""
        return self.workdir_proxy_dir / "proxy.log"

    @property
    def workdir_proxy_discovered_sources(self) -> Path:
        """<project_root>/.agent/proxy/discovered_sources.json — 由 agent/skill 自动发现并
        追加写入的订阅源地址列表(和 sources.json 手动配置的分开存放,避免 skill 写入时
        跟用户手动维护的配置互相覆盖)。类型为 "discovered" 的 SubscriptionSource 会读取此文件。
        """
        return self.workdir_proxy_dir / "discovered_sources.json"

    @property
    def workdir_proxy_integration_config(self) -> Path:
        """<project_root>/.agent/proxy/integration.json — 代理池接入 mini_agent 其它模块的开关,
        每一路接入(主 LLM 请求 / web_search 抓取工具等)都默认关闭,需要用户显式打开。
        """
        return self.workdir_proxy_dir / "integration.json"

    def ensure_workdir_proxy_dir(self) -> Path:
        d = self.workdir_proxy_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    def __repr__(self) -> str:
        return f"AgentPaths(project_root={self.project_root})"


# ── 模块级便捷函数 ──────────────────────────────────────────────────────────

def get_paths(project_root: Optional[Path] = None) -> AgentPaths:
    """
    创建 AgentPaths 实例的便捷函数。
    在不方便传递 AppConfig 的场合使用。
    """
    return AgentPaths(project_root=project_root)
