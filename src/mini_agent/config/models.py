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

    # ── wiki 转正为主检索路径（wiki 式知识库改进计划 P4）────────────────────
    # 默认开：context_builder 每轮检索时优先尝试 wiki_search（三段式，需要
    # llm_call 才能产出 grounded_page_ids），命中则采用其结果并跳过
    # shelf_search；未命中（wiki/ 为空、没有 grounded 结果、wiki_search 本身
    # 异常）时自动退回原有 shelf_search → merge_search → 全库 search 链路，
    # 行为与切换前完全一致，不存在"切换后反而检索不到东西"的风险。
    # 关闭（设为 False）即可完全退回旧默认路径，等价于本次改动前的行为；
    # `/wiki promotion` 可以随时查看 wiki 侧当前是否已经稳定达到 P4 定义的
    # 三项转正标准，作为要不要继续保持这条路径开启的参考。
    library_wiki_search_primary: bool = True

    # ── 每轮自动检索总闸（用户可关闭"处理用户输入前自动检索文档/记忆"这一行为）──
    # 默认开，行为与改动前完全一致。关闭后 refresh_turn_context() 在填充
    # turn 级缓存前直接返回，本轮不会触发 wiki_search / shelf_search /
    # merge_search 中的任何一条检索链路（既不注入 "## Relevant past
    # experience" 片段，也不产生检索开销）；记忆的写入、lesson/纠正检测、
    # consolidation 等其他功能不受影响，因为它们不经过这个入口。
    per_turn_retrieval_enabled: bool = True

    # ── wiki 式知识库（wiki式知识库重构计划.md，perception/library_index.py
    #    的 wiki_paths 参数）─────────────────────────────────────────────
    wiki_enabled: bool = True  # 过渡期双写总开关；关闭则 LibraryIndex.wiki_paths=None，wiki/ 完全不被触碰

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

    # ── wiki 提取层与组织层改进计划 O1：全量扫描架构分层 ────────────────────
    # wiki_shelf_search 是否优先复用 indexer.py 生成的 _index/ 派生索引做
    # 规则粗筛（索引缺失/明显过期时自动退回全量 parse_page 扫描，行为不变，
    # 只是数据来源不同）。默认开，关闭即完全等价于本次改动前的全量扫描行为。
    wiki_index_reuse_enabled: bool = True
    # _rule_score 里 grounded_hit_count 信度加权的系数（见 wiki/search.py::
    # _rule_score 公式 score = 0.6*token_jaccard + 0.4*tag_jaccard +
    # confidence_weight*log(1+grounded_hit_count)）。默认较小，避免信度
    # 权重压过内容相关性本身；设为 0 等价于改动前的排序结果（回归保护）。
    wiki_confidence_weight: float = 0.1

    # ── persona_capability_learning_design.md §14.1-a：使用驱动学习 ────────
    # 当前激活 persona 声明了 wiki_scopes、且该 scope 命中某个 active 的
    # knowledge 型 CapabilityTrack.wiki_tag 时，wiki_search 未命中（含未
    # 配置/异常/零 grounded）会记一条 miss_observed 台账（见
    # evolution/capability_learning.py::record_wiki_miss），供下一轮
    # capability learning 循环提高该 Track 缺口优先级。默认开；只在
    # persona 绑定了 wiki_scopes 时才可能触发，未绑定 persona 或未声明
    # wiki_scopes 的场景零开销。关闭后完全跳过这一步记录，不影响检索
    # 本身的既有行为。
    capability_wiki_miss_tracking_enabled: bool = True

    # ── wiki/quarantine_repair.py：隔离区页面 LLM 兜底修复 ─────────────────
    # 默认关闭（opt-in），复用 `llm_helper` opt-in 模式（架构决策：涉及文本
    # 理解/归类的新功能一律走 LLM，不引入 embedding 模型）。规则式修复策略
    # （_FIXERS，只处理"改法唯一"的笔误）兜底失败后，如果这个开关打开且
    # 调用方（cron handler / CLI）传入了可用的 llm_helper，才会额外尝试一次
    # LLM 修复；关闭时行为与改动前完全一致（零 LLM 成本）。
    wiki_quarantine_llm_repair_enabled: bool = False

    # ── wiki 提取层与组织层改进计划 O4：统一知识生命周期状态机 ──────────────
    # `wiki/lifecycle.py::stale_candidate_scan()` 的判定阈值（天）：
    # `knowledge_state=fresh` 且 `last_validated_at`（或历史遗留页面的
    # `created`）超过这个天数未被验证过，标记为 stale。
    lifecycle_stale_threshold_days: int = 90
    # 是否让 _rule_score 按 knowledge_state 打折（stale 减半、superseded
    # 归零）。默认关闭——按 O4 §7.4 风险条款的执行纪律，先只记录状态、
    # 观察 stale 标记比例是否合理，用真实数据校准后再决定是否默认开启，
    # 避免重蹈 P4 §6.5"零数据切换"的覆辙。
    lifecycle_discount_enabled: bool = False


@dataclass
class CompressConfig:
    """[SYS-COMPRESS] 自动上下文压缩配置。"""
    enabled: bool = False
    threshold: float = 0.7             # token 占用率超过此值触发压缩
    strategy: str = "compact_with_skills"  # "compact_with_skills"（默认，与手动 /compact 一致：LLM 摘要 + skill 重附）
                                            # | "turn_aligned" | "llm_summary" | "sliding_window" | "selective"（轻量可插拔策略，退回旧路径）
    forget_orphan_tool_results: bool = False  # 剔除保留段中无对应 tool_use 的 tool_result
    # ── wiki 提取层与组织层改进计划 E1：抽取时机与对话粒度解耦 ──────────────
    # 独立于 compact 的轻量抽取触发器（history/extraction_trigger.py +
    # history_manager.py::maybe_trigger_extraction）总开关。
    # [2026-07 默认开启]：观察期已确认零 LLM 成本的规则扫描不影响主循环
    # 性能/行为（见 extraction_trigger_log.jsonl 校准记录），应用户要求
    # 打开默认值。
    extraction_trigger_enabled: bool = True
    # 触发器命中后，是否真的发起一次独立的"仅抽取、不压缩" LLM 调用。
    # [2026-07 默认开启]：与 extraction_trigger_enabled 一起打开——原计划
    # §1.4 设想的"先只记日志、观测阈值合理后再打开"这一步，应用户明确要求
    # 提前执行，跳过了持续观测期。如果发现触发过于频繁（可查
    # extraction_trigger_log.jsonl 命中率），可以调低连接词密度阈值或把
    # 这个开关重新关掉，只退回 compact 路径的抽取。
    extraction_trigger_dispatch_enabled: bool = True
    # 轮次计数触发规则的阈值：距上次抽取满 N 轮真实用户输入且从未抽取过
    # （或连接词密度一直不够）时也触发一次，避免长期空转的 session 永远
    # 不被抽取（计划 §1.2.1 触发规则 2）。
    extraction_trigger_min_window_turns: int = 6

    extract_decisions: bool = True     # LLMSummaryStrategy 是否顺带提炼 decisions[] 并存入 pending 队列
                                        # （决策/取舍知识提炼计划 5.2 节；不增加额外 LLM 调用，仅解析开关）
    # ── wiki 提取层与组织层改进计划 E3：抽取"看不到"已有知识库的反向注入 ──
    # LLMSummaryStrategy 生成摘要/结构化抽取的 system prompt 里，是否注入一份
    # 已有实体索引摘要（wiki/entity_digest.py::build_entity_digest_section），
    # 供模型判断新识别的实体是否应复用已有 id（EntityCandidate.
    # reused_existing_id）。默认开；关闭则完全退回本次改动前的"裸识别"行为。
    entity_digest_enabled: bool = True
    entity_digest_max_entities: int = 40  # 注入 prompt 的实体条目数上限，控制 token 开销

    extract_world_model: bool = True   # LLMSummaryStrategy 是否顺带提炼 entities[]/facts[] 并存入 pending
                                        # 队列（wiki 改进计划 P1：世界模型抽取，与 extract_decisions 同一次
                                        # LLM 调用里附带解析，不增加额外调用，仅解析开关）
    decision_batch_min_interval_days: float = 1.0  # 巩固循环批量落盘时，"新建"决策页的冷却天数
                                        # （同一 topic 冷却期内跳过新建，只影响新建，不影响更新已有决策页）
    decision_recall_tool_enabled: bool = True  # 是否注册 recall_decisions 只读工具，供 agent 主动
                                        # 查询历史决策（路径 C：agent 自己意识到"这是个取舍决定"时调用）
    decision_recall_turn_gate_enabled: bool = False  # 是否在每轮用户消息进入时用启发式关键词门控
                                        # 自动触发一次决策召回（路径 B）；默认关闭，先观察启发式命中率
                                        # 再决定要不要默认开启，避免误报打断正常对话
    decision_recall_gate_k: int = 5    # 门控命中时 wiki_shelf_search 的候选数量上限

    # ── Compact 机制主动化改进计划（compact_mechanism_improvement_plan.md）──
    # P0-A：goal_mode 卡住恢复 compact 时，把尚未通过的验收标准作为提示追加到
    # compact_prompt，让摘要模型对相关内容保留更多细节。仅在 goal_mode 内生效，
    # 对普通对话/无验收标准场景无影响。
    goal_aware_weighting_enabled: bool = False

    # P0-B：compact_with_skills()（默认策略，供 goal_mode 卡住恢复 / /compact /
    # auto-compact 使用）压缩时顺带提取决策候选，入队交给巩固循环批量落盘。
    # 与 LLMSummaryStrategy 已有的 extract_decisions 是同一套解析/落盘逻辑，
    # 只是把触发点扩展到 compact_with_skills() 这条默认路径上，独立开关，
    # 默认关闭，不影响 extract_decisions 原有行为。
    decision_extraction_on_compact_with_skills_enabled: bool = False

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
    # 话题切换触发的最低 token 占用率门槛：低于此值时即使检测到话题切换也不触发
    # compact（历史本来就很短，压缩收益很小，且可能白白消耗一次 LLM 二次确认调用）。
    # 设为 0 表示不设门槛（旧行为）。仅当 ctx.budget_pct 有效估算（>0）时才生效，
    # 未启用 token 占用率估算的场景不受此项影响。
    topic_shift_min_budget_pct: float = 0.2

    # 冗余信息检测：tool_result 占比过高 / 重复调用堆积
    redundancy_detection_enabled: bool = False
    redundancy_tool_result_ratio: float = 0.6  # tool_result 消息占比超过此值触发

    # 冷却时间：compact 后这么多轮内，屏蔽除 token 硬阈值外的其他触发器，防止反复触发
    compact_cooldown_turns: int = 3

    # ── Compact 机制主动化改进计划 P1（compact_mechanism_improvement_plan.md）──
    # P1-A：安全点判定。开启后，非 token 硬阈值的触发命中若落在"最近一段工具调用
    # 链条包含有副作用操作（bash/写文件等）"的不安全点，会被挂起，等下一次到达
    # 安全点（turn 边界，或最近工具调用全是只读探索型）时才真正执行 compact，
    # 避免在一次多步骤执行序列中间被打断。默认关闭，不影响现有行为。
    safe_point_gating_enabled: bool = False

    # P1-B：触发信号强度叠加。开启后，当所有触发器都未硬命中时，会把每个触发器
    # 各自的"接近阈值程度"（intensity_hint，0~1）求和，超过阈值也视为命中一次
    # composite_intensity 软触发。用于捕捉"多个弱信号同时出现但都没单独越过
    # 自身阈值"的场景。默认关闭；阈值建议先用 observe 方式在预发环境收集
    # 若干次真实叠加强度分布后再调整。
    composite_intensity_enabled: bool = False
    composite_intensity_threshold: float = 1.2

    # P2-A：压缩质量事后自检 + lesson 反馈闭环。deep compact（非高频触发）完成后
    # 异步执行一次 LLM 校验，判断摘要是否遗漏决定性信息（约束条件/失败原因/
    # 用户明确要求）；发现遗漏时补一条 compact_supplement 历史条目，并写入
    # activity_digest.jsonl（type=compact_audit_issue）供后续统计分析。
    # 仅对 audit_compact_reasons 里列出的触发原因生效，避免每次高频触发都
    # 额外增加一次 LLM 调用成本。默认关闭。
    audit_enabled: bool = False
    audit_compact_reasons: list = field(
        default_factory=lambda: ["topic_shift_heuristic", "topic_shift_llm", "stuck_recovery_deep"]
    )
    audit_async: bool = True   # True=后台线程执行，不阻塞当前轮次；False=同步执行（主要用于测试）

    # 触发后是否需要用户确认才执行（False=全自动静默压缩，仅打印提示；
    # True=先询问用户 y/n，用户拒绝则本次跳过，下次再检查）
    require_confirmation: bool = False

    # ── Compact 预检配置 ─────────────────────────────────────────────────────
    # compact 前主动预估 token，超过阈值直接走分批路径，避免异常捕获开销
    compact_precheck_enabled: bool = True          # 是否启用预检（默认开启）
    compact_precheck_threshold: float = 0.85       # 估算 token 超过上下文窗口此比例视为超限
    model_context_window: int = 0                  # 模型上下文窗口大小（0=自动从 provider 获取或用默认）


    # ── 单条消息过长兜底截断（compact 专用）─────────────────────────────────
    # compact 会把（部分）历史消息整体发给 LLM 生成摘要。如果历史里混入了
    # 一条异常长的消息（典型场景：某次工具调用返回了超大输出，且因
    # raw_output/未走常规截断路径等原因绕过了 tool_executor 的输出截断），
    # 单条消息本身就可能超过模型的单次请求上限，导致该次 LLM 调用直接
    # 报错（而不是被模型"自动忽略"多余部分），进而拖垮整个 compact 流程
    # （chunked 路径下这类 chunk 会反复失败，因为拆分粒度是"轮"而非"消息"，
    # 巨型单消息无法再被切小）。
    #
    # 这里在送入 LLM 之前，对超过该字符数的单条消息做保留头尾、截断中段的
    # 兜底处理（仅用于 compact 摘要请求，不影响原始历史/记事本/正式回复）。
    max_message_chars_for_compact: int = 40_000


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
    # [SYS-SKILL-AUTO-UNLOAD] compact 时自动卸载长期未用的已激活 skill。
    # 默认开启：这是纯粹的上下文瘦身行为（只影响之后轮次的 system prompt
    # 注入内容），不影响已产生的历史记录，风险低。
    auto_unload_enabled: bool = True
    auto_unload_idle_seconds: int = 1800   # 超过 30 分钟未被实际调用即视为闲置
    # [SYS-SKILL-CANDIDATE-REMINDER] 问题0 修复：即使关键词自动激活关闭，也在每轮
    # 用户消息进入时检查是否有"看起来匹配但尚未激活"的 skill，命中则注入一条
    # reminder 提示模型先 skill_list/skill_activate 再动手，而不是让模型自己在一大段
    # system prompt 目录里判断。默认开启，成本很低（只是一次关键词匹配 + 可能的一条
    # 提示消息），可通过此开关关闭。
    candidate_reminder_enabled: bool = True


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

    # ── work_index.json 主动提醒（更主动地促成 update_work_thread 被调用）───
    # 此前 update_work_thread 完全依赖模型自主判断是否调用，没有任何主动
    # 提示，实践中很容易长期是空文件。加一层轻量启发式："这次 session 看起来
    # 干了不少活，但既没有关联到已有 WorkThread、也没有主动记录"，就在
    # *下一次* session 开始时提醒模型考虑补记——而不是在 SessionEnd hook 里
    # 直接自动创建 WorkThread（会退化成启发式误判也能自由发明工作线，
    # 违背 relate_session_to_work_thread() 原有的保守取舍）。
    proactive_reminder_enabled: bool = True
    # 触发提醒的最小 session 时长（分钟）——用来过滤掉"随手问一句"这类不需要
    # 跨 session 追踪的短交互，避免提醒噪音
    reminder_min_duration_minutes: float = 15.0
    # 触发提醒的最小真实用户轮次数（同上，双重阈值任一满足即可触发，
    # 因为有的长任务时间不长但轮次很多，有的相反）
    reminder_min_turns: int = 6


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
    # [next_doc/memory_backfill_and_profile_update_plan.md 第 4 节风险项 4]
    # 默认值从 False 改为 True：与 GrowthAdvisorConfig.enabled 的"零成本
    # 用起来"策略保持一致，从 opt-in 变成 opt-out。`_maybe_refresh_profile()`
    # 本身已有"无记忆来源/记忆为空则安全跳过"的兜底（agent/profile.py），
    # 默认开启不会对没有配置记忆功能的用户产生副作用。
    enabled: bool = True
    # 全局记忆每新增 N 条，触发一次 profile 刷新（后台线程，不阻塞主流程）
    refresh_interval_entries: int = 3
    # 全局记忆条目数达到该值之前，不生成 profile（信息太少意义不大）
    min_entries: int = 1
    # 取最近多少条全局记忆用于生成/刷新 profile（首次全量生成时的上限；
    # 增量更新时表示"自上次生成以来新增条目数"的兜底上限，见 profile.py）
    max_entries_for_profile: int = 20
    # tech_stack/habits 里某一项超过多少天没有被新证据再次印证，就在
    # 增量更新的 prompt 里被标注为"建议重新评估"（不是硬删，去留仍由
    # LLM 判断，见 profile.py::_stale_cutoff）
    stale_after_days: int = 90


