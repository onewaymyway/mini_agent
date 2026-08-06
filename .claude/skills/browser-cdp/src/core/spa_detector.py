"""
spa_detector.py - SPA 框架检测器

检测 React、Vue、Angular 等 SPA 框架，并等待路由变化完成
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

logger = logging.getLogger(__name__)


class SPAFramework(Enum):
    """SPA 框架类型"""
    REACT = "react"
    VUE = "vue"
    ANGULAR = "angular"
    SVELTE = "svelte"
    NEXTJS = "nextjs"
    NUXT = "nuxt"
    REMIX = "remix"
    SVELTEKIT = "sveltekit"
    SOLID = "solid"
    QWIK = "qwik"
    ASTRO = "astro"
    HYDRATED = "hydrated"  # Stencil/Hydrated components
    UNKNOWN = "unknown"


@dataclass
class SPAInfo:
    """SPA 框架信息"""
    framework: SPAFramework
    version: Optional[str] = None
    router_version: Optional[str] = None
    is_spa: bool = True


class SPADetector:
    """
    SPA 框架检测器
    
    检测页面使用的 SPA 框架，并提供路由变化等待功能
    """
    
    # 框架检测指标
    FRAMEWORK_INDICATORS = {
        SPAFramework.REACT: {
            "js": [
                "__REACT_ROOT__",
                "__REACT_DEVTOOLS_GLOBAL_HOOK__",
                "ReactDOM.createRoot",
                "ReactDOM.render",
                "react-dom",
            ],
            "css": ["react-app"],
            "data_attr": ["data-reactroot", "data-react-checksum"],
        },
        SPAFramework.VUE: {
            "js": [
                "__VUE__",
                "Vue.createApp",
                "Vue.component",
                "vue-router",
            ],
            "css": ["vue-app"],
            "data_attr": ["data-v-app"],
        },
        SPAFramework.ANGULAR: {
            "js": [
                "ngApplication",
                "angular.element",
                "angular.module",
                "ng-version",
            ],
            "css": ["ng-app"],
            "data_attr": ["ng-version"],
        },
        SPAFramework.SVELTE: {
            "js": [
                "svelte",
                "createRoot",
            ],
            "css": ["svelte"],
            "data_attr": [],
        },
        SPAFramework.NEXTJS: {
            "js": [
                "__NEXT_DATA__",
                "next",
                "__NEXT_VERSION__",
            ],
            "css": [],
            "data_attr": ["data-next-font-preload"],
        },
        SPAFramework.NUXT: {
            "js": [
                "__NUXT__",
                "nuxt",
                "__NUXT_PRELOAD__",
            ],
            "css": [],
            "data_attr": [],
        },
        SPAFramework.REMIX: {
            "js": [
                "__remixRouteModules",
                "__remixRoot",
                "remix",
            ],
            "css": [],
            "data_attr": ["data-remix-navigate"],
        },
        SPAFramework.SVELTEKIT: {
            "js": [
                "__sveltekit",
                "sveltekit",
                "__sveltekit_env",
            ],
            "css": [],
            "data_attr": [],
        },
        SPAFramework.SOLID: {
            "js": [
                "__SOLID_CONTEXT__",
                "solid-js",
                "createSignal",
                "createEffect",
            ],
            "css": [],
            "data_attr": [],
        },
        SPAFramework.QWIK: {
            "js": [
                "__qwikRoots",
                "qwik",
                "__qmanifest__",
            ],
            "css": [],
            "data_attr": ["q-component"],
        },
        SPAFramework.ASTRO: {
            "js": [
                "__astro",
                "astro",
            ],
            "css": [],
            "data_attr": ["data-astro"],
        },
        SPAFramework.HYDRATED: {
            "js": [
                "HybridRoot",
                "Stencil",
                "customElements",
            ],
            "css": [],
            "data_attr": ["hybrid-root"],
        },
    }
    
    # 路由变化检测器
    ROUTE_DETECTORS = {
        SPAFramework.REACT: {
            "check_js": "window.__reactRouterVersion || window.__remixRouteModules || document.querySelector('[data-react-router]')",
            "check_url": True,
        },
        SPAFramework.VUE: {
            "check_js": "window.__vue_router_version || document.querySelector('[data-v-route]')",
            "check_url": True,
        },
        SPAFramework.ANGULAR: {
            "check_js": "window.angular && window.angular.element(document).injector().get('$route')",
            "check_url": True,
        },
        SPAFramework.NEXTJS: {
            "check_js": "window.__NEXT_DATA__ || document.querySelector('[data-next-route]')",
            "check_url": True,
        },
        SPAFramework.NUXT: {
            "check_js": "window.__NUXT__ || document.querySelector('[data-nuxt-route]')",
            "check_url": True,
        },
        SPAFramework.REMIX: {
            "check_js": "window.__remixRouteModules || document.querySelector('[data-remix-route]')",
            "check_url": True,
        },
        SPAFramework.SVELTEKIT: {
            "check_js": "window.__sveltekit || document.querySelector('[data-sveltekit-route]')",
            "check_url": True,
        },
        SPAFramework.SOLID: {
            "check_js": "window.__SOLID_CONTEXT__ || typeof createSignal !== 'undefined'",
            "check_url": True,
        },
        SPAFramework.QWIK: {
            "check_js": "window.__qwikRoots || document.querySelector('[q-component]')",
            "check_url": True,
        },
        SPAFramework.ASTRO: {
            "check_js": "window.__astro || document.querySelector('[data-astro]')",
            "check_url": True,
        },
        SPAFramework.HYDRATED: {
            "check_js": "window.HybridRoot || document.querySelector('[hybrid-root]')",
            "check_url": True,
        },
    }
    
    def __init__(self, session):
        """
        Args:
            session: CDP session 对象
        """
        self.session = session
        self._detected_framework: Optional[SPAFramework] = None
        self._spa_info: Optional[SPAInfo] = None
    
    async def detect(self) -> SPAInfo:
        """
        检测 SPA 框架
        
        Returns:
            SPAInfo: 框架信息
        """
        if self._spa_info:
            return self._spa_info
        
        # 1. 检测框架类型
        framework = await self._detect_framework()
        
        # 2. 获取版本信息
        version = await self._get_version(framework)
        router_version = await self._get_router_version(framework)
        
        self._spa_info = SPAInfo(
            framework=framework,
            version=version,
            router_version=router_version,
            is_spa=framework != SPAFramework.UNKNOWN
        )
        
        logger.info(f"检测到 SPA 框架: {framework.value}, 版本: {version}, 路由版本: {router_version}")
        return self._spa_info
    
    async def _detect_framework(self) -> SPAFramework:
        """检测 SPA 框架类型"""
        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            # 检查 JS 变量
            for js_indicator in indicators["js"]:
                try:
                    result = await self.session.eval_js(f"() => typeof {js_indicator}")
                    if result != "undefined":
                        logger.debug(f"检测到 {framework.value} 指标: {js_indicator}")
                        return framework
                except Exception:
                    pass
            
            # 检查 CSS 类
            for css_indicator in indicators["css"]:
                try:
                    elements = await self.session.query_selector_all(f".{css_indicator}")
                    if elements:
                        logger.debug(f"检测到 {framework.value} CSS: {css_indicator}")
                        return framework
                except Exception:
                    pass
            
            # 检查 data 属性
            for data_attr in indicators["data_attr"]:
                try:
                    elements = await self.session.query_selector_all(f"[{data_attr}]")
                    if elements:
                        logger.debug(f"检测到 {framework.value} 属性: {data_attr}")
                        return framework
                except Exception:
                    pass
        
        return SPAFramework.UNKNOWN
    
    async def _get_version(self, framework: SPAFramework) -> Optional[str]:
        """获取框架版本"""
        version_scripts = {
            SPAFramework.REACT: "React.version || 'unknown'",
            SPAFramework.VUE: "Vue.version || 'unknown'",
            SPAFramework.ANGULAR: "angular.version.full || 'unknown'",
            SPAFramework.SVELTE: "svelte.version || 'unknown'",
            SPAFramework.NEXTJS: "typeof process !== 'undefined' && process.env.NEXT_VERSION || 'unknown'",
            SPAFramework.NUXT: "window.__NUXT__?.nuxtVersion || 'unknown'",
            SPAFramework.REMIX: "window.__remixVersion || 'unknown'",
            SPAFramework.SVELTEKIT: "window.__sveltekit?.version || 'unknown'",
        }
        
        script = version_scripts.get(framework)
        if not script:
            return None
        
        try:
            version = await self.session.eval_js(f"() => {script}")
            return version if version != "unknown" else None
        except Exception:
            return None
    
    async def _get_router_version(self, framework: SPAFramework) -> Optional[str]:
        """获取路由版本"""
        router_scripts = {
            SPAFramework.REACT: "window.__reactRouterVersion || 'unknown'",
            SPAFramework.VUE: "window.__vue_router_version || 'unknown'",
            SPAFramework.ANGULAR: "angular.version.full || 'unknown'",
            SPAFramework.NEXTJS: "window.__NEXT_DATA__?.buildId || 'unknown'",
            SPAFramework.NUXT: "window.__NUXT__?.router?.base || 'unknown'",
            SPAFramework.REMIX: "window.__remixRouteModules ? 'remix' : 'unknown'",
            SPAFramework.SVELTEKIT: "window.__sveltekit?.version || 'unknown'",
        }
        
        script = router_scripts.get(framework)
        if not script:
            return None
        
        try:
            version = await self.session.eval_js(f"() => {script}")
            return version if version != "unknown" else None
        except Exception:
            return None
    
    async def wait_for_route_change(self, timeout: int = 30) -> bool:
        """
        等待 SPA 路由变化完成
        
        Args:
            timeout: 超时时间（秒）
        
        Returns:
            bool: 是否成功等待
        """
        if not self._spa_info or not self._spa_info.is_spa:
            return True
        
        framework = self._spa_info.framework
        detector = self.ROUTE_DETECTORS.get(framework)
        
        if not detector:
            return True
        
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            # 检查路由是否稳定
            if await self._is_route_stable():
                return True
            
            await asyncio.sleep(0.1)
        
        logger.warning(f"路由等待超时: {timeout}s")
        return False
    
    async def _is_route_stable(self) -> bool:
        """检查路由是否稳定"""
        try:
            # 检查是否有正在进行的导航
            navigating = await self.session.eval_js("() => document.readyState === 'complete'")
            
            # 检查是否有 loading 状态
            loading = await self.session.query_selector(".loading, .skeleton, [class*='loading']")
            
            # 检查是否有活跃的 AJAX/Fetch 请求
            active_requests = await self.session.eval_js("() => window.__activeRequests || 0")
            
            # 检查是否有未完成的 transition
            has_transition = await self.session.eval_js("() => document.startViewTransition ? document.startViewTransition({}) : false")
            
            return navigating and not loading and active_requests == 0
        except Exception:
            return True
    
    async def wait_for_element(self, selector: str, timeout: int = 30) -> bool:
        """
        等待 SPA 元素出现
        
        Args:
            selector: CSS 选择器
            timeout: 超时时间
        
        Returns:
            bool: 是否找到元素
        """
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                elements = await self.session.query_selector_all(selector)
                if elements:
                    return True
            except Exception:
                pass
            
            await asyncio.sleep(0.1)
        
        return False


# 便捷函数
async def detect_spa(session) -> SPAInfo:
    """
    检测 SPA 框架的便捷函数
    
    Args:
        session: CDP session 对象
    
    Returns:
        SPAInfo: 框架信息
    """
    detector = SPADetector(session)
    return await detector.detect()


async def wait_for_spa_route(session, timeout: int = 30) -> bool:
    """
    等待 SPA 路由变化的便捷函数
    
    Args:
        session: CDP session 对象
        timeout: 超时时间
    
    Returns:
        bool: 是否成功等待
    """
    detector = SPADetector(session)
    await detector.detect()  # 先检测框架
    return await detector.wait_for_route_change(timeout)
