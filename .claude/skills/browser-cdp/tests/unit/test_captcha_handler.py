"""
测试 captcha_handler 模块的导入和基本功能
"""
import sys
import os

# 添加 skill 目录到路径
skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, skill_dir)


def test_imports():
    """测试模块导入"""
    print("测试导入...")
    
    try:
        from src.core.captcha_handler import (
            CaptchaHandler,
            CaptchaType,
            CaptchaResult,
            AntiDetection,
            detect_and_handle_captcha,
            apply_anti_detection
        )
        print("✅ captcha_handler 导入成功")
        return True
    except Exception as e:
        print(f"❌ captcha_handler 导入失败: {e}")
        return False


def test_captcha_types():
    """测试验证码类型枚举"""
    print("\n测试验证码类型...")
    
    from src.core.captcha_handler import CaptchaType
    
    expected_types = [
        CaptchaType.SLIDER,
        CaptchaType.CLICK,
        CaptchaType.TEXT,
        CaptchaType.SMS,
        CaptchaType.EMAIL,
        CaptchaType.RECAPTCHA,
        CaptchaType.HCAPTCHA,
        CaptchaType.GEOGUESSER,
        CaptchaType.UNKNOWN,
    ]
    
    for ct in expected_types:
        print(f"  - {ct.name}: {ct.value}")
    
    print("✅ 验证码类型枚举正常")
    return True


def test_captcha_result():
    """测试验证码结果类"""
    print("\n测试 CaptchaResult...")
    
    from src.core.captcha_handler import CaptchaResult, CaptchaType
    
    # 成功结果
    result1 = CaptchaResult(
        success=True,
        captcha_type=CaptchaType.SLIDER,
        solution={"distance": 280.0},
        message="滑块滑动距离: 280.0px"
    )
    print(f"  成功结果: {result1}")
    
    # 失败结果
    result2 = CaptchaResult(
        success=False,
        captcha_type=CaptchaType.TEXT,
        message="OCR 识别失败"
    )
    print(f"  失败结果: {result2}")
    
    print("✅ CaptchaResult 正常")
    return True


def test_anti_detection():
    """测试反检测类"""
    print("\n测试 AntiDetection...")
    
    from src.core.captcha_handler import AntiDetection
    
    # 创建一个 mock session
    class MockSession:
        async def set_user_agent(self, ua):
            self.last_ua = ua
        
        async def set_viewport(self, width, height):
            self.last_viewport = (width, height)
        
        async def eval_js(self, js):
            self.last_js = js
    
    session = MockSession()
    anti = AntiDetection(session)
    
    # 测试设置 User-Agent
    anti.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
    assert anti._user_agent == "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
    print("  ✅ User-Agent 设置正常")
    
    # 测试设置视口
    anti.set_viewport(1920, 1080)
    assert anti._viewport == (1920, 1080)
    print("  ✅ 视口设置正常")
    
    # 测试获取请求头
    headers = anti.get_headers()
    assert "User-Agent" in headers
    assert "Accept" in headers
    print("  ✅ 请求头获取正常")
    
    print("✅ AntiDetection 正常")
    return True


def test_stealth_import():
    """测试 stealth 模块导入"""
    print("\n测试 stealth 模块...")
    
    try:
        from src.core.stealth import StealthMode, StealthConfig
        print("✅ stealth 导入成功")
        return True
    except Exception as e:
        print(f"❌ stealth 导入失败: {e}")
        return False


def test_smart_wait_import():
    """测试 smart_wait 模块导入"""
    print("\n测试 smart_wait 模块...")
    
    try:
        from src.core.smart_wait import SmartWait, WaitConfig
        print("✅ smart_wait 导入成功")
        return True
    except Exception as e:
        print(f"❌ smart_wait 导入失败: {e}")
        return False


def test_retry_handler_import():
    """测试 retry_handler 模块导入"""
    print("\n测试 retry_handler 模块...")
    
    try:
        from src.core.retry_handler import RetryHandler, RetryConfig
        print("✅ retry_handler 导入成功")
        return True
    except Exception as e:
        print(f"❌ retry_handler 导入失败: {e}")
        return False


def test_dynamic_loader_import():
    """测试 dynamic_loader 模块导入"""
    print("\n测试 dynamic_loader 模块...")
    
    try:
        from src.core.dynamic_loader import DynamicLoader, ScrollConfig
        print("✅ dynamic_loader 导入成功")
        return True
    except Exception as e:
        print(f"❌ dynamic_loader 导入失败: {e}")
        return False


def test_complex_dom_import():
    """测试 complex_dom 模块导入"""
    print("\n测试 complex_dom 模块...")
    
    try:
        from src.core.complex_dom import ComplexDOMHandler, DOMScanConfig
        print("✅ complex_dom 导入成功")
        return True
    except Exception as e:
        print(f"❌ complex_dom 导入失败: {e}")
        return False


def test_enhanced_session_import():
    """测试 enhanced_cdp_session 模块导入"""
    print("\n测试 enhanced_cdp_session 模块...")
    
    try:
        from src.core.enhanced_cdp_session import EnhancedCDPSession
        print("✅ enhanced_cdp_session 导入成功")
        return True
    except Exception as e:
        print(f"❌ enhanced_cdp_session 导入失败: {e}")
        return False


def main():
    print("=" * 60)
    print("browser-cdp 反爬机制处理模块测试")
    print("=" * 60)
    
    tests = [
        ("导入测试", test_imports),
        ("验证码类型", test_captcha_types),
        ("验证码结果", test_captcha_result),
        ("反检测类", test_anti_detection),
        ("stealth 模块", test_stealth_import),
        ("smart_wait 模块", test_smart_wait_import),
        ("retry_handler 模块", test_retry_handler_import),
        ("dynamic_loader 模块", test_dynamic_loader_import),
        ("complex_dom 模块", test_complex_dom_import),
        ("enhanced_cdp_session 模块", test_enhanced_session_import),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"❌ {name} 测试异常: {e}")
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for name, success in results:
        status = "✅ 通过" if success else "❌ 失败"
        print(f"  {status} - {name}")
    
    print(f"\n总计: {passed}/{total} 测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print("\n⚠️ 部分测试失败，请检查错误信息")
        return 1


if __name__ == "__main__":
    sys.exit(main())
