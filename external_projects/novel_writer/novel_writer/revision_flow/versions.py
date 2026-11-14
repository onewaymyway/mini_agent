"""P0 完稿版本基线 + P5 版本撤销/丢弃。

布局：
  _versions/index.json          {"head": "v00N", "versions": [{id, parent, kind, created_at, note}]}
  _versions/v001/               完稿快照（kind=baseline，只读）
  _versions/vNNN/               派生版本（kind=derived），整棵内容树的完整副本，改动只落在这里
  _versions/manifests/vNNN.json 每个版本的文件哈希清单，用于校验只读基线未被改动

设计要点：
  * 原稿（v001）写入后只读，且每次校验都与 manifest 比对；
  * 派生版本总是从父版本整体复制出来，修改只写新目录；
  * 只有叶子版本（没有子版本）可以丢弃，v001 永不丢弃。
"""
from __future__ import annotations

import datetime as _dt
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer.revision_flow.common import (
    FlowError, file_sha256, list_content_files, load_json, make_readonly, make_writable,
    save_json, version_dir, versions_root,
)
from novel_writer.state import load_state

INDEX_NAME = "index.json"
BASELINE = "v001"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _index_path(ws):
    return versions_root(ws) / INDEX_NAME


def _manifest_path(ws, vid: str):
    return versions_root(ws) / "manifests" / f"{vid}.json"


def load_index(ws) -> Dict[str, Any]:
    data = load_json(_index_path(ws), default=None)
    if not isinstance(data, dict):
        return {"head": None, "versions": []}
    data.setdefault("head", None)
    data.setdefault("versions", [])
    return data


def _save_index(ws, data: Dict[str, Any]) -> None:
    save_json(_index_path(ws), data)


def list_versions(ws) -> List[Dict[str, Any]]:
    return list(load_index(ws)["versions"])


def get_entry(ws, vid: str) -> Dict[str, Any]:
    for item in load_index(ws)["versions"]:
        if item["id"] == vid:
            return item
    raise FlowError(f"版本不存在：{vid}")


def manifest(ws, vid: str) -> Dict[str, Any]:
    data = load_json(_manifest_path(ws, vid), default=None)
    if not isinstance(data, dict):
        raise FlowError(f"版本 {vid} 缺少清单文件")
    return data


def is_baseline_ready(ws) -> bool:
    return get_entry_or_none(ws, BASELINE) is not None


def get_entry_or_none(ws, vid: str) -> Optional[Dict[str, Any]]:
    for item in load_index(ws)["versions"]:
        if item["id"] == vid:
            return item
    return None


def final_review_passed(ws) -> bool:
    state = load_state(ws)
    step = (state.get("steps") or {}).get("final_review") or {}
    return step.get("status") == "passed"


def _next_id(ws) -> str:
    nums = [int(v["id"][1:]) for v in load_index(ws)["versions"]]
    return f"v{(max(nums) + 1 if nums else 1):03d}"


def _write_manifest(ws, vid: str, *, kind: str, parent: Optional[str], changes: List[str]) -> Dict[str, Any]:
    root = version_dir(ws, vid)
    files = {rel: file_sha256(root / rel) for rel in list_content_files(root)}
    data = {"id": vid, "kind": kind, "parent": parent, "created_at": _now(),
            "changes": list(changes), "files": files}
    save_json(_manifest_path(ws, vid), data)
    return data


META_FILES = ("项目状态/workspace_config.json",)  # 书名等元信息：不属于内容，不参与校验，但导出需要


def _copy_meta(ws, dst: Path) -> None:
    for rel in META_FILES:
        src = ws.root / rel
        if src.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / rel)


