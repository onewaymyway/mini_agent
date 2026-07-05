"""
history/raw_history.py — Raw history（完整事件日志，即时落盘）

设计目标：
  每一个有意义的动作（用户输入、LLM 回复、工具调用、工具结果、压缩事件）
  发生时立即写入磁盘，不等 save_session()。

  这样即使 agent 崩溃或被强杀，raw history 仍然完整，可用于：
  - 事后审计：完整还原每一步发生了什么
  - 断点重放：从任意一条记录开始重新运行，尝试改进结果
  - 反思机制：按 _type 区分用户意图 vs 工具噪音，生成高质量经验

存储格式：JSONL（每条记录一行 JSON）
  文件路径：.agent/sessions/<id>/raw_history.jsonl
  - 追加写（`append` 模式），每次 append() 立即写一行并 fsync
  - 不覆盖，不重写整个文件，只追加
  - 旧格式兼容：load_from_file 同时支持 .json（JSON 数组）和 .jsonl

  注意：raw_history.jsonl 永不被 agent 删减或压缩（只追加），
  文件大小随对话增长，但单行最大也就是一个工具调用结果的长度。

时间戳：
  _ts 使用本地时间（含时区偏移），格式：ISO 8601 with offset
  例如 "2026-06-18T16:30:00.123+08:00"
  - 本地时间更直观，日志对人类可读
  - 含时区偏移，跨时区场景仍可排序比较

当前状态 history（active history）与 raw 的关系：
  replay(raw_entries) → active_history
  compact_event 是重置点：它之前的所有消息被压缩掉，之后的是新起点。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from mini_agent.history.entry import HType


class RawHistory:
    """
    Raw history 管理器。

    核心特性：
    - 每次 append() 立即写入 .jsonl 文件（不缓冲）
    - 内存中保留副本（用于 replay / 只读访问）
    - clear() 不删除文件（历史只追加）；set_path() 切换 session 时关闭旧文件句柄
    """

    def __init__(self) -> None:
        self._raw: list[dict] = []
        self._path: Optional[Path] = None   # 当前绑定的 .jsonl 文件路径
        self._file = None                    # 打开的追加文件句柄

    # ── 绑定文件路径 ─────────────────────────────────────────────────────────

    def set_path(self, path: Path) -> None:
        """绑定 .jsonl 文件路径，之后所有 append() 调用立即写入该文件。

        如果文件已存在（session 恢复），先加载已有内容到内存，
        再以追加模式打开，后续新条目追加到末尾。

        可多次调用（切换 session 时），会关闭旧文件句柄。
        """
        self._close_file()
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

        # 如果文件已存在，先把内容加载到内存（用于 replay）
        if path.exists() and len(self._raw) == 0:
            self._load_jsonl(path)

        # 以追加模式打开（'a'），后续 append 直接写末尾
        self._file = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered

    def _close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                os.fsync(self._file.fileno())
                self._file.close()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.history.raw_history')
                pass
            self._file = None

    def __del__(self) -> None:
        self._close_file()

    # ── 访问 ─────────────────────────────────────────────────────────────────

    @property
    def entries(self) -> list[dict]:
        """返回 raw history 条目列表的浅拷贝（只读）。"""
        return list(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    # ── 追加（核心操作）──────────────────────────────────────────────────────

    def append(self, msg: dict) -> None:
        """追加一条条目，立即写入 .jsonl 文件并 fsync。

        自动注入 _ts（本地时间，ISO 8601 with timezone offset）。
        不修改调用方传入的 msg 对象。
        """
        from mini_agent.history.entry import _now_ts
        entry = dict(msg)
        if "_ts" not in entry:
            entry["_ts"] = _now_ts()
        self._raw.append(entry)

        # 立即写入文件（如果已绑定路径）
        if self._file is not None:
            try:
                line = json.dumps(entry, ensure_ascii=False)
                self._file.write(line + "\n")
                self._file.flush()
                os.fsync(self._file.fileno())
            except Exception as e:
                # 写入失败不抛出（不阻断主流程），但打印警告
                import sys
                print(f"[raw_history] write error: {e}", file=sys.stderr)

    def append_compact_event(
        self,
        before_count: int,
        after_count: int,
        strategy: str,
        trigger_reason: str = None,
    ) -> None:
        """记录一次 compact 操作事件（立即落盘）。"""
        from mini_agent.history.entry import make_compact_event
        self.append(make_compact_event(before_count, after_count, strategy, trigger_reason))

    def clear(self) -> None:
        """清空内存中的 raw history（不删除 .jsonl 文件，历史只追加）。
        通常在测试或 load_session 前调用。
        """
        self._raw.clear()

    # ── 加载（session 恢复）─────────────────────────────────────────────────

    def load_from_file(self, path: Path) -> None:
        """从文件加载 raw history 到内存，支持两种格式：
        - .jsonl：每行一个 JSON 对象（新格式）
        - .json：JSON 数组（旧格式，向后兼容）
        文件不存在时静默跳过。
        """
        if not path.exists():
            # 尝试旧格式路径（.json）
            old_path = path.with_suffix(".json")
            if old_path.exists():
                self._load_json_array(old_path)
            return

        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            self._load_jsonl(path)
        else:
            self._load_json_array(path)

    def _load_jsonl(self, path: Path) -> None:
        """加载 JSONL 格式。"""
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._raw.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # 跳过损坏行
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.history.raw_history')
            pass

    def _load_json_array(self, path: Path) -> None:
        """加载旧格式 JSON 数组。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._raw = data
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.history.raw_history')
            pass

    # ── 兼容旧代码的 save_to_file（不再主动调用，但保留避免调用方报错）────

    def save_to_file(self, path: Path) -> None:
        """兼容旧接口：将内存内容写入 .jsonl（覆盖）。

        新代码不应调用此方法（append 已实时写入）。
        保留此方法仅用于：
        - 旧代码调用路径的兼容（session.py 中的 raw_history=self._hist._raw）
        - 单元测试中手动导出内容
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        # 写成 jsonl 格式
        jsonl_path = path.with_suffix(".jsonl") if path.suffix == ".json" else path
        try:
            with open(jsonl_path, "w", encoding="utf-8") as f:
                for entry in self._raw:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            import sys
            print(f"[raw_history] save_to_file error: {e}", file=sys.stderr)


# ── replay 函数 ──────────────────────────────────────────────────────────────

def replay(raw_history: list[dict]) -> list[dict]:
    """从 raw history 精确还原当前状态 active history。

    规则：
    - 正常条目原样保留（含 _type 和 _ts）
    - compact_event：清空当前 active buffer（之后的条目是压缩后新起点）
    - compact_event 本身不写入 active history
    """
    active: list[dict] = []
    for msg in raw_history:
        if msg.get("_type") == HType.COMPACT_EVENT:
            active.clear()
            continue
        active.append(dict(msg))
    return active
