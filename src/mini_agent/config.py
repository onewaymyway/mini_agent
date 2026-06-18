"""
Configuration management.
Loads CLAUDE.md project context, .env settings, and tracks session state.

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
    # 同一 turn 内最多注入的 reminder 条数（避免大量 reminder 污染上下文）
    max_per_turn: int = 3
    # 调试：打印匹配到的 reminder 名称
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
    role_agent: RoleAgentConfig  = field(default_factory=RoleAgentConfig)
    env_info:   EnvInfoConfig    = field(default_factory=EnvInfoConfig)

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


# ════════════════════════════════════════════════════════════════════════════════
# load_config：平坦 JSON/CLI 参数 → 组装子配置块 → 返回 AppConfig
# ════════════════════════════════════════════════════════════════════════════════

def _parse_name_list(value) -> list:
    """把 CLI 字符串（逗号分隔）或列表统一转为 list[str]。
    例：
      "evaluator,coach"  → ["evaluator", "coach"]
      ["evaluator"]      → ["evaluator"]
      []                 → []
      None               → []
    """
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value if v]


def load_config(
    project_root: Optional[Path] = None,
    extra_system: Optional[str] = None,
    verbose: Optional[bool] = None,
    sandbox: Optional[bool] = None,
    auto_approve: Optional[bool] = None,
    model: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    use_system_tool_call: Optional[bool] = None,
    debug_llm: Optional[bool] = None,
    debug_llm_console: Optional[bool] = None,
    max_llm_calls: Optional[int] = None,
    session_dir: Optional[Path] = None,
    session_fmt: Optional[str] = None,
    auto_save_session: Optional[bool] = None,
    agent_name: Optional[str] = None,
    system_message_format: Optional[str] = None,
    config_file: Optional[Path] = None,
    claude_md_file: Optional[str] = None,
    # 以下保持与旧签名兼容，内部组装到子配置块
    memory_enabled: Optional[bool] = None,
    memory_top_k: Optional[int] = None,
    memory_global_enabled: Optional[bool] = None,
    memory_global_top_k: Optional[int] = None,
    session_summary_enabled: Optional[bool] = None,
    session_summary_min_turns: Optional[int] = None,
    session_search_enabled: Optional[bool] = None,
    auto_compress_enabled: Optional[bool] = None,
    auto_compress_threshold: Optional[float] = None,
    tool_result_trim_enabled: Optional[bool] = None,
    tool_result_trim_threshold: Optional[int] = None,
    tool_trim_bash_tail_ratio: Optional[float] = None,
    tool_trim_read_window_lines: Optional[int] = None,
    tool_trim_grep_max_lines: Optional[int] = None,
    forget_policy_enabled: Optional[bool] = None,
    skill_semantic_enabled: Optional[bool] = None,
    skill_semantic_threshold: Optional[float] = None,
    skill_tracking_enabled: Optional[bool] = None,
    skill_chunking_enabled: Optional[bool] = None,
    skill_compact_budget: Optional[int] = None,
    skill_compact_per_skill: Optional[int] = None,
    project_scan_enabled: Optional[bool] = None,
    file_watch_enabled: Optional[bool] = None,
    tool_cache_enabled: Optional[bool] = None,
    token_estimate_enabled: Optional[bool] = None,
    token_warn_threshold: Optional[float] = None,
    tool_stats_enabled: Optional[bool] = None,
    # reminder 系统
    reminder_enabled: Optional[bool] = None,
    reminders_dir: Optional[Path] = None,
    reminder_verbose: Optional[bool] = None,
    # role agent 系统
    role_agent_enabled: Optional[bool] = None,
    role_agent_allow: Optional[list] = None,
    role_agent_block: Optional[list] = None,
    role_agent_dir: Optional[Path] = None,
    # 重试退避策略
    llm_retry_backoff_mode: Optional[str] = None,
    llm_retry_backoff_step: Optional[float] = None,
    llm_retry_backoff_max_delay: Optional[float] = None,
) -> AppConfig:
    """
    加载配置，优先级（高→低）：JSON 配置文件 > CLI 参数 > 环境变量 > 内置默认值。
    返回 AppConfig（含已组装好的子配置块）。
    """
    root = project_root or Path.cwd()

    # ── JSON 配置文件 ─────────────────────────────────────────────────────────
    file_cfg: dict = {}
    if config_file is not None:
        file_cfg = _load_config_file(config_file)
    else:
        default_cfg_path = root / "agent_config.json"
        if default_cfg_path.exists():
            file_cfg = _load_config_file(default_cfg_path)

    def _f(key, cli_val, default=None):
        """CLI 参数 > 配置文件 > 默认值"""
        if cli_val is not None:
            return cli_val
        return file_cfg[key] if key in file_cfg else default

    def _fb(key, cli_val, default=False):
        """CLI 参数 > 配置文件 > 默认值（bool 版）"""
        if cli_val is not None:
            return bool(cli_val)
        if key in file_cfg:
            return bool(file_cfg[key])
        return default

    def _fn(key, cli_val, default):
        """CLI 参数 > 配置文件 > 默认值（数值版，类型与 default 一致）"""
        if cli_val is not None:
            return type(default)(cli_val)
        if key in file_cfg:
            return type(default)(file_cfg[key])
        return default

    # ── 核心参数 ──────────────────────────────────────────────────────────────
    api_key   = os.environ.get("ANTHROPIC_API_KEY", "")
    # claude_md_file 优先级：CLI 参数 > 配置文件 > 默认 "CLAUDE.md"
    _claude_md_filename = (
        claude_md_file
        or file_cfg.get("claude_md_file")
        or "CLAUDE.md"
    )
    claude_md = _read_claude_md(root, filename=_claude_md_filename)
    skills_dir = _resolve_skills_dir(root)
    prompts_dir = _resolve_prompts_dir(root)

    _llm_provider = _f("provider", llm_provider) or os.environ.get("LLM_PROVIDER", "anthropic")
    _llm_base_url = _f("base_url", llm_base_url) or os.environ.get("LLM_BASE_URL", "")
    _model = _f("model", model) or os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)
    _verbose      = bool(_fb("verbose",      verbose,      False))
    _sandbox      = bool(_fb("sandbox",      sandbox,      False))
    _auto_approve = bool(_fb("yes",          auto_approve, False))
    _extra_system = _f("system", extra_system) or ""
    _max_llm_calls_v = _f("max_llm_calls", max_llm_calls)
    _max_llm_calls = int(_max_llm_calls_v) if _max_llm_calls_v is not None else int(os.environ.get("MAX_LLM_CALLS", 8))
    _agent_name = _f("agent_name", agent_name) or os.environ.get("AGENT_NAME", DEFAULT_AGENT_NAME)

    # CLI > 配置文件 > 环境变量 > 默认
    _use_sys_tc_env = os.environ.get("LLM_SYSTEM_TOOL_CALL", "").lower() in ("1", "true", "yes")
    _use_sys_tc = bool(_fb("system_tool_call", use_system_tool_call, _use_sys_tc_env))

    _sys_msg_fmt_cli = system_message_format or os.environ.get("LLM_SYSTEM_MESSAGE_FORMAT", "system_field")
    _sys_msg_fmt = _f("system_message_format", _sys_msg_fmt_cli) or "system_field"
    if _sys_msg_fmt not in ("system_field", "system_role"):
        import warnings
        warnings.warn(f"[config] Unknown system_message_format={_sys_msg_fmt!r}, falling back to 'system_field'.")
        _sys_msg_fmt = "system_field"

    # ── 组装子配置块 ──────────────────────────────────────────────────────────

    _mem_path_str = file_cfg.get("memory_store_path", "")
    _mem_path = Path(_mem_path_str) if _mem_path_str else None

    memory_cfg = MemoryConfig(
        enabled=_fb("memory_enabled", memory_enabled),
        backend=_f("memory_backend", None) or "local",
        store_path=_mem_path,
        global_enabled=_fb("memory_global_enabled", memory_global_enabled, True),
        global_top_k=_fn("memory_global_top_k", memory_global_top_k, 2),
        top_k=_fn("memory_top_k", memory_top_k, 3),
        decay_half_life_days=_fn("memory_decay_half_life_days", None, 30.0),
        max_entries=_fn("memory_max_entries", None, 500),
    )

    compress_cfg = CompressConfig(
        enabled=_fb("auto_compress_enabled", auto_compress_enabled),
        threshold=_fn("auto_compress_threshold", auto_compress_threshold, 0.7),
        strategy=_f("auto_compress_strategy", None) or "turn_aligned",
        forget_orphan_tool_results=_fb("forget_policy_enabled", forget_policy_enabled),
    )

    tool_trim_cfg = ToolTrimConfig(
        enabled=_fb("tool_result_trim_enabled", tool_result_trim_enabled),
        threshold=_fn("tool_result_trim_threshold", tool_result_trim_threshold, 4000),
        bash_tail_ratio=_fn("tool_trim_bash_tail_ratio", tool_trim_bash_tail_ratio, 0.6),
        read_window_lines=_fn("tool_trim_read_window_lines", tool_trim_read_window_lines, 0),
        grep_max_lines=_fn("tool_trim_grep_max_lines", tool_trim_grep_max_lines, 50),
    )

    skill_cfg = SkillConfig(
        semantic_enabled=_fb("skill_semantic_enabled", skill_semantic_enabled),
        semantic_threshold=_fn("skill_semantic_threshold", skill_semantic_threshold, 0.72),
        tracking_enabled=_fb("skill_tracking_enabled", skill_tracking_enabled),
        chunking_enabled=_fb("skill_chunking_enabled", skill_chunking_enabled),
        compact_budget=_fn("skill_compact_budget", skill_compact_budget, 25_000),
        compact_per_skill=_fn("skill_compact_per_skill", skill_compact_per_skill, 5_000),
        matcher=_f("skill_matcher", None) or "keyword",
    )

    perception_cfg = PerceptionConfig(
        project_scan_enabled=_fb("project_scan_enabled", project_scan_enabled),
        file_watch_enabled=_fb("file_watch_enabled", file_watch_enabled),
        tool_cache_enabled=_fb("tool_cache_enabled", tool_cache_enabled),
        tool_cache_max_entries=_fn("tool_cache_max_entries", None, 256),
        token_estimate_enabled=_fb("token_estimate_enabled", token_estimate_enabled),
        token_warn_threshold=_fn("token_warn_threshold", token_warn_threshold, 0.75),
        tool_stats_enabled=_fb("tool_stats_enabled", tool_stats_enabled),
    )

    _session_dir_str = (
        (str(session_dir) if session_dir else "")
        or file_cfg.get("session_dir")
        or os.environ.get("SESSION_DIR", "")
    )
    session_cfg = SessionConfig(
        dir=Path(_session_dir_str) if _session_dir_str else None,
        fmt=_f("session_fmt", session_fmt) or os.environ.get("SESSION_FMT", "json"),
        auto_save=_fb("no_save_session", None, False) is False
                  and _fb("auto_save_session", auto_save_session, True),
        summary_enabled=_fb("session_summary_enabled", session_summary_enabled),
        summary_min_turns=_fn("session_summary_min_turns", session_summary_min_turns, 4),
        search_enabled=_fb("session_search_enabled", session_search_enabled),
        backend=_f("session_backend", None) or "local",
    )

    profile_cfg = ProfileConfig(
        enabled=_fb("profile_enabled", None),
        refresh_interval_entries=_fn("profile_refresh_interval_entries", None, 3),
        min_entries=_fn("profile_min_entries", None, 1),
        max_entries_for_profile=_fn("profile_max_entries_for_profile", None, 20),
    )

    _env_debug     = os.environ.get("LLM_DEBUG", "").lower() in ("1", "true", "yes")
    _env_debug_con = os.environ.get("LLM_DEBUG_CONSOLE", "").lower() in ("1", "true", "yes")
    _debug_llm_v     = bool(_fb("debug_llm",         debug_llm,         _env_debug))
    _debug_console_v = bool(_fb("debug_llm_console", debug_llm_console, _env_debug_con))
    _debug_log_dir_str = file_cfg.get("debug_log_dir") or os.environ.get("LLM_DEBUG_LOG_DIR", "")
    _debug_log_dir = Path(_debug_log_dir_str) if _debug_log_dir_str else None
    debug_cfg = DebugConfig(
        llm_enabled=_debug_llm_v,
        llm_console=_debug_console_v,
        log_dir=_debug_log_dir,
    )
    http_cfg = HttpConfig(
        enabled=_fb("http_enabled", None),
        host=_f("http_host", None) or "127.0.0.1",
        port=int(_f("http_port", None) or 8765),
        api_token=_f("http_api_token", None) or "",
        allowed_ips=file_cfg.get("http_allowed_ips", ["127.0.0.1", "::1"]),
        cors_origins=file_cfg.get("http_cors_origins", []),
        fs_readonly=_fb("http_fs_readonly", None),
        fs_excludes=file_cfg.get("http_fs_excludes", []),
        ring_maxlen=int(_f("http_ring_maxlen", None) or 2000),
    )

    retry_cfg = RetryConfig(
        max_retries=_fn("llm_retry_max", None, 15),
        delay=_fn("llm_retry_delay", None, 5.0),
        verbose=_fb("llm_retry_verbose", None, True),
        backoff_mode=_f("llm_retry_backoff_mode", llm_retry_backoff_mode) or "fixed",
        backoff_step=_fn("llm_retry_backoff_step", llm_retry_backoff_step, 60.0),
        backoff_max_delay=_fn("llm_retry_backoff_max_delay", llm_retry_backoff_max_delay, 0.0),
    )

    # ── LLM Fallback Chain ────────────────────────────────────────────────────
    # 从配置文件读取（CLI 不支持直接传 chain，只能通过 JSON 配置文件）
    _llm_fallback_chain: list = file_cfg.get("llm_fallback_chain", [])
    _llm_fallback_on_raw = file_cfg.get("llm_fallback_on", None)
    _llm_fallback_on: Optional[list] = list(_llm_fallback_on_raw) if _llm_fallback_on_raw else None

    # ── 初始化调试日志（需要在 AppConfig 构建前完成）─────────────────────────
    if _debug_llm_v:
        from mini_agent.llm.debug_logger import DebugConfig as LLMDebugConfig, init_debug_logger
        _dcfg = LLMDebugConfig(
            enabled=True,
            log_to_file=True,
            log_to_console=_debug_console_v,
            log_dir=_debug_log_dir,  # None = session-relative path resolved by debug_logger
        )
        init_debug_logger(_dcfg, root)

    # ── MCP server 配置解析 ───────────────────────────────────────────────────
    mcp_servers_raw: list[dict] = file_cfg.get("mcp_servers", [])
    mcp_server_list: list[MCPServerConfig] = []
    for raw in mcp_servers_raw:
        if not isinstance(raw, dict):
            continue
        mcp_server_list.append(MCPServerConfig(
            name=raw.get("name", "unnamed"),
            transport=raw.get("transport", "stdio"),
            command=raw.get("command", ""),
            args=raw.get("args", []),
            env=raw.get("env", {}),
            url=raw.get("url", ""),
            auto_approve=bool(raw.get("auto_approve", False)),
            timeout=float(raw.get("timeout", 10.0)),
            enabled=bool(raw.get("enabled", True)),
        ))
    mcp_cfg = MCPConfig(servers=mcp_server_list)

    _ws = file_cfg.get("web_search") if isinstance(file_cfg.get("web_search"), dict) else {}
    web_search_cfg = WebSearchConfig(
        provider=_ws.get("provider") or os.environ.get("WEB_SEARCH_PROVIDER", "duckduckgo"),
        api_key=_ws.get("api_key") or os.environ.get("WEB_SEARCH_API_KEY", ""),
        max_results=int(_ws.get("max_results") or os.environ.get("WEB_SEARCH_MAX_RESULTS", 5)),
        timeout=float(_ws.get("timeout") or os.environ.get("WEB_SEARCH_TIMEOUT", 10.0)),
    )

    _rm = file_cfg.get("reminder") if isinstance(file_cfg.get("reminder"), dict) else {}
    _reminder_enabled_val = reminder_enabled if reminder_enabled is not None else _rm.get("enabled", True)
    _reminders_dir_val: Optional[Path] = None
    if reminders_dir is not None:
        _reminders_dir_val = Path(reminders_dir)
    elif _rm.get("custom_dir"):
        _reminders_dir_val = Path(_rm["custom_dir"])
    reminder_cfg = ReminderConfig(
        enabled=bool(_reminder_enabled_val),
        custom_dir=_reminders_dir_val,
        tool_error_enabled=bool(_rm.get("tool_error_enabled", True)),
        post_tool_enabled=bool(_rm.get("post_tool_enabled", True)),
        user_intent_enabled=bool(_rm.get("user_intent_enabled", True)),
        pattern_enabled=bool(_rm.get("pattern_enabled", True)),
        max_per_turn=int(_rm.get("max_per_turn", 3)),
        verbose=bool(reminder_verbose if reminder_verbose is not None else _rm.get("verbose", False)),
    )

    # ── RoleAgent 配置组装 ────────────────────────────────────────────────────
    _ra = file_cfg.get("role_agent") if isinstance(file_cfg.get("role_agent"), dict) else {}
    # 总开关：CLI 参数 > 配置文件 > 默认 False（默认不启用）
    _ra_enabled = role_agent_enabled if role_agent_enabled is not None else _ra.get("enabled", False)
    # 白名单：CLI 参数（逗号分隔字符串或列表）> 配置文件 > 空列表（全部启用）
    _ra_allow = _parse_name_list(role_agent_allow) if role_agent_allow is not None else _parse_name_list(_ra.get("allow", []))
    # 黑名单：CLI 参数 > 配置文件 > 空列表（不屏蔽）
    _ra_block = _parse_name_list(role_agent_block) if role_agent_block is not None else _parse_name_list(_ra.get("block", []))
    # 自定义目录：CLI 参数 > 配置文件 > None（使用默认）
    _ra_dir: Optional[Path] = None
    if role_agent_dir is not None:
        _ra_dir = Path(role_agent_dir).expanduser()
    elif _ra.get("agents_dir"):
        _ra_dir = Path(_ra["agents_dir"]).expanduser()
    role_agent_cfg = RoleAgentConfig(
        enabled=bool(_ra_enabled),
        allow=_ra_allow,
        block=_ra_block,
        agents_dir=_ra_dir,
    )

    # ── EnvInfo 配置组装 ────────────────────────────────────────────────────
    _ei = file_cfg.get("env_info") if isinstance(file_cfg.get("env_info"), dict) else {}
    _ei_enabled = bool(_ei.get("enabled", True))
    _ei_providers = _ei.get("providers", None)
    _ei_provider_kwargs: dict = _ei.get("provider_kwargs", {})
    _ei_include_hostname = bool(_ei.get("include_hostname", False))
    _ei_include_username = bool(_ei.get("include_username", False))
    if _ei_include_hostname or _ei_include_username:
        sys_kwargs = _ei_provider_kwargs.setdefault("builtin.system", {})
        sys_kwargs.setdefault("include_hostname", _ei_include_hostname)
        sys_kwargs.setdefault("include_username", _ei_include_username)
    env_info_cfg = EnvInfoConfig(
        enabled=_ei_enabled,
        providers=_ei_providers,
        provider_kwargs=_ei_provider_kwargs,
        include_hostname=_ei_include_hostname,
        include_username=_ei_include_username,
    )

    return AppConfig(
        api_key=api_key,
        model=_model,
        max_tokens=int(file_cfg.get("max_tokens") or os.environ.get("CLAUDE_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        project_root=root,
        skills_dir=skills_dir,
        prompts_dir=prompts_dir,
        verbose=_verbose,
        sandbox=_sandbox,
        auto_approve=_auto_approve,
        claude_md_content=claude_md,
        system_extra=_extra_system,
        llm_provider=_llm_provider,
        llm_base_url=_llm_base_url,
        use_system_tool_call=_use_sys_tc,
        max_llm_calls=_max_llm_calls,
        agent_name=_agent_name,
        system_message_format=_sys_msg_fmt,
        # 子配置块
        memory=memory_cfg,
        compress=compress_cfg,
        tool_trim=tool_trim_cfg,
        skill=skill_cfg,
        perception=perception_cfg,
        session=session_cfg,
        profile=profile_cfg,
        debug=debug_cfg,
        http=http_cfg,
        retry=retry_cfg,
        mcp=mcp_cfg,
        web_search=web_search_cfg,
        reminder=reminder_cfg,
        role_agent=role_agent_cfg,
        env_info=env_info_cfg,
        llm_fallback_chain=_llm_fallback_chain,
        llm_fallback_on=_llm_fallback_on,
    )


# ── 辅助函数（不变）──────────────────────────────────────────────────────────

def _load_config_file(path: Path) -> dict:
    import json as _json
    import warnings as _warnings
    try:
        text = path.read_text(encoding="utf-8")
        data = _json.loads(text)
        if not isinstance(data, dict):
            _warnings.warn(f"[config] {path}: expected a JSON object. Ignored.")
            return {}
        return data
    except FileNotFoundError:
        _warnings.warn(f"[config] Config file not found: {path}")
        return {}
    except _json.JSONDecodeError as e:
        _warnings.warn(f"[config] Failed to parse config file {path}: {e}")
        return {}
    except Exception as e:
        _warnings.warn(f"[config] Error loading config file {path}: {e}")
        return {}


def _read_claude_md(root: Path, filename: str = "CLAUDE.md") -> str:
    """读取项目上下文文档。

    Args:
        root: 项目根目录。
        filename: 要加载的文档名（默认 CLAUDE.md）。
                  文件不存在时返回空字符串，不抛出异常。
    """
    for d in [root] + list(root.parents)[:3]:
        p = d / filename
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                pass
    return ""


def _resolve_prompts_dir(root: Path) -> Optional[Path]:
    """
    解析用户自定义 prompts 目录。

    查找顺序（优先级从高到低）：
    1. <project_root>/.agent/prompts/   — 项目级自定义 prompt
    2. ~/.agent/prompts/                — 全局自定义 prompt

    若均不存在，返回 None，PromptManager 将仅使用项目内置默认 prompts 目录
    （src/mini_agent/prompts/）。
    """
    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(root)
    for c in (paths.workdir_prompts_dir, paths.global_prompts_dir):
        if c.is_dir():
            return c
    return None


def _resolve_skills_dir(root: Path) -> Optional[Path]:
    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(root)
    candidates = [
        root / ".claude" / "skills",           # 旧路径，兼容保留
        paths.global_skills_dir,               # ~/.agent/skills（新路径）
        Path.home() / ".claude" / "skills",    # 旧全局路径，兼容保留
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def build_system_prompt(cfg: AppConfig, active_skills: list[str], skill_context: str = "", user_profile: str = "") -> str:
    from datetime import datetime
    from mini_agent.prompts import pm
    if cfg.prompts_dir and pm.custom_dir != cfg.prompts_dir:
        pm.set_custom_dir(cfg.prompts_dir)

    # 采集环境信息
    env_info_block = ""
    if cfg.env_info.enabled:
        try:
            from mini_agent.env_info.registry import EnvInfoRegistry
            registry = EnvInfoRegistry.from_config(
                providers=cfg.env_info.providers,
                provider_kwargs=cfg.env_info.provider_kwargs,
            )
            env_info_block = registry.build_block()
        except Exception:
            pass

    return pm.build_system_prompt(
        claude_md_content=cfg.claude_md_content,
        active_skills=active_skills,
        skill_context=skill_context,
        system_extra=cfg.system_extra,
        sandbox=cfg.sandbox,
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S %A"),
        agent_name=cfg.agent_name,
        user_profile=user_profile,
        env_info=env_info_block,
    )