@dataclass
class MemoryBackfillConfig:
    """[next_doc/memory_backfill_and_profile_update_plan.md] 记忆回填配置。

    修复"session 有内容但从没生成过摘要/记忆"的系统性遗漏（异常中断、
    LLM 调用失败未重试、cron/daemon 自动运行的 session）。只处理
    `Session.summary == ""` 且轮次达标的存量 session，不做时间窗口
    限制（方案评审已确认：陈年 session 也应该被回填，靠
    `max_sessions_per_run` 限流控制单轮开销，见方案第 4 节风险项 1）。
    """
    enabled: bool = True
    # 对齐 SessionConfig.summary_min_turns：轮次达到这个值才需要补摘要，
    # 太短的 session 内容太少，回填意义不大
    min_turns_for_backfill: int = 4
    # 每次扫描最多处理多少个候选 session，避免存量很多时一次触发跑很久
    max_sessions_per_run: int = 20
    # cron/daemon 自动执行的任务，是否也在收尾时直接产出一条记忆
    # （不经过 Session/summary 中转，见 evolution/memory_backfill.py
    # 的 backfill_cron_run()）。独立开关：出问题时可以只关这一半，
    # 不影响存量 session 的回填
    cron_run_backfill_enabled: bool = True


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
    # [http_server_blocking_call_guard_plan] 路由里同步调用 LLM 等可能长时间阻塞的
    # 操作时，统一经过 `mini_agent.utils.blocking_guard.run_blocking()`，避免把事件
    # 循环整个卡住。以下三项是它的默认参数，行为默认开启，不需要用户手动配置：
    blocking_call_timeout_seconds: float = 45.0       # 单次调用硬超时
    blocking_call_failure_threshold: int = 3          # 连续失败几次后打开熔断
    blocking_call_cooldown_seconds: float = 120.0     # 熔断打开后冷却多久再放行探测

    # [http_access_log_plan] HTTP 请求访问日志——每条请求的 method/path/状态码/
    # 耗时，写入独立的 JSONL 文件，用于事后排查"http server 长时间卡住"这类
    # 问题（比如定位到具体是哪个路由的耗时异常拖住了整个事件循环）。
    # 默认开启，开销是每请求一次 JSON 序列化+文件 append，可忽略不计；
    # 不想要这份日志可以显式关掉。
    access_log_enabled: bool = True
    # 留空 = 默认落在与 error.jsonl 相同的目录（~/.agent/logs/http_access.jsonl，
    # 或 MINI_AGENT_HOME 指向的目录），与全局错误日志保持一致的存放位置；
    # 也可以显式指定一个绝对路径，把它单独存到别处。
    access_log_path: str = ""


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
class TechRadarConfig:
    """[外部知识 wiki 计划 P3] `sys:tech_radar_search` 主动检索配置。

    见 next_doc/external_knowledge_wiki_and_self_improvement_plan.md §3 P3：
    种子来源优先复用 `wiki/gap_scanner.py` 的知识缺口扫描结果，`keywords`
    是缺口扫描暂未覆盖领域的手工兜底关键词列表（初期先简单实现，不追求
    自动发现）。`daily_seed_limit` 控制每次运行处理的种子数量上限，避免
    cron 触发即引发大量 LLM/网络调用；`max_search_results` 透传给
    `tools/builtin.py::web_search()` 的 `max_results`。
    """
    enabled: bool = True
    keywords: list = field(default_factory=list)   # list[str]，手工配置的关注关键词
    daily_seed_limit: int = 5
    max_search_results: int = 5

    # ── 检索结果质量反馈闭环（next_doc/growth_advisor_cron_search_and_
    # status_history_plan.md 方向二）────────────────────────────────────
    # 默认开启，零额外调用成本——只是在种子选择前多做一次基于既有
    # 轮转状态文件的过滤，不引入新的 LLM/网络调用。某个种子连续
    # `low_quality_streak_threshold` 次检索都没抽出任何 entity/fact，
    # 在 `low_quality_cooldown_days` 天冷却期内暂时跳过它，冷却期满后
    # 自动重新参与轮转（不是永久拉黑）。
    quality_feedback_enabled: bool = True
    low_quality_streak_threshold: int = 3
    low_quality_cooldown_days: int = 14


@dataclass
class EcosystemPositioningConfig:
    """[外部知识反馈闭环计划 P4] `sys:ecosystem_positioning_scan` 生态定位
    扫描配置。

    见 next_doc/external_knowledge_feedback_loop_improvement_plan.md §3 P4：
    复用 `tech_radar_search.py` 的"检索 → LLM 抽取 → 落盘 wiki"管道，但种子
    来源换成"同类 agent 框架/相关开源项目"，定位为"看别人在解决什么我还没
    意识到是问题的问题"。`seeds` 需要人工维护（暂不追求自动发现同类项目
    列表，与 `TechRadarConfig.keywords` 同样的"初期先简单实现"取舍）——
    默认空列表时本 job 直接跳过、不产生任何调用，需要用户在
    `agent_config.json` 里显式配置感兴趣的同类项目/框架名称后才会真正工作，
    因此默认以 `enabled=False` 状态注册（见 `ensure_ecosystem_positioning_scan_job`）。
    """
    enabled: bool = False
    seeds: list = field(default_factory=list)   # list[str]，同类项目/框架名称
    weekly_seed_limit: int = 5
    max_search_results: int = 5


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
    # [SYS-GOAL-MODE-DUAL-PATH] 验收标准生成走"纯 LLM"还是"受限只读 Agent"：
    #   "llm"   — 始终是单轮裸 chat completion（不挂工具），成本最低，适合目标
    #             本身足够自解释、不依赖项目内部结构（skill/workflow 定义等）的场景。
    #   "agent" — 始终构造一个只读、有限工具（skill_list / show_workflow /
    #             list_workflows / read_file / list_dir / tree_summary / grep /
    #             glob）的受限 Agent 来生成，可以先查证项目里的实际 skill/
    #             workflow 定义再产出验收标准，避免凭空编造路径或命令。
    #   "auto"  — 默认值。先用规则（关键词命中 + 已知 skill/workflow 名称匹配）
    #             判断目标是否涉及项目内部信息；命中则走 "agent"，否则走 "llm"。
    #             规则未命中时，仍允许 builder 在裸 LLM 输出的 JSON 里通过
    #             `needs_project_context` 字段自报"我需要读项目才能写好标准"，
    #             命中后当前这次调用的结果会被丢弃，改用 "agent" 路径重新生成。
    spec_builder_mode: str = "auto"                    # llm | agent | auto
    # 仅 "agent"/"auto" 命中 agent 路径时生效：只读工具白名单（工具名）。
    # 刻意不包含 bash / 任何写文件或写 workflow 的工具——builder 只需要"看"，
    # 不需要"改"，避免重蹈早期"受限 Agent 工具幻觉/滥用"的覆辙。
    spec_builder_agent_allowed_tools: list = field(
        default_factory=lambda: [
            "skill_list", "list_workflows", "show_workflow",
            "read_file", "list_dir", "tree_summary", "grep", "glob",
        ]
    )
    spec_builder_agent_allowed_tool_groups: list = field(default_factory=list)
    # agent 路径允许的最大轮次（先探索 skill/workflow 再产出 JSON，比纯 LLM
    # 单轮多几轮预算，但仍远小于主 Agent，避免跑飞）。
    spec_builder_agent_max_turns: int = 6


    # ── GoalJudge ────────────────────────────────────────────────────────────
    judge_model: Optional[str] = None
    judge_provider: Optional[str] = None
    # 判定 Agent 是否挂载工具（能自己跑命令验证验收标准），默认关闭（最小权限原则）
    judge_tools_enabled: bool = False
    # [系统关联性断点改进方案 F1] True 时 GoalJudgeAgent 核查前会先检索
    # wiki 里相关的历史决策页拼进 prompt（需要调用方传入 paths 才生效，
    # 见 role_agents/goal_judge.py::run_goal_judge）。默认 False，
    # 行为与改动前完全一致。
    decision_consumption_enabled: bool = False
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
    # [BUGFIX/需求变更] GoalJudge 单次核查内部允许跑的最大轮次（LLM 调用+工具
    # 调用循环的上限，与外层 max_rounds 是两回事）。此前硬编码在 judge_factory
    # 里：不挂工具时 2，挂工具时 6——挂工具场景 6 轮往往不够 GoalJudge 把验证
    # 命令（比如 pytest）跑完并收敛到最终 JSON 判定，会撞顶导致空输出、被迫
    # 保守判 CONTINUE。现在统一改成读这个配置项，不再区分是否挂工具，默认
    # 提高到 40。
    judge_max_turns: int = 40

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

    # ── [goal_mode_stuck_compact_plan.md §1.1] 分级 compact：把"卡住恢复"的
    # 强度和"已经恢复过几次"挂钩，而不是每次都用同一强度的 _do_compact()。
    # 前 light_compact_max_recoveries 次恢复只注入提示、不做 compact（第一次
    # 卡住可能只是暂时性的，没必要立刻压历史）；超过这个次数后才真正触发
    # compact（更激进地清理历史，倒逼换角度重新审视）。设为 0 等价于升级前
    # 行为（每次恢复都 compact）。
    light_compact_max_recoveries: int = 1

    # ── 调试 ─────────────────────────────────────────────────────────────────
    judge_show_prompt: bool = False   # 打印发给 GoalJudge 的完整输入 prompt（排查判定依据用）
    judge_show_raw_output: bool = False  # 打印 GoalJudge 返回的原始 JSON 判定结果（排查解析/判定依据用）

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

    # ── [goal_mode_stuck_compact_plan.md §1.2] Dead-end 持久清单：不同于
    # 按窗口滚动、会被冲掉的 recent_progress_reasons，dead_ends 只增不删
    # （去重后），确保"已经验证无效的方向"无论隔了多少轮都不会失效。
    # 默认开启；关闭后退化为纯窗口行为（回到升级前 §1.1 的实现）。
    dead_ends_persist_enabled: bool = True

    # ── [goal_mode_stuck_compact_plan.md §2.2] 自验证优先：如果 GoalSpec
    # 设置了 verification_command，GoalRunner 在拿到主 Agent 本轮输出、
    # 送进 GoalJudge 评审之前，会程序化地（不经过任何 LLM）执行一次该命令，
    # 把 {returncode, stdout_tail, stderr_tail} 作为客观证据一并传给判官。
    # 默认开启；关闭后退化为升级前的行为（判官只能看到 verification_command
    # 的文本描述，不会有代码路径主动执行它）。
    auto_verify_enabled: bool = True
    auto_verify_timeout: int = 120        # 自动执行验证命令的超时时间（秒）
    auto_verify_output_tail_lines: int = 40  # stdout/stderr 各自保留的尾部行数，避免污染 judge 上下文

    # ── [goal_mode_stuck_compact_plan.md §3.1] 进展分数：结合 checklist 客观
    # 通过数增量 + GoalJudge 主观 progress 判断，产出一个连续的进展分数
    # （而不是只有三态分类），供未来的伪进展识别（§3.2）等场景使用。
    # 仅在 criteria_tracking_enabled=True 时才有客观信号可用；关闭本开关时
    # progress_info 里不会计算/携带 progress_score 字段，不影响现有判断逻辑。
    progress_score_enabled: bool = True

    # ── [goal_mode_stuck_compact_plan.md §2.1] 过程判断 / 结果判断分离：
    # GoalJudge 除了对每条验收标准给出结果判断（passed/evidence），额外独立
    # 输出 process_flags 字段，标记"达成方式本身有问题"的投机行为（测试被
    # 弱化、检查被绕过、结果被伪造等）。process_flags 非空时，即使 checklist
    # 全部通过，GoalRunner 也不会放行 DONE，会强制降级为 CONTINUE 并在反馈里
    # 说明原因。与 progress_judge_mode / criteria_tracking_enabled 是独立开关，
    # 可任意组合；关闭后退化为升级前行为（完全不请求也不解析 process_flags，
    # DONE 判定只取决于 checklist）。
    process_integrity_check_enabled: bool = True

    # ── [goal_mode_stuck_compact_plan.md §3.2] 伪进展趋势识别：StuckDetector
    # 只能识别"完全没有变化/退步"，识别不了"每轮都有一点点进展，但累积起来
    # 毫无实质意义"这种模式。ProgressTracker 在 StuckDetector 之外新增一层，
    # 跟踪最近 pseudo_progress_window 轮的进展分数（§3.1），用早期/后期均值
    # 差值识别"平缓但非零"的趋势；一旦识别到，与 StuckDetector 判定卡住一样
    # 触发恢复流程（compact + 换角度提示），共享同一份 max_stuck_recoveries
    # 额度，不会因为触发路径不同获得额外的恢复次数。
    # 依赖 progress_score_enabled=True（没有分数就无法判断趋势）；关闭本开关
    # 时完全不做这层判断，行为与升级前一致，只依赖 StuckDetector 的"连续雷同"
    # 判定。
    pseudo_progress_detection_enabled: bool = True
    pseudo_progress_window: int = 5
    pseudo_progress_stagnation_threshold: float = 0.15
    pseudo_progress_max_score_cap: float = 0.5

    # ── [goal_mode_stuck_compact_plan.md §4] Compact 时机的"探索 vs 收敛"双模式：
    # 目前所有 compact 触发点都是被动响应（撞硬顶/判官建议/StuckDetector 判定卡住），
    # 没有任何机制根据"当前处于探索阶段还是收敛阶段"主动调整 compact 节奏。开启后，
    # 尚未出现稳定正向进展的"探索阶段"会按固定轮次节奏主动做一次轻量 compact（只
    # 重新钉住目标上下文，不做真正的历史压缩），给 agent 一次跳出当前上下文重新
    # 审视的机会；一旦连续多轮出现正向进展、判定进入"收敛阶段"，则暂停这层主动
    # 触发，只保留三个被动安全阀，避免把正在起效的上下文过早打断。
    # 默认关闭——这是一个会改变现有 compact 节奏的新机制，需要用户显式开启。
    proactive_compact_enabled: bool = False
    exploring_compact_interval: int = 2     # 探索阶段每 N 轮主动触发一次轻量 compact
    phase_convergence_window: int = 3       # 连续 N 轮正向进展分数才切换到 converging

    # ── [goal_mode_stuck_compact_plan.md §5] Goal 重规划提议：多次卡住恢复即将
    # 耗尽额度的最后一次机会时，额外要求主 Agent 在本轮输出中给出一份"重规划
    # 提议"（建议如何拆分目标为更小的子目标，或建议修改/放宽哪条验收标准及
    # 理由），用一个专门的 ```replan_proposal 代码块标记，供 GoalRunner 解析。
    # 三档取值，控制这份提议解析出来之后的实际处理方式：
    #   "off"     —— 默认。完全不开启本功能：不会在提示里额外要求 agent 输出
    #                提议，也不会解析/展示任何 replan_proposal，行为与升级前
    #                完全一致。
    #   "confirm" —— 需要人工确认：解析到非空提议后不会自动修改 GoalSpec，
    #                只会在最终 stuck/max_rounds_exhausted 终止时把提议展示
    #                给用户，由用户决定是否执行 `/goal revise` 采纳（不采纳
    #                就当作没有发生过）。这是比 "auto" 更保守、建议优先使用
    #                的档位。
    #   "auto"    —— 自动应用：解析到非空提议后立即调用
    #                GoalSpecBuilder.revise() 生成新版本 GoalSpec 并自动
    #                confirmed=True，替换当前目标后继续执行（不终止本次
    #                run()），全程不需要人工介入。
    #
    # 关键约束（无论哪个档位都成立）：这份提议**只在即将耗尽恢复额度的最后
    # 一次机会触发一次**，不会让 agent 在每一轮都尝试"申请改标准"——"auto"
    # 档位额外保证每次 goal 执行最多只自动应用一次重规划（见 runner.py
    # `_replan_auto_applied`），避免变成一个更隐蔽的"说服系统放宽标准"的漏洞。
    # `self._spec` 本身除 "auto" 档位这一条路径外，没有任何代码路径允许被
    # agent 的输出直接改写。
    replan_proposal_mode: str = "off"


