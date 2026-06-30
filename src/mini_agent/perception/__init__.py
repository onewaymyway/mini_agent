"""
perception — 感知与记忆子系统。

各功能均为独立模块，通过 AppConfig 里的开关启用/禁用：
  cfg.memory_enabled          → 跨 session 长期记忆
  cfg.session_summary_enabled → session 摘要化
  cfg.session_search_enabled  → session 语义搜索
  cfg.auto_compress_enabled   → 自动上下文压缩
  cfg.tool_result_trim_enabled→ 工具调用结果截断
  cfg.forget_policy_enabled   → 智能遗忘策略
  cfg.skill_semantic_enabled  → 技能语义匹配
  cfg.skill_tracking_enabled  → 技能使用追踪
  cfg.skill_chunking_enabled  → 技能内容裁剪
  cfg.project_scan_enabled    → 项目结构感知
  cfg.file_watch_enabled      → 文件变化感知
  cfg.tool_cache_enabled      → 工具调用结果缓存
  cfg.token_estimate_enabled  → token 用量预估
  cfg.tool_stats_enabled      → 工具调用统计
"""

from .token_counter import estimate_tokens
from .project_scanner import ProjectScanner, ProjectSnapshot
from .affordance_analyzer import AffordanceMap, AffordanceAnalyzer
from .self_model import AgentSelfModel, AgentSelfModelBuilder
from .file_watcher import FileWatcher
from .tool_cache import ToolResultCache
from .memory_store import MemoryStore, MemoryEntry
from .workdir_knowledge import (
    ProjectMeta, WorkThread, OpenThread, KnowledgeIndexEntry,
    ensure_project_meta, load_project_meta,
    capture_environment_fingerprint, detect_environment_drift,
    append_timeline_entry, load_recent_timeline,
    load_work_index, save_work_index, get_active_work_threads,
    find_work_thread, upsert_work_thread, relate_session_to_work_thread,
    load_open_threads, save_open_threads, add_open_thread,
    import_unresolved_from_manifest, get_high_priority_open_threads,
    load_knowledge_index, save_knowledge_index, upsert_knowledge_index_entry,
    search_knowledge_index, read_knowledge_section,
)

__all__ = [
    "estimate_tokens",
    "ProjectScanner", "ProjectSnapshot",
    "AffordanceMap", "AffordanceAnalyzer",
    "AgentSelfModel", "AgentSelfModelBuilder",
    "FileWatcher",
    "ToolResultCache",
    "MemoryStore", "MemoryEntry",
    "ProjectMeta", "WorkThread", "OpenThread", "KnowledgeIndexEntry",
    "ensure_project_meta", "load_project_meta",
    "capture_environment_fingerprint", "detect_environment_drift",
    "append_timeline_entry", "load_recent_timeline",
    "load_work_index", "save_work_index", "get_active_work_threads",
    "find_work_thread", "upsert_work_thread", "relate_session_to_work_thread",
    "load_open_threads", "save_open_threads", "add_open_thread",
    "import_unresolved_from_manifest", "get_high_priority_open_threads",
    "load_knowledge_index", "save_knowledge_index", "upsert_knowledge_index_entry",
    "search_knowledge_index", "read_knowledge_section",
]
