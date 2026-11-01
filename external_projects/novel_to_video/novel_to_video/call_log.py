"""call_log.py — 外部 API 调用留痕（logs/api_calls.jsonl，追加）。不记录任何 key。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def log_call(project_dir: Path, record: Dict[str, Any]) -> None:
    logs = project_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now().isoformat(timespec="seconds"), **record}
    with (logs / "api_calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def count_calls(project_dir: Path, kind: str | None = None) -> int:
    path = project_dir / "logs" / "api_calls.jsonl"
    if not path.is_file():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if kind is None or json.loads(line).get("kind") == kind:
            n += 1
    return n
