# -*- coding: utf-8 -*-
"""
DataParser ABC 抽象基类 (Step 6)

设计原则:
- ABC 抽象基类定义统一接口: parse(raw_data, source, data_type) -> List[Dict]
- 每个具体解析器实现特定数据源的格式转换
- 支持同步/异步解析入口
- 异常隔离: 单个解析失败不影响其他解析器
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Type
import logging
import re
import json

logger = logging.getLogger(__name__)


class DataParser(ABC):
    """数据解析器抽象基类"""

    @property
    @abstractmethod
    def source_name(self) -> str:
        pass

    @property
    @abstractmethod
    def supported_data_types(self) -> List[str]:
        pass

    @abstractmethod
    def parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        pass

    def can_parse(self, data_type: str) -> bool:
        return data_type in self.supported_data_types

    def safe_parse(self, raw_data: Any, data_type: str, **kwargs) -> List[Dict[str, Any]]:
        try:
            result = self.parse(raw_data, data_type, **kwargs)
            return result or []
        except Exception as e:
            logger.error(f"{self.source_name}.{data_type} 解析失败: {e}", exc_info=True)
            return []


class ParserRegistry:
    """解析器注册表"""

    def __init__(self):
        self._parsers: Dict[str, List[Type[DataParser]]] = {}
        self._instances: Dict[str, DataParser] = {}

    def register(self, parser_class: Type[DataParser]):
        inst = parser_class()
        source = inst.source_name
        self._parsers.setdefault(source, []).append(parser_class)
        self._instances[source] = inst
        logger.debug(f"注册解析器: {source} ({inst.supported_data_types})")

    def get(self, source: str) -> Optional[DataParser]:
        return self._instances.get(source)

    def find_parser(self, data_type: str) -> Optional[DataParser]:
        for source, inst in self._instances.items():
            if inst.can_parse(data_type):
                return inst
        return None

    def list_sources(self) -> List[str]:
        return list(self._instances.keys())

    def list_types(self) -> List[str]:
        types = set()
        for inst in self._instances.values():
            types.update(inst.supported_data_types)
        return sorted(types)


# ── 通用工具函数 ──

def _parse_float(val: Any, default: float = 0.0) -> float:
    try:
        v = float(val) if val is not None else default
        return v if not (v != v) else default
    except (ValueError, TypeError):
        return default


def _parse_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(val)) if val is not None else default
    except (ValueError, TypeError):
        return default


def _parse_date(val: Any, default: str = '') -> str:
    if val is None:
        return default
    s = str(val).strip()
    if not s or s in ('NaN', 'nan', 'None', ''):
        return default
    return s[:10]


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _extract_jsonp(text: str) -> Optional[dict]:
    match = re.search(r'var\s+\w+\s*=\s*(.+?)\s*;\s*$', text, re.DOTALL)
    if not match:
        return None
    json_str = match.group(1).strip()
    json_str = re.sub(r'([{,])\s*(\w+)\s*:', r'\1"\2":', json_str)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def _safe_iterrows(df: Any):
    if df is None:
        return []
    if hasattr(df, 'iterrows'):
        return df.iterrows()
    if isinstance(df, list):
        return enumerate(df)
    if isinstance(df, dict):
        vals = df.get('records', df.get('data', df.get('items', [])))
        if isinstance(vals, list):
            return enumerate(vals)
        return [('item', v) for v in df.values()]
    return []


def _to_records(obj: Any) -> List[Dict]:
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        vals = obj.get('records', obj.get('data', obj.get('items', [])))
        if isinstance(vals, list):
            return vals
        return [obj]
    try:
        return obj.to_dict('records')
    except Exception:
        return []


# ── 全局注册表 ──
registry = ParserRegistry()


def register_parser(parser_class: Type[DataParser]):
    """装饰器：自动注册解析器"""
    registry.register(parser_class)
    return parser_class


def get_registry() -> ParserRegistry:
    return registry


def parse_raw_data(raw_data: Any, source: str, data_type: str, **kwargs) -> List[Dict[str, Any]]:
    parser = registry.get(source) or registry.find_parser(data_type)
    if parser:
        return parser.safe_parse(raw_data, data_type, **kwargs)
    logger.warning(f"未找到 {source} / {data_type} 的解析器")
    return []


def parse_data(data_type: str, raw_data: Any, source: str = '', **kwargs) -> List[Dict[str, Any]]:
    if source:
        return parse_raw_data(raw_data, source, data_type, **kwargs)
    parser = registry.find_parser(data_type)
    if parser:
        return parser.safe_parse(raw_data, data_type, **kwargs)
    return []


__all__ = [
    'DataParser', 'ParserRegistry', 'registry', 'register_parser',
    'get_registry', 'parse_raw_data', 'parse_data',
    '_parse_float', '_parse_int', '_parse_date', '_now_iso',
    '_extract_jsonp', '_safe_iterrows', '_to_records',
]
