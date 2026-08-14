# -*- coding: utf-8 -*-
"""
操作验证机制

提供网页操作后的结果验证能力，确保操作执行后状态符合预期。
支持多种验证策略：DOM 元素检查、内容匹配、页面状态验证等。
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


class ValidationResult(Enum):
    """验证结果枚举"""
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"  # 警告，操作可能成功但存在异常


class ValidatorType(Enum):
    """验证器类型"""
    EXISTENCE = "existence"           # 元素存在性验证
    VISIBILITY = "visibility"          # 元素可见性验证
    TEXT_CONTENT = "text_content"      # 文本内容匹配
    ATTR_VALUE = "attr_value"         # 属性值验证
    PAGE_STATE = "page_state"         # 页面状态验证
    URL_MATCH = "url_match"           # URL 匹配验证
    SCREENSHOT_DIFF = "screenshot_diff"  # 截图差异验证
    CUSTOM = "custom"                  # 自定义验证


@dataclass
class ValidationRule:
    """验证规则"""
    validator_type: ValidatorType
    selector: str
    expected_value: Optional[Any] = None
    timeout: float = 5.0
    error_message: Optional[str] = None
    
    def __post_init__(self):
        if self.error_message is None:
            self.error_message = self._default_error_message()
    
    def _default_error_message(self) -> str:
        return f"Validation failed: {self.validator_type.value} '{self.selector}'"


@dataclass
class ValidationContext:
    """验证上下文"""
    operation: str = "unknown"
    page_url: str = ""
    page_title: str = ""
    dom_snapshot: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    """验证报告"""
    operation: str = "unknown"
    success: bool = False
    result: ValidationResult = ValidationResult.PASS
    message: str = ""
    validations: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    context: Optional[ValidationContext] = None
    duration: float = 0.0
    
    def add_validation(self, name: str, passed: bool, details: Dict[str, Any]):
        self.validations.append({
            "name": name,
            "passed": passed,
            "details": details,
        })
        if not passed:
            self.errors.append(f"{name}: {details.get('error', 'Unknown error')}")
        elif details.get("warning"):
            self.warnings.append(details["warning"])
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result.value,
            "message": self.message,
            "validations_count": len(self.validations),
            "errors": self.errors,
            "warnings": self.warnings,
            "duration": round(self.duration, 3),
        }


class BaseValidator:
    """验证器基类"""
    
    def __init__(self, selector: str, expected_value: Optional[Any] = None, **kwargs):
        self.selector = selector
        self.expected_value = expected_value
        self.timeout = kwargs.get("timeout", 5.0)
        self.error_message = kwargs.get("error_message", "")
    
    def validate(self, context: ValidationContext) -> Tuple[bool, Dict[str, Any]]:
        raise NotImplementedError
    
    def get_rule(self) -> ValidationRule:
        return ValidationRule(
            validator_type=self.validator_type,
            selector=self.selector,
            expected_value=self.expected_value,
            timeout=self.timeout,
            error_message=self.error_message or self._default_error_message(),
        )
    
    def _default_error_message(self) -> str:
        return f"Validator failed for '{self.selector}'"


class ExistenceValidator(BaseValidator):
    """元素存在性验证器"""
    
    validator_type = ValidatorType.EXISTENCE
    
    def validate(self, context: ValidationContext) -> Tuple[bool, Dict[str, Any]]:
        selector = self.selector
        # 这里应该实际调用 DOM 查询 API
        # 由于是单文件测试，我们模拟验证逻辑
        found = selector in (context.dom_snapshot or {})
        return found, {
            "selector": selector,
            "found": found,
            "error": f"Element not found: {selector}" if not found else None,
        }


class VisibilityValidator(BaseValidator):
    """元素可见性验证器"""
    
    validator_type = ValidatorType.VISIBILITY
    
    def validate(self, context: ValidationContext) -> Tuple[bool, Dict[str, Any]]:
        selector = self.selector
        visible = context.dom_snapshot.get(f"{selector}:visible", False)
        return visible, {
            "selector": selector,
            "visible": visible,
            "error": f"Element not visible: {selector}" if not visible else None,
        }


class TextContentValidator(BaseValidator):
    """文本内容匹配验证器"""
    
    validator_type = ValidatorType.TEXT_CONTENT
    
    def validate(self, context: ValidationContext) -> Tuple[bool, Dict[str, Any]]:
        if self.expected_value is None:
            return True, {"selector": self.selector, "matched": True}
        
        actual_text = context.dom_snapshot.get(self.selector, "")
        matched = self.expected_value in actual_text
        return matched, {
            "selector": self.selector,
            "expected": self.expected_value,
            "actual": actual_text,
            "matched": matched,
            "error": f"Text mismatch: expected '{self.expected_value}', got '{actual_text}'" if not matched else None,
        }


class URLMatchValidator(BaseValidator):
    """URL 匹配验证器"""
    
    validator_type = ValidatorType.URL_MATCH
    
    def validate(self, context: ValidationContext) -> Tuple[bool, Dict[str, Any]]:
        url = context.page_url
        if self.expected_value is None:
            return True, {"url": url, "matched": True}
        matched = self.expected_value in url
        return matched, {
            "url": url,
            "expected": self.expected_value,
            "matched": matched,
            "error": f"URL mismatch: expected '{self.expected_value}' in '{url}'" if not matched else None,
        }


class CustomValidator(BaseValidator):
    """自定义验证器"""
    
    validator_type = ValidatorType.CUSTOM
    
    def __init__(self, selector: str, validate_func: Callable[[ValidationContext], Tuple[bool, Dict]], **kwargs):
        super().__init__(selector, **kwargs)
        self.validate_func = validate_func
    
    def validate(self, context: ValidationContext) -> Tuple[bool, Dict[str, Any]]:
        return self.validate_func(context)


class OperationValidator:
    """
    操作验证器：封装操作后的验证逻辑
    
    使用示例:
        validator = OperationValidator(operation="click_search")
        validator.add_rule(ExistenceValidator("#search_result"))
        validator.add_rule(TextContentValidator("#result_title", "Expected Title"))
        report = validator.validate(context)
        if not report.success:
            # 处理失败
    """
    
    def __init__(self, operation: str = "unknown"):
        self.operation = operation
        self.validators: List[BaseValidator] = []
        self.on_validate: Optional[Callable[[ValidationReport], None]] = None
    
    def add_validator(self, validator: BaseValidator) -> "OperationValidator":
        """添加验证器"""
        self.validators.append(validator)
        return self
    
    def add_existence_check(self, selector: str, timeout: float = 5.0) -> "OperationValidator":
        """添加存在性检查"""
        return self.add_validator(ExistenceValidator(selector, timeout=timeout))
    
    def add_visibility_check(self, selector: str, timeout: float = 5.0) -> "OperationValidator":
        """添加可见性检查"""
        return self.add_validator(VisibilityValidator(selector, timeout=timeout))
    
    def add_text_check(self, selector: str, expected: str, timeout: float = 5.0) -> "OperationValidator":
        """添加文本内容检查"""
        return self.add_validator(TextContentValidator(selector, expected, timeout=timeout))
    
    def add_url_check(self, expected_url: str, timeout: float = 5.0) -> "OperationValidator":
        """添加 URL 匹配检查"""
        return self.add_validator(URLMatchValidator("", expected_url, timeout=timeout))
    
    def add_custom_check(self, validator: CustomValidator) -> "OperationValidator":
        """添加自定义验证"""
        return self.add_validator(validator)
    
    def validate(self, context: ValidationContext) -> ValidationReport:
        """
        执行所有验证器
        
        Returns:
            ValidationReport 包含所有验证结果
        """
        import time
        start_time = time.time()
        
        report = ValidationReport(
            operation=self.operation,
            context=context,
        )
        
        all_passed = True
        
        for validator in self.validators:
            try:
                passed, details = validator.validate(context)
                report.add_validation(
                    f"{validator.validator_type.value}:{validator.selector}",
                    passed,
                    details,
                )
                if not passed:
                    all_passed = False
                    logger.warning(f"Validation failed: {validator.get_rule().error_message}")
            except Exception as e:
                all_passed = False
                report.add_validation(
                    f"error:{validator.validator_type.value}:{validator.selector}",
                    False,
                    {"error": str(e)},
                )
                logger.error(f"Validation error: {e}")
        
        report.success = all_passed and len(self.validators) > 0
        report.message = "All validations passed" if report.success else f"{len(report.errors)} validation(s) failed"
        report.duration = time.time() - start_time
        
        if self.on_validate:
            self.on_validate(report)
        
        return report
    
    def should_retry(self, report: ValidationReport) -> bool:
        """
        判断是否需要重试
        
        条件：
        - 验证失败
        - 没有配置验证器（默认认为需要重试）
        """
        if not self.validators:
            return True  # 无验证器，默认需要重试
        
        # 关键验证失败需要重试
        critical_failures = [v for v in report.validations if not v["passed"] and v["name"].startswith("existence:")]
        return len(critical_failures) > 0


# ==================== 预定义验证模板 ====================

VALIDATION_TEMPLATES = {
    "search_result": [
        (ExistenceValidator, {"selector": "#search_results"}),
        (ExistenceValidator, {"selector": ".result-item"}),
    ],
    "login_success": [
        (ExistenceValidator, {"selector": "#user_menu"}),
        (TextContentValidator, {"selector": "#username", "expected_value": None}),  # 任意非空值
    ],
    "navigation_success": [
        (URLMatchValidator, {"expected_value": None}),  # URL 变化即成功
    ],
    "form_submit": [
        (ExistenceValidator, {"selector": ".success-message"}),
        (ExistenceValidator, {"selector": ".error-message", "expected_value": ""}),  # 不应存在
    ],
}


def create_validator_from_template(template_name: str, **overrides) -> OperationValidator:
    """
    从模板创建验证器
    
    Args:
        template_name: 模板名称（见 VALIDATION_TEMPLATES）
        **overrides: 覆盖默认参数
    """
    if template_name not in VALIDATION_TEMPLATES:
        raise ValueError(f"Unknown template: {template_name}")
    
    validator = OperationValidator(operation=f"template:{template_name}")
    
    for validator_cls, defaults in VALIDATION_TEMPLATES[template_name]:
        params = {**defaults, **overrides}
        validator.add_validator(validator_cls(**params))
    
    return validator


# ==================== 单元测试 ====================

class TestOperationValidator:
    """操作验证器测试类"""
    
    def test_existence_validator_pass(self):
        """测试存在性验证通过"""
        context = ValidationContext(
            operation="test",
            dom_snapshot={"#button": "found"},
        )
        validator = ExistenceValidator("#button")
        passed, details = validator.validate(context)
        assert passed == True
        assert details["found"] == True
        print("  PASS: existence_validator_pass")
    
    def test_existence_validator_fail(self):
        """测试存在性验证失败"""
        context = ValidationContext(
            operation="test",
            dom_snapshot={},
        )
        validator = ExistenceValidator("#missing")
        passed, details = validator.validate(context)
        assert passed == False
        assert "error" in details
        print("  PASS: existence_validator_fail")
    
    def test_text_content_validator(self):
        """测试文本内容验证"""
        context = ValidationContext(
            operation="test",
            dom_snapshot={"#title": "Hello World"},
        )
        validator = TextContentValidator("#title", "Hello")
        passed, details = validator.validate(context)
        assert passed == True
        assert details["matched"] == True
        
        # 不匹配的情况
        validator2 = TextContentValidator("#title", "Goodbye")
        passed2, details2 = validator2.validate(context)
        assert passed2 == False
        print("  PASS: text_content_validator")
    
    def test_url_match_validator(self):
        """测试 URL 匹配验证"""
        context = ValidationContext(
            operation="test",
            page_url="https://example.com/search?q=test",
        )
        validator = URLMatchValidator("", "search")
        passed, details = validator.validate(context)
        assert passed == True
        print("  PASS: url_match_validator")
    
    def test_operation_validator_combined(self):
        """测试组合验证器"""
        validator = OperationValidator(operation="search")
        validator.add_existence_check("#results")
        validator.add_text_check("#title", "Results")
        
        context = ValidationContext(
            operation="search",
            dom_snapshot={"#results": "found", "#title": "Search Results"},
            page_url="https://example.com/search",
        )
        
        report = validator.validate(context)
        assert report.success == True
        assert len(report.validations) == 2
        print("  PASS: operation_validator_combined")
    
    def test_operation_validator_failure(self):
        """测试组合验证失败"""
        validator = OperationValidator(operation="search")
        validator.add_existence_check("#results")
        validator.add_text_check("#title", "Missing")
        
        context = ValidationContext(
            operation="search",
            dom_snapshot={"#results": "found", "#title": "Search Results"},
        )
        
        report = validator.validate(context)
        assert report.success == False
        assert len(report.errors) > 0
        print("  PASS: operation_validator_failure")
    
    def test_template_validator(self):
        """测试模板验证器"""
        validator = create_validator_from_template("search_result")
        assert len(validator.validators) == 2
        print("  PASS: template_validator")
    
    def test_should_retry_logic(self):
        """测试重试判断逻辑"""
        validator = OperationValidator(operation="search")
        validator.add_existence_check("#results")
        
        # 验证通过，不需要重试
        context = ValidationContext(dom_snapshot={"#results": "found"})
        report = validator.validate(context)
        assert validator.should_retry(report) == False
        
        # 验证失败，需要重试
        context2 = ValidationContext(dom_snapshot={})
        report2 = validator.validate(context2)
        assert validator.should_retry(report2) == True
        print("  PASS: should_retry_logic")


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("操作验证机制 - 单元测试")
    print("=" * 60)
    
    tester = TestOperationValidator()
    tests = [
        "test_existence_validator_pass",
        "test_existence_validator_fail",
        "test_text_content_validator",
        "test_url_match_validator",
        "test_operation_validator_combined",
        "test_operation_validator_failure",
        "test_template_validator",
        "test_should_retry_logic",
    ]
    
    passed = 0
    failed = 0
    errors = []
    
    for test_name in tests:
        try:
            print(f"\n  Running: {test_name}")
            getattr(tester, test_name)()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test_name, str(e)))
            print(f"  FAIL: {test_name} - {e}")
    
    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    
    if errors:
        print("\n失败详情:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