@dataclass
class GoalExecutionSpecConfig:
    """[next_doc/goal_execution_spec_generation_plan.md] Goal 执行规范
    （GoalExecutionSpec）自动生成 + 用户确认机制的配置块。

    独立配置块，不塞进 GoalModeConfig——本机制虽然架构上镜像
    GoalSpecBuilder，但作用对象是 GoalBacklog 的跨会话 Goal（而不是 goal_mode
    单次会话内的一次性目标），语义上更接近 GoalBacklog/goal_cron_bridge 一侧。
    """
    enabled: bool = True

    # builder_mode：与 GoalModeConfig.spec_builder_mode 同名同义（llm/agent/auto）。
    #   "llm"   — 始终是单轮裸 chat completion（不挂工具）。
    #   "agent" — 始终构造一个只读、有限工具的受限 Agent 来生成/修订，可以
    #             先查看项目实际结构/已有 skill、workflow 定义/历史产出目录
    #             再写规范，避免凭空编造不存在的路径或文件名约定。
    #   "auto"  — 默认值。用关键词规则粗略判断 title/description（或
    #             revise 场景的用户反馈）是否提到"参考项目现有内容"类诉求，
    #             命中则走 "agent"，否则走 "llm"。与 GoalModeConfig.
    #             spec_builder_mode="auto" 的区别：这里没有做"LLM 自报
    #             needs_project_context 后二次重生成"那一层兜底，规则漏判
    #             时不会自动补救——第一版先用最朴素的规则覆盖最常见的场景，
    #             漏判可以显式传 mode="agent" 绕过。
    builder_mode: str = "auto"
    builder_model: Optional[str] = None
    builder_provider: Optional[str] = None
    # 仅 "agent"/"auto" 命中 agent 路径时生效：只读工具白名单，与
    # GoalModeConfig.spec_builder_agent_allowed_tools 同一批工具——builder
    # 只需要"看"项目里实际有什么（skill/workflow 定义、源码、历史输出
    # 目录），不需要"改"，不包含 bash/任何写文件的工具。
    builder_agent_allowed_tools: list = field(
        default_factory=lambda: [
            "skill_list", "list_workflows", "show_workflow",
            "read_file", "list_dir", "tree_summary", "grep", "glob",
        ]
    )
    builder_agent_allowed_tool_groups: list = field(default_factory=list)
    builder_agent_max_turns: int = 6

    # 看板"设为周期性"表单是否默认展示"生成规范"步骤（而不是默认走"跳过"）。
    prompt_on_recur: bool = True

    # §5.1 轻量核对（纯文件名/key 字符串匹配）总开关；关闭后仍然把规范拼进
    # prompt，但不做产出匹配、不会触发"建议复查规范"提示。
    soft_check_enabled: bool = True
    # 连续多少轮 file_check/handoff_fields 匹配不上才在 GoalNode 上追加一条
    # "建议复查执行规范"的系统备注。
    soft_check_alert_after_cycles: int = 3

    # evaluate_overall_completion()（"整个一次性 Goal 是否可以整体关闭"
    # 判定）默认只依赖 manifest 摘要文本做裸 LLM 单轮判断；打开这个开关后，
    # 改为构造一个只读、有限工具的受限 Agent（复用 GoalExecutionSpecBuilder
    # 已有的 _run_builder_agent 基础设施同一套 judge_factory 调用方式），
    # 允许判官实际打开该 Goal 产出目录下的文件核查内容（比如确认"报告里
    # 是否真的包含对比表格"，而不是只看文件名/摘要），判定更可靠但成本更高
    # （多一轮甚至多轮工具调用）。默认关闭——"整体关闭判定"本身就是可选
    # 增强（只有一次性、拆了多个子 Objective 且填了 overall_completion_
    # criteria 的 Goal 才会触发），这里进一步细分为"要不要为了准确性多付出
    # 一次 Agent 调用的成本"，交给用户按需开启。
    overall_completion_use_agent: bool = False
    # 仅 overall_completion_use_agent=True 时生效：只读工具白名单，不含
    # skill_list/list_workflows——判官核查的是"这个 Goal 自己的产出目录"，
    # 不需要了解项目里其它 skill/workflow 的定义。
    overall_completion_agent_allowed_tools: list = field(
        default_factory=lambda: ["read_file", "list_dir", "tree_summary", "grep", "glob"]
    )
    overall_completion_agent_allowed_tool_groups: list = field(default_factory=list)
    overall_completion_agent_max_turns: int = 8


@dataclass
class CycleTuningConfig:
    """[next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md
    Stage 3] 跨轮次诊断报告的自然语言摘要层 + 调优草案的自然语言解析层，
    两者都是**可选增强**、默认关闭：规则层聚合出的诊断报告/草案本身已经
    是完整可用的结构化数据，这两个开关只是"锦上添花"，不打开也不影响
    Stage 1/2 的任何功能（与 `digest_advisor.next_action_rank_with_llm`
    同一立场，见方案 §2.3/§3.1 末尾/§5 第 3 条）。

    两层都要求"失败静默回退"：LLM 调用失败/返回无法解析时，摘要层回退
    到"不生成摘要，只展示结构化字段"；解析层回退到"无法自动生成草案，
    提示用户改用具体的 `param=value` 命令"，不会因为 LLM 不可用/输出
    异常而报错中断。
    """

    # 诊断报告是否附带一段 LLM 生成的自然语言总结（§2.3）。输入只有已经
    # 聚合好的结构化报告字段，不额外读取原始产出内容，控制 token 成本。
    diagnostics_llm_summary_enabled: bool = False

    # 调优草案是否允许把用户的自然语言改进意见解析成白名单参数改动
    # （§3.2 第 1 步）。规则层能直接解析的情况（比如 CLI 直接传
    # `param=value`）不受此开关影响，只有"规则解析不出结构化改动"时才
    # 会尝试这一层。
    tuning_llm_parse_enabled: bool = False

    # 两层复用同一个 LLMHelper（跟随 `/model` 实时切换），不单独配置
    # provider/model——这是"辅助展示/辅助起草"场景，没有必要引入第二套
    # 模型选择维度，与当前活跃对话用的模型保持一致即可。


@dataclass
class ExecutionPhaseConfig:
    """[next_doc/goal_stuck_stats_and_llm_progress_judge_plan.md §2] Goal
    执行阶段（explore/converge/stable/tidy）跨轮"进展趋势"信号的配置块。

    `perception/execution_phase.py::compute_progress_trend_signal()` 默认用
    纯文本相似度（difflib）判断最近几轮 `progress_notes` 是否雷同；打开
    `progress_trend_llm_enabled` 后，改用 LLM 判断是否真的在原地打转，
    difflib 结果降级为 LLM 不可用/解析失败时的兜底，不会因为 LLM 异常
    影响 Goal 触发主流程。默认关闭，不影响现有部署行为。
    """
    progress_trend_llm_enabled: bool = False


