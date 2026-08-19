"""
validators.py - 网站抓取数据验证规则

提供：
- Schema 校验（结构化验证抓取的网页数据）
- 关键字段检查（必填字段、类型、值域）
- 数据完整性验证（记录完整率、字段填充率）
- 数据时效性验证（数据新鲜度、过期检测）
- 一致性校验（跨页面、跨周期数据一致性）
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """验证错误严重程度"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class FieldType(str, Enum):
    """字段类型枚举"""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOL = "bool"
    DATETIME = "datetime"
    EMAIL = "email"
    URL = "url"
    LIST = "list"
    DICT = "dict"


class DataFreshness(str, Enum):
    """数据新鲜度分级"""
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"


@dataclass
class FieldSchema:
    """单个字段的 Schema 定义"""
    name: str
    field_type: FieldType = FieldType.STRING
    required: bool = False
    nullable: bool = False
    default: Any = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    regex_pattern: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: Optional[List[str]] = None
    custom_validator: Optional[Callable[[Any], bool]] = None
    description: str = ""

    def __post_init__(self):
        if self.required and self.nullable:
            raise ValueError(f"Field '{self.name}' cannot be both required and nullable")


@dataclass
class ValidationError:
    """验证错误结果"""
    field_name: str
    severity: Severity
    message: str
    value: Any = None
    expected: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field_name,
            "severity": self.severity.value,
            "message": self.message,
            "value": self.value,
            "expected": self.expected,
        }


@dataclass
class ValidationRule:
    """一条完整的验证规则"""
    name: str
    fields: Dict[str, FieldSchema] = field(default_factory=dict)
    cross_field_validators: List[Callable[[Dict[str, Any]], List[ValidationError]]] = field(default_factory=list)
    freshness_hours: float = 24.0
    min_record_count: int = 1
    min_completeness_rate: float = 80.0


@dataclass
class ValidationResult:
    """验证结果"""
    rule_name: str
    passed: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    info: List[ValidationError] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def add_error(self, field_name: str, message: str, **kwargs):
        self.errors.append(ValidationError(field_name=field_name, severity=Severity.ERROR,
                                           message=message, **kwargs))

    def add_warning(self, field_name: str, message: str, **kwargs):
        self.warnings.append(ValidationError(field_name=field_name, severity=Severity.WARNING,
                                             message=message, **kwargs))

    def add_info(self, field_name: str, message: str, **kwargs):
        self.info.append(ValidationError(field_name=field_name, severity=Severity.INFO,
                                         message=message, **kwargs))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_name": self.rule_name, "passed": self.passed,
            "error_count": self.error_count, "warning_count": self.warning_count,
            "info_count": len(self.info),
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Schema 校验器
# ---------------------------------------------------------------------------

