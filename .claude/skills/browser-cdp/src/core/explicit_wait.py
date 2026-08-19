"""
explicit_wait.py - 显式等待模块

提供结构化显式等待能力，替代隐式等待（WebDriver.implicitly_wait）：
- 基于条件的等待（until/until_not）
- 可配置的超时和轮询间隔
- 支持复合条件（AND/OR）
- 等待结果可追踪（历史、统计）

设计原则：
- 不依赖 WebDriverWait 类，完全基于 asyncio/Playwright
- 与 SmartWait/SmartWaitV2 互补，提供更细粒度的条件控制
- 返回 WaitResult，兼容现有接口
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Any, Union

logger = logging.getLogger(__name__)


@dataclass
class ExplicitWaitConfig:
    """显式等待配置"""
    timeout: float = 30.0          # 总超时（秒）
    poll_interval: float = 0.3     # 轮询间隔（秒）
    ignore_exceptions: tuple = (Exception,)  # 等待期间忽略的异常类型
    retry_on_failure: bool = True  # 失败后是否自动重试一次
    max_retries: int = 1           # 最大重试次数


@dataclass
class WaitResult:
    """等待结果"""
    success: bool
    condition: str
    elapsed: float
    value: Any = None
    error: Optional[str] = None
    details: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        status = "✓" if self.success else "✗"
        return f"WaitResult({status} {self.condition} {self.elapsed:.2f}s)"


class Condition:
    """条件基类，支持复合条件"""
    
    def __init__(self, description: str, check: Callable[..., Any]):
        self.description = description
        self._check = check
    
    def evaluate(self, *args, **kwargs) -> Any:
        """评估条件，返回 True/False 或具体值"""
        try:
            result = self._check(*args, **kwargs)
            if isinstance(result, bool):
                return result
            return bool(result)
        except Exception as e:
            logger.debug(f"条件 {self.description} 评估出错: {e}")
            return False
    
    def __and__(self, other: "Condition") -> "Condition":
        """AND 组合"""
        return Condition(
            f"({self.description}) AND ({other.description})",
            lambda *a, **kw: self.evaluate(*a, **kw) and other.evaluate(*a, **kw)
        )
    
    def __or__(self, other: "Condition") -> "Condition":
        """OR 组合"""
        return Condition(
            f"({self.description}) OR ({other.description})",
            lambda *a, **kw: self.evaluate(*a, **kw) or other.evaluate(*a, **kw)
        )
    
    def __invert__(self) -> "Condition":
        """NOT 取反"""
        return Condition(
            f"NOT ({self.description})",
            lambda *a, **kw: not self.evaluate(*a, **kw)
        )
    
    def __repr__(self) -> str:
        return f"Condition({self.description})"


class ExplicitWait:
    """
    显式等待器
    
    用法示例：
        wait = ExplicitWait(session)
        
        # 等待元素可见
        result = await wait.until_visible("#submit-btn", timeout=10)
        
        # 等待自定义条件
        result = await wait.until(
            Condition("count > 5", lambda: len(items) > 5),
            timeout=15
        )
        
        # 复合条件
        cond = Condition("has_text", lambda: el.text == "OK") & \n               Condition("is_visible", lambda: el.is_visible)
        result = await wait.until(cond, timeout=10)
    """
    
    def __init__(self, session, config: ExplicitWaitConfig = None):
        self.session = session
        self.config = config or ExplicitWaitConfig()
        self._history: List[dict] = []
    
    # =========================================================================
    # 核心等待方法
    # =========================================================================
    
    async def until(
        self,
        condition: Union[Condition, Callable[..., Any]],
        timeout: float = None,
        poll_interval: float = None,
        *args,
        **kwargs,
    ) -> WaitResult:
        """
        等待条件满足
        
        Args:
            condition: Condition 对象或Callable，返回 True 表示满足
            timeout: 超时时间（秒），覆盖配置
            poll_interval: 轮询间隔（秒），覆盖配置
            *args, **kwargs: 传给 condition 的参数
        
        Returns:
            WaitResult
        """
        effective_timeout = timeout or self.config.timeout
        effective_interval = poll_interval or self.config.poll_interval
        
        # 转换为 Condition 对象
        if callable(condition) and not isinstance(condition, Condition):
            cond = Condition("callable", condition)
        else:
            cond = condition
        
        start_time = time.time()
        last_value = None
        exceptions: List[Exception] = []
        
        while time.time() - start_time < effective_timeout:
            try:
                value = cond.evaluate(*args, **kwargs)
                last_value = value
                
                if value:
                    elapsed = time.time() - start_time
                    result = WaitResult(
                        success=True,
                        condition=cond.description,
                        elapsed=elapsed,
                        value=last_value,
                    )
                    self._record(result)
                    logger.info(f"显式等待成功: {cond.description} ({elapsed:.2f}s)")
                    return result
            except self.config.ignore_exceptions as e:
                exceptions.append(e)
            
            await asyncio.sleep(effective_interval)
        
        # 超时
        elapsed = time.time() - start_time
        result = WaitResult(
            success=False,
            condition=cond.description,
            elapsed=elapsed,
            value=last_value,
            error="timeout",
        )
        self._record(result)
        logger.warning(f"显式等待超时: {cond.description} ({elapsed:.2f}s)")
        return result
    
    async def until_not(
        self,
        condition: Union[Condition, Callable[..., Any]],
        timeout: float = None,
        poll_interval: float = None,
        *args,
        **kwargs,
    ) -> WaitResult:
        """
        等待条件不再满足（取反版本）
        
        例如：等待弹窗消失、等待加载中状态结束
        """
        effective_timeout = timeout or self.config.timeout
        effective_interval = poll_interval or self.config.poll_interval
        
        if callable(condition) and not isinstance(condition, Condition):
            cond = Condition(f"not({condition.__name__})", condition)
        else:
            cond = Condition(f"not({condition.description})", lambda *a, **kw: not condition.evaluate(*a, **kw))
        
        start_time = time.time()
        last_value = None
        
        while time.time() - start_time < effective_timeout:
            try:
                value = cond.evaluate(*args, **kwargs)
                last_value = value
                
                if value:
                    elapsed = time.time() - start_time
                    result = WaitResult(
                        success=True,
                        condition=cond.description,
                        elapsed=elapsed,
                        value=last_value,
                    )
                    self._record(result)
                    return result
            except self.config.ignore_exceptions:
                pass
            
            await asyncio.sleep(effective_interval)
        
        elapsed = time.time() - start_time
        result = WaitResult(
            success=False,
            condition=cond.description,
            elapsed=elapsed,
            value=last_value,
            error="timeout",
        )
        self._record(result)
        return result
    
    # =========================================================================
    # 便捷方法
    # =========================================================================
    
    async def until_visible(
        self,
        selector: str,
        timeout: float = None,
        shadow: bool = False,
    ) -> WaitResult:
        """等待元素可见"""
        js_code = f"""
        (function() {{
            var el = null;
            var shadowContainer = {shadow!r};
            try {{
                el = shadowContainer 
                    ? document.querySelector('{selector}')?.shadowRoot?.querySelector('{selector}')
                    : document.querySelector('{selector}');
            }} catch(e) {{ el = null; }}
            if (!el) return {{ visible: false, exists: false }};
            var rect = el.getBoundingClientRect();
            return {{
                visible: rect.width > 0 && rect.height > 0 && 
                         el.offsetParent !== null &&
                         getComputedStyle(el).visibility !== 'hidden',
                exists: true,
                rect: {{ x: Math.round(rect.x), y: Math.round(rect.y), 
                        w: Math.round(rect.width), h: Math.round(rect.height) }}
            }};
        }})()
        """
        
        async def _check() -> dict:
            try:
                return await self.session.eval_js(js_code)
            except Exception:
                return {"visible": False}
        
        cond = Condition(f"visible({selector})", lambda: _check().get("visible", False))
        result = await self.until(cond, timeout=timeout)
        if result.success:
            result.details["rect"] = _check().get("rect")
        return result
    
    async def until_present(
        self,
        selector: str,
        timeout: float = None,
    ) -> WaitResult:
        """等待元素存在于 DOM（不一定可见）"""
        js_code = f"document.querySelector({selector!r}) !== null"
        
        async def _check() -> bool:
            try:
                return await self.session.eval_js(js_code)
            except Exception:
                return False
        
        cond = Condition(f"present({selector})", _check)
        return await self.until(cond, timeout=timeout)
    
    async def until_text(
        self,
        selector: str,
        text: str,
        timeout: float = None,
        exact: bool = False,
    ) -> WaitResult:
        """等待元素包含指定文本"""
        js_code = f"""
        (function() {{
            var el = document.querySelector({selector!r});
            if (!el) return {{ found: false }};
            var content = el.innerText || el.textContent || '';
            var match = {exact!r} ? content === {text!r} : content.indexOf({text!r}) !== -1;
            return {{ found: true, match: match, text: content.substring(0, 100) }};
        }})()
        """
        
        async def _check() -> dict:
            try:
                return await self.session.eval_js(js_code)
            except Exception:
                return {"found": False}
        
        cond = Condition(f"text({selector}, {text!r})", lambda: _check().get("match", False))
        result = await self.until(cond, timeout=timeout)
        if result.success:
            info = await _check()
            result.details["actual_text"] = info.get("text")
        return result
    
    async def until_count(
        self,
        selector: str,
        expected_count: int,
        timeout: float = None,
        operator: str = ">=",
    ) -> WaitResult:
        """等待匹配选择器的元素数量满足条件"""
        js_code = f"document.querySelectorAll({selector!r}).length"
        
        def _check() -> bool:
            import asyncio
            count = asyncio.get_event_loop().run_until_complete(self.session.eval_js(js_code)) if not asyncio.get_event_loop().is_running() else None
            # Fallback: use eval_js directly in async context
            return False
        
        # Use async version
        async def _async_check() -> bool:
            try:
                count = await self.session.eval_js(js_code)
                if operator == ">=":
                    return count >= expected_count
                elif operator == "==":
                    return count == expected_count
                elif operator == ">":
                    return count > expected_count
                elif operator == "<=":
                    return count <= expected_count
            except Exception:
                return False
        
        cond = Condition(f"count({selector}, {operator}{expected_count})", _async_check)
        result = await self.until(cond, timeout=timeout)
        if result.success:
            count = await self.session.eval_js(js_code)
            result.details["actual_count"] = count
        return result
    
    async def until_url_matches(
        self,
        pattern: str,
        timeout: float = None,
    ) -> WaitResult:
        """等待 URL 匹配正则模式"""
        import re
        compiled = re.compile(pattern)
        
        async def _check() -> bool:
            try:
                url = await self.session.eval_js("window.location.href")
                return bool(compiled.search(url))
            except Exception:
                return False
        
        cond = Condition(f"url_matches({pattern})", _check)
        result = await self.until(cond, timeout=timeout)
        if result.success:
            result.details["current_url"] = await self.session.eval_js("window.location.href")
        return result
    
    async def until_stable(
        self,
        selector: str,
        check_times: int = 3,
        timeout: float = None,
    ) -> WaitResult:
        """等待元素内容稳定（连续多次读取不变）"""
        js_code = f"document.querySelector({selector!r})?.innerText || ''"
        
        start_time = time.time()
        effective_timeout = timeout or self.config.timeout
        last_value = ""
        stable_count = 0
        
        while time.time() - start_time < effective_timeout:
            try:
                current_value = await self.session.eval_js(js_code)
            except Exception:
                current_value = ""
            
            if current_value == last_value and current_value:
                stable_count += 1
                if stable_count >= check_times:
                    elapsed = time.time() - start_time
                    result = WaitResult(
                        success=True,
                        condition=f"stable({selector})",
                        elapsed=elapsed,
                        value=current_value,
                    )
                    self._record(result)
                    return result
            else:
                stable_count = 0
            last_value = current_value
            await asyncio.sleep(self.config.poll_interval)
        
        elapsed = time.time() - start_time
        result = WaitResult(
            success=False,
            condition=f"stable({selector})",
            elapsed=elapsed,
            value=last_value,
            error="timeout",
        )
        self._record(result)
        return result
    
    # =========================================================================
    # 历史记录
    # =========================================================================
    
    def _record(self, result: WaitResult):
        """记录等待结果"""
        self._history.append({
            "condition": result.condition,
            "success": result.success,
            "elapsed": result.elapsed,
            "timestamp": time.time(),
        })
    
    def get_history(self, last_n: int = 10) -> List[dict]:
        """获取等待历史记录"""
        return self._history[-last_n:]
    
    def get_stats(self) -> dict:
        """获取等待统计信息"""
        if not self._history:
            return {"total": 0, "success": 0, "fail": 0, "avg_elapsed": 0}
        successes = [h for h in self._history if h["success"]]
        fails = [h for h in self._history if not h["success"]]
        return {
            "total": len(self._history),
            "success": len(successes),
            "fail": len(fails),
            "success_rate": f"{len(successes)}/{len(self._history)}",
            "avg_elapsed": sum(h["elapsed"] for h in self._history) / len(self._history),
            "max_elapsed": max(h["elapsed"] for h in self._history),
            "min_elapsed": min(h["elapsed"] for h in self._history),
        }


# =========================================================================
# 模块级便捷函数
# =========================================================================

async def wait_for_selector(
    session,
    selector: str,
    timeout: float = None,
    **kwargs,
) -> WaitResult:
    """等待元素匹配 CSS 选择器"""
    wait = ExplicitWait(session)
    return await wait.until_selector(selector, timeout=timeout, **kwargs)


async def wait_for_visible(
    session,
    selector: str,
    timeout: float = None,
    **kwargs,
) -> WaitResult:
    """等待元素可见"""
    wait = ExplicitWait(session)
    return await wait.until_visible(selector, timeout=timeout, **kwargs)


async def wait_for_text(
    session,
    text: str,
    timeout: float = None,
    **kwargs,
) -> WaitResult:
    """等待页面出现指定文本"""
    wait = ExplicitWait(session)
    return await wait.until_text(text, timeout=timeout, **kwargs)


async def wait_for_network_idle(
    session,
    idle_seconds: float = 0.5,
    timeout: float = 30.0,
    **kwargs,
) -> NetworkIdleConfig:
    """
    便捷函数：等待网络空闲。
    返回 NetworkIdleConfig（含检测结果）供调用方使用。
    """
    from .network_idle_detector import NetworkIdleConfig, NetworkIdleDetector
    config = NetworkIdleConfig(idle_seconds=idle_seconds, timeout=timeout)
    detector = NetworkIdleDetector(session, config)
    result = await detector.wait_for_idle()
    return result