@dataclass
class CyclePatrolConfig:
    """[next_doc/goal_cron_cycle_proactive_patrol_and_health_overview_plan.md
    §2.7] 能力 C：周期性 Goal/Cron 任务的主动巡检 + 推送。总开关默认关闭，
    对现有部署零影响；打开后按 `interval_hours` 节流，只巡检
    `recurring=True` 的 Goal，规则先筛出候选、命中才可能调用 LLM 生成
    摘要/合并降噪（`llm_enabled` 控制的是"命中后要不要用 LLM 呈现"，
    不是"巡检本身是否依赖 LLM"，规则筛不出候选时永远不产生 LLM 调用）。
    """

    enabled: bool = False              # 总开关，默认关闭
    interval_hours: float = 6.0        # 巡检间隔（不是 tick 间隔）
    llm_enabled: bool = True           # 命中候选时是否用 LLM 生成摘要/合并降噪
    max_push_per_run: int = 3          # 超过则合并降噪为一条
    push_cooldown_hours: float = 24.0  # 同一 Goal 同一信号的推送冷却时间
    generate_tuning_drafts: bool = True  # 命中规则建议时是否顺带生成调优草案（§2.4）
    dedupe_cron_skip_alert: bool = True  # [Stage 3/§6.2] cron_skip 信号只覆盖跨越
    # cron.skip_alert_threshold 之前的窗口，避免与 cron 层自己的阈值告警重复；
    # 设 False 退回 Stage 1/2 原始行为（阈值-1 及以上都算命中，不设上界）。
    review_trigger_enabled: bool = True  # [Track 3] 是否计算"两项搁置方向的
    # 复查触发信号"（阶段感知资源估算 / 跨 Goal explore 并发治理），见
    # goal_cron_convergence_and_governance_improvement_plan.md §3。只读统计，
    # 不影响巡检推送本身；设 False 完全跳过这部分计算。
    review_trigger_min_recurring_goals: int = 5  # 样本量门槛：recurring Goal
    # 总数低于这个值时不参与复查判断（避免小样本比例数字失真，见 §6 第 2 条）。
    review_trigger_explore_alert_ratio: float = 0.3  # 复查信号 1（阶段感知
    # 资源估算）阈值：explore 阶段且有健康告警的 Goal 占比。
    review_trigger_explore_concurrency_ratio: float = 0.5  # 复查信号 2（跨
    # Goal explore 并发治理）阈值：同时处于 explore 阶段的 Goal 占比。
    review_trigger_consecutive_rounds: int = 4  # 需要连续命中阈值的巡检轮数
    # 才认为"具备统计显著性"（默认 4 轮 × interval_hours 默认 6 小时 ≈ 1 天）。


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
    """[具身改进 B3][workflow机制改进计划.md] Workflow 执行/看护配置。

    `workflow/runner.py` 按 depends_on 拓扑分层（batch），同一层内互不依赖的
    步骤默认并发执行（每个步骤本来就用独立的 Agent 实例，互不共享可变状态，
    详见 runner.py 模块文档）。parallel_enabled/max_parallel 控制"是否启用
    并发"和"并发上限"，单步骤可通过 WorkflowStep.allow_parallel=False 单独
    强制串行。

    watchdog_* / approval_* / retry_on_error_* 为看护机制（P3/P4）相关配置，
    均为可选开关，默认值保证与改造前行为兼容（不强制开启审批门、不强制
    通用重试）。
    """
    parallel_enabled: bool = True
    max_parallel: int = 4   # 同一拓扑层最多同时执行的步骤数

    # ── 看护线程（P3）──────────────────────────────────────────────────────
    watchdog_enabled: bool = True                       # 是否启用看护线程（心跳超时检测+资源护栏）
    heartbeat_check_interval_seconds: float = 5.0        # 看护线程轮询间隔
    max_total_duration_seconds: Optional[float] = None   # 单次工作流执行的总时长护栏（None=不限制，可被 WorkflowDef.max_total_duration 覆盖）
    # [P7-②1 workflow_mechanism_improvement_plan.md] 单次工作流执行的累计
    # token（input+output）护栏，None=不限制，可被 WorkflowDef.max_total_tokens
    # 覆盖。只统计 agent/skill_agent 类型 step（见 runner.py 的回填点说明）。
    max_total_tokens: Optional[int] = None
    # [workflow_mechanism_improvement_plan_p14.md Phase 2] 跨 step 熔断：
    # 一定时间窗口内（本次运行全程，不做滑动窗口），若同一个 error_type
    # 累计在 N 个**不同** step 上各自失败过至少一次（不要求同一个 step
    # 连续失败——那是 escalate_after_n_same_failures 管的），视为"大概率
    # 是系统性问题（如某个外部依赖挂了），不是单个 step 的偶发故障"，主动
    # 请求 cancel，避免每个 step 各自把 retry_on_error 预算耗尽。
    # None（默认）=不启用，行为与改造前完全一致。
    circuit_breaker_distinct_step_threshold: Optional[int] = None

    # ── 人工审批门（P4）────────────────────────────────────────────────────
    approval_poll_interval_seconds: float = 3.0          # 审批门等待时的轮询间隔
    approval_wait_timeout_seconds: Optional[float] = 600.0  # 审批等待超时（None=无限等待），超时后自动判拒绝

    # ── 通用失败重试（P4，区别于 WorkflowStep.retry_on_gate_fail 质检门重试）──
    retry_on_error_backoff_seconds: float = 5.0          # 重试前的基础退避时长（乘以已重试次数）

    # ── 后台执行（P4）──────────────────────────────────────────────────────
    background_execution_default: bool = False           # run_workflow 未显式传 background 参数时的默认行为

    # ── Step 类型化 / 生命周期 Hook（P5）─────────────────────────────────────
    hooks_enabled: bool = True                # 是否触发 WorkflowStart/StepStart/StepEnd/GateFailed/WorkflowEnd 生命周期 Hook
    max_sub_workflow_depth: int = 3            # sub_workflow 类型 step 允许的最大嵌套深度（防止 A→B→A 循环递归）
    script_step_enabled: bool = False          # 是否允许 script 类型 step 执行 shell 命令（默认关闭，避免任意 YAML 变成命令执行入口）
    script_step_timeout_seconds: float = 60.0  # script 类型 step 的默认超时（被 step.timeout 覆盖）
    # [next_doc/workflow_python_step_and_zhihu_publish_plan.md §B4] python_step
    # 类型开关，语义与 script_step_enabled 一致：默认关闭，避免分享出去的
    # workflow YAML（可能来自他人/LLM 生成）变成任意 Python 代码执行入口。
    python_step_enabled: bool = False
    python_step_timeout_seconds: float = 120.0  # python_step 类型的默认超时（被 step.timeout 覆盖）
    tool_call_step_auto_approve: bool = False  # tool_call 类型 step 默认是否跳过人工审批门（默认 False：除非显式 require_approval=false，否则仍会走审批）
    human_input_wait_timeout_seconds: Optional[float] = 1800.0  # human_input 类型 step 等待人工输入的超时（None=无限等待），超时后该 step 标记为 FAILED

    # ── 保存前校验 / 模板库（P6）─────────────────────────────────────────────
    validate_placeholders_on_save: bool = True   # save_workflow 时是否校验 {step_id.output}/{step_id.score} 占位符引用完整性
    validate_role_refs_on_save: bool = True      # save_workflow 时是否校验 step.role 是否为已注册的角色 Agent profile

    # ── session → workflow 转换（session_to_workflow_design.md P8）───────────
    session_to_workflow_enabled: bool = True
    # 是否注册 list_recent_sessions / summarize_session_for_workflow /
    # build_workflow_from_summary 三个 Agent 工具，以及 CLI 的
    # /workflow sessions、/workflow from-session 子命令。默认开启；
    # 关闭后这两类入口都会提示"该功能已在配置中关闭"，不影响其余 workflow
    # 功能（generate_workflow/save_workflow/run_workflow 等不受影响）。

    # ── P9 候选池第一批（workflow_system_next_directions.md §6）──────────────
    condition_static_check_enabled: bool = True
    # [P9-3] save_workflow/WorkflowDef.validate() 时是否额外做一轮 condition
    # 表达式的静态一致性检查（引用的 step 是否存在、是否在 depends_on 声明的
    # 依赖范围内）。默认开启；关闭后仍会做原有的语法/占位符等其它校验，
    # 只跳过这一项，行为等价于 P9-3 之前的版本。
    dry_run_preview_on_generate: bool = True
    # [P9-1b] generate_workflow / build_workflow_from_summary 生成 YAML 后，
    # 是否自动追加一次 dry-run 预览（并发分批 + condition 求值）。默认开启；
    # 关闭后只展示步骤列表预览和 YAML 本身，不做 dry-run（可用于生成体积较大
    # 的工作流时减少一次额外计算）。
    git_hint_enabled: bool = True
    # [P9-2] save_workflow 保存成功后，若 project_root 是 git 仓库，是否追加
    # 一句"建议 git commit"的提示文案。默认开启，只是提示，从不自动 commit；
    # 关闭后 save_workflow 的返回文本里不再出现这段提示，
    # /workflow history、/workflow diff 命令本身不受此开关影响（仍可直接用）。

    # ── P11（workflow_input_passing_and_debug_logging_improvement_plan.md）──
    # 保存前校验：prompt 中 {step_id.output}/{step_id.score}/{step_id.output_file}
    # 占位符引用的 step_id 是否在 depends_on（直接或传递）范围内。默认开启；
    # 关闭后仍会做"引用的 step 是否存在"这一层原有检查，只跳过依赖范围检查，
    # 行为等价于 P11 之前的版本。
    placeholder_depends_on_check_enabled: bool = True
    # python_step 类型 step 传给子进程的 ctx.inputs 是否按 step.depends_on
    # 过滤（默认开启=只暴露声明过依赖的上游 step 结果）。关闭后回退到 P11
    # 之前"传全部已跑完 step"的行为，供升级过渡期临时兼容旧脚本使用。
    python_step_inputs_filtered_by_depends_on: bool = True
    # 是否在运行时记录调试日志（StepResult.debug_log：替换后的最终 prompt、
    # 未解析占位符、实际引用到的上游 step_id、并发线程/批次信息、
    # python_step/script 子进程的 stdout/stderr）。默认关闭，避免长期运行、
    # 高频调用的 workflow 把 session 目录撑爆；只有显式开启才产生额外体积。
    debug_log_enabled: bool = False
    # debug_log 里 resolved_prompt/subprocess_stdout/subprocess_stderr 等
    # 长文本字段的截断上限（字符数），超出部分截断并标注省略了多少字符。
    debug_log_max_chars: int = 4000


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
    # [Goal→Objective 自动拆解] maintenance 档位 tick 时，是否自动给还没有
    # Objective 子节点的 active Goal 生成 Objective（否则 Goal 会一直躺在
    # 看板里、has_actionable_work() 永远看不到它，agent 不会主动执行）。
    # 这不属于"派生新 Goal"（Goal 本身已经是用户/上游批准的意图），只是把
    # 它操作化为可执行单元，所以放在 maintenance 而不是 autonomous 档位。
    auto_objective_from_goal_enabled: bool = True
    # 单个 Goal 最多自动拆出几个 Objective（LLM 拆解失败/不可用时，
    # 降级为 1 个与 Goal 同名的 Objective，不受此上限影响）。
    auto_objective_max_per_goal: int = 3
    # [看板与自主性改进方案 Track K] 并发数自适应：默认开启，因为这是一个
    # 只降不升的机制——`max_concurrent_objectives_cap` 仍是配置得到的
    # 硬上限（等价于改造前写死的 MAX_CONCURRENT_OBJECTIVES=2），自适应
    # 逻辑只会在"最近失败率高"/"最近平均耗时长"时把实际生效的并发数往下
    # 调，不会让并发数超过这个上限，因此默认开启不会让行为变得比"改造前"
    # 更激进，只会更保守。
    adaptive_concurrency_enabled: bool = True
    # 并发数硬上限（安全阀）：无论自适应逻辑如何计算，生效并发数不会超过
    # 这个值，也不会超过模块级常量 MAX_CONCURRENT_OBJECTIVES（两者取更小
    # 的一个）。默认与改造前的硬编码值保持一致。
    max_concurrent_objectives_cap: int = 2
    # 生效并发数的下限（安全阀的另一端）：无论历史多差，自适应逻辑都不会
    # 把并发数降到 0（那等价于自主执行被整体停摆，属于 Track J 的资源
    # 门控范畴，不是本 Track 该做的事）。
    adaptive_concurrency_min: int = 1
    # 判定"最近失败率高"所需的最小样本数（最近已结束的 Objective 数量，
    # 不区分主题，全局统计）——样本太少不下结论，避免偶发的一两次失败
    # 就把并发数砍掉。
    adaptive_concurrency_min_samples: int = 3
    # 最近 N 个已结束 Objective 里，失败占比达到/超过该阈值时，并发数下调
    # 一档（一次只降 1，不会一步砍到底）。
    adaptive_concurrency_failure_rate_threshold: float = 0.5
    # 最近已完成（不含失败）Objective 的平均耗时（秒）达到/超过该阈值时，
    # 并发数额外下调一档——耗时长说明单个 Objective 本身消耗的资源/时间
    # 已经不小，并发跑多个的意义下降，且更容易互相挤占资源。两个信号
    # （失败率、平均耗时）分别判定、分别下调，可以叠加，但受
    # adaptive_concurrency_min 兜底。默认 1800 秒（30 分钟）。
    adaptive_concurrency_slow_duration_seconds: float = 1800.0
    # 参与统计的"最近 N 个已结束 Objective"窗口大小。
    adaptive_concurrency_window: int = 10
    # [看板与自主性改进方案 Track J] 资源门控降级执行：ResourceArbiter 的
    # frustration/user_presence 两条规则从二元 block/allow 改成三态
    # full/degraded/blocked。默认开启——degraded 态本身就是"比原来更宽松"
    # 的中间态（原来这两条规则一旦触发就整体停摆，现在改成先降级，只有
    # 更严重的情况才会真正 blocked），默认开启不会让行为比改造前更激进，
    # 只会在原本"整体停摆"的场景里让 agent 至少还能低并发地继续跑。
    resource_gating_degraded_enabled: bool = True
    # degraded 态下，effective_max_concurrent() 的临时天花板（与 Track K
    # 的 adaptive_concurrency_min 复用同一套"降档不降到 0"哲学，但这里是
    # 资源门控触发的降级，语义上比自适应并发的"最近跑得不顺"更紧急，所以
    # 单独给一个字段，不与 adaptive_concurrency_min 强绑定）。
    resource_gating_degraded_max_concurrent: int = 1
    # frustration 达到该阈值时才真正整体 blocked（复用
    # proprioception.frustration_threshold 作为 full→degraded 的分界线，
    # 这里额外定义一个更高的 blocked 分界线，只有挫败感严重到这个程度才会
    # 真正整体停摆）。
    frustration_blocked_threshold: float = 0.85
    # [goal_execution_fairness_improvement_plan.md P1] 同一个 Goal 同时最多能
    # 占用几个并发执行槽位（按 execution.status == "running" 且所属 Goal
    # 相同统计）。默认 1：从根源上避免一个 Goal 自己吃满所有并发槽位，让
    # 其余 Goal 长期挤不进来。设为 0 或负数视为不限制（等价于关闭本项，
    # 完全回退到改造前的行为）。
    max_concurrent_objectives_per_goal: int = 1
    # [goal_execution_fairness_improvement_plan.md P2] Objective 挑选顺序的
    # 调度策略：
    #   - "fair_round_robin"（新默认）：按 Goal 分组，组间按
    #     last_scheduled_at 升序（最久未被调度的优先）+ priority（含 P3
    #     老化加成）降序作为次级键；
    #   - "priority"：改造前的纯静态 priority 降序排序（供灰度回退）。
    goal_scheduling_strategy: str = "fair_round_robin"
    # [goal_execution_fairness_improvement_plan.md P3] 停滞 Goal 的老化加成：
    # 复用 next_action_stale_days 判定"是否已停滞"，停滞天数每超过阈值 1
    # 天，调度侧使用的 effective_priority 就临时 +
    # fairness_aging_boost_per_day（不改写 node.priority 本身），加成累计
    # 上限为 fairness_aging_boost_max_days 天对应的量，避免无限增长。
    fairness_aging_boost_per_day: float = 1.0
    fairness_aging_boost_max_days: float = 14.0
    # [goal_execution_fairness_improvement_plan.md P4] 执行时间片化（抢占式
    # 让出槽位）。默认关闭——这是比 P1-P3 更激进的行为变化（会中途打断一个
    # 本来能连续跑完的 Objective），先默认关闭，观察 P1-P3 实际效果后按需
    # 灰度开启，不强制所有部署一起切换。
    fairness_time_slicing_enabled: bool = False
    # 单个执行片段（slice）里连续完成的 step 数达到该值后，才检查是否应该
    # 让出槽位（避免 step 数很少的 Objective 被无意义打断）。
    fairness_yield_after_steps: int = 3
    # 或者单个执行片段已运行的时长（秒）达到该值后，也检查是否应该让出
    # 槽位——两个条件任一满足即可触发检查（检查本身不代表一定让出，还要看
    # 是否确实有其它 Goal 在排队等待，见 objective_executor.py
    # `_should_yield_for_fairness()`）。
    fairness_yield_after_seconds: float = 900.0
    # [daemon_autonomous_state_recovery_plan.md 阶段三 / P1] 自主任务独立
    # 上下文：开启后，`autonomous` Objective 的每个 step 不再复用 Self 的
    # 主 Session/Agent（即不再走共享的 bridge.input_queue），而是像 cron
    # 任务（见 evolution/cron_agent_bridge.py）一样，每次提交都在专属的
    # 后台线程里构建一个全新的、独立 Session 的 Agent 实例来执行，执行完
    # 即丢弃——不会和真人交互共用同一段对话历史，也不会跨 step 累积上下文
    # （"上一步做到哪了"完全靠 ObjectiveExecutor 自己在 prompt 里拼接的
    # `[前序步骤结果]`/`[前序步骤产出文件]`结构化摘要传递，不依赖共享的
    # session 历史）。
    # [daemon_stability_and_ux_improvement_plan.md P0-6] 默认改为 True：
    # 不污染 Self 主 session 对无人值守场景是直接价值（避免自主任务的中间
    # 对话把交互式 REPL 历史撑大/搅乱），改默认值成本低；新初始化的项目
    # 默认开启，已有项目的 `agent_config.json` 若显式写过 `false`，尊重
    # 用户配置不覆盖（同 P0-3 的 loader 行为：只在字段显式出现在配置文件里
    # 时才覆盖 dataclass 默认值）。开启后 Self 在 REPL/看板中看不到自主
    # 任务执行过程中的中间对话（只能看到落盘的 `step.result_summary` 等
    # 结构化状态），这是预期行为，已在 `docs/daemon-execution-model-guide.md`
    # 中说明。
    # 实践提示：本项与下方 `objective_persistent_worker_enabled` 互斥、后者
    # 优先——自 P0-3 起 `objective_persistent_worker_enabled` 已默认
    # `True`，因此对新初始化的项目而言，路由早已优先走
    # `ObjectivePersistentRunner`（同样不复用 Self 主 session，只是额外保留
    # 了跨 step 的会话连续性），本项默认开启主要覆盖"用户显式关闭了持久
    # Worker、但仍希望自主任务不污染主 session"这一组合场景。
    objective_isolated_context_enabled: bool = True
    # 隔离上下文模式下，单次 step 的 run_turn() 内部预算（max_turns）。
    objective_isolated_inner_max_turns: int = 15
    # 隔离上下文模式下，同时允许多少个 step 在独立线程里并发执行 Agent。
    # 这是一个安全阀，不是主要的并发控制手段——真正决定"同时有几个
    # Objective 在跑"的仍然是 max_concurrent_objectives_cap 等既有机制，
    # 这里只是防止极端情况下（比如配置改动导致上限突然变大）无限制地
    # 并发构造 Agent 实例耗尽资源。
    objective_isolated_max_workers: int = 4
    # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md §5 后续
    # 补做 / 阶段四] ObjectiveIsolatedRunner 共享线程池没有"按 execution
    # 精细回收单个卡死 worker"的能力（不像 cron/持久 Worker），只能做
    # "整体健康检查 + 达到临界条件（卡死数 >= max_workers）才整体重建"。
    # 判定"某个 turn 已卡死"的有效阈值 = objective_step_stale_timeout_
    # seconds（或模块默认 DEFAULT_STEP_TIMEOUT_SECONDS）+ 这个宽限期，
    # 与阶段一 stale_job_watchdog_grace_seconds 同一思路：避免跟"最后一步
    # 恰好比较慢但最终会正常返回"的情况混淆。默认 5 分钟，与阶段一保持
    # 一致的默认值哲学。
    objective_isolated_pool_rebuild_grace_seconds: int = 5 * 60
    # [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
    # 阶段一] 目标级持久 Worker：开启后，`autonomous` Objective 的 step
    # 提交走 ObjectivePersistentRunner 而不是 ObjectiveIsolatedRunner——每个
    # execution_id 独占一条专属单线程，Agent 实例在该 execution 内跨 step
    # 复用（不像 objective_isolated_context_enabled 那样每步都重建/丢弃），
    # 因此既有真并行（不同 execution 之间互不阻塞），又保留了单个 Objective
    # 内部的会话/工具状态连续性。与 objective_isolated_context_enabled
    # 互斥，本项优先——两者都开启时以本项为准。
    # [daemon_stability_and_ux_improvement_plan.md P0-3] 默认改为 True：
    # 底层的共享调度锁并发竞态问题已修复（见
    # tests/test_objective_runner_sched_lock.py），单独开启已经过验证；
    # 新初始化的项目默认开启，已有项目的 `agent_config.json` 若显式写过
    # `false`，尊重用户配置不覆盖（config loader 只在字段显式出现在配置
    # 文件里时才覆盖 dataclass 默认值，未出现的字段走这里的新默认值，
    # 因此这个改动对未升级的现有项目是透明的）。
    objective_persistent_worker_enabled: bool = True
    # 持久 Worker 的 execution 专属线程/Agent 实例，超过该时长（秒）未收到
    # 新的 step 提交时，视为孤儿（正常情况下应由 Objective 终止时的
    # release_worker_fn 回调及时释放，这里只是兜底，防止 daemon 异常重启等
    # 边界情况导致的线程/Agent 泄漏）。默认 1800 秒（30 分钟）。
    objective_persistent_worker_idle_ttl_seconds: float = 1800.0
    # [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
    # §7.3 修复] 持久 Worker 的 Agent 实例跨 step 复用会话历史，理论上可能
    # 一直累积到撑爆 context window——交互式用户会自己感觉到并手动
    # /compact，但持久 Worker 无人值守，没有人会注意到。这是一个兜底
    # （floor）：只在项目全局没有开启 cfg.compress.enabled 时才介入，强制
    # 给持久 Worker 的 Agent 打开 token 阈值 compact 触发器（复用现有的
    # compact_with_skills 实现，不是重新发明一套简易摘要）；项目已经全局
    # 配置过压缩（无论开或关）时尊重用户配置，不覆盖。默认 True——这是
    # 持久 Worker 专属的安全网，只在持久 Worker 本身被打开时才有意义，
    # 所以默认跟随其一起生效。
    objective_persistent_worker_auto_compact_enabled: bool = True
    # 上面这个兜底生效时使用的 token 占用率阈值（对应 cfg.compress.threshold）。
    # 默认 0.75，略保守于项目全局 compress.threshold 的默认值 0.7——持久
    # Worker 场景下没有人工介入，稍早一点触发更安全。
    objective_persistent_worker_auto_compact_threshold: float = 0.75
    # [daemon_stability_and_ux_improvement_plan.md 第 2 项 / P3-2] 持久
    # Worker 跨重启连续性：开启后，每个 step 完成时把当前会话状态压缩为
    # 一份摘要（复用 compact_with_skills，不是另开一套摘要机制）落盘，
    # daemon 因异常重启等原因需要重建某个 execution 的持久 Worker 时，
    # 读取这份摘要注入新 Agent 的 system_extra，让它至少"记得自己重启前
    # 做过什么"，不是完全失忆重新开始（等同于降级成隔离 runner 模式）。
    # 默认 False——只在 `objective_persistent_worker_enabled` 也开启时才
    # 有意义；每个 step 完成后多一次 compact_with_skills 调用（有 LLM
    # 开销），先默认关闭，观察实际收益与开销后再考虑调整默认值。
    objective_persistent_worker_restart_summary_enabled: bool = False
    # 上面这份摘要落盘前的最大字符数，超出截断——避免摘要本身随时间无限
    # 增长、拖慢重启后第一次 step 提交（重建 Agent 时这段文本会整段塞进
    # system_extra，摘要越长首次请求的 prompt 越大）。
    objective_persistent_worker_restart_summary_max_chars: int = 4000
    # [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
    # 阶段二] 调度心跳独立化：开启后，AutonomousLoop.tick() 不再依赖
    # AgentRunner 主循环"dequeue 超时后顺带检查"触发，而是由独立的
    # SchedulerHeartbeat 后台线程按自己的轮询间隔主动触发，不受当前主循环
    # 是否正忙于处理一个长 turn 影响。
    # [daemon_stability_and_ux_improvement_plan.md P0-3] 默认改为 True，
    # 理由与回退方式同上方 `objective_persistent_worker_enabled` 的注释——
    # 两个开关的默认值调整是同一个 Track 一起做的决定。
    scheduler_heartbeat_enabled: bool = True
    # SchedulerHeartbeat 自身的轮询间隔（秒）——只是"多久检查一次是否该
    # tick"，真正的 tick 频率仍由 AutonomousLoop 的 tick_interval_seconds
    # 决定，本项应明显小于 tick_interval_seconds 才有意义（默认 5 秒 vs
    # 默认 tick_interval 60 秒）。
    scheduler_heartbeat_poll_interval_seconds: float = 5.0
    # [daemon_autonomous_state_recovery_plan.md 阶段四 / P2] 看护模式
    # GuardianRunner：不依赖 GoalSpec/GoalJudge 的轻量监督层，跟踪
    # `autonomous` Objective 每个 execution 最近几步的结果摘要，识别"原地
    # 打转"（复用 StuckDetector + ProgressTracker），在多次恢复无效后触发
    # 既有的"重新分解 / 判失败"路径（`ObjectiveExecutor._attempt_redecompose`），
    # 不做 DONE/CONTINUE 语义裁定。这是纯增量的观察层，关闭时
    # ObjectiveExecutor 的行为与升级前完全一致。
    # [daemon_stability_and_ux_improvement_plan.md P0-6] 默认改为 True：
    # "卡住检测"对无人值守场景直接有价值（不依赖用户读文档才知道要手动
    # 开启），灰度观察已充分（该层本身只做观察+触发既有的重新分解/判失败
    # 路径，不引入新的执行分支）；新初始化的项目默认开启，已有项目的
    # `agent_config.json` 若显式写过 `false`，尊重用户配置不覆盖。开启后
    # Guardian 判定 `failed` 的 Objective，`progress_notes` 会带
    # `"guardian:"` 前缀，便于看板/日志区分于其它失败原因，已在
    # `docs/daemon-execution-model-guide.md` 中说明。
    guardian_mode_enabled: bool = True
    # [daemon_stability_and_ux_improvement_plan.md 第 1 项 / P2-1] 跨
    # Objective 广度熔断阈值：同一粗分类 error_type（见
    # evolution/circuit_breaker_core.py::classify_error_type）累计在多少
    # 个**不同** execution 上出现失败即判定为系统性问题（例如某个工具/
    # API 全局失效），不必等每个 Objective 各自试到重试耗尽才发现。
    # None/0 表示不启用（默认不启用——这是一个新增的观测/告警信号，不
    # 影响任何现有执行/重试路径，触发后也只做记录+主动告警，不阻断新
    # Objective 的调度，所以默认关闭是出于"先观察阈值是否合理"的谨慎，
    # 不是因为有风险）。
    objective_circuit_breaker_distinct_threshold: Optional[int] = None
    # 单个 execution 允许跑的最大 step 数（不是"步骤计划里声明的 step
    # 数"，是"实际提交过多少次 step，含重试"），达到即视为"到点了"，
    # 触发与"卡住多次恢复无效"相同的收尾路径。防御极端情况：即使每一步都
    # 被判定为"有细微差别、不算卡住"，也不会无限跑下去。
    guardian_max_rounds: int = 20
    # 以下三项透传给内部的 StuckDetector，语义与 goal_mode 的同名字段一致：
    # 连续 `guardian_stuck_consecutive_limit` 步结果相似度达到
    # `guardian_stuck_similarity_threshold` 视为"卡住"，最多给
    # `guardian_max_recoveries` 次恢复机会，额度耗尽后判定 GIVE_UP。
    guardian_stuck_similarity_threshold: float = 0.92
    guardian_stuck_consecutive_limit: int = 3
    guardian_max_recoveries: int = 2
    # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段三]
    # ObjectiveExecutor.reap_stale_steps() 判定"当前 step 已卡死"的超时
    # 阈值（秒）。None（默认）时回退模块级常量
    # objective_executor.DEFAULT_STEP_TIMEOUT_SECONDS（=600），保持向后
    # 兼容。有些任务本来就该跑更久（比如涉及大型代码库分析的 step），
    # 有些则应该更敏感地判定卡死，按项目调整这个值。
    objective_step_stale_timeout_seconds: Optional[int] = None
    # [daemon_stability_and_ux_improvement_plan.md 第 7 项 / P3-7] 失败模式
    # 事中拦截：step 提交前查询 `failure_pattern_store.json`（由既有的
    # `sys:failure_pattern_aggregation` 周期任务聚合，本项只新增"查询并注入
    # 提示"这一步，不改变聚合逻辑本身），若该 step/Objective 标题命中高频
    # 已知失败模式，在提交给模型的消息里附带"过去在类似任务上曾因 X 原因
    # 失败，注意规避"的提示。默认 True——只读一次本地 JSON 文件（数据本身
    # 已经在磁盘上，不新增 LLM 调用/网络请求），成本可忽略；命中率取决于
    # `failure_pattern_store.json` 的既有数据积累量，数据不足时自然不命中，
    # 不会产生噪音提示。
    failure_pattern_interception_enabled: bool = True
    # 命中阈值：与 `get_patterns_for_category()` 默认值一致，只有历史
    # occurrence_count 达到该值的失败模式才会被视为"高频"并注入提示，避免
    # 偶发一两次失败就被当成"已知模式"打扰正常执行。
    failure_pattern_interception_min_occurrence: int = 3
    # 单次提示最多附带几条命中的失败模式，避免同一个 task_category 下
    # 多个 root_cause_tag 都命中时把提示堆得过长。
    failure_pattern_interception_max_patterns: int = 3


