"""
page_render_detector.py - 页面渲染完成检测器

通过多种策略检测页面是否已完全渲染：
1. DOM 变化监听（MutationObserver）
2. 内容哈希比对（稳定性检测）
3. 动画/过渡完成检测
4. 字体加载完成检测
5. 综合渲染完成判断
"""
from __future__ import annotations

import asyncio
import logging
import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class RenderResult:
    """渲染检测结果"""
    success: bool
    elapsed: float
    strategy: str
    details: Dict[str, Any] = field(default_factory=dict)
    dom_changes: int = 0
    hash_stable_rounds: int = 0
    animations_done: bool = False
    fonts_done: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'success': self.success,
            'elapsed': round(self.elapsed, 2),
            'strategy': self.strategy,
            'details': self.details,
            'dom_changes': self.dom_changes,
            'hash_stable_rounds': self.hash_stable_rounds,
            'animations_done': self.animations_done,
            'fonts_done': self.fonts_done,
        }


@dataclass
class RenderConfig:
    """渲染检测配置"""
    timeout: float = 30.0
    check_interval: float = 0.3
    stable_rounds: int = 3  # DOM 哈希连续稳定次数
    dom_stable_threshold: int = 10  # DOM 节点数变化阈值
    max_dom_changes: int = 50  # 最大允许 DOM 变化次数
    wait_animations: bool = True
    wait_fonts: bool = True
    hash_algorithm: str = 'crc32'  # 哈希算法


