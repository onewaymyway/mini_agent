"""
L3 业务校验器 + FinanceData 统一契约验证层
行情数据、财务数据、新闻数据的业务逻辑校验
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .pipeline import BaseCleaner, CleanLevel, CleanResult
from finance_toolkit.models.finance_data import FinanceData


class QuoteValidator(BaseCleaner):
    """L3: 行情数据校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['quote', 'kline']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 价格逻辑校验
        for field in ['open', 'high', 'low', 'close', 'price', 'pre_close']:
            val = payload.get(field)
            if val is not None and val <= 0:
                issues.append(f"{field} 必须 > 0: {val}")
        
        # 2. 高低价包含关系
        if all(payload.get(f) is not None for f in ['open', 'high', 'low', 'close']):
            o, h, low_price, c = payload['open'], payload['high'], payload['low'], payload['close']
            if not (low_price <= o <= h and low_price <= c <= h):
                issues.append(f"高低价包含关系异常: O={o} H={h} L={low_price} C={c}")
        
        # 3. 涨跌幅校验
        if payload.get('pre_close') and payload.get('close'):
            calc_pct = (payload['close'] - payload['pre_close']) / payload['pre_close'] * 100
            if payload.get('pct_chg') and abs(payload['pct_chg'] - calc_pct) > 0.02:
                warnings.append(f"涨跌幅不匹配: 字段={payload['pct_chg']:.2f}% 计算={calc_pct:.2f}%")
        
        # 4. 成交量/额非负
        for field in ['volume', 'amount']:
            if payload.get(field) is not None and payload[field] < 0:
                issues.append(f"{field} 不能为负: {payload[field]}")
        
        # 5. 换手率合理性 (0-100%)
        if payload.get('turnover_rate') is not None:
            if not (0 <= payload['turnover_rate'] <= 100):
                warnings.append(f"换手率异常: {payload['turnover_rate']}%")
        
        # 6. 振幅合理性
        if payload.get('amplitude') is not None:
            if not (0 <= payload['amplitude'] <= 100):
                warnings.append(f"振幅异常: {payload['amplitude']}%")
        
        # 7. 静默价格异动检测 (> 20% 或 < -20%)
        if payload.get('pre_close') and payload.get('close'):
            pct = (payload['close'] - payload['pre_close']) / payload['pre_close'] * 100
            if abs(pct) > 20:
                warnings.append(f"价格异动: {pct:.2f}% (可能除权/除息/数据错误)")
        
        # 8. 量价配合检查
        if payload.get('volume') is not None and payload.get('pct_chg') is not None:
            if payload['volume'] == 0 and abs(payload['pct_chg']) > 0.1:
                warnings.append(f"零成交量但有涨跌幅: {payload['pct_chg']}%")
        
        metrics = {
            'price_range': f"{payload.get('low', 'N/A')} - {payload.get('high', 'N/A')}",
            'pct_chg': payload.get('pct_chg'),
            'volume': payload.get('volume'),
            'turnover_rate': payload.get('turnover_rate'),
        }
        
        return CleanResult(
            data=raw_data,
            level=self.level,
            passed=len(issues) == 0,
            issues=issues + warnings,
            metrics=metrics
        )


