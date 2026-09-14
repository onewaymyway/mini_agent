"""storage/created_dirs_registry.py — agent 自建目录健康检查登记表

背景（见 next_doc/workspace_output_governance_plan.md）：
排查项目工作目录规范性时发现，agent 曾经因为 prompt/reminder 文本里的
硬编码字面量（`prompts/reminders/write_large_file.md` 无条件建议
`mkdir -p ./temp`、`prompts/manager.py` 在没有 session 绑定时把
temp_dir/output_dir 兜底成裸的 `"./temp"`/`"./output"`），在项目根目录
下建过一些不受 `.agent/` 管辖的"游离目录"（典型是 `./temp`、`./output`）。

这些 prompt 层面的问题已经在本次一并修复（不会再产生新的游离目录），
但历史上已经产生的游离目录不会自动消失，而且即便修好了 prompt，也难保
未来不会因为别的疏漏又冒出新的游离目录。所以还需要一层"事后发现 + 引导
纠偏"的机制：

1. `scan_known_legacy_dirs()`：每次 session 绑定时，扫描项目根目录下几个
   历史上出现过问题的目录名（`temp`/`output`/`tmp` 等），把"当前是否存在、
   是否有内容"记录进 `.agent/created_dirs_registry.json`。
2. `refresh_and_diagnose()`：对比登记表里的历史记录和当前状态，识别两类
   情况并各生成一条可读的诊断说明，供调用方拼进 system prompt 或作为
   reminder 注入：
     - **vanished**：上次看到时目录还在且有内容，这次整个目录都不存在了。
       这大概率不是"任务正常清理"（任务清理一般只清内容、不会把目录本身
       和 `.agent/` 之外的东西一起消失得无影无踪），更可能是用户手动删除
       了——而且很可能是因为这个目录建错了地方（没建在 `.agent/` 下）。
       诊断说明会明确提示 agent："不要在原地重建，先想清楚该落到
       `.agent/sessions/<id>/{temp,output}` 还是别的规范位置"。
     - **still_present**：目录当前仍然存在且有内容，还没被处理过。
       只提示一次（`acknowledged_present` 置位后不再重复提示），建议 agent
       评估是否需要把内容迁移到规范目录、并在确认无用后自行清理。

只做记录 + 生成提示文本，不做任何自动删除/自动迁移——目录里可能有用户
自己放的、agent 完全不知情的文件，贸然动它比放着不管更危险。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from .paths import AgentPaths

# 历史上出现过"被硬编码进 prompt/reminder、导致 agent 在项目根目录
# 误建"的目录名。如果未来又发现新的历史遗留目录名，加进这个列表即可，
# 不需要改动其余逻辑。
KNOWN_LEGACY_DIR_NAMES: tuple[str, ...] = ("temp", "output", "tmp")


def _load_registry(paths: AgentPaths) -> dict:
    p = paths.created_dirs_registry_path
    if not p.exists():
        return {"entries": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.storage.created_dirs_registry._load_registry")
        return {"entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"entries": {}}
    return data


def _save_registry(paths: AgentPaths, data: dict) -> None:
    p = paths.created_dirs_registry_path
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _dir_has_content(dir_path: Path) -> bool:
    if not dir_path.is_dir():
        return False
    try:
        next(dir_path.rglob("*"))
        return True
    except StopIteration:
        return False
    except OSError:
        # 目录不可读之类的异常情况，保守起见当作"无内容"处理，不误报。
        return False


def record_created(paths: AgentPaths, dir_path: Path, *, purpose: str) -> None:
    """登记一个"agent 自己创建、且不属于 session/adhoc 规范位置"的目录。

    目前主要供 `config/prompt_builder.py` 的兜底 adhoc 目录创建路径调用；
    如果未来还有别的代码路径会在非规范位置建目录（理论上不应该再有，但
    留一个显式登记入口，方便万一出现时能被这套机制发现)，也可以调用这个
    函数把目录纳入监控。
    """
    data = _load_registry(paths)
    key = str(dir_path.resolve())
    entry = data["entries"].get(key, {})
    is_new_entry = not entry
    entry.update({
        "purpose": purpose,
        "created_at": entry.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_seen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_seen_had_content": _dir_has_content(dir_path),
    })
    # 只有全新登记的条目才初始化提醒状态；已存在的条目（比如
    # `scan_known_legacy_dirs()` 每次 session 绑定都会重新扫一遍）不应该
    # 把 `refresh_and_diagnose()` 已经处理过的 acknowledged_* 标记重置回
    # False，否则同一件事会在每次 session 绑定时被反复提醒。
    if is_new_entry:
        entry.setdefault("acknowledged_vanished", False)
        entry.setdefault("acknowledged_present", False)
    data["entries"][key] = entry
    _save_registry(paths, data)


def scan_known_legacy_dirs(paths: AgentPaths) -> None:
    """扫描项目根目录下 `KNOWN_LEGACY_DIR_NAMES` 里列出的历史遗留目录名，
    把当前存在的都记录/刷新进登记表。只在这些目录**已经存在**时才登记——
    不会主动创建它们，也不会因为"扫描"这个动作让原本不存在的目录出现。
    """
    for name in KNOWN_LEGACY_DIR_NAMES:
        candidate = paths.project_root / name
        if candidate.is_dir():
            record_created(paths, candidate, purpose="legacy_root_dir")


def refresh_and_diagnose(paths: AgentPaths) -> list[str]:
    """对比登记表与当前磁盘状态，返回本次需要提醒 agent 的诊断说明列表
    （每条一句可以直接拼进 prompt 的话）。同时会更新登记表状态，
    保证同一件事不会被反复提醒。

    调用时机：建议在 session 绑定（`agent/lifecycle.py::_bind_session_extras`）
    时调用一次，先 `scan_known_legacy_dirs()` 刷新一遍已知遗留目录名的
    状态，再调用本函数拿诊断结果。
    """
    data = _load_registry(paths)
    notes: list[str] = []
    changed = False
    for key, entry in data.get("entries", {}).items():
        dir_path = Path(key)
        currently_exists = dir_path.is_dir()
        currently_has_content = _dir_has_content(dir_path) if currently_exists else False
        had_content_before = bool(entry.get("last_seen_had_content"))

        if had_content_before and not currently_exists and not entry.get("acknowledged_vanished"):
            notes.append(
                f"提醒：你之前创建过的目录 `{key}` 现在整个都不存在了（不是"
                f"内容被清空，是目录本身消失了）。这大概率是用户手动删除的——"
                f"很可能正是因为这个目录建在了不规范的位置（比如项目根目录下的"
                f"临时/输出目录，而不是 `.agent/sessions/<session_id>/temp` 或 "
                f"`.agent/sessions/<session_id>/output`）。请不要在原路径重建，"
                f"先确认这次任务的产出应该落到哪个规范目录（临时文件用 Temp dir，"
                f"最终交付物用 Output dir，两者都以 system prompt 里当前给出的"
                f"绝对路径为准），再继续写文件。"
            )
            entry["acknowledged_vanished"] = True
            changed = True
        elif currently_exists and currently_has_content and not entry.get("acknowledged_present"):
            notes.append(
                f"提醒：检测到一个不属于规范工作目录体系的目录 `{key}` 目前仍然"
                f"存在且有内容。如果这是历史遗留（比如早期版本误建），建议评估"
                f"里面的内容是否还需要——需要的话迁移到规范的 Temp/Output 目录下，"
                f"确认不再需要的话可以清理掉；不确定的话先询问用户，不要自行删除。"
            )
            entry["acknowledged_present"] = True
            changed = True

        # 无论是否触发提醒，都刷新一次"当前状态"快照，供下一次比较用。
        if entry.get("last_seen_had_content") != currently_has_content:
            entry["last_seen_had_content"] = currently_has_content
            changed = True
        entry["last_seen_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    if changed:
        _save_registry(paths, data)
    return notes


def check_workspace_directory_health(project_root: Optional[Path] = None) -> list[str]:
    """一站式入口：扫描已知历史遗留目录名 + 生成本次需要提醒的诊断说明。
    异常情况下静默返回空列表——这只是一个辅助提醒机制，不应该因为自身
    出错影响正常的 session 绑定流程。
    """
    try:
        paths = AgentPaths(project_root)
        scan_known_legacy_dirs(paths)
        return refresh_and_diagnose(paths)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.storage.created_dirs_registry.check_workspace_directory_health")
        return []