class PageRenderDetector:
    """
    页面渲染完成检测器

    使用多策略组合判断页面是否完全渲染：
    1. MutationObserver 监听 DOM 变化
    2. 页面内容哈希比对检测稳定性
    3. CSS 动画/过渡完成检测
    4. Web Font 加载完成检测
    """

    def __init__(self, session, config: Optional[RenderConfig] = None):
        self.session = session
        self.config = config or RenderConfig()
        self._mutation_observer_installed = False
        self._dom_change_count = 0
        self._last_dom_hash = ""
        self._stable_count = 0

    # =========================================================================
    # 1. DOM 变化监听
    # =========================================================================

    async def wait_for_dom_stable(
        self,
        timeout: float = None,
        stable_rounds: int = None,
        check_interval: float = None,
    ) -> RenderResult:
        """
        等待 DOM 结构稳定（通过 MutationObserver 监听）

        Args:
            timeout: 超时时间（秒）
            stable_rounds: 连续稳定次数
            check_interval: 检查间隔（秒）

        Returns:
            RenderResult: 检测结果
        """
        timeout = timeout or self.config.timeout
        stable_rounds = stable_rounds or self.config.stable_rounds
        check_interval = check_interval or self.config.check_interval

        start_time = time.time()
        self._dom_change_count = 0
        self._last_dom_hash = ""
        self._stable_count = 0

        logger.info(f"开始 DOM 稳定检测: stable_rounds={stable_rounds}, timeout={timeout}s")

        # 注入 MutationObserver
        await self._install_mutation_observer()

        try:
            while time.time() - start_time < timeout:
                # 获取当前 DOM 哈希
                current_hash = await self._get_dom_hash()
                changes = await self._get_dom_change_count()

                if current_hash != self._last_dom_hash:
                    # DOM 发生变化
                    self._stable_count = 0
                    self._last_dom_hash = current_hash
                    self._dom_change_count += 1
                    logger.debug(f"DOM 发生变化，哈希={current_hash[:8]}..., 总变化={self._dom_change_count}")

                    # 如果变化过多，提前返回
                    if self._dom_change_count > self.config.max_dom_changes:
                        logger.warning(f"DOM 变化过多 ({self._dom_change_count})，认为页面持续渲染中")
                        return RenderResult(
                            success=False,
                            elapsed=time.time() - start_time,
                            strategy='dom_stable',
                            details={'error': 'too_many_changes', 'change_count': self._dom_change_count},
                            dom_changes=self._dom_change_count,
                        )
                else:
                    # DOM 未变化，增加稳定计数
                    self._stable_count += 1
                    logger.debug(f"DOM 稳定检测 #{self._stable_count}/{stable_rounds}")

                    if self._stable_count >= stable_rounds:
                        elapsed = time.time() - start_time
                        logger.info(f"DOM 稳定检测通过: 耗时 {elapsed:.2f}s, 总变化 {self._dom_change_count} 次")
                        return RenderResult(
                            success=True,
                            elapsed=elapsed,
                            strategy='dom_stable',
                            details={'change_count': self._dom_change_count, 'stable_rounds': stable_rounds},
                            dom_changes=self._dom_change_count,
                            hash_stable_rounds=stable_rounds,
                        )

                await asyncio.sleep(check_interval)

            elapsed = time.time() - start_time
            logger.warning(f"DOM 稳定检测超时: 耗时 {elapsed:.2f}s, 当前稳定 {self._stable_count}/{stable_rounds}")
            return RenderResult(
                success=False,
                elapsed=elapsed,
                strategy='dom_stable',
                details={'error': 'timeout', 'stable_rounds': self._stable_count, 'required': stable_rounds},
                dom_changes=self._dom_change_count,
            )
        finally:
            await self._remove_mutation_observer()

    async def _install_mutation_observer(self) -> None:
        """注入 MutationObserver 监听 DOM 变化"""
        if self._mutation_observer_installed:
            return
        js = """
        (function() {
            if (window.__browser_cdp_mutation_observer) return;
            window.__browser_cdp_dom_changes = 0;
            var observer = new MutationObserver(function(mutations) {
                window.__browser_cdp_dom_changes += mutations.length;
            });
            observer.observe(document.documentElement, {
                childList: true,
                attributes: true,
                characterData: true,
                subtree: true
            });
            window.__browser_cdp_mutation_observer = observer;
        })()
        """
        try:
            await self.session.eval_js(js)
            self._mutation_observer_installed = True
        except Exception as e:
            logger.debug(f"PageRenderDetector: 注入 MutationObserver 失败: {e}")

    async def _remove_mutation_observer(self) -> None:
        """移除 MutationObserver"""
        if not self._mutation_observer_installed:
            return
        js = """
        (function() {
            if (!window.__browser_cdp_mutation_observer) return;
            window.__browser_cdp_mutation_observer.disconnect();
            delete window.__browser_cdp_mutation_observer;
            delete window.__browser_cdp_dom_changes;
        })()
        """
        try:
            await self.session.eval_js(js)
        except Exception as e:
            logger.debug(f"PageRenderDetector: 移除 MutationObserver 失败: {e}")
        finally:
            self._mutation_observer_installed = False

    async def _get_dom_change_count(self) -> int:
        """获取当前 DOM 变化次数"""
        try:
            result = await self.session.eval_js("window.__browser_cdp_dom_changes || 0")
            return int(result) if result else 0
        except Exception:
            return 0

    async def _get_dom_hash(self) -> str:
        """获取当前 DOM 结构哈希"""
        try:
            js = """
            (function() {
                // 只取 body 内的结构化内容，排除动态 ID
                var body = document.body ? document.body.innerHTML : '';
                // 移除动态属性（如 data-reactid, ng-scope 等）
                body = body.replace(/data-(react|vue|ng)[^=]*="[^"]*"/g, '');
                body = body.replace(/id="dyn-[a-z0-9]+"/g, '');
                // 计算 CRC32 风格的哈希
                var hash = 0;
                for (var i = 0; i < Math.min(body.length, 50000); i++) {
                    hash = ((hash << 5) - hash) + body.charCodeAt(i);
                    hash = hash & hash;
                }
                return (hash >>> 0).toString(16);
            })()
            """
            return await self.session.eval_js(js)
        except Exception:
            return ""

    # =========================================================================
    # 2. 文本内容稳定性检测
    # =========================================================================

    async def wait_for_content_stable(
        self,
        timeout: float = None,
        stable_rounds: int = None,
        check_interval: float = None,
    ) -> RenderResult:
        """
        等待页面文本内容稳定（多次读取不变）

        Args:
            timeout: 超时时间（秒）
            stable_rounds: 连续稳定次数
            check_interval: 检查间隔（秒）

        Returns:
            RenderResult: 检测结果
        """
        timeout = timeout or self.config.timeout
        stable_rounds = stable_rounds or self.config.stable_rounds
        check_interval = check_interval or self.config.check_interval

        start_time = time.time()
        previous_content = None
        stable_count = 0

        logger.info(f"开始内容稳定检测: stable_rounds={stable_rounds}")

        while time.time() - start_time < timeout:
            try:
                content = await self.session.eval_js("document.body ? document.body.innerText : ''")
            except Exception:
                await asyncio.sleep(check_interval)
                continue

            if previous_content is not None:
                if content == previous_content:
                    stable_count += 1
                    logger.debug(f"内容稳定检测 #{stable_count}/{stable_rounds}")
                    if stable_count >= stable_rounds:
                        elapsed = time.time() - start_time
                        logger.info(f"内容稳定检测通过: 耗时 {elapsed:.2f}s")
                        return RenderResult(
                            success=True,
                            elapsed=elapsed,
                            strategy='content_stable',
                            details={'stable_rounds': stable_count},
                            hash_stable_rounds=stable_count,
                        )
                else:
                    stable_count = 0
                    logger.debug("内容发生变化，重置稳定计数")

            previous_content = content
            await asyncio.sleep(check_interval)

        elapsed = time.time() - start_time
        logger.warning(f"内容稳定检测超时: 耗时 {elapsed:.2f}s")
        return RenderResult(
            success=False,
            elapsed=elapsed,
            strategy='content_stable',
            details={'error': 'timeout', 'final_rounds': stable_count},
        )

    # =========================================================================
    # 3. CSS 动画/过渡完成检测
    # =========================================================================

    async def wait_for_animations_done(
        self,
        timeout: float = None,
        check_interval: float = None,
    ) -> RenderResult:
        """
        等待 CSS 动画和过渡完成

        Args:
            timeout: 超时时间（秒）
            check_interval: 检查间隔（秒）

        Returns:
            RenderResult: 检测结果
        """
        timeout = timeout or self.config.timeout
        check_interval = check_interval or self.config.check_interval

        start_time = time.time()

        logger.info("开始动画完成检测")

        check_js = """
        (function() {
            // 获取所有具有动画或过渡的元素
            var allElements = document.querySelectorAll('*');
            var animating = 0;
            var transitioning = 0;
            for (var i = 0; i < allElements.length; i++) {
                var el = allElements[i];
                try {
                    var style = getComputedStyle(el);
                    if (style.animationName && style.animationName !== 'none' && style.animationPlayState !== 'paused') {
                        animating++;
                    }
                    if (style.transitionProperty && style.transitionProperty !== 'none' && style.transitionDuration !== '0s') {
                        transitioning++;
                    }
                } catch(e) {}
            }
            return { animating: animating, transitioning: transitioning };
        })()
        """

        while time.time() - start_time < timeout:
            try:
                result = await self.session.eval_js(check_js)
            except Exception:
                await asyncio.sleep(check_interval)
                continue

            animating = result.get('animating', 0) if isinstance(result, dict) else 0
            transitioning = result.get('transitioning', 0) if isinstance(result, dict) else 0

            if animating == 0 and transitioning == 0:
                elapsed = time.time() - start_time
                logger.info(f"动画完成检测通过: 耗时 {elapsed:.2f}s")
                return RenderResult(
                    success=True,
                    elapsed=elapsed,
                    strategy='animations_done',
                    details={'animations': 0, 'transitions': 0},
                    animations_done=True,
                )

            logger.debug(f"动画状态: animating={animating}, transitioning={transitioning}")
            await asyncio.sleep(check_interval)

        elapsed = time.time() - start_time
        logger.warning(f"动画完成检测超时: 耗时 {elapsed:.2f}s")
        return RenderResult(
            success=False,
            elapsed=elapsed,
            strategy='animations_done',
            details={'error': 'timeout', 'animating': animating, 'transitioning': transitioning},
            animations_done=False,
        )

    # =========================================================================
    # 4. Web Font 加载完成检测
    # =========================================================================

    async def wait_for_fonts_loaded(
        self,
        timeout: float = None,
        check_interval: float = None,
    ) -> RenderResult:
        """
        等待 Web Font 加载完成

        Args:
            timeout: 超时时间（秒）
            check_interval: 检查间隔（秒）

        Returns:
            RenderResult: 检测结果
        """
        timeout = timeout or self.config.timeout
        check_interval = check_interval or self.config.check_interval

        start_time = time.time()

        logger.info("开始字体加载检测")

        while time.time() - start_time < timeout:
            try:
                ready = await self.session.eval_js("document.fonts && document.fonts.ready ? true : false")
            except Exception:
                await asyncio.sleep(check_interval)
                continue

            if ready:
                elapsed = time.time() - start_time
                logger.info(f"字体加载完成检测通过: 耗时 {elapsed:.2f}s")
                return RenderResult(
                    success=True,
                    elapsed=elapsed,
                    strategy='fonts_loaded',
                    details={},
                    fonts_done=True,
                )

            await asyncio.sleep(check_interval)

        elapsed = time.time() - start_time
        logger.warning(f"字体加载完成检测超时: 耗时 {elapsed:.2f}s")
        return RenderResult(
            success=False,
            elapsed=elapsed,
            strategy='fonts_loaded',
            details={'error': 'timeout'},
            fonts_done=False,
        )

    # =========================================================================
    # 5. 综合渲染完成检测
    # =========================================================================

    async def wait_for_page_ready(
        self,
        strategy: str = 'auto',
        timeout: float = None,
        dom_stable: bool = True,
        content_stable: bool = True,
        animations: bool = None,
        fonts: bool = None,
    ) -> RenderResult:
        """
        综合检测页面渲染完成

        根据页面复杂度和配置，组合多种检测策略。

        Args:
            strategy: 检测策略，可选 'auto'/'dom'/'content'/'combined'
            timeout: 总超时时间（秒）
            dom_stable: 是否检测 DOM 稳定
            content_stable: 是否检测内容稳定
            animations: 是否检测动画完成（None=自动）
            fonts: 是否检测字体加载（None=自动）

        Returns:
            RenderResult: 检测结果
        """
        timeout = timeout or self.config.timeout
        animations = animations if animations is not None else self.config.wait_animations
        fonts = fonts if fonts is not None else self.config.wait_fonts

        start_time = time.time()
        results = []
        remaining = timeout

        logger.info(f"开始综合渲染检测: strategy={strategy}")

        if strategy in ('auto', 'dom') and dom_stable:
            elapsed_start = time.time() - start_time
            dom_result = await self.wait_for_dom_stable(timeout=remaining)
            results.append(dom_result)
            if not dom_result.success:
                logger.warning(f"DOM 稳定检测失败: {dom_result.details}")
            remaining -= (time.time() - start_time - elapsed_start)

        if strategy in ('auto', 'content') and content_stable and remaining > 0:
            elapsed_start = time.time() - start_time
            content_result = await self.wait_for_content_stable(timeout=remaining)
            results.append(content_result)
            if not content_result.success:
                logger.warning(f"内容稳定检测失败: {content_result.details}")
            remaining -= (time.time() - start_time - elapsed_start)

        if animations and remaining > 0:
            elapsed_start = time.time() - start_time
            anim_result = await self.wait_for_animations_done(timeout=remaining)
            results.append(anim_result)
            if not anim_result.success:
                logger.warning(f"动画完成检测失败: {anim_result.details}")
            remaining -= (time.time() - start_time - elapsed_start)

        if fonts and remaining > 0:
            elapsed_start = time.time() - start_time
            font_result = await self.wait_for_fonts_loaded(timeout=remaining)
            results.append(font_result)
            if not font_result.success:
                logger.warning(f"字体加载检测失败: {font_result.details}")
            remaining -= (time.time() - start_time - elapsed_start)

        # 综合判断
        elapsed = time.time() - start_time
        all_success = all(r.success for r in results) if results else False

        # 至少有一个策略成功才认为整体成功
        any_success = any(r.success for r in results) if results else True

        if not results:
            # 没有配置任何检测策略，直接返回成功
            return RenderResult(
                success=True,
                elapsed=elapsed,
                strategy='none',
                details={'note': 'no detection strategies configured'},
            )

        success = all_success or any_success
        primary_strategy = results[0].strategy if results else 'unknown'

        logger.info(f"综合渲染检测完成: success={success}, elapsed={elapsed:.2f}s, strategies={[r.strategy for r in results]}")

        return RenderResult(
            success=success,
            elapsed=elapsed,
            strategy=primary_strategy,
            details={
                'all_strategies': [r.to_dict() for r in results],
                'all_success': all_success,
                'any_success': any_success,
            },
            dom_changes=results[0].dom_changes if results and results[0].dom_changes > 0 else 0,
            hash_stable_rounds=results[0].hash_stable_rounds if results and results[0].hash_stable_rounds > 0 else 0,
            animations_done=any(r.animations_done for r in results),
            fonts_done=any(r.fonts_done for r in results),
        )

    # =========================================================================
    # 6. 增量检测：等待特定选择器出现后页面稳定
    # =========================================================================

    async def wait_for_selector_and_stable(
        self,
        selector: str,
        timeout: float = None,
        extra_stable_timeout: float = 5.0,
    ) -> RenderResult:
        """
        等待指定选择器出现，然后等待页面稳定

        适用于：知道某个关键元素会出现，但不确定何时出现。

        Args:
            selector: CSS 选择器
            timeout: 等待选择器的超时时间
            extra_stable_timeout: 选择器出现后额外等待稳定的时间

        Returns:
            RenderResult: 检测结果
        """
        timeout = timeout or self.config.timeout
        start_time = time.time()

        logger.info(f"开始等待选择器 [{selector}] 并检测稳定")

        # 阶段1：等待选择器出现
        wait_js = f"document.querySelector({selector!r}) !== null"
        deadline = start_time + timeout
        selector_found = False

        while time.time() < deadline:
            try:
                found = await self.session.eval_js(wait_js)
            except Exception:
                await asyncio.sleep(0.3)
                continue
            if found:
                selector_found = True
                break
            await asyncio.sleep(0.3)

        if not selector_found:
            elapsed = time.time() - start_time
            return RenderResult(
                success=False,
                elapsed=elapsed,
                strategy='selector_and_stable',
                details={'error': 'selector_not_found', 'selector': selector},
            )

        # 阶段2：选择器出现后，等待页面稳定
        stable_deadline = time.time() + extra_stable_timeout
        previous_hash = None
        stable_count = 0

        while time.time() < stable_deadline:
            try:
                current_hash = await self._get_dom_hash()
            except Exception:
                await asyncio.sleep(0.3)
                continue

            if current_hash != previous_hash:
                stable_count = 0
                previous_hash = current_hash
            else:
                stable_count += 1
                if stable_count >= 3:
                    elapsed = time.time() - start_time
                    logger.info(f"选择器 [{selector}] 出现后页面稳定: 耗时 {elapsed:.2f}s")
                    return RenderResult(
                        success=True,
                        elapsed=elapsed,
                        strategy='selector_and_stable',
                        details={'selector': selector, 'stable_rounds': stable_count},
                        hash_stable_rounds=stable_count,
                    )

            await asyncio.sleep(0.3)

        elapsed = time.time() - start_time
        logger.warning(f"选择器 [{selector}] 出现后稳定检测超时: 耗时 {elapsed:.2f}s")
        return RenderResult(
            success=False,
            elapsed=elapsed,
            strategy='selector_and_stable',
            details={'error': 'timeout_after_selector', 'selector': selector, 'stable_rounds': stable_count},
        )


