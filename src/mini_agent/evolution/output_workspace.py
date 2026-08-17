"""
evolution/output_workspace.py — Goal/Cron 周期性执行的产出目录规范
（next_doc/goal_cron_output_directory_convention_plan.md）

目录结构（<project_root>/.agent/daemon_run_outputs/）：
    goals/<goal_id>/
        latest.json           指针文件：{"latest_dir": "cycle_0003", "updated_at": ...}
        cycle_0001/manifest.json    # recurring Goal：按"轮次"编号
        cycle_0002/manifest.json
        ...
        run_0001/manifest.json      # 一次性 Goal：按子 Objective 创建顺序编号
        run_0002/manifest.json      # （两种命名不会出现在同一个 goal_id 下，
        ...                         #  一个 Goal 要么 recurring 要么不是）
    cron/<job_id>/            job_id 里的 ':' 换成 '_'，与 CronJobWorkspace 一致
        latest.json
        run_<run_id>/manifest.json
        ...

本模块只负责 §2/§3 的目录分配和 manifest 读写，不关心"什么时候该分配/
该写"——那部分逻辑分别在 cron_job_executor.py（dedicated-execution cron）、
goal_cron_bridge.py（recurring Goal 触发时分配目录 + 拼 prompt）、
goal_backlog.py（一次性 Goal 创建子 Objective 时分配目录 + 拼 description）、
objective_executor.py（子 Objective 收尾时落 manifest，recurring/一次性
两种 Goal 都覆盖，见 §5 开放问题 3 的最终结论）。

不用符号链接（跨平台，Windows 默认无权限创建），"最新一轮"用 latest.json
这个小指针文件表达。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from mini_agent.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


MANIFEST_VERSION = 1


# ── 目录归属 ──────────────────────────────────────────────────────────────────

def outputs_root(paths: "AgentPaths") -> Path:
    # [用户反馈] 顶层目录改放 .agent/ 内部（daemon_run_outputs），不占用
    # 项目根目录一级命名空间——避免和用户项目里可能已有的同名 outputs/
    # 目录冲突。语义上仍然是"daemon 自主运行产出"，跟 .agent/ 下其余
    # "agent 自己的内部状态"目录（cron_jobs/、policies/ 等）放在一起，
    # 用户想找的话打开 .agent/daemon_run_outputs/ 即可，也方便按需整体
    # 加进 .gitignore。
    return Path(paths.project_root) / ".agent" / "daemon_run_outputs"


def goal_output_base_dir(paths: "AgentPaths", goal_id: str) -> Path:
    return outputs_root(paths) / "goals" / goal_id


def cron_output_base_dir(paths: "AgentPaths", job_id: str) -> Path:
    # job_id 里可能含 ':'，文件系统里用 '_' 替换，与 CronJobWorkspace 的
    # safe_id 规则保持一致，方便用户对照 .agent/cron_jobs/<safe_id>/ 找到
    # 对应的 .agent/daemon_run_outputs/cron/<safe_id>/。
    safe_id = job_id.replace(":", "_")
    return outputs_root(paths) / "cron" / safe_id


# ── 目录分配 ──────────────────────────────────────────────────────────────────

def allocate_cycle_dir(paths: "AgentPaths", goal_id: str, cycle: int) -> Path:
    """为 recurring Goal 的第 `cycle` 轮分配（幂等）产出目录，返回已存在的
    绝对路径。cycle 编号取 GoalNode.cycle_count + 1（触发前的值 +1）。
    """
    d = goal_output_base_dir(paths, goal_id) / f"cycle_{cycle:04d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def allocate_run_dir(paths: "AgentPaths", job_id: str, run_id: str) -> Path:
    """为普通 CronJob（非 goal_cycle）的一次触发分配（幂等）产出目录。"""
    d = cron_output_base_dir(paths, job_id) / f"run_{run_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def allocate_objective_dir(paths: "AgentPaths", goal_id: str, ordinal: int) -> Path:
    """[goal_cron_output_directory_convention_plan.md §5 开放问题 3] 为**一次性**
    （非 recurring）Goal 名下的第 `ordinal` 个子 Objective 分配（幂等）产出目录。

    与 recurring Goal 的 `cycle_%04d` 目录同放在 `goals/<goal_id>/` 下，但用
    `run_%04d` 命名以示区分——一次性 Goal 的多个子 Objective（拆解出的若干
    步骤）之间不是"轮次"关系，语义上更接近 CronJob 的一次次离散触发，故沿用
    `run_` 前缀与 `cron/<job_id>/run_<run_id>/` 保持视觉上的一致性。

    ordinal 取该 Objective 在父 Goal.children_ids 里的 1-based 位置（调用方
    负责计算，见 `goal_backlog.add_objectives_for_goal()` /
    `objective_executor._write_output_manifest()` 两处各自独立按同一条规则
    重新计算，无需额外持久化 ordinal 本身）。
    """
    d = goal_output_base_dir(paths, goal_id) / f"run_{ordinal:04d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── [goal_output_directory_and_execution_phase_redesign_plan.md] 新目录模型 ──
#
# recurring Goal（run_mode="goal_cycle"）专用：不再"每轮一个新目录"，改成
# 四个并列的固定目录，跨轮共用：
#
#   goals/<goal_id>/output/    唯一的、跨轮共用的正式产出目录（方案 §2）
#   goals/<goal_id>/notes/     每轮一份总结笔记，过程记录，非交付物（方案 §3）
#   goals/<goal_id>/spec/      执行规范当前版本 + 历史版本（方案 §4，写入
#                              逻辑在 goal_execution_spec.py，这里只提供路径）
#   goals/<goal_id>/scratch/   explore/converge 期的试验田（方案 §5）
#
# 一次性（非 recurring）Goal 和独立 cron job（cron/<job_id>/run_<run_id>/）
# 不受影响，继续使用上面 allocate_run_dir()/allocate_objective_dir() 的
# "每次触发一个目录"模式——"探索到稳定"这个语境本身依赖"同一个 Goal 的连续
# 多轮"，这两类没有这个语境，套用新模型意义不大（见方案 §1）。
#
# 旧的 allocate_cycle_dir()/write_manifest()/read_latest_manifest() 等函数
# 保留不删除（标记为 legacy，供已存在的历史 cycle_NNNN/ 目录、以及尚未迁移
# 的调用方继续读取），新的 recurring Goal 触发逻辑改用下面这组函数。

# output/ 根目录下的系统保留名——tidy 阶段据此判断"根目录下是否存在白名单
# 之外的文件/目录"（方案 §2.1），是一段确定性代码检查，不依赖 LLM 判断。
OUTPUT_RESERVED_NAMES = ("README.md", "_misc", "_archive", "scripts")

# scripts/ 目录下的固定骨架文件/目录名（方案 §6）。
SCRIPTS_RESERVED_NAMES = (
    "README.md", "requirements.txt", "CHANGELOG.md", "lib", "_run_logs", "_experiments",
)


def goal_output_dir(paths: "AgentPaths", goal_id: str) -> Path:
    """recurring Goal 的正式产出目录（方案 §2），跨轮共用，不再按轮次新建。"""
    return goal_output_base_dir(paths, goal_id) / "output"


def goal_notes_dir(paths: "AgentPaths", goal_id: str) -> Path:
    """recurring Goal 的每轮总结笔记目录（方案 §3）。"""
    return goal_output_base_dir(paths, goal_id) / "notes"


def goal_spec_dir(paths: "AgentPaths", goal_id: str) -> Path:
    """recurring Goal 的执行规范落盘目录（当前版本 + 历史版本，方案 §4）。
    实际读写在 perception/goal_execution_spec.py，这里只提供路径。"""
    return goal_output_base_dir(paths, goal_id) / "spec"


def goal_scratch_dir(paths: "AgentPaths", goal_id: str) -> Path:
    """recurring Goal 的 explore/converge 期试验田（方案 §5）。"""
    return goal_output_base_dir(paths, goal_id) / "scratch"


def ensure_output_skeleton(paths: "AgentPaths", goal_id: str) -> Path:
    """确保 output/ 固定骨架存在（方案 §2.1/§6）：README.md（首次创建给一个
    占位内容，实际索引由 render_output_readme() 刷新）、_misc/、_archive/、
    scripts/（含 scripts/lib/、scripts/_run_logs/、scripts/_experiments/
    三个固定子目录）。已存在的文件/目录不覆盖，返回 output/ 本身路径。幂等，
    可重复调用。
    """
    out_dir = goal_output_dir(paths, goal_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_misc").mkdir(exist_ok=True)
    (out_dir / "_archive").mkdir(exist_ok=True)
    scripts_dir = out_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "lib").mkdir(exist_ok=True)
    (scripts_dir / "_run_logs").mkdir(exist_ok=True)
    (scripts_dir / "_experiments").mkdir(exist_ok=True)
    if not (scripts_dir / "requirements.txt").exists():
        (scripts_dir / "requirements.txt").write_text("", encoding="utf-8")
    if not (scripts_dir / "CHANGELOG.md").exists():
        (scripts_dir / "CHANGELOG.md").write_text(
            "# 脚本改动历史\n\n（新脚本入驻/重大修改时在这里补一行记录）\n",
            encoding="utf-8",
        )
    if not (scripts_dir / "README.md").exists():
        (scripts_dir / "README.md").write_text(
            "# scripts/ 说明\n\n（尚未有正式脚本，见 _experiments/ 下是否有可转正的实验代码）\n",
            encoding="utf-8",
        )
    readme_path = out_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
            "# 产出目录索引\n\n（尚未生成，下一次 tidy/stable 轮次结束后会自动刷新）\n",
            encoding="utf-8",
        )
    return out_dir


def scan_output_structure(paths: "AgentPaths", goal_id: str) -> dict:
    """扫描 output/ 实际内容，返回结构化统计——方案 §2.5/§7.1 里"代码算出的
    问题清单"的数据来源，render_output_readme() 和 tidy 阶段核对清单共用
    这份扫描结果，不重复实现目录遍历逻辑。

    返回结构：
    {
      "root_unexpected": [...],       output/ 根目录下的散落文件（目录一律
                                        视为业务子目录，是否与 spec 一致由
                                        调用方结合 GoalExecutionSpec 核对）
      "misc_count": int,              _misc/ 下的文件数（非目录）
      "misc_files": [...],
      "sub_dirs": {name: {"file_count": int, "latest_mtime": float|None}},
      "scripts": {
          "root_files": [...],            scripts/ 根目录下的 .py 文件名
          "unexpected_root_files": [...], 疑似临时脚本却在根目录（方案 §6.4）
          "experiments_count": int,
          "run_logs_count": int,
      },
      "archive_entries": int,
    }
    """
    out_dir = goal_output_dir(paths, goal_id)
    result = {
        "root_unexpected": [],
        "misc_count": 0,
        "misc_files": [],
        "sub_dirs": {},
        "scripts": {
            "root_files": [],
            "unexpected_root_files": [],
            "experiments_count": 0,
            "run_logs_count": 0,
        },
        "archive_entries": 0,
    }
    if not out_dir.is_dir():
        return result

    _TEMP_NAME_HINTS = ("test_", "try_", "tmp_", "debug_", "temp_", "scratch_")

    for child in sorted(out_dir.iterdir()):
        name = child.name
        if child.is_file():
            # 只有直接散落在 output/ 根目录下的**文件**才算"未分类"——目录一律
            # 视为业务子目录（是否与 spec 声明的 sub_directories 对得上，是
            # tidy 阶段结合 GoalExecutionSpec 再核对的职责，这个函数本身不
            # 感知 spec，避免产生循环依赖）。
            if name not in OUTPUT_RESERVED_NAMES:
                result["root_unexpected"].append(name)
            continue
        if name == "_misc" and child.is_dir():
            files = [f.name for f in child.iterdir() if f.is_file()]
            result["misc_count"] = len(files)
            result["misc_files"] = files
        elif name == "_archive" and child.is_dir():
            result["archive_entries"] = sum(1 for _ in child.iterdir())
        elif name == "scripts" and child.is_dir():
            root_py = [f.name for f in child.iterdir() if f.is_file() and f.suffix == ".py"]
            result["scripts"]["root_files"] = root_py
            result["scripts"]["unexpected_root_files"] = [
                f for f in root_py if f.lower().startswith(_TEMP_NAME_HINTS)
            ]
            experiments_dir = child / "_experiments"
            if experiments_dir.is_dir():
                result["scripts"]["experiments_count"] = sum(
                    1 for f in experiments_dir.rglob("*") if f.is_file()
                )
            run_logs_dir = child / "_run_logs"
            if run_logs_dir.is_dir():
                result["scripts"]["run_logs_count"] = sum(
                    1 for f in run_logs_dir.iterdir() if f.is_file()
                )
        elif child.is_dir() and name not in ("_misc", "_archive", "scripts"):
            files = [f for f in child.rglob("*") if f.is_file()]
            latest_mtime = max((f.stat().st_mtime for f in files), default=None)
            result["sub_dirs"][name] = {"file_count": len(files), "latest_mtime": latest_mtime}

    return result


def render_output_readme(paths: "AgentPaths", goal_id: str, *, cycle_no: Optional[int] = None) -> str:
    """扫描 output/ 实际内容，机械生成 output/README.md（方案 §2.5）——
    刻意不经过 LLM，保证这份索引反映的是客观文件系统事实，而不是 agent 的
    主观整理报告（那部分内容留在 notes/ 里）。返回写入的文本内容。
    """
    ensure_output_skeleton(paths, goal_id)
    stats = scan_output_structure(paths, goal_id)

    lines: list[str] = ["# 产出目录索引\n"]
    when = f"第 {cycle_no} 轮" if cycle_no is not None else "未知轮次"
    lines.append(f"最后更新：{when}（自动生成，非 agent 手写）\n")

    lines.append("## 业务子目录")
    if stats["sub_dirs"]:
        for name, info in sorted(stats["sub_dirs"].items()):
            mtime = info["latest_mtime"]
            mtime_str = time.strftime("%Y-%m-%d", time.localtime(mtime)) if mtime else "（空）"
            lines.append(f"- `{name}/`（{info['file_count']} 个文件，最新：{mtime_str}）")
    else:
        lines.append("（尚无业务子目录，待 spec 声明后由 converge 阶段迁入）")
    lines.append("")

    lines.append("## 脚本")
    scripts_info = stats["scripts"]
    lines.append(f"- `scripts/`：{len(scripts_info['root_files'])} 个正式脚本")
    if scripts_info["unexpected_root_files"]:
        lines.append(
            "  - ⚠️ 疑似临时脚本混在根目录，应挪进 _experiments/："
            + "、".join(scripts_info["unexpected_root_files"])
        )
    lines.append(f"- `scripts/_experiments/`：{scripts_info['experiments_count']} 个文件")
    lines.append(f"- `scripts/_run_logs/`：{scripts_info['run_logs_count']} 个日志")
    lines.append("")

    lines.append("## 待整理")
    misc_mark = "✅" if stats["misc_count"] == 0 else "⚠️"
    lines.append(f"- `_misc/`：{stats['misc_count']} 个文件 {misc_mark}"
                 + ("" if stats["misc_count"] == 0 else "（下次 tidy 请处理）"))
    if stats["root_unexpected"]:
        lines.append("- ⚠️ 根目录下发现散落文件（应归入某个子目录或 _misc/）：" + "、".join(stats["root_unexpected"]))
    lines.append("")

    lines.append("## 历史归档")
    lines.append(f"- `_archive/`：共 {stats['archive_entries']} 项")

    text = "\n".join(lines) + "\n"
    readme_path = goal_output_dir(paths, goal_id) / "README.md"
    readme_path.write_text(text, encoding="utf-8")
    return text


def write_cycle_note(paths: "AgentPaths", goal_id: str, cycle_no: int, content: str) -> Path:
    """写入本轮总结笔记 notes/cycle_NNNN.md（方案 §3）。已存在则覆盖（正常
    不应重复写同一轮，防御性地允许覆盖而不是报错）。"""
    notes_dir = goal_notes_dir(paths, goal_id)
    notes_dir.mkdir(parents=True, exist_ok=True)
    note_path = notes_dir / f"cycle_{cycle_no:04d}.md"
    note_path.write_text(content, encoding="utf-8")
    return note_path


def read_recent_notes(paths: "AgentPaths", goal_id: str, limit: int = 3) -> list[dict]:
    """读取最近 limit 轮的总结笔记原文（方案 §3），按轮次从新到旧排序。
    返回 [{"cycle_no": int, "path": str, "content": str}, ...]，notes/
    不存在或为空时返回空列表。"""
    notes_dir = goal_notes_dir(paths, goal_id)
    if not notes_dir.is_dir():
        return []
    files = sorted(
        (f for f in notes_dir.iterdir() if f.is_file() and f.name.startswith("cycle_") and f.suffix == ".md"),
        key=lambda f: f.name,
        reverse=True,
    )
    out: list[dict] = []
    for f in files[:limit]:
        try:
            content = f.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            cycle_no = int(f.stem.split("_")[1])
        except (IndexError, ValueError):
            cycle_no = -1
        out.append({"cycle_no": cycle_no, "path": str(f.as_posix()), "content": content})
    return out


def archive_old_notes(paths: "AgentPaths", goal_id: str, keep_recent: int = 10) -> int:
    """notes/ 文件数超过 keep_recent 时，把较旧的挪进 notes/archive/
    （方案 §3 收尾），tidy 阶段调用。返回本次归档的文件数。"""
    notes_dir = goal_notes_dir(paths, goal_id)
    if not notes_dir.is_dir():
        return 0
    files = sorted(
        (f for f in notes_dir.iterdir() if f.is_file() and f.name.startswith("cycle_") and f.suffix == ".md"),
        key=lambda f: f.name,
    )
    if len(files) <= keep_recent:
        return 0
    archive_dir = notes_dir / "archive"
    archive_dir.mkdir(exist_ok=True)
    to_archive = files[: len(files) - keep_recent]
    moved = 0
    for f in to_archive:
        try:
            f.rename(archive_dir / f.name)
            moved += 1
        except OSError:
            continue
    return moved


def scratch_is_empty(paths: "AgentPaths", goal_id: str) -> bool:
    """判断 scratch/ 是否已清空（方案 §5/§7.1 tidy 核查项）——不存在或没有
    任何文件都视为"已清空"。"""
    scratch_dir = goal_scratch_dir(paths, goal_id)
    if not scratch_dir.is_dir():
        return True
    return not any(f.is_file() for f in scratch_dir.rglob("*"))


# ── manifest 读写 ─────────────────────────────────────────────────────────────

def _latest_path(base_dir: Path) -> Path:
    return base_dir / "latest.json"


def write_manifest(
    base_dir: Path,
    cycle_dir: Path,
    *,
    task_summary: str = "",
    started_at: float = 0.0,
    finished_at: float = 0.0,
    status: str = "completed",
    artifacts: Optional[list[dict]] = None,
    progress_note: str = "",
    extra: Optional[dict] = None,
) -> Path:
    """把这一轮/这一次触发的产出清单写入 `cycle_dir/manifest.json`，并更新
    `base_dir/latest.json` 指向这一轮。

    base_dir  — goal_output_base_dir()/cron_output_base_dir() 的返回值
    cycle_dir — allocate_cycle_dir()/allocate_run_dir() 的返回值，必须是
                base_dir 的直接子目录
    """
    manifest = {
        "version": MANIFEST_VERSION,
        "dir_name": cycle_dir.name,
        "task_summary": task_summary,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "artifacts": artifacts or [],
        "progress_note": progress_note,
    }
    if extra:
        manifest.update(extra)

    # previous_cycle_dir：manifest 自己就是一条链表，读上一轮的 latest.json
    # 拿到"这一轮之前"的目录名（写入 latest.json 之前读，避免读到自己）。
    prev = _read_latest_pointer(base_dir)
    if prev and prev != cycle_dir.name:
        manifest["previous_cycle_dir"] = str((base_dir / prev).as_posix())
    else:
        manifest["previous_cycle_dir"] = None

    cycle_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(cycle_dir / "manifest.json", manifest)
    atomic_write_json(_latest_path(base_dir), {
        "latest_dir": cycle_dir.name,
        "updated_at": time.time(),
    })
    return cycle_dir / "manifest.json"


def _read_latest_pointer(base_dir: Path) -> Optional[str]:
    path = _latest_path(base_dir)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        latest_dir = d.get("latest_dir")
        return latest_dir if latest_dir else None
    except (OSError, json.JSONDecodeError):
        return None


def read_latest_manifest(base_dir: Path) -> Optional[dict]:
    """读 `base_dir/latest.json` 拿到最新一轮目录名，再读该目录下的
    manifest.json。没有任何历史轮次（latest.json 不存在/损坏，或指向的
    manifest.json 缺失）时返回 None——调用方据此判断"没有上一轮产出"。
    """
    latest_dir = _read_latest_pointer(base_dir)
    if not latest_dir:
        return None
    manifest_path = base_dir / latest_dir / "manifest.json"
    try:
        d = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    d.setdefault("previous_cycle_dir", None)
    d["_dir"] = str((base_dir / latest_dir).as_posix())
    return d


def read_all_manifests(base_dir: Path) -> list[dict]:
    """[goal_execution_spec_generation_plan.md §5 `overall_completion_criteria`
    消费] 读取 `base_dir` 下全部子目录（`cycle_%04d/` / `run_%04d/`）的
    `manifest.json`，按目录名排序（等价于按时间顺序，命名规则本身是零填充
    序号）返回。用于"整个 Goal 能否关闭"这类需要看**全部**历史产出、而不是
    只看最新一轮的场景，与只取 `latest.json` 指针的 `read_latest_manifest()`
    互补——那个是"跨轮传递上一轮摘要"用的，这个是"回顾整个 Goal 做过什么"
    用的，两者读取的数据源相同但用途不同，不合并成一个函数。

    目录不存在或没有任何 manifest.json 时返回空列表，不抛异常。
    """
    if not base_dir.is_dir():
        return []
    out: list[dict] = []
    for child in sorted(base_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        try:
            d = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        d["_dir"] = str(child.as_posix())
        out.append(d)
    return out


# ── prompt 注入格式化 ─────────────────────────────────────────────────────────

def format_manifest_for_prompt(manifest: dict) -> str:
    """把 manifest 的 artifacts/progress_note 格式化成几行文本，供
    {{previous_output}} 占位符注入。"""
    lines: list[str] = []
    task_summary = (manifest.get("task_summary") or "").strip()
    if task_summary:
        lines.append(f"上轮任务：{task_summary}")
    artifacts = manifest.get("artifacts") or []
    if artifacts:
        lines.append("产出文件：")
        for a in artifacts:
            path = a.get("path", "") if isinstance(a, dict) else str(a)
            desc = a.get("description", "") if isinstance(a, dict) else ""
            if not path:
                continue
            lines.append(f"- {path}" + (f"：{desc}" if desc else ""))
    progress_note = (manifest.get("progress_note") or "").strip()
    if progress_note:
        lines.append(f"备注：{progress_note}")
    return "\n".join(lines)


__all__ = [
    "MANIFEST_VERSION",
    "outputs_root",
    "goal_output_base_dir",
    "cron_output_base_dir",
    "allocate_cycle_dir",
    "allocate_run_dir",
    "allocate_objective_dir",
    "write_manifest",
    "read_latest_manifest",
    "read_all_manifests",
    "format_manifest_for_prompt",
    # 新目录模型（goal_output_directory_and_execution_phase_redesign_plan.md）
    "OUTPUT_RESERVED_NAMES",
    "SCRIPTS_RESERVED_NAMES",
    "goal_output_dir",
    "goal_notes_dir",
    "goal_spec_dir",
    "goal_scratch_dir",
    "ensure_output_skeleton",
    "scan_output_structure",
    "render_output_readme",
    "write_cycle_note",
    "read_recent_notes",
    "archive_old_notes",
    "scratch_is_empty",
]
