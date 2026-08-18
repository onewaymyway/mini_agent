# -*- coding: utf-8 -*-
"""
数据验证规则集 - 扩展版本

包含:
- I001-I020: 输入校验规则 (URL、API响应、网络参数)
- TS001-TS025: 时间序列验证规则 (K线、历史数据)
- L020-L040: 扩展业务逻辑约束 (市场特定规则)
- C007-C020: 跨源一致性增强规则
- NV001-NV015: 数值验证增强规则

使用示例:
    from finance_toolkit.validation_rules import (
        InputValidationRules,
        TimeSeriesValidationRules,
        BusinessLogicValidationRules,
        CrossSourceValidationRules,
        NumericValidationRules
    )
"""

import re
import ipaddress
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum


class SeverityLevel(Enum):
    """问题严重程度"""
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    """验证问题"""
    rule_id: str
    rule_name: str
    severity: SeverityLevel
    field: str
    value: Any
    message: str
    suggestion: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'rule_id': self.rule_id,
            'rule_name': self.rule_name,
            'severity': self.severity.value,
            'field': self.field,
            'value': str(self.value) if self.value is not None else None,
            'message': self.message,
            'suggestion': self.suggestion,
            'details': self.details
        }


# ==================== 输入校验规则 ====================

class InputValidationRules:
    """
    输入校验规则 I001-I020
    
    覆盖: URL格式、API响应结构、网络参数、请求头、超时配置
    """
    
    # URL格式正则
    URL_PATTERN = re.compile(
        r'^https?://'
        r'([a-zA-Z0-9_-]+\.)+[a-zA-Z]{2,}'
        r'(:\d+)?'
        r'(/[\w./%=&?+#-]*)?$'
    )
    
    # 常见金融数据API域名白名单
    FINANCE_DOMAIN_WHITELIST = {
        'eastmoney.com', 'sina.com.cn', 'finance.sina.com.cn',
        'stock.finance.sina.com.cn', 'quotes.sina.cn',
        'api.doctor.com', 'api.jijinhao.com',
        'push2.eastmoney.com', 'push2his.eastmoney.com',
        'qt.gtimg.cn', 'hq.sinajs.cn',
        'stock.xueqiu.com', 'stock.xx.xueqiu.com',
        'api.tushare.pro', 'jprx.m.qq.com',
        'datacenter.webapi.com', 'interface.sina.cn',
        'hq.sinajs.cn', 'money.finance.sina.com.cn',
        'quotes.finance.qq.com', 'vip.stock.finance.qq.com',
        'gb.cfi.cn',
    }
    
    # 禁止的URL模式（爬虫防护、WAF等）
    BLOCKED_URL_PATTERNS = [
        r'/waf/', r'/block/', r'/captcha/',
        r'/verify/', r'/challenge/',
        r'/js/challenge/',
    ]
    
    # 合法HTTP方法
    VALID_HTTP_METHODS = {'GET', 'POST', 'PUT', 'DELETE', 'PATCH'}
    
    # 合法Content-Type
    VALID_CONTENT_TYPES = {
        'application/json', 'application/x-www-form-urlencoded',
        'text/html', 'text/plain', 'application/xml',
        'text/xml', 'multipart/form-data',
    }
    
    # 推荐User-Agent模式
    RECOMMENDED_UA_PATTERNS = [
        r'Mozilla/5\.0', r'Python-urllib', r'requests/',
        r'curl/', r'httpclient/', r'okhttp/',
    ]
    
    # 超时配置范围（秒）
    TIMEOUT_RANGE = (1, 120)
    
    # 并发限制范围
    CONCURRENCY_RANGE = (1, 50)
    
    # 重试次数范围
    RETRY_RANGE = (0, 10)
    
    # 最大响应大小（MB）
    MAX_RESPONSE_SIZE_MB = 50
    
    @classmethod
    def validate_url(cls, url: str) -> List[ValidationIssue]:
        """I001: URL格式校验"""
        issues = []
        
        if not url or not isinstance(url, str):
            issues.append(ValidationIssue(
                rule_id='I001',
                rule_name='URL非空校验',
                severity=SeverityLevel.CRITICAL,
                field='url',
                value=url,
                message='URL为空或无效类型',
                suggestion='请提供有效的URL字符串'
            ))
            return issues
        
        if not cls.URL_PATTERN.match(url):
            issues.append(ValidationIssue(
                rule_id='I001',
                rule_name='URL格式校验',
                severity=SeverityLevel.ERROR,
                field='url',
                value=url,
                message=f'URL格式无效: {url}',
                suggestion='请使用标准URL格式: https://domain/path'
            ))
        
        # 检查是否在白名单中
        domain = cls._extract_domain(url)
        if domain and domain not in cls.FINANCE_DOMAIN_WHITELIST:
            issues.append(ValidationIssue(
                rule_id='I002',
                rule_name='域名白名单校验',
                severity=SeverityLevel.WARNING,
                field='url',
                value=domain,
                message=f'域名不在白名单中: {domain}',
                suggestion='请确认数据来源是否可信',
                details={'domain': domain}
            ))
        
        # 检查是否包含WAF特征
        for pattern in cls.BLOCKED_URL_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                issues.append(ValidationIssue(
                    rule_id='I003',
                    rule_name='WAF拦截特征检测',
                    severity=SeverityLevel.ERROR,
                    field='url',
                    value=url,
                    message=f'URL包含WAF拦截特征: {pattern}',
                    suggestion='可能需要使用代理或更换请求方式'
                ))
                break
        
        return issues
    
    @classmethod
    def validate_api_response(cls, response_data: Any, expected_type: str = 'dict') -> List[ValidationIssue]:
        """I004: API响应结构校验"""
        issues = []
        
        if response_data is None:
            issues.append(ValidationIssue(
                rule_id='I004',
                rule_name='响应非空校验',
                severity=SeverityLevel.CRITICAL,
                field='response',
                value=None,
                message='API响应为空',
                suggestion='检查网络连接或重试请求'
            ))
            return issues
        
        type_map = {'dict': dict, 'list': list, 'str': str, 'number': (int, float)}
        expected_cls = type_map.get(expected_type, dict)
        if not isinstance(response_data, expected_cls):
            issues.append(ValidationIssue(
                rule_id='I005',
                rule_name='响应类型校验',
                severity=SeverityLevel.ERROR,
                field='response',
                value=str(type(response_data)),
                message=f'响应类型不匹配: 期望{expected_type}, 实际{type(response_data).__name__}',
                suggestion='检查API文档确认响应格式'
            ))
        
        if isinstance(response_data, dict):
            for error_field in ['error', 'errno', 'error_code', 'code', 'status']:
                if error_field in response_data:
                    error_value = response_data[error_field]
                    if isinstance(error_value, (int, str)) and error_value not in [0, '0', 'success', 'SUCCESS', None]:
                        issues.append(ValidationIssue(
                            rule_id='I006',
                            rule_name='API错误码校验',
                            severity=SeverityLevel.ERROR,
                            field=f'response.{error_field}',
                            value=error_value,
                            message=f'API返回错误码: {error_value}',
                            suggestion='检查API认证或请求参数',
                            details={'error_field': error_field, 'error_value': error_value}
                        ))
        
        try:
            response_size = len(str(response_data))
            if response_size > cls.MAX_RESPONSE_SIZE_MB * 1024 * 1024:
                issues.append(ValidationIssue(
                    rule_id='I007',
                    rule_name='响应大小校验',
                    severity=SeverityLevel.WARNING,
                    field='response',
                    value=f'{response_size / 1024 / 1024:.2f}MB',
                    message=f'响应过大: {response_size / 1024 / 1024:.2f}MB',
                    suggestion='考虑分页请求或压缩数据'
                ))
        except (TypeError, OverflowError):
            pass
        
        return issues    
    @classmethod
    def validate_request_config(cls, config: Dict[str, Any]) -> List[ValidationIssue]:
        """I008-I015: 请求配置校验"""
        issues = []
        
        # I008: HTTP方法校验
        method = config.get('method', 'GET').upper()
        if method not in cls.VALID_HTTP_METHODS:
            issues.append(ValidationIssue(
                rule_id='I008',
                rule_name='HTTP方法校验',
                severity=SeverityLevel.ERROR,
                field='method',
                value=method,
                message=f'无效的HTTP方法: {method}',
                suggestion=f'使用以下方法之一: {cls.VALID_HTTP_METHODS}'
            ))
        
        # I009: 超时配置校验
        timeout = config.get('timeout')
        if timeout is not None:
            try:
                timeout_val = float(timeout)
                if timeout_val < cls.TIMEOUT_RANGE[0] or timeout_val > cls.TIMEOUT_RANGE[1]:
                    issues.append(ValidationIssue(
                        rule_id='I009',
                        rule_name='超时配置校验',
                        severity=SeverityLevel.WARNING,
                        field='timeout',
                        value=timeout_val,
                        message=f'超时配置超出合理范围 [{cls.TIMEOUT_RANGE[0]}, {cls.TIMEOUT_RANGE[1]}]: {timeout_val}s',
                        suggestion=f'建议超时设置在{cls.TIMEOUT_RANGE[0]}-{cls.TIMEOUT_RANGE[1]}秒之间'
                    ))
            except (ValueError, TypeError):
                issues.append(ValidationIssue(
                    rule_id='I009',
                    rule_name='超时配置校验',
                    severity=SeverityLevel.ERROR,
                    field='timeout',
                    value=timeout,
                    message=f'超时配置格式无效: {timeout}',
                    suggestion='请使用数字表示秒数'
                ))
        
        # I010: 并发配置校验
        concurrency = config.get('concurrency')
        if concurrency is not None:
            try:
                conc_val = int(concurrency)
                if conc_val < cls.CONCURRENCY_RANGE[0] or conc_val > cls.CONCURRENCY_RANGE[1]:
                    issues.append(ValidationIssue(
                        rule_id='I010',
                        rule_name='并发配置校验',
                        severity=SeverityLevel.WARNING,
                        field='concurrency',
                        value=conc_val,
                        message=f'并发数超出合理范围 [{cls.CONCURRENCY_RANGE[0]}, {cls.CONCURRENCY_RANGE[1]}]: {conc_val}',
                        suggestion=f'建议并发数设置在{cls.CONCURRENCY_RANGE[0]}-{cls.CONCURRENCY_RANGE[1]}之间'
                    ))
            except (ValueError, TypeError):
                issues.append(ValidationIssue(
                    rule_id='I010',
                    rule_name='并发配置校验',
                    severity=SeverityLevel.ERROR,
                    field='concurrency',
                    value=concurrency,
                    message=f'并发配置格式无效: {concurrency}',
                    suggestion='请使用整数'
                ))
        
        # I011: 重试配置校验
        retries = config.get('retries')
        if retries is not None:
            try:
                retry_val = int(retries)
                if retry_val < cls.RETRY_RANGE[0] or retry_val > cls.RETRY_RANGE[1]:
                    issues.append(ValidationIssue(
                        rule_id='I011',
                        rule_name='重试配置校验',
                        severity=SeverityLevel.WARNING,
                        field='retries',
                        value=retry_val,
                        message=f'重试次数超出合理范围 [{cls.RETRY_RANGE[0]}, {cls.RETRY_RANGE[1]}]: {retry_val}',
                        suggestion=f'建议重试次数设置在{cls.RETRY_RANGE[0]}-{cls.RETRY_RANGE[1]}之间'
                    ))
            except (ValueError, TypeError):
                issues.append(ValidationIssue(
                    rule_id='I011',
                    rule_name='重试配置校验',
                    severity=SeverityLevel.ERROR,
                    field='retries',
                    value=retries,
                    message=f'重试配置格式无效: {retries}',
                    suggestion='请使用整数'
                ))
        
        # I012: User-Agent校验
        headers = config.get('headers', {})
        user_agent = headers.get('User-Agent', '')
        if user_agent:
            ua_valid = any(re.match(pattern, user_agent) for pattern in cls.RECOMMENDED_UA_PATTERNS)
            if not ua_valid and len(user_agent) < 10:
                issues.append(ValidationIssue(
                    rule_id='I012',
                    rule_name='User-Agent校验',
                    severity=SeverityLevel.WARNING,
                    field='headers.User-Agent',
                    value=user_agent,
                    message='User-Agent过短或格式不标准',
                    suggestion='使用完整的浏览器User-Agent字符串'
                ))
        
        # I013: Content-Type校验
        content_type = headers.get('Content-Type', '')
        if content_type and content_type not in cls.VALID_CONTENT_TYPES:
            issues.append(ValidationIssue(
                rule_id='I013',
                rule_name='Content-Type校验',
                severity=SeverityLevel.WARNING,
                field='headers.Content-Type',
                value=content_type,
                message=f'非标准Content-Type: {content_type}',
                suggestion='使用标准Content-Type值'
            ))
        
        # I014: 认证信息校验
        auth = config.get('auth')
        if auth:
            if isinstance(auth, dict):
                if 'token' in auth and not auth['token']:
                    issues.append(ValidationIssue(
                        rule_id='I014',
                        rule_name='认证Token校验',
                        severity=SeverityLevel.ERROR,
                        field='auth.token',
                        value=None,
                        message='认证Token为空',
                        suggestion='请提供有效的API Token'
                    ))
            elif isinstance(auth, str) and not auth:
                issues.append(ValidationIssue(
                    rule_id='I014',
                    rule_name='认证Token校验',
                    severity=SeverityLevel.ERROR,
                    field='auth',
                    value=None,
                    message='认证Token为空',
                    suggestion='请提供有效的API Token'
                ))
        
        # I015: 代理配置校验
        proxy = config.get('proxy')
        if proxy:
            if isinstance(proxy, str):
                if not proxy.startswith(('http://', 'https://', 'socks5://')):
                    issues.append(ValidationIssue(
                        rule_id='I015',
                        rule_name='代理地址校验',
                        severity=SeverityLevel.WARNING,
                        field='proxy',
                        value=proxy,
                        message=f'代理地址格式可能无效: {proxy}',
                        suggestion='请使用 http:// 或 https:// 前缀'
                    ))
            elif isinstance(proxy, dict):
                if 'http' not in proxy and 'https' not in proxy:
                    issues.append(ValidationIssue(
                        rule_id='I015',
                        rule_name='代理地址校验',
                        severity=SeverityLevel.WARNING,
                        field='proxy',
                        value=proxy,
                        message='代理配置缺少HTTP/HTTPS地址',
                        suggestion='请提供完整的代理配置'
                    ))
        
        return issues
    
    @classmethod
    def validate_network_params(cls, params: Dict[str, Any]) -> List[ValidationIssue]:
        """I016-I020: 网络参数校验"""
        issues = []
        
        # I016: 分页参数校验
        page = params.get('page')
        page_size = params.get('page_size') or params.get('pageSize')
        
        if page is not None:
            try:
                page_val = int(page)
                if page_val < 1:
                    issues.append(ValidationIssue(
                        rule_id='I016',
                        rule_name='分页参数校验',
                        severity=SeverityLevel.ERROR,
                        field='page',
                        value=page_val,
                        message=f'页码不能小于1: {page_val}',
                        suggestion='页码应从1开始'
                    ))
            except (ValueError, TypeError):
                issues.append(ValidationIssue(
                    rule_id='I016',
                    rule_name='分页参数校验',
                    severity=SeverityLevel.ERROR,
                    field='page',
                    value=page,
                    message=f'页码格式无效: {page}',
                    suggestion='请使用正整数'
                ))
        
        if page_size is not None:
            try:
                size_val = int(page_size)
                if size_val < 1 or size_val > 1000:
                    issues.append(ValidationIssue(
                        rule_id='I017',
                        rule_name='分页大小校验',
                        severity=SeverityLevel.WARNING,
                        field='page_size',
                        value=size_val,
                        message=f'分页大小超出合理范围 [1, 1000]: {size_val}',
                        suggestion='建议分页大小在100-500之间'
                    ))
            except (ValueError, TypeError):
                issues.append(ValidationIssue(
                    rule_id='I017',
                    rule_name='分页大小校验',
                    severity=SeverityLevel.ERROR,
                    field='page_size',
                    value=page_size,
                    message=f'分页大小格式无效: {page_size}',
                    suggestion='请使用正整数'
                ))
        
        # I018: 日期范围校验
        start_date = params.get('start_date') or params.get('startDate')
        end_date = params.get('end_date') or params.get('endDate')
        
        if start_date and end_date:
            try:
                start = datetime.strptime(str(start_date), '%Y-%m-%d')
                end = datetime.strptime(str(end_date), '%Y-%m-%d')
                if start > end:
                    issues.append(ValidationIssue(
                        rule_id='I018',
                        rule_name='日期范围校验',
                        severity=SeverityLevel.ERROR,
                        field='date_range',
                        value=f'{start_date} ~ {end_date}',
                        message='开始日期晚于结束日期',
                        suggestion='请调整日期范围'
                    ))
                
                date_range = (end - start).days
                if date_range > 365 * 5:
                    issues.append(ValidationIssue(
                        rule_id='I019',
                        rule_name='日期范围长度校验',
                        severity=SeverityLevel.WARNING,
                        field='date_range',
                        value=f'{date_range}天',
                        message=f'日期范围过长: {date_range}天',
                        suggestion='建议分批请求历史数据'
                    ))
            except ValueError:
                issues.append(ValidationIssue(
                    rule_id='I018',
                    rule_name='日期格式校验',
                    severity=SeverityLevel.ERROR,
                    field='date_range',
                    value=f'{start_date} ~ {end_date}',
                    message='日期格式无效，请使用YYYY-MM-DD',
                    suggestion='请检查日期格式'
                ))
        
        # I020: 参数值类型校验
        type_checks = {
            'code': str,
            'symbol': str,
            'market': str,
            'fields': (str, list),
            'limit': int,
        }
        
        for param_name, expected_type in type_checks.items():
            if param_name in params:
                value = params[param_name]
                if not isinstance(value, expected_type):
                    issues.append(ValidationIssue(
                        rule_id='I020',
                        rule_name='参数类型校验',
                        severity=SeverityLevel.WARNING,
                        field=param_name,
                        value=str(value),
                        message=f'参数{param_name}类型不匹配: 期望{expected_type.__name__}',
                        suggestion=f'请将{param_name}转换为{expected_type.__name__}类型'
                    ))
        
        return issues
    
    @classmethod
    def _extract_domain(cls, url: str) -> Optional[str]:
        """从URL中提取域名"""
        try:
            url = re.sub(r'^https?://', '', url)
            domain = url.split('/')[0]
            domain = domain.split(':')[0]
            return domain.lower()
        except Exception:
            return None

