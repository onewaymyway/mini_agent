"""digest_catchup.py — 日报弹窗的"是否已弹过"记忆与启动/重连补弹判定（纯逻辑，不依赖 Qt）。

规则（用户确认）：
  - 日报生成时若桌面端在线，由 SSE attention 事件实时弹窗；
  - 若生成时桌面端不在线，启动或重连时补弹一次；
  - "弹过"的判定以"真正弹出了 Toast"为准，按日期记录在 desktop 配置目录下的
    digest_toast_state.json 中，重启后依然有效（内存里的去重表会丢失，所以需要落盘）；
  - 只补弹最近 CATCHUP_MAX_AGE_SECONDS 内生成的日报，避免首次安装时把很久以前的日报弹出来。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from mini_agent_desktop.attention import KIND_DAILY_DIGEST

STATE_FILENAME = "digest_toast_state.json"
CATCHUP_MAX_AGE_SECONDS = 2 * 86400


class DigestToastMemory:
    """记录最近一次已经弹出过 Toast 的日报日期。读写失败一律退化，不抛异常。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def last_day(self) -> str:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return str(data.get("last_popped_day") or "")
        except Exception:  # noqa: BLE001
            return ""

    def mark(self, day: str) -> None:
        if not day:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"last_popped_day": day}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)   # 原子替换，避免半写文件
        except Exception:  # noqa: BLE001
            pass


def pending_digest_event(
    resp: Optional[dict],
    memory: DigestToastMemory,
    now: Optional[float] = None,
) -> Optional[dict]:
    """根据 /v1/digest/daily 的响应，判断启动/重连时是否需要补弹。

    需要补弹返回一个与 SSE attention 事件同构的 dict；否则返回 None。
    """
    if not resp:
        return None
    digest = resp.get("digest") or {}
    day = str(digest.get("day") or "")
    generated_at = digest.get("generated_at")
    if not day or not generated_at:
        return None
    if day == memory.last_day():
        return None                       # 已经弹过
    current = time.time() if now is None else now
    if current - float(generated_at) > CATCHUP_MAX_AGE_SECONDS:
        return None                       # 太旧，不补弹
    return {
        "kind": KIND_DAILY_DIGEST,
        "title": f"{day} 日报已生成",
        "ref_id": day,
        "level": "normal",
        "extra": {"day": day, "catchup": True},
    }
