"""
perception/raw_result_store.py — 原始工具结果留存仓库。

背景（[SYS-RAWSTORE]）：
  ToolExecutor._trim_result() 对超长工具结果做截断（规则截断）或摘要
  ([SYS-SMARTTRIM] LLM 摘要）后，原来的做法是直接丢弃原文，agent 之后
  再也看不到完整内容。RawResultStore 让"被截断/摘要过的原文"继续保留一份，
  配合 tools/builtin.py 里的 view_raw_result 工具（现改为经 read_file 按
  路径读取），agent 需要时可以取回完整内容。

[改进：next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md
 第1节] 存储方式从"session 内内存 LRU + 模块级全局单例传递 id"改为
 "落盘到 <project_root>/.agent/raw_results/<session_id>/ + 直接传路径"：

  - 旧实现的问题：`put()` 返回一个纯 result_id，读取时靠
    `tools/builtin.py` 里的模块级全局 `_raw_result_store` 单例查找内容。
    多个 Agent/SubAgent 实例在同一进程内先后构造时，各自的 `__init__`
    都会覆盖这个全局单例（`configure_raw_result_store()`），探索子agent
    跑完之后全局指针停留在探索子agent的 store 上，主 agent 后续按自己
    存过的 id 查询时读到的是错误的 store，报"找不到"。
  - 落盘 + 传路径后，`put()`/`get()` 都是围绕文件路径的纯函数，不再需要
    任何"当前活跃 store 是谁"的全局可变状态，天然消除上述互相覆盖问题；
    同时带来跨 task / 跨 session 事后查看原文的能力（内存 LRU 版本随进程
    结束就彻底丢失）。

设计：
  - 落盘布局：
      <project_root>/.agent/raw_results/<session_id>/<result_id>.txt
      <project_root>/.agent/raw_results/<session_id>/<result_id>.meta.json
  - id 使用内容 md5 短哈希：同一段原文多次被截断也只存一份，天然去重
    （put 前先看目标文件是否已存在，存在则跳过写入，仅刷新 mtime）。
  - 原子写入：先写 `.tmp` 临时文件，再 `os.replace()` 成目标文件名，
    避免"写到一半"的文件被并发读到。
  - 清理策略不再是"内存 LRU 超限即驱逐单条"，而是低频后台巡检
    （见 `raw_result_cleanup.py`，风格与 `_engine/health_patrol.py` 一致：
    只读扫描 + 显式清理开关），本模块自身不做同步驱逐。
  - 线程安全：用一把锁保护内存态统计，文件系统操作本身用 tmp+replace
    保证原子性，不依赖锁互斥多进程场景。

不做的事：
  - 不在 `put`/`get` 路径上做同步的容量驱逐（清理交给独立的低频巡检，
    避免每次写入都扫描整个 session 目录）。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_RAW_RESULTS_SUBDIR = ".agent/raw_results"


@dataclass
class RawResultRef:
    """put() 的返回值：既给出 result_id，也给出可直接用于 read_file 的路径。"""

    result_id: str
    path: str


class RawResultStore:
    """
    原始工具结果的落盘仓库，按 session_id 分目录。

    用法：
        store = RawResultStore(project_root="/path/to/project", session_id="sess-abc")

        ref = store.put(tool_name="bash", content=full_output)
        # ref.path 是完整文件路径，直接拼进提示文案供 agent 用 read_file 查看

        content = store.get(ref.result_id)   # 仍支持按 id 取（当前 session 内），
                                               # 但推荐场景是 agent 直接 read_file(ref.path)
    """

    def __init__(
        self,
        project_root: str,
        session_id: str,
        *,
        base_dir: Optional[str] = None,
    ) -> None:
        self._session_id = session_id or "default"
        root = Path(base_dir) if base_dir else Path(project_root) / _RAW_RESULTS_SUBDIR
        self._session_dir = root / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._stats = {"puts": 0, "gets": 0, "misses": 0, "dedup_hits": 0}

    # ── 核心 API ──────────────────────────────────────────────────────────────

    def put(self, content: str, tool_name: str = "") -> RawResultRef:
        """存入原文，落盘到当前 session 目录，返回 result_id + 完整路径。"""
        result_id = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:12]
        txt_path = self._session_dir / f"{result_id}.txt"
        meta_path = self._session_dir / f"{result_id}.meta.json"

        with self._lock:
            if txt_path.exists():
                # 天然去重：同内容已存在，只刷新 mtime，不重复写入
                os.utime(txt_path, None)
                self._stats["dedup_hits"] += 1
                return RawResultRef(result_id=result_id, path=str(txt_path))

            tmp_txt = txt_path.with_suffix(".txt.tmp")
            tmp_txt.write_text(content, encoding="utf-8", errors="replace")
            os.replace(tmp_txt, txt_path)

            meta = {
                "tool_name": tool_name,
                "created_at": time.time(),
                "chars": len(content),
            }
            tmp_meta = meta_path.with_suffix(".meta.json.tmp")
            tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_meta, meta_path)

            self._stats["puts"] += 1
            return RawResultRef(result_id=result_id, path=str(txt_path))

    def get(self, result_id: str) -> Optional[str]:
        """按 id 在当前 session 目录内查找原文；推荐直接用 put() 返回的路径 read_file。"""
        txt_path = self._session_dir / f"{result_id}.txt"
        with self._lock:
            self._stats["gets"] += 1
            if not txt_path.exists():
                self._stats["misses"] += 1
                return None
            return txt_path.read_text(encoding="utf-8", errors="replace")

    def clear(self) -> None:
        """清空当前 session 目录（谨慎使用；一般清理走 raw_result_cleanup.py 的低频巡检）。"""
        with self._lock:
            for p in self._session_dir.glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def stats_summary(self) -> str:
        with self._lock:
            puts = self._stats["puts"]
            dedup = self._stats["dedup_hits"]
        dedup_str = f", {dedup} dedup hits" if dedup else ""
        return f"raw result store: {puts} files written under {self._session_dir}{dedup_str}"
