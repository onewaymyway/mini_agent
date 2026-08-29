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
# [cron_async_user_feedback_mechanism_plan] 本次触发过程中通过
# ask_user_async 提了问题、还在等用户异步作答，其它部分该跑的已经跑完/
# 无法再推进。跟 STATUS_NEEDS_REVIEW（StuckDetector 判定 GIVE_UP，代表
# "卡死放弃"）语义不同：这不是失败，不计入 consecutive_failures，也不
# 触发熔断——纯粹是"等一个具体问题的答案"，下次照常按周期触发（见方案
# 文档已确认：未回答时下次到期照常触发，agent 见机行事）。
STATUS_WAITING_FEEDBACK = "waiting_feedback"

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

    # 可被 job 级 config.json 覆盖的字段名单——read_config()/from_dict()
    # 的合并逻辑、以及 CronJobWorkspace.write_config_overrides() 的白名单
    # 校验都复用这份定义，避免两处字段列表长出来不一致。
    OVERRIDE_FIELDS = (
        "timeout_seconds", "max_steps", "stuck_similarity_threshold",
        "stuck_consecutive_limit", "stuck_max_recoveries",
    )

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
        for k in CronJobConfig.OVERRIDE_FIELDS:
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
    "{{#pending_answers}}\n"
    "--- 上次搁置的问题，用户已回答 ---\n"
    "{{pending_answers}}\n"
    "请据此继续之前搁置的工作。\n"
    "{{/pending_answers}}\n"
    "\n"
    "{{#unanswered_questions}}\n"
    "--- 以下问题仍未收到回复 ---\n"
    "{{unanswered_questions}}\n"
    "不要重复调用 ask_user_async 问同一个问题，先处理其它可推进的部分。\n"
    "{{/unanswered_questions}}\n"
    "\n"
    "{{#dismissed_questions}}\n"
    "--- 以下问题用户已明确表示不需要回答，不要再问 ---\n"
    "{{dismissed_questions}}\n"
    "{{/dismissed_questions}}\n"
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
        # [cron_async_feedback_hardening_plan.md D2] 上一次 render_prompt()
        # 渲染进 {{pending_answers}} 的 question_id 列表，供
        # consume_last_rendered_answers() 延迟标记消费。
        self._last_rendered_answer_ids: list[str] = []

    # ── 初始化 / 幂等 ensure ──────────────────────────────────────────────

    def ensure(self, default_task_template: str = "", default_config: Optional[CronJobConfig] = None) -> None:
        """确保文件夹和默认文件存在；已存在的文件不覆盖（用户可能已编辑过）。

        default_config — [兼容旧调用签名保留，不再影响写入内容] 早期版本会把
        这个参数在**首次创建**时整份 to_dict() 写进 config.json，导致"用户
        从未设置过、只是调用方按当时的全局 AppConfig.cron 拼出来的快照值"
        被永久固化成了这个 job 自己的显式覆盖——之后哪怕运维再调整全局的
        CronConfig.default_timeout_seconds，这个 job 也不会跟着变，因为
        config.json 里已经"显式写着"当初创建那一刻的值，read_config() 的
        缺省回退逻辑对它不生效。

        现在的语义改为：**只有用户通过 write_config_overrides() 主动设置过
        的字段才会被持久化**；config.json 首次创建时写入的是空覆盖 `{}`，
        job 自己没设置过的字段在每次 read_config(default=...) 时都会实时
        跟随调用方传入的全局默认值（通常来自当前的 AppConfig.cron），不需要
        对已存在的 job 目录做迁移。default_config 参数因此不再被这里使用，
        仅保留形参以免破坏既有调用方的位置/关键字参数写法。
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        if not self.prompt_path.exists():
            content = default_task_template or (
                "{{task_description}}\n\n{{progress}}\n"
                "{{#pending_answers}}\n{{pending_answers}}\n{{/pending_answers}}\n"
                "{{#unanswered_questions}}\n{{unanswered_questions}}\n{{/unanswered_questions}}\n"
                "{{#dismissed_questions}}\n{{dismissed_questions}}\n{{/dismissed_questions}}\n"
            )
            self.prompt_path.write_text(content, encoding="utf-8")
        if not self.config_path.exists():
            atomic_write_json(self.config_path, {})
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

    def read_raw_overrides(self) -> dict:
        """读取 config.json 里用户显式设置过的字段（原始 dict，未与任何
        default 合并）。看板据此展示"这个字段是用户自定义的，还是跟随全局
        默认"，区分于 read_config() 返回的、已经合并完默认值的最终生效值。
        文件缺失/损坏/内容非法（例如被手动改坏成非 dict）时返回空 dict。
        """
        try:
            d = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                return {}
            return {k: v for k, v in d.items() if k in CronJobConfig.OVERRIDE_FIELDS}
        except (OSError, json.JSONDecodeError):
            return {}

    def read_state(self) -> CronJobState:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return CronJobState.from_dict(d)
        except (OSError, json.JSONDecodeError):
            return CronJobState()

    # ── 写入 ──────────────────────────────────────────────────────────────

    def write_state(self, state: CronJobState) -> None:
        atomic_write_json(self.state_path, state.to_dict())

    def write_config_overrides(self, overrides: dict) -> dict:
        """用户在看板/API 里主动修改某个 job 的专属限制（超时秒数/最大
        步数/卡死检测参数）时的唯一写入口——只有经过这里写入的字段才会
        持久化到 config.json，取代早期 ensure(default_config=...) 那种
        "首次创建时把全局默认值当成用户设置永久固化下来"的做法（见
        ensure() 顶部说明）。

        overrides — 只包含用户这次想要改动的字段（不在 CronJobConfig.
        OVERRIDE_FIELDS 白名单内的键会被忽略，防止 config.json 里混入
        任意垃圾字段）；某个字段的 value 传 None 表示"清除这个字段的
        自定义覆盖，恢复跟随全局默认"，而不是把 timeout_seconds 真的设成
        None（那样后续 `time.time() + cfg.timeout_seconds` 会直接崩）。
        没提到的字段维持原样不动。

        返回写入后的原始 overrides dict（未与任何 default 合并），供调用方
        （如 API 层）直接回显。
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        current = self.read_raw_overrides()
        for k, v in overrides.items():
            if k not in CronJobConfig.OVERRIDE_FIELDS:
                continue
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
        atomic_write_json(self.config_path, current)
        return current

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
          {{pending_answers}}      — [cron_async_user_feedback_mechanism_plan]
                                     上次通过 ask_user_async 提出、现在已经
                                     被用户回答、但还没喂给过 agent 的问答对
                                     （"上次你问过「X」，用户回答：Y"），
                                     渲染后会调用 questions_store.
                                     mark_answers_consumed() 标记为已消费，
                                     避免同一个答案被后续多次触发反复注入。
          {{unanswered_questions}} — 仍处于待回答状态的问题列表，提醒 agent
                                     不要重复调用 ask_user_async 问同一个
                                     问题，先处理其它可推进的部分。
          {{dismissed_questions}}  — [cron_async_feedback_hardening_plan.md
                                     D3] 用户已明确忽略（不想回答）的问题，
                                     最近 20 条，提醒 agent 不要再问这些
                                     问题——原机制里"忽略"后问题对 agent
                                     完全不可见，导致 agent 换个措辞反复
                                     问同一件事、用户反复忽略的死循环。
        以及四个极简的 {{#xxx}}...{{/xxx}} 条件块：progress/previous_output/
        pending_answers/unanswered_questions 为空时整段连同标记一起去掉，
        避免每次都印出一段空标题。
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

        pending_answers_text = self._format_pending_answers()
        unanswered_questions_text = self._format_unanswered_questions()
        dismissed_questions_text = self._format_dismissed_questions()

        template = self._render_condition_block(template, "progress", bool(progress))
        template = self._render_condition_block(template, "previous_output", bool(previous_output_text))
        template = self._render_condition_block(template, "pending_answers", bool(pending_answers_text))
        template = self._render_condition_block(template, "unanswered_questions", bool(unanswered_questions_text))
        template = self._render_condition_block(template, "dismissed_questions", bool(dismissed_questions_text))

        template = template.replace("{{task_description}}", task_description)
        template = template.replace("{{progress}}", progress)
        template = template.replace("{{previous_output}}", previous_output_text)
        template = template.replace("{{previous_output_dir}}", previous_output_dir_text)
        template = template.replace("{{output_dir}}", output_dir_text)
        template = template.replace("{{pending_answers}}", pending_answers_text)
        template = template.replace("{{unanswered_questions}}", unanswered_questions_text)
        template = template.replace("{{dismissed_questions}}", dismissed_questions_text)
        if "{{output_policy}}" in template:
            try:
                from mini_agent.evolution.output_path_policy import load_policy
                policy_text = load_policy(self._paths)
            except Exception:
                policy_text = ""
            template = template.replace("{{output_policy}}", policy_text)
        return template

    def _format_pending_answers(self) -> str:
        """[cron_async_user_feedback_mechanism_plan] 取出本 job 已回答但
        还没消费过的问答对，格式化成文本。异常兜底返回空字符串——问答
        续接是感知增强，不能反过来影响 render_prompt() 本身。

        [cron_async_feedback_hardening_plan.md D2] **不在这里标记
        consumed。** 原实现在渲染时刻就调用 `mark_answers_consumed()`，
        但渲染发生在 `submit_step_fn()` 真正调用/成功之前——如果这一步
        LLM 调用本身失败（网络错误等），agent 从未看到这段 prompt，答案
        却已经被标记消费、下次触发查不到了，等于静默丢答案。现在只把
        本次渲染取到的 question_id 记到 `self._last_rendered_answer_ids`，
        真正的"标记消费"挪到 `consume_last_rendered_answers()`，由调用方
        （`CronJobExecutor.run_job()`）确认第一步执行成功后再调用。
        """
        try:
            from mini_agent.notification import questions_store
            rows = questions_store.list_unconsumed_answers_for_job(self._paths, self.job_id)
            self._last_rendered_answer_ids = [r["question_id"] for r in rows]
            if not rows:
                return ""
            lines = [
                f"- 上次你问过「{r.get('question', '')}」，用户回答：{r.get('answer', '')}"
                for r in rows
            ]
            return "\n".join(lines)
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.evolution.cron_job_workspace._format_pending_answers")
            self._last_rendered_answer_ids = []
            return ""

    def consume_last_rendered_answers(self) -> int:
        """[cron_async_feedback_hardening_plan.md D2] 把上一次
        `render_prompt()` 渲染进 `{{pending_answers}}` 的问答对正式标记为
        已消费。调用方应在确认这次渲染出的 prompt 已经被成功提交给
        agent（第一步 `submit_step_fn()` 未抛异常、且 `result.error` 为空）
        之后再调用；如果那一步失败了，就不要调用——让这些答案留在
        "未消费"状态，下次触发时能再被注入一次，不会因为这一步没跑起来
        就永久丢失。没有调用过 `render_prompt()`，或那次渲染里没有任何
        待消费答案时，是安全的空操作（返回 0）。
        """
        ids = getattr(self, "_last_rendered_answer_ids", None) or []
        if not ids:
            return 0
        try:
            from mini_agent.notification import questions_store
            return questions_store.mark_answers_consumed(self._paths, ids)
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.evolution.cron_job_workspace.consume_last_rendered_answers")
            return 0

    def _format_unanswered_questions(self) -> str:
        """[cron_async_user_feedback_mechanism_plan] 取出本 job 仍处于待
        回答状态的问题，提醒 agent 不要重复提问。异常兜底返回空字符串。

        [cron_async_feedback_lifecycle_and_usability_plan.md E1] 每条附带
        "已等待 N 天"——一方面提示 agent 越拖越久的问题更应该优先想办法
        自己给个合理默认值/绕过去，而不是无限期干等；另一方面这个问题
        如果拖过维护性 tick 的 `stale_after_days` 阈值会被自动关闭，提前
        让 agent（间接也是用户，因为 progress_summary 常被摘要进通知）
        感知到"这个问题快要被系统放弃"的时间压力。

        [cron_async_feedback_further_improvements_plan.md F3] 列表本身已经
        由 `list_pending_question_texts_for_job()` 排好序（blocking 在前），
        这里额外给 `urgency=blocking` 的问题加一个"（阻塞）"前缀标记，
        帮 agent 一眼看出"这几条不是随便问问，是真的卡住了没法继续"。
        """
        try:
            from mini_agent.notification import questions_store
            rows = questions_store.list_pending_question_texts_for_job(self._paths, self.job_id)
            if not rows:
                return ""
            now = time.time()
            lines = []
            for r in rows:
                created_at = r.get("created_at") or now
                days = max(0, int((now - created_at) // 86400))
                age_note = f"，已等待 {days} 天未回答" if days >= 1 else "，尚未回答"
                urgency_prefix = "（阻塞）" if questions_store.normalize_urgency(r.get("urgency")) == questions_store.URGENCY_BLOCKING else ""
                lines.append(f"- {urgency_prefix}「{r.get('question', '')}」{age_note}")
            return "\n".join(lines)
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.evolution.cron_job_workspace._format_unanswered_questions")
            return ""

    def _format_dismissed_questions(self, limit: int = 20) -> str:
        """[cron_async_feedback_hardening_plan.md D3] 取出本 job 最近被
        忽略/自动关闭的问题（最多 `limit` 条，按时间倒序），提醒 agent
        不要再问。异常兜底返回空字符串——这是感知增强，不能影响
        render_prompt() 本身。

        [cron_async_feedback_lifecycle_and_usability_plan.md E1] 区分展示
        "用户主动忽略" vs "长期无人回答被系统自动关闭"两种来源
        （`dismiss_reason`）——后者要额外提醒 agent："不是用户不想要这个
        答案，只是没来得及回而已，如果这个问题依旧关键，可以换个更容易
        被顺手回答的方式重新问一次"，跟前者"用户明确不需要，绝对不要再问"
        的语气不应该一样，否则 agent 会把"暂时没空回答"误解成"用户拒绝"。
        """
        try:
            from mini_agent.notification import questions_store
            rows = questions_store.list_dismissed_questions(self._paths, job_id=self.job_id, limit=limit)
            if not rows:
                return ""
            lines = []
            for r in rows:
                reason = r.get("dismiss_reason") or questions_store.DISMISS_REASON_MANUAL
                if reason == questions_store.DISMISS_REASON_STALE_TIMEOUT:
                    note = "（长期无人回答，已被系统自动关闭；如果仍然关键，可以换个更容易顺手回答的方式重新问一次，不要用原话重复问）"
                else:
                    note = "（用户已忽略，不要再问）"
                lines.append(f"- 「{r.get('question', '')}」{note}")
            return "\n".join(lines)
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.evolution.cron_job_workspace._format_dismissed_questions")
            return ""

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