@dataclass
class SchedulerConfig:
    """[goal_cron_unified_scheduler_improvement_plan.md P5 第 3 步]
    `UnifiedTaskScheduler` 的裁决参数。本类只影响"degraded 状态下各通道
    分到多少并发槽位"这一件事，不涉及 P5 第 1-2 步的只读预览端点（那部分
    不需要配置，权重默认全 1.0 且只用于展示）。

    默认 `unified_arbitration_enabled=False`——改进计划 §设计边界第 4 条
    要求"默认行为变化需要可灰度控制"，本类新增的所有字段在开关关闭时
    不会被读取，`ObjectiveExecutor`/`CronJobRunner` 仍走各自改造前就有的
    独立裁决路径（各自读 `autonomy.resource_gating_degraded_max_concurrent`
    / `cron.degraded_max_concurrent`）。
    """

    # 是否启用统一调度层的仲裁裁决：True 时，degraded 状态下 goal 通道与
    # cron 通道的并发上限改由 `allocate_weighted_slots()` 按
    # `channel_weights`/`degraded_total_slots`/`cron.reserved_min_concurrent`
    # 统一计算；False（默认）时两条通道继续各自独立裁决，行为与本 Track
    # 之前完全一致。
    unified_arbitration_enabled: bool = False

    # [goal_execution_scheduling_global_cap_bugfix.md] Goal Objective 通道
    # （`ObjectiveExecutor.effective_max_concurrent()`，默认 cap=2）与普通
    # cron 通道（`CronJobRunner.effective_max_concurrent()`，默认
    # `cron.max_concurrent_jobs=2`）此前是两套完全独立的并发上限，只有在
    # ResourceArbiter 判定为 degraded、且 `unified_arbitration_enabled=True`
    # 时才会通过 `allocate_weighted_slots()` 统一裁决——也就是说在正常
    # （非 degraded）状态下，两条通道各自最多能跑到自己的 cap，加总起来
    # 系统里可能同时有 2（Objective）+ 2（cron）= 4 个任务在跑，完全不受
    # 任何"总并行数"控制，看板"正在执行 N 项任务"里 N > 单条通道 cap 的
    # 现象（比如同时 3 个）就是这么来的。
    #
    # 本字段提供一个跨通道的总并发天花板：为 None（默认）时完全不生效，
    # 两条通道继续各走各的独立 cap，与改造前行为一致；设为一个正整数后，
    # `ObjectiveExecutor`/`CronJobRunner` 的 `effective_max_concurrent()`
    # 会在各自原有 cap 的基础上，再clamp到
    # `max(0, max_total_concurrent_tasks - 对方通道当前运行数)`——始终
    # "只降不升"，不会让任何一条通道的上限变得比改造前更宽松。任意时刻
    # 全局同时运行的 Objective + cron job 总数不会超过这个值。
    max_total_concurrent_tasks: Optional[int] = None

    # degraded 状态下，goal 通道与 cron 通道总共可用的并发槽位数（两条
    # 通道共享，按权重 + cron 保底切分）。默认 2，等于改造前两条通道各自
    # 默认 degraded 上限（1 + 1）之和——保证开关刚打开、权重仍是默认 1:1
    # 时，两条通道各自分到的槽位数与改造前完全一致，是一个"接入但不改变
    # 现状"的安全默认值。
    degraded_total_slots: int = 2

    # 各通道的相对权重，用于 `allocate_weighted_slots()` 的比例分配。
    # `goal_cycle` 通道复用 goal 通道的执行池（见改进计划背景第 3 条：
    # goal_cycle 触发后转发进 `ObjectiveExecutor`，复用通道 1 的并发），
    # 所以这里只需要 `goal`/`cron` 两个权重参与实际裁决；`goal_cycle`
    # 权重目前仅供 P5 第 1-2 步的只读排序预览（`suggest_order()`）使用。
    # 默认全 1.0，不偏向任何通道——与 P5 第 1-2 步 `suggest_order()` 的
    # 默认权重保持一致的"不引入隐含偏向"原则。
    channel_weights: dict = field(default_factory=lambda: {
        "goal": 1.0,
        "cron": 1.0,
        "goal_cycle": 1.0,
    })

    # [P5 第 5 步 · 灰度接入实际派发] 是否让 AutonomousLoop._tick_passive()
    # 改用 unified_task_scheduler.dispatch_due_cron_jobs()（经
    # CronChannelAdapter/GoalCycleChannelAdapter.execute() 委托派发）
    # 触发普通 cron + goal_cycle job，而不是直接调用
    # CronScheduler.tick()。默认 False——两条路径最终触发的到期
    # 判断/记账逻辑完全一致（dispatch_due_cron_jobs() 复用的就是
    # tick() 内部同一份 _trigger_and_record()），开关只影响"谁来组织
    # 触发顺序"这一层，用于在正式切换默认值之前先小范围观察是否有
    # 意外行为（改进计划 P5 第 4 步待确认项 12："需要先设计一个灰度
    # 开关……不适合与证明 execute() 委托本身可靠这一步骤合并"，本项
    # 即是该灰度开关）。仅影响普通 cron/goal_cycle 通道——Goal 通道
    # 派发路径不受影响（ObjectiveChannelAdapter.execute() 仍未实现）。
    unified_dispatch_enabled: bool = False


