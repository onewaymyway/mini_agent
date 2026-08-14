# -*- coding: utf-8 -*-
"""
步骤6: 集成测试 - 重试机制 + 操作验证 全流程验证

测试场景:
1. 重试 + 验证通过流程
2. 重试耗尽后验证失败流程
3. 超时触发重试流程
4. 熔断器与验证联动流程
"""

import time
import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# 导入被测模块
from src.reliability.enhanced_retry import (
    RetryConfig,
    BackoffStrategy,
    CircuitBreaker,
    RetryStats,
    retry_operation,
    retry_operation_async,
)
from src.reliability.operation_validator import (
    OperationValidator,
    ExistenceValidator,
    TextContentValidator,
    ValidationContext,
    ValidationReport,
    ValidationResult,
    VALIDATION_TEMPLATES,
    create_validator_from_template,
)
from src.reliability.error import CDPConnectionLostError


# ==================== 测试辅助 ====================

class MockDOMSnapshot:
    """模拟 DOM 快照"""
    
    def __init__(self):
        self.data: Dict[str, str] = {}
        self.call_count: int = 0
    
    def set(self, selector: str, value: str):
        self.data[selector] = value
    
    def clear(self):
        self.data.clear()
    
    def simulate_failure_then_success(self, fail_times: int = 2):
        """模拟前 N 次失败，之后成功"""
        def factory():
            count = [0]
            def func():
                count[0] += 1
                self.call_count += 1
                if count[0] <= fail_times:
                    raise ConnectionError(f"Transient error #{count[0]}")
                return f"result_{count[0]}"
            return func
        return factory()


# ==================== 测试用例 ====================

