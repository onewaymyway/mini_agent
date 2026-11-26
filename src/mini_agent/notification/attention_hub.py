"""notification/attention_hub.py — "需要用户关注"事件的进程内发布点。

背景（见 next_doc/windows_desktop_resident_plan.md S0）：
桌面常驻客户端（托盘/Toast）需要在"新增一条待处理汇报 / cron 异步提问"
时被即时唤醒，而不是靠轮询。这两类数据各自落在独立的 jsonl 存储里
（reports / cron_questions），写入点分散。

这里提供一个极薄的发布订阅：
  - 两个 store（reports / cron_questions）的 append 函数在落盘成功后调用 `publish_attention()`；
  - HttpServer 在 `http.desktop_attention_events_enabled=true` 时注册监听，
    把事件转成 SSE `attention` 事件；默认不注册 → 没有任何监听者 →
    `publish_attention()` 退化为空操作，现有行为完全不变（保守 opt-in）。

约束：发布永远是尽力而为——监听者抛异常、没有监听者，都不能影响 store
的写入结果，也不能影响调用方（cron 提问路径必须保持原样可靠）。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

# kind：事件来源，客户端据此决定点击后跳到哪里
KIND_REPORT = "report"              # reports.jsonl 汇报（/v1/notifications/pending）
KIND_CRON_QUESTION = "cron_question"  # cron 异步提问（/v1/cron_questions/pending）
KIND_PERMISSION = "permission"        # 工具审批等待用户（/v1/permissions/pending）【S3 新增】
KIND_INTERACTION = "interaction"      # 通用交互式提问 ask_user 系列（/v1/interactions/pending）【S3 新增】
KIND_TURN_DONE = "turn_done"          # 用户发起的 turn 完成【S3 新增】
KIND_TURN_FAILED = "turn_failed"      # 用户发起的 turn 失败【S3 新增】

# 注意：daemon 崩溃告警【不】经过本发布点——它由 supervisor 进程写入，
# 而崩溃的 daemon 进程里的监听者早已不在；客户端应在 SSE 断线后通过
# /v1/desktop/summary 的 crash_alerts_unacked 发现崩溃。

# level：客户端的 Toast 分级依据（沿用项目已有的 blocking/normal 语义并扩展）
LEVEL_NORMAL = "normal"
LEVEL_BLOCKING = "blocking"   # 有任务在等用户回答，没有答案无法继续

Listener = Callable[[dict], None]

_lock = threading.Lock()
_listeners: list[Listener] = []


def register_listener(fn: Listener) -> None:
    """注册监听者（幂等：同一个函数对象只注册一次）。"""
    with _lock:
        if fn not in _listeners:
            _listeners.append(fn)


def unregister_listener(fn: Listener) -> None:
    with _lock:
        try:
            _listeners.remove(fn)
        except ValueError:
            pass


def listener_count() -> int:
    with _lock:
        return len(_listeners)


def publish_attention(
    kind: str,
    title: str,
    ref_id: str = "",
    level: str = LEVEL_NORMAL,
    extra: Optional[dict] = None,
) -> None:
    """发布一条"需要关注"事件。没有监听者时是空操作；任何异常都被吞掉。

    ref_id 是对应存储里的记录 id（report_id / question_id / alert_id），
    客户端用它定位具体条目。title 只放一行摘要，不放正文（避免把长文本/
    敏感内容塞进 SSE 环形缓冲；详情靠 ref_id 回查对应 REST 端点）。
    """
    with _lock:
        listeners = list(_listeners)
    if not listeners:
        return
    event = {
        "kind": kind,
        "title": (title or "")[:200],
        "ref_id": ref_id or "",
        "level": level,
        "extra": dict(extra) if extra else {},
    }
    for fn in listeners:
        try:
            fn(event)
        except Exception as exc:  # noqa: BLE001 - 发布必须尽力而为
            try:
                from mini_agent.errors import log_exception
                log_exception(exc, where="mini_agent.notification.attention_hub.publish_attention")
            except Exception:
                pass