# ==================== 时间序列验证规则 ====================

class TimeSeriesValidationRules:
    """
    时间序列验证规则 TS001-TS025
    
    覆盖: K线连续性、数据 gaps、趋势合理性、季节性
    """
    
    REASONABLE_INTERVAL_MINUTES = {
        '1m': (58, 62), '5m': (295, 305), '15m': (895, 905),
        '30m': (1795, 1805), '60m': (3595, 3605), 'daily': (86000, 88000),
    }
    
    MAX_INTERVAL_MULTIPLIER = 2.0
    
    TREND_THRESHOLD = {'daily': 0.15, 'weekly': 0.30, 'monthly': 0.50}
    
    @classmethod
    def validate_kline_continuity(cls, klines: List[Dict], interval: str = 'daily') -> List[ValidationIssue]:
        """TS001-TS005: K线连续性检查"""
        issues = []
        if len(klines) < 2:
            return issues
        
        dates = []
        for i, kline in enumerate(klines):
            date_str = kline.get('date') or kline.get('timestamp')
            if date_str:
                try:
                    if isinstance(date_str, (int, float)):
                        dt = datetime.fromtimestamp(date_str / 1000) if date_str > 1e12 else datetime.fromtimestamp(date_str)
                    else:
                        dt = datetime.strptime(str(date_str), '%Y-%m-%d')
                    dates.append((i, dt))
                except (ValueError, TypeError, OSError):
                    issues.append(ValidationIssue(
                        rule_id='TS001', rule_name='日期格式校验',
                        severity=SeverityLevel.ERROR, field=f'klines[{i}].date',
                        value=date_str, message=f'日期格式无效: {date_str}',
                        suggestion='请使用YYYY-MM-DD或时间戳格式'
                    ))
        
        if len(dates) < 2:
            return issues
        
        dates.sort(key=lambda x: x[1])
        expected_interval = cls.REASONABLE_INTERVAL_MINUTES.get(interval, (86000, 88000))
        
        for i in range(1, len(dates)):
            prev_idx, prev_dt = dates[i-1]
            curr_idx, curr_dt = dates[i]
            gap_seconds = (curr_dt - prev_dt).total_seconds()
            
            if gap_seconds < expected_interval[0] or gap_seconds > expected_interval[1] * cls.MAX_INTERVAL_MULTIPLIER:
                gap_days = gap_seconds / 86400
                severity = SeverityLevel.WARNING if gap_days <= 7 else SeverityLevel.ERROR
                issues.append(ValidationIssue(
                    rule_id='TS002', rule_name='K线间隔异常',
                    severity=severity, field=f'klines[{curr_idx}].date',
                    value=f'{gap_days:.1f}天',
                    message=f'K线间隔异常: 预期{expected_interval[0]/86400:.0f}天, 实际{gap_days:.1f}天',
                    suggestion='检查是否存在数据缺失或重复'
                ))
            
            if curr_dt < prev_dt:
                issues.append(ValidationIssue(
                    rule_id='TS003', rule_name='日期顺序校验',
                    severity=SeverityLevel.ERROR, field=f'klines[{curr_idx}].date',
                    value=str(curr_dt), message='K线日期顺序错误',
                    suggestion='请确保K线数据按时间正序排列'
                ))
        
        # TS004-TS005: 数据缺失检查
        if interval == 'daily' and len(dates) > 5:
            missing_days = cls._detect_missing_days(dates)
            if missing_days > len(dates) * 0.1:
                issues.append(ValidationIssue(
                    rule_id='TS004', rule_name='数据缺失率校验',
                    severity=SeverityLevel.WARNING, field='klines',
                    value=f'{missing_days / len(dates) * 100:.1f}%',
                    message=f'K线数据缺失率过高: {missing_days / len(dates) * 100:.1f}%',
                    suggestion='考虑补充缺失数据或使用插值'
                ))
        
        return issues
    
    @classmethod
    def validate_trend_reasonableness(cls, klines: List[Dict]) -> List[ValidationIssue]:
        """TS006-TS010: 趋势合理性检查"""
        issues = []
        if len(klines) < 2:
            return issues
        
        prev_close = None
        consecutive_gains = consecutive_losses = max_consecutive = 0
        
        for i, kline in enumerate(klines):
            close = kline.get('close')
            if close is None:
                continue
            try:
                close_val = float(close)
            except (ValueError, TypeError):
                continue
            
            if prev_close is not None and prev_close > 0:
                change_pct = (close_val - prev_close) / prev_close * 100
                
                if abs(change_pct) > cls.TREND_THRESHOLD['daily'] * 100:
                    issues.append(ValidationIssue(
                        rule_id='TS006', rule_name='单日涨跌幅异常',
                        severity=SeverityLevel.WARNING, field=f'klines[{i}].close',
                        value=f'{change_pct:.2f}%', message=f'单日涨跌幅过大: {change_pct:.2f}%',
                        suggestion='检查是否为涨跌停或数据错误'
                    ))
                
                if change_pct > 0:
                    consecutive_gains += 1
                    consecutive_losses = 0
                else:
                    consecutive_losses += 1
                    consecutive_gains = 0
                max_consecutive = max(max_consecutive, consecutive_gains, consecutive_losses)
            prev_close = close_val
        
        if max_consecutive >= 10:
            issues.append(ValidationIssue(
                rule_id='TS007', rule_name='连续涨跌异常',
                severity=SeverityLevel.WARNING, field='klines',
                value=max_consecutive, message=f'检测到连续{max_consecutive}天同向波动',
                suggestion='检查是否为数据错误或特殊事件'
            ))
        
        # TS008-TS010: 移动平均线合理性
        closes = [float(k.get('close', 0)) for k in klines if k.get('close') is not None]
        if len(closes) >= 5:
            for i in range(4, len(closes)):
                ma5 = sum(closes[i-4:i+1]) / 5
                close = closes[i]
                if ma5 > 0:
                    deviation = abs(close - ma5) / ma5 * 100
                    if deviation > 30:
                        issues.append(ValidationIssue(
                            rule_id='TS008', rule_name='价格偏离MA5异常',
                            severity=SeverityLevel.INFO, field=f'klines[{i}].close',
                            value=f'{deviation:.1f}%', message=f'价格偏离MA5过大: {deviation:.1f}%',
                            suggestion='可能是异常波动或数据错误'
                        ))
        
        return issues
    
    @classmethod
    def validate_volume_price_relationship(cls, klines: List[Dict]) -> List[ValidationIssue]:
        """TS011-TS015: 量价关系检查"""
        issues = []
        if len(klines) < 2:
            return issues
        
        volumes, price_changes = [], []
        prev_close = None
        
        for i, kline in enumerate(klines):
            volume, close = kline.get('volume'), kline.get('close')
            if volume is None or close is None:
                continue
            try:
                vol_val = float(volume)
                close_val = float(close)
                volumes.append(vol_val)
                if prev_close is not None and prev_close > 0:
                    price_changes.append(close_val - prev_close)
                prev_close = close_val
            except (ValueError, TypeError):
                continue
        
        if len(volumes) < 2 or len(price_changes) < 2:
            return issues
        
        avg_volume = sum(volumes) / len(volumes)
        high_volume_count = sum(1 for v in volumes if v > avg_volume * 1.5)
        
        if high_volume_count > len(volumes) * 0.3:
            for i in range(1, len(volumes)):
                if volumes[i] > avg_volume * 1.5 and abs(price_changes[i-1]) < 0.01:
                    issues.append(ValidationIssue(
                        rule_id='TS011', rule_name='量价背离检测',
                        severity=SeverityLevel.WARNING, field=f'klines[{i}].volume',
                        value=f'{volumes[i]/avg_volume:.1f}x平均',
                        message='高成交量但价格无变化',
                        suggestion='检查是否存在对倒交易或数据异常'
                    ))
        
        return issues
    
    @classmethod
    def _detect_missing_days(cls, dates: List[Tuple[int, datetime]]) -> int:
        """检测缺失的交易日期数量"""
        if len(dates) < 2:
            return 0
        
        first_date = dates[0][1]
        last_date = dates[-1][1]
        total_days = (last_date - first_date).days
        
        # 排除周末
        expected_trading_days = 0
        current = first_date
        while current <= last_date:
            if current.weekday() < 5:  # 周一到周五
                expected_trading_days += 1
            current += timedelta(days=1)
        
        actual_days = len(dates)
        return max(0, expected_trading_days - actual_days)

