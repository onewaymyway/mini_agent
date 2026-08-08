"""
evolution/cron_scheduler.py — Daemon 模式定时任务调度器

支持两种 schedule 格式：
  "interval:<seconds>"   — 固定间隔，如 "interval:3600"（每小时）
  "cron:<expr>"          — 标准 cron 表达式，如 "cron:0 */6 * * *"（每6小时）
    cron 表达式字段：分 时 日 月 周（与 POSIX cron 一致）
    不依赖外部库，内置轻量 cron 解析器

内置 job（首次初始化时写入 cron_jobs.json，用户可修改 enabled/schedule，
但 sys: 前缀 job 不可删除，只可 disable）：

  sys:consolidation         — 巩固循环扫描（技能剪枝/能力地图）  interval:21600
  sys:workdir_sync   — 工作区知识整合                     interval:3600
  sys:self_eval      — 能力自评（capability_map 更新）     interval:86400
  sys:goal_review    — 目标清理（已完成/过期 Goal）         interval:43200
  sys:digest_trim    — activity_digest 日志修剪            interval:604800
  sys:session_cleanup — Session 清理（保留在用/近期，其余先抽取再删）interval:604800
  sys:self_maintain  — 自维护健康检查（具身改进 C4）         interval:86400
  sys:daily_digest           — 每日融合日报（行为+目标+提交）      cron:0 22 * * *
  sys:next_action_digest     — 主动推荐排序（停滞目标/注意力错配）  interval:10800
  sys:decision_profile_update — 决策画像归纳（默认关闭，见改进计划）interval:604800
  sys:growth_advisor_daily        — 成长顾问候选扫描+调研报告（默认开启）cron:30 22 * * *
  sys:growth_monthly_retrospective — 成长顾问月度复盘（默认开启）      interval:2592000

存储：<project_root>/.agent/cron_jobs.json
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.evolution.cron_job_runner import CronJobRunner


# ── CronJob 数据结构 ──────────────────────────────────────────────────────────

@dataclass
class CronJob:
    id: str
    name: str
    schedule: str               # "interval:<sec>" 或 "cron:<expr>"
    task_template: str          # 提交到 InputQueue 的消息模板
    enabled: bool = True
    last_run_at: float = 0.0
    next_run_at: float = 0.0    # 由 _compute_next() 计算
    run_count: int = 0
    tags: list[str] = field(default_factory=list)
    initiator: str = "cron"     # 区别于 "autonomous" / "user"
    description: str = ""       # 人类可读说明

    # [goal_cron_binding_plan.md Track A] Goal ⇄ Cron 绑定：
    # run_mode="message"（默认，即现状）时 _fire() 走原有的裸消息投递路径；
    # run_mode="goal_cycle" 时 _fire() 改为调用 CronScheduler.set_goal_cycle_handler()
    # 注册的回调，为 goal_id 对应的 Goal 派生/推进一轮子 Objective，task_template
    # 此时的语义变成"这一轮 Objective 的任务描述"，不再直接进 InputQueue。
    goal_id: Optional[str] = None
    run_mode: str = "message"

    # [scheduling_unification_and_kanban_visibility_improvement_plan.md P2]
    # 数值越大优先级越高。同一次 tick() 内多个 job 同时到期时，按此字段
    # 降序排序后依次触发——只影响"谁先被提交/先拿到 worker 排队位置"，
    # 不做抢占（正在执行的 job 不会被打断）。缺省 0 保证旧的 cron_jobs.json
    # 反序列化后行为等同于改造前（原本就是插入顺序，且旧数据没有这个字段）。
    priority: int = 0

    # [goal_cron_feedback_and_output_policy_plan.md Track A] 与 GoalNode.user_feedback
    # 同结构，用户对本 CronJob 的持续意见反馈历史（追加记录，供 UI/CLI 回看）。
    user_feedback: list[dict] = field(default_factory=list)

    # [goal_cron_unified_scheduler_improvement_plan.md P2] 连续"到点但未能
    # 成功触发"的次数（_fire() 返回 False 时 +1，成功触发一次清零）。
    # 缺省 0 保证旧 cron_jobs.json 反序列化后行为等同于改造前。
    consecutive_skip_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule,
            "task_template": self.task_template,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "run_count": self.run_count,
            "tags": self.tags,
            "initiator": self.initiator,
            "description": self.description,
            "goal_id": self.goal_id,
            "run_mode": self.run_mode,
            "priority": self.priority,
            "user_feedback": self.user_feedback,
            "consecutive_skip_count": self.consecutive_skip_count,
            # [看板 cron 面板补齐删除功能] 显式下发 is_system，避免前端
            # 只能靠 id.startswith("sys:") 这种约定猜测，接口更自描述。
            "is_system": self.is_system,
        }

    @staticmethod
    def from_dict(d: dict) -> "CronJob":
        return CronJob(
            id=d.get("id", ""),
            name=d.get("name", ""),
            schedule=d.get("schedule", "interval:3600"),
            task_template=d.get("task_template", ""),
            enabled=d.get("enabled", True),
            last_run_at=d.get("last_run_at", 0.0),
            next_run_at=d.get("next_run_at", 0.0),
            run_count=d.get("run_count", 0),
            tags=d.get("tags", []),
            initiator=d.get("initiator", "cron"),
            description=d.get("description", ""),
            goal_id=d.get("goal_id"),
            run_mode=d.get("run_mode", "message"),
            priority=d.get("priority", 0),
            user_feedback=d.get("user_feedback", []),
            consecutive_skip_count=d.get("consecutive_skip_count", 0),
        )

    @property
    def is_system(self) -> bool:
        return self.id.startswith("sys:")

    def time_until_next(self) -> float:
        """距下次触发还有多少秒（负数表示已过期）。"""
        return self.next_run_at - time.time()

    def next_run_str(self) -> str:
        """人类可读的下次运行时间。"""
        delta = self.time_until_next()
        if delta <= 0:
            return "now / overdue"
        if delta < 60:
            return f"in {delta:.0f}s"
        if delta < 3600:
            return f"in {delta/60:.0f}m"
        if delta < 86400:
            return f"in {delta/3600:.1f}h"
        return f"in {delta/86400:.1f}d"


# ── 内置 Job 定义 ─────────────────────────────────────────────────────────────

_BUILTIN_JOBS: list[dict] = [
    {
        "id": "sys:consolidation",
        "name": "巩固循环扫描",
        "schedule": "interval:21600",
        "description": "技能剪枝、去重、能力地图更新（每 6 小时）",
        "task_template": "[系统维护] 执行巩固循环扫描：检查技能库冗余、更新能力地图、评估晋升候选",
        "tags": ["maintenance", "evolution"],
        "enabled": True,
    },
    {
        # next_doc/wiki_next_phase_improvement_plan.md 第 5 节：wiki 巩固此前
        # 完全捆在 sys:consolidation 一次 6 小时任务里跑（且 wiki 部分只是
        # 其中一个子步骤），无法单独观测/调频/手动触发。这里拆出"主动缺口
        # 发现"这一类动作单独调度——它和 sys:consolidation 里的被动镜像/
        # 判重不是一回事：sys:consolidation 处理"已经进了 pending 队列的
        # 内容该怎么整理"，这个 job 处理"wiki 里哪些地方明显缺内容，该不该
        # 主动补"。频率比 sys:consolidation 更低（12h），因为触发的补全
        # 子任务本身成本更高。
        "id": "sys:wiki_gap_scan",
        "name": "wiki 知识缺口扫描",
        "schedule": "interval:43200",
        "description": "扫描浅层实体/孤儿页面/陈旧专题页，标注陈旧专题页并可派发补全子任务（每 12 小时）",
        "task_template": (
            "[wiki 维护] 执行一次 /wiki gap-scan --max-results 3，"
            "查看是否发现浅层实体或孤儿页面；如果发现的缺口确实值得花时间补全"
            "（比如某个核心模块只有一句描述、缺乏关系），可以自行读源码/文档"
            "补全对应 wiki 页面，不需要机械地对每条缺口都行动"
        ),
        "tags": ["maintenance", "wiki"],
        "enabled": True,
    },
    {
        # next_doc/wiki_next_phase_improvement_plan.md 第 5 节：低频清理类，
        # 和 sys:digest_trim 同属一档（7d 一次），处理 wiki/world_writer.py
        # 兜底页长期堆积、没人整理的问题。
        "id": "sys:wiki_fallback_cleanup",
        "name": "wiki 兜底页面清理",
        "schedule": "interval:604800",
        "description": "归并/标记 session-facts 兜底页里长期未被合并的 fact（每 7 天）",
        "task_template": "[wiki 维护] 执行一次 /wiki fallback-cleanup --days 30",
        "tags": ["maintenance", "wiki"],
        "enabled": True,
    },
    {
        # wiki/quarantine.py + wiki/quarantine_repair.py：全量扫描 wiki/
        # 发现解析失败的页面，记入隔离区 _quarantine.json，并对已知的
        # 格式性问题（frontmatter.links 写成裸字符串等）尝试自动修复。
        # 零 LLM 成本，本地回调（ensure_wiki_quarantine_repair_job 在
        # daemon 启动时通过 register_local_handler 注册，这里的
        # task_template 字段不会被用到，仅为跟其它 job 的记录格式保持
        # 一致，方便 /cron list 展示描述）。
        "id": "sys:wiki_quarantine_repair",
        "name": "wiki 问题页面自动修复",
        "schedule": "interval:21600",
        "description": "扫描 wiki/ 记录解析失败的页面并尝试自动修复已知格式问题（每 6 小时）",
        "task_template": "",
        "tags": ["maintenance", "wiki"],
        "enabled": True,
    },
    {
        "id": "sys:workdir_sync",
        "name": "工作区知识整合",
        "schedule": "interval:3600",
        "description": "同步工作区文件变化到 WorkdirKnowledge（每小时）",
        "task_template": "[系统维护] 整合工作区知识：扫描文件变化、更新 WorkThread 进展、刷新 next_suggested",
        "tags": ["maintenance"],
        "enabled": True,
    },
    {
        "id": "sys:self_eval",
        "name": "能力自评",
        "schedule": "interval:86400",
        "description": "评估当前能力边界，更新 capability_map 置信度（每 24 小时）",
        "task_template": "[自我评估] 回顾最近 24 小时的工具使用和任务结果，更新 capability_map：哪些能力变强/变弱，哪些场景仍不确定",
        "tags": ["evolution", "self_awareness"],
        "enabled": True,
    },
    {
        "id": "sys:goal_review",
        "name": "目标清理",
        "schedule": "interval:43200",
        "description": "清理已完成/长期无进展的 Goal 和 Objective（每 12 小时）",
        "task_template": "[目标管理] 审查 GoalBacklog：标记已实质完成的 Objective 为 completed，识别超过 7 天无进展的 Objective 并暂停",
        "tags": ["maintenance", "goals"],
        "enabled": True,
    },
    {
        "id": "sys:digest_trim",
        "name": "日志修剪",
        "schedule": "interval:604800",
        "description": "修剪 activity_digest.jsonl，保留最近 30 天（每 7 天）",
        "task_template": "[系统维护] 修剪 activity_digest.jsonl：删除 30 天前的记录，压缩历史统计",
        "tags": ["maintenance"],
        "enabled": True,
    },
    {
        # session 清理功能设计方案：长期运行后 .agent/sessions/ 会越积越多，
        # 只保留"在用"（当前会话/goal 未结束）或"近期"（keep_recent_days/
        # keep_recent_count 两道安全网）的 session，其余先确认知识已抽取
        # （没抽取过的先补跑一次抽取）再删除。与 sys:digest_trim 同属低频
        # 清理档（7 天一次）。cron 场景下默认带 --extract-first（用户已确认），
        # 保证被删除前的知识不会凭空丢失；如需更保守，可把这条 job 的
        # task_template 里的 --extract-first 去掉，或直接 disable 这条
        # job 改成只手动执行 `/session cleanup --dry-run` 观察。
        "id": "sys:session_cleanup",
        "name": "Session 清理",
        "schedule": "interval:604800",
        "description": "清理长期不用的旧 session：保留在用/最近的，其余先补抽取知识再删除（每 7 天）",
        "task_template": "[系统维护] 执行一次 /session cleanup --extract-first",
        "tags": ["maintenance", "sessions"],
        "enabled": True,
    },
    {
        "id": "sys:self_maintain",
        "name": "自维护健康检查",
        "schedule": "interval:86400",
        "description": "检查可能失效的工具、过时 skill、矛盾的 lesson（每 24 小时）",
        "task_template": "[系统维护] 执行自维护健康检查：扫描近期工具调用失败率、长期未使用的 skill、记忆库中可能矛盾的经验，生成修复建议",
        "tags": ["maintenance", "self_awareness"],
        "enabled": True,
    },
    {
        "id": "sys:daily_digest",
        "name": "每日融合日报",
        "schedule": "cron:0 22 * * *",
        "description": "合并当天行为分布、目标进展、代码提交，生成融合日报（每天 22:00）",
        "task_template": "[日报] 执行一次 /digest daily，生成当天融合日报并落盘",
        "tags": ["digest", "behavior", "goals"],
        "enabled": True,
    },
    {
        "id": "sys:next_action_digest",
        "name": "主动推荐排序",
        "schedule": "interval:10800",
        "description": "对停滞目标/注意力错配候选排序生成推荐（每 3 小时，候选为空则不生成）",
        "task_template": "[推荐] 执行一次 /next refresh，重新计算候选并排序，如果没有候选则跳过",
        "tags": ["advisor", "goals"],
        "enabled": True,
    },
    {
        "id": "sys:decision_profile_update",
        "name": "决策画像归纳",
        "schedule": "interval:604800",
        "description": "从历史决策记录归纳可追溯的用户价值模式（每 7 天，证据不足 3 条的模式不落地）",
        "task_template": "[画像] 执行一次 /decision_profile update，归纳决策画像并落盘",
        "tags": ["profile", "wiki"],
        "enabled": False,
    },
    {
        # [next_doc/growth_advisor_design.md] 成长顾问：信号扫描 + 候选生成 +
        # Top-N 调研报告。默认 enabled=True 与 GrowthAdvisorConfig.enabled
        # 的默认值保持一致（opt-out），实际是否运行由
        # GrowthAdvisorConfig.generation_frequency 决定档位、
        # 由 growth_advisor.run_daily_cycle 内部的 cfg.enabled 兜底跳过。
        "id": "sys:growth_advisor_daily",
        "name": "成长顾问：候选扫描",
        "schedule": "cron:30 22 * * *",
        "description": "扫描近期记忆信号，生成/更新成长方向候选，并为置信度最高的候选生成调研报告（每天 22:30，弱信号不生成候选）",
        "task_template": "[成长顾问] 执行一次 /growth scan，扫描信号、更新候选队列，并为符合条件的候选生成调研报告",
        "tags": ["growth_advisor", "wiki"],
        "enabled": True,
    },
    {
        "id": "sys:growth_monthly_retrospective",
        "name": "成长顾问：月度复盘",
        "schedule": "interval:2592000",
        "description": "统计成长候选的生成/采纳/忽略情况，生成一份月度复盘摘要（每 30 天）",
        "task_template": "[成长顾问] 执行一次 /growth retrospective，生成月度成长复盘摘要",
        "tags": ["growth_advisor", "digest"],
        "enabled": True,
    },
]


# ── 轻量 Cron 解析器 ──────────────────────────────────────────────────────────

def _next_interval(last_run_at: float, interval_seconds: float) -> float:
    """interval 模式：下次触发时间（距上次运行 interval 秒后）。"""
    if last_run_at <= 0:
        return time.time()  # 从未运行过，立即可以跑
    return last_run_at + interval_seconds


def _cron_field_match(value: int, expr: str) -> bool:
    """
    判断单个 cron 字段是否匹配。
    支持：* / */n / n / n,m / n-m
    """
    if expr == "*":
        return True
    if expr.startswith("*/"):
        try:
            step = int(expr[2:])
            return value % step == 0
        except ValueError:
            return False
    if "," in expr:
        return any(_cron_field_match(value, part.strip()) for part in expr.split(","))
    if "-" in expr:
        parts = expr.split("-", 1)
        try:
            lo, hi = int(parts[0]), int(parts[1])
            return lo <= value <= hi
        except ValueError:
            return False
    try:
        return value == int(expr)
    except ValueError:
        return False


def _next_cron(expr: str, after: Optional[float] = None) -> float:
    """
    计算 cron 表达式的下次触发时间（Unix timestamp）。
    expr 格式：分 时 日 月 周（5 字段）
    最大向前搜索 1 年（8760 次分钟迭代），找不到则返回 now + 365d。
    """
    import time as _time
    parts = expr.strip().split()
    if len(parts) != 5:
        # 格式错误，退化为每小时
        return (_time.time() if after is None else after) + 3600

    m_expr, h_expr, dom_expr, mon_expr, dow_expr = parts
    start = math.ceil((after if after is not None else _time.time()) / 60) * 60 + 60
    # 从下一分钟开始搜索
    import calendar
    for _ in range(525600):  # 最多搜 1 年（分钟）
        t = start
        tm = _time.localtime(t)
        if (
            _cron_field_match(tm.tm_min, m_expr)
            and _cron_field_match(tm.tm_hour, h_expr)
            and _cron_field_match(tm.tm_mday, dom_expr)
            and _cron_field_match(tm.tm_mon, mon_expr)
            and _cron_field_match(tm.tm_wday, dow_expr)
        ):
            return float(t)
        start += 60

    return time.time() + 365 * 86400


_CRON_FIELD_RE = re.compile(r"^(\*|\d+)(/\d+)?(-\d+)?(,(\*|\d+)(/\d+)?(-\d+)?)*$")


def validate_schedule(schedule: str) -> Optional[str]:
    """校验 schedule 字符串的格式合法性，不合法时返回错误提示（字符串），
    合法时返回 None。

    只做格式层面的校验（不校验语义上的取值范围，比如 `cron:99 * * * *`
    这种"分钟字段写了 99"这里不拦，`_cron_field_match()` 运行时会自然
    匹配不到、退化成\"这个字段永远不命中\"，不会崩溃，只是这个 job 实际上
    跑不起来——留给用户在看板上观察 next_run_at 是否合理）。

    用于看板\"新建 cron job\"表单在提交前做前置校验，避免用户输入
    `interval:abc` 这类明显错误的格式后，后端只能默认退化成\"1 小时后\"
    而用户毫无察觉。CronScheduler.compute_next_run() 本身对不合法输入仍然
    保留静默退化的兜底行为（向后兼容，不因为校验函数的存在就改变运行时
    容错策略）。
    """
    schedule = (schedule or "").strip()
    if not schedule:
        return "schedule 不能为空"
    if schedule.startswith("interval:"):
        raw = schedule[len("interval:"):].strip()
        try:
            sec = float(raw)
        except ValueError:
            return "interval 格式应为 interval:<秒数>，例如 interval:3600"
        if sec <= 0:
            return "interval 的秒数必须大于 0"
        return None
    if schedule.startswith("cron:"):
        expr = schedule[len("cron:"):].strip()
        parts = expr.split()
        if len(parts) != 5:
            return "cron 表达式需要 5 个字段（分 时 日 月 周），例如 cron:0 22 * * *"
        for field in parts:
            if not _CRON_FIELD_RE.match(field):
                return f"cron 字段格式不合法：{field!r}（支持 * / */n / n / n,m / n-m）"
        return None
    return "schedule 必须以 interval: 或 cron: 开头"


def compute_next_run(schedule: str, last_run_at: float = 0.0) -> float:
    """
    根据 schedule 字符串计算下次运行时间。

    格式：
      "interval:<seconds>"   → 固定间隔
      "cron:<5-field-expr>"  → cron 表达式
    """
    schedule = schedule.strip()
    if schedule.startswith("interval:"):
        try:
            sec = float(schedule[9:])
        except ValueError:
            sec = 3600.0
        return _next_interval(last_run_at, sec)
    elif schedule.startswith("cron:"):
        expr = schedule[5:]
        return _next_cron(expr, after=time.time())
    else:
        # 未知格式，1 小时后
        return time.time() + 3600


# ── CronScheduler 主类 ────────────────────────────────────────────────────────

class CronScheduler:
    """
    Daemon 模式的定时任务调度器。

    - 存储：<workdir>/.agent/cron_jobs.json
    - tick() 由 AutonomousLoop._tick_passive() 调用
    - 触发的 Job 通过回调函数 _submit_fn 提交（注入 InputQueue.enqueue）
    """

    VERSION = 1

    def __init__(
        self,
        paths: "AgentPaths",
        submit_fn: Optional[Callable[[str, str, dict], bool]] = None,
        digest_advisor_cfg: Optional["DigestAdvisorConfig"] = None,
        job_runner: Optional["CronJobRunner"] = None,
    ) -> None:
        """
        paths       — AgentPaths，用于定位 cron_jobs.json
        submit_fn   — [旧路径，向后兼容] 触发 job 时的提交回调：
                      submit_fn(message, initiator, meta) -> bool，
                      通常注入 InputQueue.enqueue 的包装。job_runner 未注入时
                      走这条老路（消息排队给主 Agent，与用户消息共用同一条
                      InputQueue/AgentRunner 主线程）。
        digest_advisor_cfg — 日报/推荐/画像三层功能的配置（config/models.py::
                      DigestAdvisorConfig）。只在 sys:daily_digest /
                      sys:next_action_digest / sys:decision_profile_update
                      三个内置 job **首次注入**时用来决定初始 enabled 状态，
                      不影响用户后续通过 /cron enable|disable 做的手动修改
                      （见 load() 里"已存在的不覆盖"注释）。为 None 时使用
                      _BUILTIN_JOBS 里写死的默认值，保持向后兼容。
        job_runner  — [新路径] evolution.cron_job_runner.CronJobRunner 实例。
                      注入后，_fire() 会优先走这条独立执行通道：cron job 在
                      专属后台线程里跑（不占用 AgentRunner 主线程），带超时/
                      步数上限/卡死检测/跨次进度恢复（见
                      cron_job_executor.py / cron_job_workspace.py）。
                      为 None 时完全保持旧行为，不影响未升级的部署。
        """
        self._paths = paths
        self._submit_fn = submit_fn
        self._digest_advisor_cfg = digest_advisor_cfg
        self._job_runner = job_runner
        self._jobs: dict[str, CronJob] = {}
        self._jobs_path = paths.workdir_dir / "cron_jobs.json"
        # [watchlist_notification_goal_design.md §10.1] 本地回调 handler
        # 注册表：job_id -> Callable[[CronJob], bool]。`_fire()` 会优先
        # 检查这里，命中则在本进程内直接执行、不经过 InputQueue/job_runner，
        # 因此不产生 LLM 调用。用于"确定性、不需要 LLM 参与"的内置 job
        # （比如 sys:watchlist_report_<tier>），不影响既有 submit_fn/job_runner
        # 两条路径的行为。
        self._local_handlers: dict[str, Callable[["CronJob"], bool]] = {}
        # [goal_cron_binding_plan.md Track B] 单个通用回调，处理所有
        # run_mode="goal_cycle" 的 job（不像 _local_handlers 那样按固定 job_id
        # 逐个注册——goal_cycle job 的 id 是用户动态创建的 user:xxxx，数量不固定，
        # 用一个按 run_mode 分派的通用回调更合适）。为 None 时 goal_cycle job
        # 不会被触发（_fire 直接返回 False，等同于"这个功能还没接线"）。
        self._goal_cycle_fn: Optional[Callable[["CronJob"], bool]] = None

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def load(self) -> None:
        """加载 cron_jobs.json，首次加载时注入内置 Job。"""
        existing: dict[str, CronJob] = {}
        if self._jobs_path.exists():
            try:
                data = json.loads(self._jobs_path.read_text(encoding="utf-8"))
                for jd in data.get("jobs", []):
                    j = CronJob.from_dict(jd)
                    if j.id:
                        existing[j.id] = j
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.cron_scheduler')
                pass

        # 重命名兼容：sys:phase_g 是本 job 重命名为 sys:consolidation 之前的旧 id。
        # 存量 cron_jobs.json 里可能还留着这个 id——如果不处理，下面的"注入内置 Job"
        # 会因为找不到 sys:consolidation 而新增一份，导致旧 job 和新 job 同时存在、
        # 重复触发。这里原地改名，保留用户对旧 job 的 enabled/schedule 自定义。
        _legacy_id = "sys:phase_g"
        if _legacy_id in existing and "sys:consolidation" not in existing:
            legacy_job = existing.pop(_legacy_id)
            legacy_job.id = "sys:consolidation"
            existing["sys:consolidation"] = legacy_job
        elif _legacy_id in existing:
            # 两者都存在（比如迁移逻辑上线前用户已经手动加过同名 job）：
            # 保留新 id 的记录，丢弃旧 id，避免重复触发。
            existing.pop(_legacy_id, None)

        # 注入内置 Job（已存在的不覆盖，保留用户修改的 enabled/schedule）
        _cfg_enabled_overrides = {
            "sys:daily_digest": (
                self._digest_advisor_cfg.daily_digest_enabled
                if self._digest_advisor_cfg is not None else None
            ),
            "sys:next_action_digest": (
                self._digest_advisor_cfg.next_action_enabled
                if self._digest_advisor_cfg is not None else None
            ),
            "sys:decision_profile_update": (
                self._digest_advisor_cfg.decision_profile_enabled
                if self._digest_advisor_cfg is not None else None
            ),
        }
        for bd in _BUILTIN_JOBS:
            bid = bd["id"]
            if bid not in existing:
                j = CronJob.from_dict(bd)
                override = _cfg_enabled_overrides.get(bid)
                if override is not None:
                    j.enabled = bool(override)
                # [P2] 内置系统维护 job 默认给一个中等优先级（5），高于普通
                # 用户自定义 message 类 job 的默认值 0，低于 goal_cycle job
                # 的默认值 10（见 add_job()）——同一个 tick 内多个 job 同时
                # 到期时，goal 驱动的任务优先，其次是系统维护，普通用户
                # 一次性任务最后。_BUILTIN_JOBS 字典本身不带 priority 字段，
                # 这里统一补上，不逐条改 _BUILTIN_JOBS 定义。
                j.priority = 5
                j.next_run_at = compute_next_run(j.schedule, 0.0)
                existing[bid] = j

        self._jobs = existing

    def save(self) -> None:
        """原子写入 cron_jobs.json。"""
        data = {
            "version": self.VERSION,
            "jobs": [j.to_dict() for j in self._jobs.values()],
        }
        atomic_write_json(self._jobs_path, data)

    # ── 主调度入口 ────────────────────────────────────────────────────────────

    def tick(self) -> list[str]:
        """
        检查所有 enabled Job 是否到期，触发到期 Job。
        返回本次触发的 job_id 列表。
        由 AutonomousLoop._tick_passive() 调用。
        """
        now = time.time()
        triggered: list[str] = []
        skip_state_changed = False

        # [P2] 先收集本轮到期的 job，再按 priority 降序排序后依次触发——
        # 之前是遍历 dict 插入顺序，sys: 内置 job 因为 load() 时先注入
        # 永远排最前，用户自定义 job 之间的先后也只取决于创建时间，没有
        # 语义。这里改成两阶段：第一阶段只负责"谁到期了/顺便重算未初始化
        # 的 next_run_at"，不在遍历中直接触发；第二阶段按优先级排序后
        # 触发。不做抢占——已经在 _fire() 里排队/执行的 job 不受影响，
        # 只影响"同一个 tick 内，多个 job 同时到期时先提交谁"。
        due_jobs: list["CronJob"] = []
        for job in list(self._jobs.values()):
            if not job.enabled:
                continue
            if job.next_run_at <= 0:
                # next_run_at 未初始化，重新计算
                job.next_run_at = compute_next_run(job.schedule, job.last_run_at)
                continue
            if now < job.next_run_at:
                continue
            due_jobs.append(job)

        due_jobs.sort(key=lambda j: j.priority, reverse=True)

        for job in due_jobs:
            # 到期，触发。[P5 第 4 步] 单个 job 的"触发 + 记账"逻辑抽成
            # `_trigger_and_record()`，供 `trigger_job_now()`（统一调度层
            # execute() 复用）与这里共用同一份实现，避免记账逻辑存在两份
            # 拷贝、日后改一处忘了改另一处。
            prev_skip_count = job.consecutive_skip_count
            triggered_ok = self._trigger_and_record(job, now)
            if triggered_ok:
                triggered.append(job.id)
            if job.consecutive_skip_count != prev_skip_count:
                skip_state_changed = True

        if triggered or skip_state_changed:
            try:
                self.save()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.cron_scheduler')
                pass

        return triggered

    def _trigger_and_record(self, job: "CronJob", now: float) -> bool:
        """触发单个 job 并更新其记账字段（`last_run_at`/`run_count`/
        `next_run_at`/`consecutive_skip_count`），不负责 `save()`——调用方
        （`tick()`/`trigger_job_now()`）各自决定何时落盘，避免这里重复
        写文件。返回值与 `_fire()` 含义一致：True 表示确实触发成功。"""
        success = self._fire(job)
        if success:
            job.last_run_at = now
            job.run_count += 1
            job.next_run_at = compute_next_run(job.schedule, now)
            if job.consecutive_skip_count:
                job.consecutive_skip_count = 0
        else:
            # [goal_cron_unified_scheduler_improvement_plan.md P2]
            # 到点但未能成功触发（仲裁 blocked / 已有一次执行在跑 /
            # semaphore 排队中拒绝等）：记一次连续跳过，跨越告警阈值
            # 时发一次通知，避免长期"静默重试"导致用户完全无感知。
            job.consecutive_skip_count += 1
            self._maybe_alert_consecutive_skip(job)
        return success

    def trigger_job_now(self, job_id: str) -> bool:
        """[goal_cron_unified_scheduler_improvement_plan.md P5 第 4 步]
        由外部（当前是 `UnifiedTaskScheduler` 的 `CronChannelAdapter`/
        `GoalCycleChannelAdapter.execute()`）直接触发某个 job 的公开入口，
        与 `tick()` 内部触发到期 job 走的是**同一份** `_trigger_and_record()`
        记账逻辑——不会出现"通过这个入口触发了，但 `next_run_at`/
        `consecutive_skip_count` 没更新，导致下一次 `tick()` 又把它当成
        到期任务重复触发"这种记账错位。

        与 `tick()` 的区别只在于"谁来决定现在该不该触发"：`tick()` 自己
        判断到期与否，本方法信任调用方已经做过判断（`SchedulableTask`
        本身就是从 `poll_due()` 里筛出来的到期任务），不重复检查
        `next_run_at`，也不要求 job 一定"到期"——调用方如果传一个未到期
        的 job_id，本方法仍会尝试触发（语义等价于"立即手动触发一次"）。

        `job_id` 不存在时返回 `False`（静默，不抛异常——与本类其它对外
        接口一贯的失败处理风格一致）。
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        now = time.time()
        success = self._trigger_and_record(job, now)
        try:
            self.save()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.cron_scheduler.CronScheduler.trigger_job_now')
        return success

    def set_goal_cycle_handler(self, handler: Optional[Callable[["CronJob"], bool]]) -> None:
        """[Track B] 注册/替换 goal_cycle 通用回调。daemon 启动时由
        goal_cron_bridge.register_goal_cycle_handler() 调用一次；传 None 可以
        显式关闭该功能（比如测试场景不想牵扯 ObjectiveExecutor）。
        """
        self._goal_cycle_fn = handler

    def _maybe_alert_consecutive_skip(self, job: CronJob) -> None:
        """[goal_cron_unified_scheduler_improvement_plan.md P2] 只在
        consecutive_skip_count 恰好跨越 `cron.skip_alert_threshold`（默认 5）
        那一刻发一次告警，不重复刷屏——与 `record_gating_transition()` 的
        "状态变化才写入"是同一节流思路：== threshold 而不是 >= threshold，
        保证每次连续跳过的"轮次"只在跨越阈值时触发一次；后续再连续跳过
        不会重复通知，直到某次成功触发清零、重新从零累积、再次跨越阈值。
        失败静默：告警本身失败不能影响 tick() 主流程。"""
        try:
            # CronScheduler 本身不持有 AppConfig（构造参数只有 paths/
            # submit_fn/digest_advisor_cfg/job_runner），复用 job_runner
            # 已经持有的 base_cfg（job_runner 未注入时退回默认阈值 5，
            # 不引入额外的 load_config() 调用）。
            base_cfg = getattr(self._job_runner, "_base_cfg", None) if self._job_runner is not None else None
            cron_cfg = getattr(base_cfg, "cron", None) if base_cfg is not None else None
            threshold = getattr(cron_cfg, "skip_alert_threshold", 5) if cron_cfg is not None else 5
            if threshold <= 0 or job.consecutive_skip_count != threshold:
                return
            from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
            NotificationDispatcher(self._paths).dispatch(NotificationMessage(
                title="cron job 长期未能触发",
                body=(
                    f"cron job {job.name!r}（{job.id}）已连续 {job.consecutive_skip_count} "
                    "次到点未能成功触发，可能是资源仲裁持续 blocked，或已有一次执行"
                    "长期未结束，建议检查"
                )[:200],
                source="cron_skip_alert",
                meta={"job_id": job.id, "consecutive_skip_count": job.consecutive_skip_count},
            ))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.evolution.cron_scheduler.CronScheduler._maybe_alert_consecutive_skip")

    def _fire(self, job: CronJob) -> bool:
        """
        触发一个 Job。

        job_runner 已注入时优先走独立执行通道（见 __init__ 里的说明）；
        job_runner.submit() 内部若发现该 job 已有一次执行在跑（避免同一个
        job 被并发触发两次）会返回 False——这种情况下 tick() 不应该更新
        last_run_at/next_run_at（视为"这次没真正触发成功"，下次 tick 会
        再次尝试，等上一次跑完自然空出来），行为与旧 submit_fn 返回 False
        时完全一致。

        job_runner 未注入时回退到旧的 submit_fn 路径（消息进 InputQueue，
        由 AgentRunner 主线程当作一次普通 turn 处理）。
        """
        if job.run_mode == "goal_cycle":
            # goal_cycle job 永远走这一条独立分支，不参与 local_handler/
            # job_runner/submit_fn 的既有优先级链——那三条路径都是"把
            # task_template 当一条完整指令处理"，语义上不适用于"确保某个
            # Goal 下有一轮 Objective 在推进"这件事。_goal_cycle_fn 未注册时
            # （比如非 daemon 场景、或功能尚未接线）直接返回 False，等同于
            # "这次没触发成功"，tick() 不会推进 last_run_at，下次再试。
            if self._goal_cycle_fn is None:
                return False
            try:
                return self._goal_cycle_fn(job)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.cron_scheduler.CronScheduler._fire.goal_cycle')
                return False

        local_handler = self._local_handlers.get(job.id)
        if local_handler is not None:
            try:
                return local_handler(job)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.cron_scheduler.CronScheduler._fire.local_handler')
                return False

        if self._job_runner is not None:
            try:
                return self._job_runner.submit(job)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.cron_scheduler.CronScheduler._fire.job_runner')
                return False

        if self._submit_fn is None:
            return False
        try:
            message = job.task_template
            # [goal_cron_feedback_and_output_policy_plan.md 5.3] run_mode="message"
            # 裸投递路径也统一注入产出路径规范，保持三条执行路径（dedicated cron /
            # goal_cycle cron / 普通 objective）行为一致。
            try:
                from mini_agent.evolution.output_path_policy import load_policy
                policy_text = load_policy(self._paths)
                if policy_text:
                    message = f"{message}\n\n[产出路径规范]\n{policy_text}"
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(
                    _mini_agent_exc,
                    where="mini_agent.evolution.cron_scheduler.CronScheduler._fire.output_policy",
                )
            return self._submit_fn(
                message,
                job.initiator,
                {"cron_job_id": job.id, "cron_job_name": job.name},
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.cron_scheduler.CronScheduler._fire')
            return False

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add_job(
        self,
        name: str,
        schedule: str,
        task_template: str,
        description: str = "",
        tags: Optional[list[str]] = None,
        enabled: bool = True,
        goal_id: Optional[str] = None,
        run_mode: str = "message",
        priority: Optional[int] = None,
    ) -> CronJob:
        """添加用户自定义 Job。

        goal_id/run_mode — [goal_cron_binding_plan.md Track A 新增] 供
        goal_cron_bridge.make_goal_recurring() 创建 run_mode="goal_cycle" 的
        绑定 job；其余既有调用方不传时保持 run_mode="message"，行为不变。

        priority — [P2 新增] 不传时按 run_mode 给默认值：goal_cycle 默认
        10（goal 驱动的任务通常是用户主动绑定的持续性目标，优先级应高于
        内置系统维护 job 的默认 5），普通 message 类一次性/周期任务默认 0
        （低于系统维护 job，避免大量用户自定义 job 挤占 sys: job 的执行
        时机）。显式传入时以传入值为准。
        """
        job_id = f"user:{uuid.uuid4().hex[:8]}"
        if priority is None:
            priority = 10 if run_mode == "goal_cycle" else 0
        job = CronJob(
            id=job_id,
            name=name,
            schedule=schedule,
            task_template=task_template,
            description=description,
            tags=tags or ["user"],
            enabled=enabled,
            initiator="cron",
            next_run_at=compute_next_run(schedule, 0.0),
            goal_id=goal_id,
            run_mode=run_mode,
            priority=priority,
        )
        self._jobs[job_id] = job
        self.save()
        return job

    def register_local_handler(self, job_id: str, handler: Callable[["CronJob"], bool]) -> None:
        """注册一个"零 LLM 成本"的本地回调，供 `_fire()` 优先使用（见
        watchlist_notification_goal_design.md §10.1）。可重复调用覆盖同一
        job_id 的 handler（daemon 每次启动都会重新注册一遍，属预期行为）。
        """
        self._local_handlers[job_id] = handler

    def ensure_job(
        self,
        job_id: str,
        name: str,
        schedule: str,
        description: str = "",
        tags: Optional[list[str]] = None,
        task_template: str = "",
        initiator: str = "cron",
    ) -> CronJob:
        """"缺失才补"注册路径（区别于 `add_job` 的"用户手动加 job"）：
        job_id 已存在时直接返回现有 job（不覆盖用户可能已经手动改过的
        schedule/enabled），不存在时创建一个新的、默认 enabled=True 的
        job 并落盘。专门给"配置文件驱动的内置 job"用，比如
        `sys:watchlist_report_<tier_id>`——job 本身的存储结构/治理规则
        （sys: 前缀不可删除只可 disable）与既有内置 job 完全一致。
        """
        existing = self._jobs.get(job_id)
        if existing is not None:
            return existing
        job = CronJob(
            id=job_id,
            name=name,
            schedule=schedule,
            task_template=task_template,
            description=description,
            tags=tags or [],
            enabled=True,
            initiator=initiator,
            next_run_at=compute_next_run(schedule, 0.0),
        )
        self._jobs[job_id] = job
        self.save()
        return job

    def remove_job(self, job_id: str) -> bool:
        """删除 Job（sys: 前缀不可删除，只可 disable）。"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.is_system:
            return False  # 系统 job 不可删
        del self._jobs[job_id]
        self.save()
        return True

    def enable(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.enabled = True
        # 重新计算下次运行时间
        job.next_run_at = compute_next_run(job.schedule, job.last_run_at)
        self.save()
        return True

    def disable(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.enabled = False
        self.save()
        return True

    def add_user_feedback(self, job_id: str, text: str, *, _sync: bool = True) -> bool:
        """[goal_cron_feedback_and_output_policy_plan.md Track B] 用户对某个
        CronJob「提意见」，持久化写入三个地方：
          1. job.user_feedback —— 历史记录，供 UI/CLI 回看
          2. job.description   —— 人类可读展示
          3. job.task_template —— 真正决定 run_mode="message" 裸投递内容、以及
             _fire_goal_cycle() 兜底内容的字段，只改 description 不影响实际执行
        若该 job 是 dedicated-execution 模式（存在 prompt.md），同步追加进
        prompt.md（render_prompt() 读的是它，不是 task_template）。

        _sync=True 时，如果 job.run_mode == "goal_cycle" 且 goal_id 有效，
        额外把同一条意见同步到绑定的 Goal（GoalBacklog.add_user_feedback()）；
        联动调用时对方传 _sync=False，防止两边互相调用形成死循环。
        """
        if not text or not text.strip():
            return False
        text = text.strip()
        job = self._jobs.get(job_id)
        if not job:
            return False
        from mini_agent.time_utils import ts_to_str
        job.user_feedback.append({"text": text, "at": time.time()})
        stamp = f"[用户意见 {ts_to_str(time.time())}] {text}"
        job.description = f"{job.description}\n\n{stamp}" if job.description else stamp
        job.task_template = (
            f"{job.task_template}\n\n{stamp}" if job.task_template else stamp
        )
        self.save()

        try:
            from mini_agent.evolution.cron_job_workspace import CronJobWorkspace
            workspace = CronJobWorkspace(self._paths, job_id)
            if workspace.prompt_path.exists():
                workspace.append_user_feedback(text)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(
                _mini_agent_exc,
                where="mini_agent.evolution.cron_scheduler.CronScheduler.add_user_feedback",
            )

        if _sync and job.run_mode == "goal_cycle" and job.goal_id:
            try:
                from mini_agent.perception.goal_backlog import GoalBacklog
                backlog = GoalBacklog(self._paths)
                backlog.add_user_feedback(job.goal_id, text, _sync=False)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(
                    _mini_agent_exc,
                    where="mini_agent.evolution.cron_scheduler.CronScheduler.add_user_feedback",
                )
        return True

    def run_now(self, job_id: str) -> bool:
        """立即触发一次（不修改 next_run_at）。"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        success = self._fire(job)
        if success:
            job.last_run_at = time.time()
            job.run_count += 1
            self.save()
        return success

    def update_schedule(self, job_id: str, schedule: str) -> bool:
        """更新 job 的 schedule 并重新计算 next_run_at。"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.schedule = schedule
        job.next_run_at = compute_next_run(schedule, job.last_run_at)
        self.save()
        return True

    def update_priority(self, job_id: str, priority: int) -> bool:
        """[P2] 更新 job 的 priority（不影响 next_run_at/enabled 等其它字段），
        供 kanban 的编辑入口和 PUT /cron/jobs/{job_id} 使用。"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.priority = int(priority)
        self.save()
        return True

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def is_job_running(self, job_id: str) -> bool:
        """job_runner 未注入（旧路径）时始终返回 False——旧路径的执行状态
        并入普通 turn，没有独立的"是否在跑"概念可查。"""
        if self._job_runner is None:
            return False
        return self._job_runner.is_running(job_id)

    def reap_stale_jobs(self) -> list[str]:
        """[daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段一]
        委托给 job_runner.reap_stale_jobs()：回收卡死超过有效超时阈值的
        cron job，代替永远不会执行到的 finally 释放并发许可、清空记账，
        使其可以被下一次到期重新 submit()。job_runner 未注入（旧路径）时
        始终返回空列表——旧路径的 cron job 直接跑在 AgentRunner 主线程上，
        没有独立的"卡死回收"概念。由 AutonomousLoop._tick_maintenance()
        每次 tick 调用，返回值仅供上层日志/计数，不影响本方法本身的
        幂等性——重复调用是安全的。"""
        if self._job_runner is None:
            return []
        return self._job_runner.reap_stale_jobs()

    def set_gating_degraded(self, degraded: bool) -> None:
        """[goal_cron_unified_scheduler_improvement_plan.md P0] 委托给
        job_runner.set_gating_degraded()：反映 ResourceArbiter.gating_state()
        的最新结果，供 CronJobRunner 收紧/恢复普通 cron 通道的并发上限。
        job_runner 未注入（旧路径）时静默跳过——旧路径的 cron job 直接跑
        在 AgentRunner 主线程上，没有独立的并发概念可收紧。"""
        if self._job_runner is None:
            return
        self._job_runner.set_gating_degraded(degraded)

    def execution_phase(self, job_id: str) -> str:
        """[next_doc/kanban_execution_visibility_and_control_plan.md
        阶段 B] 委托给 job_runner.execution_phase()：区分 job 当前是
        "not_running"/"queued"/"running"。job_runner 未注入（旧路径）
        时始终返回 "not_running"，与 is_job_running() 的降级方式一致。"""
        if self._job_runner is None:
            return "not_running"
        return self._job_runner.execution_phase(job_id)

    def get(self, job_id: str) -> Optional[CronJob]:
        return self._jobs.get(job_id)

    def list_jobs(
        self,
        tags: Optional[list[str]] = None,
        enabled_only: bool = False,
    ) -> list[CronJob]:
        """列出 Job，按 next_run_at 升序排序。"""
        jobs = list(self._jobs.values())
        if enabled_only:
            jobs = [j for j in jobs if j.enabled]
        if tags:
            tag_set = set(tags)
            jobs = [j for j in jobs if set(j.tags) & tag_set]
        return sorted(jobs, key=lambda j: j.next_run_at)

    def next_run_summary(self) -> str:
        """简洁的下次运行总览（供 /cron status 和 daemon status 使用）。"""
        jobs = self.list_jobs(enabled_only=True)
        if not jobs:
            return "无启用的 cron job"
        lines = []
        for j in jobs[:8]:
            lines.append(f"  {j.id:<24}  {j.name:<18}  {j.next_run_str()}")
        return "\n".join(lines)


# ── 便捷函数 ──────────────────────────────────────────────────────────────────

def load_cron_scheduler(
    paths: "AgentPaths",
    submit_fn: Optional[Callable] = None,
    digest_advisor_cfg: Optional["DigestAdvisorConfig"] = None,
    job_runner: Optional["CronJobRunner"] = None,
) -> CronScheduler:
    """加载并返回 CronScheduler（便捷函数）。digest_advisor_cfg 透传给
    CronScheduler，用于内置日报/推荐/画像三个 job 首次注入时的默认 enabled。
    job_runner 透传，注入后 cron job 执行会走独立后台线程通道（见
    CronScheduler.__init__ / _fire() 的说明），为 None 时保持旧行为。"""
    cs = CronScheduler(paths, submit_fn=submit_fn, digest_advisor_cfg=digest_advisor_cfg,
                        job_runner=job_runner)
    cs.load()
    return cs


__all__ = [
    "CronJob",
    "CronScheduler",
    "compute_next_run",
    "validate_schedule",
    "load_cron_scheduler",
]
