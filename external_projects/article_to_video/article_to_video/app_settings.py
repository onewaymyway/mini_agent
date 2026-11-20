"""app_settings.py — 统一设置（R4）：全局默认 + 项目覆盖。

生效值 = 内置默认 ⊕ 全局默认（<projects_root>/settings.json）⊕ 项目覆盖（project.json 中的同名字段）。

  * 项目 project.json 中的分组字段（settings / visual / compose / branding / bgm / pipeline / logs）
    只保存「与继承值不同」的叶子字段（稀疏覆盖）。旧项目中已存在的完整字段视为显式覆盖，行为不变。
  * 身份字段（作者、来源标题、来源链接、BGM 文件路径）只属于项目本身，不参与全局继承。
  * 读取方使用 load_effective_project()（生效值视图）；写入方仍使用 load_project()（原始内容），
    并通过 save_group / update_group 只写覆盖值，避免把生效值整块写回。
  * 所有分组都经由各自的规范化函数校验，全局与项目两层相同。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .audio.config import normalize_tts
from .paths import projects_root
from .project_store import (
    normalize_branding,
    normalize_settings,
    now_iso,
    read_json,
    write_json_atomic,
)
from .compose.config import normalize_compose
from .visual.config import normalize_visual

GLOBAL_FILE = "settings.json"
GEARS = ("G0", "G1", "G2", "G3")
OUTLINE_ENGINES = ("llm", "rules")
GEN_MODES = ("classic", "deck")   # classic：现有方案（口播稿→分镜→画面）；deck：新方案（幻灯片→讲解）
GROUPS = ("settings", "visual", "compose", "branding", "bgm", "pipeline", "logs")

# 身份字段：只属于项目本身，不写入全局默认，也不从全局继承
IDENTITY = {
    "branding": ("author", "author_handle", "source_title", "source_url"),
    "bgm": ("path",),
}


# ---------------------------------------------------------------- 规范化（每组一个）

def _norm_settings(raw: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    base, warn = normalize_settings(raw)
    tts, warn_t = normalize_tts((raw or {}).get("tts") if isinstance(raw, dict) else None)
    base["tts"] = tts
    return base, warn + warn_t


def _norm_bgm(raw: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    raw = raw if isinstance(raw, dict) else {}
    warnings: List[str] = []
    try:
        vol = min(1.0, max(0.0, float(raw.get("volume", 0.25))))
    except (TypeError, ValueError):
        vol = 0.25
        warnings.append("BGM 音量不是数字，已改用 0.25")
    path = str(raw.get("path") or "").strip().replace("\\", "/")
    if path.startswith("/") or ".." in Path(path).parts:
        path = ""
    return {"enabled": bool(raw.get("enabled", False)), "path": path, "volume": round(vol, 2)}, warnings


def _norm_pipeline(raw: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    raw = raw if isinstance(raw, dict) else {}
    warnings: List[str] = []
    gear = raw.get("gear", "G0")
    if gear not in GEARS:
        warnings.append(f"挡位 {gear!r} 不合法，已改用 G0")
        gear = "G0"
    engine = raw.get("outline_engine", "llm")
    if engine not in OUTLINE_ENGINES:
        warnings.append(f"大纲引擎 {engine!r} 不合法，已改用 llm")
        engine = "llm"
    try:
        max_images = max(0, int(raw.get("max_images", 0) or 0))
    except (TypeError, ValueError):
        max_images = 0
        warnings.append("生图次数上限不是整数，已改用 0（不限）")
    try:
        max_minutes = max(0.0, float(raw.get("max_minutes", 0.0) or 0.0))
    except (TypeError, ValueError):
        max_minutes = 0.0
        warnings.append("耗时上限不是数字，已改用 0（不限）")
    mode = raw.get("mode", "classic")
    if mode not in GEN_MODES:
        warnings.append(f"生成方案 {mode!r} 不合法，已改用现有方案 classic")
        mode = "classic"
    return {"gear": gear, "outline_engine": engine, "max_images": max_images,
            "max_minutes": round(max_minutes, 1), "mode": mode}, warnings


def _norm_logs(raw: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    raw = raw if isinstance(raw, dict) else {}
    warnings: List[str] = []
    try:
        lines = int(raw.get("max_lines", 0) or 0)
    except (TypeError, ValueError):
        lines = 0
        warnings.append("日志保留行数不是整数，已改用 0（不限）")
    if lines < 0:
        warnings.append("日志保留行数不能为负，已改用 0（不限）")
        lines = 0
    return {"max_lines": min(lines, 1_000_000)}, warnings


def _norm_branding(raw: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    return normalize_branding(raw)


_NORMALIZERS: Dict[str, Callable[[Optional[Dict[str, Any]]], Tuple[Dict[str, Any], List[str]]]] = {
    "settings": _norm_settings,
    "visual": normalize_visual,
    "compose": normalize_compose,
    "branding": _norm_branding,
    "bgm": _norm_bgm,
    "pipeline": _norm_pipeline,
    "logs": _norm_logs,
}


def normalize_group(group: str, raw: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    if group not in _NORMALIZERS:
        raise KeyError(f"未知的设置分组：{group}")
    return _NORMALIZERS[group](raw)


def builtin_group(group: str) -> Dict[str, Any]:
    return normalize_group(group, None)[0]


# ---------------------------------------------------------------- 小工具

def _merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


_SAME = object()


def diff(full: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """返回 full 中与 base 不同的叶子字段（嵌套结构）。相同的字段不出现。"""
    out: Dict[str, Any] = {}
    for k, v in full.items():
        if k not in base:
            out[k] = copy.deepcopy(v)
            continue
        b = base[k]
        if isinstance(v, dict) and isinstance(b, dict):
            sub = diff(v, b)
            if sub:
                out[k] = sub
        elif v != b:
            out[k] = copy.deepcopy(v)
    return out


def get_path(d: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_path(d: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _del_path(d: Dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    chain = [d]
    for part in parts[:-1]:
        nxt = chain[-1].get(part)
        if not isinstance(nxt, dict):
            return
        chain.append(nxt)
    chain[-1].pop(parts[-1], None)
    # 清理因此变空的中间字典
    for i in range(len(chain) - 1, 0, -1):
        if chain[i]:
            break
        chain[i - 1].pop(parts[i - 1], None)


# ---------------------------------------------------------------- 全局默认

def global_path(root: Optional[Path] = None) -> Path:
    return Path(root or projects_root()) / GLOBAL_FILE


def load_global_raw(root: Optional[Path] = None) -> Tuple[Dict[str, Any], List[str]]:
    """全局默认（稀疏）。文件缺失返回 {}；文件损坏时返回 {} 并给出警告（不会在读取时覆盖它）。"""
    path = global_path(root)
    if not path.is_file():
        return {}, []
    try:
        data = read_json(path)
    except Exception as exc:  # noqa: BLE001
        return {}, [f"全局设置文件无法读取，已忽略：{exc}"]
    if not isinstance(data, dict):
        return {}, ["全局设置文件格式不正确，已忽略"]
    return {g: v for g, v in data.items() if g in GROUPS and isinstance(v, dict)}, []


def save_global_group(group: str, value: Dict[str, Any], root: Optional[Path] = None) -> List[str]:
    """保存全局默认的一个分组（完整值；内部只存与内置默认不同的字段，身份字段不写入）。"""
    builtin = builtin_group(group)
    norm, warnings = normalize_group(group, value)
    for key in IDENTITY.get(group, ()):
        norm[key] = builtin.get(key)
    over = diff(norm, builtin)
    data, _ = load_global_raw(root)
    if over:
        data[group] = over
    else:
        data.pop(group, None)
    path = global_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data)
    return warnings


def global_sparse_group(group: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """全局默认的原始稀疏值（未规范化）。合并时使用它，规范化只在最后做一次。"""
    gl, _ = load_global_raw(root)
    return dict(gl.get(group) or {})


def inherited_group(group: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """未被项目覆盖时的值（规范化后）：内置默认 ⊕ 全局默认。它是覆盖值的比较基准。"""
    return normalize_group(group, global_sparse_group(group, root))[0]


# ---------------------------------------------------------------- 生效值

def effective_group(project_dir: Path, group: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """生效值：全局原始稀疏值 ⊕ 项目覆盖，最后统一规范化（内置默认在规范化时补齐）。"""
    raw = read_json(Path(project_dir) / "project.json").get(group)
    merged = _merge(global_sparse_group(group, root), raw if isinstance(raw, dict) else {})
    return normalize_group(group, merged)[0]


def effective_project(project_dir: Path, root: Optional[Path] = None) -> Dict[str, Any]:
    """项目原始内容，其中设置分组已替换为生效值（只用于读取，不要写回）。"""
    project = read_json(Path(project_dir) / "project.json")
    for group in GROUPS:
        project[group] = effective_group(project_dir, group, root)
    return project


def load_effective_project(project_dir: Path) -> Dict[str, Any]:
    return effective_project(Path(project_dir))


def source_of(project_dir: Path, group: str, dotted: str, root: Optional[Path] = None) -> str:
    """某个字段的来源：项目 / 全局 / 内置。"""
    raw = read_json(Path(project_dir) / "project.json").get(group)
    if isinstance(raw, dict) and get_path(raw, dotted) is not None:
        return "项目"
    gl, _ = load_global_raw(root)
    if get_path(gl.get(group, {}) or {}, dotted) is not None:
        return "全局"
    return "内置"


# ---------------------------------------------------------------- 项目覆盖的写入

def save_group(project_dir: Path, group: str, value: Dict[str, Any], now=None,
               root: Optional[Path] = None) -> List[str]:
    """保存项目的一个分组：value 为该分组的完整期望值；只写入与继承值不同的字段。"""
    project_dir = Path(project_dir)
    inh = inherited_group(group, root)
    norm, warnings = normalize_group(group, value)
    over = diff(norm, inh)
    project = read_json(project_dir / "project.json")
    project[group] = over
    project["updated_at"] = now_iso(now)
    write_json_atomic(project_dir / "project.json", project)
    return warnings


BUDGET_KEYS = ("gear", "max_images", "max_minutes")


def save_budget(project_dir: Path, *, gear: str, max_images: int, max_minutes: float, mode: Optional[str] = None,
                now=None, root: Optional[Path] = None) -> bool:
    """把驾驶舱的挡位与预算记为本项目的设置（写入 project.json 的 pipeline 分组，只记录与全局默认不同的部分）。

    值没有变化时不写文件，返回 False；有变化时写入并返回 True。
    """
    project_dir = Path(project_dir)
    cur = effective_group(project_dir, "pipeline", root)
    new = dict(cur)
    new.update(gear=gear, max_images=int(max_images), max_minutes=round(float(max_minutes), 1))
    if mode is not None:
        new["mode"] = mode
    if all(new[k] == cur[k] for k in BUDGET_KEYS + (("mode",) if mode is not None else ())):
        return False
    save_group(project_dir, "pipeline", new, now=now, root=root)
    return True


def update_group(project_dir: Path, group: str, mutate: Callable[[Dict[str, Any]], Dict[str, Any]],
                 now=None, root: Optional[Path] = None) -> List[str]:
    """读取生效值 → 交给 mutate 修改 → 保存覆盖值。"""
    current = effective_group(Path(project_dir), group, root)
    return save_group(project_dir, group, mutate(copy.deepcopy(current)), now=now, root=root)


def set_field(project_dir: Path, group: str, dotted: str, value: Any, now=None,
              root: Optional[Path] = None) -> List[str]:
    def mutate(cur: Dict[str, Any]) -> Dict[str, Any]:
        set_path(cur, dotted, value)
        return cur
    return update_group(project_dir, group, mutate, now=now, root=root)


def reset_field(project_dir: Path, group: str, dotted: str, now=None) -> None:
    """恢复为全局默认（删除该字段的项目覆盖）。"""
    project_dir = Path(project_dir)
    project = read_json(project_dir / "project.json")
    raw = project.get(group)
    if isinstance(raw, dict):
        _del_path(raw, dotted)
        project[group] = raw
    project["updated_at"] = now_iso(now)
    write_json_atomic(project_dir / "project.json", project)


def reset_group(project_dir: Path, group: str, now=None) -> None:
    project_dir = Path(project_dir)
    project = read_json(project_dir / "project.json")
    project[group] = {}
    project["updated_at"] = now_iso(now)
    write_json_atomic(project_dir / "project.json", project)


# ---------------------------------------------------------------- 修改影响（R4b）

def flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """嵌套字典展开为 {点分路径: 叶子值}；列表视为叶子。"""
    out: Dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def changed_paths(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    """两个生效分组之间值不同的字段路径（点分）。"""
    a, b = flatten(before), flatten(after)
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


# 字段 → 受影响的阶段（前缀匹配，先匹配到的优先）。依据第 6–8 节与 P1–P7 的产物依赖关系。
_IMPACT: List[Tuple[str, str]] = [
    ("settings.mode", "大纲、口播稿、配音、分镜、画面、合成与导出（整体内容变化）"),
    ("settings.persona", "口播稿、配音、分镜与合成（讲解视角）"),
    ("settings.target_minutes", "大纲、口播稿、配音、分镜、画面与合成（时长预算）"),
    ("settings.episode_max_minutes", "导出（分期方式）"),
    ("settings.video", "分镜（画面尺寸）、画面渲染、章合成与导出"),
    ("settings.tts.use_system_proxy", "无：只影响之后的配音请求，不改动已生成的产物"),
    ("settings.tts.chapter_card_sec", "只重建时间轴，再重新渲染画面、合成（不重新配音、不重写分镜、不重新生图）（章封面与旁白起点变化）"),
    ("settings.tts.lead_in_sec", "只重建时间轴，再重新渲染画面、合成（不重新配音、不重写分镜、不重新生图）（章首静音变化）"),
    ("settings.tts.sentence_gap_sec", "只重建时间轴，再重新渲染画面、合成（不重新配音、不重写分镜、不重新生图）（句间停顿变化）"),
    ("settings.tts.tail_sec", "只重建时间轴，再重新渲染画面、合成（不重新配音、不重写分镜、不重新生图）（章尾停顿变化）"),
    ("settings.llm", "无：只影响之后的大模型请求，不改动已生成的产物"),
    ("visual.images.use_system_proxy", "无：只影响之后的生图请求，不改动已生成的产物"),
    ("settings.tts", "配音、章合成与导出"),
    ("visual", "分镜（插图预算与风格）、画面渲染、章合成与导出"),
    ("compose.subtitles", "章合成与导出（字幕，不重渲染画面）"),
    ("compose", "章合成与导出（转场、进度条、章标签，不重渲染画面）"),
    ("branding", "成片导出的署名与水印（最后一步，不重渲染画面）"),
    ("bgm", "成片导出的背景音乐（最后一步，不重渲染画面）"),
    ("pipeline", "无：挡位与预算只影响之后的运行，不改动已生成的产物"),
    ("logs", "无：日志设置不影响生成结果"),
]


def affected_stages(group: str, path: str) -> str:
    key = f"{group}.{path}" if path else group
    for prefix, text in _IMPACT:
        if key == prefix or key.startswith(prefix + "."):
            return text
    return "后续阶段（请重新运行受影响的步骤）"
