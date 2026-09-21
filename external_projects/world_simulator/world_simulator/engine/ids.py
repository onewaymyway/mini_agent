"""world_simulator/engine/ids.py — 实例 id / skill 名生成。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

import datetime
import secrets
import string
from pathlib import Path

_ID_ALPHABET = string.ascii_lowercase + string.digits


def _new_sim_id(template: str) -> str:
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"{template}_{suffix}"


def _skill_name_for_template(template: str) -> str:
    return f"{template.replace('_', '-')}-template"


def _read_skill_version(workspace_root: Path, template: str) -> str:
    """读取 `template` 对应 `SKILL.md` 文件的最后修改时间戳，格式化成
    ISO 字符串（第八轮批次六，`next_doc/world_simulator_c_category_
    precision_upgrade_improvement_plan.md` 第 7 节）。

    这是"不引入 git commit hash 等额外基础设施"前提下最小成本的版本
    区分方式——文件没被改过，返回值就不变。**任何失败都静默返回空
    字符串**（文件不存在、路径解析出错、权限问题等），不抛错、不
    阻断推进——这只是一个辅助性的版本标注，值不值得信任由使用方
    （用户对比不同版本 skill 跑出来的预测质量）自行判断，计算失败
    不应该让整步推进失败。
    """
    try:
        skill_name = _skill_name_for_template(template)
        skill_md_path = Path(workspace_root) / "skills" / skill_name / "SKILL.md"
        mtime = skill_md_path.stat().st_mtime
        return datetime.datetime.fromtimestamp(
            mtime, tz=datetime.timezone.utc
        ).isoformat()
    except OSError:
        return ""
    except Exception:  # noqa: BLE001 — 版本标注失败绝不应该阻断推进
        return ""
