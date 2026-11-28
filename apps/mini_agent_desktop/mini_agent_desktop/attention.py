"""S3：Toast 策略与待处理事项（纯逻辑，不依赖 Qt）。

Toast 范围（收窄，遵循\"主动性晚于可靠性\"）：
  - 权限审批、交互式提问（ask_user 系列）、cron 异步提问 → 需要你处理
  - 用户自己发起的任务完成 / 失败（cron、autonomous 触发的一律静默；
    快速面板自己的 session 也静默——面板里已经直接显示结果）
  - daemon 异常退出（由控制器在离线时检测未确认崩溃告警的增长）
  - 每日融合日报生成（daily_digest）→ 非阻塞弹窗，按日期去重
  - 汇报（report）、autonomous tick、巩固循环一律静默

免打扰：quiet_hours（如 "23:00-08:00"）时段内，非阻塞类 Toast 被抑制；
阻塞类（有任务在等你回答）不受免打扰影响——没有答案，任务就停在那里。
同一事件（dedupe key）只提醒一次；被抑制的事件依然留在待处理列表里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from mini_agent_desktop.api import DaemonApi

QUICK_SESSION_ID = "desktop-quick"     # 与 quick.py 保持一致
DEDUPE_MEMORY = 500

KIND_PERMISSION = "permission"
KIND_INTERACTION = "interaction"
KIND_CRON_QUESTION = "cron_question"
KIND_TURN_DONE = "turn_done"
KIND_TURN_FAILED = "turn_failed"
KIND_DAEMON_CRASH = "daemon_crash"
KIND_DAILY_DIGEST = "daily_digest"


# ════════════════════════════════════════════════════════════════════
# 免打扰时段
# ════════════════════════════════════════════════════════════════════

def _hm_to_minutes(text: str) -> int:
    hh, _, mm = text.strip().partition(":")
    if not (hh.isdigit() and mm.isdigit()):
        raise ValueError(f"时间格式错误：{text!r}（应为 HH:MM）")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时间超出范围：{text!r}")
    return h * 60 + m


@dataclass(frozen=True)
class QuietHours:
    start: int   # 分钟，含
    end: int     # 分钟，不含；start > end 表示跨午夜

    @classmethod
    def parse(cls, spec: str) -> Optional["QuietHours"]:
        """空字符串 = 不设免打扰；格式错误抛 ValueError。"""
        if not spec or not spec.strip():
            return None
        left, sep, right = spec.partition("-")
        if not sep:
            raise ValueError(f"免打扰格式应为 HH:MM-HH:MM：{spec!r}")
        return cls(_hm_to_minutes(left), _hm_to_minutes(right))

    def contains(self, when: datetime) -> bool:
        m = when.hour * 60 + when.minute
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= m < self.end
        return m >= self.start or m < self.end


# ════════════════════════════════════════════════════════════════════
# Toast 策略
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Toast:
    title: str
    body: str
    key: str
    blocking: bool


# 判定原因（last_reason）。阶段A 起记录，供日志与 --check 排障：为什么没有弹窗？
REASON_OK = "ok"
REASON_DISABLED = "toast_disabled"        # desktop.json 的 toast_enabled=false（默认值）
REASON_SILENT_KIND = "silent_kind"        # report / autonomous_tick / digest 等，设计上静默
REASON_QUICK_SESSION = "quick_session"    # 快速面板自己的任务，结果已在面板里
REASON_NO_REF = "no_ref"                  # 事件缺少 ref_id，无法去重，保守不弹
REASON_DUPLICATE = "duplicate"            # 同一事件已提醒过
REASON_QUIET = "quiet_hours"              # 非阻塞类在免打扰时段内


@dataclass
class ToastPolicy:
    enabled: bool = False
    quiet: Optional[QuietHours] = None
    clock: Callable[[], datetime] = datetime.now
    _seen: dict = field(default_factory=dict)
    last_reason: str = ""
    _last_toast: Optional["Toast"] = None

    def _remember(self, key: str) -> bool:
        """返回 True 表示首次出现。"""
        if key in self._seen:
            return False
        self._seen[key] = True
        while len(self._seen) > DEDUPE_MEMORY:
            self._seen.pop(next(iter(self._seen)))
        return True

    def decide(self, event: dict) -> Optional[Toast]:
        self.last_reason = self._decide(event)
        if self.last_reason != REASON_OK:
            return None
        return self._last_toast

    def _decide(self, event: dict) -> str:
        self._last_toast = None
        if not self.enabled:
            return REASON_DISABLED
        if not isinstance(event, dict):
            return REASON_SILENT_KIND
        kind = str(event.get("kind", ""))
        ref = str(event.get("ref_id", ""))
        title = str(event.get("title", "") or "")
        level = str(event.get("level", "normal"))
        extra = event.get("extra") or {}
        blocking = level == "blocking"

        if kind == KIND_PERMISSION:
            head, body = "需要审批", title
            blocking = True
        elif kind == KIND_INTERACTION:
            head, body = "需要你回答", title
            blocking = True
        elif kind == KIND_CRON_QUESTION:
            head, body = "后台任务有问题", title
        elif kind == KIND_TURN_DONE:
            if str(extra.get("session_id", "")) == QUICK_SESSION_ID:
                return REASON_QUICK_SESSION
            head, body = "任务完成", title
        elif kind == KIND_TURN_FAILED:
            if str(extra.get("session_id", "")) == QUICK_SESSION_ID:
                return REASON_QUICK_SESSION
            head, body = "任务失败", title
        elif kind == KIND_DAEMON_CRASH:
            head, body = "daemon 异常退出", title
            blocking = True
        elif kind == KIND_DAILY_DIGEST:
            # 日报生成是用户期望看到的提醒：非阻塞，遵守免打扰，按日期去重
            head, body = "今日日报已生成", title
            blocking = False
        else:
            return REASON_SILENT_KIND     # report 等一律静默

        key = f"{kind}:{ref}"
        if not ref:
            return REASON_NO_REF
        if not self._remember(key):
            return REASON_DUPLICATE
        if not blocking and self.quiet is not None and self.quiet.contains(self.clock()):
            return REASON_QUIET
        self._last_toast = Toast(title=head, body=body[:200], key=key, blocking=blocking)
        return REASON_OK


# ════════════════════════════════════════════════════════════════════
# 待处理事项（面板里的回答列表）
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PendingItem:
    kind: str                 # permission / interaction / cron
    ref_id: str
    title: str
    detail: str = ""
    interaction_kind: str = ""      # ask_user / ask_user_confirm / ask_user_choice / ...
    options: tuple = ()


def _short_json(value, limit: int = 200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def collect_pending(api: DaemonApi) -> list[PendingItem]:
    """汇总三类待回答项。任一接口失败时该类返回空，不影响其它类。"""
    items: list[PendingItem] = []
    try:
        for p in api.permissions_pending():
            items.append(PendingItem(
                kind="permission",
                ref_id=str(p.get("req_id", "")),
                title=f"审批工具：{p.get('tool_name', '?')}",
                detail=_short_json(p.get("tool_input", {})),
            ))
    except Exception:  # noqa: BLE001
        pass
    try:
        for i in api.interactions_pending():
            data = i.get("data") or {}
            options = tuple(str(o) for o in (data.get("options") or []))
            items.append(PendingItem(
                kind="interaction",
                ref_id=str(i.get("req_id", "")),
                title=str(data.get("question") or data.get("prompt_text") or "需要你回答"),
                detail=str(data.get("hint") or ""),
                interaction_kind=str(i.get("kind", "")),
                options=options,
            ))
    except Exception:  # noqa: BLE001
        pass
    try:
        for q in api.cron_questions_pending():
            items.append(PendingItem(
                kind="cron",
                ref_id=str(q.get("question_id", "")),
                title=str(q.get("question", "")),
                detail=str(q.get("hint") or q.get("job_id", "")),
                options=tuple(str(o) for o in (q.get("options") or [])),
            ))
    except Exception:  # noqa: BLE001
        pass
    return items


def answer_item(api: DaemonApi, item: PendingItem, *, text: str = "",
                approve: Optional[bool] = None, choice_index: Optional[int] = None,
                confirmed: Optional[bool] = None) -> None:
    """按条目类型提交回答。参数按类型取用，其余忽略。"""
    if item.kind == "permission":
        api.respond_permission(item.ref_id, bool(approve), mode="once")
        return
    if item.kind == "cron":
        if not text.strip():
            raise ValueError("回答不能为空")
        api.answer_cron_question(item.ref_id, text.strip())
        return
    # interaction：body 字段随 kind 变化（见 bridge.py::_PendingInteraction）
    if item.interaction_kind == "ask_user_confirm":
        body = {"confirmed": bool(confirmed)}
    elif item.interaction_kind == "ask_user_choice" and choice_index is not None:
        body = {"choice_index": int(choice_index)}
    else:
        if not text.strip():
            raise ValueError("回答不能为空")
        body = {"answer": text.strip()}
    api.respond_interaction(item.ref_id, body)