class SchemaValidator:
    """
    基于 FieldSchema 的 Schema 校验器。
    支持：类型检查、必填检查、长度范围、数值范围、正则匹配、允许值集合、自定义校验器。
    """

    def __init__(self, rule: ValidationRule):
        self.rule = rule
        self._field_map = rule.fields

    def validate(self, data: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> ValidationResult:
        result = ValidationResult(rule_name=self.rule.name, passed=True)
        context = context or {}
        now = (context or {}).get("now", datetime.utcnow())
        for field_name, schema in self._field_map.items():
            value = data.get(field_name)
            self._validate_field(result, field_name, schema, value, now)
        for validator_fn in self.rule.cross_field_validators:
            try:
                errors = validator_fn(data)
                for err in errors:
                    target = result.errors if err.severity == Severity.ERROR else (
                        result.warnings if err.severity == Severity.WARNING else result.info)
                    target.append(err)
            except Exception as exc:
                result.add_warning("__internal__", f"Cross-field validator error: {exc}")
        result.passed = len(result.errors) == 0
        result.metadata = {"field_count": len(data), "schema_field_count": len(self._field_map)}
        return result

    def _validate_field(self, result: ValidationResult, field_name: str,
                        schema: FieldSchema, value: Any, now: datetime) -> None:
        if value is None or value == "":
            if schema.required:
                result.add_error(field_name, f"Required field '{field_name}' is missing or empty")
            return
        if not self._check_type(result, field_name, schema, value):
            return
        if isinstance(value, str):
            self._validate_string(result, field_name, schema, value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self._validate_number(result, field_name, schema, value)
        if schema.allowed_values is not None and value not in schema.allowed_values:
            result.add_error(field_name, f"Value '{value}' not in allowed_values: {schema.allowed_values}")
        if schema.custom_validator is not None:
            try:
                if not schema.custom_validator(value):
                    result.add_error(field_name, f"Custom validator failed for '{field_name}'")
            except Exception as exc:
                result.add_warning(field_name, f"Custom validator raised: {exc}")

    def _check_type(self, result: ValidationResult, field_name: str,
                    schema: FieldSchema, value: Any) -> bool:
        type_map = {
            FieldType.STRING: str, FieldType.INTEGER: int, FieldType.FLOAT: (int, float),
            FieldType.BOOL: bool, FieldType.DATETIME: (str, datetime),
            FieldType.EMAIL: str, FieldType.URL: str, FieldType.LIST: list, FieldType.DICT: dict,
        }
        expected = type_map.get(schema.field_type)
        if expected is None:
            return True
        if schema.field_type == FieldType.INTEGER and isinstance(value, bool):
            result.add_error(field_name, f"Expected integer, got bool"); return False
        if schema.field_type == FieldType.FLOAT and isinstance(value, bool):
            result.add_error(field_name, f"Expected float, got bool"); return False
        if not isinstance(value, expected):
            result.add_error(field_name, f"Expected type {schema.field_type.value}, got {type(value).__name__}")
            return False
        return True

    def _validate_string(self, result: ValidationResult, field_name: str,
                         schema: FieldSchema, value: str) -> None:
        if schema.min_length is not None and len(value) < schema.min_length:
            result.add_error(field_name, f"String length {len(value)} < min_length {schema.min_length}")
        if schema.max_length is not None and len(value) > schema.max_length:
            result.add_error(field_name, f"String length {len(value)} > max_length {schema.max_length}")
        if schema.regex_pattern is not None and not re.match(schema.regex_pattern, value):
            result.add_error(field_name, f"Value does not match pattern '{schema.regex_pattern}'")
        if schema.field_type == FieldType.EMAIL and not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', value):
            result.add_error(field_name, f"Invalid email format: {value}")
        if schema.field_type == FieldType.URL and not value.startswith(('http://', 'https://')):
            result.add_error(field_name, f"Invalid URL format: {value}")

    def _validate_number(self, result: ValidationResult, field_name: str,
                         schema: FieldSchema, value: float) -> None:
        if schema.min_value is not None and value < schema.min_value:
            result.add_error(field_name, f"Value {value} < min_value {schema.min_value}")
        if schema.max_value is not None and value > schema.max_value:
            result.add_error(field_name, f"Value {value} > max_value {schema.max_value}")


# ---------------------------------------------------------------------------
# 预置常用 Schema
# ---------------------------------------------------------------------------

def build_article_schema() -> ValidationRule:
    return ValidationRule(
        name="article",
        fields={
            "title": FieldSchema(name="title", field_type=FieldType.STRING, required=True,
                                 min_length=1, max_length=500, description="文章标题"),
            "url": FieldSchema(name="url", field_type=FieldType.URL, required=True, description="页面原始 URL"),
            "author": FieldSchema(name="author", field_type=FieldType.STRING, required=False,
                                  nullable=True, description="作者名称"),
            "publish_time": FieldSchema(name="publish_time", field_type=FieldType.STRING,
                                        required=False, nullable=True, description="发布时间 (ISO 8601 或常见格式)"),
            "content": FieldSchema(name="content", field_type=FieldType.STRING, required=True,
                                   min_length=10, description="正文内容"),
            "word_count": FieldSchema(name="word_count", field_type=FieldType.INTEGER,
                                      required=False, nullable=True, min_value=0, description="字数统计"),
            "tags": FieldSchema(name="tags", field_type=FieldType.LIST, required=False,
                                nullable=True, description="标签列表"),
            "site_domain": FieldSchema(name="site_domain", field_type=FieldType.STRING, required=True,
                                       description="来源域名"),
            "crawl_time": FieldSchema(name="crawl_time", field_type=FieldType.STRING, required=True,
                                      description="抓取时间 (ISO 8601)"),
        },
        freshness_hours=48.0, min_completeness_rate=85.0,
    )


def build_product_schema() -> ValidationRule:
    return ValidationRule(
        name="product",
        fields={
            "name": FieldSchema(name="name", field_type=FieldType.STRING, required=True, min_length=1),
            "url": FieldSchema(name="url", field_type=FieldType.URL, required=True),
            "price": FieldSchema(name="price", field_type=FieldType.FLOAT, required=True, min_value=0),
            "original_price": FieldSchema(name="original_price", field_type=FieldType.FLOAT,
                                          required=False, nullable=True, min_value=0),
            "brand": FieldSchema(name="brand", field_type=FieldType.STRING, required=False, nullable=True),
            "description": FieldSchema(name="description", field_type=FieldType.STRING,
                                       required=False, nullable=True, min_length=5),
            "image_url": FieldSchema(name="image_url", field_type=FieldType.URL, required=False, nullable=True),
            "in_stock": FieldSchema(name="in_stock", field_type=FieldType.BOOL, required=False, nullable=True, default=True),
            "site_domain": FieldSchema(name="site_domain", field_type=FieldType.STRING, required=True),
            "crawl_time": FieldSchema(name="crawl_time", field_type=FieldType.STRING, required=True),
        },
        freshness_hours=24.0, min_completeness_rate=80.0,
    )


def build_news_schema() -> ValidationRule:
    return ValidationRule(
        name="news",
        fields={
            "headline": FieldSchema(name="headline", field_type=FieldType.STRING, required=True,
                                    min_length=3, max_length=300),
            "url": FieldSchema(name="url", field_type=FieldType.URL, required=True),
            "source": FieldSchema(name="source", field_type=FieldType.STRING, required=True),
            "publish_time": FieldSchema(name="publish_time", field_type=FieldType.STRING, required=True),
            "content": FieldSchema(name="content", field_type=FieldType.STRING, required=True, min_length=20),
            "category": FieldSchema(name="category", field_type=FieldType.STRING, required=False, nullable=True),
            "site_domain": FieldSchema(name="site_domain", field_type=FieldType.STRING, required=True),
            "crawl_time": FieldSchema(name="crawl_time", field_type=FieldType.STRING, required=True),
        },
        freshness_hours=12.0, min_completeness_rate=90.0,
    )


def build_search_result_schema() -> ValidationRule:
    return ValidationRule(
        name="search_result",
        fields={
            "query": FieldSchema(name="query", field_type=FieldType.STRING, required=True, min_length=1),
            "results": FieldSchema(name="results", field_type=FieldType.LIST, required=True),
            "total_count": FieldSchema(name="total_count", field_type=FieldType.INTEGER,
                                       required=False, nullable=True, min_value=0),
            "site_domain": FieldSchema(name="site_domain", field_type=FieldType.STRING, required=True),
            "crawl_time": FieldSchema(name="crawl_time", field_type=FieldType.STRING, required=True),
        },
        freshness_hours=6.0, min_completeness_rate=85.0,
    )


# ---------------------------------------------------------------------------
# 数据完整性校验器
# ---------------------------------------------------------------------------

class CompletenessValidator:
    """数据完整性校验器：必填字段填充率、整体记录完整率、单字段异常值比例。"""

    def __init__(self, rule: ValidationRule):
        self.rule = rule

    def validate_completeness(self, records: List[Dict[str, Any]]) -> ValidationResult:
        result = ValidationResult(rule_name=f"completeness:{self.rule.name}", passed=True)
        if not records:
            result.add_error("__data__", "No records to validate")
            result.passed = False
            return result
        total = len(records)
        complete_count = 0
        field_fill_rates: Dict[str, Tuple[int, int]] = {}
        for rec in records:
            rec_complete = True
            for field_name, schema in self.rule.fields.items():
                if not schema.required:
                    continue
                val = rec.get(field_name)
                is_filled = val is not None and val != ""
                filled, tot = field_fill_rates.get(field_name, (0, 0))
                field_fill_rates[field_name] = (filled + (1 if is_filled else 0), tot + 1)
                if not is_filled:
                    rec_complete = False
                    result.add_warning(field_name, f"Required field '{field_name}' is missing in record")
            if rec_complete:
                complete_count += 1
        completeness_rate = (complete_count / total) * 100 if total else 0
        result.metadata.update({
            "total_records": total, "complete_records": complete_count,
            "completeness_rate": round(completeness_rate, 2),
            "field_fill_rates": {k: round(fc / ft * 100, 2) if ft else 0
                                 for k, (fc, ft) in field_fill_rates.items()},
        })
        if completeness_rate < self.rule.min_completeness_rate:
            result.add_error("__data__",
                             f"Completeness rate {completeness_rate:.1f}% < threshold {self.rule.min_completeness_rate}%")
        result.passed = len(result.errors) == 0
        return result


# ---------------------------------------------------------------------------
# 数据时效性校验器
# ---------------------------------------------------------------------------

class FreshnessValidator:
    """数据时效性校验器：记录年龄、发布时间合法性、整体新鲜度评分。"""

    def __init__(self, rule: ValidationRule):
        self.rule = rule

    def validate_freshness(self, records: List[Dict[str, Any]],
                           context: Optional[Dict[str, Any]] = None) -> ValidationResult:
        result = ValidationResult(rule_name=f"freshness:{self.rule.name}", passed=True)
        if not records:
            result.add_error("__data__", "No records to validate")
            result.passed = False
            return result
        now = (context or {}).get("now", datetime.utcnow())
        threshold = timedelta(hours=self.rule.freshness_hours)
        fresh_count = stale_count = expired_count = parse_errors = 0
        time_field = "publish_time" if "publish_time" in self.rule.fields and any("publish_time" in r for r in records) else "crawl_time"
        for rec in records:
            raw = rec.get(time_field)
            if raw is None or raw == "":
                expired_count += 1
                result.add_warning(time_field, f"Missing time field in record")
                continue
            age = self._parse_age(raw, now)
            if age is None:
                parse_errors += 1
                result.add_warning(time_field, f"Unparseable time value: {raw}")
                continue
            if age <= threshold * 0.5:
                fresh_count += 1
            elif age <= threshold:
                stale_count += 1
            else:
                expired_count += 1
                result.add_error(time_field,
                                 f"Data expired: age={age.total_seconds()/3600:.1f}h > threshold={self.rule.freshness_hours}h")
        total = len(records)
        freshness_score = (fresh_count / total) * 100 if total else 0
        result.metadata.update({
            "total_records": total, "fresh_count": fresh_count, "stale_count": stale_count,
            "expired_count": expired_count, "parse_errors": parse_errors,
            "freshness_score": round(freshness_score, 2),
            "freshness_level": self._score_to_level(freshness_score),
            "threshold_hours": self.rule.freshness_hours,
        })
        result.passed = expired_count == 0 and parse_errors == 0
        return result

    def _parse_age(self, raw_time: str, now: datetime) -> Optional[timedelta]:
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
                    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y年%m月%d日 %H:%M", "%Y年%m月%d日"]:
            try:
                parsed = datetime.strptime(raw_time.strip(), fmt)
                now_local = now.replace(tzinfo=None)
                return now_local - parsed
            except ValueError:
                continue
        return None

    @staticmethod
    def _score_to_level(score: float) -> str:
        if score >= 80:
            return DataFreshness.FRESH.value
        if score >= 50:
            return DataFreshness.STALE.value
        return DataFreshness.EXPIRED.value


# ---------------------------------------------------------------------------
# 字段值域校验器
# ---------------------------------------------------------------------------

class DomainValidator:
    """字段值域合法性校验器：URL可达性、价格范围合理性、数量非负等。"""
    _URL_DOMAIN_PATTERN = re.compile(r'^https?://[\w\-]+(\.[\w\-]+)+')

    def validate(self, records: List[Dict[str, Any]], rule: ValidationRule) -> ValidationResult:
        result = ValidationResult(rule_name=f"domain:{rule.name}", passed=True)
        for rec in records:
            for field_name, schema in rule.fields.items():
                value = rec.get(field_name)
                if value is None or value == "":
                    continue
                if schema.field_type == FieldType.URL:
                    if not self._URL_DOMAIN_PATTERN.match(str(value)):
                        result.add_error(field_name, f"Invalid URL domain: {value}", value=value)
                if field_name == "price" and isinstance(value, (int, float)):
                    if value <= 0:
                        result.add_error(field_name, f"Price must be positive: {value}", value=value)
                    if value > 1e9:
                        result.add_warning(field_name, f"Suspiciously large price: {value}", value=value)
                if field_name in ("word_count", "total_count") and isinstance(value, int):
                    if value < 0:
                        result.add_error(field_name, f"Count must be non-negative: {value}", value=value)
        result.passed = len(result.errors) == 0
        return result


# ---------------------------------------------------------------------------
# 一致性校验器
# ---------------------------------------------------------------------------

class ConsistencyValidator:
    """跨记录一致性校验器：URL唯一性、数值异常值(IQR)、时间单调性。"""

    def validate(self, records: List[Dict[str, Any]], rule: ValidationRule,
                 key_field: str = "url") -> ValidationResult:
        result = ValidationResult(rule_name=f"consistency:{rule.name}", passed=True)
        if len(records) < 2:
            result.add_info("__data__", "Insufficient records for consistency check (need >= 2)")
            return result
        urls = [str(r.get(key_field, "")) for r in records]
        dup_urls = [u for u in urls if urls.count(u) > 1]
        if dup_urls:
            result.add_warning(key_field, f"Duplicate URLs found: {len(set(dup_urls))} duplicates")
        for field_name, schema in rule.fields.items():
            if schema.field_type not in (FieldType.INTEGER, FieldType.FLOAT):
                continue
            values = [r.get(field_name) for r in records
                      if isinstance(r.get(field_name), (int, float)) and not isinstance(r.get(field_name), bool)]
            if len(values) < 4:
                continue
            sv = sorted(values); n = len(sv)
            q1, q3 = sv[n // 4], sv[3 * n // 4]
            iqr = q3 - q1
            outliers = [v for v in values if v < q1 - 1.5 * iqr or v > q3 + 1.5 * iqr]
            if outliers:
                result.add_warning(field_name, f"{len(outliers)} outlier(s) detected (IQR method)")
        time_field = "crawl_time" if "crawl_time" in rule.fields else None
        if time_field:
            times = []
            for r in records:
                dt = self._try_parse(r.get(time_field, ""))
                if dt:
                    times.append(dt)
            if len(times) >= 2:
                descents = sum(1 for i in range(1, len(times)) if times[i] < times[i - 1])
                if descents > len(times) * 0.3:
                    result.add_warning(time_field, f"High number of time descents: {descents}/{len(times)-1}")
        result.passed = len(result.errors) == 0
        return result

    @staticmethod
    def _try_parse(raw: str) -> Optional[datetime]:
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y年%m月%d日 %H:%M"]:
            try:
                return datetime.strptime(raw.strip(), fmt)
            except ValueError:
                continue
        return None


# ---------------------------------------------------------------------------
# 统一验证入口
# ---------------------------------------------------------------------------

class DataValidator:
    """
    统一数据验证入口，串联 Schema校验、完整性校验、时效性校验、一致性校验。

    用法示例：
        validator = DataValidator()
        rule = validator.build_rule_for_type("article")
        results = validator.validate(records, rule=rule)
        overall = validator.get_overall_result(results)
    """

    _SCHEMA_BUILDERS = {
        "article": build_article_schema,
        "product": build_product_schema,
        "news": build_news_schema,
        "search_result": build_search_result_schema,
    }

    def __init__(self):
        self._schema_validator = SchemaValidator
        self._completeness_validator = CompletenessValidator
        self._freshness_validator = FreshnessValidator
        self._domain_validator = DomainValidator
        self._consistency_validator = ConsistencyValidator

    def build_rule_for_type(self, type_name: str) -> ValidationRule:
        """根据类型名构建验证规则。"""
        builder = self._SCHEMA_BUILDERS.get(type_name)
        if builder is None:
            raise ValueError(f"Unknown schema type: {type_name}. Available: {list(self._SCHEMA_BUILDERS.keys())}")
        return builder()

    def validate(self, records: List[Dict[str, Any]], rule: ValidationRule,
                 context: Optional[Dict[str, Any]] = None) -> Dict[str, ValidationResult]:
        """执行全套验证，返回各阶段结果字典。"""
        context = context or {}
        results: Dict[str, ValidationResult] = {}
        # 1. Schema 校验（逐条）
        schema_result = ValidationResult(rule_name=f"schema:{rule.name}", passed=True)
        schema_v = self._schema_validator(rule)
        for rec in records:
            sub = schema_v.validate(rec, context=context)
            schema_result.errors.extend(sub.errors)
            schema_result.warnings.extend(sub.warnings)
            schema_result.info.extend(sub.info)
        schema_result.passed = len(schema_result.errors) == 0
        schema_result.metadata["records_checked"] = len(records)
        results["schema"] = schema_result
        # 2. 完整性校验
        results["completeness"] = self._completeness_validator(rule).validate_completeness(records)
        # 3. 时效性校验
        results["freshness"] = self._freshness_validator(rule).validate_freshness(records, context=context)
        # 4. 值域校验
        results["domain"] = self._domain_validator().validate(records, rule)
        # 5. 一致性校验
        if len(records) >= 2:
            results["consistency"] = self._consistency_validator().validate(records, rule)
        else:
            cons = ValidationResult(rule_name="consistency", passed=True)
            cons.add_info("__data__", "Skipped: < 2 records")
            results["consistency"] = cons
        return results

    def get_overall_result(self, results: Dict[str, ValidationResult]) -> Dict[str, Any]:
        """汇总各阶段结果，输出总体通过率。"""
        total_errors = sum(len(r.errors) for r in results.values())
        total_warnings = sum(len(r.warnings) for r in results.values())
        passed_phases = sum(1 for r in results.values() if r.passed)
        total_phases = len(results)
        return {
            "overall_passed": total_errors == 0,
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "phases_passed": f"{passed_phases}/{total_phases}",
            "phase_results": {name: r.to_dict() for name, r in results.items()},
        }

    def generate_summary_report(self, records: List[Dict[str, Any]],
                                 rule: ValidationRule) -> str:
        """生成验证摘要报告（Markdown格式）。"""
        results = self.validate(records, rule)
        overall = self.get_overall_result(results)
        lines = [f"# 数据验证报告 — {rule.name}", ""]
        lines.append(f"**总记录数**: {len(records)} | **整体通过**: {'✅' if overall['overall_passed'] else '❌'}")
        lines.append(f"**错误数**: {overall['total_errors']} | **警告数**: {overall['total_warnings']}")
        lines.append(f"**通过阶段**: {overall['phases_passed']}", "")
        lines.append("## 各阶段详情")
        for phase_name, phase_result in results.items():
            status = "✅" if phase_result.passed else "❌"
            lines.append(f"### {status} {phase_name}")
            if phase_result.errors:
                lines.append("**错误**:")
                for err in phase_result.errors[:5]:
                    lines.append(f"- [{err.field_name}] {err.message}")
            if phase_result.warnings:
                lines.append("**警告**:")
                for warn in phase_result.warnings[:5]:
                    lines.append(f"- [{warn.field_name}] {warn.message}")
            meta = phase_result.metadata
            if meta:
                lines.append(f"**元数据**: {meta}")
            lines.append("")
        return "\n".join(lines)
