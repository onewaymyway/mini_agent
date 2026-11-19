"""visual/images.py — 插图：提示词拼接（固定风格锚）、按内容哈希缓存、版本化、失败降级。

缓存：parts/<章>/images/index.json 记录每个场景的 sha、版本与文件；sha 不变则复用，不调用生图。
版本：同一场景重新生成时写入 <场景>_vNNN.png，旧版本保留。
降级：provider 为 None（未配置 key / 已关闭）或生图失败 → 返回 ok=False，渲染层按卡片绘制并记录原因。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

from .. import joblog
from ..project_store import read_json, write_json_atomic
from .config import aspect_ratio
from .prompting import PROMPT_BUILD_VERSION, compose_prompt


def build_prompt(scene: Dict[str, Any], style_anchor: str, orientation: str) -> str:
    """第三期：主体 + 场景 + 构图 + 风格锚 + 无文字约束（见 visual/prompting.py）。"""
    return compose_prompt(scene.get("image") or {}, style_anchor, orientation)


def image_sha(prompt: str, size: str, reference_sha: str) -> str:
    raw = "|".join([PROMPT_BUILD_VERSION, prompt, size, reference_sha])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_sha(path: Optional[Path]) -> str:
    if not path or not Path(path).is_file():
        return ""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def images_dir(pdir: Path, part_id: str) -> Path:
    return Path(pdir) / "parts" / part_id / "images"


def _index_path(pdir: Path, part_id: str) -> Path:
    return images_dir(pdir, part_id) / "index.json"


def load_index(pdir: Path, part_id: str) -> Dict[str, Any]:
    p = _index_path(pdir, part_id)
    return read_json(p) if p.is_file() else {}


def ensure_illustration(pdir: Path, part_id: str, scene: Dict[str, Any], *, provider, style_anchor: str,
                        orientation: str, size: str, reference: Optional[Path] = None) -> Dict[str, Any]:
    """返回 {ok, path, reused, prompt, error}。Cancelled 异常原样上抛（停止即刻生效）。"""
    prompt = build_prompt(scene, style_anchor, orientation)
    sha = image_sha(prompt, size, file_sha(reference))
    index = load_index(pdir, part_id)
    entry = index.get(scene["id"])
    if entry and entry.get("sha") == sha and Path(entry.get("file", "")).is_file():
        joblog.bound_log(f"插图 {scene['id']}：复用已生成的图片（未变化）")
        return {"ok": True, "path": Path(entry["file"]), "reused": True, "prompt": prompt, "error": None}
    if provider is None:
        joblog.bound_log(f"插图 {scene['id']}：未配置生图服务，改用卡片绘制", level="warn")
        return {"ok": False, "path": None, "reused": False, "prompt": prompt,
                "error": "未配置生图服务（缺少 API key 或已在设置中关闭）"}
    version = int(entry.get("version", 0)) + 1 if entry else 1
    out = images_dir(pdir, part_id) / f"{scene['id']}_v{version:03d}.png"
    refs = [reference] if reference and Path(reference).is_file() else None
    joblog.bound_log(f"插图 {scene['id']}：调用生图服务（第 {version} 版{'，带风格参考图' if refs else ''}）")
    res = provider.generate(prompt, out, ratio=aspect_ratio(orientation), size=size, reference_images=refs)
    if not res.ok:
        joblog.bound_log(f"插图 {scene['id']}：生图失败，改用卡片绘制：{res.error or '未知原因'}", level="warn")
        return {"ok": False, "path": None, "reused": False, "prompt": prompt,
                "error": res.error or "生图失败"}
    joblog.bound_log(f"插图 {scene['id']}：已生成 {out.name}", level="ok")
    index[scene["id"]] = {"sha": sha, "version": version, "file": str(out), "prompt": prompt}
    write_json_atomic(_index_path(pdir, part_id), index)
    return {"ok": True, "path": out, "reused": False, "prompt": prompt, "error": None}
