"""
evolution/cron_job_workspace.py — 每个 cron job 的专属文件夹管理

目录结构（<project_root>/.agent/cron_jobs/<job_id>/）：
    prompt.md      用户可编辑的任务 prompt。支持 {{progress}} 占位符，
                   触发执行时会被替换为上次遗留的进度摘要（首次为空字符串）。
    config.json    该 job 的专属限制覆盖（超时秒数/最大步数等）。
                   缺省时回退到全局默认值，用户按需创建/编辑此文件。
    state.json     跨次启动持久化的执行状态机（见 CronJobState）。
    runs/<ts>.jsonl  每次执行的逐步事件流，供看板回放/诊断。

看板（Streamlit）可以直接扫 .agent/cron_jobs/*/state.json 渲染每个 job
当前状态，不需要额外的 REST 层——本模块只负责读写这些文件，不做任何
调度/执行逻辑（那部分见 cron_job_executor.py）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


# ── 状态机 ────────────────────────────────────────────────────────────────────

# idle              从未运行过 / 上次正常结束
# running           当前正在执行（用于检测"上次异常退出、state 还留在 running"的僵尸状态）
# needs_human_review  StuckDetector 判定 GIVE_UP，或连续失败次数超阈值
# timed_out         上次因触达硬超时被收尾（不算失败，下次会带着进度继续）
STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_NEEDS_REVIEW = "needs_human_review"
STATUS_TIMED_OUT = "timed_out"

DEFAULT_TIMEOUT_SECONDS = 20 * 60
DEFAULT_MAX_STEPS = 60


@dataclass
class CronJobConfig:
    """单个 job 的专属限制覆盖（config.json），缺省字段回退全局默认。"""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_steps: int = DEFAULT_MAX_STEPS
    stuck_similarity_threshold: float = 0.92
    stuck_consecutive_limit: int = 3
    stuck_max_recoveries: int = 2

    @staticmethod
    def from_dict(d: dict, default: Optional["CronJobConfig"] = None) -> "CronJobConfig":
        """从 config.json 的原始 dict 构造。

        default —— 字段缺省时的回退来源。不传则回退到硬编码的
        CronJobConfig() 默认值（旧行为）；传入时（通常是根据全局
        AppConfig.cron 构造的实例）用于实现"config.json 里没写的字段，
        跟随全局配置实时生效"，而不需要对已存在的 job 做一次性迁移——
        每次 read_config() 都会重新按这个规则合并。
        """
        base = CronJobConfig() if default is None else CronJobConfig(**asdict(default))
        for k in ("timeout_seconds", "max_steps", "stuck_similarity_threshold",
                  "stuck_consecutive_limit", "stuck_max_recoveries"):
            if k in d:
                setattr(base, k, d[k])
        return base

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CronJobState:
    """跨次启动持久化的执行状态（state.json）。"""
    status: str = STATUS_IDLE
    progress_summary: str = ""       # 拼进下次 prompt 的 {{progress}} 占位符
    last_step_index: int = 0         # 上次收尾时执行到第几步（仅供诊断展示）
    consecutive_failures: int = 0    # 连续 needs_human_review/异常退出次数
    last_run_started_at: float = 0.0
    last_run_finished_at: float = 0.0
    last_run_id: str = ""            # 对应 runs/<run_id>.jsonl
    last_error: str = ""

    @staticmethod
    def from_dict(d: dict) -> "CronJobState":
        base = CronJobState()
        for k in base.__dataclass_fields__:
            if k in d:
                setattr(base, k, d[k])
        return base

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_PROMPT_TEMPLATE = (
    "{{task_description}}\n\n"
    "{{#progress}}\n"
    "--- 上次执行遗留的进度 ---\n"
    "{{progress}}\n"
    "请从上述进度继续，不要从头重新开始。\n"
    "{{/progress}}\n"
    "\n"
    "{{output_policy}}\n"
)


class CronJobWorkspace:
    """单个 cron job 的文件夹句柄。所有读写都通过这个类完成。"""

    def __init__(self, paths: "AgentPaths", job_id: str):
        self._paths = paths
        self.job_id = job_id
        # job_id 里可能含 ':' (如 "sys:consolidation" / "user:ab12cd34")，
        # 文件系统里用 '_' 替换避免部分平台不支持冒号做文件名。
        safe_id = job_id.replace(":", "_")
        self.dir = Path(paths.project_root) / ".agent" / "cron_jobs" / safe_id
        self.prompt_path = self.dir / "prompt.md"
        self.config_path = self.dir / "config.json"
        self.state_path = self.dir / "state.json"
        self.runs_dir = self.dir / "runs"

    # ── 初始化 / 幂等 ensure ──────────────────────────────────────────────

    def ensure(self, default_task_template: str = "", default_config: Optional[CronJobConfig] = None) -> None:
        """确保文件夹和默认文件存在；已存在的文件不覆盖（用户可能已编辑过）。

        default_config — 新建 config.json 时使用的默认值来源，通常由调用方
        传入根据 AppConfig.cron（全局配置，见 config/models.py::CronConfig）
        构造的 CronJobConfig；不传时退回 CronJobConfig() 的硬编码默认值。
        只影响**首次创建**该 job 文件夹时写入的 config.json 内容，job 自己
        已经存在的 config.json 不会被这里覆盖。
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        if not self.prompt_path.exists():
            content = default_task_template or "{{task_description}}\n\n{{progress}}\n"
            self.prompt_path.write_text(content, encoding="utf-8")
        if not self.config_path.exists():
            atomic_write_json(self.config_path, (default_config or CronJobConfig()).to_dict())
        if not self.state_path.exists():
            atomic_write_json(self.state_path, CronJobState().to_dict())

    # ── 读取 ──────────────────────────────────────────────────────────────

    def read_prompt(self) -> str:
        try:
            return self.prompt_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def read_config(self, default: Optional[CronJobConfig] = None) -> CronJobConfig:
        """读取 config.json；default 缺省时的合并来源见 CronJobConfig.from_dict()。

        JSON 文件本身缺失/损坏时也回退到 default（不传则用硬编码默认值）。
        """
        try:
            d = json.loads(self.config_path.read_text(encoding="utf-8"))
            return CronJobConfig.from_dict(d, default=default)
        except (OSError, json.JSONDecodeError):
            return default or CronJobConfig()

    def read_state(self) -> CronJobState:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return CronJobState.from_dict(d)
        except (OSError, json.JSONDecodeError):
            return CronJobState()

    # ── 写入 ──────────────────────────────────────────────────────────────

    def write_state(self, state: CronJobState) -> None:
        atomic_write_json(self.state_path, state.to_dict())

    def render_prompt(self, task_description: str) -> str:
        """把 prompt.md 模板渲染成最终发给 agent 的文本。

        支持三个占位符：
          {{task_description}} — cron_jobs.json 里配置的 task_template
          {{progress}}         — 上次执行遗留的 progress_summary（可能为空）
          {{output_policy}}    — [goal_cron_feedback_and_output_policy_plan.md
                                  5.3] 产出路径规范全文（见 output_path_policy.py）。
                                  已存在的、用户自定义的 prompt.md 如果没有这个
                                  占位符则不受影响（不强行插入）——用户如果想要
                                  这段规范，自己在 prompt.md 里加上即可，等价于
                                  用户主动选择不需要。
        以及一个极简的 {{#progress}}...{{/progress}} 条件块：
        progress 为空时整段连同标记一起去掉，避免每次都印出一段空的
        "上次进度"标题。
        """
        template = self.read_prompt()
        state = self.read_state()
        progress = state.progress_summary.strip()

        if "{{#progress}}" in template and "{{/progress}}" in template:
            start = template.index("{{#progress}}")
            end = template.index("{{/progress}}") + len("{{/progress}}")
            block = template[start:end]
            if progress:
                inner = block[len("{{#progress}}"):-len("{{/progress}}")]
                template = template[:start] + inner + template[end:]
            else:
                template = template[:start] + template[end:]

        template = template.replace("{{task_description}}", task_description)
        template = template.replace("{{progress}}", progress)
        if "{{output_policy}}" in template:
            try:
                from mini_agent.evolution.output_path_policy import load_policy
                policy_text = load_policy(self._paths)
            except Exception:
                policy_text = ""
            template = template.replace("{{output_policy}}", policy_text)
        return template

    # ── 用户意见反馈 ──────────────────────────────────────────────────────

    def append_user_feedback(self, text: str) -> None:
        """[goal_cron_feedback_and_output_policy_plan.md 3.2] dedicated-execution
        模式下 render_prompt() 读的是 prompt.md 模板，不是 task_template，
        所以 CronScheduler.add_user_feedback() 同步写意见时也要喂到这里。
        直接在文件末尾追加一段，不做模板占位符解析，避免破坏用户已有的
        自定义模板结构。
        """
        if not text or not text.strip():
            return
        from mini_agent.time_utils import ts_to_str
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = f"\n\n[用户意见 {ts_to_str(time.time())}] {text.strip()}\n"
        if not self.prompt_path.exists():
            self.prompt_path.write_text(DEFAULT_PROMPT_TEMPLATE, encoding="utf-8")
        with open(self.prompt_path, "a", encoding="utf-8") as f:
            f.write(stamp)

    # ── 执行记录 ──────────────────────────────────────────────────────────

    def new_run_id(self) -> str:
        from mini_agent.time_utils import ts_to_str
        return ts_to_str(time.time()).replace(":", "-").replace(" ", "T")

    def run_log_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.jsonl"

    def append_run_event(self, run_id: str, event: dict) -> None:
        path = self.run_log_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": time.time(), **event}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    def recent_runs(self, limit: int = 10) -> list[str]:
        """按时间倒序返回最近 N 次 run_id（供看板列出历史执行记录）。"""
        if not self.runs_dir.exists():
            return []
        files = sorted(self.runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.stem for p in files[:limit]]

    def read_run_events(self, run_id: str) -> list[dict]:
        path = self.run_log_path(run_id)
        if not path.exists():
            return []
        events = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events


def list_all_workspaces(paths: "AgentPaths") -> list[str]:
    """列出 .agent/cron_jobs/ 下所有已存在的 job 文件夹名（供看板枚举）。"""
    root = Path(paths.project_root) / ".agent" / "cron_jobs"
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


__all__ = [
    "CronJobConfig",
    "CronJobState",
    "CronJobWorkspace",
    "list_all_workspaces",
    "STATUS_IDLE",
    "STATUS_RUNNING",
    "STATUS_NEEDS_REVIEW",
    "STATUS_TIMED_OUT",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_STEPS",
]
