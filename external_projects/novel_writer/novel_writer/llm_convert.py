"""novel_writer/llm_convert.py — 由 LLM 直接把损坏的旧文件转换为目标布局的小文件（通用）。

不做 JSON 修复：LLM 直接读取损坏原文，按规定的目标布局写出 Markdown 小文件；
Python 用同一套严格解析器读回校验，不合格则把具体错误反馈给 LLM，让它重写，最多 max_attempts 次。

产出一律视为「待复核」：调用方（迁移流程）必须经作者确认后才会落盘。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# 校验函数：(本次产出的根目录) -> (数据, 错误列表)。错误列表为空表示合格。
Validator = Callable[[Path], Tuple[Optional[Dict[str, Any]], List[str]]]


@dataclass
class ConvertResult:
    ok: bool
    data: Optional[Dict[str, Any]] = None
    attempts: int = 0
    errors: List[str] = field(default_factory=list)      # 最后一次失败的错误（ok=False 时）
    warnings: List[str] = field(default_factory=list)    # 合格但需要关注的提示（如疑似遗漏）
    staging: Optional[Path] = None


def convert_with_llm(*, source: Path, workdir: Path, invoker: Callable[..., Any], skill: str,
                     instructions: str, validate: Validator, max_attempts: int = 3,
                     step_id: str = "llm_convert") -> ConvertResult:
    """让技能 skill 读取 source 并写出目标布局。每次尝试写入 workdir/attemptK/，校验通过即返回。

    instructions：目标布局的完整说明（由调用方给出，含精确的文件格式与示例）。
    invoker：框架执行器，签名 invoker(skill=, prompt=, export_dir=, step_id=)。
    """
    workdir = Path(workdir)
    errors: List[str] = []
    result = ConvertResult(ok=False)
    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        stage = workdir / f"attempt{attempt:02d}"
        if stage.exists():
            shutil.rmtree(stage)
        out = stage / "out"
        out.mkdir(parents=True, exist_ok=True)
        feedback = ""
        if errors:
            feedback = ("\n【上一次的产出未通过校验，问题如下，请修正后重新写出全部文件】\n"
                        + "\n".join(f"- {e}" for e in errors[:30]) + "\n")
        prompt = (f"【待转换的损坏文件】{source}\n"
                  f"请用文件读取工具完整阅读它（它可能不是合法 JSON，请尽量从原文中识别每一条记录，不要遗漏）。\n"
                  f"【写出位置】把全部产出写入目录：{out}\n\n"
                  f"{instructions}\n{feedback}")
        try:
            invoker(skill=skill, prompt=prompt, export_dir=out, step_id=f"{step_id}_{attempt}")
        except Exception as exc:  # noqa: BLE001  执行器异常计为一次失败，继续重试
            errors = [f"执行器异常：{exc}"]
            continue
        data, errors = validate(out)
        if not errors:
            result.ok = True
            result.data = data
            result.staging = out
            result.errors = []
            return result
    result.errors = errors
    return result


def id_count_hint(source_text: str, id_pattern: str = r'"id"\s*:\s*"([^"]+)"') -> List[str]:
    """从损坏原文中粗略统计 id 出现次数，用于发现 LLM 可能的遗漏（只作提示，不作判定）。"""
    import re
    return re.findall(id_pattern, source_text)
