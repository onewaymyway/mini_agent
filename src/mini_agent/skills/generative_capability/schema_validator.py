"""
schema_validator.py
=====================
Generative-Capability 引擎的完整 intent_schema 校验器（阶段四）。

对应文档: next_doc/generative-capability-skill-plan.md 第 8 节安全边界(3)
          "产物强制 schema 校验" / 实施记录阶段一"已知遗留"、阶段三"已知遗留"。

背景:
  阶段一/阶段三里的 `_validate_schema` 只做了"必填字段是否存在"这一层浅层
  校验，完全没检查类型/结构，会把"字段存在但类型/结构完全不对"的数据放过，
  这与方案文档"不允许自我认定成功"的原则相悖——命中执行与探索蒸馏产物的
  校验强度应当一致且足够严格。

设计取舍:
  不引入 `jsonschema` 第三方依赖（保持 skill 引擎自身零外部依赖、可在任意
  沙箱环境直接运行），改为实现一个覆盖 intent_schema_template 实际会用到
  的子集的最小 JSON Schema 校验器：
    - type: object / array / string / number / integer / boolean / null
    - required（仅 object 有意义）
    - properties（递归校验每个已声明字段）
    - items（数组元素的 schema，递归校验每个元素）
    - enum
  不支持的关键字（如 oneOf/allOf/pattern/minLength 等）会被忽略而不是报错，
  避免因为 capability.yaml 里写了本校验器暂不支持的高级关键字就直接拒绝
  所有正常数据；如需要更严格的校验，后续可以按需扩展，而不必现在就引入
  一整套通用 JSON Schema 实现。
"""

from __future__ import annotations

from typing import Any

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def validate(data: Any, schema: Any, path: str = "$") -> list[str]:
    """
    返回校验错误列表；空列表表示校验通过。
    schema 为空/None 时，只要求 data 不是 None（保持与阶段一/三行为兼容）。
    """
    if not schema:
        return [] if data is not None else [f"{path}: 数据为 None"]

    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type:
        py_type = _TYPE_MAP.get(expected_type)
        if py_type is None:
            pass  # 未知 type 声明，不阻断校验
        elif expected_type == "integer" and isinstance(data, bool):
            errors.append(f"{path}: 期望类型 integer，实际是 boolean")
        elif not isinstance(data, py_type):
            errors.append(f"{path}: 期望类型 {expected_type}，实际是 {type(data).__name__}")
            return errors  # 类型都不对，跳过后续结构性校验，避免级联报错

    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: 值 {data!r} 不在 enum {schema['enum']} 中")

    if expected_type == "object" or (expected_type is None and isinstance(data, dict)):
        if isinstance(data, dict):
            for required_field in schema.get("required", []):
                if required_field not in data:
                    errors.append(f"{path}: 缺少必填字段 `{required_field}`")
            properties = schema.get("properties", {})
            for key, sub_schema in properties.items():
                if key in data:
                    errors.extend(validate(data[key], sub_schema, path=f"{path}.{key}"))

    if expected_type == "array" or (expected_type is None and isinstance(data, list)):
        if isinstance(data, list):
            item_schema = schema.get("items")
            if item_schema:
                for i, item in enumerate(data):
                    errors.extend(validate(item, item_schema, path=f"{path}[{i}]"))

    return errors


def is_valid(data: Any, schema: Any) -> bool:
    return len(validate(data, schema)) == 0