@dataclass
class CronConfig:
    """[daemon cron 任务专属执行机制] 见 evolution/cron_job_{workspace,
    executor,runner,agent_bridge}.py。控制 cron job 后台线程执行的全局
    默认限制；单个 job 可以在自己的 `.agent/cron_jobs/<id>/config.json`
    里覆盖 timeout_seconds/max_steps（那是"每个 job 的"，这里是"全局的"，
    两者不冲突：job 级 config.json 缺省时才回退到这里的默认值）。
    """

    # 同时并发执行的 cron job 数量上限（CronJobRunner 内部信号量）。
    # 超出上限的 job 到期后会在后台线程里排队等待，不会丢失触发，只是
    # 延后开始——见 cron_job_runner.py::CronJobRunner.submit() 的说明。
    max_concurrent_jobs: int = 2

    # 单个 job 若自己的 config.json 里没写 timeout_seconds/max_steps，
    # 回退到这两个全局默认值（与 cron_job_workspace.py 里的
    # DEFAULT_TIMEOUT_SECONDS/DEFAULT_MAX_STEPS 保持一致，这里允许整机
    # 级别统一调整而不用逐个 job 改文件）。
    default_timeout_seconds: int = 20 * 60
    default_max_steps: int = 60

    # cron 任务专用 Agent 单次 run_turn() 内部的 max_turns 预算（对应
    # cron_agent_bridge.py::CRON_INNER_MAX_TURNS_DEFAULT），做"内层限步数、
    # 外层限墙钟时间"双重兜底的内层那一道。
    inner_max_turns: int = 15

    # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段一]
    # 外部存活性回收（CronJobRunner.reap_stale_jobs()）判定"job 已卡死"的
    # 宽限期：有效超时阈值 = job 自己的 timeout_seconds（或本类的
    # default_timeout_seconds）+ 这个宽限期。之所以要在 job 自己的超时
    # 之上再加一段宽限期，是因为 CronJobExecutor.run_job() 内部的墙钟检查
    # 只在两次 step 之间生效，允许最后一步本身多跑一段时间才被外部判定为
    # 真正卡死，避免跟"最后一步恰好比较慢但最终会正常返回"的情况混淆。
    stale_job_watchdog_grace_seconds: int = 5 * 60

    # [daemon_stability_and_ux_improvement_plan.md 第 1 项 / P2-1] 跨
    # cron job 广度熔断阈值：与 AutonomyConfig.
    # objective_circuit_breaker_distinct_threshold 语义一致，只是 scope
    # 换成了 job_id——同一粗分类 error_type 累计在多少个不同 job 上失败
    # 即判定为系统性问题。None/0 表示不启用（默认不启用，理由同上）。
    circuit_breaker_distinct_threshold: Optional[int] = None

    # [goal_cron_unified_scheduler_improvement_plan.md P0] ResourceArbiter
    # 判定为 degraded（而非 blocked）时，普通 cron 通道（CronJobRunner）
    # 临时收紧到的并发上限。与 AutonomyConfig.resource_gating_degraded_
    # max_concurrent（Objective 通道用的同名字段）故意分开配置——cron
    # 通道对"时间确定性"要求更高，收紧幅度允许单独调整，不与 Objective
    # 通道强绑定。默认 1：degraded 时仍能触发到期的 cron job，只是并发
    # 收紧到 1，不再像改造前那样把 degraded 当 blocked 处理、整体跳过。
    degraded_max_concurrent: int = 1

    # [goal_cron_unified_scheduler_improvement_plan.md P2] CronJob 连续
    # 跳过次数达到该阈值时，通过 NotificationDispatcher 发一次告警。
    # 默认 5：太低容易在偶发的短暂 blocked 期间就刷屏告警，太高则错过
    # 真正"长期没跑成功"的信号。
    skip_alert_threshold: int = 5

    # [goal_cron_unified_scheduler_improvement_plan.md P5 第 3 步] degraded
    # 状态下、且 `scheduler.unified_arbitration_enabled=True` 时，普通
    # cron 通道的保底并发槽位数——即使 goal 通道权重更高，cron 通道分配到
    # 的槽位也不会低于这个值。默认 1，与改造前 `degraded_max_concurrent`
    # 的默认值语义一致（degraded 时 cron 至少还能跑一个），只是决定权从
    # "cron 自己固定收紧到这个数"变成"UnifiedTaskScheduler 保证它至少
    # 拿到这么多"。仅在 `unified_arbitration_enabled=True` 时生效，默认
    # 关闭状态下完全不影响现有行为。
    reserved_min_concurrent: int = 1


@dataclass
class DigestAdvisorConfig:
    """[主动推荐与数字分身机制设计方案] 日报融合 / 主动推荐 / 决策画像
    三层功能的总开关与阈值配置。三者运行节奏依次变慢（日级 → 小时级候选
    扫描 → 周级归纳），对应 cron job：sys:daily_digest / sys:next_action_digest /
    sys:decision_profile_update，具体调度频率仍由 cron job 的 schedule 字段
    控制，这里只控制"要不要跑""跑出来的东西展示到什么程度"。
    """

    # ── 阶段一：每日融合报告 daily_digest ──────────────────────────────
    daily_digest_enabled: bool = True
    # 启动时是否打印一行未展示过的日报摘要（关闭后 cron job 仍会正常生成
    # 文件，只是不在 REPL 启动时打扰用户，用户仍可 /digest daily 主动查看）
    daily_digest_startup_print_enabled: bool = True

    # ── 阶段二：主动推荐排序层 next_action_advisor ─────────────────────
    next_action_enabled: bool = True
    next_action_startup_print_enabled: bool = True
    # 是否接入 LLM 排序层（默认 False，先跑规则层观察准确度，见方案 4.2 节
    # 和改进计划阶段二）
    next_action_rank_with_llm: bool = False
    # 停滞目标判定：与 evolution/next_action_advisor.py 的 STALE_DAYS /
    # STALE_PRIORITY_FLOOR 模块级常量对应，可在此覆盖而不用改代码
    next_action_stale_days: float = 7.0
    next_action_stale_priority_floor: int = 1
    # 注意力错配判定窗口/阈值，对应模块级 ATTENTION_WINDOW_HOURS /
    # ATTENTION_MISMATCH_RATIO
    next_action_attention_window_hours: float = 6.0
    next_action_attention_mismatch_ratio: float = 0.5
    # 是否用 decision_profile 已归纳出的高置信度模式对候选排序做加权
    # （方案 4.4 节"初期用法 2"），默认 False——依赖 decision_profile 本身
    # 已经积累出可信模式，画像质量未经人工确认前不默认影响推荐排序
    next_action_profile_weighting_enabled: bool = False
    # 参与加权的模式最低置信度门槛，低于此值的模式即使存在也不参与排序
    next_action_profile_weighting_min_confidence: float = 0.5
    # 注意力错配 daemon 主动推送：默认关闭，避免打断式骚扰（见方案 4.3 节
    # "daemon 主动推送"一行）。开启后，若同一错配信号连续被检测到超过
    # push_threshold_hours，daemon 才会通过多客户端推送通道主动提醒一次。
    next_action_push_enabled: bool = False
    next_action_push_threshold_hours: float = 2.0
    # 同一 daemon 会话内最多推送次数（避免重复错配信号反复推送）
    next_action_push_max_per_session: int = 1

    # ── 阶段三：决策画像 decision_profile_builder ──────────────────────
    # 归纳逻辑本身默认可用（/decision_profile update 手动触发不受此项影响），
    # 这里的 enabled 只控制 sys:decision_profile_update cron job 的默认状态；
    # 与方案文档"阶段三 cron job 默认 enabled: False"保持一致。
    decision_profile_enabled: bool = False
    decision_profile_min_evidence_count: int = 3


