"""novel_writer/textio.py — 容错读取文本：检测非 UTF-8 编码，就地统一转存为 UTF-8。

背景：Windows 下子 agent / PowerShell 重定向（`>`）写出的文件常是 UTF-16 LE（以 FF FE 开头），
也可能是带 BOM 的 UTF-8 或 GBK。直接 `read_text(encoding="utf-8")` 会抛出
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0`，让整个步骤失败。

`read_text_utf8(path)` 的行为：
1. 严格 UTF-8 能读 → 直接返回（绝大多数情况，无额外开销）。
2. 否则按「BOM → 无 BOM 的 UTF-16/32 特征 → GB18030」顺序检测编码并解码；
3. 解码成功 → 先把原文件备份为 `<原名>.原编码.bak`（后缀不是 .md，不会被章节通配符匹配），
   再用 UTF-8 原子覆盖原文件，并回读校验；转存记录进入 `drain_repairs()`，由 worker 写进运行日志；
4. 任何一步失败 → 抛出 `TextEncodingError`，消息里带文件路径、大小、文件头字节、每种编码的失败原因和处理建议。

不会静默吞错：检测不出可信编码，或转存失败，一律报错，不改动原文件。
"""

from __future__ import annotations

import codecs
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from novel_writer.fsutil import atomic_write_text

BACKUP_SUFFIX = ".原编码.bak"
_HEAD_BYTES = 16

# 已发生的转存记录；worker 每步结束后取走并写入运行日志与 last_run
_repairs: List[Dict[str, Any]] = []
_lock = threading.Lock()


class TextEncodingError(ValueError):
    """文本文件无法按 UTF-8 读取，且自动检测/转存失败。消息已含完整诊断信息。"""


def drain_repairs() -> List[Dict[str, Any]]:
    """取走（并清空）自上次调用以来发生的自动转存记录。"""
    with _lock:
        out = list(_repairs)
        _repairs.clear()
    return out


