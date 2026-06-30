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
    Stage 8 Phase G），不产生任何自主行为。
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
    """
    max_retries: int = 15
    delay: float = 5.0
    verbose: bool = True
    backoff_mode: str = "fixed"          # "fixed" | "linear" | "exponential"
    backoff_step: float = 60.0           # linear: 步长(s)；exponential: 倍数(>1.0)
    backoff_max_delay: float = 0.0       # 0 = 不限制上限


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
    # 是否把每轮快照写入 traces.jsonl（供 Phase G 后续分析趋势）
    trace_enabled: bool = True
    # 调试：打印每轮快照
    verbose: bool = False


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
    workflow:   WorkflowConfig   = field(default_factory=WorkflowConfig)
    format_correction: FormatCorrectionConfig = field(default_factory=FormatCorrectionConfig)
    role_agent: RoleAgentConfig  = field(default_factory=RoleAgentConfig)
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
    def ensemble_enabled(self) -> bool:          return self.ensemble.mode != "off"
    @property
    def ensemble_mode(self) -> str:              return self.ensemble.mode