@dataclass
class GrowthAdvisorConfig:
    """[growth_advisor_design.md] 用户成长顾问配置。

    与 DigestAdvisorConfig 是姊妹配置块：那边服务 Agent 自己的推荐/画像，
    这里服务人类用户的成长方向候选/调研报告。见方案第 -1 节的战略定位——
    默认值按"零成本用起来"的原则设定为开启，而不是常见的 opt-in 保守默认。

    generation（信号扫描/候选生成/调研报告生成）与 interruption（主动推送/
    通知）是方案第 4.1/4.2 节明确要求拆开治理的两个维度，因此这里也拆成
    两组独立配置，不能用同一个开关/频率控制。
    """

    # 总开关：默认开启（opt-out）。关闭后 growth_signal_scan /
    # growth_candidate_derive / sys:growth_advisor_daily 全部跳过，
    # 已生成的数据不会被自动清除（清除是看板/CLI 里单独的显式操作）。
    enabled: bool = True

    # ── 生成频率（generation）──────────────────────────────────────────
    # daily | every_12h | weekly | manual | off，对应 sys:growth_advisor_daily
    # 的调度档位（第 4.1 节）。manual 时该 cron job 保持 disabled，只能通过
    # /growth scan、看板"立即为我看看"按钮手动触发。
    generation_frequency: str = "daily"

    # ── 推送频率（interruption，独立于生成频率，见第 4.2 节）────────────
    # daily（默认，最多 1 条/天，取当天新生成里置信度最高的一条）|
    # weekly_digest（每 7 天把窗口期内新生成的全部报告标题打包成一条摘要
    # 推送，而非逐条推）| kanban_only（只更新看板，不推送）
    notification_frequency: str = "daily"
    # 单日最多推送条数（notification_frequency=daily 时生效）
    notification_max_per_day: int = 1
    # 值得推送的最低置信度阈值，低于此值当天只更新看板、不发通知
    notification_min_confidence: float = 0.6

    # ── 候选克制阈值（对齐 decision_profile_builder.MIN_EVIDENCE_COUNT）──
    min_evidence_count: int = 3
    # 候选队列 pending 状态上限，超过则本轮不再新增（避免无限堆积）
    max_pending_candidates: int = 10
    # 每轮调研 cron 最多处理的 Top-N 候选个数
    max_reports_per_run: int = 2
    # 同一 candidate 被 dismissed 后的冷却期（天），期间不再重复调研同一方向
    dismissed_cooldown_days: int = 30

    # ── 隐私/克制（第 8 节，默认开启但知情权不能省略）──────────────────
    # 首次进入看板是否需要展示一次轻量提示（不打断使用），展示后本地记录
    # 已提示过，不重复弹出
    first_touch_notice_enabled: bool = True

    # ── 用户可选的关注领域黑名单（第 5 节"设置入口"）────────────────────
    # 命中黑名单关键词的候选在 growth_candidate_derive 阶段直接跳过
    excluded_topics: list = field(default_factory=list)

    # ── P3：growth_signal_scan 的 LLM 增强版归纳（可选，默认关闭）──────
    # 默认仍是纯规则式关键词扫描（零 LLM 成本），这是 enabled=True 默认
    # 开启时的成本底线（见方案第 -1/8 节）。只有显式打开这个开关，且
    # 调用方（CLI/API 的 /growth scan）确实处于有 agent 上下文的场景时，
    # 才会额外调用一次 LLM，尝试从 _TOPIC_KEYWORDS 关键词表命中不到的
    # 近期记忆里归纳新的主题——不影响、不覆盖规则式扫描结果，只做补充。
    llm_signal_augment_enabled: bool = False

    # ── 采纳即启动：自主持续调研（growth_advisor 自主定位改进）──────────
    # 用户在看板/API 上"采纳"一个候选后，是否自动衔接后续全部步骤（没有
    # 报告先生成一份 → 落地成 Goal → 用 growth_pursuit 模板生成执行规范
    # 草稿并直接确认 → 绑定周期性）。这是与用户明确讨论过的方向性决定：
    # 默认开启（"采纳"本身就意味着"开始持续调研"，对齐"成长顾问应自主
    # 持续为用户收集素材"的定位），而不是像其余克制默认那样 opt-in——
    # 用户随时可以在「🎯 目标」tab 里对生成的 Goal 调用 unrecur 暂停。
    auto_pursue_on_accept: bool = True
    # 自动绑定周期性时使用的调度表达式，`CronScheduler`/`make_goal_
    # recurring` 认得的格式（"interval:<秒数>"）。默认每天一次，对应与
    # 用户确认过的"每天"节奏。
    auto_pursue_schedule: str = "interval:86400"
    # 自动生成执行规范草稿时使用的模板 id，对应新增的
    # `goal_execution_spec_templates/growth_pursuit.json`——专门为"持续
    # 深化同一个成长方向、不在原地打转"设计，区别于通用的
    # `research_exploration`（那个模板是给"随手调研一下"用的最简骨架）。
    auto_pursue_template_id: str = "growth_pursuit"

    # ── P4-3：采纳后回访（growth_advisor_improvement_plan_v2.md P4-3）───
    # 候选被采纳这么多天后，如果用户还没回答过"有没有推进"，看板会展示一张
    # 回访卡片；答案（progressed/stalled）反馈进置信度调权，不强制回答。
    followup_review_days: int = 30

    # ── P4-4：报告质量分级 / 增量刷新（growth_advisor_improvement_plan_v2.md
    # P4-4）────────────────────────────────────────────────────────────
    # 默认关闭：run_daily_cycle 生成报告正文时用零成本模板。打开后，
    # cron/手动扫描生成报告时会额外花一次 LLM 调用换取更高信息密度，
    # 独立于 llm_signal_augment_enabled（那个控制的是扫描阶段的主题归纳，
    # 不是报告正文）。
    report_quality_llm_enabled: bool = False
    # 候选证据数比上一次生成报告时又新增达到这个数量，才提示"可以刷新
    # 这份报告了"，避免证据每多 1 条就打扰用户。
    report_refresh_min_new_evidence: int = 3
    # [growth_advisor_autonomous_search_and_material_improvement_plan.md
    # 方向"外部世界变化驱动的刷新"] 默认关闭：`reports_needing_refresh()`
    # 此前只看用户自己新增的记忆证据，不看外部世界本身是否发生变化。
    # 打开后，额外拿"生成报告时拼进 prompt 的外部摘录指纹"跟"现在被动
    # 扫描能拿到的摘录"做比对（只读比对本地已抓取的素材，不触发新的
    # 检索/LLM 调用），出现指纹变化时也会把这份报告纳入待刷新列表。
    # 只对开启过 `report_include_external_context` 且当时确实拿到了
    # 摘录的报告生效，其余报告这个信号恒为 `None`，不受影响。
    report_external_drift_refresh_enabled: bool = False
    # 外部摘录指纹变化达到这个条数（新增摘录 + 内容变化的摘录之和）才
    # 计入待刷新，避免摘录来源哪怕只更新了一个字都被判定为"世界变了"。
    report_external_drift_min_changes: int = 1

    # ── P4-5：通知策略细化（growth_advisor_improvement_plan_v2.md P4-5）─
    # 按主题类别覆盖推送偏好——目前只支持"kanban_only"这一种覆盖值（把
    # 某个类别完全静音，只在看板展示、不主动推送），不支持给某个类别单独
    # 设置和全局不同的 daily/weekly_digest 频率（那需要拆分出按类别独立
    # 节流状态，留给更明确的需求出现后再做，避免过度设计）。key 是类别名
    # （"技术类"/"管理类"/"表达类"/"其他类"），value 目前只识别
    # "kanban_only"，其余值原样透传给全局 notification_frequency 逻辑
    # （即等价于未设置覆盖）。
    category_notification_frequency: dict = field(default_factory=dict)

    # ── P5-3：自定义/学习到的主题也能参与类别系统（growth_advisor_
    # improvement_plan_v3.md P5-3）───────────────────────────────────
    # 默认关闭：`_TOPIC_CATEGORIES` 硬编码只覆盖内置 7 个主题，自定义/
    # LLM 学到的主题一律落进"其他类"。打开后，用户新增自定义主题、或者
    # LLM 学习到的主题被确认转正时，额外调用一次 llm_helper 做"4 选 1"
    # 粗粒度分类（技术类/管理类/表达类/其他类），结果持久化到
    # `profile.derived["growth_topic_categories"]`，不重复调用。独立于
    # `llm_signal_augment_enabled`（那个控制"归纳新主题"，这个控制"给
    # 已经存在的主题分类"），两者互不影响。明确不引入 embedding，理由见
    # 方案文档非目标一节。
    topic_category_llm_enabled: bool = False

    # ── 候选去重：LLM 语义判重（本次新增）────────────────────────────────
    # `growth_candidate_derive()` 生成新候选前，除了已有的精确标题去重
    # （`normalize_title_key` 字面匹配），可选再用 LLM 判断这个新主题是否
    # 和"已存在的方向"（pending/accepted 候选 + 已采纳为 Goal 的方向）
    # 语义上是同一件事，只是措辞不同——避免"最近关注的方向"反复被换个说法
    # 重新提议。命中时合并证据到已有候选（有对应候选时）或直接跳过（只
    # 命中 Goal、没有对应候选时），不产生新的重复候选。
    # 默认关闭：和 `llm_signal_augment_enabled`/`topic_category_llm_enabled`
    # 同一惯例——多一次 LLM 调用不该在没有显式开启的情况下发生；关闭时
    # 精确标题去重仍然照常工作，行为与改动前完全一致。
    duplicate_direction_llm_check_enabled: bool = False

    # ── P5-6：候选/推送排序里留一个小比例的"探索位"（growth_advisor_
    # improvement_plan_v3.md P5-6）─────────────────────────────────────
    # 默认关闭：Top-N 报告生成此前完全是"置信度越高越优先"的纯利用策略，
    # 这个开关打破了"证据不够强就不推荐"的一贯克制原则，需要显式打开才
    # 生效。打开后，`max_reports_per_run` 名额里最多留 1 个给"最近几轮
    # 报告里没出现过的类别"，如果所有类别都出现过，退化成正常按置信度
    # 选，不强行制造探索。
    exploration_slot_enabled: bool = False
    # 判断"某类别最近是否出现过"时，往回看最近多少份报告（不含已归档的
    # 旧报告）。
    exploration_recent_window: int = 5

    # ── N3：关键词表 → tech_radar 种子同步（growth_advisor_improvement_
    # plan_v4.md 方向二 2.2 节）────────────────────────────────────────
    # 默认关闭：这会实际修改 agent_config.json 的内容（写入
    # TechRadarConfig.keywords），属于有实际外部效果的写操作，不应该
    # 默认开启——用户需要显式打开才会触发这个联动。打开后，
    # `run_daily_cycle()` 每日收尾时会把成长顾问里"已确认"
    # （confirmed_by_user=True）且当前未被隐藏的主题关键词同步进
    # `TechRadarConfig.keywords`（幂等，只增不减，见
    # `sync_confirmed_topics_to_tech_radar()`）。
    sync_confirmed_topics_to_tech_radar_enabled: bool = False

    # ── N4：调研报告可选纳入外部资讯背景（growth_advisor_improvement_
    # plan_v4.md 方向二 2.4 节）──────────────────────────────────────
    # 默认关闭，独立于 report_quality_llm_enabled（用户可能想要更好的
    # 报告质量但不想引入外部资讯作为背景，两者应该能独立控制）。打开后，
    # 仅当报告走 LLM 生成路径（`llm_helper` 非 None）时，才会把
    # `_external_signal_count_for_topic()` 统计到的外部资讯数量作为
    # "背景参考"额外拼进 prompt——只影响 LLM 输入，不改变候选的置信度/
    # 排序（2.3/2.4 节"仅展示、不影响判断"的克制设计）。
    report_include_external_context: bool = False

    # ── Goal/Cron 打通（growth_advisor_goal_cron_integration_plan.md）──
    # 阶段 A：兴趣方向 ⇄ Goal 对齐分析，纯规则式关键词匹配，零 LLM 成本，
    # 默认开启（对齐项目"默认零成本可用"的一贯原则）。关闭后
    # `goal_growth_alignment()` 直接返回空结果，`/growth align` 提示
    # 功能已关闭，不影响候选/报告/回访等其余机制。
    goal_alignment_enabled: bool = True
    # 一个 Goal 的 status="active" 但 last_touched_at 超过这么多天没动，
    # 才判定为"停滞"。独立于 `followup_review_days`（那是"候选采纳多久
    # 后该回访"），两者语义不同，不应该共用一个默认值。
    goal_alignment_stalled_days: int = 21
    # 关键词匹配之外，是否额外调一次 LLM 对"规则没匹配上的兴趣方向"和
    # "规则没匹配上的 Goal"做一次语义匹配（比如兴趣叫"数据分析能力"、
    # Goal 叫"提升可视化技能"，字面不重合但语义相关，规则关键词匹配不
    # 出来）。默认关闭（保持阶段 A 总开关"零 LLM 成本"的默认基线），
    # 且只在有 agent 上下文的调用路径（CLI `/growth align`、API
    # `/growth/align`）才会真正触发；LLM 输出的匹配只在两侧候选池里
    # 各自的 id 都能对上号时才采纳，避免幻觉匹配。命中的匹配单独放进
    # 返回结果的 `llm_suggested_matches`，跟关键词精确匹配的
    # `linked_goals` 区分开——前者是"建议你看看要不要关联"，不会自动
    # 写入任何持久化的关联关系。
    goal_alignment_llm_enabled: bool = False

    # ── 反馈模式统计展示第二步（growth_advisor_ideal_advisor_gap_and_
    # roadmap_plan.md 方向 2 第二步）────────────────────────────────────
    # 第一步（`growth_feedback_pattern_summary()` 的规则式分组统计）默认
    # 一直开启、零 LLM 成本；这个开关控制的是在此之上、有 agent 上下文时
    # 额外调一次 LLM，把统计摘要转成一句更自然的归纳文字（放进返回值的
    # `llm_insight` 字段，不覆盖规则式的 `summary_text`）。默认关闭，
    # 对齐 `goal_alignment_llm_enabled`/`llm_signal_augment_enabled` 等
    # 同类开关"默认零 LLM 成本"的基线；且即便打开，也只在规则式统计已经
    # `has_enough_data=True` 时才会触发（样本不够时统计本身就没有可归纳
    # 的东西，不值得为此多花一次 LLM 调用）。这段归纳文字仍然只是展示，
    # 不会喂回任何候选排序/置信度计算——跟第一步同样的"止步于展示"取舍。
    feedback_pattern_llm_enabled: bool = False

    # ── 方向 6：调研风格智能分类（growth_advisor_ideal_advisor_gap_and_
    # roadmap_plan.md 方向 6）──────────────────────────────────────────
    # `determine_pursuit_style()` 的规则式关键词匹配默认一直开启、零 LLM
    # 成本，总能返回一个合法的调研风格标签（技能实操类/知识理论类/习惯
    # 养成类）。这个开关控制的是在规则结果之上、有 agent 上下文时额外调
    # 一次 LLM 复核/纠偏——默认关闭，对齐同批其它 LLM 增强开关"默认零
    # 成本"的基线；开启后 LLM 解析失败/异常时静默沿用规则式结果，不会
    # 让这个 Goal 的自动持续推进流程因为分类失败而中断。
    pursuit_style_llm_enabled: bool = False

    # ── 方向 6 动态修正（growth_advisor_ideal_advisor_gap_and_roadmap_
    # plan.md 方向 6，2026-08 追加候选并落地）───────────────────────────
    # `auto_pursue_candidate()` 只在 Goal 首次落地时基于候选标题猜一次
    # 调研风格，容易猜错。这个字段控制累计满多少轮后，改用该方向实际
    # 已经产出的 `covered_subtopics` 文本重新分类一次（`maybe_
    # reclassify_pursuit_style()`）；`<=0` 视为关闭。默认 `8`——比
    # `reorganize_every_n_cycles`（10）略短，比 `pursuit_self_check_
    # every_n_cycles`（5）略长，取一个"有足够内容可供判断、但不会拖太
    # 久才修正"的折中值。
    pursuit_style_reclassify_every_n_cycles: int = 8

    # ── 方向 7：报告质量自动闭环（growth_advisor_ideal_advisor_gap_and_
    # roadmap_plan.md 方向 7，2026-08 追加候选并落地）──────────────────
    # `report_not_useful` dismiss 反馈此前只是"记录下来给人看"（月度
    # 复盘/诊断面板），从没被用来反过来指导报告生成策略。这个开关控制
    # 的是：某个方向的报告被标"内容太笼统"累计达到
    # `report_quality_auto_upgrade_threshold` 次时，下一次生成自动
    # 临时切到 LLM 生成路径（即便全局 `report_quality_llm_enabled` 关
    # 闭），只影响这一份报告，不修改全局开关本身。默认关闭，对齐"增加
    # 调用成本的能力默认关闭"的一贯原则；开启后也只在调用方确实能拿到
    # `llm_helper`（比如 cron 触发）、且命中阈值的方向才会真正多花一次
    # LLM 调用，不是无差别升级全部报告。
    report_quality_auto_upgrade_enabled: bool = False
    # 触发自动升级所需的"报告没写好"累计次数，`<=0` 视为关闭（等价于
    # `report_quality_auto_upgrade_enabled=False`）。
    report_quality_auto_upgrade_threshold: int = 2

    # ── 情境感知候选：推送的软性节流（growth_advisor_ideal_advisor_gap_
    # and_roadmap_plan.md 感知维度分析，2026-08 追加候选并落地）────────
    # 推送节流此前完全是静态规则（置信度阈值、每天最多几条），感知不到
    # "用户最近是不是明显没那么活跃"。这个开关控制的是：最近一周的记忆
    # 条目数相对更早几周的周均值显著偏低时，临时把有效置信度门槛抬高
    # 一点（软性因子，依然可能推送，只是需要更高置信度），不是硬性
    # 阻断整轮推送。默认关闭，对齐"新增行为默认不改变既有推送节奏"的
    # 原则；开启后不产生任何额外 LLM/网络调用，只是多扫一次已经在内存
    # 里的 `memory_store` 条目。
    notification_context_aware_throttle_enabled: bool = False
    # 判定"最近明显更安静"的密度比值门槛：最近一周条目数 / 基线周均值
    # 低于这个值才触发软性抬高；`<=0` 视为关闭（永远不会触发，等价于
    # 该开关没打开）。
    notification_low_activity_ratio_threshold: float = 0.3
    # 命中"更安静"时，在 `notification_min_confidence` 基础上额外加多少
    # （封顶 1.0）。
    notification_low_activity_confidence_boost: float = 0.15

    # ── A3：对齐分析结果批量落地（growth_advisor_autonomy_deepening_
    # plan.md 方向 A3）─────────────────────────────────────────────────
    # `/growth align --adopt-all` / 看板"全部采纳"一次性对"未匹配兴趣"
    # 列表逐条调用 `auto_pursue_candidate()` 时，单次最多处理这么多条，
    # 避免批量操作一次性触发过多 LLM 调用（生成报告 + 生成执行规范）
    # 造成成本意外爆炸——语义上类似 `max_reports_per_run`，但这是用户
    # 主动触发的批量动作，不共用同一个每日 cron 的上限，独立配置。
    goal_alignment_adopt_all_max_batch: int = 3

    # ── 调研信息获取与整理（growth_advisor_research_quality_plan.md）──
    # 阶段 2：报告生成先让 LLM 提炼 3-4 个具体问题，再逐个回答，替代
    # "一次性要求四段通用内容"。默认关闭——这一步是额外一次 LLM 调用，
    # 成本翻倍，遵循"增加调用成本的能力默认 opt-in"的一贯原则。
    report_two_stage_enabled: bool = False
    # 阶段 4：如果这个方向之前的报告被标过"内容太笼统"
    # （report_not_useful），生成 prompt 时追加一句强约束提醒 LLM
    # 别再写得空泛。不产生任何新的 LLM 调用，只是复用已有的
    # `_report_quality_dismiss_counts()` 统计结果调整 prompt 文字，
    # 成本几乎为零，默认开启。
    report_dismiss_reason_adaptive_enabled: bool = True

    # ── 真正的主动检索（growth_advisor_active_search_and_lifecycle_
    # plan.md 方向一）──────────────────────────────────────────────────
    # 默认关闭：会实际发起 web_search 调用，属于"增加调用成本的能力
    # 默认 opt-in"。打开后，`generate_growth_report()` 在
    # `_external_signal_count_for_topic()` 命中 0 条、且调用方传入了
    # `web_search_fn` 时，才会现查一次并把结果落一份 wiki 页面；只在
    # 被动扫描（N4）确实没有可用素材时补一次，不是"素材不够多再补"。
    report_active_search_enabled: bool = False
    # [growth_advisor_autonomous_search_and_material_improvement_plan.md
    # 阶段二] 单次报告最多触发几次检索调用，默认 1（与此前行为一致，只用
    # 关键词表第一个关键词查一次）。调大后会用关键词表里后续的关键词各
    # 追加一个查询角度（见 growth_advisor.py::_build_active_search_
    # queries()），关键词数量不够时不重复拼凑同一个角度。默认值不变，
    # 只有显式调大才会增加 web_search_fn 调用次数。
    report_active_search_max_calls: int = 1

    # ── cron 无人值守路径的主动检索（next_doc/growth_advisor_cron_search_
    # and_status_history_plan.md 方向一）──────────────────────────────
    # 默认关闭：`run_daily_cycle()`（`sys:growth_advisor_daily`）此前完全
    # 不触发主动检索，覆盖面依赖人工触发 `/growth report`。打开后，借用
    # `tech_radar_search.py` 同一套"控频率+控预算"节流模式，cron 路径
    # 每天最多对 `cron_triggered_active_search_daily_limit` 个"证据数
    # 最高但从未有过任何外部背景"的候选触发一次定向检索，复用
    # `_active_search_excerpts_for_topic()` 落盘管道，不新增检索通道。
    cron_triggered_active_search_enabled: bool = False
    cron_triggered_active_search_daily_limit: int = 1

    # ── C1：定期整理（growth_advisor_autonomy_deepening_plan.md 方向 C1）──
    # `growth_pursuit` 模板此前只会"线性追加"，轮次一多页面会越来越长、
    # 缺乏组织。默认开启（零 LLM 成本——只是往 prompt 里多拼一段整理
    # 指令，复用同一次执行循环，不新增 cron job/数据结构）：每累计这么
    # 多轮，下一次执行时额外提示"顺带做一次重新组织"。设为 0 或负数视为
    # 关闭这条提示。
    reorganize_every_n_cycles: int = 10

    # ── C2：本轮新增摘要推送（growth_advisor_autonomy_deepening_plan.md
    # 方向 C2）──────────────────────────────────────────────────────────
    # 默认开启：每轮持续调研完成后，把 handoff.covered_subtopics 的新增
    # 差集整理成一句"本轮新增摘要"，暂存起来，等下一次 `_maybe_dispatch_
    # notification()`/`_maybe_dispatch_weekly_digest()` 真正推送时顺带
    # 打包进同一条消息——复用已有的推送节流（notification_frequency/
    # notification_max_per_day），不为这条摘要单独消耗一次推送额度。
    pursuit_digest_enabled: bool = True

    # ── 方向 1：B1 增量质量校验的 LLM 复核（growth_advisor_autonomy_
    # deepening_plan_v2.md 方向 1）────────────────────────────────────────
    # 默认关闭：会额外触发一次 LLM 调用，属于"增加调用成本的能力默认
    # opt-in"。打开后，`evaluate_cycle_increment()` 只在规则式初筛已经
    # 判定 `low_increment=True` 的轮次上，才追加一次 LLM 语义复核——
    # 判断这次"重叠比例高"究竟是真的在原地打转，还是子话题标签凑巧
    # 重复但内容其实往前推进了。复核结果（`llm_reviewed`/`llm_verdict`/
    # `llm_reason`）只是诊断展示，跟规则式判断分开记录，不覆盖规则式
    # 判断本身，也不影响 B2 饱和度 streak 的计数方式。
    pursuit_increment_llm_review_enabled: bool = False

    # ── 方向 4：跨方向全局视角摘要（growth_advisor_ideal_advisor_gap_
    # and_roadmap_plan.md 方向 4）──────────────────────────────────────
    # "长期无人查看"的判定阈值：某个方向的素材已经比用户上次查看时新了
    # 多少轮，就计入 `pursuits_portfolio_summary()` 的"建议关注"分类。
    # 默认 5——跟方向 1 文档里"自测环节间隔不宜太长"是类似的取舍，太大
    # 意义打折，太小容易对正常节奏的方向也报警。
    pursuit_long_unviewed_threshold: int = 5

    # ── 方向 5：学习效果自测环节（growth_advisor_ideal_advisor_gap_and_
    # roadmap_plan.md 方向 5）──────────────────────────────────────────
    # 累计满这个轮次数时，在当轮子 Objective description 里追加一段
    # "顺带生成自测小节"的提示，复用同一次执行循环已有的 LLM 调用，零
    # 增量成本，默认开启。<=0 视为关闭。默认 5——比 reorganize_every_n_
    # cycles 的默认 10 更小，因为自测的价值在于"及时发现没跟上"，间隔
    # 太长意义打折。
    pursuit_self_check_every_n_cycles: int = 5


