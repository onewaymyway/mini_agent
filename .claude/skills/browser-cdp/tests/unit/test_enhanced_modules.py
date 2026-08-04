"""
test_enhanced_cdp.py - 增强模块测试脚本

测试所有新增模块的功能。
"""
import asyncio
import sys
import logging
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.smart_wait import SmartWait, WaitConfig
from src.core.retry_handler import RetryHandler, RetryConfig, FailureReason
from src.core.dynamic_loader import DynamicLoader, ScrollConfig
from src.core.complex_dom import ComplexDOMHandler, DOMScanConfig
from src.core.stealth import StealthMode, StealthConfig
from src.core.enhanced_cdp_session import EnhancedCDPSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================================================================
# 测试 SmartWait
# =========================================================================

async def test_smart_wait():
    """测试智能等待模块"""
    logger.info("=== 测试 SmartWait ===")
    
    # 测试配置
    config = WaitConfig(
        timeout=10.0,
        idle_timeout=0.5,
        check_interval=0.3,
        stable_count=3
    )
    
    # 注意：实际测试需要 CDP session
    # 这里只测试配置和初始化
    wait = SmartWait(None, config)
    logger.info(f"SmartWait 初始化成功: timeout={config.timeout}s")
    return True


# =========================================================================
# 测试 RetryHandler
# =========================================================================

async def test_retry_handler():
    """测试重试处理器"""
    logger.info("=== 测试 RetryHandler ===")
    
    # 测试配置
    config = RetryConfig(
        max_attempts=3,
        base_delay=0.1,
        max_delay=1.0,
        retry_on=[FailureReason.TIMEOUT, FailureReason.NETWORK_ERROR]
    )
    
    handler = RetryHandler(config)
    
    # 测试成功场景
    async def success_func():
        return "success"
    
    result = await handler.execute(success_func)
    assert result == "success"
    logger.info("✓ 成功场景测试通过")
    
    # 测试重试场景
    call_count = 0
    async def fail_then_success():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("timeout exceeded")
        return "success_after_retry"
    
    result = await handler.execute(fail_then_success)
    assert result == "success_after_retry"
    assert call_count == 3
    logger.info("✓ 重试场景测试通过")
    
    # 测试熔断器
    cb = handler.circuit_breaker
    assert cb.state == "closed"
    logger.info("✓ 熔断器状态测试通过")
    
    return True


# =========================================================================
# 测试 DynamicLoader
# =========================================================================

async def test_dynamic_loader():
    """测试动态内容加载模块"""
    logger.info("=== 测试 DynamicLoader ===")
    
    # 注意：实际测试需要 CDP session
    loader = DynamicLoader(None)
    logger.info("DynamicLoader 初始化成功")
    return True


# =========================================================================
# 测试 ComplexDOMHandler
# =========================================================================

async def test_complex_dom():
    """测试复杂 DOM 处理模块"""
    logger.info("=== 测试 ComplexDOMHandler ===")
    
    # 注意：实际测试需要 CDP session
    handler = ComplexDOMHandler(None)
    logger.info("ComplexDOMHandler 初始化成功")
    return True


# =========================================================================
# 测试 StealthMode
# =========================================================================

async def test_stealth():
    """测试反检测模块"""
    logger.info("=== 测试 StealthMode ===")
    
    # 测试配置
    config = StealthConfig(
        enable_webdriver_removal=True,
        enable_chrome_runtime=True,
        humanize_mouse=True,
        humanize_typing=True
    )
    
    # 注意：实际测试需要 CDP session
    stealth = StealthMode(None, config)
    logger.info("StealthMode 初始化成功")
    
    # 测试用户代理生成
    ua = stealth.get_random_user_agent()
    assert "Mozilla" in ua
    logger.info(f"✓ 用户代理生成测试通过: {ua[:50]}...")
    
    return True


# =========================================================================
# 测试 EnhancedCDPSession
# =========================================================================

async def test_enhanced_session():
    """测试增强会话"""
    logger.info("=== 测试 EnhancedCDPSession ===")
    
    # 注意：实际测试需要 CDP 连接
    # 这里只测试模块导入和初始化
    logger.info("EnhancedCDPSession 模块导入成功")
    return True


# =========================================================================
# 主测试流程
# =========================================================================

async def run_all_tests():
    """运行所有测试"""
    tests = [
        ("SmartWait", test_smart_wait),
        ("RetryHandler", test_retry_handler),
        ("DynamicLoader", test_dynamic_loader),
        ("ComplexDOMHandler", test_complex_dom),
        ("StealthMode", test_stealth),
        ("EnhancedCDPSession", test_enhanced_session),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, True, None))
            logger.info(f"✓ {name} 测试通过")
        except Exception as e:
            results.append((name, False, str(e)))
            logger.error(f"✗ {name} 测试失败: {e}")
    
    # 汇总结果
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    
    logger.info(f"\n测试结果: {passed}/{total} 通过")
    
    if passed == total:
        logger.info("✓ 所有测试通过")
        return 0
    else:
        logger.error("✗ 部分测试失败")
        for name, ok, err in results:
            if not ok:
                logger.error(f"  - {name}: {err}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_all_tests())
    sys.exit(exit_code)