def init_baseline(ws, note: str = "完稿快照") -> Dict[str, Any]:
    """P0：全书终审通过后创建 v001（只读）。已存在则拒绝。"""
    if is_baseline_ready(ws):
        raise FlowError("完稿基线 v001 已存在，不能重复创建")
    if not final_review_passed(ws):
        raise FlowError("全书终审尚未通过，不能创建完稿基线")
    dst = version_dir(ws, BASELINE)
    if dst.exists():
        raise FlowError("v001 目录已存在但未登记，请先人工检查 _versions/ 目录")
    dst.mkdir(parents=True)
    for name in ("大纲规划", "正文", "章节总结", "一致性检查", "修订记录"):
        src = ws.root / name
        if src.is_dir():
            shutil.copytree(src, dst / name)
    _copy_meta(ws, dst)
    if not list_content_files(dst):
        shutil.rmtree(dst)
        raise FlowError("没有可快照的内容文件")
    _write_manifest(ws, BASELINE, kind="baseline", parent=None, changes=[])
    make_readonly(dst)
    index = {"head": BASELINE, "versions": [
        {"id": BASELINE, "parent": None, "kind": "baseline", "created_at": _now(), "note": note}]}
    _save_index(ws, index)
    return get_entry(ws, BASELINE)


def derive(ws, parent: str, note: str = "") -> str:
    """从 parent 复制出新的派生版本目录，返回新版本号。调用方随后在新目录内写入改动。"""
    get_entry(ws, parent)
    vid = _next_id(ws)
    dst = version_dir(ws, vid)
    if dst.exists():
        raise FlowError(f"目录已存在：{dst.name}")
    shutil.copytree(version_dir(ws, parent), dst)
    make_writable(dst)
    index = load_index(ws)
    index["versions"].append({"id": vid, "parent": parent, "kind": "derived", "created_at": _now(), "note": note})
    _save_index(ws, index)  # head 不变：只有定稿成功的版本才会成为 head
    return vid


def finalize(ws, vid: str, changes: List[str]) -> Dict[str, Any]:
    """改动完成后写入清单（哈希）并把派生版本设为只读，防止事后误改。"""
    entry = get_entry(ws, vid)
    if entry["kind"] != "derived":
        raise FlowError("只能定稿派生版本")
    data = _write_manifest(ws, vid, kind="derived", parent=entry["parent"], changes=changes)
    make_readonly(version_dir(ws, vid))
    return data


def verify(ws, vid: str) -> List[str]:
    """校验版本内容与清单一致，返回不一致的文件列表（空表示通过）。"""
    root = version_dir(ws, vid)
    recorded = manifest(ws, vid)["files"]
    problems: List[str] = []
    current = set(list_content_files(root))
    for rel in sorted(current | set(recorded)):
        if rel not in recorded:
            problems.append(f"多出文件：{rel}")
        elif rel not in current:
            problems.append(f"缺少文件：{rel}")
        elif file_sha256(root / rel) != recorded[rel]:
            problems.append(f"内容已变化：{rel}")
    return problems


def set_head(ws, vid: str) -> None:
    get_entry(ws, vid)
    index = load_index(ws)
    index["head"] = vid
    _save_index(ws, index)


def _force_remove(func, path, _exc):
    """Windows 下只读文件无法删除：先放开权限再重试。"""
    import os
    try:
        os.chmod(path, 0o777)
        func(path)
    except OSError:
        pass


def discard(ws, vid: str) -> None:
    """P5：丢弃一个派生版本（撤销）。只允许叶子版本，且不能是基线。"""
    if vid == BASELINE:
        raise FlowError("完稿基线 v001 不能丢弃")
    entry = get_entry(ws, vid)
    index = load_index(ws)
    if any(v["parent"] == vid for v in index["versions"]):
        raise FlowError(f"{vid} 之后还有派生版本，请先丢弃它们")
    shutil.rmtree(version_dir(ws, vid), onerror=_force_remove)
    mpath = _manifest_path(ws, vid)
    if mpath.exists():
        mpath.unlink()
    index["versions"] = [v for v in index["versions"] if v["id"] != vid]
    index["head"] = entry["parent"] if index.get("head") == vid else index.get("head")
    _save_index(ws, index)


def undo_head(ws) -> Optional[str]:
    """撤销最近一次修改：丢弃当前 head（若是派生版本），head 回到父版本。"""
    head = load_index(ws)["head"]
    if not head or head == BASELINE:
        return None
    discard(ws, head)
    return load_index(ws)["head"]