@dataclass
class CapabilityLearningConfig:
    """[next_doc/persona_capability_learning_design.md] 人设能力自主学习配置。

    真实 retriever（对接 web_search）与对应 cron 任务（`sys:
    capability_learning_cycle`/`sys:capability_question_sweep`）此前是
    opt-in（默认关闭），需要用户在 P1 收尾评审通过、且 §13.3-g 合规过滤
    落地之后才手动打开。经确认合规过滤（`apply_compliance_filter()` +
    `requires_disclaimer` 标注）已随 P1 一起实现并有测试覆盖，本轮改为
    **默认开启**——`retriever_enabled=True` 且两个 cron job 默认
    `enabled=True`，Track 一经创建就会按既定节奏自动检索、沉淀、清理过期
    问题，不再需要用户额外去配置里手动打开。用户仍可以随时在
    `agent_config.json` 里把 `retriever_enabled` 改回 `False`，或在看板
    「⏰ Cron 任务」Tab / CLI 里单独 disable 这两个 job（关闭 retriever
    时循环仍会跑，只是需要检索的子主题记 `skipped` 台账，不产生网络请求）。

    [v0.22 §14.4 检索方式扩展] 此前 `retriever_enabled=True` 时唯一的真实
    检索实现是 `make_web_search_retriever()`——直接调用
    `web_search/factory.py` 的 provider，只能做"关键词 → 搜索引擎"这一种
    最朴素的检索，查不到多跳信息、用不了项目里已有的 skill 生态（比如某个
    领域专用的检索/分析 skill）。`retriever_mode` 新增一个可选档位
    `"agent"`：改用 `make_agent_retriever()`，让一个受限工具集的 SubAgent
    （见 `orchestrator/sub_agent.py`）带着 `web_search`/`search_knowledge`/
    `skill_list`/`skill_activate` 等只读工具自主完成调研，必要时可以先
    激活更合适的 skill 再检索，产出一段综合摘要，而不是单次 API 调用的原始
    结果列表。默认仍保留 `"web_search"`（成本更低、单轮调用耗时更短，符合
    项目一贯"新机制先保守默认"的取向），用户可按需切到 `"agent"`。
    """

    # 总开关：是否允许 `run_capability_learning_cycle` 使用真实检索（而非
    # 跳过并记 skipped 台账）；具体走哪种检索实现由 `retriever_mode` 决定。
    # 默认 True（opt-out）。
    retriever_enabled: bool = True

    # 检索实现档位：
    #   "web_search" —— `make_web_search_retriever()`，单次关键词搜索
    #                    （默认，成本低、耗时短）
    #   "agent"      —— `make_agent_retriever()`，用受限工具集的 SubAgent
    #                    自主调研（可用 web_search/search_knowledge/skill_*
    #                    等工具，必要时先激活更合适的 skill 再检索），
    #                    能覆盖 web_search 单次调用查不到的复合/多跳内容，
    #                    但会多消耗 LLM 调用与时间（受
    #                    `agent_retriever_max_turns`/
    #                    `agent_retriever_timeout_seconds` 限制）。
    # 未识别的值按 "web_search" 处理（见 capability_cmd.py 选择逻辑）。
    retriever_mode: str = "web_search"

    # 每个子主题检索时最多拉取的搜索结果条数，直接传给
    # WebSearchProvider.search(max_results=...)。太大会增加合规过滤的
    # 误判概率（更多句子意味着更多可能命中风险短语正则的机会）和单轮
    # 循环耗时，默认给一个克制的小值。仅 retriever_mode="web_search" 时
    # 生效。
    max_results_per_topic: int = 3

    # 每条搜索结果摘要（summary）截断长度（字符数），避免整页原文被摘要
    # 字段吞掉——wiki 沉淀的应该是"检索到了什么要点"，不是原文转载
    # （原文转载还涉及版权问题，不在本模块讨论范围内，但截断本身是
    # 独立于版权考虑的克制默认）。两种 retriever_mode 都会用这个值截断
    # 最终写入的摘要文本。
    summary_max_chars: int = 400

    # [v0.22] retriever_mode="agent" 时，单个子主题调研 SubAgent 允许的
    # 最大工具调用轮数（Task.max_turns）。太大会让一次调研无限展开、拖慢
    # 整轮 cron 循环，默认给一个够用但克制的小值。
    agent_retriever_max_turns: int = 6

    # [v0.22] retriever_mode="agent" 时，等待单个子主题调研 SubAgent 完成
    # 的超时时间（秒）。超时后按"本轮无结果"处理（记 skipped/research_empty
    # 台账，下一轮重试），已启动的 SubAgent 线程不会被强行杀死，只是发送
    # 取消信号，避免阻塞整轮 cron 循环。
    agent_retriever_timeout_seconds: int = 240

    # ── v0.21 §8 通知系统接入 ──────────────────────────────────────────
    # 是否在 run_capability_learning_cycle 每轮跑完后尝试推送一条摘要
    # 通知（"你有 N 个待回答问题，本轮新沉淀 M 篇 wiki 页面"）。风格
    # 对齐 GrowthAdvisorConfig.notification_frequency/max_per_day，但
    # 这里是 CapabilityLearningConfig 自己独立的一套字段，不和
    # GrowthAdvisorConfig 混用（两个子系统的节流状态、节流额度互相独立，
    # 关掉一个不影响另一个）。默认 True，但只有"确有新内容"（新问题或新
    # wiki 页面）时才会真正发送，没有新内容的空轮不会产生任何推送。
    notification_enabled: bool = True

    # "kanban_only" 时完全不主动推送（看板轮询 /v1/capability/* 端点不
    # 受影响，仍然随时可查）；"daily" 时按天节流，当天最多推
    # notification_max_per_day 条摘要（多轮合并成一条，不逐轮推送）。
    notification_frequency: str = "daily"

    # 当天（本地时区自然日）最多推送几条摘要。多轮循环产生的新内容会
    # 合并进同一条里，不会因为触发了多次 cron 就发多条。
    notification_max_per_day: int = 1


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
    # [daemon_autonomous_state_recovery_plan.md 阶段一] run_turn() 返回前的最后
    # 一道"结果健全性校验"：命中 max_turns 硬顶 / 纠错重试用尽等路径时，避免把
    # 仍带 <tool_use> 协议残留的畸形文本当作最终结果返回（尤其会污染自主任务
    # ObjectiveExecutor 的 step.result_summary）。默认开启；关闭时完全回退到
    # 升级前行为（一键回退）。
    result_sanity_check_enabled: bool = True


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
    # [SYS-MAX-TURNS-POLICY] 单次 run_turn 内 agentic loop 达到 max_turns 时的处理策略：
    #   "stop"             —— 原有行为，达到上限直接停下等待真人输入（默认）
    #   "continue"         —— 自动注入一条模拟的"继续"消息，不压缩，直接继续跑，
    #                          预算按 max_turns 的增量往上加，直到 max_turns_hard_limit
    #   "compact_continue" —— 先执行一次历史压缩（等价于 /compact），再注入"继续"消息并延长预算
    max_turns_on_limit: str = "stop"
    # 无论 max_turns_on_limit 取何值，loop_count 增长到这个硬上限后一律停止，
    # 防止 continue/compact_continue 策略下模型陷入死循环导致无限跑下去。
    max_turns_hard_limit: int = DEFAULT_MAX_TURNS * 5

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

    # [SYS-BASH-STREAM] bash 工具是否实时把子进程输出打印到终端（边跑边看），
    # 默认关闭——保持原有 subprocess.run(capture_output=True) 的一次性返回行为
    # 完全不变。开启后：
    #   - 终端会实时看到命令的逐行输出（不影响喂给 LLM 的最终工具结果内容）
    #   - 超时（timeout）时不再只返回 "[timeout after Ns]"，而是把已经产生的
    #     部分输出也一并返回，并在末尾追加明确的超时标记，避免部分输出被丢弃。
    bash_stream_output_enabled: bool = False

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

    # ── Compact 机制主动化改进计划 P2-B（compact_mechanism_improvement_plan.md）──
    # recall_from_raw_history 只读工具总开关。默认关闭——工具本身仍注册在
    # 全局 registry 中（与 notepad_enabled 等既有取舍一致），关闭时调用直接
    # 返回错误提示，不做任何检索。开启后可按关键词在当前 session 的完整
    # raw history（含已被 compact 掉的片段）里找回原始内容。
    recall_history_enabled: bool = False
    # "keyword"（默认，复用 triggers.py::_simple_keywords 关键词匹配，
    # 不引入额外依赖）| "embedding"（预留，当前未实现，退回 keyword 档）
    recall_history_mode: str = "keyword"

    # [next_doc/errors_tool_executor_log_toggle_plan.md] 全局错误日志
    # （~/.agent/logs/error.jsonl）里 `where` 前缀为
    # "mini_agent.tool_executor" 的记录是否落盘。默认 True（保持改动前的
    # 行为不变）。tool_executor 里这类记录绝大多数是工具调用过程中的
    # 预期内降级路径（如历史消息里工具结果 JSON 解析失败），当用户觉得
    # 这类记录占比过高、淹没了其它更需要关注的错误时，可以在
    # agent_config.json 里设 `"save_tool_executor_error_logs": false`
    # 关闭——注意这是"从源头不写入"，跟 `errors.error_log_stats()` 的
    # `exclude_tool_executor`（只在统计展示时过滤、不影响落盘）是两个
    # 独立的开关，互不冲突，可以只开其中一个。
    save_tool_executor_error_logs: bool = True

    # ── 功能子配置块（每个功能域独立聚合）────────────────────────────────────
    memory:     MemoryConfig     = field(default_factory=MemoryConfig)
    compress:   CompressConfig   = field(default_factory=CompressConfig)
    tool_trim:  ToolTrimConfig   = field(default_factory=ToolTrimConfig)
    skill:      SkillConfig      = field(default_factory=SkillConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    session:    SessionConfig    = field(default_factory=SessionConfig)
    profile:    ProfileConfig    = field(default_factory=ProfileConfig)
    memory_backfill: MemoryBackfillConfig = field(default_factory=MemoryBackfillConfig)
    debug:      DebugConfig      = field(default_factory=DebugConfig)
    http:       HttpConfig       = field(default_factory=HttpConfig)
    retry:      RetryConfig      = field(default_factory=RetryConfig)
    mcp:        MCPConfig        = field(default_factory=MCPConfig)
    web_search: WebSearchConfig  = field(default_factory=WebSearchConfig)
    tech_radar: TechRadarConfig  = field(default_factory=TechRadarConfig)
    ecosystem_positioning: EcosystemPositioningConfig = field(default_factory=EcosystemPositioningConfig)
    reminder:   ReminderConfig   = field(default_factory=ReminderConfig)
    proprioception: ProprioceptionConfig = field(default_factory=ProprioceptionConfig)
    affordance: AffordanceConfig = field(default_factory=AffordanceConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    digest_advisor: DigestAdvisorConfig = field(default_factory=DigestAdvisorConfig)
    growth_advisor: GrowthAdvisorConfig = field(default_factory=GrowthAdvisorConfig)
    capability_learning: CapabilityLearningConfig = field(default_factory=CapabilityLearningConfig)
    cron: CronConfig = field(default_factory=CronConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    workflow:   WorkflowConfig   = field(default_factory=WorkflowConfig)
    format_correction: FormatCorrectionConfig = field(default_factory=FormatCorrectionConfig)
    role_agent: RoleAgentConfig  = field(default_factory=RoleAgentConfig)
    goal_mode:  GoalModeConfig   = field(default_factory=GoalModeConfig)
    goal_execution_spec: GoalExecutionSpecConfig = field(default_factory=GoalExecutionSpecConfig)
    execution_phase: ExecutionPhaseConfig = field(default_factory=ExecutionPhaseConfig)
    cycle_tuning: CycleTuningConfig = field(default_factory=CycleTuningConfig)
    # [goal_cron_cycle_proactive_patrol_and_health_overview_plan.md §2.7]
    cycle_patrol: CyclePatrolConfig = field(default_factory=CyclePatrolConfig)
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
    def skill_auto_unload_enabled(self) -> bool:      return self.skill.auto_unload_enabled
    @property
    def skill_auto_unload_idle_seconds(self) -> int:  return self.skill.auto_unload_idle_seconds
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


