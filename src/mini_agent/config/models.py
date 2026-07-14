"""
config/models.py — 配置数据模型（dataclass 定义）

拆分自原 config.py（v3，对应 self_evolution_implementation_plan.md Stage 0.4）。
本文件只放纯数据结构，不含任何加载/解析逻辑：
  - SessionStats        — 运行时统计（不是配置，但历史上一起放在 config.py）
  - 14 个功能子配置块（MemoryConfig/CompressConfig/.../EnvInfoConfig）
  - AppConfig           — 应用主配置，聚合所有子配置块 + 向后兼容 property

加载逻辑（load_config 及其辅助函数）在 config/loader.py；
system prompt 构建逻辑（build_system_prompt 及其辅助函数）在 config/prompt_builder.py。
外部代码应继续 `from mini_agent.config import AppConfig` 等，由 config/__init__.py 统一重导出，
不需要关心内部按文件拆分的细节。

重构（v2）：功能开关从平坦字段改为子配置块（Feature Config）。
- 每个功能模块对应一个独立的 @dataclass，聚合其开关 + 参数
- AppConfig 主体只持有核心字段 + 各功能块的引用
- 新增功能只需新建子配置类，AppConfig 主体不变
- 向后兼容：load_config / agent_config.json 仍使用平坦 key，内部组装为子块

子配置块列表：
  MemoryConfig        — 跨 session 长期记忆
  CompressConfig      — 自动上下文压缩
  ToolTrimConfig      — 工具结果截断
  SkillConfig         — Skill 系统
  PerceptionConfig    — 项目感知（扫描/监听/缓存/token 估算）
  SessionConfig       — Session 持久化
  DebugConfig         — 调试日志
  HttpConfig          — HTTP API 服务
  RetryConfig         — LLM 调用重试
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.mcp.config import MCPConfig, MCPServerConfig


# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_MODEL      = "claude-opus-4-5"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_TURNS  = 50
DEFAULT_AGENT_NAME = "orzooo"


# ── Session stats（不变）─────────────────────────────────────────────────────


@dataclass
class SessionStats:
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    start_time: float = field(default_factory=time.time)
    tool_stats: dict = field(default_factory=dict)
    skill_activations: dict = field(default_factory=dict)

    @property
    def elapsed(self) -> str:
        s = int(time.time() - self.start_time)
        return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"

    def summary(self) -> str:
        base = (
            f"Turns: {self.turns} | "
            f"Tokens in/out: {self.input_tokens}/{self.output_tokens} | "
            f"Tool calls: {self.tool_calls} | "
            f"Elapsed: {self.elapsed}"
        )
        if self.tool_stats:
            top = sorted(self.tool_stats.items(), key=lambda x: -x[1].get("calls", 0))[:3]
            tool_line = ", ".join(f"{k}×{v['calls']}" for k, v in top)
            base += f" | Tools: {tool_line}"
        return base

    def record_tool_call(self, name: str, success: bool, result_len: int) -> None:
        ts = self.tool_stats.setdefault(name, {"calls": 0, "success": 0, "fail": 0, "total_len": 0})
        ts["calls"] += 1
        ts["total_len"] += result_len
        if success:
            ts["success"] += 1
        else:
            ts["fail"] += 1

    def record_skill_activation(self, name: str) -> None:
        sa = self.skill_activations.setdefault(name, {"activations": 0})
        sa["activations"] += 1


# ════════════════════════════════════════════════════════════════════════════════
# 子配置块（Feature Configs）
# 每块聚合一个功能域的所有开关+参数，新增功能只需新建子类，不改 AppConfig 主体。
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class MemoryConfig:
    """[SYS-MEMORY] 跨 session 长期记忆配置。"""
    enabled: bool = False
    backend: str = "local"             # "local" | "chroma" | "redis"（预留扩展点）
    store_path: Optional[Path] = None  # None = <project_root>/.agent/memory.jsonl（项目级）
    global_enabled: bool = True        # 同时维护全局记忆 ~/.agent/memory.jsonl
    global_top_k: int = 2              # 全局记忆检索条目数（项目级优先，全局补充）
    top_k: int = 3                     # 项目记忆检索返回的最大条目数
    decay_half_life_days: float = 30.0 # 时间衰减半衰期（天）
    max_entries: int = 500             # 记忆条目上限，超出淘汰最旧

    # ── Lesson Memory 扩展（Stage 1，对应设计文档第 3 节 / 6.2 节）────────────
    lesson_rules_enabled: bool = True       # 规则触发（连续失败/权限拒绝后重试成功）总开关
    lesson_fail_threshold: int = 3          # 同一工具连续失败 ≥ N 次触发 lesson
    correction_detection_enabled: bool = True  # 人类反馈纠正检测总开关（1.4）

    # ── 图书馆式索引（perception/library_index.py）──────────────────────────
    # 分类树自动生长 + 实体目录 + 分类目录，"两步检索"（先定位书架再精排）。
    library_index_enabled: bool = True      # 总开关，关闭则 MemoryStore 行为与改造前完全一致
    library_shelf_search_enabled: bool = True  # context_builder 检索时是否走两步检索（可单独关闭，仅保留写入侧归类）
    library_index_user_scoped: bool = False  # 改进7：多用户场景下按 user_id 拆分独立书架（默认关闭，共享归并）

    # ── 方案一：记忆语义检索（混合 TF-IDF + 本地离线 Embedding）────────────
    # backend 可选值扩展为 "local" | "hybrid" | "chroma" | "redis"，"hybrid" 为新增。
    embedding_enabled: bool = False          # [默认关闭] 唯一总开关，见 perception/local_embedding.py
    embedding_model: str = "bge-small-zh-v1.5"   # 内置候选名，或用户自定义模型的本地路径
    embedding_model_cache_dir: Optional[Path] = None  # None = ~/.agent/models/
    embedding_tfidf_weight: float = 0.5
    embedding_weight: float = 0.5
    embedding_top_n: int = 20

    # ── 方案二：记忆巩固——从"淘汰"变成"归纳"────────────────────────────
    consolidation_enabled: bool = True       # 淘汰前是否尝试归纳（默认开，失败静默降级不影响可用性）
    consolidation_min_group_size: int = 3


@dataclass
class CompressConfig:
    """[SYS-COMPRESS] 自动上下文压缩配置。"""
    enabled: bool = False
    threshold: float = 0.7             # token 占用率超过此值触发压缩
    strategy: str = "turn_aligned"     # "turn_aligned" | "llm_summary" | "sliding_window" | "selective"
    forget_orphan_tool_results: bool = False  # 剔除保留段中无对应 tool_use 的 tool_result

    # ── SelectiveStrategy 专用 ───────────────────────────────────────────────
    # 各 _type 保留权重（0.0=最先丢弃，1.0=始终保留），None 使用内置默认
    selective_weights: dict = None
    selective_min_user_turns: int = 3  # 无论 budget 多紧，至少保留这么多轮用户输入

    # ── 触发器开关（每个触发方式独立开关，默认关闭，不影响现有行为）───────
    # 轮次计数触发：距上次 compact 满 N 轮自动触发
    turn_count_trigger_enabled: bool = False
    max_turns_before_compact: int = 20

    # 工具调用计数触发：距上次 compact 累计 N 次工具调用自动触发
    tool_call_count_trigger_enabled: bool = False
    max_tool_calls_before_compact: int = 50

    # 话题切换检测："off" | "heuristic" | "llm"
    #   off       —— 不检测（默认）
    #   heuristic —— 纯规则/关键词/路径跳变检测，无额外 LLM 调用
    #   llm       —— 启发式命中疑似切换后，再用一次小模型调用做二次确认
    topic_shift_detection: str = "off"
    topic_shift_keyword_overlap_threshold: float = 0.15  # 关键词重合度低于此值视为疑似切换

    # 冗余信息检测：tool_result 占比过高 / 重复调用堆积
    redundancy_detection_enabled: bool = False
    redundancy_tool_result_ratio: float = 0.6  # tool_result 消息占比超过此值触发

    # 冷却时间：compact 后这么多轮内，屏蔽除 token 硬阈值外的其他触发器，防止反复触发
    compact_cooldown_turns: int = 3

    # 触发后是否需要用户确认才执行（False=全自动静默压缩，仅打印提示；
    # True=先询问用户 y/n，用户拒绝则本次跳过，下次再检查）
    require_confirmation: bool = False

    # ── Compact 预检配置 ─────────────────────────────────────────────────────
    # compact 前主动预估 token，超过阈值直接走分批路径，避免异常捕获开销
    compact_precheck_enabled: bool = True          # 是否启用预检（默认开启）
    compact_precheck_threshold: float = 0.85       # 估算 token 超过上下文窗口此比例视为超限
    model_context_window: int = 0                  # 模型上下文窗口大小（0=自动从 provider 获取或用默认）


@dataclass
class ToolTrimConfig:
    """[SYS-TRIM] 工具调用结果截断配置。"""
    enabled: bool = True
    threshold: int = 4000              # 超过此字符数触发截断（约 1000 tokens）
    bash_tail_ratio: float = 0.6       # bash 结果：尾部保留比例（错误/输出通常在尾部）
    read_window_lines: int = 0         # read_file：滑动窗口行数（0=自动推算）
    grep_max_lines: int = 50           # grep/glob：最大保留行数
    # [SYS-LARGEFILE] 大文件感知
    large_file_threshold_bytes: int = 20000  # 超过此字节数视为大文件（默认 20 KB）
    list_dir_show_size: bool = True            # list_dir 是否显示文件大小
    large_file_warn_marker: str = "⚠"         # 大文件在 list_dir 中的标记符

    # [SYS-RAWSTORE] 原始输出留存：只要发生了截断/摘要，完整原文都会被保存，
    # 供 agent 通过 view_raw_result 工具按需回看。默认开启，几乎零成本
    # （只是内存里多存一份字符串，不涉及额外 LLM 调用）。
    raw_store_enabled: bool = True
    raw_store_max_entries: int = 128          # 原始结果 LRU 容量上限
    raw_store_max_total_chars: int = 5_000_000  # 所有留存原文的总字符数上限（防止内存无界增长）

    # [SYS-SMARTTRIM] 智能摘要：结果超过 smart_summary_threshold 时，
    # 不再单纯按规则截断，而是调用 LLM 提炼出与本次调用相关的关键信息。
    # 默认关闭（避免引入额外 LLM 调用开销），可显式开启。
    smart_summary_enabled: bool = False
    smart_summary_threshold: int = 12000      # 触发 LLM 摘要的字符数阈值（应 >= threshold）
    smart_summary_max_input_chars: int = 60000  # 喂给摘要模型的原文上限，超过则退化为规则截断
    smart_summary_model: str = ""             # 摘要用的模型名；留空则复用当前主模型


@dataclass
class SkillConfig:
    """[SYS-SKILL] Skill 系统配置。"""
    semantic_enabled: bool = False     # 语义匹配（需要 embedding）
    semantic_threshold: float = 0.72   # 语义相似度阈值
    tracking_enabled: bool = False     # 使用追踪统计
    chunking_enabled: bool = False     # 内容裁剪（按 query 相关性）
    compact_budget: int = 25_000       # 压缩时重附的总 token 预算
    compact_per_skill: int = 5_000     # 单个 skill 最多贡献的 token 数
    matcher: str = "keyword"           # "keyword" | "ngram" | "semantic"（预留扩展点）
    keyword_activation_enabled: bool = False  # 是否允许根据关键词自动激活 skill（默认关闭，需显式启用）


@dataclass
class PerceptionConfig:
    """[SYS-PERCEPTION] 项目感知相关配置（扫描/监听/缓存/token 估算）。"""
    project_scan_enabled: bool = False
    file_watch_enabled: bool = False
    tool_cache_enabled: bool = False
    tool_cache_max_entries: int = 256  # 工具结果缓存容量上限
    token_estimate_enabled: bool = False
    token_warn_threshold: float = 0.75
    tool_stats_enabled: bool = False
    # 产出物自动侦测：write_file/create_file/patch_file/bash 成功执行后自动
    # 扫描是否生成了文档/图片类产出并登记到产出物看板。默认关闭——涉及对
    # bash 命令/输出做正则扫描 + 文件系统访问，需用户显式启用。
    artifact_auto_detect_enabled: bool = False


@dataclass
class WorkdirKnowledgeConfig:
    """[SYS-WORKDIR-KNOWLEDGE] Workdir 知识层配置（W2，对应设计文档 8.2 节）。

    覆盖 project.json / timeline.jsonl / work_index.json / open_threads.json /
    knowledge.md 五个文件的维护与 context 注入开关。默认开启——这是纯粹的
    "数据沉淀与观察"层，不产生任何自主行为，风险极低（对应 Stage 4+ 计划文档
    "改造原则 4：数据先于行为"），与 lesson_rules_enabled 默认开启的取舍一致。
    """
    enabled: bool = True
    # work_index 关联启发式：本次 session 与某 WorkThread 最近一条
    # related_sessions 的时间间隔小于此值（天）时，视为"延续该 WorkThread"
    work_thread_relation_days: float = 7.0
    # context 注入：open_threads 中 priority=high 的条目最多注入几条，避免占用过多 context
    open_threads_inject_limit: int = 5


@dataclass
class ObservabilityConfig:
    """[SYS-OBSERVABILITY] 第 9 章观察性配置（Stage 6）。

    覆盖：
      6.1  traces.jsonl  — session 内时序性能追踪
      6.2  /diagnostics  — 实时聚合健康检查端点
      6.3  anomaly_flags — 异常行为检测（依赖 activity_log 数据积累）
      6.4  error_category / resolves_seq — 工具调用因果链字段
    """
    enabled: bool = True
    # 6.1：是否写入 traces.jsonl（可独立关闭，降低磁盘写入）
    tracing_enabled: bool = True
    # 6.3：异常检测触发阈值 k（value > mean + k*std 时告警），建议 2.5~3.5
    anomaly_k_sigma: float = 3.0
    # 6.3：至少需要多少条 activity_log 历史记录才启用异常检测（小样本方差不稳定）
    anomaly_min_samples: int = 10


@dataclass
class GlobalKnowledgeConfig:
    """[SYS-GLOBAL-KNOWLEDGE] Global 知识层配置（W3，对应设计文档 8.3 节 /
    self_evolution_stage4plus_plan.md Stage 5）。

    覆盖 self_profile.json / projects_index.json / cross_project_index.json /
    activity_log.jsonl 四个文件的维护与 context 注入开关。默认开启，与
    WorkdirKnowledgeConfig 取舍一致——这是纯粹的"数据沉淀与观察"层（5.4 的
    跨项目扫描函数本身可被调用，但不在本配置下自动周期触发，触发时机留给
    Stage 8 巩固循环），不产生任何自主行为。
    """
    enabled: bool = True
    # projects_index：项目超过此天数无 last_active 更新则标记 dormant
    dormant_after_days: float = 30.0
    # context 注入：workdir 变化时注入 activity_log 最近几条
    activity_log_inject_limit: int = 5


@dataclass
class ProfileConfig:
    """[SYS-PROFILE] 用户画像（profile）生成配置。

    为后续多用户预留：profile 存储路径由 AgentPaths.profile_path(user_id) 决定，
    当前 user_id 默认为 None（单用户），不影响现有行为。
    """
    enabled: bool = False
    # 全局记忆每新增 N 条，触发一次 profile 刷新（后台线程，不阻塞主流程）
    refresh_interval_entries: int = 3
    # 全局记忆条目数达到该值之前，不生成 profile（信息太少意义不大）
    min_entries: int = 1
    # 取最近多少条全局记忆用于生成/刷新 profile
    max_entries_for_profile: int = 20


@dataclass
class SessionConfig:
    """Session 持久化配置。"""
    dir: Optional[Path] = None         # None = <project_root>/.agent/sessions
    fmt: str = "json"                  # "json" | "jsonl"
    auto_save: bool = True
    summary_enabled: bool = False
    summary_min_turns: int = 4
    search_enabled: bool = False
    backend: str = "local"             # "local" | "sqlite"（预留扩展点）


@dataclass
class DebugConfig:
    """调试日志配置。"""
    llm_enabled: bool = False
    llm_console: bool = False
    log_dir: Optional[Path] = None


@dataclass
class HttpConfig:
    """HTTP API 服务配置。"""
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    api_token: str = ""
    allowed_ips: list = field(default_factory=lambda: ["127.0.0.1", "::1"])
    cors_origins: list = field(default_factory=list)
    fs_readonly: bool = False
    fs_excludes: list = field(default_factory=list)
    ring_maxlen: int = 2000
    # daemon 多用户架构 Phase 1：是否启用多用户认证（MultiUserAuthMiddleware）。
    # 默认 False，保持现有单 token 单用户行为完全不变（向后兼容）。
    multi_user_enabled: bool = False


@dataclass
class WebSearchConfig:
    """[SYS-WEBSEARCH] 网络搜索工具配置。

    provider 可选值（由 web_search/factory.py 注册表决定，可动态扩展）：
      "duckduckgo" (默认，免费，无需 key) | "brave" | "serper" | "tavily"

    api_key: 显式指定时优先于环境变量（BRAVE_API_KEY / SERPER_API_KEY / TAVILY_API_KEY）。
    """
    provider: str = "duckduckgo"
    api_key: str = ""
    max_results: int = 5
    timeout: float = 10.0


@dataclass
class RetryConfig:
    """LLM 调用重试配置。

    backoff_mode 决定每次重试的等待时长计算方式：
      "fixed"       — 每次等待固定的 delay 秒（默认）
      "linear"      — 线性递增：delay, delay+step, delay+2*step, …
      "exponential" — 指数递增：delay, delay*step², …
                      （此时 backoff_step 为倍数，如 1.5 表示每次 ×1.5）

    backoff_max_delay — 等待时间上限（秒），0 = 不限制

    network_aware — 是否启用断网感知（默认 True）：请求失败时，如果异常
      "看起来像"网络层失败（DNS/连接/超时）且此刻确实探测不到网络，就不按
      backoff 盲目重试，而是阻塞等待网络恢复，恢复后立即重试且不消耗
      重试预算。真正的断网重试没有意义，等它是唯一有效的策略。
    network_check_interval — 断网等待期间的轮询间隔（秒）
    network_max_wait — 断网等待的最长时长（秒），0 = 不限时长一直等到恢复
      为止；设为正数后，超时仍未恢复会退回正常重试逻辑（消耗一次重试预算）
    """
    max_retries: int = 15
    delay: float = 5.0
    verbose: bool = True
    backoff_mode: str = "fixed"          # "fixed" | "linear" | "exponential"
    backoff_step: float = 60.0           # linear: 步长(s)；exponential: 倍数(>1.0)
    backoff_max_delay: float = 0.0       # 0 = 不限制上限
    network_aware: bool = True
    network_check_interval: float = 5.0
    network_max_wait: float = 0.0


@dataclass
class EnsembleConfig:
    """[SYS-ENSEMBLE] 多结果合并取优（Best-of-N）配置。

    mode 决定是否触发以及如何触发：
      "off"     完全关闭（默认）
      "manual"  仅当调用方显式指定 ensemble=true 时触发
      "auto"    由规则层 + 模型自判层共同决定是否触发
      "always"  对所有匹配的任务强制触发（调试/评测用）

    granularity 控制粒度开关：
      "llm_call" 仅同输入多次调用模型
      "subagent" 仅多 subagent 不同上下文
      "both"     两种都允许（按调用方/场景选择）
    """
    mode: str = "off"                          # off | manual | auto | always
    granularity: str = "both"                  # llm_call | subagent | both
    n: int = 3
    execution: str = "parallel"                # serial | parallel
    max_concurrency: int = 3
    judge_strategy: str = "llm_judge"           # llm_judge | first_success | vote | merge
    judge_model: Optional[str] = None
    judge_provider: Optional[str] = None        # None = 复用主 cfg.llm_provider（与 judge_model 独立覆盖）
    early_stop_on_consensus: bool = True
    max_extra_cost_ratio: float = 2.0           # AUTO 模式下的成本保护上限（相对单次调用的倍数）


@dataclass
class RoleAgentConfig:
    """[SYS-ROLE-AGENT] 多角色 Agent 协作系统配置。

    控制 EvaluatorAgent / CoachAgent 等角色 Agent 的启用与过滤。
    总开关（enabled）优先级最高；其他参数仅在总开关开启时生效。
    """
    # ── 总开关（默认关闭，需显式启用）────────────────────────────────────────
    enabled: bool = False

    # ── 白名单：只启用指定名称的角色 Agent（空列表 = 全部启用）────────────────
    # 例：["evaluator"]  只启用 evaluator，忽略其他所有角色 Agent
    allow: list = field(default_factory=list)

    # ── 黑名单：屏蔽指定名称的角色 Agent（空列表 = 不屏蔽）────────────────────
    # 例：["coach"]  屏蔽 coach，其余正常加载
    block: list = field(default_factory=list)

    # ── 自定义目录：仅从该目录加载角色 Agent profile（覆盖默认目录）────────────
    # None = 使用项目默认的 .agent/agents/ 目录
    agents_dir: Optional[Path] = None


@dataclass
class GoalModeConfig:
    """[SYS-GOAL-MODE] Goal 模式配置：设定一个目标，Agent 自动多轮尝试直至达成或触发安全阀。

    总开关（enabled）只影响 `/goal` 命令是否可用，不影响其他现有功能。
    """
    enabled: bool = False

    # ── 验收标准协商 ─────────────────────────────────────────────────────────
    # GoalSpecBuilder 用于生成/修订 GoalSpec 的模型（None = 复用主 cfg.model）
    spec_builder_model: Optional[str] = None
    spec_builder_provider: Optional[str] = None

    # ── GoalJudge ────────────────────────────────────────────────────────────
    judge_model: Optional[str] = None
    judge_provider: Optional[str] = None
    # 判定 Agent 是否挂载工具（能自己跑命令验证验收标准），默认关闭（最小权限原则）
    judge_tools_enabled: bool = False
    # 仅当 judge_tools_enabled=True 时生效的白名单（工具名 / 工具组名均可）
    judge_allowed_tools: list = field(default_factory=lambda: ["bash", "read_file", "grep", "glob"])
    judge_allowed_tool_groups: list = field(default_factory=list)
    # [SYS-GOAL-MODE] GoalJudge 挂工具时默认走 sandbox（拦截真实执行，只能看到
    # "would have executed"），因为验证命令（比如 pytest/python 脚本）往往需要
    # 真实跑起来才有意义，sandbox 模式下这类验证等于形同虚设。开启后 GoalJudge
    # 的工具调用会以 auto_approve=True + 不走 sandbox 的方式真实执行（等价于
    # 人工始终按 --yes 全部放行），不会逐条弹出确认。请只在信任验收标准里的
    # 验证命令、且愿意让 GoalJudge 自己真实执行命令时开启。
    judge_yes_mode: bool = False

    # ── 外层循环安全阀 ───────────────────────────────────────────────────────
    max_rounds: int = 20                       # 外层 goal 迭代轮数上限
    max_total_compacts: int = 10               # 单次 goal 执行期间最多允许几次 compact
    consecutive_same_feedback_limit: int = 3   # 连续 N 轮 judge 反馈高度雷同 → 判定"卡住"
    same_feedback_similarity_threshold: float = 0.9  # difflib.SequenceMatcher 相似度阈值

    # 判定"卡住"后不直接终止：先压缩一次历史、给 agent 一次重新整理思路的机会，
    # 再继续跑；如果之后又卡住了才真正终止。max_stuck_recoveries 是这种
    # "卡住→compact→再给机会"额度的次数上限，用完之后再卡住就直接终止。
    # 设为 0 等价于旧行为（一卡住就终止，不做恢复尝试）。
    # [BUGFIX/需求变更] 之前默认是 1——也就是"只给一次机会"：卡住 → compact
    # 一次 → 再卡住就直接终止。改成默认 3：只要还在"没有新进展"（连续反馈
    # 高度雷同），就持续压缩历史 + 换角度重试，直到连续 3 次 compact 之后
    # 仍然没有新进展（即又被判定卡住）才真正终止。任何一轮出现了明显不同
    # 于上一轮的反馈（说明确实有新进展），_check_stuck() 都会把卡住计数和
    # 这个恢复额度重新计数（见 runner.py _check_stuck 的重置逻辑），不会被
    # 提前耗尽。
    max_stuck_recoveries: int = 3

    # ── 调试 ─────────────────────────────────────────────────────────────────
    judge_show_prompt: bool = False   # 打印发给 GoalJudge 的完整输入 prompt（排查判定依据用）

    # ── 状态持久化（异常中断恢复）────────────────────────────────────────────
    persist_state: bool = True   # 是否在每个轮次边界落盘 goal_state.json
    auto_resume_prompt: bool = True  # 启动时检测到未完成 goal 是否主动提示恢复

    # ── [next_doc/goal_mode_completion_improvement_plan.md 改造项一]
    # 卡住判定方式："llm"（默认）→ 让 GoalJudge 在同一次结构化输出里额外判断
    # progress（是否有实质进展），GoalRunner 据此驱动卡住计数，比纯文本相似度
    # 更能识别"表述不同但本质相同"或"表述相似但确有进展"这两类规则算法处理不好
    # 的情况；GoalJudge 输出解析失败/未按新 schema 输出 progress 字段时自动
    # 回退到 "text_similarity" 规则，不影响鲁棒性。
    # "text_similarity" → 完全恢复升级前的 difflib 文本相似度规则，一键回退。
    progress_judge_mode: str = "llm"

    # ── [改造项三] 验收标准逐条状态追踪：GoalJudge 每轮额外输出 checklist
    # （每条标准 passed/evidence），GoalRunner 据此在 GoalState 里维护
    # criteria_status，并把上一轮状态回传给下一轮 GoalJudge，减少判定抖动、
    # 让 CONTINUE 反馈更聚焦"还差哪一条"。仅在 progress_judge_mode="llm" 时
    # 生效（两者共用同一次扩展 JSON 输出）。
    criteria_tracking_enabled: bool = True

    # ── [改造项二] 卡住恢复提示携带"已尝试路径清单"：触发卡住恢复时，把最近
    # 几轮 GoalJudge 给出的 progress_reason 拼成"已验证无效的方向"提示给主
    # Agent，而不是只给一句通用的"换个角度"。依赖 progress_judge_mode="llm"
    # 积累的 progress_reason；关闭 progress_judge_mode 或本开关时退化为旧的
    # 通用提示文本。
    stuck_recovery_attempted_paths_enabled: bool = True

    # ── [改造项五] 失败经验沉淀：goal 因 stuck / max_rounds_exhausted 终止时，
    # 把已尝试路径 + 失败原因整理成一条 entry_type="lesson"（source=
    # "goal_mode_failure"）写入 memory，供未来同类目标的 GoalSpecBuilder /
    # 主 Agent 参考，避免重复踩坑。写入失败不影响 goal 本身的终止流程。
    failure_lesson_enabled: bool = True

    # ── [改造项四，预留开关，暂未实现内部逻辑] 卡住恢复时并行生成多个候选
    # continuation、择优继续，而不是单路径重来。复用 ensemble/ 基础设施，
    # 涉及额外的并发 LLM 调用成本，默认关闭；stuck_recovery_candidates 是
    # 届时并行候选数量。见 next_doc/goal_mode_completion_improvement_plan.md
    # 改造项四——本次改造只占位配置字段，不实现调度逻辑。
    stuck_recovery_ensemble_enabled: bool = False
    stuck_recovery_candidates: int = 3

    # ── [改造项六，预留开关，暂未实现] 细粒度执行器：在 run_turn 内部按工具
    # 调用次数插入轻量判断，而不是等整个 run_turn 跑完才评审。见改造计划
    # 文档"改造项六"，本次改造只占位配置字段，GoalRunner 仍固定使用
    # CoarseStepExecutor。
    fine_grained_execution_enabled: bool = False


@dataclass
class TurnJudgeConfig:
    """[SYS-TURN-JUDGE] 轮次守门员配置：每轮对话结束、真正进入"等待用户输入"之前，
    先让一个轻量 judge agent 核查一次——这是主 Agent 真的完成了、需要真人介入，
    还是遇到了纯技术性问题（模型输出格式有问题、撞到 max_turns 硬顶需要 compact
    等），应该由系统自动代替用户反馈让主 Agent 继续处理。

    总开关默认关闭，不影响现有行为；打开后仅在没有 TurnEnd hook 已经接管
    （即 agent._turn_end_user_input 仍为 None）时才会触发，避免和用户自定义
    TurnEnd hook 冲突。
    """
    enabled: bool = False

    # ── TurnJudge 模型（None = 复用主 cfg.model / provider，通常建议用更便宜的模型）
    judge_model: Optional[str] = None
    judge_provider: Optional[str] = None

    # ── 安全阀：连续自动接管次数上限，防止死循环刷屏 ─────────────────────────
    # 每次真正等到真人输入后计数会被重置。
    max_auto_rounds: int = 3

    # ── 卡住检测 + compact 恢复（与 goal_mode 的同名机制思路一致）───────────
    # [SYS-TURN-JUDGE] 之前 TurnJudge 唯一的"防止死循环"手段就是
    # max_auto_rounds：不管这几轮自动接管到底有没有实质进展，凑够次数就
    # 强制交还真人。这样有两个问题：
    #   1) 如果模型连续几轮给出高度相似的输出（比如反复卡在同一个报错、
    #      同一种格式问题），说明历史里可能堆积了噪音干扰判断，这时候
    #      "先 compact 一次换个角度重试"往往比"硬撑到 max_auto_rounds
    #      耗尽"更容易破局——但之前只有 TurnJudge 自己主观判定
    #      NEED_COMPACT 时才会 compact，不会主动因为"检测到没有实质
    #      进展"而触发。
    #   2) 万一确实卡住了，之前的 compact（无论是 TurnJudge 主动判定还是
    #      这里新加的卡住检测触发）会占用 max_auto_rounds 里的一次名额，
    #      挤占本该留给"真正推进任务"的自动接管次数。
    # 加入这一组参数后：主 Agent 连续 `consecutive_same_output_limit` 轮
    # 输出高度相似（`same_output_similarity_threshold` 判定）就判定"没有
    # 实质进展"，主动 compact + 注入"换个角度重试"的提示，最多连续尝试
    # `max_stuck_recoveries` 次，且这些"卡住恢复"用的 compact 轮次不计入
    # max_auto_rounds 预算（与 goal_mode 的 _try_stuck_recovery 语义一致）。
    # 一旦某一轮输出明显不同于上一轮（真实进展），卡住计数和恢复额度都会
    # 重置。恢复额度耗尽后再次判定卡住，就强制交还真人（等价于撞到
    # max_auto_rounds），不会无限重试下去。设为 0 关闭这个机制，回到旧行为。
    consecutive_same_output_limit: int = 3
    same_output_similarity_threshold: float = 0.9
    max_stuck_recoveries: int = 3

    # ── 调试 ─────────────────────────────────────────────────────────────────
    judge_show_prompt: bool = False   # 打印发给 TurnJudge 的完整输入 prompt

    # ── 纳入判定上下文的最近历史消息条数 ──────────────────────────────────────
    history_window: int = 6


@dataclass
class EnvInfoConfig:
    """[SYS-ENV-INFO] 环境信息采集与注入配置。

    控制将哪些环境信息（OS、Python 版本、时区等）注入到 system prompt 中，
    帮助模型感知运行环境，给出更贴合实际的建议和命令。

    providers 列表支持两种格式：
      - 内置别名：  "builtin.system" | "builtin.runtime" | "builtin.locale"
      - 完整类路径："mypkg.module.MyProvider"（需实现 EnvInfoProvider 接口）

    provider_kwargs 为各 Provider 的初始化参数，key 为 Provider 标识：
      {"builtin.system": {"include_hostname": True}}
    """
    enabled: bool = True

    # 启用的 Provider 列表（None = 使用默认三个内置 Provider）
    providers: Optional[list] = None

    # 各 Provider 的初始化参数
    provider_kwargs: dict = field(default_factory=dict)

    # 隐私开关（透传给内置 Provider，无需在 provider_kwargs 中重复写）
    include_hostname: bool = False
    include_username: bool = False


@dataclass
class WorkflowConfig:
    """[具身改进 B3] Workflow 并发执行配置。

    `workflow/runner.py` 按 depends_on 拓扑分层（batch），同一层内互不依赖的
    步骤默认并发执行（每个步骤本来就用独立的 Agent 实例，互不共享可变状态，
    详见 runner.py 模块文档）。这里只控制"是否启用并发"和"并发上限"，
    单步骤可通过 WorkflowStep.allow_parallel=False 单独强制串行。
    """
    parallel_enabled: bool = True
    max_parallel: int = 4   # 同一拓扑层最多同时执行的步骤数


@dataclass
class ProprioceptionConfig:
    """[具身改进 B1] 本体感知模块配置。

    ProprioceptionModule 让 agent 对自身状态（认知负荷、不确定性、风险感知、
    剩余预算、挫败感）有一个轮间快照，供主循环决定是否需要调整行为。
    本身不调用 LLM，是 O(1) 的纯计算，默认开启不会带来明显成本。
    """
    enabled: bool = True
    # frustration 超过该阈值且连续失败次数达到 consecutive_failure_threshold 时，
    # 注入一条元认知提示（建议模型停下来汇报困境，而不是盲目重试）。
    frustration_threshold: float = 0.5
    consecutive_failure_threshold: int = 3
    # 是否把每轮快照写入 traces.jsonl（供 巩固循环 后续分析趋势）
    trace_enabled: bool = True
    # 调试：打印每轮快照
    verbose: bool = False
    # [方案三] uncertainty 信号接入事件总线：连续多轮 uncertainty 都超过该
    # 阈值才发布 "proprioception.uncertainty_sustained" 事件（限流，避免
    # uncertainty 这种逐轮波动的连续值每次越过阈值就刷屏）。
    uncertainty_threshold: float = 0.45
    uncertainty_streak_required: int = 3


@dataclass
class AffordanceConfig:
    """[具身改进 B4] 余裕感知层（AffordanceMap）配置。

    session 开始时构建一次（不是每轮 turn），交叉分析 open_threads /
    capability_map / lesson memory，生成"当前环境对我意味着哪些行动机会"
    的简短文本块，拼进 system_extra。纯只读分析，不调用 LLM，不写入任何
    文件——失败时静默跳过，不阻断 session 创建。
    """
    enabled: bool = True
    # 是否在分析中纳入 capability_map（依赖 巩固循环 历史扫描数据；
    # 项目从未跑过 巩固循环 时该字段不影响功能，只是 unexplored_areas 为空）
    use_capability_map: bool = True
    # [打通具身感知与行为感知] 是否交叉分析用户行为感知层（perception/behavior/）
    # 的近期活动摘要。双重开关：仅当 BehaviorConfig.enabled 与本字段同时为
    # True 时才生效；任一为 False 时该输入源视为缺失，不影响其余分析路径。
    # 默认关闭，避免在未显式启用行为感知采集的项目里意外产生跨层读取。
    use_behavior_context: bool = False
    # 调试：打印生成的 AffordanceMap
    verbose: bool = False
    # [方案一] 高风险域接入自主探索门控：总开关，关闭则方案一全部逻辑
    # （SoftGoalDeriver 候选降权 / ExplorationSandbox 预算收紧）不生效，
    # 行为与改动前完全一致。
    risk_gating_enabled: bool = True
    # [方案一] 高风险域候选的 urgency 乘数（降权而非拒绝——具身层的风险
    # 判断本身也可能过时或误判，不应该直接拉黑一个域）。
    risk_downweight_factor: float = 0.4


@dataclass
class AutonomyConfig:
    """[方案三] 自主探索——好奇心评分 + 探索结果回写记忆 相关配置。

    只影响 SoftGoalDeriver 的排序权重和 ExplorationSandbox 的"已探索"冷却
    窗口，不改变 ExplorationBudget/exploration_budget_ratio 的预算占比。
    """
    novelty_weight: float = 0.5           # urgency + novelty_weight * novelty 排序权重
    exploration_min_calls_threshold: int = 2   # total_calls 低于此值视为"几乎未探索"
    already_explored_cooldown_days: float = 30.0
    # [方案二] BehaviorContext 接入自主任务调度门控：双开关哲学，与
    # affordance.use_behavior_context 保持一致，默认 False——behavior 采集
    # 依赖桌面/浏览器 collector，不是所有部署场景都装了，强行默认开启会让
    # 没配置 collector 的用户平白多一次无意义的文件读取。
    behavior_gating_enabled: bool = False
    # 观察窗口内应用切换次数达到该阈值时，视为"用户明显在忙碌切换"。
    behavior_gating_switch_threshold: int = 3


@dataclass
class ReminderConfig:
    """[SYS-REMINDER] 动态 Reminder 提示注入配置。

    reminder 在特定情境下（工具出错、特定工具输出、用户意图识别）
    动态追加到对话历史中，帮助模型更好地解决当前问题。
    不注入 system prompt，而是注入为 user 或 assistant 消息。
    """
    enabled: bool = True
    # 用户自定义 reminder 目录（优先级高于系统默认目录）
    custom_dir: Optional[Path] = None
    # 各类触发源开关（精细控制）
    tool_error_enabled: bool = True     # 工具调用出错时触发
    post_tool_enabled: bool = True      # 工具调用成功后触发
    user_intent_enabled: bool = True    # 用户意图识别触发
    pattern_enabled: bool = True        # assistant 输出模式触发
    # [具身改进 A3] 前馈控制：工具调用前触发（成熟的运动系统不只靠事后纠错，
    # 还依赖预期动作的预先调节——危险操作发生前先提醒，而不是出错后再补救）
    pre_tool_enabled: bool = True        # 工具调用前触发
    # [SYS-FORMAT-CORRECTION 统一化] 格式纠错检测器（perception/format_correction_detector.py）
    # 命中问题时触发，文案由 reminder 文件提供（trigger_event: format_issue）。
    # 关闭后 format_correction 会退回内置默认文案，但检测+自动续跑逻辑不受影响
    # （该逻辑受 FormatCorrectionConfig.enabled 单独控制）。
    format_issue_enabled: bool = True
    # 同一 turn 内最多注入的 reminder 条数（避免大量 reminder 污染上下文）
    max_per_turn: int = 3
    # 调试：打印匹配到的 reminder 名称
    verbose: bool = False


@dataclass
class FormatCorrectionConfig:
    """[SYS-FORMAT-CORRECTION] 工具调用格式纠错配置。

    背景：模型有时会"意图"调用工具（输出中出现 <tool_use> / <tool_result>
    等协议关键字），但因为标签未闭合、JSON 截断、标签名用混等格式问题，
    导致 parse_tool_calls() 解析失败，最终 response.tool_calls 为空。

    若不处理，_agentic_loop() 会把这个"半成品"输出当成最终答案，直接结束
    对话——但模型本意是还没做完事。本配置控制：检测到这类"格式损坏但明显
    想调用工具"的输出时，是否自动以 user 角色注入纠错提示，让模型重新
    输出一次，loop 继续而不是中断。

    可扩展性：新的"格式异常模式"在
    perception/format_correction_detector.py 的规则注册表里追加即可，
    不需要改这里的配置结构。
    """
    enabled: bool = True
    # 同一 turn 内最多允许的纠错重试次数（防止模型持续输出坏格式导致死循环）
    max_retries_per_turn: int = 2
    # 调试：打印命中的格式问题类型 + 注入的纠错提示
    verbose: bool = False


@dataclass
class PrivacyConfig:
    """
    隐私信息保护配置。

    enabled=True 时，agent 在每次调用 LLM 前会把 secrets 里的真实值替换成
    占位符（如 {{SECRET_1}}），收到回复后再还原。LLM 全程看不到真实 key。

    auto_env_patterns：
        匹配这些正则的环境变量名会被自动纳入保护，无需手动列举。
        设为 [] 可完全禁用环境变量自动采集。
        设为 None 时使用 PrivacyGuard 的内置默认模式（推荐）。

    secrets：
        显式指定的隐私条目列表，每项是 {"name": "标签", "value": "真实值"}。
        可在 agent_config.json 的 "privacy" 块里配置，也可在代码里通过
        load_config(privacy_secrets=[...]) 传入。

    placeholder_prefix：
        占位符前缀，默认 SECRET，生成 {{SECRET_1}}、{{SECRET_2}} 等。
        一般不需要修改，除非业务场景中 {{SECRET_N}} 本身有特殊含义。
    """
    enabled: bool = True
    secrets: list = field(default_factory=list)   # list[{"name": str, "value": str}]
    auto_env_patterns: Optional[list] = None      # None = 使用 PrivacyGuard 内置默认
    placeholder_prefix: str = "SECRET"
    # 调试：打印屏蔽摘要（不含真实值）
    verbose: bool = False


# ════════════════════════════════════════════════════════════════════════════════
# 主配置类
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class AppConfig:
    """
    应用主配置。

    核心字段直接持有，功能特性通过子配置块聚合。
    子配置块有合理默认值，不需要某功能时完全不用关心它的字段名。

    用法示例：
        cfg = AppConfig(
            model="claude-opus-4-5",
            memory=MemoryConfig(enabled=True, top_k=5),
            compress=CompressConfig(enabled=True, strategy="llm_summary"),
        )
        if cfg.memory.enabled:
            backend = create_memory_backend(cfg)
    """

    # ── 核心（必填/常用）──────────────────────────────────────────────────────
    api_key: str = ""
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_turns: int = DEFAULT_MAX_TURNS
    project_root: Path = field(default_factory=Path.cwd)
    skills_dir: Optional[Path] = None
    prompts_dir: Optional[Path] = None

    # ── 运行行为 ───────────────────────────────────────────────────────────────
    verbose: bool = False
    sandbox: bool = False
    simple_mode: bool = False
    raw_output: bool = False
    show_reasoning: bool = True   # False 时不打印模型的 reasoning/思考过程（/reasoning 切换）
    auto_approve: bool = False
    stream: bool = True

    # ── LLM Provider ──────────────────────────────────────────────────────────
    llm_provider: str = "anthropic"
    llm_base_url: str = ""
    use_system_tool_call: bool = False
    max_llm_calls: int = 8
    system_message_format: str = "system_field"

    # ── LLM Fallback Chain（多配置故障转移 + 多 key 轮转）────────────────────
    # 每条 dict 至少包含 provider/model/api_key；
    # 可含 api_keys（列表）、key_rotation、key_switch_on、key_cooldown。
    # 若为空列表，退化为只使用主配置的单条链。
    llm_fallback_chain: list = field(default_factory=list)
    # 触发切换到下一条 LLM 配置的错误类名称集合（None = 使用 LLMClientPool 默认值）
    llm_fallback_on: Optional[list] = None

    # ── System prompt 注入 ────────────────────────────────────────────────────
    claude_md_content: str = ""
    system_extra: str = ""
    agent_name: str = DEFAULT_AGENT_NAME

    # [具身改进 C3] 认知锚点文件开关——任务被打断时是否生成/读取
    # .agent/cognitive_anchor.md。默认开启，禁用后 _save_cognitive_anchor /
    # _maybe_load_cognitive_anchor 均直接 no-op。
    cognitive_anchor_enabled: bool = True

    # [记事本] 记事本功能总开关。默认开启——常驻 system prompt 的持久便签，
    # 用于记录任务过程中的关键信息/结果/注意事项，不受 history compact 影响。
    # 关闭后：不再向 system prompt 注入记事本块，也不再为当前 session 创建/
    # 加载 notepad.json；notepad_* 工具调用会返回错误提示（工具本身仍注册在
    # 全局 registry 中，与 workdir_knowledge_enabled 等开关的既有取舍一致）。
    notepad_enabled: bool = True

    # ── 功能子配置块（每个功能域独立聚合）────────────────────────────────────
    memory:     MemoryConfig     = field(default_factory=MemoryConfig)
    compress:   CompressConfig   = field(default_factory=CompressConfig)
    tool_trim:  ToolTrimConfig   = field(default_factory=ToolTrimConfig)
    skill:      SkillConfig      = field(default_factory=SkillConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    session:    SessionConfig    = field(default_factory=SessionConfig)
    profile:    ProfileConfig    = field(default_factory=ProfileConfig)
    debug:      DebugConfig      = field(default_factory=DebugConfig)
    http:       HttpConfig       = field(default_factory=HttpConfig)
    retry:      RetryConfig      = field(default_factory=RetryConfig)
    mcp:        MCPConfig        = field(default_factory=MCPConfig)
    web_search: WebSearchConfig  = field(default_factory=WebSearchConfig)
    reminder:   ReminderConfig   = field(default_factory=ReminderConfig)
    proprioception: ProprioceptionConfig = field(default_factory=ProprioceptionConfig)
    affordance: AffordanceConfig = field(default_factory=AffordanceConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    workflow:   WorkflowConfig   = field(default_factory=WorkflowConfig)
    format_correction: FormatCorrectionConfig = field(default_factory=FormatCorrectionConfig)
    role_agent: RoleAgentConfig  = field(default_factory=RoleAgentConfig)
    goal_mode:  GoalModeConfig   = field(default_factory=GoalModeConfig)
    turn_judge: TurnJudgeConfig  = field(default_factory=TurnJudgeConfig)
    env_info:   EnvInfoConfig    = field(default_factory=EnvInfoConfig)
    workdir_knowledge: WorkdirKnowledgeConfig = field(default_factory=WorkdirKnowledgeConfig)
    global_knowledge: GlobalKnowledgeConfig = field(default_factory=GlobalKnowledgeConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    privacy:    PrivacyConfig     = field(default_factory=PrivacyConfig)
    ensemble:   EnsembleConfig    = field(default_factory=EnsembleConfig)

    # ── 向后兼容属性（让旧代码 cfg.memory_enabled 不报错）────────────────────
    # 以下属性委托给子配置块，方便渐进式迁移，后续版本可删除

    @property
    def memory_enabled(self) -> bool:           return self.memory.enabled
    @property
    def memory_top_k(self) -> int:              return self.memory.top_k
    @property
    def memory_store_path(self) -> Optional[Path]: return self.memory.store_path

    @property
    def auto_compress_enabled(self) -> bool:    return self.compress.enabled
    @property
    def auto_compress_threshold(self) -> float: return self.compress.threshold
    @property
    def forget_policy_enabled(self) -> bool:    return self.compress.forget_orphan_tool_results

    @property
    def tool_result_trim_enabled(self) -> bool: return self.tool_trim.enabled
    @property
    def tool_result_trim_threshold(self) -> int: return self.tool_trim.threshold
    @property
    def tool_trim_bash_tail_ratio(self) -> float: return self.tool_trim.bash_tail_ratio
    @property
    def tool_trim_read_window_lines(self) -> int: return self.tool_trim.read_window_lines
    @property
    def tool_trim_grep_max_lines(self) -> int:  return self.tool_trim.grep_max_lines
    @property
    def raw_store_enabled(self) -> bool: return self.tool_trim.raw_store_enabled
    @property
    def smart_summary_enabled(self) -> bool: return self.tool_trim.smart_summary_enabled

    @property
    def skill_semantic_enabled(self) -> bool:   return self.skill.semantic_enabled
    @property
    def skill_semantic_threshold(self) -> float: return self.skill.semantic_threshold
    @property
    def skill_tracking_enabled(self) -> bool:   return self.skill.tracking_enabled
    @property
    def skill_chunking_enabled(self) -> bool:   return self.skill.chunking_enabled
    @property
    def skill_compact_budget(self) -> int:      return self.skill.compact_budget
    @property
    def skill_compact_per_skill(self) -> int:   return self.skill.compact_per_skill
    @property
    def skill_keyword_activation_enabled(self) -> bool: return self.skill.keyword_activation_enabled

    @property
    def project_scan_enabled(self) -> bool:     return self.perception.project_scan_enabled
    @property
    def file_watch_enabled(self) -> bool:       return self.perception.file_watch_enabled
    @property
    def tool_cache_enabled(self) -> bool:       return self.perception.tool_cache_enabled
    @property
    def token_estimate_enabled(self) -> bool:   return self.perception.token_estimate_enabled
    @property
    def token_warn_threshold(self) -> float:    return self.perception.token_warn_threshold
    @property
    def tool_stats_enabled(self) -> bool:       return self.perception.tool_stats_enabled
    @property
    def artifact_auto_detect_enabled(self) -> bool: return self.perception.artifact_auto_detect_enabled

    @property
    def workdir_knowledge_enabled(self) -> bool: return self.workdir_knowledge.enabled

    @property
    def global_knowledge_enabled(self) -> bool: return self.global_knowledge.enabled
    @property
    def observability_enabled(self) -> bool: return self.observability.enabled
    @property
    def tracing_enabled(self) -> bool: return self.observability.enabled and self.observability.tracing_enabled

    @property
    def profile_enabled(self) -> bool:          return self.profile.enabled
    @property
    def profile_refresh_interval_entries(self) -> int: return self.profile.refresh_interval_entries
    @property
    def profile_min_entries(self) -> int:       return self.profile.min_entries

    @property
    def session_dir(self) -> Optional[Path]:    return self.session.dir
    @property
    def session_fmt(self) -> str:               return self.session.fmt
    @property
    def auto_save_session(self) -> bool:        return self.session.auto_save
    @property
    def session_summary_enabled(self) -> bool:  return self.session.summary_enabled
    @property
    def session_summary_min_turns(self) -> int: return self.session.summary_min_turns
    @property
    def session_search_enabled(self) -> bool:   return self.session.search_enabled

    @property
    def debug_llm(self) -> bool:                return self.debug.llm_enabled
    @property
    def debug_llm_console(self) -> bool:        return self.debug.llm_console
    @property
    def debug_log_dir(self) -> Optional[Path]:  return self.debug.log_dir

    @property
    def http_enabled(self) -> bool:             return self.http.enabled
    @property
    def http_host(self) -> str:                 return self.http.host
    @property
    def http_port(self) -> int:                 return self.http.port
    @property
    def http_api_token(self) -> str:            return self.http.api_token
    @property
    def http_allowed_ips(self) -> list:         return self.http.allowed_ips
    @property
    def http_cors_origins(self) -> list:        return self.http.cors_origins
    @property
    def http_fs_readonly(self) -> bool:         return self.http.fs_readonly
    @property
    def http_fs_excludes(self) -> list:         return self.http.fs_excludes
    @property
    def http_ring_maxlen(self) -> int:          return self.http.ring_maxlen
    @property
    def http_multi_user_enabled(self) -> bool:  return self.http.multi_user_enabled

    @property
    def llm_retry_max(self) -> int:             return self.retry.max_retries
    @property
    def llm_retry_delay(self) -> float:         return self.retry.delay
    @property
    def llm_retry_verbose(self) -> bool:        return self.retry.verbose
    @property
    def llm_retry_backoff_mode(self) -> str:    return self.retry.backoff_mode
    @property
    def llm_retry_backoff_step(self) -> float:  return self.retry.backoff_step
    @property
    def llm_retry_backoff_max_delay(self) -> float: return self.retry.backoff_max_delay
    @property
    def llm_network_aware(self) -> bool:         return self.retry.network_aware
    @property
    def llm_network_check_interval(self) -> float: return self.retry.network_check_interval
    @property
    def llm_network_max_wait(self) -> float:     return self.retry.network_max_wait

    @property
    def ensemble_enabled(self) -> bool:          return self.ensemble.mode != "off"
    @property
    def ensemble_mode(self) -> str:              return self.ensemble.mode