class FinancialValidator(BaseCleaner):
    """L3: 财务数据校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['financial']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 报表日期合理性
        if payload.get('report_date'):
            rd = payload['report_date']
            if isinstance(rd, datetime):
                if rd > datetime.now(timezone.utc) + timedelta(days=30):
                    warnings.append(f"报告期在未来: {rd}")
                if rd.year < 2000:
                    issues.append(f"报告期异常: {rd}")
        
        # 2. 利润表恒等式校验 (允许 1% 误差)
        revenue = payload.get('revenue')
        net_profit = payload.get('net_profit')
        if revenue and net_profit and revenue != 0:
            margin = net_profit / revenue
            if margin > 1 or margin < -5:  # 净利率 > 100% 或 < -500%
                warnings.append(f"净利率异常: {margin*100:.1f}%")
        
        # 3. ROE 合理性
        roe = payload.get('roe')
        if roe is not None and (roe > 100 or roe < -100):
            warnings.append(f"ROE 异常: {roe}%")
        
        # 4. 同比/环比字段一致性
        for field in ['revenue_yoy', 'net_profit_yoy', 'eps_yoy', 'revenue_qoq', 'net_profit_qoq']:
            if payload.get(field) is not None and abs(payload[field]) > 1000:
                warnings.append(f"{field} 同比/环比异常: {payload[field]}%")
        
        # 5. 资产负债表平衡
        total_assets = payload.get('total_assets')
        total_liab = payload.get('total_liab')
        equity = payload.get('equity')
        if all(v is not None for v in [total_assets, total_liab, equity]):
            if abs(total_assets - (total_liab + equity)) / total_assets > 0.01:
                warnings.append(f"资产负债表不平衡: 资产={total_assets} 负债+权益={total_liab + equity}")
        
        # 6. 每股指标合理性
        eps = payload.get('eps')
        bps = payload.get('bps')  # 每股净资产
        if eps is not None and bps is not None and bps != 0:
            if eps / bps > 5:  # EPS/BPS > 5 异常
                warnings.append(f"EPS/BPS 比异常: {eps/bps:.2f}")
        
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues+warnings)


class NewsValidator(BaseCleaner):
    """L3: 新闻/文本数据校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['news', 'guba', 'report']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 标题非空
        if not payload.get('title') or len(payload['title'].strip()) < 2:
            issues.append("标题为空或过短")
        
        # 2. 正文长度
        content = payload.get('content', '')
        if len(content) < 50:
            warnings.append(f"正文过短: {len(content)} 字符")
        if len(content) > 500000:
            warnings.append(f"正文过长: {len(content)} 字符，建议截断")
        
        # 3. 发布时间不晚于抓取时间
        pub_time = payload.get('publish_time')
        crawl_time = raw_data.get('crawl_time')
        if pub_time and crawl_time and pub_time > crawl_time + timedelta(hours=1):
            warnings.append(f"发布时间晚于抓取时间: {pub_time} > {crawl_time}")
        
        # 4. URL 格式
        url = payload.get('url', '')
        if url and not url.startswith(('http://', 'https://')):
            issues.append(f"URL 格式无效: {url}")
        
        # 5. 股票代码格式校验
        symbols = payload.get('symbols', [])
        for sym in symbols:
            if not re.match(r'^\d{6}\.(SZ|SH|BJ)$', sym) and not re.match(r'^[A-Z]{1,5}-?[A-Z]{0,5}$', sym):
                warnings.append(f"股票代码格式可疑: {sym}")
        
        # 6. 来源字段
        if not payload.get('author') and not payload.get('source'):
            warnings.append("缺少作者/来源信息")
        
        # 7. 关键词/实体
        if not payload.get('keywords') and not payload.get('entities'):
            warnings.append("缺少关键词/实体标注")
        
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues+warnings)


