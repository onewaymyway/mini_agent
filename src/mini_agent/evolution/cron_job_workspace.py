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
    "{{#previous_output}}\n"
    "--- 上一轮产出（{{previous_output_dir}}） ---\n"
    "{{previous_output}}\n"
    "{{/previous_output}}\n"
    "\n"
    "{{output_policy}}\n"
    "\n"
    "本轮产出请写入：{{output_dir}}\n"
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

    def render_prompt(self, task_description: str, run_id: Optional[str] = None) -> str:
        """把 prompt.md 模板渲染成最终发给 agent 的文本。

        支持的占位符：
          {{task_description}} — cron_jobs.json 里配置的 task_template
          {{progress}}         — 上次执行遗留的 progress_summary（可能为空）
          {{output_policy}}    — [goal_cron_feedback_and_output_policy_plan.md
                                  5.3] 产出路径规范全文（见 output_path_policy.py）。
                                  已存在的、用户自定义的 prompt.md 如果没有这个
                                  占位符则不受影响（不强行插入）——用户如果想要
                                  这段规范，自己在 prompt.md 里加上即可，等价于
                                  用户主动选择不需要。
          {{previous_output}}     — [goal_cron_output_directory_convention_plan.md
                                     §3] 上一次触发产出的文件清单摘要（没有
                                     历史记录时为空，配合 {{#previous_output}}
                                     条件块整体隐藏）。
          {{previous_output_dir}} — 上一次触发产出目录的绝对路径。
          {{output_dir}}           — 本次触发分配到的产出目录绝对路径，
                                     由 output_workspace.allocate_run_dir()
                                     幂等创建。run_id 未传入时（调用方还没
                                     生成 run_id）留空，不创建目录。
        以及两个极简的 {{#xxx}}...{{/xxx}} 条件块：progress/previous_output
        为空时整段连同标记一起去掉，避免每次都印出一段空标题。
        """
        template = self.read_prompt()
        state = self.read_state()
        progress = state.progress_summary.strip()

        output_dir_text = ""
        previous_output_text = ""
        previous_output_dir_text = ""
        if run_id:
            from mini_agent.evolution import output_workspace
            base_dir = output_workspace.cron_output_base_dir(self._paths, self.job_id)
            out_dir = output_workspace.allocate_run_dir(self._paths, self.job_id, run_id)
            output_dir_text = str(out_dir)
            prev_manifest = output_workspace.read_latest_manifest(base_dir)
            if prev_manifest:
                previous_output_text = output_workspace.format_manifest_for_prompt(prev_manifest)
                previous_output_dir_text = prev_manifest.get("_dir", "")

        template = self._render_condition_block(template, "progress", bool(progress))
        template = self._render_condition_block(template, "previous_output", bool(previous_output_text))

        template = template.replace("{{task_description}}", task_description)
        template = template.replace("{{progress}}", progress)
        template = template.replace("{{previous_output}}", previous_output_text)
        template = template.replace("{{previous_output_dir}}", previous_output_dir_text)
        template = template.replace("{{output_dir}}", output_dir_text)
        if "{{output_policy}}" in template:
            try:
                from mini_agent.evolution.output_path_policy import load_policy
                policy_text = load_policy(self._paths)
            except Exception:
                policy_text = ""
            template = template.replace("{{output_policy}}", policy_text)
        return template

    @staticmethod
    def _render_condition_block(template: str, name: str, keep: bool) -> str:
        """极简 {{#name}}...{{/name}} 条件块处理：keep=False 时整段连同
        标记一起去掉；keep=True 时只去掉标记本身，保留内部内容（内容里的
        占位符由调用方后续统一 replace）。标记不存在时原样返回。
        """
        start_tag = f"{{{{#{name}}}}}"
        end_tag = f"{{{{/{name}}}}}"
        if start_tag not in template or end_tag not in template:
            return template
        start = template.index(start_tag)
        end = template.index(end_tag) + len(end_tag)
        block = template[start:end]
        if keep:
            inner = block[len(start_tag):-len(end_tag)]
            return template[:start] + inner + template[end:]
        return template[:start] + template[end:]

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

    def recent_runs_summary(self, limit: int = 10) -> list[dict]:
        """[看板"最近执行记录"只显示时间、看不出成功/失败的反馈] 按时间倒序
        返回最近 N 次执行的摘要（run_id/起止时间/是否成功/失败原因），供看板
        不用逐条点开事件详情就能一眼看出哪次失败了、为什么失败。

        每条摘要从对应 run 的 `runs/<run_id>.jsonl` 事件流里提取：
            - started_at   : `run_started` 事件的写入时间（无该事件时回退为
                              文件 mtime，理论上不应发生，仅防御性兜底）
            - finished_at  : `run_finished` 事件的写入时间；没有该事件说明
                              这次执行异常中断（进程崩溃/被杀）或仍在运行，
                              status 归为 "crashed_or_running"
            - status       : `run_finished.status`（idle/timed_out/
                              needs_human_review 之一，见模块顶部状态机
                              常量），映射为更易读的 success/timed_out/
                              failed/crashed_or_running 四种之一（见
                              `_RUN_STATUS_DISPLAY_MAP`）
            - success      : bool，status == STATUS_IDLE 时为 True，
                              其余（含 timed_out）一律 False——超时不算
                              "正常完成"，看板应该能看出来
            - error        : 失败原因文本。优先取 `run_finished` 所在这条
                              run 里最后一条带 `error` 字段的事件
                              （`step_error`/`stuck_give_up`，`step` 事件的
                              `error` 字段为 None 时忽略），取不到但
                              status 是 timed_out 时给一句固定说明
                              （区分是硬超时还是达到 max_steps 上限）
            - steps_executed / duration_seconds：直接取自 `run_finished`
              事件，供看板展示执行规模，取不到时为 None
        """
        run_ids = self.recent_runs(limit=limit)
        summaries: list[dict] = []
        for run_id in run_ids:
            events = self.read_run_events(run_id)
            summaries.append(self._summarize_run_events(run_id, events))
        return summaries

    @staticmethod
    def _summarize_run_events(run_id: str, events: list[dict]) -> dict:
        started_at = None
        finished_at = None
        raw_status = None
        error_text = ""
        steps_executed = None
        duration_seconds = None
        timeout_kind = ""  # "timed_out" / "max_steps_reached"，仅在没有更具体 error 时兜底用

        for ev in events:
            ev_type = ev.get("type")
            if ev_type == "run_started" and started_at is None:
                started_at = ev.get("at")
            elif ev_type == "run_finished":
                finished_at = ev.get("at")
                raw_status = ev.get("status")
                steps_executed = ev.get("steps_executed")
                duration_seconds = ev.get("duration_seconds")
            elif ev_type in ("step_error", "stuck_give_up"):
                err = ev.get("error")
                if err:
                    error_text = str(err)
            elif ev_type == "step":
                err = ev.get("error")
                if err:
                    error_text = str(err)
            elif ev_type == "timed_out":
                timeout_kind = "硬超时（触达 timeout_seconds 上限）"
            elif ev_type == "max_steps_reached":
                timeout_kind = "触达单次执行最大步数上限（max_steps）"

        if raw_status is None:
            display_status = "crashed_or_running"
            success = False
            if not error_text:
                error_text = "本次执行没有找到结束事件（进程可能异常退出，或仍在运行中）"
        elif raw_status == STATUS_IDLE:
            display_status = "success"
            success = True
        elif raw_status == STATUS_TIMED_OUT:
            display_status = "timed_out"
            success = False
            if not error_text:
                error_text = timeout_kind or "执行超时，未在限定时间/步数内完成"
        else:
            # STATUS_NEEDS_REVIEW 及其它未识别值一律按失败处理，宁可多报
            # 一次"失败"让用户去看详情，也不要把异常状态误判成成功。
            display_status = "failed"
            success = False
            if not error_text:
                error_text = "执行异常结束，未记录到具体错误信息（可展开事件详情查看）"

        return {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": display_status,
            "raw_status": raw_status,
            "success": success,
            "error": error_text,
            "steps_executed": steps_executed,
            "duration_seconds": duration_seconds,
        }

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
