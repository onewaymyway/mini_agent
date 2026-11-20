"""batch/store.py — 批量队列的落盘存储（R23）。

位置：<项目集合根目录>/_queue/
  queue.json        队列本体：共用参数 params、条目 items、下一个编号 next_id
  sources/          每篇原文的副本（入队时复制，之后不依赖上传组件或原路径）
  stop.flag         停止请求（跨进程；新一次「开始」会清除）
  runner.lock       运行者锁（pid + 心跳），保证同一时间只有一个运行者
  queue.lock        读改写 queue.json 时的短时文件锁
  logs/job.jsonl    队列级日志（scope=queue）；每篇的细日志仍在各自项目里

条目状态：queued 排队中 / running 运行中 / done 完成 / failed 失败（重试用尽）/
          stopped 已停止 / waiting 停在人工检查点（G0–G2）/ budget_exceeded 超预算（G2）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..pipeline.gates import GEARS, STAGES, PipelineError, check_budget_requirement
from ..project_store import now_iso, read_json, slugify, write_json_atomic, write_text_atomic

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
QUEUE_DIRNAME = "_queue"
SCHEMA = 1
STATES = ("queued", "running", "done", "failed", "stopped", "waiting", "budget_exceeded")
REQUEUE_FROM = ("failed", "stopped", "waiting", "budget_exceeded")   # 「重新排队」允许的来源状态
LOCK_STALE_SEC = 60.0            # 运行者心跳超过这个时间视为已死
QUEUE_LOCK_WAIT_SEC = 10.0

DEFAULT_PARAMS: Dict[str, Any] = {
    "gear": "G3",                 # 队列默认使用完全无人值守档位（需用户显式点击「开始」才会运行）
    "author": "",                 # 共用作者：非空时对队列里所有文章生效（优先于逐篇作者）；留空 = 逐篇设置
    "until": "export",
    "max_images": 0,              # 0 = 不限
    "max_minutes": 0.0,           # 0 = 不限
    "merge_all": False,
    "max_retries": 3,             # 失败后最多重试几次（共 1 + 3 次尝试）
    "retry_delays": [30.0, 60.0, 120.0],   # 第 N 次重试前等待的秒数（不足时沿用最后一个）
    "settings": {},               # 与「新建项目」同一套字段，只写与全局默认不同的部分
    "branding": {},
}

_THREAD_LOCKS: Dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class QueueError(ValueError):
    """入队或参数不合法；界面直接展示其文字。"""


# ---------- 路径 ----------

def queue_dir(root: Path) -> Path:
    return Path(root) / QUEUE_DIRNAME


def queue_file(root: Path) -> Path:
    return queue_dir(root) / "queue.json"


# ---------- 锁 ----------

def _thread_lock(root: Path) -> threading.RLock:
    key = str(Path(root).resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextlib.contextmanager
def locked(root: Path) -> Iterator[None]:
    """进程内线程锁 + 跨进程文件锁（O_EXCL；持锁超过 30 秒视为残留并清除）。"""
    qdir = queue_dir(root)
    qdir.mkdir(parents=True, exist_ok=True)
    lock_path = qdir / "queue.lock"
    with _thread_lock(root):
        deadline = time.monotonic() + QUEUE_LOCK_WAIT_SEC
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > 30:
                        lock_path.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise QueueError("队列文件被占用，请稍后重试")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass


# ---------- 参数 ----------

def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


def normalize_params(raw: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """校验并补全共用参数；不合法的字段回退默认并给出警告。G2 缺预算上限直接报错（与流水线一致）。"""
    raw = raw if isinstance(raw, dict) else {}
    warnings: List[str] = []
    p: Dict[str, Any] = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                         for k, v in DEFAULT_PARAMS.items()}
    gear = raw.get("gear", p["gear"])
    if gear not in GEARS:
        warnings.append(f"挡位 {gear!r} 不合法，已改用 {DEFAULT_PARAMS['gear']}")
        gear = DEFAULT_PARAMS["gear"]
    p["gear"] = gear
    until = raw.get("until", p["until"])
    if until not in STAGES:
        warnings.append(f"运行到 {until!r} 不合法，已改用 export")
        until = "export"
    p["until"] = until
    p["max_images"] = int(_num(raw.get("max_images", 0), 0, 0, 10_000_000))
    p["max_minutes"] = _num(raw.get("max_minutes", 0), 0.0, 0.0, 10_000_000.0)
    p["merge_all"] = bool(raw.get("merge_all", False))
    p["author"] = str(raw.get("author") or "").strip()
    p["max_retries"] = int(_num(raw.get("max_retries", 3), 3, 0, 10))
    delays = raw.get("retry_delays", p["retry_delays"])
    if not isinstance(delays, (list, tuple)) or not delays:
        delays = DEFAULT_PARAMS["retry_delays"]
    p["retry_delays"] = [_num(d, 30.0, 0.0, 3600.0) for d in delays]
    for key in ("settings", "branding"):
        val = raw.get(key, {})
        p[key] = val if isinstance(val, dict) else {}
    try:
        check_budget_requirement(gear, p["max_images"] or None, p["max_minutes"] or None)
    except PipelineError as exc:
        raise QueueError(str(exc)) from exc
    return p, warnings


# ---------- 读写 ----------

def _empty() -> Dict[str, Any]:
    params, _ = normalize_params({})
    return {"schema": SCHEMA, "created_at": now_iso(), "updated_at": now_iso(),
            "params": params, "next_id": 1, "items": []}


def load(root: Path) -> Dict[str, Any]:
    p = queue_file(root)
    if not p.is_file():
        return _empty()
    try:
        data = read_json(p)
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueError(f"queue.json 无法读取：{exc}") from exc
    data.setdefault("items", [])
    data.setdefault("next_id", len(data["items"]) + 1)
    data["params"], _ = normalize_params(data.get("params"))
    return data


def _save(root: Path, data: Dict[str, Any]) -> None:
    data["updated_at"] = now_iso()
    write_json_atomic(queue_file(root), data)


def set_params(root: Path, raw: Dict[str, Any]) -> List[str]:
    """更新共用参数（整体替换为 raw 补全默认后的结果）。对已在运行的条目不生效，只影响之后开始的条目。"""
    params, warnings = normalize_params(raw)
    with locked(root):
        data = load(root)
        data["params"] = params
        _save(root, data)
    return warnings


def find_item(data: Dict[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    return next((i for i in data["items"] if i["id"] == item_id), None)


def get_item(root: Path, item_id: str) -> Optional[Dict[str, Any]]:
    return find_item(load(root), item_id)


def update_item(root: Path, item_id: str, **fields: Any) -> Dict[str, Any]:
    with locked(root):
        data = load(root)
        item = find_item(data, item_id)
        if item is None:
            raise QueueError(f"没有这个条目：{item_id}")
        item.update(fields)
        _save(root, data)
        return dict(item)


# ---------- 作者 ----------

def effective_author(params: Dict[str, Any], item: Dict[str, Any]) -> str:
    """一篇文章实际使用的作者：共用作者非空则用共用作者（覆盖逐篇作者）；否则用加入队列时设置的逐篇作者。
    留空表示不署名（导入时仍会尝试读取文章里的作者行，与新建项目一致）。"""
    shared = str((params or {}).get("author") or "").strip()
    return shared or str(item.get("author") or "").strip()


# ---------- 入队与维护 ----------

def _known_hashes(root: Path, data: Dict[str, Any]) -> Dict[str, str]:
    """sha256 → 说明：队列里已有的条目与已导入的项目（只用于重复提醒，不拦截）。"""
    seen: Dict[str, str] = {}
    for it in data["items"]:
        seen.setdefault(it.get("sha256", ""), f"队列中的「{it['title']}」")
    root = Path(root)
    if root.is_dir():
        for d in root.iterdir():
            pj = d / "project.json"
            if d.name.startswith("_") or not pj.is_file():
                continue
            try:
                sha = (read_json(pj).get("source") or {}).get("sha256", "")
            except (OSError, json.JSONDecodeError):
                continue
            if sha:
                seen.setdefault(sha, f"已有项目「{d.name}」")
    return seen


def enqueue(root: Path, *, source_text: str, source_name: str, title: str, author: str) -> Dict[str, Any]:
    """加入队列（随时可调用，包括队列正在运行时）。标题必填；作者可为空（表示不署名，之后仍会尝试从文章里读取）。

    返回 {"item": {...}, "warnings": [...]}；原文为空或标题为空抛 QueueError。
    """
    title = (title or "").strip()
    if not title:
        raise QueueError("请填写标题")
    text = (source_text or "").lstrip("﻿")
    if not text.strip():
        raise QueueError("文章内容为空")
    author = (author or "").strip()
    sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    with locked(root):
        data = load(root)
        warnings: List[str] = []
        dup = _known_hashes(root, data).get(sha)
        if dup:
            warnings.append(f"内容与{dup}相同（仍已加入队列）")
        item_id = f"q{data['next_id']:04d}"
        data["next_id"] += 1
        rel = f"sources/{item_id}_{slugify(Path(source_name).stem or title)}.md"
        write_text_atomic(queue_dir(root) / rel, text)
        item = {"id": item_id, "title": title, "author": author, "source_name": source_name,
                "source_path": rel, "sha256": sha, "chars": len(text),
                "chinese_chars": len(_CJK_RE.findall(text)), "state": "queued",
                "failures": 0, "project_id": "", "error": "", "outputs": [],
                "enqueued_at": now_iso(), "started_at": "", "finished_at": ""}
        data["items"].append(item)
        _save(root, data)
    return {"item": dict(item), "warnings": warnings}


def remove_item(root: Path, item_id: str) -> None:
    """移出队列：运行中的条目不能移除（先停止）。已生成的项目不受影响。"""
    with locked(root):
        data = load(root)
        item = find_item(data, item_id)
        if item is None:
            raise QueueError(f"没有这个条目：{item_id}")
        if item["state"] == "running":
            raise QueueError("该条目正在运行，请先停止")
        data["items"] = [i for i in data["items"] if i["id"] != item_id]
        _save(root, data)


def requeue(root: Path, item_id: Optional[str] = None) -> int:
    """把失败/已停止/等待中/超预算的条目重新排队（item_id 为空 = 全部）；失败次数清零。返回重排的条目数。"""
    n = 0
    with locked(root):
        data = load(root)
        for it in data["items"]:
            if (item_id is None or it["id"] == item_id) and it["state"] in REQUEUE_FROM:
                it.update(state="queued", failures=0, error="", finished_at="")
                n += 1
        if n:
            _save(root, data)
    return n


def claim_next(root: Path) -> Optional[Dict[str, Any]]:
    """取出队首的排队条目并标为运行中；没有则返回 None。每次都重新读盘，所以运行中追加的条目会被取到。"""
    with locked(root):
        data = load(root)
        for it in data["items"]:
            if it["state"] == "queued":
                it.update(state="running", started_at=now_iso(), error="")
                _save(root, data)
                return dict(it)
    return None


def recover_running(root: Path) -> int:
    """运行者启动时调用：上次异常退出留下的 running 改回 queued（不消耗重试次数）。"""
    n = 0
    with locked(root):
        data = load(root)
        for it in data["items"]:
            if it["state"] == "running":
                it["state"] = "queued"
                n += 1
        if n:
            _save(root, data)
    return n


def summary(root: Path) -> Dict[str, Any]:
    data = load(root)
    counts = {s: 0 for s in STATES}
    for it in data["items"]:
        counts[it["state"]] = counts.get(it["state"], 0) + 1
    return {"total": len(data["items"]), "counts": counts, "runner_alive": runner_alive(root),
            "stop_requested": stop_requested(root), "gear": data["params"]["gear"]}


# ---------- 停止标志 ----------

def _stop_path(root: Path) -> Path:
    return queue_dir(root) / "stop.flag"


def request_stop(root: Path) -> None:
    queue_dir(root).mkdir(parents=True, exist_ok=True)
    _stop_path(root).write_text(now_iso(), encoding="utf-8")


def clear_stop(root: Path) -> None:
    try:
        _stop_path(root).unlink()
    except OSError:
        pass


def stop_requested(root: Path) -> bool:
    return _stop_path(root).is_file()


# ---------- 运行者锁 ----------

def _runner_path(root: Path) -> Path:
    return queue_dir(root) / "runner.lock"


def runner_alive(root: Path) -> bool:
    p = _runner_path(root)
    if not p.is_file():
        return False
    try:
        return time.time() - float(json.loads(p.read_text(encoding="utf-8")).get("ts", 0)) < LOCK_STALE_SEC
    except (OSError, ValueError):
        return False


def acquire_runner(root: Path) -> bool:
    """成为唯一的运行者；已有存活的运行者则返回 False。"""
    with locked(root):
        if runner_alive(root):
            return False
        touch_runner(root)
        return True


def touch_runner(root: Path) -> None:
    write_text_atomic(_runner_path(root), json.dumps({"pid": os.getpid(), "ts": time.time()}))


def release_runner(root: Path) -> None:
    try:
        _runner_path(root).unlink()
    except OSError:
        pass