# ==================== 业务逻辑验证规则 ====================

class BusinessLogicValidationRules:
    """
    业务逻辑验证规则 L020-L040
    
    覆盖: A股市场规则、涨跌停限制、停牌检查、板块逻辑
    """
    
    # A股涨跌停限制
    A_STOCK_LIMIT = {
        'main_board': 0.10,      # 主板10%
        'star_board': 0.20,      # 科创板20%
        'gem_board': 0.20,       # 创业板20%
        'bmei_board': 0.30,      # 北交所30%
        'st_board': 0.05,        # ST股5%
    }
    
    # 股票代码前缀与板块映射
    STOCK_PREFIX_MAP = {
        '6': 'main_board', '5': 'star_board',  # 沪市
        '0': 'gem_board', '3': 'gem_board',     # 深市
        '8': 'bmei_board',                        # 北交所
    }
    
    # ST标记模式
    ST_PATTERN = re.compile(r'[^A-Za-z0-9]*(ST|[*]ST|退市)[^A-Za-z0-9]*', re.IGNORECASE)
    
    @classmethod
    def validate_a_stock_rules(cls, data: Dict) -> List[ValidationIssue]:
        """L020-L025: A股市场规则校验"""
        issues = []
        payload = data.get('payload', data)
        records = payload if isinstance(payload, list) else [payload]
        
        for i, record in enumerate(records):
            prefix = f'[{i}]' if len(records) > 1 else ''
            symbol = data.get('symbol', '') or record.get('symbol', '')
            
            # L020: 涨跌停检查
            change_pct = record.get('change_pct')
            if change_pct is not None:
                try:
                    pct_val = float(change_pct)
                    
                    # 判断板块
                    board = cls._detect_board(symbol, record)
                    limit = cls.A_STOCK_LIMIT.get(board, 0.10)
                    
                    if abs(pct_val) > limit + 0.005:  # 允许0.5%误差
                        issues.append(ValidationIssue(
                            rule_id='L020', rule_name='涨跌停限制校验',
                            severity=SeverityLevel.WARNING,
                            field=f'change_pct{prefix}',
                            value=f'{pct_val}%',
                            message=f'涨跌幅{pct_val}%超出{board}限制±{limit*100}%',
                            suggestion=f'检查是否为涨跌停或数据错误',
                            details={'board': board, 'limit': limit}
                        ))
                except (ValueError, TypeError):
                    pass
            
            # L021: ST股特殊标记检查
            name = record.get('name', '') or record.get('stock_name', '')
            if cls.ST_PATTERN.search(name):
                if 'change_pct' in record:
                    try:
                        pct = float(record['change_pct'])
                        if abs(pct) > cls.A_STOCK_LIMIT['st_board'] + 0.005:
                            issues.append(ValidationIssue(
                                rule_id='L021', rule_name='ST股涨跌停校验',
                                severity=SeverityLevel.WARNING,
                                field='change_pct', value=f'{pct}%',
                                message='ST股涨跌幅超出5%限制',
                                suggestion='ST股涨跌停限制为±5%'
                            ))
                    except (ValueError, TypeError):
                        pass
            
            # L022-L023: 开盘价/收盘价逻辑
            for field_name in ['open', 'close', 'pre_close']:
                if field_name in record:
                    try:
                        val = float(record[field_name])
                        if val <= 0:
                            issues.append(ValidationIssue(
                                rule_id='L022', rule_name='价格正值校验',
                                severity=SeverityLevel.ERROR,
                                field=field_name, value=val,
                                message=f'{field_name}必须为正数',
                                suggestion='检查数据源或清洗逻辑'
                            ))
                    except (ValueError, TypeError):
                        pass
            
            # L024: 成交量合理性
            volume = record.get('volume')
            if volume is not None:
                try:
                    vol_val = float(volume)
                    if vol_val < 0:
                        issues.append(ValidationIssue(
                            rule_id='L024', rule_name='成交量非负校验',
                            severity=SeverityLevel.ERROR,
                            field='volume', value=vol_val,
                            message='成交量不能为负数',
                            suggestion='检查数据处理逻辑'
                        ))
                except (ValueError, TypeError):
                    pass
            
            # L025: 成交额与成交量关系
            amount = record.get('amount')
            if amount is not None and volume is not None:
                try:
                    amt_val = float(amount)
                    vol_val = float(volume)
                    if vol_val > 0:
                        avg_price = amt_val / vol_val
                        close = record.get('close')
                        if close is not None:
                            close_val = float(close)
                            if close_val > 0 and avg_price > 0:
                                price_deviation = abs(avg_price - close_val) / close_val
                                if price_deviation > 0.05:  # 偏差超过5%
                                    issues.append(ValidationIssue(
                                        rule_id='L025', rule_name='成交均价校验',
                                        severity=SeverityLevel.WARNING,
                                        field='amount/volume',
                                        value=f'{avg_price:.2f}',
                                        message=f'成交均价{avg_price:.2f}与收盘价{close_val:.2f}偏差过大',
                                        suggestion='检查成交额计算或数据处理'
                                    ))
                except (ValueError, TypeError):
                    pass
        
        return issues
    
    @classmethod
    def _detect_board(cls, symbol: str, record: Dict) -> str:
        """检测股票板块"""
        if not symbol:
            return 'main_board'
        
        # 检查ST标记
        name = record.get('name', '') or record.get('stock_name', '')
        if cls.ST_PATTERN.search(name):
            return 'st_board'
        
        # 根据代码前缀判断
        first_digit = symbol[0] if symbol else ''
        return cls.STOCK_PREFIX_MAP.get(first_digit, 'main_board')
    
    @classmethod
    def validate_market_open_rules(cls, data: Dict) -> List[ValidationIssue]:
        """L026-L030: 市场开盘规则校验"""
        issues = []
        payload = data.get('payload', data)
        records = payload if isinstance(payload, list) else [payload]
        
        for i, record in enumerate(records):
            prefix = f'[{i}]' if len(records) > 1 else ''
            
            # L026: 集合竞价价格检查
            open_price = record.get('open')
            pre_close = record.get('pre_close')
            
            if open_price is not None and pre_close is not None:
                try:
                    open_val = float(open_price)
                    pre_close_val = float(pre_close)
                    
                    if pre_close_val > 0:
                        open_change = (open_val - pre_close_val) / pre_close_val
                        # 集合竞价开盘价不应超过涨跌停限制
                        if abs(open_change) > 0.12:  # 略高于10%允许误差
                            issues.append(ValidationIssue(
                                rule_id='L026', rule_name='集合竞价价格校验',
                                severity=SeverityLevel.WARNING,
                                field=f'open{prefix}',
                                value=open_val,
                                message=f'集合竞价开盘价偏差过大: {open_change*100:.2f}%',
                                suggestion='检查集合竞价数据或是否为涨停/跌停开盘'
                            ))
                except (ValueError, TypeError):
                    pass
            
            # L027: 停牌检查（成交量为0且价格无变化）
            volume = record.get('volume', 0)
            change_pct = record.get('change_pct', 0)
            
            try:
                if float(volume) == 0 and abs(float(change_pct)) < 0.001:
                    issues.append(ValidationIssue(
                        rule_id='L027', rule_name='停牌检测',
                        severity=SeverityLevel.INFO,
                        field='volume', value=volume,
                        message='疑似停牌股票（成交量为0且价格无变化）',
                        suggestion='确认是否为停牌状态'
                    ))
            except (ValueError, TypeError):
                pass
        
        return issues
    
    @classmethod
    def validate_sector_logic(cls, data: Dict) -> List[ValidationIssue]:
        """L031-L035: 板块逻辑校验"""
        issues = []
        payload = data.get('payload', data)
        
        if not isinstance(payload, list):
            return issues
        
        # L031: 板块内个股涨跌与板块涨跌关系
        if len(payload) > 1:
            total_change = 0
            valid_count = 0
            
            for i, record in enumerate(payload):
                change_pct = record.get('change_pct')
                if change_pct is not None:
                    try:
                        total_change += float(change_pct)
                        valid_count += 1
                    except (ValueError, TypeError):
                        pass
            
            if valid_count > 0:
                avg_change = total_change / valid_count
                sector_change = data.get('sector_change') or data.get('change_pct')
                
                if sector_change is not None:
                    try:
                        sector_val = float(sector_change)
                        # 板块涨跌应与个股平均涨跌接近（允许5%偏差）
                        if abs(avg_change - sector_val) > 5:
                            issues.append(ValidationIssue(
                                rule_id='L031', rule_name='板块涨跌一致性校验',
                                severity=SeverityLevel.WARNING,
                                field='sector_change',
                                value=f'{sector_val}%',
                                message=f'板块涨跌{sector_val}%与个股平均{avg_change:.2f}%偏差过大',
                                suggestion='检查板块指数计算逻辑'
                            ))
                    except (ValueError, TypeError):
                        pass
        
        # L032-L035: 领涨/领跌股检查
        if len(payload) >= 3:
            changes = []
            for record in payload:
                change_pct = record.get('change_pct')
                if change_pct is not None:
                    try:
                        changes.append((record.get('symbol', ''), float(change_pct)))
                    except (ValueError, TypeError):
                        pass
            
            if len(changes) >= 3:
                changes.sort(key=lambda x: x[1])
                min_change = changes[0][1]
                max_change = changes[-1][1]
                
                # L032: 最大涨幅不超过涨停限制
                if max_change > 20:
                    issues.append(ValidationIssue(
                        rule_id='L032', rule_name='板块领涨校验',
                        severity=SeverityLevel.INFO,
                        field='max_change',
                        value=f'{max_change}%',
                        message=f'板块内最大涨幅{max_change}%异常',
                        suggestion='检查是否为新股或特殊事件'
                    ))
                
                # L033: 最大跌幅不超过跌停限制
                if min_change < -20:
                    issues.append(ValidationIssue(
                        rule_id='L033', rule_name='板块领跌校验',
                        severity=SeverityLevel.INFO,
                        field='min_change',
                        value=f'{min_change}%',
                        message=f'板块内最小跌幅{min_change}%异常',
                        suggestion='检查是否为新股或特殊事件'
                    ))
        
        return issues

