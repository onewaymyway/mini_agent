"""
perception/artifact_detector.py — 产出物自动侦测
=====================================================

目标：工具执行成功后，自动判断"是不是生成了一份文档/图片类产出"，
如果是，自动调用 storage.artifacts.record_artifact() 登记，
不需要 Agent/工具作者手动调用，产出预览看板就能看到。

只关心命令行不便展示的类型：image / document / pdf（不含 code/text，
后者太常见，逐个登记会把看板刷成噪音，且本来就能在终端直接看）。

侦测策略（按工具类型区别对待，尽量少误报）：
  - write_file / create_file / patch_file / patch_file_simple：
    这些工具的 `path` 参数就是目标文件，直接检查其后缀。
  - bash：命令是黑盒，唯一线索是命令文本本身；从命令字符串里正则提取
    形如 `xxx.docx` / "xxx.png" 这样的路径 token，再逐个检查是否真实
    存在、且是否是"最近生成/修改"的（避免把命令里顺带提到的历史文件
    也当成新产出）。
  - 其它工具：默认不侦测（可按需在 _PATH_ARG_TOOLS 里追加）。

去重：同一 ToolExecutor 生命周期内，同一路径 + 同一 mtime 只登记一次
（ArtifactAutoDetector 实例自己维护 `_seen` 集合），防止同一份文件在
多次工具调用间被反复读取/写入时重复生成 manifest。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

# 复用 storage.artifacts 里的后缀映射，但只保留"命令行不便展示"的三类
_AUTO_DETECT_EXTS = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".bmp": "image",
    ".pdf": "pdf",
    ".docx": "document", ".doc": "document", ".dotx": "document",
    ".pptx": "document", ".ppt": "document", ".xlsx": "document", ".xls": "document",
}

# 这些工具的 tool_input 里有明确的 `path` 字段指向产出文件本身
_PATH_ARG_TOOLS = frozenset({"write_file", "create_file", "patch_file", "patch_file_simple"})

# 从 bash 命令 / 输出文本里提取候选路径的正则：
# 匹配不含空白、以已知后缀结尾的 token（允许常见路径字符，含中文）
_PATH_TOKEN_RE = re.compile(
    r"""[^\s'"]+\.(?:png|jpe?g|gif|webp|bmp|pdf|docx?|dotx|pptx?|xlsx?)""",
    re.IGNORECASE,
)

# 新产出判定阈值：mtime 距离"这次工具调用发生时刻"在这个窗口内，才算"新生成"，
# 避免 bash 命令里提到的老文件（比如只是读取/引用）被误判为本次产出。
_RECENT_WINDOW_SECONDS = 30.0


class ArtifactAutoDetector:
    """挂在 ToolExecutor 上的轻量侦测器，维护跨调用的去重状态。"""

    def __init__(self) -> None:
        self._seen: set[tuple[str, float]] = set()

    def detect(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        result_str: str,
    ) -> list[dict[str, Any]]:
        """返回本次工具调用中侦测到的候选产出文件列表：
        [{"path": abs_path, "type": "image"/"document"/"pdf"}, ...]
        调用方负责决定是否/如何登记（本函数不做任何 IO 之外的副作用）。"""
        candidates: list[str] = []

        if tool_name in _PATH_ARG_TOOLS:
            p = tool_input.get("path")
            if p:
                candidates.append(p)
        elif tool_name == "bash":
            command = tool_input.get("command", "") or ""
            candidates.extend(_PATH_TOKEN_RE.findall(command))
            # 输出里也可能提到生成的文件路径（比如脚本打印 "Saved to xxx.docx"）
            candidates.extend(_PATH_TOKEN_RE.findall(result_str or ""))
        else:
            return []

        now = time.time()
        found: list[dict[str, Any]] = []
        seen_in_this_call: set[str] = set()
        for raw_path in candidates:
            ext = Path(raw_path).suffix.lower()
            file_type = _AUTO_DETECT_EXTS.get(ext)
            if not file_type:
                continue
            try:
                full = Path(raw_path).expanduser().resolve()
            except OSError:
                continue
            if not full.exists() or not full.is_file():
                continue
            key = str(full)
            if key in seen_in_this_call:
                continue
            seen_in_this_call.add(key)

            try:
                mtime = full.stat().st_mtime
            except OSError:
                continue

            # bash 场景下要求"最近修改"，避免误报历史文件；
            # write_file/create_file 场景本身就是工具刚写完，直接放行。
            if tool_name == "bash" and (now - mtime) > _RECENT_WINDOW_SECONDS:
                continue

            dedup_key = (key, mtime)
            if dedup_key in self._seen:
                continue
            self._seen.add(dedup_key)

            found.append({"path": key, "type": file_type})

        return found


def maybe_record_artifact(
    detector: ArtifactAutoDetector,
    *,
    project_root,
    session_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    result_str: str,
) -> Optional[str]:
    """检测 + 登记的入口，供 ToolExecutor 在工具成功执行后调用一次。

    静默失败：任何异常都不应影响主流程（工具调用已经成功了，产出登记
    只是锦上添花），返回 None 表示本次没有登记任何东西。
    """
    if not project_root or not session_id:
        return None
    try:
        files = detector.detect(tool_name, tool_input, result_str)
        if not files:
            return None

        from mini_agent.storage.paths import AgentPaths
        from mini_agent.storage.artifacts import record_artifact

        paths = AgentPaths(project_root)
        title = f"{tool_name} 自动产出" if len(files) > 1 else Path(files[0]["path"]).name
        manifest = record_artifact(
            paths,
            session_id,
            title,
            files,
            source={"tool": tool_name, "auto_detected": True},
        )
        return manifest.manifest_id
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.artifact_detector")
        return None
