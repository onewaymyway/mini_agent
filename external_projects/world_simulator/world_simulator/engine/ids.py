"""world_simulator/engine/ids.py — 实例 id / skill 名生成。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

import secrets
import string

_ID_ALPHABET = string.ascii_lowercase + string.digits


def _new_sim_id(template: str) -> str:
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"{template}_{suffix}"


def _skill_name_for_template(template: str) -> str:
    return f"{template.replace('_', '-')}-template"
