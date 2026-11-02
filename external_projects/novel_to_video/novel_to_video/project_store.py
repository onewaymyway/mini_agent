"""novel_to_video/project_store.py — 项目目录、状态文件读写与章节范围解析。

约定：
  * 所有 JSON/YAML 状态文件走 write_*_atomic（临时文件 + os.replace），防止中途崩溃留下半截文件；
  * 项目目录名 = `<书名>_<YYYYmmdd_HHMMSS>`，与 novel_writer 的 `小说名_日期` 习惯一致；
  * 章节目录统一三位补零（`chapters/001/`），支持超过 99 章。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .source_import import ImportResult

SCHEMA_VERSION = 1
PROGRESS_FILE = "PROGRESS.md"

DEFAULT_ART_STYLE = ("cinematic realistic, dystopian sci-fi, muted cold palette, "
                     "soft volumetric light, high detail")

DEFAULT_AUTO_FIX_RETRIES = 5   # 校验失败后自动修正的默认次数；项目里显式写 0 表示不修正、直接失败
MAX_AUTO_FIX_RETRIES = 5       # 界面与接口允许设置的上限

# 视频接口的等待与重试（可在挡位页调整）
DEFAULT_VIDEO_WAIT = {"max_wait_sec": 3600, "max_retries": 5, "queue_wait_sec": 3600}

DEFAULT_OPTIONS: Dict[str, Any] = {
    "art_style": DEFAULT_ART_STYLE,
    "providers": {"image": "agnes", "text": "miniagent", "video": "agnes", "tts": "edge-tts"},
    "target_chapter_duration_sec": 90,  # 单章目标时长（秒）；0 = 不限制时长与字数，按原文实际情况生成
    "script_max_retries": DEFAULT_AUTO_FIX_RETRIES,  # 剧本校验失败后，把问题反馈给模型自动修正的最多次数
    "shots_max_retries": DEFAULT_AUTO_FIX_RETRIES,   # 镜头规划校验失败后，同上
    "orientation": "portrait",
    "aspect_ratio": "9:16",
    # 配音引擎链：先 engine，失败后降级到 fallback（null = 不降级）。与 novel-video-studio 默认一致。
    "video": dict(DEFAULT_VIDEO_WAIT),
    "tts": {"engine": "cosyvoice", "fallback": "edge-tts", "voice": None,
            "cosyvoice_model_dir": None, "cosyvoice_timeout_sec": 60, "edge_tts_timeout_sec": 45,
            "edge_tts_proxy": None},   # None = 系统代理；"direct" = 强制直连；或指定代理地址
    "gear": "G0",
    "budget": {"max_video_calls": None, "max_image_calls": None, "max_minutes": None},
}



def auto_fix_retries(project_dir: Path, key: str) -> int:
    """读取 script_max_retries / shots_max_retries。键不存在（旧项目）用默认值；写了 0 就是 0。"""
    try:
        val = read_json(Path(project_dir) / "project.json").get(key)
    except (OSError, ValueError):
        val = None
    return DEFAULT_AUTO_FIX_RETRIES if val is None else max(0, min(MAX_AUTO_FIX_RETRIES, int(val)))


CHAPTER_STATUSES = ("pending", "planned", "in_review", "done", "stale")


# ---------------------------------------------------------------- 原子写入

REPLACE_RETRIES = 8


def replace_with_retry(src, dst) -> None:
    """os.replace 在 Windows 上会因为目标文件被其他进程短暂占用（界面读取、杀毒扫描、索引）而报
    PermissionError [WinError 5]。这里带退避重试；全部失败才抛出。"""
    import time as _time
    last: Exception | None = None
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last = exc
            _time.sleep(0.05 * (attempt + 1))
    raise last  # type: ignore[misc]


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: Any) -> None:
    _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def working_source_dir(project_dir: Path) -> Path:
    """读取导出内容时使用的目录：转义导出为项目内规范化副本，否则为原始导出目录。
    所有读取正文、人设卡的代码都应通过此函数，而不是直接使用 manifest 的 source_dir。"""
    man = read_json(project_dir / "source" / "manifest.json")
    if man.get("working_dir"):
        return project_dir / man["working_dir"]
    return Path(man["source_dir"])


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class _NoAliasDumper(yaml.SafeDumper):
    """同一个列表对象被多处引用时，PyYAML 默认写成 &id001 / *id001 锚点与别名，肉眼看像是“内容重复”，
    手工编辑一处还会影响其他处。这里关闭别名，每处都完整展开。"""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def write_yaml_atomic(path: Path, data: Any) -> None:
    _write_atomic(path, yaml.dump(data, Dumper=_NoAliasDumper, allow_unicode=True, sort_keys=False))


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def append_progress(project_dir: Path, text: str, when: Optional[datetime] = None) -> None:
    """PROGRESS.md 只追加：记录决策、反馈、补建与坑，不改写历史。"""
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
    path = project_dir / PROGRESS_FILE
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"\n## {stamp}\n{text.rstrip()}\n")


# ---------------------------------------------------------------- 章节范围

def parse_chapter_spec(spec: Optional[str], available: List[int]) -> List[int]:
    """解析章节范围表达式。

    None / "" / "all" / "全部" → 全部章节（默认）。
    其余支持：单章 `5`、区间 `1-10`、开区间 `20-`、`-5`、逗号/空格分隔组合 `1-3,7,10-`。
    """
    if not available:
        raise ValueError("没有可用章节")
    if spec is None or not spec.strip() or spec.strip().lower() in ("all", "全部"):
        return sorted(available)
    avail = set(available)
    lo_all, hi_all = min(avail), max(avail)
    chosen: set = set()
    for part in re.split(r"[,，\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)?\s*-\s*(\d+)?", part)
        if m:
            lo = int(m.group(1)) if m.group(1) else lo_all
            hi = int(m.group(2)) if m.group(2) else hi_all
            if lo > hi:
                raise ValueError(f"章节范围倒置：{part}")
            chosen.update(n for n in range(lo, hi + 1) if n in avail)
        elif part.isdigit():
            n = int(part)
            if n not in avail:
                raise ValueError(f"章节不存在：{n}")
            chosen.add(n)
        else:
            raise ValueError(f"无法识别的章节表达式：{part}")
    if not chosen:
        raise ValueError("没有选中任何章节")
    return sorted(chosen)


# ---------------------------------------------------------------- 项目创建

def chapter_dir_name(no: int) -> str:
    return f"{no:03d}"


def _slug(title: str) -> str:
    slug = re.sub(r'[\\/:*?"<>|\s]+', "_", title.strip()).strip("_")
    return slug or "novel"


def _unique_dir(projects_root: Path, base: str) -> Path:
    candidate = projects_root / base
    n = 2
    while candidate.exists():
        candidate = projects_root / f"{base}_{n}"
        n += 1
    return candidate


def create_project(
    projects_root: Path,
    result: ImportResult,
    *,
    title: Optional[str] = None,
    chapter_spec: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Path:
    """依据导入结果创建项目目录并落盘全部全书级文件。调用前应确认 result.ok。"""
    if not result.ok:
        raise ValueError("导入结果存在阻断性错误，不能创建项目")
    now = now or datetime.now()
    book_title = (title or result.source_title).strip() or "novel"
    opts = json.loads(json.dumps(DEFAULT_OPTIONS))
    opts.update(options or {})

    all_nos = [c["no"] for c in result.chapters]
    selected = set(parse_chapter_spec(chapter_spec, all_nos))

    projects_root.mkdir(parents=True, exist_ok=True)
    proj = _unique_dir(projects_root, f"{_slug(book_title)}_{now.strftime('%Y%m%d_%H%M%S')}")
    for sub in ("source", "global/assets", "chapters", "exports", "logs"):
        (proj / sub).mkdir(parents=True, exist_ok=True)
    for no in sorted(selected):
        (proj / "chapters" / chapter_dir_name(no)).mkdir(exist_ok=True)

    timestamp = now.isoformat(timespec="seconds")
    write_json_atomic(proj / "project.json", {
        "schema_version": SCHEMA_VERSION,
        "title": book_title,
        "source_dir": result.source_dir,
        "source_fingerprint": result.fingerprint,
        "created_at": timestamp,
        "updated_at": timestamp,
        **opts,
    })
    working_rel = None
    src_path = Path(result.source_dir)
    from .name_normalize import has_escape, normalize_tree
    if src_path.is_dir() and has_escape(src_path):
        # 转义导出：把规范化副本落盘到项目内，之后所有读取都指向它（导入时的临时副本会被删除）
        normalize_tree(src_path, proj / "source" / "normalized")
        working_rel = "source/normalized"
    write_json_atomic(proj / "source" / "manifest.json", {
        "schema_version": SCHEMA_VERSION,
        "source_dir": result.source_dir,
        "working_dir": working_rel,
        "fingerprint": result.fingerprint,
        "imported_at": timestamp,
        "stats": result.stats,
        "issues": [i.__dict__ for i in result.issues],
    })
    write_json_atomic(proj / "global" / "characters.json", {
        "schema_version": SCHEMA_VERSION, "characters": result.characters,
    })
    write_json_atomic(proj / "global" / "locations.json", {
        "schema_version": SCHEMA_VERSION, "locations": result.locations,
    })
    write_json_atomic(proj / "global" / "review.json", {
        "schema_version": SCHEMA_VERSION, "items": {},
    })
    write_yaml_atomic(proj / "chapters" / "index.yaml", {
        "schema_version": SCHEMA_VERSION,
        "chapters": [
            {
                "no": c["no"],
                "dir": chapter_dir_name(c["no"]),
                "title": c["title"],
                "char_count": c["char_count"],
                "selected": c["no"] in selected,
                "status": "pending",
                "source_file": c["source_file"],
                "summary_file": c["summary_file"],
            }
            for c in result.chapters
        ],
    })
    _write_atomic(proj / PROGRESS_FILE, f"# 项目进度日志：{book_title}\n")
    append_progress(proj, (
        f"- 从 novel_writer 导出目录导入：{result.stats['chapters']} 章，"
        f"{result.stats['characters']} 个角色，{result.stats['locations']} 个地点。\n"
        f"- 选中章节：{len(selected)} 章（{', '.join(str(n) for n in sorted(selected))}）。\n"
        f"- 导入警告 {result.stats['warnings']} 条，详见 source/manifest.json。"
    ), when=now)
    return proj


# ---------------------------------------------------------------- 读取

def load_project(project_dir: Path) -> Dict[str, Any]:
    project_dir = Path(project_dir)
    meta_path = project_dir / "project.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"不是 novel_to_video 项目（缺少 project.json）：{project_dir}")
    return {
        "dir": str(project_dir),
        "project": read_json(meta_path),
        "chapters": read_yaml(project_dir / "chapters" / "index.yaml").get("chapters", []),
        "characters": read_json(project_dir / "global" / "characters.json").get("characters", []),
        "locations": read_json(project_dir / "global" / "locations.json").get("locations", []),
    }


def list_project_dirs(projects_root: Path) -> List[Path]:
    if not projects_root.is_dir():
        return []
    return sorted(
        (p for p in projects_root.iterdir() if (p / "project.json").is_file()),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
