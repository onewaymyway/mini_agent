"""
structured_extractor.py - 结构化数据提取模块

支持多种结构化数据源：
1. JSON-LD / Microdata / RDFa 提取
2. 数据属性（data-*）提取
3. 内联 JavaScript 变量提取（window.__INITIAL_STATE__ 等）
4. JSONP 回调解析
5. 表格数据提取
6. OpenGraph / Twitter Card 元数据提取
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型
# ============================================================================


@dataclass
class ExtractedField:
    """提取的字段"""
    name: str
    value: str
    source: str  # 'jsonld', 'data_attr', 'inline_js', 'jsonp', 'table', 'meta'
    selector: str = ""
    confidence: float = 1.0


@dataclass
class StructuredDataResult:
    """结构化数据提取结果"""
    success: bool
    fields: List[ExtractedField] = field(default_factory=list)
    raw_data: Optional[Any] = None
    error: Optional[str] = None
    methods_used: List[str] = field(default_factory=list)

    @property
    def field_count(self) -> int:
        return len(self.fields)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "field_count": len(self.fields),
            "fields": [
                {
                    "name": f.name,
                    "value": f.value,
                    "source": f.source,
                    "selector": f.selector,
                    "confidence": f.confidence,
                }
                for f in self.fields
            ],
            "raw_data": self.raw_data,
            "error": self.error,
            "methods_used": self.methods_used,
        }

    def get_field(self, name: str) -> Optional[str]:
        """按名称获取字段值"""
        for f in self.fields:
            if f.name == name:
                return f.value
        return None

    def get_fields_by_source(self, source: str) -> List[ExtractedField]:
        """按来源筛选字段"""
        return [f for f in self.fields if f.source == source]


# ============================================================================
# 结构化数据提取器
# ============================================================================


class StructuredDataExtractor:
    """
    结构化数据提取器

    支持多种数据源：
    - JSON-LD 结构化数据
    - Microdata
    - RDFa
    - data-* 属性
    - 内联 JS 变量
    - JSONP
    - OpenGraph / Twitter Card
    - 表格数据
    """

    # 常见的内联 JS 状态变量名
    INLINE_JS_VARIABLES = [
        "__INITIAL_STATE__",
        "__NEXT_DATA__",
        "__REDUX_STORE__",
        "__NUXT__",
        "__INITIAL_PROPS__",
        "windowState",
        "pageData",
        "appData",
        "storeData",
        "initialData",
        "__STATE__",
        "__DATA__",
        "__CONTEXT__",
    ]

    # JSONP 回调匹配模式
    JSONP_PATTERN = re.compile(
        r"callBackFunctionName\s*\\(\\s*({.+?})\\s*\\)"
        ,
        re.DOTALL,
    )

    # OpenGraph 属性名映射
    OG_MAPPING = {
        "og:title": "title",
        "og:description": "description",
        "og:image": "image",
        "og:url": "url",
        "og:type": "type",
        "og:site_name": "site_name",
        "og:locale": "locale",
        "article:published_time": "published_time",
        "article:author": "author",
    }

    # Twitter Card 属性名映射
    TWITTER_MAPPING = {
        "twitter:card": "card_type",
        "twitter:title": "title",
        "twitter:description": "description",
        "twitter:image": "image",
        "twitter:site": "site",
        "twitter:creator": "creator",
    }

    def __init__(self):
        self._fields: List[ExtractedField] = []

    # =========================================================================
    # 入口方法
    # =========================================================================

    def extract(self, html: str, url: str = "", options: Optional[Dict] = None) -> StructuredDataResult:
        """
        从 HTML 中提取结构化数据

        Args:
            html: HTML 内容
            url: 页面 URL
            options: 提取选项
                - methods: 要使用的提取方法列表
                - fields: 只提取的字段列表
                - min_confidence: 最低置信度阈值

        Returns:
            StructuredDataResult
        """
        options = options or {}
        methods = options.get("methods", ["jsonld", "meta", "data_attrs", "inline_js"])
        target_fields = options.get("fields", None)
        min_confidence = options.get("min_confidence", 0.0)

        self._fields = []
        methods_used = []

        for method in methods:
            try:
                if method == "jsonld":
                    result = self._extract_jsonld(html, url)
                    if result:
                        self._fields.extend(result)
                        methods_used.append("jsonld")
                elif method == "microdata":
                    result = self._extract_microdata(html)
                    if result:
                        self._fields.extend(result)
                        methods_used.append("microdata")
                elif method == "meta":
                    result = self._extract_meta_tags(html, url)
                    if result:
                        self._fields.extend(result)
                        methods_used.append("meta")
                elif method == "data_attrs":
                    result = self._extract_data_attrs(html)
                    if result:
                        self._fields.extend(result)
                        methods_used.append("data_attrs")
                elif method == "inline_js":
                    result = self._extract_inline_js(html)
                    if result:
                        self._fields.extend(result)
                        methods_used.append("inline_js")
                elif method == "jsonp":
                    result = self._extract_jsonp(html)
                    if result:
                        self._fields.extend(result)
                        methods_used.append("jsonp")
                elif method == "table":
                    result = self._extract_table_data(html)
                    if result:
                        self._fields.extend(result)
                        methods_used.append("table")
            except Exception as e:
                logger.debug(f"{method} 提取失败: {e}")

        # 过滤目标字段
        if target_fields:
            self._fields = [
                f for f in self._fields
                if f.name in target_fields or any(t in f.name for t in target_fields)
            ]

        # 过滤低置信度字段
        if min_confidence > 0:
            self._fields = [
                f for f in self._fields if f.confidence >= min_confidence
            ]

        # 去重（同名字段保留最高置信度）
        self._fields = self._deduplicate_fields(self._fields)

        success = len(self._fields) > 0 or "jsonld" in methods_used
        return StructuredDataResult(
            success=success,
            fields=self._fields,
            raw_data=self._get_raw_summary(),
            methods_used=methods_used,
        )

    # =========================================================================
    # JSON-LD 提取
    # =========================================================================

    def _extract_jsonld(self, html: str, url: str = "") -> List[ExtractedField]:
        """从 JSON-LD 提取结构化数据"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return self._extract_jsonld_regex(html, url)

        scripts = soup.find_all('script', {'type': 'application/ld+json'})
        if not scripts:
            return []

        fields = []
        for script in scripts:
            text = script.get_text(strip=True)
            if not text:
                continue
            try:
                data = json.loads(text)
                fields.extend(self._flatten_jsonld(data, url))
            except json.JSONDecodeError:
                continue
        return fields

    def _extract_jsonld_regex(self, html: str, url: str = "") -> List[ExtractedField]:
        """正则兜底：JSON-LD 提取"""
        pattern = r'<script\s+(?:[^>]*\btype=["\']application/ld\+json["\'][^>]*)>([\s\S]*?)</script>'
        fields = []
        for match in re.finditer(pattern, html, re.IGNORECASE):
            text = match.group(1).strip()
            try:
                data = json.loads(text)
                fields.extend(self._flatten_jsonld(data, url))
            except json.JSONDecodeError:
                continue
        return fields

    def _flatten_jsonld(self, data: Any, url: str = "", prefix: str = "") -> List[ExtractedField]:
        """递归展平 JSON-LD 数据"""
        fields = []

        if isinstance(data, dict):
            # 处理 @context 和 @graph
            if '@context' in data and '@graph' in data:
                for item in data['@graph']:
                    fields.extend(self._flatten_jsonld(item, url, prefix))
                return fields

            # 提取已知字段
            known_fields = [
                'name', 'headline', 'title', 'description', 'text',
                'url', '@id', 'image', 'author', 'publisher',
                'datePublished', 'dateModified', 'keywords',
                'offers', 'aggregateRating', 'review',
            ]

            for key, value in data.items():
                if key.startswith('@'):
                    continue

                full_name = f"{prefix}.{key}" if prefix else key

                if isinstance(value, str):
                    fields.append(ExtractedField(
                        name=full_name,
                        value=value,
                        source='jsonld',
                        confidence=0.9,
                    ))
                elif isinstance(value, (int, float)):
                    fields.append(ExtractedField(
                        name=full_name,
                        value=str(value),
                        source='jsonld',
                        confidence=0.95,
                    ))
                elif isinstance(value, dict):
                    if '@id' in value and 'url' not in value:
                        value['url'] = value['@id']
                    fields.extend(self._flatten_jsonld(value, url, full_name))
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, (str, int, float)):
                            fields.append(ExtractedField(
                                name=f"{full_name}[{i}]",
                                value=str(item),
                                source='jsonld',
                                confidence=0.85,
                            ))
                        else:
                            fields.extend(self._flatten_jsonld(item, url, f"{full_name}[{i}]"))

            # 检查是否是特定类型的结构化数据
            type_value = data.get('@type', '')
            if type_value:
                for field_name in known_fields:
                    if field_name in data:
                        pass  # 已在上面处理

        return fields

    # =========================================================================
    # Microdata 提取
    # =========================================================================

    def _extract_microdata(self, html: str) -> List[ExtractedField]:
        """从 Microdata 提取结构化数据"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return []

        fields = []
        items = soup.find_all(itemscope=True)

        for item in items:
            item_type = item.get('itemtype', '')
            item_id = item.get('itemid', '')
            prefix = f"item:{item_type}"

            # 提取 itemprop
            for prop in item.find_all(attrs={'itemprop': True}):
                prop_name = prop.get('itemprop', '')
                prop_value = prop.get_text(strip=True)
                if prop_value:
                    fields.append(ExtractedField(
                        name=f"{prefix}.{prop_name}",
                        value=prop_value,
                        source='microdata',
                        selector=f'[itemprop="{prop_name}"]',
                        confidence=0.85,
                    ))

        return fields

    # =========================================================================
    # Meta 标签提取（OpenGraph + Twitter Card）
    # =========================================================================

    def _extract_meta_tags(self, html: str, url: str = "") -> List[ExtractedField]:
        """从 <meta> 标签提取 OpenGraph 和 Twitter Card 数据"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return []

        fields = []

        # 提取 <meta> 标签
        meta_tags = soup.find_all('meta')
        for tag in meta_tags:
            property_name = tag.get('property') or tag.get('name') or tag.get('itemprop')
            content = tag.get('content', '')

            if not property_name or not content:
                continue

            # 映射到标准字段名
            field_name = self._map_meta_property(property_name)
            if field_name:
                fields.append(ExtractedField(
                    name=field_name,
                    value=content,
                    source='meta',
                    selector=('meta[' + property_name.replace(':', '=') + ']'
                              if ':' in property_name
                              else f'meta[name="{property_name}"]'),
                    confidence=0.9,
                ))

        # 提取 <title>
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)
            if title:
                fields.append(ExtractedField(
                    name='title',
                    value=title,
                    source='meta',
                    selector='title',
                    confidence=1.0,
                ))

        # 提取 <link rel="canonical">
        canonical = soup.find('link', rel='canonical')
        if canonical:
            href = canonical.get('href', '')
            if href:
                fields.append(ExtractedField(
                    name='canonical_url',
                    value=href,
                    source='meta',
                    selector='link[rel="canonical"]',
                    confidence=0.95,
                ))

        return fields

    def _map_meta_property(self, property_name: str) -> Optional[str]:
        """将 meta 属性映射为标准字段名"""
        # OpenGraph
        if property_name in self.OG_MAPPING:
            return self.OG_MAPPING[property_name]
        # Twitter Card
        if property_name in self.TWITTER_MAPPING:
            return self.TWITTER_MAPPING[property_name]
        # 通用名称映射
        mapping = {
            'description': 'description',
            'keywords': 'keywords',
            'robots': 'robots',
            'viewport': 'viewport',
            'charset': 'charset',
            'author': 'author',
            'generator': 'generator',
            'referrer': 'referrer',
        }
        return mapping.get(property_name)

    # =========================================================================
    # data-* 属性提取
    # =========================================================================

    def _extract_data_attrs(self, html: str) -> List[ExtractedField]:
        """从 data-* 属性提取结构化数据"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return []

        fields = []
        seen_names = set()

        # 搜索所有带有 data-* 属性的元素
        for elem in soup.find_all(attrs=lambda x: x and any(k.startswith('data-') for k in x)):
            for attr_name, attr_value in elem.attrs.items():
                if attr_name.startswith('data-'):
                    field_name = attr_name[5:]  # 去掉 'data-' 前缀
                    key = f"{elem.name}.{field_name}"

                    if key in seen_names:
                        continue
                    seen_names.add(key)

                    value = attr_value if isinstance(attr_value, str) else str(attr_value)
                    fields.append(ExtractedField(
                        name=f"{elem.name}.{field_name}",
                        value=value,
                        source='data_attr',
                        selector=f"{elem.name}[{attr_name}]",
                        confidence=0.8,
                    ))

        return fields

    # =========================================================================
    # 内联 JS 变量提取
    # =========================================================================

    def _extract_inline_js(self, html: str) -> List[ExtractedField]:
        """从内联 JavaScript 提取状态变量"""
        fields = []

        # 匹配常见的状态变量模式
        patterns = [
            (r'var\s+(__\w+__|window\.\w+)\s*=\s*(\{.+?\})\s*;', 'js_var'),
            (r'(?:const|let)\s+(\w+)\s*=\s*(\{.+?\})\s*;', 'js_const'),
            (r'window\.(\w+)\s*=\s*(\{.+?\})\s*;', 'js_window'),
        ]

        for pattern, src_type in patterns:
            for var_name, var_value in re.findall(pattern, html, re.DOTALL):
                try:
                    # 尝试解析为 JSON
                    data = json.loads(var_value)
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, (str, int, float, bool)):
                                fields.append(ExtractedField(
                                    name=f"{var_name}.{k}",
                                    value=str(v),
                                    source='inline_js',
                                    selector=f'script:contains("{var_name}")',
                                    confidence=0.75,
                                ))
                            elif isinstance(v, dict):
                                for k2, v2 in v.items():
                                    if isinstance(v2, (str, int, float, bool)):
                                        fields.append(ExtractedField(
                                            name=f"{var_name}.{k}.{k2}",
                                            value=str(v2),
                                            source='inline_js',
                                            selector=f'script:contains("{var_name}")',
                                            confidence=0.7,
                                        ))
                except json.JSONDecodeError:
                    # 可能是非 JSON 格式，作为原始文本保存
                    fields.append(ExtractedField(
                        name=var_name,
                        value=var_value[:500],  # 截断过长的值
                        source='inline_js',
                        selector=f'script:contains("{var_name}")',
                        confidence=0.5,
                    ))

        return fields

    # =========================================================================
    # JSONP 提取
    # =========================================================================

    def _extract_jsonp(self, html: str) -> List[ExtractedField]:
        """从 JSONP 响应中提取数据"""
        fields = []

        # 匹配 JSONP 模式：callbackFunctionName({...})
        jsonp_patterns = [
            r'(\w+)\s*\(\s*({.+?})\s*\)',
            r'//\s*(\w+)\s*\(\s*({.+?})\s*\)',
        ]

        for pattern in jsonp_patterns:
            for match in re.finditer(pattern, html, re.DOTALL):
                callback_name = match.group(1)
                json_data = match.group(2)

                try:
                    data = json.loads(json_data)
                    fields.extend(self._flatten_json_for_field(data, callback_name))
                except json.JSONDecodeError:
                    continue

        return fields

    def _flatten_json_for_field(self, data: Any, prefix: str = "") -> List[ExtractedField]:
        """展平 JSON 数据为字段"""
        fields = []

        if isinstance(data, dict):
            for key, value in data.items():
                full_name = f"{prefix}.{key}" if prefix else key
                if isinstance(value, (str, int, float, bool)):
                    fields.append(ExtractedField(
                        name=full_name,
                        value=str(value),
                        source='jsonp',
                        confidence=0.85,
                    ))
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, (str, int, float, bool)):
                            fields.append(ExtractedField(
                                name=f"{full_name}[{i}]",
                                value=str(item),
                                source='jsonp',
                                confidence=0.8,
                            ))
                        elif isinstance(item, dict):
                            fields.extend(self._flatten_json_for_field(item, f"{full_name}[{i}]"))
                elif isinstance(value, dict):
                    fields.extend(self._flatten_json_for_field(value, full_name))

        return fields

    # =========================================================================
    # 表格数据提取
    # =========================================================================

    def _extract_table_data(self, html: str) -> List[ExtractedField]:
        """从 <table> 提取结构化数据"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return []

        fields = []
        tables = soup.find_all('table')

        for table_idx, table in enumerate(tables):
            rows = table.find_all('tr')
            if not rows:
                continue

            # 提取表头
            headers = []
            header_row = table.find('thead')
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]
                data_rows = rows
            else:
                first_row = rows[0]
                headers = [cell.get_text(strip=True) for cell in first_row.find_all(['th', 'td'])]
                data_rows = rows[1:]

            # 提取数据行
            for row in data_rows:
                cells = row.find_all(['td', 'th'])
                for cell_idx, cell in enumerate(cells):
                    if cell_idx < len(headers):
                        header = headers[cell_idx]
                        value = cell.get_text(strip=True)
                        if value:
                            fields.append(ExtractedField(
                                name=f"table[{table_idx}].{header}",
                                value=value,
                                source='table',
                                selector=f'table:nth-of-type({table_idx + 1}) td:nth-child({cell_idx + 1})',
                                confidence=0.8,
                            ))

        return fields

    # =========================================================================
    # 工具方法
    # =========================================================================

    def _deduplicate_fields(self, fields: List[ExtractedField]) -> List[ExtractedField]:
        """去重：同名字段保留最高置信度"""
        seen: Dict[str, ExtractedField] = {}
        for field in fields:
            # 标准化字段名
            name = field.name.lower().strip()
            if name not in seen or field.confidence > seen[name].confidence:
                seen[name] = field
        return list(seen.values())

    def _get_raw_summary(self) -> Optional[Dict]:
        """获取原始数据摘要"""
        if not self._fields:
            return None
        return {
            "total_fields": len(self._fields),
            "sources": list(set(f.source for f in self._fields)),
            "field_names": [f.name for f in self._fields[:20]],  # 只返回前20个
        }


# ============================================================================
# 便捷函数
# ============================================================================


def extract_structured_data(
    html: str,
    url: str = "",
    methods: Optional[List[str]] = None,
    **kwargs,
) -> StructuredDataResult:
    """便捷函数：提取结构化数据"""
    extractor = StructuredDataExtractor()
    return extractor.extract(html, url=url, options={"methods": methods or ["jsonld", "meta", "data_attrs", "inline_js"]})


def parse_jsonp_response(text: str) -> Optional[Dict]:
    """解析 JSONP 响应"""
    pattern = r'(\w+)\s*\(\s*({.+?})\s*\)'
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(2))
    except json.JSONDecodeError:
        return None


__all__ = [
    "StructuredDataExtractor",
    "StructuredDataResult",
    "ExtractedField",
    "extract_structured_data",
    "parse_jsonp_response",
]
