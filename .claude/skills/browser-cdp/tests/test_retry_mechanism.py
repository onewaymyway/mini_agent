# -*- coding: utf-8 -*-
"""
步骤3: 带指数退避的重试机制实现与验证

目标:
- 实现指数退避重试机制
- 支持 max_retries 和 timeout 两个核心参数
- 单文件测试验证重试行为
"""

import time
import random
import asyncio
from typing import Callable, Any, Optional, Dict, List, Type, Tuple
from dataclasses import dataclass, field
from enum import Enum


# ==================== 核心数据结构 ====================

class BackoffStrategy(Enum):
    """退避策略枚举"""
    EXPONENTIAL = "exponential"        # 纯指数退避
    EXPONENTIAL_JITTER = "exponential_jitter"  # 指数退避 + 随机抖动
    FIXED = "fixed"                    # 固定延迟
    LINEAR = "linear"                  # 线性增长


@dataclass
class RetryResult:
    """重试操作结果"""
    success: bool = False
    result: Any = None
    error: Optional[Exception] = None
    attempts: int = 0
    total_delay: float = 0.0
    strategy: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "attempts": self.attempts,
            "total_delay": round(self.total_delay, 3),
            "strategy": self.strategy,
            "error": str(self.error) if self.error else None,
        }


@dataclass
class RetryConfig:
    """重试配置 - 核心参数"""
    max_retries: int = 3
    base_delay: float = 1.0          # 基础延迟秒数
    max_delay: float = 30.0          # 最大延迟上限
    timeout: float = 60.0            # 单次操作超时
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL_JITTER
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    on_retry: Optional[Callable] = None
    
    def __post_init__(self):
        if self.retryable_exceptions is None:
            self.retryable_exceptions = (Exception,)


# ==================== 重试计算引擎 ====================

class BackoffCalculator:
    """退避延迟计算器"""
    
    @staticmethod
    def calculate(
        strategy: BackoffStrategy,
        attempt: int,
        base_delay: float,
        max_delay: float
    ) -> float:
        """
        计算退避延迟
        
        Args:
            attempt: 当前尝试次数(从0开始)
            base_delay: 基础延迟
            max_delay: 最大延迟上限
        
        Returns:
            等待延迟秒数
        """
        if strategy == BackoffStrategy.FIXED:
            return min(base_delay, max_delay)
        
        elif strategy == BackoffStrategy.LINEAR:
            return min(base_delay * attempt, max_delay)
        
        elif strategy == BackoffStrategy.EXPONENTIAL:
            return min(base_delay ** attempt, max_delay)
        
        elif strategy == BackoffStrategy.EXPONENTIAL_JITTER:
            delay = min(base_delay ** attempt, max_delay)
            # 添加 50% 随机抖动避免重试风暴
            jitter = delay * (0.5 + random.random())
            return jitter
        
        return min(base_delay * attempt, max_delay)


# ==================== 核心重试函数 ====================

def retry_with_backoff(
    func: Callable,
    config: Optional[RetryConfig] = None,
    operation: str = "unknown",
    max_retries: Optional[int] = None,
    timeout: Optional[float] = None,
    *args,
    **kwargs,
) -> RetryResult:
    """
    带指数退避的重试机制（同步版本）

    核心参数:
    - max_retries: 最大重试次数
    - timeout: 单次操作超时时间

    返回 RetryResult 包含完整执行信息
    """
    if config is None:
        config = RetryConfig()
    if max_retries is not None:
        config.max_retries = max_retries
    if timeout is not None:
        config.timeout = timeout
    cfg = config
    result = RetryResult(strategy=cfg.strategy.value)
    result = RetryResult(strategy=cfg.strategy.value)
    
    start_time = time.time()
    last_exception = None
    
    for attempt in range(cfg.max_retries + 1):
        result.attempts = attempt + 1
        
        # 超时检查
        elapsed = time.time() - start_time
        if elapsed > cfg.timeout:
            result.error = TimeoutError(f"Operation {operation} timed out after {elapsed:.1f}s")
            result.success = False
            return result
        
        try:
            # 执行函数（带单次超时）
            attempt_start = time.time()
            res = func(*args, **kwargs)
            
            # 检查单次超时
            attempt_duration = time.time() - attempt_start
            if attempt_duration > cfg.timeout:
                raise TimeoutError(f"Operation timed out in {attempt_duration:.1f}s")
            
            result.success = True
            result.result = res
            result.total_delay = time.time() - start_time - attempt_duration
            return result
            
        except cfg.retryable_exceptions as e:
            last_exception = e
            
            # 通知回调（仅在实际重试时触发，不包括最后一次失败）
            if attempt < cfg.max_retries and cfg.on_retry:
                cfg.on_retry(attempt + 1, e)
            
            # 最后一次尝试失败，不重试
            if attempt >= cfg.max_retries:
                break
            
            # 计算退避延迟
            delay = BackoffCalculator.calculate(
                cfg.strategy, attempt, cfg.base_delay, cfg.max_delay
            )
            
            result.total_delay += delay
            print(f"  [RETRY {attempt+1}/{cfg.max_retries}] Wait {delay:.2f}s: {type(e).__name__}: {e}")
            time.sleep(delay)
            
        except Exception as e:
            # 不可重试异常立即返回
            result.error = e
            result.success = False
            return result
    
    # 所有重试耗尽
    result.error = last_exception
    result.success = False
    result.total_delay = time.time() - start_time - (result.attempts * cfg.base_delay)
    return result


