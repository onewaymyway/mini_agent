"""novel_writer/workspace.py — workspace-manager 的代码化实现。

对应 skills/workspace-manager：确定性工作（建目录、路径查询、命名规范、
项目恢复/体检）不需要 LLM，直接由 Python 完成（方案第 1.1 节"执行器=Python"）。

要点：
  * 每本小说一个工作目录，所有内容都在里面；skill 标准的 6 个中文目录 +
    本项目自己的系统目录 `_studio/`（方案第 3 节）。
  * `PATHS` 是路径的**唯一来源**。skills 原文里存在路径写法不一致
    （`project/summaries`、`novel_blueprint.json`、未带前缀的 `idea_bank.json`
    / `simulations/`），统一以这里为准，并通过 `paths_table_markdown()`
    渲染进每一步 prompt 覆盖冲突写法——不改 skill 原文。
  * 初始化是幂等的：对已有工作目录再次初始化 = 恢复（只补缺失文件）。
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import settings as settings_mod
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json

SCHEMA_VERSION = "1.0"

# skill 标准目录（相对工作目录），创建顺序即展示顺序
STANDARD_DIRS: List[str] = [
    "大纲规划/人物设定/人设卡",
    "大纲规划/人物设定/snapshots",
    "大纲规划/世界观设定/地点详情",
    "大纲规划/线索设定",
    "大纲规划/其他设定",
    "正文/_drafts",
    "一致性检查/幕次健康报告",
    "修订记录",
    "章节总结",
    "项目状态/memory/snapshots",
    "推演记录",  # plot-simulator 的 simulations/ 在此补定义
    "导出",
]

# 本项目自己的系统目录（同样在工作目录内，方案第 3 节）
STUDIO_DIR = "_studio"
STUDIO_SUBDIRS: List[str] = [
    "inbox",
    "answers",
    "author_notes",
    "steps",
    "snapshots",
]

# 路径模板：键 -> 相对工作目录的路径模板（用 {name} 占位）。
# 前半部分与 workspace-manager 的路径表一致；标注 [补定义] 的是 skills 里
# 提到文件名但没给出完整路径的条目。
PATHS: Dict[str, str] = {
    # 核心蓝图
    "blueprint": "大纲规划/小说蓝图.json",
    "blueprint_readable": "大纲规划/章节大纲.md",
    "theme_design": "大纲规划/主题设计.md",
    "pacing_design": "大纲规划/节奏规划.md",
    "opening_design": "大纲规划/前三章开篇设计.md",
    # 人物设定
    "characters": "大纲规划/人物设定/characters.json",
    "behavior_index": "大纲规划/人物设定/behavior_index.json",
    "character_card": "大纲规划/人物设定/人设卡/{name}.md",
    "character_snapshot": "大纲规划/人物设定/snapshots/ch_{n}.json",
    # 世界观设定
    "world_bible": "大纲规划/世界观设定/world_bible.json",
    "world_changelog": "大纲规划/世界观设定/world_changelog.md",
    "location_detail": "大纲规划/世界观设定/地点详情/{name}.md",
    "world_usage_report": "大纲规划/世界观设定/world_usage_report_act{n}.md",
    # 线索设定
    "plot_tracker": "大纲规划/线索设定/plot_tracker.json",
    "foreshadowing_list": "大纲规划/线索设定/伏笔注册表.md",
    "thread_status": "大纲规划/线索设定/故事线状态.md",
    "tension_curve": "大纲规划/线索设定/tension_curve.md",
    "decision_log": "大纲规划/线索设定/decision_log.json",
    # 其他设定
    "style_profile": "大纲规划/其他设定/style_profile.json",
    "style_examples": "大纲规划/其他设定/style_examples.json",
    "word_frequency": "大纲规划/其他设定/word_frequency.json",
    "style_check_log": "大纲规划/其他设定/style_check_log.md",
    "word_count": "大纲规划/其他设定/word_count.json",
    "word_count_log": "大纲规划/其他设定/word_count_log.md",
    "idea_bank": "大纲规划/其他设定/idea_bank.json",  # [补定义]
    # 正文
    "chapter_text": "正文/第{nn}章 {title}.md",
    "chapter_drafts_dir": "正文/_drafts",
    # 一致性检查
    "review_report": "一致性检查/第{nn}章_检查报告.md",
    "reader_report": "一致性检查/第{nn}章_读者体验报告.md",
    "act_health_report": "一致性检查/幕次健康报告/第{act}幕_健康报告.md",
    "act_style_report": "一致性检查/幕次健康报告/第{act}幕_风格对比.md",
    "act_pacing_report": "一致性检查/幕次健康报告/第{act}幕_节奏.md",
    "act_theme_report": "一致性检查/幕次健康报告/第{act}幕_主题.md",
    # 修订记录
    "revision_log": "修订记录/第{nn}章_修订记录.md",
    "blueprint_changelog": "修订记录/蓝图变更日志.md",
    # 章节总结
    "chapter_summary": "章节总结/第{nn}章_总结.md",
    "chapter_index": "章节总结/全书章节索引.md",
    # 项目状态
    "progress": "项目状态/progress.json",
    "status_overview": "项目状态/当前状态概览.md",
    "workspace_config": "项目状态/workspace_config.json",
    "system_state": "项目状态/system_state.json",
    "memory_summary": "项目状态/memory/ch_{n}.json",
    "memory_snapshot": "项目状态/memory/snapshots/snapshot_ch{n}.json",
    "memory_index": "项目状态/memory/search_index.json",
    "context_pack": "项目状态/memory/context_ch{n}.md",
    "style_hint": "项目状态/memory/style_hint_ch{n}.md",
    "chapter_tasks": "项目状态/memory/tasks_ch{n}.json",
    "quota_file": "项目状态/memory/quota_ch{n}.json",
    "machine_report": "一致性检查/第{nn}章_机器校验.json",
    "revision_diag": "修订记录/第{nn}章_修订诊断.md",
    # 推演记录 / 导出
    "simulations_dir": "推演记录",  # [补定义] plot-simulator 的 simulations/
    "export_dir": "导出",
    # M6
    "candidate_alt": "正文/_drafts/第{nn}章_候选B.md",          # 精品档：best-of-N 的第二稿
    "best_pick_check": "一致性检查/第{nn}章_择优机检.json",   # 择优前的机器校验结果（供 LLM 参考）
    "best_pick_report": "一致性检查/第{nn}章_择优.json",     # LLM 的择优结论
    "patrol_report": "修订记录/第{nn}章_巡检报告.md",            # 精品档：每 10 章巡检
    "ending_design": "大纲规划/结局设计.md",
    "final_report": "一致性检查/全书终审报告.md",
    # 本项目系统文件
    "studio_manifest": f"{STUDIO_DIR}/manifest.json",
    "studio_settings": f"{STUDIO_DIR}/settings.json",
    "studio_state": f"{STUDIO_DIR}/pipeline_state.json",
    "studio_control": f"{STUDIO_DIR}/control.json",
    "studio_worker": f"{STUDIO_DIR}/worker.json",
    "studio_usage": f"{STUDIO_DIR}/usage.jsonl",
    "studio_log": f"{STUDIO_DIR}/run.log",
    "studio_steps_dir": f"{STUDIO_DIR}/steps",
    "studio_intake": f"{STUDIO_DIR}/intake.json",
    "studio_inbox": f"{STUDIO_DIR}/inbox",
    "studio_requests": f"{STUDIO_DIR}/requests",
    "intake_note": f"{STUDIO_DIR}/intake_note.md",
}

# 项目恢复/体检时检查的关键文件：(路径键, 说明, 是否在规划完成后才需要)
KEY_FILES: List[tuple] = [
    ("blueprint", "小说蓝图", True),
    ("characters", "人物档案", True),
    ("world_bible", "世界圣经", True),
    ("plot_tracker", "伏笔/线索账本", True),
    ("word_count", "字数档案", True),
    ("style_profile", "风格指纹（前三章确认后建立）", False),
]

_ILLEGAL_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_CHAPTER_FILE_RE = re.compile(r"^第(\d+)章(?:\s+(.*))?\.md$")


class WorkspaceError(RuntimeError):
    pass


def sanitize_name(name: str, *, what: str = "名称") -> str:
    """去掉文件名非法字符（`? * : / \\` 等，skill 命名规范要求），空则报错。"""
    cleaned = _ILLEGAL_NAME_CHARS.sub("", str(name or "")).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise WorkspaceError(f"{what}为空或只包含非法字符")
    return cleaned


def count_words(text: str) -> int:
    """章节字数：去掉 Markdown 标题行（`# ...`）后，统计非空白字符数。

    中文小说按"字"计数；英文单词按字符计会偏大，M0 不处理（本项目面向中文创作）。
    """
    body = "\n".join(line for line in str(text or "").splitlines() if not line.lstrip().startswith("#"))
    return len(re.sub(r"\s+", "", body))


def chapter_number_width(total_chapters: Optional[int]) -> int:
    """章节号位数：超过 99 章改三位（workspace-manager 命名规范）。"""
    return 3 if (total_chapters or 0) > 99 else 2


def chapter_filename(n: int, title: str, total_chapters: Optional[int] = None) -> str:
    width = chapter_number_width(total_chapters)
    safe = sanitize_name(title, what="章节标题")
    return f"第{n:0{width}d}章 {safe}.md"


@dataclass
class ChapterFile:
    number: int
    title: str
    path: Path
    words: int = 0


@dataclass
class HealthReport:
    ok: bool
    workspace: str
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "workspace": self.workspace,
            "problems": self.problems,
            "warnings": self.warnings,
            "checks": self.checks,
        }


class Workspace:
    """一本小说的工作目录。所有路径查询都经过这里，skill/步骤不得硬编码路径。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    # ── 路径查询 ───────────────────────────────────────────────────────────
    def path(self, key: str, **fmt: Any) -> Path:
        """按路径键取绝对路径；模板里的占位符用关键字参数填充。

        `nn` 会被格式化为两位（或三位）数：传 `nn=3` 得到 `03`（超过 99 章的书
        请改用 `chapter_path()`，它会按总章数决定位数）。
        """
        if key not in PATHS:
            raise KeyError(f"未知路径键 {key!r}")
        template = PATHS[key]
        if "{nn}" in template and isinstance(fmt.get("nn"), int):
            fmt["nn"] = f"{fmt['nn']:02d}"
        try:
            rel = template.format(**fmt)
        except KeyError as exc:
            raise KeyError(f"路径 {key!r} 缺少参数 {exc.args[0]!r}（模板：{template}）") from None
        return self.root / rel

    def chapter_path(self, n: int, title: str, total_chapters: Optional[int] = None) -> Path:
        return self.root / "正文" / chapter_filename(n, title, total_chapters)

    @property
    def studio(self) -> Path:
        return self.root / STUDIO_DIR

    def step_dir(self, step_id: str, run: int) -> Path:
        return self.path("studio_steps_dir") / sanitize_name(step_id, what="步骤 id").replace(" ", "_") / f"run_{run}"

    # ── 判定 / 读取 ────────────────────────────────────────────────────────
    def exists(self) -> bool:
        return self.root.is_dir()

    def is_initialized(self) -> bool:
        return self.path("studio_manifest").is_file() and self.path("workspace_config").is_file()

    def manifest(self) -> Dict[str, Any]:
        return read_json(self.path("studio_manifest"), default={}) or {}

    def workspace_config(self) -> Dict[str, Any]:
        return read_json(self.path("workspace_config"), default={}) or {}

    def settings(self) -> Dict[str, Any]:
        return settings_mod.load_settings(self.path("studio_settings"))

    def save_settings(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return settings_mod.save_settings(self.path("studio_settings"), data)

    # ── 初始化（幂等）──────────────────────────────────────────────────────
    def init(
        self,
        title: str,
        *,
        gear: str = "G0",
        quality: str = "standard",
        total_chapters_planned: int = 0,
        extra_manifest: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建标准目录与初始文件。已存在的文件一律不覆盖（幂等 = 恢复）。

        目录已存在但不是本项目的工作目录且非空时拒绝初始化，避免把小说文件
        撒进用户的无关目录。
        """
        safe_title = sanitize_name(title, what="书名")
        if self.root.exists() and not self.root.is_dir():
            raise WorkspaceError(f"路径已存在但不是目录：{self.root}")
        if self.root.is_dir() and not self.is_initialized() and any(self.root.iterdir()):
            raise WorkspaceError(
                f"目录非空且不是 novel_writer 工作目录，拒绝初始化：{self.root}"
                "（请选择空目录或一个不存在的新目录）"
            )
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise WorkspaceError(f"工作目录不可写：{self.root}（{exc}）") from exc

        created_dirs: List[str] = []
        for rel in STANDARD_DIRS + [f"{STUDIO_DIR}/{sub}" for sub in STUDIO_SUBDIRS] + [STUDIO_DIR]:
            d = self.root / rel
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)
                created_dirs.append(rel)

        now = _dt.datetime.now().astimezone()
        created_files: List[str] = []

        def _write_text_if_missing(key: str, text: str) -> None:
            p = self.path(key)
            if not p.exists():
                atomic_write_text(p, text)
                created_files.append(PATHS[key])

        def _write_json_if_missing(key: str, data: Any) -> None:
            p = self.path(key)
            if not p.exists():
                atomic_write_json(p, data)
                created_files.append(PATHS[key])

        _write_text_if_missing("chapter_index", "# 全书章节索引\n\n| 章节 | 标题 | 一句话总结 | 字数 |\n|---|---|---|---|\n")
        _write_text_if_missing("status_overview", f"# 当前状态概览\n\n《{safe_title}》项目刚创建（{now:%Y-%m-%d %H:%M}）。\n")
        _write_text_if_missing("blueprint_changelog", "# 蓝图变更日志\n\n")
        _write_json_if_missing(
            "workspace_config",
            {
                "workspace": str(self.root),
                "novel_title": safe_title,
                "created": now.date().isoformat(),
                "last_accessed": now.date().isoformat(),
                "total_chapters_planned": int(total_chapters_planned or 0),
                "chapters_completed": 0,
                "schema_version": SCHEMA_VERSION,
            },
        )
        manifest: Dict[str, Any] = {
            "schema_version": 1,
            "title": safe_title,
            "created_at": now.isoformat(timespec="seconds"),
            "template": "novel",
            "app": "novel_writer",
        }
        manifest.update(extra_manifest or {})
        _write_json_if_missing("studio_manifest", manifest)
        _write_json_if_missing("studio_state", {"schema_version": 1, "steps": {}})
        if not self.path("studio_settings").exists():
            cfg = settings_mod.validate_settings({"gear": gear, "quality": quality})
            atomic_write_json(self.path("studio_settings"), cfg)
            created_files.append(PATHS["studio_settings"])

        return {
            "workspace": str(self.root),
            "title": safe_title,
            "created_dirs": created_dirs,
            "created_files": created_files,
            "already_initialized": not created_dirs and not created_files,
        }

    # ── 章节扫描 / 恢复 ────────────────────────────────────────────────────
    def list_chapters(self) -> List[ChapterFile]:
        """扫描 `正文/` 下符合命名规范的章节文件，按章号排序。"""
        out: List[ChapterFile] = []
        body_dir = self.root / "正文"
        if not body_dir.is_dir():
            return out
        for p in body_dir.iterdir():
            m = _CHAPTER_FILE_RE.match(p.name)
            if not (p.is_file() and m):
                continue
            try:
                words = count_words(p.read_text(encoding="utf-8"))
            except OSError:
                words = 0
            out.append(ChapterFile(number=int(m.group(1)), title=(m.group(2) or "").strip(), path=p, words=words))
        out.sort(key=lambda c: c.number)
        return out

    def recover(self) -> Dict[str, Any]:
        """项目恢复（workspace-manager）：扫描现有文件，给出进度与关键文件检查。"""
        if not self.is_initialized():
            raise WorkspaceError(f"不是已初始化的 novel_writer 工作目录：{self.root}")
        cfg = self.workspace_config()
        blueprint = read_json(self.path("blueprint"), default=None)
        total = None
        target_length = None
        if isinstance(blueprint, dict):
            total = (blueprint.get("structure") or {}).get("total_chapters")
            target_length = (blueprint.get("meta") or {}).get("target_length")
        total = total or cfg.get("total_chapters_planned") or None
        chapters = self.list_chapters()
        total_words = sum(c.words for c in chapters)
        files = []
        for key, label, needed_after_plan in KEY_FILES:
            present = self.path(key).is_file()
            files.append({"key": key, "label": label, "path": PATHS[key], "present": present, "needed_after_plan": needed_after_plan})
        return {
            "workspace": str(self.root),
            "title": cfg.get("novel_title") or self.manifest().get("title", ""),
            "chapters_written": len(chapters),
            "last_chapter": chapters[-1].number if chapters else 0,
            "total_chapters_planned": total,
            "total_words": total_words,
            "target_length": target_length,
            "progress_pct": round(len(chapters) / total * 100, 1) if total else None,
            "files": files,
            "blueprint_exists": isinstance(blueprint, dict),
        }

    def touch_accessed(self) -> None:
        """刷新 workspace_config.last_accessed（失败静默，不影响主流程）。"""
        try:
            cfg = self.workspace_config()
            if cfg:
                cfg["last_accessed"] = _dt.date.today().isoformat()
                atomic_write_json(self.path("workspace_config"), cfg)
        except OSError:
            pass

    # ── 体检 ───────────────────────────────────────────────────────────────
    def health(self) -> HealthReport:
        """不依赖 LLM/网络的体检：目录完整性、JSON 可解析、章节命名、设置合法。"""
        rep = HealthReport(ok=True, workspace=str(self.root))

        def check(name: str, ok: bool, detail: str = "", *, level: str = "problem") -> None:
            rep.checks.append({"name": name, "ok": ok, "detail": detail})
            if not ok:
                (rep.problems if level == "problem" else rep.warnings).append(f"{name}：{detail}" if detail else name)

        if not self.exists():
            rep.ok = False
            rep.problems.append(f"工作目录不存在：{self.root}")
            return rep
        check("已初始化", self.is_initialized(), "缺少 _studio/manifest.json 或 项目状态/workspace_config.json")
        for rel in STANDARD_DIRS + [f"{STUDIO_DIR}/{sub}" for sub in STUDIO_SUBDIRS]:
            check(f"目录 {rel}", (self.root / rel).is_dir(), "缺失（可重新初始化补齐）")
        for key in ("studio_manifest", "workspace_config", "studio_settings", "studio_state"):
            p = self.path(key)
            if p.exists():
                check(f"JSON {PATHS[key]}", read_json(p, default=None) is not None, "无法解析")
        raw_settings = read_json(self.path("studio_settings"), default=None)
        if isinstance(raw_settings, dict):
            try:
                settings_mod.validate_settings(raw_settings)
                check("设置合法", True)
            except settings_mod.SettingsError as exc:
                check("设置合法", False, str(exc))
        # 章节命名与连续性
        body_dir = self.root / "正文"
        if body_dir.is_dir():
            bad = [p.name for p in body_dir.iterdir() if p.is_file() and p.suffix == ".md" and not _CHAPTER_FILE_RE.match(p.name)]
            check("正文命名规范", not bad, f"不符合 `第nn章 标题.md`：{bad[:5]}", level="warning")
            nums = [c.number for c in self.list_chapters()]
            gaps = [i for i in range(1, (nums[-1] if nums else 0)) if i not in set(nums)]
            check("章节连续", not gaps, f"缺失章号：{gaps[:10]}", level="warning")
        # 规划完成后应有的关键文件
        if self.path("blueprint").is_file():
            for key, label, needed in KEY_FILES:
                if needed and not self.path(key).is_file():
                    check(f"关键文件 {label}", False, f"缺失 {PATHS[key]}", level="warning")
        rep.ok = not rep.problems
        return rep

    # ── prompt 注入用的路径表 ──────────────────────────────────────────────
    def paths_table_markdown(self, keys: Optional[List[str]] = None) -> str:
        """渲染成注入每步 prompt 的统一路径表（覆盖 skills 原文中的冲突写法）。"""
        lines = [
            f"工作目录 WORKSPACE = `{self.root}`",
            "",
            "所有文件必须写入下列路径（相对 WORKSPACE），**不得使用 skill 文档里其它写法的路径**"
            "（如 `project/summaries/`、`project/state/`、`novel_blueprint.json`、裸 `simulations/`）：",
            "",
            "| 内容 | 相对路径 |",
            "|---|---|",
        ]
        for key in keys or [k for k in PATHS if not k.startswith("studio_")]:
            lines.append(f"| `{key}` | `{PATHS[key]}` |")
        return "\n".join(lines)


def workspace_dir_name(title: str, *, now: Optional[_dt.datetime] = None) -> str:
    """默认工作目录名：`<书名>_<yyyymmddHHMM>`。"""
    now = now or _dt.datetime.now()
    return f"{sanitize_name(title, what='书名')}_{now:%Y%m%d%H%M}"
