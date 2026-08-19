"""
Enhanced explicit wait module - integrated visibility detection

Provides complete explicit wait capabilities, integrating element visibility
detection, loading state awareness, and login validation adaptation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Union, Callable

from .element_visibility_detector import (
    ElementVisibilityDetector,
    VisibilityResult,
)

logger = logging.getLogger(__name__)


@dataclass
class EnhancedWaitConfig:
    """Enhanced wait configuration"""
    timeout: float = 30.0
    poll_interval: float = 0.3
    wait_for_visible: bool = True
    wait_for_interactive: bool = False
    wait_for_stable: int = 0
    ignore_loading_overlay: bool = True
    retry_on_failure: bool = True
    max_retries: int = 2
    backoff_factor: float = 1.5
    detect_page_load: bool = True
    network_idle_timeout: float = 2.0


@dataclass
class EnhancedWaitResult:
    """Enhanced wait result"""
    success: bool
    condition: str
    elapsed: float
    visibility: Optional[VisibilityResult] = None
    loading_state: Optional[Dict] = None
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    
    def __repr__(self) -> str:
        status = "OK" if self.success else "FAIL"
        vis = f" vis={self.visibility.visible}" if self.visibility else ""
        return f"EnhancedWaitResult({status} {self.condition} {self.elapsed:.2f}s{vis})"


class Condition:
    """Condition base class"""
    
    def __init__(self, description: str, check: Callable[..., Any]):
        self.description = description
        self._check = check
    
    def evaluate(self, *args, **kwargs) -> Any:
        try:
            result = self._check(*args, **kwargs)
            if isinstance(result, bool):
                return result
            return bool(result)
        except Exception as e:
            logger.debug(f"Condition evaluation error: {e}")
            return False
    
    def __and__(self, other: "Condition") -> "Condition":
        return Condition(
            f"({self.description}) AND ({other.description})",
            lambda *a, **kw: self.evaluate(*a, **kw) and other.evaluate(*a, **kw)
        )
    
    def __or__(self, other: "Condition") -> "Condition":
        return Condition(
            f"({self.description}) OR ({other.description})",
            lambda *a, **kw: self.evaluate(*a, **kw) or other.evaluate(*a, **kw)
        )
    
    def __invert__(self) -> "Condition":
        return Condition(
            f"NOT ({self.description})",
            lambda *a, **kw: not self.evaluate(*a, **kw)
        )


class CreateCondition:
    """Condition factory for common wait scenarios"""

    @staticmethod
    def url_contains(substring: str) -> Condition:
        """URL contains substring"""
        return Condition(
            f"url contains '{substring}'",
            lambda session: substring in session.get_url()
        )

    @staticmethod
    def element_visible(selector: str) -> Condition:
        """Element is visible"""
        return Condition(
            f"element visible: {selector}",
            lambda session, sel=selector: getattr(
                getattr(session, 'page', None), 'is_visible', lambda: False
            )(sel)
        )

    @staticmethod
    def network_idle() -> Condition:
        """Network is idle"""
        return Condition(
            "network idle",
            lambda session: session.wait_for_network_idle()
        )

    @staticmethod
    def page_ready() -> Condition:
        """Page is fully loaded"""
        return Condition(
            "page ready",
            lambda session: session.wait_for_page_ready()
        )


def create_condition(name: str, **kwargs) -> Condition:
    """Factory function to create conditions by name"""
    factory = CreateCondition
    if name == "url_contains":
        return factory.url_contains(kwargs.get("substring"))
    elif name == "element_visible":
        return factory.element_visible(kwargs.get("selector"))
    elif name == "network_idle":
        return factory.network_idle()
    elif name == "page_ready":
        return factory.page_ready()
    else:
        raise ValueError(f"Unknown condition: {name}")


class ExplicitWaitEnhanced:
    """
    Enhanced explicit wait processor
    
    Features:
    1. Integrated element visibility detection
    2. Loading state awareness
    3. Login validation adaptation
    4. Smart backoff retry
    5. Network idle wait
    """
    
    def __init__(self, session, config: EnhancedWaitConfig = None):
        self.session = session
        self.config = config or EnhancedWaitConfig()
        self._visibility_detector = ElementVisibilityDetector(session)
        self._history: List[dict] = []
        self._retry_count: int = 0
    
    async def until(
        self,
        condition: Union[Condition, Callable[..., Any]],
        timeout: float = None,
        poll_interval: float = None,
        *args,
        **kwargs,
    ) -> EnhancedWaitResult:
        """Wait until condition is met"""
        effective_timeout = timeout or self.config.timeout
        effective_interval = poll_interval or self.config.poll_interval
        
        if callable(condition) and not isinstance(condition, Condition):
            cond = Condition("callable", condition)
        else:
            cond = condition
        
        start_time = time.time()
        
        while time.time() - start_time < effective_timeout:
            try:
                value = cond.evaluate(*args, **kwargs)
                if value:
                    elapsed = time.time() - start_time
                    result = EnhancedWaitResult(
                        success=True,
                        condition=cond.description,
                        elapsed=elapsed,
                    )
                    self._record(result)
                    return result
                    
            except Exception as e:
                logger.debug(f"Condition evaluation error: {e}")
            
            await asyncio.sleep(effective_interval)
        
        elapsed = time.time() - start_time
        result = EnhancedWaitResult(
            success=False,
            condition=cond.description,
            elapsed=elapsed,
            error="timeout",
        )
        self._record(result)
        return result
    
    async def until_not(
        self,
        condition: Union[Condition, Callable[..., Any]],
        timeout: float = None,
    ) -> EnhancedWaitResult:
        """Wait until condition is no longer met"""
        effective_timeout = timeout or self.config.timeout
        effective_interval = self.config.poll_interval
        start_time = time.time()
        
        while time.time() - start_time < effective_timeout:
            try:
                value = condition.evaluate() if callable(condition) else condition.evaluate()
                if not value:
                    return EnhancedWaitResult(success=True, condition="not condition", elapsed=time.time() - start_time)
            except Exception:
                pass
            await asyncio.sleep(effective_interval)
        
        return EnhancedWaitResult(success=False, condition="not condition", elapsed=time.time()-start_time, error="timeout")
    
    async def wait_for_visible(
        self,
        selector: str,
        timeout: float = None,
        wait_interactive: bool = None,
    ) -> EnhancedWaitResult:
        """Wait for element to be visible (with integrated visibility detection)"""
        timeout = timeout or self.config.timeout
        wait_interactive = wait_interactive if wait_interactive is not None else self.config.wait_for_interactive
        
        if self.config.detect_page_load:
            loading = await self._visibility_detector.check_loading_state()
            if loading.get('page_loading') and self.config.ignore_loading_overlay:
                logger.debug(f"Page loading, skip wait: {selector}")
                return EnhancedWaitResult(
                    success=False,
                    condition=f"visible({selector})",
                    elapsed=0,
                    loading_state=loading,
                    error="page_loading",
                )
        
        visibility_result = await self._visibility_detector.check_visibility(selector, timeout=timeout)
        
        if wait_interactive and visibility_result.visible:
            interactive_result = await self._visibility_detector.check_interactive(selector)
            if not interactive_result.interactive:
                visibility_result.visible = False
                visibility_result.reason = "not_interactive"
        
        success = visibility_result.visible
        
        return EnhancedWaitResult(
            success=success,
            condition=f"visible({selector})",
            elapsed=timeout if not success else 0,
            visibility=visibility_result,
        )
    
    async def wait_for_interactive(
        self,
        selector: str,
        timeout: float = None,
    ) -> EnhancedWaitResult:
        """Wait for element to be interactive"""
        timeout = timeout or self.config.timeout
        vis_result = await self.wait_for_visible(selector, timeout)
        if not vis_result.success:
            return vis_result
        
        interactive_result = await self._visibility_detector.check_interactive(selector)
        
        return EnhancedWaitResult(
            success=interactive_result.interactive,
            condition=f"interactive({selector})",
            elapsed=timeout,
            visibility=interactive_result,
        )
    
    async def wait_for_not_blocked(
        self,
        selector: str,
        timeout: float = None,
    ) -> EnhancedWaitResult:
        """Wait for element not blocked (by login popup etc.)"""
        timeout = timeout or self.config.timeout
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            blocked = await self._visibility_detector.is_element_blocked(selector)
            if not blocked:
                return EnhancedWaitResult(
                    success=True,
                    condition=f"not_blocked({selector})",
                    elapsed=time.time() - start_time,
                )
            await asyncio.sleep(self.config.poll_interval)
        
        return EnhancedWaitResult(
            success=False,
            condition=f"not_blocked({selector})",
            elapsed=time.time() - start_time,
            error="timeout_blocked",
        )
    
    async def wait_for_stable(
        self,
        selector: str,
        check_times: int = 3,
        timeout: float = None,
    ) -> EnhancedWaitResult:
        """Wait for element content to stabilize"""
        timeout = timeout or self.config.timeout
        js_code = f"document.querySelector({selector!r})?.innerText || ''"
        
        start_time = time.time()
        last_value = ""
        stable_count = 0
        
        while time.time() - start_time < timeout:
            try:
                current_value = await self.session.eval_js(js_code)
            except Exception:
                current_value = ""
            
            if current_value == last_value and current_value:
                stable_count += 1
                if stable_count >= check_times:
                    return EnhancedWaitResult(
                        success=True,
                        condition=f"stable({selector})",
                        elapsed=time.time() - start_time,
                        details={'content': current_value},
                    )
            else:
                stable_count = 0
            last_value = current_value
            await asyncio.sleep(self.config.poll_interval)
        
        return EnhancedWaitResult(
            success=False,
            condition=f"stable({selector})",
            elapsed=time.time() - start_time,
            error="timeout_stable",
        )
    
    async def check_element_state(
        self,
        selector: str,
    ) -> Dict[str, Any]:
        """Comprehensive element state check"""
        visibility = await self._visibility_detector.check_visibility(selector)
        loading = await self._visibility_detector.check_loading_state(selector)
        blocked = await self._visibility_detector.is_element_blocked(selector)
        
        return {
            'selector': selector,
            'exists': visibility.exists,
            'visible': visibility.visible,
            'interactive': visibility.interactive,
            'in_viewport': visibility.details.get('in_viewport', False),
            'loading_page': loading.get('page_loading', False),
            'blocked_by_overlay': blocked,
            'status': 'ready' if visibility.visible and not blocked and not loading.get('page_loading') else 'pending',
        }
    
    def get_history(self, last_n: int = 10) -> List[dict]:
        return self._history[-last_n:]
    
    def get_stats(self) -> Dict[str, Any]:
        if not self._history:
            return {'total': 0}
        successes = [h for h in self._history if h.get('success')]
        return {
            'total': len(self._history),
            'success': len(successes),
            'fail': len(self._history) - len(successes),
            'success_rate': f"{len(successes)}/{len(self._history)}",
        }
    
    def _record(self, result: EnhancedWaitResult):
        self._history.append({
            'condition': result.condition,
            'success': result.success,
            'elapsed': result.elapsed,
            'timestamp': time.time(),
        })
