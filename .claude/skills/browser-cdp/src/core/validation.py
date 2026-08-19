"""
validation.py - 数据完整性与时效性验证模块

与步骤4（验证规则）和步骤5（测试报告框架）集成。
从ContentDatabase读取抓取结果，执行字段完整性、去重、时效性验证，
输出兼容步骤5报告格式的结构化结果。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add skill root to path
SKILL_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.content.database import ContentDatabase  # noqa: E402
from src.content.models import Article, ContentType, ContentSource  # noqa: E402


# ============================================================================
# Validation Data Models (aligned with step4 rules)
# ============================================================================

@dataclass
class FieldValidationResult:
    """单字段验证结果"""
    field_name: str
    passed: bool
    value: Any
    expected: str
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "passed": self.passed,
            "value": self.value,
            "expected": self.expected,
            "message": self.message,
        }


@dataclass
class TimelinessResult:
    """时效性验证结果"""
    article_id: str
    is_valid: bool = True
    published_time: Optional[str] = None
    parsed_date: Optional[datetime] = None
    age_days: Optional[int] = None
    max_age_days: int = 365
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "article_id": self.article_id,
            "is_valid": self.is_valid,
            "published_time": self.published_time,
            "parsed_date": self.parsed_date.isoformat() if self.parsed_date else None,
            "age_days": self.age_days,
            "max_age_days": self.max_age_days,
            "message": self.message,
        }


@dataclass
class SchemaViolation:
    """Schema违规记录"""
    article_id: str
    rule: str
    severity: str  # ERROR / WARN
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "article_id": self.article_id,
            "rule": self.rule,
            "severity": self.severity,
            "detail": self.detail,
        }


# ============================================================================
# Time Parsing (from step4 rules)
# ============================================================================

TIME_PATTERNS: List[tuple] = [
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    (r"\d{4}/\d{2}/\d{2}", "%Y/%m/%d"),
    (r"\d{4}\.(\d{1,2})\.(\d{1,2})", None),  # handled separately
]


def parse_publish_time(time_str: str) -> Optional[datetime]:
    """解析多种时间格式，返回datetime或None"""
    if not time_str:
        return None
    time_str = str(time_str).strip()
    if not time_str:
        return None

    # 标准格式
    for pattern, fmt in TIME_PATTERNS:
        if re.match(pattern, time_str):
            try:
                return datetime.strptime(time_str[:10], fmt)
            except ValueError:
                continue

    # 中文格式: 2024年1月15日
    cn_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", time_str)
    if cn_match:
        try:
            return datetime(
                int(cn_match.group(1)),
                int(cn_match.group(2)),
                int(cn_match.group(3)),
            )
        except ValueError:
            pass

    # 点分隔格式: 2024.1.15
    dot_match = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", time_str)
    if dot_match:
        try:
            return datetime(
                int(dot_match.group(1)),
                int(dot_match.group(2)),
                int(dot_match.group(3)),
            )
        except ValueError:
            pass

    # ISO8601（含时间部分）
    try:
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except (ValueError, AttributeError):
        pass

    # 相对时间: "3天前", "2小时前"
    rel_match = re.search(r"(\d+)\s*(天|小时|分钟|周|月|年)", time_str)
    if rel_match:
        num = int(rel_match.group(1))
        unit = rel_match.group(2)
        now = datetime.now()
        if unit == "天":
            return now - timedelta(days=num)
        elif unit == "小时":
            return now - timedelta(hours=num)
        elif unit == "周":
            return now - timedelta(weeks=num)
        elif unit == "月":
            return now - timedelta(days=num * 30)
        elif unit == "年":
            return now - timedelta(days=num * 365)

    return None


# ============================================================================
# Validation Functions (step4 rules implementation)
# ============================================================================

REQUIRED_FIELDS = ["source", "title", "url", "snippet"]
OPTIONAL_FIELDS = ["published_time", "author", "tags"]


def validate_field_not_empty(
    article: Article, field_name: str,
) -> FieldValidationResult:
    """检查必填字段非空"""
    value = getattr(article, field_name, "")
    passed = bool(value and str(value).strip())
    return FieldValidationResult(
        field_name=field_name,
        passed=passed,
        value=value,
        expected="non-empty",
        message="" if passed else f"{field_name} 为空",
    )


def validate_url_format(article: Article) -> FieldValidationResult:
    """URL格式校验"""
    url = article.url
    passed = bool(url and (url.startswith("http") or url.startswith("//") or url.startswith("/")))
    return FieldValidationResult(
        field_name="url_format",
        passed=passed,
        value=url,
        expected="valid URL (http/https//absolute)",
        message="" if passed else f"URL格式无效: {url}",
    )


def validate_title_length(article: Article) -> FieldValidationResult:
    """标题长度校验（5-200字）"""
    title = article.title
    passed = 5 <= len(title) <= 200 if title else False
    return FieldValidationResult(
        field_name="title_length",
        passed=passed,
        value=len(title) if title else 0,
        expected="5-200 chars",
        message="" if passed else f"标题长度异常: {len(title) if title else 0}",
    )


def validate_snippet_length(article: Article) -> FieldValidationResult:
    """摘要长度校验（至少10字）"""
    snippet = article.excerpt
    passed = snippet and len(snippet.strip()) >= 10
    return FieldValidationResult(
        field_name="snippet_length",
        passed=passed,
        value=len(snippet.strip()) if snippet else 0,
        expected=">=10 chars",
        message="" if passed else f"摘要过短: {len(snippet.strip()) if snippet else 0}",
    )


def _normalize_published_time(article: Article) -> Optional[str]:
    """将 published_at 统一转为字符串（兼容 datetime 和 str）"""
    val = getattr(article, "published_at", None)
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val).strip() or None


def validate_timeliness(
    article: Article, max_age_days: int = 365
) -> TimelinessResult:
    """时效性验证"""
    time_str = _normalize_published_time(article)
    if not time_str:
        return TimelinessResult(
            article_id=article.article_id,
            is_valid=True,
            published_time=None,
            message="无发布时间信息（可选字段）",
        )
    parsed = parse_publish_time(time_str)
    if parsed is None:
        return TimelinessResult(
            article_id=article.article_id,
            is_valid=False,
            published_time=time_str,
            max_age_days=max_age_days,
            message=f"时间格式无法解析: {time_str}",
        )
    age_days = (datetime.now() - parsed).days
    is_valid = age_days <= max_age_days
    return TimelinessResult(
        article_id=article.article_id,
        is_valid=is_valid,
        published_time=time_str,
        parsed_date=parsed,
        age_days=age_days,
        max_age_days=max_age_days,
        message="" if is_valid else f"内容过期: {age_days}天前",
    )


# ============================================================================
# Aggregated Validation (step4 scoring rules)
# ============================================================================


def validate_completeness(
    articles: List[Article],
) -> Dict[str, Any]:
    """计算整体字段完整率"""
    if not articles:
        return {"overall": 0.0, "details": {}, "article_count": 0}

    detail: Dict[str, Dict[str, Any]] = {}
    for fld in REQUIRED_FIELDS + OPTIONAL_FIELDS:
        filled = sum(
            1
            for a in articles
            if getattr(a, fld, "") and str(getattr(a, fld, "")).strip()
        )
        detail[fld] = {
            "filled": filled,
            "total": len(articles),
            "rate": round(filled / len(articles) * 100, 2),
        }

    req_rate = sum(detail[fld]["rate"] for fld in REQUIRED_FIELDS)
    opt_rate = sum(detail[fld]["rate"] for fld in OPTIONAL_FIELDS)
    overall = req_rate * 0.8 / len(REQUIRED_FIELDS) + opt_rate * 0.2 / len(OPTIONAL_FIELDS)

    return {
        "overall": round(overall, 2),
        "details": detail,
        "article_count": len(articles),
    }


def validate_uniqueness(articles: List[Article]) -> Dict[str, Any]:
    """计算URL去重率"""
    if not articles:
        return {"rate": 100.0, "total": 0, "unique": 0, "duplicates": []}
    urls = [a.url for a in articles if a.url]
    unique_urls = set(urls)
    duplicates = list(set(u for u in urls if urls.count(u) > 1 and u))
    return {
        "rate": round(len(unique_urls) / len(urls) * 100, 2) if urls else 0,
        "total": len(urls),
        "unique": len(unique_urls),
        "duplicates": duplicates[:10],
    }


def validate_schema(
    articles: List[Article],
) -> List[SchemaViolation]:
    """Schema合规性检查"""
    violations = []
    for a in articles:
        # 必填字段非空
        for fld in REQUIRED_FIELDS:
            val = getattr(a, fld, "")
            if not val or not str(val).strip():
                violations.append(
                    SchemaViolation(
                        article_id=a.article_id,
                        rule=f"required_field:{fld}",
                        severity="ERROR",
                        detail=f"必填字段 '{fld}' 为空",
                    )
                )
        # URL格式
        if a.url and not (a.url.startswith("http") or a.url.startswith("//") or a.url.startswith("/")):
            violations.append(
                SchemaViolation(
                    article_id=a.article_id,
                    rule="url_format",
                    severity="WARN",
                    detail=f"URL格式异常: {a.url[:80]}",
                )
            )
        # 标题长度
        if a.title and not (5 <= len(a.title) <= 200):
            violations.append(
                SchemaViolation(
                    article_id=a.article_id,
                    rule="title_length",
                    severity="WARN",
                    detail=f"标题长度异常: {len(a.title)}",
                )
            )
        # 无效content_type枚举值
        if hasattr(a, "content_type") and isinstance(a.content_type, str):
            try:
                ContentType(a.content_type)
            except ValueError:
                violations.append(
                    SchemaViolation(
                        article_id=a.article_id,
                        rule="content_type_enum",
                        severity="WARN",
                        detail=f"无效content_type: {a.content_type}",
                    )
                )
    return violations


def compute_overall_score(
    completeness: Dict[str, Any],
    uniqueness: Dict[str, Any],
    timeliness_valid_rate: float,
) -> Dict[str, Any]:
    """
    综合评分（step4 §4.1 加权公式）
    完整率40% + 去重率30% + 时效有效率30%
    """
    score = (
        completeness.get("overall", 0) * 0.4
        + uniqueness.get("rate", 0) * 0.3
        + timeliness_valid_rate * 0.3
    )
    comp_overall = completeness.get("overall", 0)

    if score >= 90 and comp_overall >= 80:
        status = "PASS (优秀)"
    elif score >= 80 and comp_overall >= 70:
        status = "PASS"
    elif score >= 60:
        status = "WARN"
    else:
        status = "FAIL"

    return {
        "status": status,
        "score": round(score, 2),
        "completeness": completeness,
        "uniqueness": uniqueness,
        "timeliness_valid_rate": round(timeliness_valid_rate, 2),
    }


# ============================================================================
# Main Validator (integrates with step5 report format)
# ============================================================================

class DataIntegrityValidator:
    """
    数据完整性与时效性验证器
    集成步骤4验证规则 + 步骤5报告格式
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_age_days: int = 365,
    ):
        self.db = ContentDatabase(db_path)
        self.max_age_days = max_age_days
        self._last_validation: Optional[Dict[str, Any]] = None

    def validate_all(self) -> Dict[str, Any]:
        """执行完整验证流程"""
        articles = self.db.get_all_articles()
        result = self._run_validation(articles)
        self._last_validation = result
        return result

    def _run_validation(
        self, articles: List[Article]
    ) -> Dict[str, Any]:
        completeness = validate_completeness(articles)
        uniqueness = validate_uniqueness(articles)

        timeliness_results = [
            validate_timeliness(a, self.max_age_days) for a in articles
        ]
        timeliness_valid_count = sum(
            1 for t in timeliness_results if t.is_valid
        )
        timeliness_valid_rate = (
            timeliness_valid_count / len(articles) * 100
            if articles
            else 0.0
        )

        schema_violations = validate_schema(articles)
        overall = compute_overall_score(
            completeness, uniqueness, timeliness_valid_rate
        )

        # Build step5-compatible report structure
        report: Dict[str, Any] = {
            **overall,
            "schema_violations": [v.to_dict() for v in schema_violations],
            "timeliness_invalid": [
                t.to_dict()
                for t in timeliness_results
                if not t.is_valid
            ][:10],
            "articles_analyzed": len(articles),
            "validation_timestamp": datetime.now().isoformat(),
            "max_age_days": self.max_age_days,
        }
        return report

    def get_last_result(self) -> Optional[Dict[str, Any]]:
        return self._last_validation

    def validate_search_results(
        self, raw_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        验证searcher返回的原始结果（dict格式）
        将dict映射为Article-like结构后再验证
        """
        articles = []
        for r in raw_results:
            a = Article(
                article_id=r.get("source", "") + "_" + r.get("url", "")[:32],
                title=r.get("title", ""),
                url=r.get("url", ""),
                excerpt=r.get("snippet", ""),
                published_at=r.get("published_time", ""),
            )
            articles.append(a)
        return self._run_validation(articles)

    def export_report_md(self, output_path: str) -> str:
        """
        导出Markdown格式报告（兼容步骤5模板）
        """
        result = self._last_validation or self.validate_all()
        lines: List[str] = []
        lines.append(f"# 数据完整性与时效性验证报告")
        lines.append(f"")
        lines.append(f"**验证时间**: {result.get('validation_timestamp', 'N/A')}")
        lines.append(f"**状态**: {result.get('status', 'N/A')}  ")
        lines.append(f"**综合评分**: {result.get('score', 0):.2f}/100")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

        # 字段完整率
        comp = result.get("completeness", {})
        lines.append(f"## 一、字段完整性 ({comp.get('overall', 0):.2f}%)")
        lines.append(f"")
        lines.append(f"| 字段 | 已填充 | 总数 | 完整率 |")
        lines.append(f"|------|--------|------|--------|")
        for fld, info in comp.get("details", {}).items():
            lines.append(f"| {fld} | {info['filled']} | {info['total']} | {info['rate']:.2f}% |")
        lines.append(f"")

        # 去重率
        uniq = result.get("uniqueness", {})
        lines.append(f"## 二、数据去重 ({uniq.get('rate', 0):.2f}%)")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 总URL数 | {uniq.get('total', 0)} |")
        lines.append(f"| 唯一URL | {uniq.get('unique', 0)} |")
        if uniq.get("duplicates"):
            lines.append(f"| 重复数 | {len(uniq['duplicates'])} |")
        lines.append(f"")

        # 时效性
        tl = result.get("timeliness_invalid", [])
        valid_rate = result.get("timeliness_valid_rate", 0)
        lines.append(f"## 三、时效性验证 (有效率 {valid_rate:.2f}%)")
        lines.append(f"")
        lines.append(f"最大允许年龄: {result.get('max_age_days', 365)} 天")
        if tl:
            lines.append(f"\n失效条目（前{min(len(tl), 5)}条）:")
            lines.append(f"| 文章ID | 发布时间 | 问题 |")
            lines.append(f"|--------|----------|------|")
            for t in tl[:5]:
                lines.append(
                    f"| {t.get('article_id','')} | {t.get('published_time','')} | {t.get('message','')} |"
                )
        lines.append(f"")

        # Schema违规
        violations = result.get("schema_violations", [])
        if violations:
            lines.append(f"## 四、Schema违规 ({len(violations)}条)")
            lines.append(f"")
            lines.append(f"| 文章ID | 规则 | 严重级 | 详情 |")
            lines.append(f"|--------|------|--------|------|")
            for v in violations[:10]:
                lines.append(
                    f"| {v.get('article_id','')} | {v.get('rule','')} | {v.get('severity','')} | {v.get('detail','')} |"
                )
            lines.append(f"")

        # 验收判定
        lines.append(f"## 五、验收判定")
        lines.append(f"")
        score = result.get("score", 0)
        comp_overall = comp.get("overall", 0)
        lines.append(f"- 综合评分: **{score:.2f}**")
        lines.append(f"- 字段完整率: **{comp_overall:.2f}%**")
        lines.append(f"- 判定结果: **{result.get('status', 'N/A')}**")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

        content = "\n".join(lines)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(content, encoding="utf-8")
        return output_path


# ============================================================================
# CLI entry point
# ============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Data Integrity & Timeliness Validator"
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to content_db.sqlite (default: skill data dir)",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=365,
        help="Max allowed article age in days (default: 365)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output markdown report path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of markdown",
    )
    args = parser.parse_args()

    validator = DataIntegrityValidator(
        db_path=args.db_path, max_age_days=args.max_age_days
    )
    result = validator.validate_all()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.output:
        validator.export_report_md(args.output)
        print(f"Report saved to: {args.output}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
