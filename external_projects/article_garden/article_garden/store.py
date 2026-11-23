"""store.py — 原子写入与 JSON / YAML 读写。所有落盘都经此模块，保证中途中断不留半个文件。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(Path(path), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.is_file():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_yaml_atomic(path: Path, data: Any) -> None:
    write_text_atomic(Path(path), yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False))


def read_yaml(path: Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.is_file():
        return default
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return default if data is None else data