class ReportValidator(BaseCleaner):
    """L3: 研报数据校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['report']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 标题
        if not payload.get('title') or len(payload['title'].strip()) < 5:
            issues.append("研报标题为空或过短")
        
        # 2. 机构/作者
        if not payload.get('institution') and not payload.get('author'):
            warnings.append("缺少机构/作者信息")
        
        # 3. 评级
        rating = payload.get('rating')
        valid_ratings = ['买入', '增持', '中性', '减持', '卖出', 'Buy', 'Hold', 'Sell', 'Overweight', 'Underweight']
        if rating and rating not in valid_ratings:
            warnings.append(f"评级格式非标准: {rating}")
        
        # 4. 目标价
        target_price = payload.get('target_price')
        if target_price is not None and target_price <= 0:
            issues.append(f"目标价异常: {target_price}")
        
        # 5. 发布日期
        pub_date = payload.get('publish_date')
        if pub_date and isinstance(pub_date, datetime):
            if pub_date > datetime.now(timezone.utc) + timedelta(days=1):
                warnings.append(f"发布日期在未来: {pub_date}")
        
        # 6. 正文长度
        content = payload.get('content', '')
        if len(content) < 200:
            warnings.append(f"研报正文过短: {len(content)} 字符")
        
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues+warnings)


class GubaValidator(BaseCleaner):
    """L3: 股吧帖子校验"""
    
    level = CleanLevel.L3_VALIDATION
    source_types = ['guba']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        issues = []
        warnings = []
        
        # 1. 标题
        if not payload.get('title') or len(payload['title'].strip()) < 2:
            issues.append("帖子标题为空或过短")
        
        # 2. 内容
        content = payload.get('content', '')
        if len(content) < 10:
            warnings.append(f"帖子内容过短: {len(content)} 字符")
        
        # 3. 阅读/评论数非负
        for field in ['read_count', 'comment_count']:
            if payload.get(field) is not None and payload[field] < 0:
                issues.append(f"{field} 不能为负: {payload[field]}")
        
        # 4. 用户信息
        if not payload.get('user_id') and not payload.get('user_name'):
            warnings.append("缺少用户信息")
        
        # 5. 发布时间
        pub_time = payload.get('publish_time')
        if pub_time and isinstance(pub_time, datetime):
            if pub_time > datetime.now(timezone.utc) + timedelta(hours=1):
                warnings.append(f"发布时间在未来: {pub_time}")
        
        return CleanResult(data=raw_data, level=self.level, passed=len(issues)==0, issues=issues+warnings)


# ═══════════════════════════════════════════════════════════════
# FinanceData 统一契约验证层（L1-L2）
# ═══════════════════════════════════════════════════════════════

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from finance_toolkit.plugins.types import DataType
from finance_toolkit.data_validator import DataValidator, SeverityLevel, ValidationIssue, ValidationResult


@dataclass
class FieldIssue:
    """FinanceData 字段级验证问题"""
    dimension: str          # "field" | "type" | "range"
    field_path: str         # 如 "symbol" / "payload.open"
    value: Any
    expected: str           # 期望描述
    actual: str             # 实际值描述
    suggestion: str = ""

    def to_validation_issue(self, rule_id: str) -> ValidationIssue:
        return ValidationIssue(
            rule_id=rule_id,
            rule_name=f"{self.dimension}_{self.field_path}",
            severity=SeverityLevel.WARNING,
            field=self.field_path,
            value=str(self.value),
            message=f"[{self.dimension}] {self.field_path}: {self.expected}, got {self.actual}",
            suggestion=self.suggestion,
        )


@dataclass
class FinanceDataValidationResult:
    """FinanceData 综合验证结果"""
    is_valid: bool
    field_issues: List[FieldIssue] = field(default_factory=list)
    type_issues: List[FieldIssue] = field(default_factory=list)
    range_issues: List[FieldIssue] = field(default_factory=list)
    base_validation: Optional[ValidationResult] = None

    @property
    def total_issues(self) -> int:
        return len(self.field_issues) + len(self.type_issues) + len(self.range_issues)

    @property
    def health_score(self) -> float:
        total = self.total_issues
        if total == 0:
            return 100.0
        penalty = total * 10
        return max(0.0, 100.0 - penalty)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "health_score": round(self.health_score, 1),
            "total_issues": self.total_issues,
            "field_issues": [
                {"field": i.field_path, "expected": i.expected, "actual": i.actual, "suggestion": i.suggestion}
                for i in self.field_issues
            ],
            "type_issues": [
                {"field": i.field_path, "expected": i.expected, "actual": i.actual}
                for i in self.type_issues
            ],
            "range_issues": [
                {"field": i.field_path, "expected": i.expected, "actual": i.actual}
                for i in self.range_issues
            ],
        }

    def summary(self) -> str:
        mark = "\u2713" if self.is_valid else "\u2717"
        lines = [
            f"FinanceData 验证结果 [{mark}] 健康评分: {self.health_score:.1f}/100",
            f"  字段校验: {len(self.field_issues)} 个问题",
            f"  类型校验: {len(self.type_issues)} 个问题",
            f"  范围校验: {len(self.range_issues)} 个问题",
        ]
        if self.base_validation:
            lines.append(f"  基础验证: {self.base_validation.verdict} (评分 {self.base_validation.health_score:.1f})")
        return "\n".join(lines)


class FieldValidator:
    """
    L1: FinanceData 必填字段校验
    确保 source / data_type / symbol / timestamp / payload 存在且非空
    """

    REQUIRED_FIELDS: Dict[str, str] = {
        "source": "str (non-empty)",
        "data_type": "DataType enum or valid string",
        "symbol": "str (non-empty, format: 600000.SH / 000001.SZ)",
        "timestamp": "ISO 8601 string (e.g. 2026-08-15T10:30:00+08:00)",
        "payload": "dict (non-empty, contains business fields)",
    }

    def validate(self, fd) -> List[FieldIssue]:
        from finance_toolkit.models.finance_data import FinanceData
        issues: List[FieldIssue] = []

        if not fd.source or not isinstance(fd.source, str):
            issues.append(FieldIssue(
                dimension="field", field_path="source",
                value=fd.source,
                expected="required (non-empty str)",
                actual=f"{type(fd.source).__name__}: {fd.source!r}",
                suggestion="必须提供有效的数据源标识，如 'akshare', 'eastmoney'",
            ))

        dt = fd.data_type
        if dt is None:
            issues.append(FieldIssue(
                dimension="field", field_path="data_type",
                value=None, expected="required (DataType enum)",
                actual="None", suggestion="必须指定 DataType 枚举值",
            ))
        elif not isinstance(dt, DataType):
            issues.append(FieldIssue(
                dimension="field", field_path="data_type",
                value=dt, expected="DataType enum",
                actual=f"{type(dt).__name__}: {dt!r}",
                suggestion="请使用 finance_toolkit.plugins.types.DataType 枚举",
            ))

        if not fd.symbol or not isinstance(fd.symbol, str):
            issues.append(FieldIssue(
                dimension="field", field_path="symbol",
                value=fd.symbol,
                expected="required (non-empty str, format: XXXXXX.SH/SZ)",
                actual=f"{type(fd.symbol).__name__}: {fd.symbol!r}",
                suggestion="股票代码格式应为 '600000.SH' 或 '000001.SZ'",
            ))

        if not fd.timestamp or not isinstance(fd.timestamp, str):
            issues.append(FieldIssue(
                dimension="field", field_path="timestamp",
                value=fd.timestamp,
                expected="required (ISO 8601 str)",
                actual=f"{type(fd.timestamp).__name__}: {fd.timestamp!r}",
                suggestion="请使用 ISO 8601 格式，如 '2026-08-15T10:30:00+08:00'",
            ))
        else:
            try:
                datetime.fromisoformat(fd.timestamp.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                issues.append(FieldIssue(
                    dimension="field", field_path="timestamp",
                    value=fd.timestamp,
                    expected="ISO 8601 format (e.g. 2026-08-15T10:30:00+08:00)",
                    actual=fd.timestamp,
                    suggestion="请转换为标准 ISO 8601 格式",
                ))

        if not fd.payload or not isinstance(fd.payload, dict):
            issues.append(FieldIssue(
                dimension="field", field_path="payload",
                value=fd.payload,
                expected="required (non-empty dict)",
                actual=f"{type(fd.payload).__name__}: {fd.payload!r}",
                suggestion="payload 必须是包含业务字段的字典",
            ))
        elif len(fd.payload) == 0:
            issues.append(FieldIssue(
                dimension="field", field_path="payload",
                value=fd.payload, expected="required (non-empty dict)",
                actual="empty dict", suggestion="payload 至少应包含一个业务字段",
            ))

        return issues


class TypeValidator:
    """
    L2: payload 内部字段 Python 类型校验
    对常见金融字段定义期望类型，允许 None（缺失）
    """

    TYPE_MAP: Dict[str, tuple] = {
        "open": (int, float), "high": (int, float), "low": (int, float),
        "close": (int, float), "price": (int, float), "pre_close": (int, float),
        "volume": (int, float), "amount": (int, float),
        "change": (int, float), "change_pct": (int, float), "pct_chg": (int, float),
        "turnover_rate": (int, float), "amplitude": (int, float),
        "pe_ratio": (int, float), "pb_ratio": (int, float),
        "total_mv": (int, float), "circ_mv": (int, float),
        "date": (str, ), "datetime": (str, ),
        "revenue": (int, float), "net_profit": (int, float),
        "total_assets": (int, float), "total_liab": (int, float), "equity": (int, float),
        "eps": (int, float), "bps": (int, float), "roe": (int, float),
        "report_date": (str, ),
        "north_buy": (int, float), "north_sell": (int, float), "net_inflow": (int, float),
        "nav": (int, float), "fund_code": str, "fund_name": str,
        "index_code": str, "index_name": str,
    }

    def validate(self, payload: Dict[str, Any]) -> List[FieldIssue]:
        issues: List[FieldIssue] = []
        for path, expected_types in self.TYPE_MAP.items():
            if path not in payload:
                continue
            val = payload[path]
            if val is None:
                continue
            if not isinstance(val, expected_types):
                issues.append(FieldIssue(
                    dimension="type", field_path=f"payload.{path}",
                    value=val, expected=f"one of {expected_types}",
                    actual=f"{type(val).__name__}: {val!r}",
                    suggestion=f"字段 '{path}' 应转换为目标类型",
                ))
        return issues


class RangeValidator:
    """
    L3: payload 数值字段业务范围校验
    复用 data_validator.VALUE_RANGES 中的约束
    """

    def validate(self, payload: Dict[str, Any]) -> List[FieldIssue]:
        ranges = DataValidator.VALUE_RANGES
        issues: List[FieldIssue] = []
        for field_name, (low, high) in ranges.items():
            if field_name not in payload:
                continue
            val = payload[field_name]
            if val is None or not isinstance(val, (int, float)):
                continue
            if val < low or val > high:
                issues.append(FieldIssue(
                    dimension="range", field_path=f"payload.{field_name}",
                    value=val, expected=f"[{low}, {high}]", actual=str(val),
                    suggestion=f"值 {val} 超出合理范围 [{low}, {high}]，请核查数据源",
                ))
        return issues


class FinanceDataValidator:
    """
    FinanceData 统一验证中间件

    组合三层校验：
      1. FieldValidator    — FinanceData 契约字段完整性 (L1)
      2. TypeValidator     — payload 内部字段 Python 类型 (L2)
      3. RangeValidator    — payload 数值业务范围 (L3)

    同时支持调用底层 DataValidator 进行 97 条规则全量验证。
    """

    def __init__(self, enable_base_validator: bool = True, strict_mode: bool = False):
        self.enable_base_validator = enable_base_validator
        self.strict_mode = strict_mode
        self._field_v = FieldValidator()
        self._type_v = TypeValidator()
        self._range_v = RangeValidator()
        self._base_validator = DataValidator(strict_mode=strict_mode) if enable_base_validator else None

    def validate(self, fd) -> FinanceDataValidationResult:
        """对 FinanceData 对象执行完整验证"""
        from finance_toolkit.models.finance_data import FinanceData
        if not isinstance(fd, FinanceData):
            return FinanceDataValidationResult(
                is_valid=False,
                field_issues=[FieldIssue(
                    dimension="field", field_path="root", value=str(type(fd)),
                    expected="FinanceData instance", actual=f"{type(fd).__name__}",
                    suggestion="必须传入 FinanceData 对象",
                )],
            )

        result = FinanceDataValidationResult(is_valid=True)
        result.field_issues = self._field_v.validate(fd)
        if fd.payload:
            result.type_issues = self._type_v.validate(fd.payload)
            result.range_issues = self._range_v.validate(fd.payload)

        if self._base_validator and self.enable_base_validator:
            try:
                base_result = self._base_validator.validate(
                    fd.to_dict(),
                    data_type=fd.data_type.value if isinstance(fd.data_type, DataType) else str(fd.data_type),
                    symbol=fd.symbol,
                )
                result.base_validation = base_result
            except Exception:
                pass

        result.is_valid = len(result.field_issues) == 0
        return result

    def validate_many(self, items: List) -> List[FinanceDataValidationResult]:
        """批量验证"""
        return [self.validate(item) for item in items]

    def validate_and_normalize(self, fd) -> FinanceData:
        """验证并尝试自动修复可修复的类型问题"""
        result = self.validate(fd)
        if result.is_valid:
            return fd

        from finance_toolkit.models.finance_data import FinanceData
        normalized_payload = dict(fd.payload) if fd.payload else {}
        for path, expected_types in TypeValidator.TYPE_MAP.items():
            if path not in normalized_payload:
                continue
            val = normalized_payload[path]
            if val is None or isinstance(val, expected_types):
                continue
            try:
                if float in expected_types:
                    normalized_payload[path] = float(val)
                elif int in expected_types:
                    normalized_payload[path] = int(float(val))
            except (ValueError, TypeError):
                pass

        return FinanceData(
            source=fd.source, data_type=fd.data_type, symbol=fd.symbol,
            timestamp=fd.timestamp, payload=normalized_payload,
            raw=fd.raw, meta=fd.meta, error=fd.error,
        )


def validate_finance_data(fd, **kwargs) -> FinanceDataValidationResult:
    """便捷函数：验证单个 FinanceData 对象"""
    return FinanceDataValidator(**kwargs).validate(fd)


def validate_finance_data_batch(items: List, **kwargs) -> List[FinanceDataValidationResult]:
    """便捷函数：批量验证 FinanceData 列表"""
    return FinanceDataValidator(**kwargs).validate_many(items)
