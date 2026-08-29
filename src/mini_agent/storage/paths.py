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
    paths.workdir_work_thread_reminder  # .agent/work_thread_reminder.json（主动提醒暂存）
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
        self.project_root = (Path(project_root) if project_root else Path.cwd()).resolve()

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

    @property
    def global_http_access_log(self) -> Path:
        """~/.agent/logs/http_access.jsonl —— HTTP 请求访问日志（JSON Lines）。
        默认与 global_error_log 同目录，每一行记录一次请求的 method/path/
        状态码/耗时，用于事后排查 http server 卡顿等问题（见
        mini_agent.api.http_log）。可通过 HttpConfig.access_log_path 显式
        覆盖到别的路径，此属性只是"未显式配置时"的默认值。"""
        return self.global_logs_dir / "http_access.jsonl"

    @property
    def global_capability_debug_log(self) -> Path:
        """~/.agent/logs/capability_debug.jsonl —— generative-capability
        引擎全链路调试日志（JSON Lines），与 global_error_log 同目录、同
        轮转策略。由 mini_agent.skills.generative_capability.capability_debug
        统一写入，仅在 AppConfig.debug_capability（agent_config.json 的
        debug.capability_enabled）打开时才产生内容；关闭时不创建/不写入。
        记录范围覆盖 capability_call 调用链路（resolve/execute/explore/
        distill）以及各 skill 自己（如 browser-core 的 impl/ 代码）主动调用
        capability_debug_log() 写入的补充细节——skill 侧只管直接调用，是否
        真正落盘由这里的开关统一判断，skill 代码不需要关心开关状态。"""
        return self.global_logs_dir / "capability_debug.jsonl"

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

    # ── Wiki 式知识库（wiki式知识库重构计划.md 5.1 节）─────────────────────

    @property
    def wiki_dir(self) -> Path:
        """<project_root>/.agent/wiki/ — wiki 页面根目录"""
        return self.workdir_dir / "wiki"

    @property
    def wiki_entities_dir(self) -> Path:
        """<project_root>/.agent/wiki/entities/ — 实体型页面"""
        return self.wiki_dir / "entities"

    @property
    def wiki_decisions_dir(self) -> Path:
        """<project_root>/.agent/wiki/decisions/ — 决策型页面"""
        return self.wiki_dir / "decisions"

    @property
    def wiki_usage_log_path(self) -> Path:
        """<project_root>/.agent/wiki/usage_log.jsonl — wiki 检索命中埋点
        （外部知识反馈闭环计划 P2）：`wiki/search.py::wiki_shelf_search()`
        每次返回结果前追加一条记录（page_id 列表 + stage_reached + 时间戳），
        供 `evolution/wiki_utility_audit.py` 周期性聚合出"近期利用率"指标。
        只做追加写，不做读改写，不需要跨调用方的独占锁。"""
        return self.wiki_dir / "usage_log.jsonl"

    @property
    def wiki_quarantine_path(self) -> Path:
        """<project_root>/.agent/wiki/_quarantine.json — 解析失败页面的
        问题记录（`wiki/quarantine.py`）：`page_path -> QuarantineRecord`
        的整表 JSON（同类小文件参考 `usage_stats.json` 的写法，预期条目
        数量少，整表重写足够）。`wiki/stats.py::compute_stats()` /
        `wiki/indexer.py::build_index()` 遇到解析失败的页面时写入一条
        记录；`sys:wiki_quarantine_repair` cron job 周期性尝试修复。"""
        return self.wiki_dir / "_quarantine.json"

    # ── 主动推荐 / 日报 / 决策画像（次日议程改进计划）───────────────────────

    # ── Workflow Session（workflow机制改进计划.md P1）───────────────────────
    # 一次 run_workflow 执行 = 一个 workflow_session_id，该目录下聚合本次执行
    # 涉及的所有数据：执行元信息、增量落盘的 step 结果、结构化事件流、看护
    # 日志，以及每个 step 对应的 Agent 完整数据子目录（复用 cfg.session_dir
    # 覆盖机制，让 SessionManager 把该 step 的 session 直接创建在此目录下，
    # 不需要改动 SessionManager/AgentPaths 的 sessions_dir 默认拼接逻辑）。

    @property
    def workflow_sessions_dir(self) -> Path:
        """<project_root>/.agent/workflow_sessions/ — 所有工作流执行记录的根目录"""
        return self.workdir_dir / "workflow_sessions"

    def workflow_session_dir(self, workflow_session_id: str) -> Path:
        """<project_root>/.agent/workflow_sessions/<workflow_session_id>/"""
        return self.workflow_sessions_dir / workflow_session_id

    def workflow_session_meta(self, workflow_session_id: str) -> Path:
        """…/workflow_sessions/<wf_session_id>/session.json
        WorkflowSession 元信息：status/进度/control_flags，增量更新。"""
        return self.workflow_session_dir(workflow_session_id) / "session.json"

    def workflow_session_def_snapshot(self, workflow_session_id: str) -> Path:
        """…/workflow_sessions/<wf_session_id>/workflow_def.yaml
        执行开始时保存的工作流定义快照，防止运行中途原文件被修改导致
        resume 时用到不一致的定义。"""
        return self.workflow_session_dir(workflow_session_id) / "workflow_def.yaml"

    def workflow_session_step_results(self, workflow_session_id: str) -> Path:
        """…/workflow_sessions/<wf_session_id>/step_results.json
        增量落盘的 StepResult 集合，断点恢复用。"""
        return self.workflow_session_dir(workflow_session_id) / "step_results.json"

    def workflow_session_events(self, workflow_session_id: str) -> Path:
        """…/workflow_sessions/<wf_session_id>/events.jsonl
        结构化事件流：workflow_start/step_start/step_end/gate_failed/
        paused/resumed/cancelled/approval_requested/approved/rejected/
        workflow_end。"""
        return self.workflow_session_dir(workflow_session_id) / "events.jsonl"

    def workflow_session_watchdog_log(self, workflow_session_id: str) -> Path:
        """…/workflow_sessions/<wf_session_id>/watchdog.jsonl
        看护线程的心跳/超时/资源护栏告警记录。"""
        return self.workflow_session_dir(workflow_session_id) / "watchdog.jsonl"

    def workflow_step_agent_dir(self, workflow_session_id: str, agent_session_id: str) -> Path:
        """…/workflow_sessions/<wf_session_id>/<agent_session_id>/
        单个 step 对应的 Agent 完整数据目录（history/meta/traces/temp/output/
        artifacts），通过把该目录整体作为 cfg.session_dir 传给 SessionManager
        实现（SessionManager 会在其下再建一层 <session_id>/，因此调用方应把
        agent_session_id 同时用作 cfg.session_dir 的最后一级，具体见
        workflow/runner.py 的 _execute_with_main_agent）。"""
        return self.workflow_session_dir(workflow_session_id) / agent_session_id

    def ensure_workflow_session_dir(self, workflow_session_id: str) -> Path:
        """确保 workflow_session 目录存在并返回路径。"""
        d = self.workflow_session_dir(workflow_session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def workflow_session_output_dir(self, workflow_session_id: str) -> Path:
        """…/workflow_sessions/<wf_session_id>/output/

        本次 workflow 执行的"最终交付物"落地目录。用户在触发工作流时若未
        显式指定输出路径，任何需要落盘的产出（汇总报告、生成的文件等）都
        应该默认写到这里，而不是写到触发本次 workflow 的主 Agent 自己的
        session output 目录（.agent/sessions/<agent_session_id>/output/）——
        那是主 Agent 自己对话过程中的产出目录，与"这次 workflow 跑出来的
        东西"是两回事，混在一起会导致同一个 workflow 多次运行 / 多个
        workflow 交替运行时输出相互覆盖、难以追溯是哪次执行产生的文件。"""
        return self.workflow_session_dir(workflow_session_id) / "output"

    def ensure_workflow_session_output_dir(self, workflow_session_id: str) -> Path:
        """确保 workflow_session 的 output/ 目录存在并返回路径。"""
        d = self.workflow_session_output_dir(workflow_session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_workflow_session_ids(self) -> list[str]:
        """列举本项目下所有已存在的 workflow_session_id（按目录名，未做排序保证）。"""
        d = self.workflow_sessions_dir
        if not d.exists():
            return []
        return [p.name for p in d.iterdir() if p.is_dir()]

    @property
    def daily_reports_dir(self) -> Path:
        """<project_root>/.agent/daily_reports/ — 每日融合报告（行为+目标+提交）"""
        return self.workdir_dir / "daily_reports"

    def daily_report_path(self, day: str) -> Path:
        """<project_root>/.agent/daily_reports/<YYYY-MM-DD>.md"""
        return self.daily_reports_dir / f"{day}.md"

    @property
    def next_actions_path(self) -> Path:
        """<project_root>/.agent/next_actions.json — 主动推荐候选与展示状态"""
        return self.workdir_dir / "next_actions.json"

    @property
    def improvement_backlog_path(self) -> Path:
        """<project_root>/.agent/improvement_backlog.json — 改进信号聚合器
        （自诊断闭环深化 P1，next_doc/self_diagnosis_feedback_loop_deepening_plan.md）
        汇总 self_maintenance/gap_scanner/decommission/self_model 四路信号后
        产出的排序候选清单快照，由 evolution/improvement_backlog_merge.py 写入，
        只读消费。"""
        return self.workdir_dir / "improvement_backlog.json"

    @property
    def self_model_history_path(self) -> Path:
        """<project_root>/.agent/self_model_history.jsonl — 能力自画像时间序列
        快照（自诊断闭环深化 P3），每行一条 {at, capability_snapshot} 记录，
        由 evolution/self_model_snapshot.py 写入，保留最近 90 天。"""
        return self.workdir_dir / "self_model_history.jsonl"

    @property
    def user_value_profile_path(self) -> Path:
        """<project_root>/.agent/wiki/user_value_profile.md — 决策画像（归纳后的用户价值模式）"""
        return self.wiki_dir / "user_value_profile.md"

    @property
    def decision_profile_state_path(self) -> Path:
        """<project_root>/.agent/decision_profile_state.json — 决策画像归纳的运行状态（上次扫描时间等）"""
        return self.workdir_dir / "decision_profile_state.json"

    # ── 成长顾问 Growth Advisor（growth_advisor_design.md）────────────────

    @property
    def growth_backlog_path(self) -> Path:
        """<project_root>/.agent/growth_backlog.jsonl — GrowthCandidate 候选队列
        （每行一条 JSON，status: pending/accepted/dismissed/expired）。"""
        return self.workdir_dir / "growth_backlog.jsonl"

    @property
    def growth_reports_index_path(self) -> Path:
        """<project_root>/.agent/growth_reports.jsonl — GrowthReport 元数据索引，
        正文落在 wiki_growth_dir 下的 Markdown 文件。"""
        return self.workdir_dir / "growth_reports.jsonl"

    @property
    def growth_reports_archive_path(self) -> Path:
        """<project_root>/.agent/growth_reports.archive.jsonl — [P5-0]
        `compact_reports_index_storage()` 归档掉的"已被刷新替换、不再是
        任何候选当前挂着的那份"的旧 GrowthReport 记录。只追加，不参与
        `list_reports()`/`reports_needing_refresh()` 等运行时读取路径，
        纯粹是历史留痕，需要时可以手动查阅。"""
        return self.workdir_dir / "growth_reports.archive.jsonl"

    @property
    def growth_materials_index_path(self) -> Path:
        """<project_root>/.agent/growth_materials.jsonl —
        [growth_advisor_autonomous_search_and_material_improvement_
        plan.md 方向"报告与学习素材分层"] GrowthLearningMaterial 元数据
        索引，跟 `growth_reports_index_path`（决策向简报）平行但独立的
        一份索引——"报告"回答"值不值得投入"，"学习素材"回答"投入之后
        怎么学"，两者不是同一份文档的不同版本，是两种不同定位的产物，
        分开存储避免互相污染。正文同样落在 `wiki_growth_dir` 下的
        Markdown 文件（文件名带 `-material` 后缀区分）。"""
        return self.workdir_dir / "growth_materials.jsonl"

    @property
    def growth_feedback_ledger_path(self) -> Path:
        """<project_root>/.agent/growth_feedback_ledger.jsonl — 用户对候选/报告
        的采纳/忽略反馈流水，供研判层下次调整候选置信度。"""
        return self.workdir_dir / "growth_feedback_ledger.jsonl"

    @property
    def growth_state_path(self) -> Path:
        """<project_root>/.agent/growth_advisor_state.json — 成长顾问运行状态
        （上次信号扫描/候选生成/推送时间等节奏控制）。"""
        return self.workdir_dir / "growth_advisor_state.json"

    @property
    def growth_topic_trend_path(self) -> Path:
        """<project_root>/.agent/growth_topic_trend.jsonl — [P4-6] 每次
        `growth_candidate_derive()` 运行后按主题记录一条证据数/置信度快照
        （每行一条 JSON：scanned_at/topic/evidence_count/confidence），
        供看板画"证据数走势"这类简单趋势视图；只追加不改写，是
        `growth_backlog.jsonl`（当前状态）之外单独的历史序列。"""
        return self.workdir_dir / "growth_topic_trend.jsonl"

    @property
    def growth_health_trend_path(self) -> Path:
        """<project_root>/.agent/growth_health_trend.jsonl — [v4 N1]
        `run_daily_cycle()` 每轮结束时记一条全局健康度快照（字段只取自
        `diagnostics_snapshot()` 已经在展示的数字，不引入新的统计口径），
        供看板画"记忆总条数/待回填候选数/关注主题数"这类全局趋势折线图。
        跟 `growth_topic_trend_path`（单主题证据数走势）是平行但独立的
        只追加文件，同样从设计时起就预留降采样接口（见
        `_compact_health_trend_rows()`）。"""
        return self.workdir_dir / "growth_health_trend.jsonl"

    @property
    def growth_pursuit_saturation_trend_path(self) -> Path:
        """<project_root>/.agent/growth_pursuit_saturation_trend.jsonl —
        [growth_advisor_autonomy_deepening_plan_v2.md 方向 3] 每次
        `record_pursuit_cycle_signal()` 更新某个自主推进方向（Goal）的
        饱和度计数时，顺带追加一条降采样的历史记录（goal_id/recorded_at/
        low_increment/streak/saturated），供看板画"这个方向饱和之后，
        用户调整频率有没有用"这类简单走势。跟 `growth_health_trend_path`
        （全局健康度）/`growth_topic_trend_path`（单主题证据数）是平行
        但独立的只追加文件，同样是"按天降采样、旧数据自动压缩"模式（见
        `_compact_pursuit_saturation_trend_rows()`）。"""
        return self.workdir_dir / "growth_pursuit_saturation_trend.jsonl"

    @property
    def llm_call_stats_path(self) -> Path:
        """<project_root>/.agent/llm_call_stats.jsonl — [kanban_perception_gaps_
        improvement_plan.md 方向 B.2] 轻量 LLM 调用计数，跟 `llm/debug_logger.py`
        （默认关闭、记录完整请求/响应正文，用于调试排障）是两套独立的东西：
        这里默认开启，只记数字（provider/model/token 数/耗时/结果分类），不含
        任何请求/响应正文。近期记录是逐条原始记录，超过 `_RAW_WINDOW_DAYS`
        的旧记录会被压缩成按天聚合的汇总行（`llm/call_stats.py::
        compact_call_stats_storage()`），避免无限增长。"""
        return self.workdir_dir / "llm_call_stats.jsonl"

    @property
    def objective_completion_trend_path(self) -> Path:
        """<project_root>/.agent/objective_completion_trend.jsonl —
        [kanban_perception_gaps_improvement_plan.md 方向 D.1] Objective
        完成率每日快照（`objectives_completed_today` /
        `objectives_failed_today` / `avg_retry_count` /
        `active_goals_count`），供看板"📌 目标看板"Tab 画趋势折线图。
        用 `perception/daily_snapshot.py` 的通用"每日一条 + 按天降采样"
        存储小工具，跟 `growth_health_trend_path` 是平行但独立的只追加
        文件。"""
        return self.workdir_dir / "objective_completion_trend.jsonl"

    @property
    def wiki_growth_dir(self) -> Path:
        """<project_root>/.agent/wiki/growth/ — 成长顾问调研报告正文目录"""
        return self.wiki_dir / "growth"

    def growth_report_path(self, slug: str) -> Path:
        """…/wiki/growth/<slug>.md"""
        return self.wiki_growth_dir / f"{slug}.md"

    def growth_material_path(self, slug: str) -> Path:
        """…/wiki/growth/<slug>-material.md — 学习素材正文（跟同名候选的
        调研报告是两份独立文件，见 `growth_materials_index_path`）。"""
        return self.wiki_growth_dir / f"{slug}-material.md"

    @property
    def gating_history_path(self) -> Path:
        """<project_root>/.agent/gating_history.jsonl — ResourceArbiter 三态门控
        （full/degraded/blocked）状态变化流水（调度统一化 + 看板可观测性改进
        方案 P5），每行一条 {at, at_str, state, reason}，只在状态发生变化
        （相对上一条记录）时追加，由 evolution/resource_arbiter.py::
        record_gating_transition() 写入，仅保留最近 N 条（见该函数说明），
        供看板"🗓️ 全局日程" tab 的仲裁状态时间线展示。"""
        return self.workdir_dir / "gating_history.jsonl"

    @property
    def external_trend_capability_link_state_path(self) -> Path:
        """<project_root>/.agent/external_trend_capability_link_state.json —
        `sys:external_trend_capability_link`（外部知识 wiki 计划 P4）的运行状态
        （上次扫描时间、已产出候选的去重 key 集合），避免同一批"外部知识 ×
        能力薄弱点"组合每周重复产出草稿。"""
        return self.workdir_dir / "external_trend_capability_link_state.json"

    @property
    def external_trend_capability_candidates_path(self) -> Path:
        """<project_root>/.agent/wiki/external_trend_capability_candidates.md —
        `sys:external_trend_capability_link`（外部知识 wiki 计划 P4）产出的
        "外部技术趋势 × 自身能力薄弱点"候选草稿文档，人工审核用，不自动创建
        Goal、不自动修改代码。"""
        return self.wiki_dir / "external_trend_capability_candidates.md"

    @property
    def attention_mismatch_state_path(self) -> Path:

        """<project_root>/.agent/attention_mismatch_state.json — 注意力错配信号的
        首次发现时间/推送次数跟踪，供 next_action_advisor 的 daemon 主动推送判断
        "持续超过阈值时长"使用（避免每次扫描都重新计时导致误判）。"""
        return self.workdir_dir / "attention_mismatch_state.json"

    @property
    def cycle_patrol_state_path(self) -> Path:
        """<project_root>/.agent/cycle_patrol_state.json — 能力 C（主动巡检）
        的运行状态：上次巡检时间 `last_run_at` + 每个 recurring Goal 当前
        "命中中"的问题信号跟踪（`first_detected_at`/`last_pushed_at`/
        `push_count`），结构与 `attention_mismatch_state_path` 同构，见
        next_doc/goal_cron_cycle_proactive_patrol_and_health_overview_plan.md
        §2.1/§2.5。同时也是能力 D（全局健康总览）优先复用的数据源（§3.1）。
        """
        return self.workdir_dir / "cycle_patrol_state.json"

    @property
    def decision_candidates_pending_path(self) -> Path:
        """<project_root>/.agent/decision_candidates_pending.jsonl — 决策候选待批量落盘队列。

        compact 时只 append 到这里，真正的落盘（匹配/新建/推翻）延后到巩固循环
        （evolution/consolidation.py::consolidate_pending）批量执行，避免逐条即时
        落盘导致 wiki/decisions/ 碎片化。
        """
        return self.workdir_dir / "decision_candidates_pending.jsonl"

    @property
    def world_candidates_pending_path(self) -> Path:
        """<project_root>/.agent/world_candidates_pending.jsonl — 世界模型候选
        （entities[]/facts[]，wiki 改进计划 P1）待批量落盘队列。

        与 decision_candidates_pending_path 同一套节流模式：compact 时只
        append，真正的匹配/新建/合并延后到巩固循环
        （wiki/world_writer.py::consolidate_pending）批量执行。
        """
        return self.workdir_dir / "world_candidates_pending.jsonl"

    @property
    def extraction_stats_log(self) -> Path:
        """<project_root>/.agent/extraction_stats.jsonl — 每次结构化抽取批次
        （LLMSummaryStrategy 里与 compact 同一次 LLM 输出解析出的
        decisions/entities/facts）的数量记录（wiki 提取层改进计划 E2 方案B）。

        只用于观测 schema 顺序调整（decisions/entities/facts 提到
        compact_summary 之前）前后的抽取充分性对比，纯 append-only，
        不参与任何决策逻辑；写入失败静默跳过，不影响 compact 主流程。
        """
        return self.workdir_dir / "extraction_stats.jsonl"

    @property
    def extraction_cursor_path(self) -> Path:
        """<project_root>/.agent/extraction_cursor.json — 记录独立抽取触发器
        （history/extraction_trigger.py，wiki 提取层与组织层改进计划 E1
        §1.2.2）"抽取到 raw history 的第几条了"，避免同一段内容被反复抽取。

        与 compact 的 cursor（active history 里的位置）是两套独立坐标，
        本字段只针对 raw history（append-only、永不删减）计数。
        """
        return self.workdir_dir / "extraction_cursor.json"

    @property
    def extraction_trigger_log(self) -> Path:
        """<project_root>/.agent/extraction_trigger_log.jsonl — 抽取候选窗口
        探测记录（E1 §1.4）：先只记录候选窗口命中情况、不实际发起 LLM 调用，
        用真实数据校准触发阈值后再打开实际抽取开关，避免重蹈 P4"零数据
        切换"的教训。纯 append-only，写入失败静默跳过。
        """
        return self.workdir_dir / "extraction_trigger_log.jsonl"

    @property
    def wiki_processes_dir(self) -> Path:
        """<project_root>/.agent/wiki/processes/ — 流程型页面"""
        return self.wiki_dir / "processes"

    @property
    def wiki_experiences_dir(self) -> Path:
        """<project_root>/.agent/wiki/experiences/ — 经验型页面"""
        return self.wiki_dir / "experiences"

    @property
    def wiki_topics_dir(self) -> Path:
        """<project_root>/.agent/wiki/topics/ — 专题聚合页面"""
        return self.wiki_dir / "topics"

    @property
    def wiki_index_dir(self) -> Path:
        """<project_root>/.agent/wiki/_index/ — 脚本生成的派生索引，可随时删除重建"""
        return self.wiki_dir / "_index"

    @property
    def wiki_graph_index(self) -> Path:
        """<project_root>/.agent/wiki/_index/graph.json — 页面间链接图"""
        return self.wiki_index_dir / "graph.json"

    @property
    def wiki_tags_index(self) -> Path:
        """<project_root>/.agent/wiki/_index/tags.json — tag -> 页面列表"""
        return self.wiki_index_dir / "tags.json"

    @property
    def wiki_backlinks_index(self) -> Path:
        """<project_root>/.agent/wiki/_index/backlinks.json — 反向链接"""
        return self.wiki_index_dir / "backlinks.json"

    @property
    def wiki_search_index(self) -> Path:
        """<project_root>/.agent/wiki/_index/search_index.json — 关键词 + 向量粗筛索引"""
        return self.wiki_index_dir / "search_index.json"

    @property
    def wiki_promotion_log_path(self) -> Path:
        """<project_root>/.agent/wiki/_index/promotion_log.jsonl — wiki 转正评估

        每日快照日志（wiki 式知识库改进计划 P4）：source_kind 占比、校验错误数，
        用于判断是否满足"wiki 转正为主索引"的量化标准。属于可重建的观测记录，
        不是知识本身，但需要跨日累积，因此没有放进 _migration_map.json 那种
        单次持久状态里，而是独立开一个 jsonl 追加日志。
        """
        return self.wiki_index_dir / "promotion_log.jsonl"

    @property
    def wiki_search_ab_log_path(self) -> Path:
        """<project_root>/.agent/wiki/_index/search_ab_log.jsonl — wiki_search 与
        shelf_search 命中率 A/B 对比日志（wiki 式知识库改进计划 P4）。"""
        return self.wiki_index_dir / "search_ab_log.jsonl"

    @property
    def wiki_topics_run_counter_path(self) -> Path:
        """<project_root>/.agent/wiki/_index/topics_run_counter.json — topic

        巩固循环运行计数（wiki 提取层与组织层改进计划 O3）：记录
        `consolidate_topics()` 累计运行次数，用于按
        `TopicConfig.reconsolidation_interval_runs` 控制"再巩固扫描"的
        触发频率（不需要每次巩固循环都跑一遍全部已有 topic 页面）。
        属于可重建的运行时计数，不是知识本身，读写失败时静默降级为
        "本次不触发再巩固"，不影响新专题页生成这条主路径。"""
        return self.wiki_index_dir / "topics_run_counter.json"

    @property
    def wiki_topics_reconsolidation_log_path(self) -> Path:
        """<project_root>/.agent/wiki/_index/topics_reconsolidation_log.jsonl

        topic 再巩固事件日志（wiki 提取层与组织层改进计划 O3）：每次
        `append_to_topic_page()` 成功并入新成员时追加一条记录，供后续
        用真实数据校准 `TopicConfig.reconsolidation_interval_runs` 与重合度
        阈值，延续本改进计划一贯的"先观测、再调参"执行纪律。"""
        return self.wiki_index_dir / "topics_reconsolidation_log.jsonl"

    @property
    def wiki_decommission_report_path(self) -> Path:
        """<project_root>/.agent/wiki/_index/decommission_report.json — 旧图书馆索引
        （classification.py/entity_index.py/catalog.py）下线评估报告
        （next_doc/wiki_next_phase_improvement_plan.md 第 1 节）。

        由 wiki/decommission.py::check_and_plan() 写入，只读评估结果的快照，
        不是可执行的下线动作本身——是否真的下线仍需人工确认。可随时删除重建。
        """
        return self.wiki_index_dir / "decommission_report.json"

    @property
    def wiki_gap_scan_log_path(self) -> Path:
        """<project_root>/.agent/wiki/_index/gap_scan_log.jsonl — 知识缺口扫描历史
        （next_doc/wiki_next_phase_improvement_plan.md 第 4.2.3 / 5 节）。

        每次 `wiki/gap_scanner.py::scan_gaps()` 被 daemon cron job 调用时追加一条
        记录（发现了多少个缺口、是否触发了 --dispatch），供后续校准扫描频率与
        `max_results` 参数。append-only，可随时清空不影响后续扫描。
        """
        return self.wiki_index_dir / "gap_scan_log.jsonl"

    def wiki_type_dir(self, page_type: str) -> Path:
        """按 type 取对应目录，type 取值见 wiki.parser.PAGE_TYPES。"""
        mapping = {
            "entity": self.wiki_entities_dir,
            "decision": self.wiki_decisions_dir,
            "process": self.wiki_processes_dir,
            "experience": self.wiki_experiences_dir,
            "topic": self.wiki_topics_dir,
        }
        if page_type not in mapping:
            raise ValueError(f"未知的 wiki 页面类型: {page_type!r}，可选: {sorted(mapping)}")
        return mapping[page_type]

    def ensure_wiki_dirs(self) -> Path:
        """确保 wiki 全部子目录存在（含 _index/），返回 wiki_dir。"""
        for d in (
            self.wiki_entities_dir,
            self.wiki_decisions_dir,
            self.wiki_processes_dir,
            self.wiki_experiences_dir,
            self.wiki_topics_dir,
            self.wiki_index_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        return self.wiki_dir

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
    def workdir_work_thread_reminder(self) -> Path:
        """<project_root>/.agent/work_thread_reminder.json — work_index.json 主动提醒暂存。

        SessionEnd 时若启发式判断"本次 session 干了不少活，但既没关联到已有
        WorkThread、也没主动调用过 update_work_thread"，就把一条待提醒记录
        写在这里；下一次 session 开始时 context_builder 读到后注入一条提醒
        并立即清空该文件（提醒只出现一次，不会每个 turn 反复打扰）。
        只保留最近一条待提醒记录，不追加历史（这只是一个"便签"，不是审计
        日志——多个未追踪 session 连续发生时，只提醒最近一次即可）。
        """
        return self.workdir_dir / "work_thread_reminder.json"

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
        """.agent/unclassified_candidates.jsonl — 待归类候选（巩固循环 批量处理后清空/归档）"""
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
    def workdir_knowledge_timeline_index(self) -> Path:
        """.agent/knowledge_timeline_index.json — 改进6：实体/分类 -> 行号 的侧车索引，
        支持 catalog.load_timeline_for() 按实体/分类过滤读取而不必全文件扫描"""
        return self.workdir_dir / "knowledge_timeline_index.json"
    @property
    def workdir_cognitive_anchor(self) -> Path:
        """<project_root>/.agent/cognitive_anchor.md — [已废弃，仅保留属性
        避免外部代码直接访问时报 AttributeError] 认知锚点旧版存储位置
        （具身改进 v3 C3 的最初实现）。

        问题：这是 workdir 级单文件，任何一个新建/恢复的 session 在
        `_init_session()` 时都会读到它，与"锚点应该只属于留下它的那个具体
        session"的语义不符——例如 session-1 被打断留下锚点后，session-2
        （哪怕是完全不相关的任务）也会读到并消费掉它。

        现已改为 session 级存储：`<sessions_dir>/<session_id>/
        cognitive_anchor.md`（见 `agent/lifecycle.py::
        AgentLifecycleMixin._cognitive_anchor_path`），只在
        `load_session(session_id)` resume 到具体某个 session 时才检查
        该 session 自己目录下的锚点文件，天然不会跨 session 串味。
        """
        return self.workdir_dir / "cognitive_anchor.md"

    @property
    def proprioception_snapshot(self) -> Path:
        """<project_root>/.agent/proprioception_snapshot.json — 本体感知最新快照
        （具身改进 B1 与 Stage 9 ResourceArbiter 之间的信号桥接）。ProprioceptionModule
        是每个 Agent 实例内存中的状态，AutonomousLoop/ResourceArbiter 跑在 daemon
        后台 tick 里、不持有活跃 Agent 引用，因此需要落盘一份轻量快照供其读取。
        单文件、被覆盖（不追加历史）：只关心"最近一次感受"，不是日志。"""
        return self.workdir_dir / "proprioception_snapshot.json"

    @property
    def system_events(self) -> Path:
        """<project_root>/.agent/events.jsonl — 跨子系统事件日志（记忆/自我进化/
        具身感知之间的事件总线，见 perception/system_events.py）。

        与 proprioception_snapshot.json（单文件覆盖、只存"最近一次"）不同，
        这是追加写的日志：事件是一次性的边沿信号（"刚刚发生了什么"），覆盖写
        会丢掉还没被某个消费者读到的事件。多 session/多进程可能并发追加，
        写入必须走 system_events.py 里的跨平台文件锁，不能直接 open(...,'a')。"""
        return self.workdir_dir / "events.jsonl"

    @property
    def system_events_archive_dir(self) -> Path:
        """<project_root>/.agent/events_archive/ — events.jsonl 按天滚动归档目录，
        避免主日志无限增长（滚动逻辑见 system_events.py::rotate_if_needed()）。"""
        return self.workdir_dir / "events_archive"

    @property
    def system_events_cursors_dir(self) -> Path:
        """<project_root>/.agent/event_cursors/ — 每个消费者各自的读取游标
        ({consumer_name}.json: last_event_id / last_ts)，重启后不重复处理，
        也不漏读——与 rhythm.json/self_maintenance_state.json 同源的"小状态文件"模式。"""
        return self.workdir_dir / "event_cursors"

    @property
    def external_input_dir(self) -> Path:
        """<project_root>/.agent/external_input/ — External Input Gateway 的配置与
        私有状态根目录（见 next_doc/external_input_gateway_design.md §4）。事件本身
        不落在这里，仍然复用 system_events.jsonl；这里只放网关自己的配置/游标/告警。"""
        return self.workdir_dir / "external_input"

    @property
    def external_input_sources_config(self) -> Path:
        """<project_root>/.agent/external_input/sources.yaml — 已注册 source 的配置
        （id/type/params/interval_seconds/enabled），P2 的 GatewayPoller 读取。"""
        return self.external_input_dir / "sources.yaml"

    @property
    def external_input_policies_config(self) -> Path:
        """<project_root>/.agent/external_input/policies.yaml — IngestionPolicy 路由
        规则，P3 的路由决策读取。"""
        return self.external_input_dir / "policies.yaml"

    @property
    def external_input_watchlist_config(self) -> Path:
        """<project_root>/.agent/external_input/watchlist.yaml — 用户关注对象配置
        （关键词/汇报 tier），见
        next_doc/watchlist_notification_goal_design.md §3.1，WatchlistMatcher（P2）读取。"""
        return self.external_input_dir / "watchlist.yaml"

    @property
    def external_input_pending_hits(self) -> Path:
        """<project_root>/.agent/external_input/pending_hits.jsonl — 关注对象命中后
        待各 tier 汇报消费的队列，见 §3.4，WatchlistMatcher 写入、
        report_tiers（P3）消费。"""
        return self.external_input_dir / "pending_hits.jsonl"

    @property
    def external_input_goal_relevance_candidates(self) -> Path:
        """<project_root>/.agent/external_input/goal_relevance_candidates.jsonl —
        GoalRelevanceEngine Stage①（规则层）写入的候选队列，Stage②（LLM
        批量判定）消费，见 next_doc/watchlist_notification_goal_design.md
        §3.6/§4.2（P4/P5）。"""
        return self.external_input_dir / "goal_relevance_candidates.jsonl"

    @property
    def external_input_relevance_threshold_state(self) -> Path:
        """<project_root>/.agent/external_input/relevance_threshold_state.json —
        `evolution/relevance_threshold_calibration.py`（P3）持久化的当前
        校准阈值/调整历史，见
        next_doc/external_knowledge_feedback_loop_improvement_plan.md §3 P3。
        跟 goal_relevance_candidates.jsonl 同目录但独立文件，不共享锁。"""
        return self.external_input_dir / "relevance_threshold_state.json"

    @property
    def external_input_state_dir(self) -> Path:
        """<project_root>/.agent/external_input/state/ — 每个 source 的增量状态
        ({source_id}.json：去重游标/ETag 等），来源私有，网关负责落盘保存。"""
        return self.external_input_dir / "state"

    @property
    def external_input_gateway_dedup_cache(self) -> Path:
        """<project_root>/.agent/external_input/state/gateway_dedup_cache.json —
        `gateway.py::_RecentIdCache` 的轻量快照持久化（见
        next_doc/external_input_reliability_observability_archive_plan.md
        §1）。跟各 source 的 state 文件同级：这份缓存只是"同一进程内防止
        手滑重复发布"的最后一道保险，权威去重仍然是各 source 自己的游标，
        不追求强一致，节流写、允许在异常崩溃时丢最多几十秒的数据。"""
        return self.external_input_state_dir / "gateway_dedup_cache.json"

    @property
    def external_input_poll_history(self) -> Path:
        """<project_root>/.agent/external_input/state/poll_history.jsonl —
        每次 GatewayPoller 调用 source.poll() 后追加一条精简记录
        （ok/duration_ms/event_count/error），供
        `external_input/poll_history.py::summarize_poll_history()` 做
        成功率/延迟趋势聚合查询（见改造方案 §3）。只追加、有滚动上限，
        跟 dispatch_log.jsonl 的处理方式一致。"""
        return self.external_input_state_dir / "poll_history.jsonl"

    @property
    def external_input_novelty_candidates_raw(self) -> Path:
        """<project_root>/.agent/external_input/novelty_candidates_raw.jsonl —
        NoveltyJudge Stage①（规则粗筛）写入的候选队列，Stage②（LLM 批量
        重要性判定）消费，见改造方案 §2.3/§2.4。跟
        goal_relevance_candidates.jsonl 是完全独立的队列/游标/文件。"""
        return self.external_input_dir / "novelty_candidates_raw.jsonl"

    @property
    def external_input_alerts(self) -> Path:
        """<project_root>/.agent/external_input/alerts.jsonl — notify_only 落点的
        持久化记录，供 /v1/inbox 与看板"全局待办中心"查询（P3 起用）。"""
        return self.external_input_dir / "alerts.jsonl"

    @property
    def external_input_tech_radar_state(self) -> Path:
        """<project_root>/.agent/external_input/state/tech_radar_search_state.json —
        `sys:tech_radar_search`（外部知识 wiki 计划 P3）的种子轮转游标
        （{"offset": int, "last_run_id": str, "last_run_at": float}），
        跟其它 source 的增量状态同级存放，不是事件游标（没有
        `perception/system_events.py` 消费者），所以单独一个小状态文件，
        不复用 `poll_since()` 的游标机制。"""
        return self.external_input_state_dir / "tech_radar_search_state.json"

    @property
    def external_input_ecosystem_positioning_state(self) -> Path:
        """<project_root>/.agent/external_input/state/ecosystem_positioning_scan_state.json —
        `sys:ecosystem_positioning_scan`（外部知识反馈闭环计划 P4）的种子
        轮转游标，跟 `external_input_tech_radar_state` 同构但独立文件——
        两个模块各自的种子池/轮转节奏完全独立，不共享游标，见
        next_doc/external_knowledge_feedback_loop_improvement_plan.md §3 P4。"""
        return self.external_input_state_dir / "ecosystem_positioning_scan_state.json"

    @property
    def monthly_trend_retrospective_state_path(self) -> Path:
        """<project_root>/.agent/monthly_trend_retrospective_state.json —
        `sys:monthly_trend_retrospective`（外部知识反馈闭环计划 P5）的运行
        状态：上次运行时间、上一轮的 wiki `by_source_kind` 快照、上一轮的
        capability_map 置信度快照（domain -> confidence），供下一轮计算
        环比增量用，不依赖任何专门的历史时间序列存储。"""
        return self.workdir_dir / "monthly_trend_retrospective_state.json"

    @property
    def monthly_trend_retrospective_dir(self) -> Path:
        """<project_root>/.agent/wiki/monthly_trend_retrospective/ —
        `sys:monthly_trend_retrospective`（外部知识反馈闭环计划 P5）产出的
        月度回顾文档目录，每月一份（`<YYYY-MM>.md`），人工审核用，供
        `decision_profile_update`/`soft_goal_deriver` 参考，不自动创建
        Goal、不自动修改代码。"""
        return self.wiki_dir / "monthly_trend_retrospective"

    def monthly_trend_retrospective_path(self, month: str) -> Path:
        """…/wiki/monthly_trend_retrospective/<YYYY-MM>.md"""
        return self.monthly_trend_retrospective_dir / f"{month}.md"

    @property
    def notification_dir(self) -> Path:
        """<project_root>/.agent/notification/ — 通知系统（渠道配置、分级汇报
        tier 配置）的根目录，见 next_doc/watchlist_notification_goal_design.md
        §3.2/§3.3。跟 external_input_dir 分开存放，因为通知渠道不是
        External Input Gateway 专属——以后网关之外的模块也可能复用
        NotificationDispatcher（比如目标完成提醒）。"""
        return self.workdir_dir / "notification"

    @property
    def notification_config(self) -> Path:
        """<project_root>/.agent/notification/config.yaml — 通知渠道配置
        （kanban/email 等），见 §3.3。"""
        return self.notification_dir / "config.yaml"

    @property
    def notification_report_tiers_config(self) -> Path:
        """<project_root>/.agent/notification/report_tiers.yaml — 分级汇报 tier
        配置（P3 新增），见 next_doc/watchlist_notification_goal_design.md
        §3.2。daemon 启动时按这份配置动态补注册
        sys:watchlist_report_<tier_id> cron job。"""
        return self.notification_dir / "report_tiers.yaml"

    @property
    def notification_tier_state(self) -> Path:
        """<project_root>/.agent/notification/tier_state.json — 每个 tier 的
        运行时状态（连续空转计数，用于 §9.2 #7 高频 tier 空转节流），
        P3 新增，纯运维态数据，不属于用户配置。"""
        return self.notification_dir / "tier_state.json"

    @property
    def notification_reports(self) -> Path:
        """<project_root>/.agent/notification/reports.jsonl — watchlist_report
        分级汇报的落地记录（与网关 notify_only 的 external_input_alerts.jsonl
        彻底分开存储）。KanbanChannel 写入、看板"关注与通知"tab 的"📋 待处理
        汇报"面板读取/ack，走独立的 /v1/notifications/* 端点，不再混进
        /v1/inbox 全局待办中心，也不再跟外部输入网关共用同一份文件。"""
        return self.notification_dir / "reports.jsonl"

    @property
    def notification_novelty_candidates(self) -> Path:
        """<project_root>/.agent/notification/novelty_candidates.jsonl —
        NoveltyJudge Stage②（LLM 批量重要性判定）产出的、`importance=="high"`
        的候选，等待人工确认（见改造方案
        next_doc/external_input_reliability_observability_archive_plan.md
        §2.5）。确认（confirm）才会调用 GoalBacklog.add_goal() 创建新
        Goal，是唯一允许创建新 Goal 的入口；忽略（dismiss）只标记状态，
        不做任何执行动作。跟 reports.jsonl/dispatch_log.jsonl 一样独立
        存放，不跟任何既有队列共用。"""
        return self.notification_dir / "novelty_candidates.jsonl"

    @property
    def notification_cron_questions(self) -> Path:
        """<project_root>/.agent/notification/cron_questions.jsonl — cron 任务
        异步用户反馈问答记录（见
        next_doc/cron_async_user_feedback_mechanism_plan.md）。跟
        reports.jsonl 是两回事：reports 是只读汇报 + 已读标记，这份文件是
        **双向问答**——ask_user_async 工具写入待回答问题，用户在看板上提交/
        修改答案也写回这份文件（`answer_history` 保留每次修改），下次对应
        cron job 触发时 `CronJobWorkspace.render_prompt()` 会读取已回答但
        未消费过的记录注入 prompt。独立存放，不跟 reports.jsonl 共用。"""
        return self.notification_dir / "cron_questions.jsonl"

    @property
    def notification_dispatch_log(self) -> Path:
        """<project_root>/.agent/notification/dispatch_log.jsonl — 每次
        NotificationDispatcher.dispatch() 的发送结果记录（P7 新增，见
        next_doc/watchlist_notification_goal_design.md §6 P7），供看板
        "通知发送记录"面板只读展示。跟 external_input_alerts 是两回事：
        alerts.jsonl 只有 kanban 渠道落地成功才会有一条；这份文件记录的是
        **每个渠道各自的发送结果**（包括失败的邮件等），用于诊断"为什么
        我没收到邮件通知"这类问题。只追加、有上限截断（见
        `notification/dispatcher.py::_append_dispatch_log`），不是需要
        精确对账的审计日志。"""
        return self.notification_dir / "dispatch_log.jsonl"

    @property
    def notification_daemon_crash_alerts(self) -> Path:
        """<project_root>/.agent/notification/daemon_crash_alerts.jsonl —
        daemon 崩溃告警的独立存储，见
        next_doc/daemon_crash_recovery_and_alert_plan.md §3.2。刻意跟
        reports.jsonl（周期性汇报，可批量已读、可慢慢看）彻底分开：崩溃
        告警时效性强、不该被淹没在常规通知列表里，看板走专门的常驻横幅
        展示，不是"关注与通知"tab 下的一个分类筛选项。"""
        return self.notification_dir / "daemon_crash_alerts.jsonl"

    # ── 长期归档（archive.gc，改造方案 §4）─────────────────────────────────
    # 热文件里"已处理超过 retention_hours"的记录按自然月迁出到这里，只追加、
    # 视为只读；跟 sessions_dir 平级，不挂在任何具体模块目录下面，因为
    # archive 是一个横切多个模块的通用能力。

    @property
    def archive_dir(self) -> Path:
        """<project_root>/.agent/archive/ — 长期归档根目录，按
        `<subdir>/<file_stem>-YYYY-MM.jsonl` 分片存放（见
        `mini_agent/archive/gc.py`）。"""
        return self.workdir_dir / "archive"

    def archive_file(self, subdir: str, file_stem: str, year_month: str) -> Path:
        """<project_root>/.agent/archive/<subdir>/<file_stem>-<year_month>.jsonl
        `year_month` 形如 "2026-06"。"""
        return self.archive_dir / subdir / f"{file_stem}-{year_month}.jsonl"

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

    # ── 通用异步任务（kanban_async_job_mechanism_plan.md）────────────────────
    # LLM/长耗时调用在 HTTP 路由里的标准接入方式：不再同步阻塞 handler，
    # 而是丢进后台任务，job 记录落盘在这里，daemon 重启/浏览器刷新后仍可
    # 从磁盘查到"上一次调用还在跑/已完成/失败了"。不放进 cache_dir（cache
    # 语义是"可安全删除"，而 job 结果在用户看到之前不应该被随便清掉）。

    @property
    def async_jobs_dir(self) -> Path:
        """<project_root>/.agent/async_jobs/ — 通用异步任务落盘目录"""
        return self.workdir_dir / "async_jobs"

    def async_job_record(self, job_id: str) -> Path:
        """<project_root>/.agent/async_jobs/<job_id>.json — 单个任务的状态记录"""
        return self.async_jobs_dir / f"{job_id}.json"

    @property
    def async_jobs_latest_dir(self) -> Path:
        """<project_root>/.agent/async_jobs/latest_by_key/ — 每个业务 key
        （如 "execution_spec_generate:{goal_id}"）指向"最近一次任务 id"的
        指针文件目录，供前端在丢失 session_state 后仍能找回上一次任务。"""
        return self.async_jobs_dir / "latest_by_key"

    def async_job_latest_pointer(self, key: str) -> Path:
        import hashlib
        safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]
        return self.async_jobs_latest_dir / f"{safe}.json"

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

    def session_notepad(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/notepad.json
        本 session 的记事本（关键信息/结果/注意事项），常驻 system prompt，
        不受 history compact 影响。"""
        return self.session_dir(session_id) / "notepad.json"

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

    # ── Session 级临时/输出目录 ───────────────────────────────────────────
    # 用户未显式指定目标目录时的默认落地位置：
    #   temp   — 临时文件、一次性脚本、中间产物，任务结束后可随时清空
    #   output — 明确作为最终交付物的文件（报告/代码/文档等）
    # 两者在 session 初始化时（_init_session / load_session / new_session，
    # 见 agent/lifecycle.py::_bind_session_extras）就自动创建好，
    # 后续 agent 可以直接引用，无需每次现建。

    def session_temp_dir(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/temp/
        存放临时文件、一次性脚本、中间产物。未指定目标目录时的默认落地位置，
        任务结束后可随时清空，不影响正式产出。"""
        return self.session_dir(session_id) / "temp"

    def session_output_dir(self, session_id: str) -> Path:
        """<project_root>/.agent/sessions/<session_id>/output/
        存放明确作为最终交付物的文件（报告/代码/文档/生成结果等）。
        未指定目标目录、但用户明确要求"保存/生成/输出一个文件"时的默认落地位置。"""
        return self.session_dir(session_id) / "output"

    def ensure_session_working_dirs(self, session_id: str) -> tuple[Path, Path]:
        """确保 session 的 temp/ 与 output/ 目录都存在，返回 (temp_dir, output_dir)。

        在 session 初始化时调用一次即可，后续可直接假定这两个目录已存在。
        """
        self.ensure_session_dir(session_id)
        temp_dir = self.session_temp_dir(session_id)
        output_dir = self.session_output_dir(session_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir, output_dir

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

    # ── 人设能力自主学习 Capability Learning（persona_capability_learning_design.md）──

    @property
    def capability_tracks_path(self) -> Path:
        """<project_root>/.agent/capability_tracks.json — 全部 CapabilityTrack 列表
        （knowledge 型 / persona 型均落在同一份文件，用 target_type 区分），
        量级不大，参照 cron_jobs.json 单文件存法，不做分文件。"""
        return self.workdir_dir / "capability_tracks.json"

    def capability_ledger_path(self, track_id: str) -> Path:
        """<project_root>/.agent/capability_ledger/<track_id>.jsonl — 单个 Track
        的学习台账（每行一条 CapabilityLedgerEntry），供看板进度展示与月度复盘引用。"""
        d = self.workdir_dir / "capability_ledger"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{track_id}.jsonl"

    @property
    def capability_questions_path(self) -> Path:
        """<project_root>/.agent/capability_questions.jsonl — CapabilityQuestion
        异步问答队列，追加写；status: pending/answered/dismissed/expired，
        看板按 status/track_id 过滤展示。"""
        return self.workdir_dir / "capability_questions.jsonl"

    @property
    def capability_outline_suggestions_path(self) -> Path:
        """<project_root>/.agent/capability_outline_suggestions.jsonl —
        v0.21 §13.2-f 大纲动态生长建议队列（OutlineSuggestion）。用户在
        回答异步问题时提到大纲之外的新关注点，LLM 提炼出的"要不要加进
        大纲"建议存在这里，供看板/CLI 采纳或忽略。风格对齐
        capability_questions_path：整体读出/内存改/整体写回，量级不大。
        """
        return self.workdir_dir / "capability_outline_suggestions.jsonl"

    def capability_notify_state_path(self) -> Path:
        """<project_root>/.agent/capability_notify_state.json — v0.21 §8
        通知接入的按天节流状态（last_notify_date/notify_count_today），
        风格对齐 growth_advisor 的 growth_state_path，但独立存储，不与
        GrowthAdvisorConfig 的节流状态混用（两套 notification_frequency
        配置本来就互相独立，见 CapabilityLearningConfig 字段注释）。
        """
        return self.workdir_dir / "capability_notify_state.json"

    def capability_persona_draft_path(self, track_id: str) -> Path:
        """<project_root>/.agent/capability_persona_drafts/<track_id>.md —
        persona 型 Track（target_type="persona"）的人设草稿（§10.3）。
        草稿不是 .agent/personas/*.md 的正式角色文件，只是"合成结果的
        预览态"——用户点"发布"后才会被复制/写入正式 personas 目录，
        这一步必须是显式用户动作（见设计文档 §10.3 第 4 点）。"""
        d = self.workdir_dir / "capability_persona_drafts"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{track_id}.md"

    def __repr__(self) -> str:
        return f"AgentPaths(project_root={self.project_root})"


# ── 模块级便捷函数 ──────────────────────────────────────────────────────────

def get_paths(project_root: Optional[Path] = None) -> AgentPaths:
    """
    创建 AgentPaths 实例的便捷函数。
    在不方便传递 AppConfig 的场合使用。
    """
    return AgentPaths(project_root=project_root)