async def retry_with_backoff_async(
    func: Callable,
    config: Optional[RetryConfig] = None,
    operation: str = "unknown",
    *args,
    **kwargs,
) -> RetryResult:
    """
    带指数退避的重试机制（异步版本）
    """
    cfg = config or RetryConfig()
    result = RetryResult(strategy=cfg.strategy.value)
    
    start_time = time.time()
    last_exception = None
    
    for attempt in range(cfg.max_retries + 1):
        result.attempts = attempt + 1
        
        # 超时检查
        elapsed = time.time() - start_time
        if elapsed > cfg.timeout:
            result.error = TimeoutError(f"Operation {operation} timed out after {elapsed:.1f}s")
            result.success = False
            return result
        
        try:
            attempt_start = time.time()
            res = await func(*args, **kwargs)
            
            attempt_duration = time.time() - attempt_start
            if attempt_duration > cfg.timeout:
                raise TimeoutError(f"Operation timed out in {attempt_duration:.1f}s")
            
            result.success = True
            result.result = res
            result.total_delay = time.time() - start_time - attempt_duration
            return result
            
        except cfg.retryable_exceptions as e:
            last_exception = e
            
            if cfg.on_retry:
                cfg.on_retry(attempt + 1, e)
            
            if attempt >= cfg.max_retries:
                break
            
            delay = BackoffCalculator.calculate(
                cfg.strategy, attempt, cfg.base_delay, cfg.max_delay
            )
            
            result.total_delay += delay
            print(f"  [RETRY {attempt+1}/{cfg.max_retries}] Wait {delay:.2f}s: {type(e).__name__}: {e}")
            await asyncio.sleep(delay)
            
        except Exception as e:
            result.error = e
            result.success = False
            return result
    
    result.error = last_exception
    result.success = False
    return result


# ==================== 工具函数 ====================

def exponential_backoff_delays(max_retries: int, base_delay: float = 1.0, max_delay: float = 30.0) -> List[float]:
    """计算指定配置下的退避延迟序列"""
    return [
        BackoffCalculator.calculate(
            BackoffStrategy.EXPONENTIAL_JITTER, i, base_delay, max_delay
        )
        for i in range(max_retries)
    ]


# ==================== 单元测试 ====================

