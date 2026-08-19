"""
显式等待与元素可见性检测 - 最小化验证用例

测试范围：
1. Condition 基础操作（AND/OR/NOT）
2. ElementVisibilityDetector 核心方法
3. ExplicitWaitEnhanced 等待流程
4. 边界情况处理
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.core.element_visibility_detector import ElementVisibilityDetector
from src.core.explicit_wait_enhanced import (
    Condition,
    EnhancedWaitConfig,
    ExplicitWaitEnhanced,
)


class MockSession:
    """模拟 session 对象，用于单元测试"""
    def __init__(self):
        self._call_history = []
        self._visible_counter = 0   # 仅对 #exists_visible 计数
        self._spinner_counter = 0    # 仅对 .loading-spinner 计数

    async def eval_js(self, js_code, args=None):
        self._call_history.append({'js': js_code[:50], 'args': args})

        # check_loading_state 调用格式：args = [LOADING_SELECTORS, target, OVERLAY_SELECTORS]
        if args and len(args) == 3 and isinstance(args[0], list):
            target = args[1] if len(args) > 1 else ''
            # 仅当 target 非空且包含 loading/spinner 时才视为加载中
            if target and ('loading' in target.lower() or 'spinner' in target.lower()):
                return {'page_loading': True, 'has_overlay': False, 'loading_count': 1}
            # 否则页面未加载（允许后续可见性检查正常进行）
            return {'page_loading': False, 'has_overlay': False, 'loading_count': 0}

        selector = args[0] if args else None

        if selector == '#exists_visible':
            self._visible_counter += 1
            # 前两次调用返回不可见，第三次起返回可见
            if self._visible_counter <= 2:
                return {'visible': False, 'interactive': False, 'exists': True,
                        'in_viewport': False, 'rect': {'x': 0, 'y': 0, 'w': 0, 'h': 0}}
            return {'visible': True, 'interactive': True, 'exists': True,
                    'in_viewport': True, 'rect': {'x': 10, 'y': 20, 'w': 100, 'h': 30}}
        elif selector == '#exists_hidden':
            return {'visible': False, 'interactive': False, 'exists': True,
                    'in_viewport': False, 'rect': {'x': 0, 'y': 0, 'w': 0, 'h': 0}}
        elif selector == '#not_exists':
            return {'visible': False, 'interactive': False, 'exists': False}
        elif selector == '.loading-spinner':
            self._spinner_counter += 1
            return {'page_loading': True, 'has_overlay': False, 'loading_count': 1}
        else:
            return {'visible': False, 'interactive': False,
                    'exists': selector is not None,
                    'in_viewport': False, 'rect': {'x': 0, 'y': 0, 'w': 0, 'h': 0}}

    def reset(self):
        self._call_history = []
        self._visible_counter = 0
        self._spinner_counter = 0


async def test_condition_basic():
    print("\n=== 测试 Condition 基础操作 ===")
    cond_a = Condition("a", lambda: True)
    cond_b = Condition("b", lambda: False)

    cond_and = cond_a & cond_b
    assert cond_and.evaluate() == False
    print("  [PASS] AND 操作正确")

    cond_or = cond_a | cond_b
    assert cond_or.evaluate() == True
    print("  [PASS] OR 操作正确")

    cond_not = ~cond_a
    assert cond_not.evaluate() == False
    print("  [PASS] NOT 操作正确")

    complex_cond = (cond_a & cond_b) | (~cond_a)
    assert complex_cond.evaluate() == False
    print("  [PASS] 复合条件正确")

    return True


async def test_element_visibility_detector():
    print("\n=== 测试 ElementVisibilityDetector ===")
    session = MockSession()
    detector = ElementVisibilityDetector(session)

    result = await detector.check_visibility('#exists_visible', timeout=2.0)
    assert result.exists == True, "元素应该存在"
    assert result.visible == True, "元素应该可见（自动轮询）"
    assert result.interactive == True, "元素应该可交互"
    print("  [PASS] 可见元素检测正确")

    result = await detector.check_visibility('#exists_hidden', timeout=2.0)
    assert result.exists == True, "隐藏元素应该存在"
    assert result.visible == False, "隐藏元素不应该可见"
    print("  [PASS] 隐藏元素检测正确")

    result = await detector.check_visibility('#not_exists', timeout=2.0)
    assert result.exists == False, "不存在的元素应该返回 exists=False"
    print("  [PASS] 不存在元素检测正确")

    # check_loading_state 使用列表参数格式调用
    loading_state = await detector.check_loading_state('.loading-spinner')
    assert loading_state.get('page_loading') == True, f"应该有加载状态，实际: {loading_state}"
    print("  [PASS] 加载状态检测正确")

    blocked = await detector.is_element_blocked('#exists_visible')
    print(f"  [PASS] 遮挡检测: blocked={blocked}")

    stats = detector.get_stats()
    assert stats['total'] >= 3, f"应该有至少3条记录，实际: {stats}"
    print("  [PASS] 统计信息正确")

    return True


async def test_explicit_wait_enhanced():
    print("\n=== 测试 ExplicitWaitEnhanced ===")
    session = MockSession()
    wait = ExplicitWaitEnhanced(session)

    # 测试自定义异步条件等待（动态返回）
    async def dynamic_check():
        data = await session.eval_js("", ['#exists_visible'])
        return data.get('visible', False)

    result = await wait.until(dynamic_check, timeout=2.0)
    assert result.success == True, f"动态条件应该成功，实际: {result}"
    print("  [PASS] 自定义条件等待正确")

    # 测试超时处理
    result = await wait.until(lambda: False, timeout=0.5)
    assert result.success == False
    assert result.error == "timeout"
    print("  [PASS] 超时处理正确")

    # 测试 wait_for_visible（重置以重新计数可见性）
    session.reset()
    result = await wait.wait_for_visible('#exists_visible', timeout=2.0)
    assert result.success == True, f"可见元素应该成功等待，实际: {result}"
    assert result.visibility is not None
    assert result.visibility.visible == True
    print("  [PASS] wait_for_visible 正确")

    # 测试等待不存在元素超时
    result = await wait.wait_for_visible('#not_exists', timeout=1.0)
    assert result.success == False
    print("  [PASS] 等待不存在元素超时处理正确")

    # 测试 check_element_state（先重置，再等待可见）
    session.reset()
    state = await wait.check_element_state('#exists_visible')
    assert state['exists'] == True
    assert state['visible'] == True
    assert state['status'] == 'ready'
    print("  [PASS] check_element_state 正确")

    # 测试加载状态感知
    config_loading = EnhancedWaitConfig(timeout=1.0, detect_page_load=True,
                                         ignore_loading_overlay=True)
    wait_loading = ExplicitWaitEnhanced(MockSession(), config_loading)
    # 直接检查 loading 元素的加载状态
    loading_result = await wait_loading._visibility_detector.check_loading_state('.loading-spinner')
    assert loading_result.get('page_loading') == True, f"应检测到加载状态，实际: {loading_result}"
    print("  [PASS] 加载状态感知正确")

    # 测试统计
    stats = wait.get_stats()
    assert stats['total'] >= 1
    assert 'success_rate' in stats
    print("  [PASS] 统计信息正确")

    return True


async def test_edge_cases():
    print("\n=== 测试边界情况 ===")
    session = MockSession()
    wait = ExplicitWaitEnhanced(session)

    # 空 selector
    result = await wait.wait_for_visible('', timeout=0.5)
    assert result.success == False
    print("  [PASS] 空 selector 处理正确")

    # 极短超时
    result = await wait.until(lambda: False, timeout=0.1)
    assert result.success == False
    assert result.elapsed < 0.5
    print("  [PASS] 极短超时处理正确")

    # 空统计
    wait_empty = ExplicitWaitEnhanced(MockSession())
    stats = wait_empty.get_stats()
    assert stats['total'] == 0
    print("  [PASS] 空统计处理正确")

    return True


async def test_integration():
    print("\n=== 集成测试：完整等待流程 ===")
    session = MockSession()
    wait = ExplicitWaitEnhanced(session)

    state = await wait.check_element_state('#exists_visible')
    assert state['status'] == 'ready', f"状态应该是 ready，实际: {state['status']}"
    print(f"  [PASS] 综合状态检查: {state['status']}")

    result = await wait.wait_for_not_blocked('#exists_visible', timeout=1.0)
    print(f"  [PASS] 等待未遮挡完成: success={result.success}")

    return True


async def main():
    print("=" * 60)
    print("显式等待与元素可见性检测 - 验证用例")
    print("=" * 60)

    tests = [
        ("Condition 基础操作", test_condition_basic),
        ("ElementVisibilityDetector", test_element_visibility_detector),
        ("ExplicitWaitEnhanced", test_explicit_wait_enhanced),
        ("边界情况", test_edge_cases),
        ("集成测试", test_integration),
    ]

    results = []
    for name, test_func in tests:
        try:
            start = time.time()
            await test_func()
            elapsed = time.time() - start
            results.append((name, True, elapsed))
            print(f"  [RESULT] {name} PASS ({elapsed:.2f}s)")
        except Exception as e:
            results.append((name, False, 0))
            print(f"  [RESULT] {name} FAIL: {e}")

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"通过: {passed}/{total}")
    for name, ok, elapsed in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")

    if passed == total:
        print("\n所有测试通过！")
        return 0
    else:
        print(f"\n{total - passed} 个测试失败")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