def _hex(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def _detect_candidates(data: bytes) -> List[Tuple[str, str]]:
    """返回 [(编码名, 判定依据)]，按可信度从高到低。"""
    out: List[Tuple[str, str]] = []
    if data.startswith(codecs.BOM_UTF32_LE):
        out.append(("utf-32-le", "文件头为 FF FE 00 00（UTF-32 LE BOM）"))
    elif data.startswith(codecs.BOM_UTF32_BE):
        out.append(("utf-32-be", "文件头为 00 00 FE FF（UTF-32 BE BOM）"))
    if data.startswith(codecs.BOM_UTF16_LE):
        out.append(("utf-16-le", "文件头为 FF FE（UTF-16 LE BOM，常见于 PowerShell 重定向）"))
    elif data.startswith(codecs.BOM_UTF16_BE):
        out.append(("utf-16-be", "文件头为 FE FF（UTF-16 BE BOM）"))
    if not out and len(data) >= 4 and len(data) % 2 == 0:
        # 无 BOM 的 UTF-16：ASCII 字符（换行、标点、字母）的高位字节为 00，且只出现在同一奇偶位上；
        # 纯中文可能一个 00 都没有，无法判断；这里只选 00 更多的一侧，解码结果再交给合理性检查把关
        odd_nul = data[1::2].count(0)
        even_nul = data[0::2].count(0)
        if odd_nul > even_nul:
            out.append(("utf-16-le", f"无 BOM，但奇数位出现 {odd_nul} 个 00 字节、多于偶数位的 {even_nul} 个（UTF-16 LE 特征）"))
        elif even_nul > odd_nul:
            out.append(("utf-16-be", f"无 BOM，但偶数位出现 {even_nul} 个 00 字节、多于奇数位的 {odd_nul} 个（UTF-16 BE 特征）"))
    if not out and b"\x00" not in data:
        out.append(("gb18030", "无 BOM、无 NUL 字节，按 Windows 中文常见的 GBK/GB18030 尝试"))
    return out


_COMMON_RANGES = ((0x20, 0x7E), (0xA0, 0x24F), (0x2000, 0x206F), (0x3000, 0x303F), (0x3040, 0x30FF),
                  (0x4E00, 0x9FFF), (0xFF00, 0xFFEF))


def _common_ratio(text: str) -> float:
    chars = [c for c in text if c not in "\n\r\t"]
    if not chars:
        return 1.0
    ok = sum(1 for c in chars if any(lo <= ord(c) <= hi for lo, hi in _COMMON_RANGES))
    return ok / len(chars)


def _plausible(text: str, *, guessed: bool = False) -> Optional[str]:
    """解码结果的合理性检查。返回 None 表示通过，否则返回不通过的原因。

    guessed=True（没有 BOM、靠特征猜编码）时额外要求绝大多数字符是常见文字（中日文、拉丁、常用标点），
    避免把二进制垃圾「解码」成一堆生僻字后当成成功。"""
    if "�" in text:
        return "解码结果含替换字符 U+FFFD"
    bad = [c for c in text if ord(c) < 32 and c not in "\n\r\t"]
    if bad:
        return f"解码结果含 {len(bad)} 个异常控制字符（如 U+{ord(bad[0]):04X}），疑似编码判断有误"
    if guessed and _common_ratio(text) < 0.9:
        return f"解码结果中常见文字只占 {_common_ratio(text):.0%}（低于 90%），疑似不是该编码"
    return None


def _utf8_failure(data: bytes, exc: UnicodeDecodeError) -> str:
    lo, hi = max(exc.start - 8, 0), min(exc.end + 8, len(data))
    return (f"{exc.reason}：位置 {exc.start}，字节 {_hex(data[exc.start:exc.end])}"
            f"（附近字节：{_hex(data[lo:hi])}）")


def _fail(path: Path, data: bytes, utf8_err: str, attempts: List[str], extra: str = "") -> "TextEncodingError":
    lines = [
        f"无法读取文本文件：{path}",
        f"  文件大小：{len(data)} 字节；文件头：{_hex(data[:_HEAD_BYTES]) or '（空）'}",
        f"  UTF-8 读取失败：{utf8_err}",
    ]
    lines += [f"  {a}" for a in attempts] or ["  未检测到可尝试的其他编码"]
    if extra:
        lines.append(f"  {extra}")
    lines.append("  处理建议：用编辑器（如 VS Code 右下角编码 → 通过编码重新打开 → 保存为 UTF-8）转存该文件后重跑；"
                 "若文件由 PowerShell 的 `>` 重定向生成，请改用 `Out-File -Encoding utf8` 或 `Set-Content -Encoding utf8`。")
    return TextEncodingError("\n".join(lines))


def read_text_utf8(path: Path, *, repair: bool = True) -> str:
    """读取文本。非 UTF-8 时自动检测并就地转存为 UTF-8（repair=False 则只读不转存）。

    文件不存在/无权限等仍抛 OSError（调用方原有的 except OSError 照常生效）；
    编码问题抛 TextEncodingError（ValueError 子类）。换行统一为 \\n，与 `Path.read_text` 一致。
    """
    path = Path(path)
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        utf8_err = _utf8_failure(data, exc)
    else:
        if not text.startswith("\ufeff"):
            return text.replace("\r\n", "\n").replace("\r", "\n")
        # 带 BOM 的 UTF-8：内容可读，但 BOM 会混进正文，同样转存清掉
        return _repair(path, data, "utf-8-sig", "文件头为 EF BB BF（UTF-8 BOM）", repair)

    attempts: List[str] = []
    for enc, why in _detect_candidates(data):
        try:
            decoded = data.decode(enc)
        except UnicodeDecodeError as exc:
            attempts.append(f"按 {enc} 解码失败（{why}）：位置 {exc.start}，{exc.reason}")
            continue
        problem = _plausible(decoded.lstrip("\ufeff"), guessed=not why.startswith("文件头"))
        if problem:
            attempts.append(f"按 {enc} 解码被拒绝（{why}）：{problem}")
            continue
        return _repair(path, data, enc, why, repair)
    raise _fail(path, data, utf8_err, attempts)


def _repair(path: Path, data: bytes, enc: str, why: str, repair: bool) -> str:
    text = data.decode(enc).lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not repair:
        return text
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        if not backup.exists():  # 只保留最早的原始文件，避免被后续转存覆盖
            backup.write_bytes(data)
        atomic_write_text(path, text)
        back = path.read_bytes().decode("utf-8")
        if back != text:
            raise OSError("回读校验不一致：写入后的 UTF-8 内容与解码结果不同")
    except (OSError, UnicodeError) as exc:
        raise _fail(path, data, "（非 UTF-8）", [f"检测到编码 {enc}（{why}），已解码成功，但转存为 UTF-8 失败："
                                               f"{type(exc).__name__}: {exc}"],
                    "原文件未被改动（若备份已生成，位于 " + str(backup) + "）。常见原因：文件被其他程序占用、只读或磁盘无空间。") from exc
    with _lock:
        _repairs.append({"path": str(path), "from": enc, "reason": why, "chars": len(text), "backup": str(backup)})
    return text