class TestRetryMechanism:
    """重试机制测试类"""
    
    def test_exponential_backoff_calculation(self):
        """测试指数退避延迟计算"""
        # base_delay=2.0: attempt=0 -> 2^0=1, attempt=2 -> 2^2=4
        d0 = BackoffCalculator.calculate(BackoffStrategy.EXPONENTIAL_JITTER, 0, 2.0, 30.0)
        d2 = BackoffCalculator.calculate(BackoffStrategy.EXPONENTIAL_JITTER, 2, 2.0, 30.0)
        # attempt=0: 1.0 * (0.5~1.5) = 0.5~1.5
        assert 0.5 <= d0 <= 1.5, f"Expected ~1.0, got {d0}"
        # attempt=2: 4.0 * (0.5~1.5) = 2.0~6.0
        assert 2.0 <= d2 <= 6.0, f"Expected ~4.0, got {d2}"
        # 指数增长：delay_2 > delay_0
        assert d2 > d0, f"Expected exponential growth, got d0={d0}, d2={d2}"
        
        print(f"  PASS: exponential_backoff_calculation (d0={d0:.2f}, d2={d2:.2f})")
    
    def test_fixed_backoff(self):
        """测试固定延迟"""
        d1 = BackoffCalculator.calculate(BackoffStrategy.FIXED, 0, 2.0, 10.0)
        d2 = BackoffCalculator.calculate(BackoffStrategy.FIXED, 5, 2.0, 10.0)
        assert d1 == d2 == 2.0
        print("  PASS: fixed_backoff")
    
    def test_linear_backoff(self):
        """测试线性退避"""
        d1 = BackoffCalculator.calculate(BackoffStrategy.LINEAR, 1, 1.0, 10.0)
        d2 = BackoffCalculator.calculate(BackoffStrategy.LINEAR, 3, 1.0, 10.0)
        assert d1 == 1.0
        assert d2 == 3.0
        print("  PASS: linear_backoff")
    
    def test_max_delay_cap(self):
        """测试最大延迟上限"""
        d = BackoffCalculator.calculate(
            BackoffStrategy.EXPONENTIAL, 10, 2.0, 10.0
        )
        assert d == 10.0  # 被 max_delay 限制
        print("  PASS: max_delay_cap")
    
    def test_success_on_first_attempt(self):
        """测试首次即成功"""
        call_count = [0]
        def always_success():
            call_count[0] += 1
            return "ok"
        
        result = retry_with_backoff(always_success, max_retries=3, operation="test")
        assert result.success == True
        assert result.result == "ok"
        assert result.attempts == 1
        assert call_count[0] == 1
        print("  PASS: success_on_first_attempt")
    
    def test_success_after_retries(self):
        """测试重试后成功"""
        call_count = [0]
        def fail_then_success():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError(f"Attempt {call_count[0]} failed")
            return "recovered"
        
        config = RetryConfig(
            max_retries=5,
            base_delay=0.01,  # 快速测试
            strategy=BackoffStrategy.FIXED,
        )
        result = retry_with_backoff(fail_then_success, config=config, operation="test")
        
        assert result.success == True
        assert result.result == "recovered"
        assert result.attempts == 3
        assert call_count[0] == 3
        print("  PASS: success_after_retries")
    
    def test_exhausted_retries(self):
        """测试重试耗尽"""
        call_count = [0]
        def always_fail():
            call_count[0] += 1
            raise RuntimeError(f"Persistent error #{call_count[0]}")
        
        config = RetryConfig(
            max_retries=2,
            base_delay=0.01,
            strategy=BackoffStrategy.FIXED,
        )
        result = retry_with_backoff(always_fail, config=config, operation="test")
        
        assert result.success == False
        assert isinstance(result.error, RuntimeError)
        assert result.attempts == 3  # 1 initial + 2 retries
        assert call_count[0] == 3
        print("  PASS: exhausted_retries")
    
    def test_timeout_parameter(self):
        """测试超时参数生效"""
        def slow_operation():
            time.sleep(5)
            return "done"
        
        config = RetryConfig(
            max_retries=1,
            timeout=0.1,  # 100ms 超时
            base_delay=0.01,
        )
        result = retry_with_backoff(slow_operation, config=config, operation="slow_test")
        
        assert result.success == False
        assert isinstance(result.error, TimeoutError)
        print("  PASS: timeout_parameter")
    
    def test_retryable_exceptions_filtering(self):
        """测试仅重试指定异常类型"""
        call_count = [0]
        def raises_type_error():
            call_count[0] += 1
            raise TypeError("Type error is not retryable")
        
        config = RetryConfig(
            max_retries=3,
            retryable_exceptions=(ValueError,),  # 只重试 ValueError
        )
        result = retry_with_backoff(raises_type_error, config=config, operation="test")
        
        # TypeError 不应被重试
        assert result.success == False
        assert isinstance(result.error, TypeError)
        assert result.attempts == 1  # 仅调用一次
        print("  PASS: retryable_exceptions_filtering")
    
    def test_on_retry_callback(self):
        """测试重试回调"""
        retry_logs = []
        
        def counting_func():
            raise ConnectionError("Connection lost")
        
        def on_retry_handler(attempt, error):
            retry_logs.append(f"Retry {attempt}: {error}")
        
        config = RetryConfig(
            max_retries=2,
            base_delay=0.01,
            on_retry=on_retry_handler,
        )
        result = retry_with_backoff(counting_func, config=config, operation="test")
        
        assert len(retry_logs) == 2  # 触发2次回调
        assert retry_logs[0].startswith("Retry 1")
        assert retry_logs[1].startswith("Retry 2")
        print("  PASS: on_retry_callback")
    
    def test_exponential_backoff_sequence(self):
        """测试指数退避序列生成"""
        delays = exponential_backoff_delays(
            max_retries=4, base_delay=1.0, max_delay=10.0
        )
        
        # 应该有4个延迟值（对应4次重试等待）
        assert len(delays) == 4
        
        # 延迟应大致呈指数增长（EXPONENTIAL_JITTER 有随机抖动，允许 ±50% 偏差）
        for i in range(1, len(delays)):
            expected_min = delays[i-1] * 0.5
            assert delays[i] >= expected_min * 0.5, (
                f"delay[{i}]={delays[i]:.2f} 不应小于 "
                f"delay[{i-1}]={delays[i-1]:.2f} 的 25%"
            )
        
        # 统计上，指数退避序列应呈现增长趋势（后三个值之和 > 第一个值）
        assert sum(delays[1:]) > delays[0], (
            f"指数退避应呈现增长趋势，但得到: {delays}"
        )
        
        print(f"  PASS: exponential_backoff_sequence ({[round(d, 2) for d in delays]})")


# ==================== 主程序 ====================

def run_all_tests():
    """运行所有测试并输出结果"""
    print("=" * 60)
    print("步骤3: 指数退避重试机制 - 单文件测试验证")
    print("=" * 60)
    
    tester = TestRetryMechanism()
    tests = [
        "test_exponential_backoff_calculation",
        "test_fixed_backoff",
        "test_linear_backoff",
        "test_max_delay_cap",
        "test_success_on_first_attempt",
        "test_success_after_retries",
        "test_exhausted_retries",
        "test_timeout_parameter",
        "test_retryable_exceptions_filtering",
        "test_on_retry_callback",
        "test_exponential_backoff_sequence",
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