# ==================== 跨源一致性验证规则 ====================

class CrossSourceValidationRules:
    """
    跨源一致性验证规则 C007-C020
    
    覆盖: 多源数据比对、差异检测、置信度评估
    """
    
    # 不同数据类型的合理差异阈值
    TOLERANCE_THRESHOLDS = {
        'quote': {'price': 0.01, 'volume': 0.05, 'change_pct': 0.01},
        'kline': {'price': 0.02, 'volume': 0.10, 'change_pct': 0.02},
        'fund': {'nav': 0.001, 'amount': 0.01},
        'bond': {'price': 0.01, 'yield': 0.0001},
        'index': {'price': 0.01, 'change_pct': 0.01},
    }
    
    @classmethod
    def validate_cross_source(cls, sources_data: Dict[str, Dict], data_type: str = 'quote') -> List[ValidationIssue]:
        """C007-C010: 多源数据交叉验证"""
        issues = []
        
        if len(sources_data) < 2:
            return issues
        
        tolerance = cls.TOLERANCE_THRESHOLDS.get(data_type, {'price': 0.01, 'change_pct': 0.01})
        
        # 获取所有数据源
        source_keys = list(sources_data.keys())
        
        # C007: 逐对比较
        for i in range(len(source_keys)):
            for j in range(i + 1, len(source_keys)):
                source_a = source_keys[i]
                source_b = source_keys[j]
                
                data_a = sources_data[source_a]
                data_b = sources_data[source_b]
                
                payload_a = data_a.get('payload', data_a)
                payload_b = data_b.get('payload', data_b)
                
                # 处理列表数据
                if isinstance(payload_a, list) and len(payload_a) > 0:
                    payload_a = payload_a[0]
                if isinstance(payload_b, list) and len(payload_b) > 0:
                    payload_b = payload_b[0]
                
                # 比较关键字段
                if not isinstance(payload_a, dict) or not isinstance(payload_b, dict):
                    continue
                for field_name, threshold in tolerance.items():
                    val_a = payload_a.get(field_name)
                    val_b = payload_b.get(field_name)
                    
                    if val_a is None or val_b is None:
                        continue
                    
                    try:
                        num_a = float(val_a)
                        num_b = float(val_b)
                        
                        if num_a == 0 and num_b == 0:
                            continue
                        
                        if num_a == 0 or num_b == 0:
                            issues.append(ValidationIssue(
                                rule_id='C007', rule_name='跨源数据空值检测',
                                severity=SeverityLevel.WARNING,
                                field=f'{source_a}.{field_name}',
                                value=f'{num_a} vs {num_b}',
                                message=f'{source_a}与{source_b}的{field_name}差异: {num_a} vs {num_b}',
                                suggestion='检查数据源有效性'
                            ))
                            continue
                        
                        # 计算相对差异
                        avg_val = (num_a + num_b) / 2
                        relative_diff = abs(num_a - num_b) / abs(avg_val)
                        
                        if relative_diff > threshold:
                            issues.append(ValidationIssue(
                                rule_id='C008', rule_name='跨源价格差异校验',
                                severity=SeverityLevel.WARNING if relative_diff < threshold * 2 else SeverityLevel.ERROR,
                                field=f'{source_a}.{field_name}',
                                value=f'{num_a} vs {num_b} ({relative_diff*100:.2f}%)',
                                message=f'{source_a}与{source_b}的{field_name}差异过大: {relative_diff*100:.2f}% > {threshold*100:.2f}%',
                                suggestion='使用置信度更高的数据源或进行人工核查'
                            ))
                    except (ValueError, TypeError):
                        pass
        
        # C009: 数据源可用性统计
        valid_sources = []
        invalid_sources = []
        
        for source, data in sources_data.items():
            if data.get('status') == 'success' or (isinstance(data.get('payload'), dict) and data['payload']):
                valid_sources.append(source)
            else:
                invalid_sources.append(source)
        
        if invalid_sources and valid_sources:
            issues.append(ValidationIssue(
                rule_id='C009', rule_name='数据源可用性检测',
                severity=SeverityLevel.WARNING,
                field='sources',
                value=f'{len(valid_sources)}个可用, {len(invalid_sources)}个失效',
                message=f'{len(invalid_sources)}个数据源不可用: {invalid_sources}',
                suggestion='检查失效数据源配置或考虑降级策略'
            ))
        
        # C010: 时间戳一致性
        timestamps = []
        for source, data in sources_data.items():
            ts = data.get('timestamp') or (data.get('payload') or {}).get('timestamp')
            if ts:
                timestamps.append((source, ts))
        
        if len(timestamps) >= 2:
            ts_values = [t[1] for t in timestamps]
            if len(set(ts_values)) > 1:
                issues.append(ValidationIssue(
                    rule_id='C010', rule_name='时间戳一致性校验',
                    severity=SeverityLevel.INFO,
                    field='timestamps',
                    value=str([t[1] for t in timestamps]),
                    message='不同数据源的时间戳不一致',
                    suggestion='确认是否为同一时间点的数据',
                    details={'timestamps': timestamps}
                ))
        
        return issues
    
    @classmethod
    def validate_data_confidence(cls, sources_data: Dict[str, Dict]) -> Dict[str, Any]:
        """C011-C015: 数据置信度评估"""
        result = {
            'overall_confidence': 0.0,
            'source_confidences': {},
            'recommendations': []
        }
        
        total_sources = len(sources_data)
        if total_sources == 0:
            return result
        
        total_score = 0
        
        for source, data in sources_data.items():
            score = 0
            
            # 数据来源评分
            if data.get('status') == 'success':
                score += 40
            elif data.get('status') == 'partial':
                score += 20
            
            # 数据完整性评分
            payload = data.get('payload')
            if isinstance(payload, dict):
                non_null_fields = sum(1 for v in payload.values() if v is not None)
                completeness = non_null_fields / len(payload) if payload else 0
                score += int(completeness * 30)
            elif isinstance(payload, list) and len(payload) > 0:
                score += 20
            
            # 时间新鲜度评分
            timestamp = data.get('timestamp')
            if timestamp:
                try:
                    ts_dt = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
                    age_hours = (datetime.now(ts_dt.tzinfo) - ts_dt).total_seconds() / 3600
                    if age_hours < 1:
                        score += 30
                    elif age_hours < 24:
                        score += 20
                    elif age_hours < 72:
                        score += 10
                except (ValueError, TypeError):
                    pass
            
            result['source_confidences'][source] = score
            total_score += score
        
        result['overall_confidence'] = total_score / total_sources
        
        # 生成建议
        if result['overall_confidence'] < 50:
            result['recommendations'].append('数据置信度较低，建议人工核查或重新获取数据')
        elif result['overall_confidence'] < 70:
            result['recommendations'].append('数据置信度中等，建议验证关键数据点')
        
        low_confidence_sources = [s for s, conf in result['source_confidences'].items() if conf < 50]
        if low_confidence_sources:
            result['recommendations'].append(f'以下数据源置信度较低: {low_confidence_sources}')
        
        return result


