"""P19-2：探索参数表单的纯逻辑（与 streamlit 无关，便于单元测试）。

- widget_spec：把注册表中的一行参数转换成控件描述（类型、范围、选项、帮助文字）；
- changed_values：只挑出“相对当前生效值确实被改动”的项提交。
  这样保存一组参数时，没动过的项不会被写成看板覆盖值，仍然跟随 yaml。
"""
from __future__ import annotations

from typing import Any

SOURCE_LABEL = {"dashboard": "看板设置", "yaml": "config/discovery.yaml", "default": "默认值"}


def widget_spec(row: dict[str, Any]) -> dict[str, Any]:
    kind = row["kind"]
    source = f"当前来源：{SOURCE_LABEL.get(row['source'], row['source'])}"
    help_text = f"{row['hint']}；{source}" if row.get("hint") else source
    spec: dict[str, Any] = {"key": row["key"], "label": row["label"], "kind": kind,
                            "value": row["value"], "help": help_text, "group": row.get("group", "")}
    if kind == "int":
        spec.update(min=int(row["min"] if row["min"] is not None else 0),
                    max=int(row["max"] if row["max"] is not None else 10**9), step=1)
    elif kind == "float":
        hi = row["max"] if row["max"] is not None else 1000.0
        spec.update(min=float(row["min"] if row["min"] is not None else 0.0), max=float(hi),
                    step=0.05 if hi <= 1 else 0.5)
    elif kind == "choice":
        spec["options"] = list(row["choices"])
    return spec


def changed_values(rows: list[dict[str, Any]], submitted: dict[str, Any]) -> dict[str, Any]:
    """返回相对当前生效值有变化的项。未知键忽略。"""
    current = {r["key"]: r["value"] for r in rows}
    out: dict[str, Any] = {}
    for key, value in submitted.items():
        if key not in current:
            continue
        before = current[key]
        if isinstance(before, float) or isinstance(value, float):
            try:
                if abs(float(value) - float(before)) < 1e-9:
                    continue
            except (TypeError, ValueError):
                pass
        elif value == before:
            continue
        out[key] = value
    return out