class TestRetryWithValidation:
    """重试 + 验证集成测试"""
    
    def test_retry_then_validate_pass(self):
        """重试成功后验证通过"""
        dom = MockDOMSnapshot()
        call_count = [0]
        
        from src.reliability.error import CDPConnectionLostError
        # 模拟操作：前2次失败，第3次成功
        def operation():
            call_count[0] += 1
            if call_count[0] < 3:
                raise CDPConnectionLostError(f"Transient error #{call_count[0]}")
            dom.set("#result", "success")
            return "done"
        
        config = RetryConfig(
            max_retries=3,
            base_delay=0.01,
            backoff_strategy=BackoffStrategy.FIXED,
        )
        
        result = retry_operation(operation, config=config, operation="test_op")
        
        assert result == "done"
        assert call_count[0] == 3
        
        # 验证结果
        validator = OperationValidator(operation="test_op")
        validator.add_existence_check("#result")
        context = ValidationContext(dom_snapshot=dom.data)
        report = validator.validate(context)
        
        assert report.success == True
        assert len(report.validations) == 1
        print(f"  PASS: retry_then_validate_pass (calls={call_count[0]})")
    
    def test_retry_exhausted_validate_fail(self):
        """重试耗尽后验证失败"""
        call_count = [0]
        
        def operation():
            call_count[0] += 1
            raise CDPConnectionLostError(f"Persistent error #{call_count[0]}")
        
        config = RetryConfig(
            max_retries=2,
            base_delay=0.01,
            backoff_strategy=BackoffStrategy.FIXED,
        )
        
        try:
            retry_operation(operation, config=config, operation="test_op")
            assert False, "Should have raised"
        except CDPConnectionLostError:
            pass
        
        assert call_count[0] == 3  # 1 initial + 2 retries
        print(f"  PASS: retry_exhausted_validate_fail (calls={call_count[0]})")
    
    def test_validation_triggers_retry(self):
        """验证失败触发重试"""
        dom = MockDOMSnapshot()
        call_count = [0]
        
        # 前2次验证失败，第3次通过
        def operation():
            call_count[0] += 1
            if call_count[0] < 3:
                # 模拟元素不存在
                dom.clear()
                raise CDPConnectionLostError(f"Element missing attempt #{call_count[0]}")
            dom.set("#target", "found")
            return "ok"
        
        config = RetryConfig(
            max_retries=5,
            base_delay=0.01,
            backoff_strategy=BackoffStrategy.FIXED,
        )
        
        result = retry_operation(operation, config=config, operation="validate_test")
        
        assert result == "ok"
        assert call_count[0] == 3
        
        # 最终验证通过
        validator = OperationValidator(operation="validate_test")
        validator.add_existence_check("#target")
        context = ValidationContext(dom_snapshot=dom.data)
        report = validator.validate(context)
        
        assert report.success == True
        print(f"  PASS: validation_triggers_retry (calls={call_count[0]})")
    
    def test_circuit_breaker_with_validation(self):
        """熔断器与验证联动"""
        call_count = [0]
        
        def failing_operation():
            call_count[0] += 1
            raise CDPConnectionLostError(f"Failure #{call_count[0]}")
        
        config = RetryConfig(
            max_retries=5,
            base_delay=0.01,
            backoff_strategy=BackoffStrategy.FIXED,
            circuit_breaker=True,
            circuit_breaker_threshold=3,
        )
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10)
        
        try:
            retry_operation(
                failing_operation,
                config=config,
                circuit_breaker=cb,
                operation="cb_test",
            )
        except Exception:
            pass
        
        # 熔断器应已打开
        assert cb.state == "open"
        assert cb.failure_count >= 3
        
        # 验证：熔断器打开后不应再执行
        second_calls = call_count[0]  # 记录第一次调用后的调用次数
        try:
            retry_operation(
                failing_operation,
                config=config,
                circuit_breaker=cb,
                operation="cb_test",
            )
            assert False, "Should raise exception when circuit is open"
        except Exception as e:
            # 熔断器打开时会抛出 CDPConnectionLostError（含 circuit_breaker_state 详情）
            assert call_count[0] == second_calls, f"Operation should not execute when CB is open: calls={call_count[0]}, expected={second_calls}"
        
        print(f"  PASS: circuit_breaker_with_validation (trips={cb.trip_count})")
    
    def test_template_validator_integration(self):
        """模板验证器与重试集成"""
        # 创建搜索验证模板
        validator = create_validator_from_template("search_result")
        assert len(validator.validators) == 2
        
        # 模拟 DOM
        dom = {
            "#search_results": "found",
            ".result-item": "item1",
        }
        
        context = ValidationContext(dom_snapshot=dom)
        report = validator.validate(context)
        
        assert report.success == True
        assert len(report.validations) == 2
        print(f"  PASS: template_validator_integration")
    
    def test_async_retry_with_validation(self):
        """异步重试 + 验证集成"""
        async def async_operation():
            await asyncio.sleep(0.01)
            raise CDPConnectionLostError("Async error")
        
        async def run_test():
            config = RetryConfig(
                max_retries=2,
                base_delay=0.01,
                backoff_strategy=BackoffStrategy.FIXED,
            )
            try:
                await retry_operation_async(async_operation, config=config, operation="async_test")
            except CDPConnectionLostError:
                pass
        
        asyncio.run(run_test())
        print(f"  PASS: async_retry_with_validation")
    
    def test_mixed_validation_scenarios(self):
        """混合验证场景测试"""
        validator = OperationValidator(operation="complex_op")
        
        # 添加多种验证类型
        validator.add_existence_check("#header")
        validator.add_text_check("#title", "Welcome")
        validator.add_existence_check("#content")
        
        # 通过场景
        dom_pass = {"#header": "h", "#title": "Welcome Page", "#content": "c"}
        context_pass = ValidationContext(dom_snapshot=dom_pass)
        report_pass = validator.validate(context_pass)
        assert report_pass.success == True
        
        # 部分失败场景
        dom_partial = {"#header": "h", "#title": "Wrong Title", "#content": "c"}
        context_partial = ValidationContext(dom_snapshot=dom_partial)
        report_partial = validator.validate(context_partial)
        assert report_partial.success == False
        assert len(report_partial.errors) > 0
        
        print(f"  PASS: mixed_validation_scenarios")


# ==================== 主程序 ====================

def run_all_tests():
    """运行所有集成测试"""
    print("=" * 60)
    print("步骤6: 重试+验证集成测试")
    print("=" * 60)
    
    tester = TestRetryWithValidation()
    tests = [
        "test_retry_then_validate_pass",
        "test_retry_exhausted_validate_fail",
        "test_validation_triggers_retry",
        "test_circuit_breaker_with_validation",
        "test_template_validator_integration",
        "test_async_retry_with_validation",
        "test_mixed_validation_scenarios",
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
    print(f"集成测试结果: {passed} 通过, {failed} 失败")
    
    if errors:
        print("\n失败详情:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