# ==================== 数值验证增强规则 ====================

class NumericValidationRules:
    """
    数值验证增强规则 NV001-NV015
    
    覆盖: 异常值检测、分布合理性、极端值处理
    """
    
    # 合理的财务比率范围
    FINANCIAL_RATIOS = {
        'pe_ratio': (0, 500),           # 市盈率
        'pb_ratio': (0, 50),            # 市净率
        'ps_ratio': (0, 100),           # 市销率
        'dividend_yield': (0, 0.20),    # 股息率
        'roe': (-1, 1),                 # 净资产收益率
        'roa': (-1, 1),                 # 总资产收益率
        'debt_to_asset': (0, 2),        # 资产负债率
        'current_ratio': (0, 20),       # 流动比率
    }
    
    # 合理的市值范围（元）
    MARKET_CAP_RANGE = (1e6, 1e13)  # 100万到10万亿
    
    @classmethod
    def validate_financial_ratios(cls, data: Dict) -> List[ValidationIssue]:
        """NV001-NV005: 财务比率合理性校验"""
        issues = []
        payload = data.get('payload', data)
        
        if isinstance(payload, list):
            records = payload
        else:
            records = [payload]
        
        for i, record in enumerate(records):
            prefix = f'[{i}]' if len(records) > 1 else ''
            
            for ratio_name, (min_val, max_val) in cls.FINANCIAL_RATIOS.items():
                if ratio_name in record:
                    try:
                        ratio_val = float(record[ratio_name])
                        if ratio_val < min_val or ratio_val > max_val:
                            severity = SeverityLevel.WARNING if ratio_name in ['roe', 'roa'] else SeverityLevel.ERROR
                            issues.append(ValidationIssue(
                                rule_id='NV001', rule_name='财务比率合理性校验',
                                severity=severity,
                                field=f'{ratio_name}{prefix}',
                                value=ratio_val,
                                message=f'{ratio_name}超出合理范围 [{min_val}, {max_val}]: {ratio_val}',
                                suggestion=f'请检查{ratio_name}计算逻辑或数据源'
                            ))
                    except (ValueError, TypeError):
                        pass
        
        return issues
    
    @classmethod
    def validate_market_cap(cls, data: Dict) -> List[ValidationIssue]:
        """NV006-NV008: 市值合理性校验"""
        issues = []
        payload = data.get('payload', data)
        
        market_cap = payload.get('market_cap') or payload.get('cap')
        if market_cap is not None:
            try:
                cap_val = float(market_cap)
                
                if cap_val < cls.MARKET_CAP_RANGE[0]:
                    issues.append(ValidationIssue(
                        rule_id='NV006', rule_name='市值下限校验',
                        severity=SeverityLevel.WARNING,
                        field='market_cap',
                        value=cap_val,
                        message=f'市值过小: {cap_val/1e8:.2f}亿元',
                        suggestion='确认是否为退市股或数据错误'
                    ))
                
                if cap_val > cls.MARKET_CAP_RANGE[1]:
                    issues.append(ValidationIssue(
                        rule_id='NV007', rule_name='市值上限校验',
                        severity=SeverityLevel.WARNING,
                        field='market_cap',
                        value=cap_val,
                        message=f'市值过大: {cap_val/1e12:.2f}万亿元',
                        suggestion='确认是否为数据错误'
                    ))
            except (ValueError, TypeError):
                issues.append(ValidationIssue(
                    rule_id='NV008', rule_name='市值格式校验',
                    severity=SeverityLevel.ERROR,
                    field='market_cap',
                    value=market_cap,
                    message=f'市值格式无效: {market_cap}',
                    suggestion='请使用数值格式'
                ))
        
        return issues
    
    @classmethod
    def validate_volume_anomalies(cls, klines: List[Dict]) -> List[ValidationIssue]:
        """NV009-NV012: 成交量异常检测"""
        issues = []
        
        if len(klines) < 10:
            return issues
        
        volumes = []
        for kline in klines:
            vol = kline.get('volume')
            if vol is not None:
                try:
                    volumes.append(float(vol))
                except (ValueError, TypeError):
                    pass
        
        if len(volumes) < 10:
            return issues
        
        # 计算统计指标
        avg_vol = sum(volumes) / len(volumes)
        sorted_vols = sorted(volumes)
        median_vol = sorted_vols[len(sorted_vols) // 2]
        
        # NV009: 极值检测（超过3倍标准差）
        if len(volumes) >= 30:
            variance = sum((v - avg_vol) ** 2 for v in volumes) / len(volumes)
            std_dev = variance ** 0.5
            
            for i, vol in enumerate(volumes):
                if std_dev > 0 and abs(vol - avg_vol) > 3 * std_dev:
                    issues.append(ValidationIssue(
                        rule_id='NV009', rule_name='成交量极值检测',
                        severity=SeverityLevel.WARNING,
                        field=f'volumes[{i}]',
                        value=vol,
                        message=f'成交量异常: {vol:.0f} (均值{avg_vol:.0f}, 偏差{abs(vol-avg_vol)/std_dev:.1f}σ)',
                        suggestion='检查是否为特殊事件（如大宗交易、新股上市）'
                    ))
        
        # NV010: 零成交量检测
        zero_volume_count = sum(1 for v in volumes if v == 0)
        if zero_volume_count > len(volumes) * 0.1:
            issues.append(ValidationIssue(
                rule_id='NV010', rule_name='零成交量比例校验',
                severity=SeverityLevel.WARNING,
                field='volumes',
                value=f'{zero_volume_count}/{len(volumes)}',
                message=f'零成交量比例过高: {zero_volume_count/len(volumes)*100:.1f}%',
                suggestion='检查是否为停牌期间数据'
            ))
        
        # NV011-NV012: 成交量突变检测
        for i in range(1, len(volumes)):
            if volumes[i-1] > 0:
                ratio = volumes[i] / volumes[i-1]
                if ratio > 10 or ratio < 0.1:
                    issues.append(ValidationIssue(
                        rule_id='NV011', rule_name='成交量突变检测',
                        severity=SeverityLevel.INFO,
                        field=f'volumes[{i}]',
                        value=f'{volumes[i]:.0f} vs {volumes[i-1]:.0f}',
                        message=f'成交量突变: {ratio:.1f}倍',
                        suggestion='检查是否为特殊事件'
                    ))
        
        return issues
    
    @classmethod
    def validate_price_patterns(cls, klines: List[Dict]) -> List[ValidationIssue]:
        """NV013-NV015: 价格模式校验"""
        issues = []
        
        if len(klines) < 2:
            return issues
        
        prev_close = None
        consecutive_same_close = 0
        
        for i, kline in enumerate(klines):
            close = kline.get('close')
            if close is None:
                continue
            
            try:
                close_val = float(close)
            except (ValueError, TypeError):
                continue
            
            # NV013: 连续相同收盘价
            if prev_close is not None and abs(close_val - prev_close) < 0.001:
                consecutive_same_close += 1
                if consecutive_same_close >= 3:
                    issues.append(ValidationIssue(
                        rule_id='NV013', rule_name='连续相同收盘价检测',
                        severity=SeverityLevel.INFO,
                        field=f'closes[{i}]',
                        value=close_val,
                        message=f'连续{consecutive_same_close+1}天收盘价相同',
                        suggestion='检查是否为停牌复牌或数据重复'
                    ))
            else:
                consecutive_same_close = 0
            
            # NV014: 价格小数位检查
            close_str = str(close_val)
            if '.' in close_str:
                decimal_places = len(close_str.split('.')[1])
                if decimal_places > 4:
                    issues.append(ValidationIssue(
                        rule_id='NV014', rule_name='价格精度校验',
                        severity=SeverityLevel.WARNING,
                        field=f'close[{i}]',
                        value=close_val,
                        message=f'价格精度异常: {decimal_places}位小数',
                        suggestion='A股价格通常为2-3位小数'
                    ))
            
            prev_close = close_val
        
        # NV015: 价格连续性检查（无跳空缺口过大）
        closes = [float(k.get('close', 0)) for k in klines if k.get('close') is not None]
        if len(closes) >= 5:
            gaps = []
            for i in range(1, len(closes)):
                if closes[i-1] > 0:
                    gap = (closes[i] - closes[i-1]) / closes[i-1] * 100
                    gaps.append(gap)
            
            if gaps:
                large_gaps = [g for g in gaps if abs(g) > 10]
                if len(large_gaps) > len(gaps) * 0.1:
                    issues.append(ValidationIssue(
                        rule_id='NV015', rule_name='价格跳空检测',
                        severity=SeverityLevel.WARNING,
                        field='closes',
                        value=f'{len(large_gaps)}/{len(gaps)}',
                        message=f'价格跳空比例过高: {len(large_gaps)}/{len(gaps)}',
                        suggestion='检查是否为数据错误或特殊事件'
                    ))
        
        return issues


# ==================== 验证规则集注册 ====================

class ValidationRuleRegistry:
    """验证规则注册表"""
    
    REGISTRY = {
        'input': InputValidationRules,
        'time_series': TimeSeriesValidationRules,
        'business_logic': BusinessLogicValidationRules,
        'cross_source': CrossSourceValidationRules,
        'numeric': NumericValidationRules,
    }
    
    @classmethod
    def get_rules(cls, rule_category: str):
        """获取指定类别的验证规则类"""
        return cls.REGISTRY.get(rule_category)
    
    @classmethod
    def list_categories(cls):
        """列出所有可用的规则类别"""
        return list(cls.REGISTRY.keys())
    
    @classmethod
    def get_all_rule_ids(cls) -> List[str]:
        """获取所有规则ID"""
        all_rules = []
        for category, rule_class in cls.REGISTRY.items():
            # 从类方法中推断规则ID
            methods = [m for m in dir(rule_class) if m.startswith('validate_')]
            all_rules.extend(methods)
        return all_rules


# ==================== 便捷函数 ====================

def validate_input(url: str = None, response: Any = None, config: Dict = None, params: Dict = None) -> List[ValidationIssue]:
    """便捷函数: 执行所有输入校验"""
    issues = []
    if url:
        issues.extend(InputValidationRules.validate_url(url))
    if response is not None:
        issues.extend(InputValidationRules.validate_api_response(response))
    if config:
        issues.extend(InputValidationRules.validate_request_config(config))
    if params:
        issues.extend(InputValidationRules.validate_network_params(params))
    return issues


def validate_time_series(klines: List[Dict], interval: str = 'daily') -> List[ValidationIssue]:
    """便捷函数: 执行时间序列验证"""
    issues = []
    issues.extend(TimeSeriesValidationRules.validate_kline_continuity(klines, interval))
    issues.extend(TimeSeriesValidationRules.validate_trend_reasonableness(klines))
    issues.extend(TimeSeriesValidationRules.validate_volume_price_relationship(klines))
    return issues


def validate_business_logic(data: Dict) -> List[ValidationIssue]:
    """便捷函数: 执行业务逻辑验证"""
    issues = []
    issues.extend(BusinessLogicValidationRules.validate_a_stock_rules(data))
    issues.extend(BusinessLogicValidationRules.validate_market_open_rules(data))
    issues.extend(BusinessLogicValidationRules.validate_sector_logic(data))
    return issues


def validate_numeric(data: Dict = None, klines: List[Dict] = None) -> List[ValidationIssue]:
    """便捷函数: 执行数值验证"""
    issues = []
    if data:
        issues.extend(NumericValidationRules.validate_financial_ratios(data))
        issues.extend(NumericValidationRules.validate_market_cap(data))
    if klines:
        issues.extend(NumericValidationRules.validate_volume_anomalies(klines))
        issues.extend(NumericValidationRules.validate_price_patterns(klines))
    return issues


# ==================== 模块导出 ====================

__all__ = [
    'SeverityLevel',
    'ValidationIssue',
    'InputValidationRules',
    'TimeSeriesValidationRules',
    'BusinessLogicValidationRules',
    'CrossSourceValidationRules',
    'NumericValidationRules',
    'ValidationRuleRegistry',
    'validate_input',
    'validate_time_series',
    'validate_business_logic',
    'validate_numeric',
]