# =========================================================================
# 模块级便捷函数
# =========================================================================

async def wait_for_page_ready(
    session,
    timeout: float = 30.0,
    dom_stable: bool = True,
    content_stable: bool = True,
) -> dict:
    """
    便捷函数：等待页面渲染完成

    Args:
        session: CDP session 对象
        timeout: 超时时间
        dom_stable: 是否检测 DOM 稳定
        content_stable: 是否检测内容稳定

    Returns:
        dict: {success, elapsed, strategy, details}
    """
    detector = PageRenderDetector(session)
    result = await detector.wait_for_page_ready(
        timeout=timeout,
        dom_stable=dom_stable,
        content_stable=content_stable,
    )
    return result.to_dict()


def create_render_detector(session, **kwargs) -> PageRenderDetector:
    """
    工厂函数：创建渲染检测器

    Args:
        session: CDP session 对象
        **kwargs: 传递给 RenderConfig 的参数

    Returns:
        PageRenderDetector 实例
    """
    config = RenderConfig(**kwargs)
    return PageRenderDetector(session, config)


async def wait_for_page_ready(
    session,
    timeout: float = 30.0,
    strategy: str = "auto",
    **kwargs,
) -> RenderResult:
    """
    便捷函数：等待页面渲染完成

    Args:
        session: CDP session 对象
        timeout: 超时时间（秒）
        strategy: 检测策略 ("auto" | "dom" | "network" | "visual")
        **kwargs: 传递给 PageRenderDetector 的其他参数

    Returns:
        RenderResult: 渲染检测结果
    """
    detector = PageRenderDetector(session)
    detector.config.timeout = timeout
    detector.config.strategy = strategy
    return await detector.wait_for_ready()

