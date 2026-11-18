"""planning/jsonio.py — 从模型输出里取出 JSON 对象（去围栏、截取、json_repair 兜底）。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict


def parse_json_object(text: str) -> Dict[str, Any]:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("输出中没有 JSON 对象")
        chunk = s[start:end + 1]
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            try:
                import json_repair  # 可选依赖，与 novel_to_video 相同的修复手段
            except ImportError as exc:
                raise ValueError("JSON 解析失败，且未安装 json_repair") from exc
            obj = json_repair.loads(chunk)
    if not isinstance(obj, dict):
        raise ValueError("顶层不是 JSON 对象")
    return obj
