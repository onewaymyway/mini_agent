
"""
dynamic_page_support.py - 动态页面统一支持模块

整合 SPA 路由监听、滚动加载检测、Cookie/Session 管理、反爬策略处理。

核心功能：
1. SPA 路由监听与等待（React Router / Vue Router / Angular Router）
2. 滚动加载智能检测（无限滚动 / 懒加载 / 虚拟列表）
3. Cookie / Session 持久化管理（跨会话保留登录态）
4. 常见反爬策略处理（检测 + 自动应对）

用法示例：
    from src.core.dynamic_page_support import DynamicPageSupport
    dps = DynamicPageSupport(session)

    # SPA 导航
    await dps.wait_for_spa_route("#results", timeout=10)

    # 滚动加载
    items = await dps.scroll_to_load(max_pages=5, selector=".item")

    # Cookie 管理
    await dps.cookie_mgr.save_cookies(session, "example.com")
    await dps.cookie_mgr.restore_cookies(session, "example.com")

    # 反爬检测
    detection = await dps.detect_anti_bot()
    if detection["risk"] == "high":
        await dps.apply_stealth()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class RouteChangeType(Enum):
    """路由变化类型"""
    FULL_NAVIGATION = "full_navigation"
    SPA_HISTORY = "spa_history"
    SPA_HASH = "spa_hash"
    AJAX_CONTENT = "ajax_content"
    UNKNOWN = "unknown"


class AntiBotRisk(Enum):
    """反爬风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


@dataclass
class RouteChangeEvent:
    """路由变化事件"""
    change_type: RouteChangeType
    old_url: str = ""
    new_url: str = ""
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change_type": self.change_type.value,
            "old_url": self.old_url,
            "new_url": self.new_url,
            "timestamp": self.timestamp,
            "details": self.details,
        }


@dataclass
class AntiBotDetection:
    """反爬检测结果"""
    risk: AntiBotRisk
    detected_features: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk": self.risk.value,
            "detected_features": self.detected_features,
            "recommendations": self.recommendations,
            "raw_data": self.raw_data,
        }


@dataclass
class DynamicPageResult:
    """动态页面操作结果（兼容 browser_interactions 导入）"""
    success: bool = False
    url: str = ""
    wait_time: float = 0.0
    route_changes: int = 0
    items_loaded: int = 0
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "url": self.url,
            "wait_time": round(self.wait_time, 2),
            "route_changes": self.route_changes,
            "items_loaded": self.items_loaded,
            "error": self.error,
            "data": self.data,
        }


class SPARouteListener:
    """
    SPA 路由监听器
    监听并检测 SPA 框架的路由变化：
    - React Router (v5/v6)
    - Vue Router
    - Angular Router
    - Next.js / Nuxt.js
    - URL hash 变化
    - history API 调用
    """

    ROUTER_STATE_SCRIPTS = {
        "react_router": """
        (() => {
            if (window.__reactRouterVersion) return { type: "react_router", version: window.__reactRouterVersion, pathname: window.location.pathname };
            const root = document.querySelector('[data-reactroot]');
            if (root) return { type: "react_dom", hasRoot: true, pathname: window.location.pathname };
            return null;
        })()
        """,
        "vue_router": """
        (() => {
            if (window.__VUE_ROUTER__) {
                const current = window.__VUE_ROUTER__.currentRoute;
                return { type: "vue_router", version: window.__VUE_ROUTER__._version, path: current?.value?.path || current?.path };
            }
            const app = document.querySelector('[data-v-app]');
            if (app) return { type: "vue_app", hasApp: true, pathname: window.location.pathname };
            return null;
        })()
        """,
        "angular_router": """
        (() => {
            if (window.angular) {
                try {
                    const inj = angular.element(document).injector();
                    if (inj) {
                        const route = inj.get('$route');
                        if (route) return { type: "angular_router", current: route.current?.path };
                    }
                } catch(e) {}
            }
            const el = document.querySelector('[ng-version]');
            if (el) return { type: "angular", hasNgVersion: true, pathname: window.location.pathname };
            return null;
        })()
        """,
        "nextjs": """
        (() => {
            if (window.__NEXT_DATA__) {
                return { type: "nextjs", pathname: window.__NEXT_DATA__.props?.pageProps?.pathname };
            }
            return null;
        })()
        """,
        "nuxt": """
        (() => {
            if (window.__NUXT__) {
                return { type: "nuxt", path: window.__NUXT__.state?.path };
            }
            return null;
        })()
        """,
    }

    def __init__(self, session: Any):
        self.session = session
        self._last_route_state: str = ""
        self._route_history: List[RouteChangeEvent] = []
        self._max_history = 50
        self._history_hook_installed = False

    async def start_listening(self) -> None:
        """启动路由监听"""
        self._route_history.clear()
        self._last_route_state = await self._get_current_route_state()
        await self._inject_history_hook()
        logger.debug("SPARouteListener: 开始监听路由变化")

    async def stop_listening(self) -> None:
        """停止路由监听"""
        logger.debug("SPARouteListener: 停止监听")

    async def wait_for_route_change(
        self,
        timeout: float = 10.0,
        url_contains: Optional[str] = None,
        check_elements: Optional[List[str]] = None,
    ) -> List[RouteChangeEvent]:
        """
        等待路由变化
        Args:
            timeout: 超时时间（秒）
            url_contains: 新 URL 需包含的字符串
            check_elements: 等待出现的元素选择器列表
        Returns:
            路由变化事件列表
        """
        initial_count = len(self._route_history)
        start_time = time.time()
        last_state = self._last_route_state

        while time.time() - start_time < timeout:
            current_url = await self._get_current_url()
            if url_contains and url_contains in current_url:
                event = self._record_route_change(RouteChangeType.FULL_NAVIGATION, current_url)
                return [event]

            current_state = await self._get_current_route_state()
            if current_state != last_state:
                event = self._record_route_change(RouteChangeType.SPA_HISTORY, current_url)
                last_state = current_state
                if check_elements:
                    for selector in check_elements:
                        exists = await self._element_exists(selector)
                        if exists:
                            event.details["element_found"] = selector
                            return [event]
                return [event]

            if check_elements:
                for selector in check_elements:
                    exists = await self._element_exists(selector)
                    if exists:
                        event = RouteChangeEvent(
                            change_type=RouteChangeType.AJAX_CONTENT,
                            old_url=await self._get_current_url(),
                            new_url=await self._get_current_url(),
                            details={"element": selector},
                        )
                        self._route_history.append(event)
                        return [event]

            history_event = await self._check_history_event()
            if history_event:
                self._last_route_state = await self._get_current_route_state()
                return [history_event]

            await asyncio.sleep(0.1)

        logger.warning(f"SPARouteListener: 路由等待超时 ({timeout}s)")
        return self._route_history[initial_count:]

    async def _inject_history_hook(self) -> None:
        """注入 history API 监听"""
        if self._history_hook_installed:
            return
        hook_js = """
        (() => {
            if (window.__browser_cdp_history_hook__) return;
            window.__browser_cdp_history_hook__ = true;
            const origPush = history.pushState.bind(history);
            const origReplace = history.replaceState.bind(history);
            history.pushState = function(...args) {
                origPush(...args);
                window.__browser_cdp_history_event__ = { type: "pushState", url: location.href, ts: Date.now() };
            };
            history.replaceState = function(...args) {
                origReplace(...args);
                window.__browser_cdp_history_event__ = { type: "replaceState", url: location.href, ts: Date.now() };
            };
        })()
        """
        try:
            await self.session.eval_js(hook_js)
            self._history_hook_installed = True
        except Exception as e:
            logger.warning(f"SPARouteListener: 注入 history 钩子失败: {e}")

    async def _check_history_event(self) -> Optional[RouteChangeEvent]:
        """检查是否有 history API 事件"""
        try:
            result = await self.session.eval_js("window.__browser_cdp_history_event__ || null")
            if result:
                event = RouteChangeEvent(
                    change_type=RouteChangeType.SPA_HISTORY,
                    old_url=self._last_route_state,
                    new_url=result.get("url", ""),
                    details={"type": result.get("type")},
                )
                self._route_history.append(event)
                await self.session.eval_js("delete window.__browser_cdp_history_event__")
                return event
        except Exception as e:
            logger.debug(f"SPARouteListener: 检查 history 事件失败: {e}")
        return None

    async def _get_current_url(self) -> str:
        try:
            return await self.session.eval_js("window.location.href")
        except Exception:
            return ""

    async def _get_current_route_state(self) -> str:
        """获取当前路由状态（用于检测变化）"""
        states = []
        for name, script in self.ROUTER_STATE_SCRIPTS.items():
            try:
                result = await self.session.eval_js(script)
                if result:
                    states.append(f"{name}:{json.dumps(result, sort_keys=True)}")
            except Exception:
                pass
        url = await self._get_current_url()
        states_str = "|".join(states)
        return f"url:{url}|{states_str}"

    async def _element_exists(self, selector: str) -> bool:
        try:
            result = await self.session.eval_js(f"document.querySelector('{selector}') !== null")
            return bool(result)
        except Exception:
            return False

    def _record_route_change(self, change_type: RouteChangeType, new_url: str) -> RouteChangeEvent:
        old_url = self._last_route_state
        event = RouteChangeEvent(
            change_type=change_type,
            old_url=old_url,
            new_url=new_url,
        )
        self._route_history.append(event)
        if len(self._route_history) > self._max_history:
            self._route_history = self._route_history[-self._max_history:]
        return event

    def get_history(self) -> List[RouteChangeEvent]:
        return list(self._route_history)



class ScrollLoadDetector:
    """
    滚动加载检测器
    检测页面滚动时的动态内容加载：
    - 无限滚动列表
    - 懒加载内容
    - 虚拟列表
    - 分页加载
    """

    def __init__(self, session: Any):
        self.session = session
        self._scroll_callbacks: List[Callable] = []
        self._load_count = 0
        self._content_hashes: List[str] = []

    def on_content_loaded(self, callback: Callable[[int, int], None]) -> None:
        """注册内容加载回调 (page_num, total_items)"""
        self._scroll_callbacks.append(callback)

    async def detect_scroll_load(
        self,
        max_pages: int = 10,
        scroll_amount: int = 800,
        stability_threshold: int = 50,
        item_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        检测并处理滚动加载
        Returns: {pages_loaded, items_added, final_height, stabilized_at_page}
        """
        result = {
            "pages_loaded": 0,
            "items_added": 0,
            "final_height": 0,
            "stabilized_at_page": 0,
        }
        prev_height = 0
        prev_item_count = 0

        for page in range(1, max_pages + 1):
            current_height = await self._get_page_height()
            current_items = await self._count_items(item_selector) if item_selector else 0
            height_diff = abs(current_height - prev_height)
            item_diff = current_items - prev_item_count

            if height_diff < stability_threshold and page > 1:
                result["stabilized_at_page"] = page
                logger.info(f"滚动加载已稳定，第 {page} 页后高度无显著变化")
                break

            await self._scroll_by(scroll_amount)
            await asyncio.sleep(0.3)

            result["pages_loaded"] = page
            result["items_added"] += item_diff
            result["final_height"] = current_height
            prev_height = current_height
            prev_item_count = current_items

            for cb in self._scroll_callbacks:
                try:
                    cb(page, current_items)
                except Exception as e:
                    logger.warning(f"ScrollLoadDetector: 回调失败: {e}")

            logger.debug(f"第 {page} 页：高度={current_height}, 元素数={current_items}")

        return result

    async def wait_for_lazy_content(
        self,
        selector: str = None,
        timeout: float = 10.0,
    ) -> bool:
        """
        等待懒加载内容完成
        Args:
            selector: 懒加载元素选择器
            timeout: 超时时间
        Returns:
            bool: 是否全部加载完成
        """
        selector = selector or "img[loading='lazy'], [data-src], [data-lazy]"
        deadline = time.time() + timeout

        while time.time() < deadline:
            pending = await self._count_pending_lazy(selector)
            if pending == 0:
                return True
            await asyncio.sleep(0.3)
        return False

    async def collect_virtual_list(
        self,
        container_selector: str,
        item_selector: str,
        max_items: int = 100,
    ) -> List[str]:
        """
        收集虚拟列表中的所有项
        Returns:
            所有项的文本列表
        """
        items = []
        seen = set()

        for _ in range(max_items):
            visible = await self._get_visible_items(container_selector, item_selector)
            new_items = [t for t in visible if t not in seen]
            items.extend(new_items)
            seen.update(visible)

            if len(new_items) == 0:
                break

            await self._scroll_virtual_item(container_selector)
            await asyncio.sleep(0.2)

        return items

    async def _get_page_height(self) -> int:
        return await self.session.eval_js("document.documentElement.scrollHeight")

    async def _scroll_by(self, amount: int) -> None:
        await self.session.eval_js(f"window.scrollBy(0, {amount})")

    async def _count_items(self, selector: str) -> int:
        try:
            return await self.session.eval_js(f"document.querySelectorAll('{selector}').length")
        except Exception:
            return 0

    async def _count_pending_lazy(self, selector: str) -> int:
        js = f"""
        (() => {{
            const els = document.querySelectorAll('{selector}');
            let pending = 0;
            els.forEach(el => {{
                const src = el.dataset.src || el.dataset.lazy || el.getAttribute('data-src');
                if (src && !el.complete) pending++;
            }});
            return pending;
        }})()
        """
        return await self.session.eval_js(js) or 0

    async def _get_visible_items(self, container_sel: str, item_sel: str) -> List[str]:
        js = f"""
        (() => {{
            const container = document.querySelector('{container_sel}');
            if (!container) return [];
            return Array.from(container.querySelectorAll('{item_sel}')).map(el => (el.innerText || '').trim()).filter(t => t);
        }})()
        """
        return await self.session.eval_js(js) or []

    async def _scroll_virtual_item(self, container_sel: str) -> None:
        js = f"""
        (() => {{
            const container = document.querySelector('{container_sel}');
            if (!container) return;
            const items = container.querySelectorAll('[class*="item"], [role="option"]');
            if (items.length > 0) items[0].scrollIntoView({{ block: "center" }});
        }})()
        """
        await self.session.eval_js(js)


class SessionManager:
    """
    Session 管理器
    管理浏览器 Session 状态：
    - Cookie 持久化与恢复
    - LocalStorage / SessionStorage 备份
    - 多 Session 切换
    """

    def __init__(self, storage_dir: str = "./data/sessions"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: Dict[str, Dict[str, Any]] = {}

    async def save_session(
        self,
        session: Any,
        session_id: str,
        domain: str,
    ) -> int:
        """
        保存当前浏览器 Session
        Returns:
            保存的 Cookie 数量
        """
        cookies = await self._get_all_cookies(session)
        ls = await self._get_local_storage(session, domain)
        ss = await self._get_session_storage(session, domain)

        data = {
            "session_id": session_id,
            "domain": domain,
            "saved_at": time.time(),
            "cookies": cookies,
            "localStorage": ls,
            "sessionStorage": ss,
        }

        safe_name = domain.replace(":", "_").replace("/", "_")
        file_path = self.storage_dir / f"{session_id}_{safe_name}.json"
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(f"SessionManager: 保存 Session [{session_id}] [{domain}]，{len(cookies)} 条 Cookie")
        return len(cookies)

    async def restore_session(
        self,
        session: Any,
        session_id: str,
        domain: str,
    ) -> bool:
        """
        恢复浏览器 Session
        Returns:
            是否恢复成功
        """
        safe_name = domain.replace(":", "_").replace("/", "_")
        file_path = self.storage_dir / f"{session_id}_{safe_name}.json"

        if not file_path.exists():
            logger.warning(f"SessionManager: Session 文件不存在 [{session_id}] [{domain}]")
            return False

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if data.get("cookies"):
                await self._set_cookies(session, data["cookies"], domain)
            if data.get("localStorage"):
                await self._set_local_storage(session, data["localStorage"], domain)
            if data.get("sessionStorage"):
                await self._set_session_storage(session, data["sessionStorage"], domain)

            logger.info(f"SessionManager: 恢复 Session [{session_id}] [{domain}] 成功")
            return True
        except Exception as e:
            logger.error(f"SessionManager: 恢复 Session 失败 [{session_id}] [{domain}]: {e}")
            return False

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有已保存的 Session"""
        sessions = []
        for f in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append({
                    "file": f.name,
                    "session_id": data.get("session_id"),
                    "domain": data.get("domain"),
                    "saved_at": data.get("saved_at"),
                    "cookie_count": len(data.get("cookies", [])),
                })
            except Exception as e:
                logger.warning(f"SessionManager: 读取 Session 文件失败 {f.name}: {e}")
        return sessions

    async def delete_session(self, session_id: str, domain: str) -> bool:
        """删除指定 Session"""
        safe_name = domain.replace(":", "_").replace("/", "_")
        file_path = self.storage_dir / f"{session_id}_{safe_name}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    # ---- 内部方法 ----

    async def _get_all_cookies(self, session: Any) -> List[Dict]:
        try:
            result = await session.eval_js("document.cookie")
            cookies = []
            if result:
                for c in result.split("; "):
                    if "=" in c:
                        name, _, value = c.partition("=")
                        cookies.append({"name": name.strip(), "value": value.strip(), "domain": ""})
            return cookies
        except Exception:
            return []

    async def _set_cookies(self, session: Any, cookies: List[Dict], domain: str) -> None:
        try:
            cdp_cookies = [{"name": c["name"], "value": c["value"], "domain": domain, "path": "/"} for c in cookies]
            await session.send("Network.setCookies", {"cookies": cdp_cookies})
        except Exception as e:
            logger.warning(f"SessionManager: 设置 Cookie 失败: {e}")

    async def _get_local_storage(self, session: Any, domain: str) -> Dict[str, str]:
        try:
            return await session.eval_js("(() => { const d = {}; for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);d[k]=localStorage.getItem(k);} return d; })()")
        except Exception:
            return {}

    async def _set_local_storage(self, session: Any, data: Dict, domain: str) -> None:
        try:
            entries = json.dumps(data)
            await session.eval_js(f"(() => {{ const d = {entries}; for(const k in d) localStorage.setItem(k, d[k]); }})()")
        except Exception as e:
            logger.warning(f"SessionManager: 恢复 localStorage 失败: {e}")

    async def _get_session_storage(self, session: Any, domain: str) -> Dict[str, str]:
        try:
            return await session.eval_js("(() => { const d = {}; for(let i=0;i<sessionStorage.length;i++){const k=sessionStorage.key(i);d[k]=sessionStorage.getItem(k);} return d; })()")
        except Exception:
            return {}

    async def _set_session_storage(self, session: Any, data: Dict, domain: str) -> None:
        try:
            entries = json.dumps(data)
            await session.eval_js(f"(() => {{ const d = {entries}; for(const k in d) sessionStorage.setItem(k, d[k]); }})()")
        except Exception as e:
            logger.warning(f"SessionManager: 恢复 sessionStorage 失败: {e}")


class AntiBotHandler:
    """
    反爬策略处理器
    检测常见反爬机制并自动应对：
    - webdriver 检测
    - iframe 嵌套检测
    - Canvas 指纹
    - 请求频率异常
    - 验证码检测
    """

    DETECTION_SCRIPTS = {
        "webdriver": "navigator.webdriver",
        "iframe_nested": "window !== window.top",
        "chrome_runtime": "!!window.chrome",
        "permissions_api": "!!navigator.permissions",
        "plugins_count": "navigator.plugins.length",
        "hardware_concurrency": "navigator.hardwareConcurrency",
        "device_memory": "navigator.deviceMemory",
    }

    CAPTCHA_KEYWORDS = [
        "captcha", "verify", "slide", "click", "checkbox",
        "recaptcha", "hcaptcha", "geetest", "turnstile",
        "验证", "滑块", "点选", "验证码",
        "请完成验证", "拖动滑块", "点击", "人机验证",
    ]

    STEALTH_JS = """
    (() => {
        // 移除 webdriver 标记
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        // 模拟真实 Chrome
        if (!window.chrome) {
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
        }
        // 模拟插件
        if (navigator.plugins.length === 0) {
            const fakePlugins = Object.freeze([{0: {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format'}, filename: 'internal-pdf-viewer', description: 'PDF.plugin', name: 'Chrome PDF Plugin'}]);
            Object.defineProperty(navigator, 'plugins', { get: () => fakePlugins });
        }
        // 模拟语言
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        // 注入完成标记
        window.__browser_cdp_stealth_applied__ = true;
    })()
    """

    def __init__(self, session: Any):
        self.session = session
        self._request_count = 0
        self._request_timestamps: List[float] = []
        self._stealth_applied = False

    async def track_request(self) -> None:
        """追踪请求频率"""
        self._request_count += 1
        self._request_timestamps.append(time.time())
        # 清理超过 60 秒的记录
        self._request_timestamps = [t for t in self._request_timestamps if time.time() - t < 60]

    async def detect_anti_bot(self) -> AntiBotDetection:
        """
        检测反爬措施
        Returns:
            AntiBotDetection: 检测结果
        """
        detected = []
        recommendations = []
        risk_score = 0
        raw_data = {}

        # 检测 webdriver
        try:
            webdriver = await self.session.eval_js(self.DETECTION_SCRIPTS["webdriver"])
            raw_data["webdriver"] = webdriver
            if webdriver:
                detected.append("webdriver=true 被检测到")
                risk_score += 3
                recommendations.append("启用 stealth 模式移除 webdriver 标记")
        except Exception:
            pass

        # 检测 iframe 嵌套
        try:
            is_iframe = await self.session.eval_js(self.DETECTION_SCRIPTS["iframe_nested"])
            raw_data["iframe_nested"] = is_iframe
            if is_iframe:
                detected.append("页面在 iframe 中运行")
                risk_score += 1
                recommendations.append("尝试在主 frame 中操作")
        except Exception:
            pass

        # 检测请求频率
        if len(self._request_timestamps) >= 10:
            recent = self._request_timestamps[-10:]
            interval = recent[-1] - recent[0] if len(recent) > 1 else 0
            if interval < 2.0:
                detected.append("请求频率过高")
                risk_score += 2
                recommendations.append("降低请求频率，增加随机延迟")

        # 检测验证码页面
        page_text = await self._get_page_text_snippet()
        for keyword in self.CAPTCHA_KEYWORDS:
            if keyword in page_text.lower():
                detected.append(f"页面包含验证码关键词: {keyword}")
                risk_score += 2
                recommendations.append(f"检测到可能含验证码，建议人工处理或更换策略")
                break

        # 判断风险等级
        if risk_score >= 5:
            risk = AntiBotRisk.BLOCKED
        elif risk_score >= 3:
            risk = AntiBotRisk.HIGH
        elif risk_score >= 1:
            risk = AntiBotRisk.MEDIUM
        else:
            risk = AntiBotRisk.LOW

        return AntiBotDetection(
            risk=risk,
            detected_features=detected,
            recommendations=recommendations,
            raw_data=raw_data,
        )

    async def apply_stealth(self) -> bool:
        """
        应用反检测措施
        Returns:
            是否成功应用
        """
        try:
            await self.session.eval_js(self.STEALTH_JS)
            self._stealth_applied = True
            logger.info("AntiBotHandler: stealth 模式已应用")
            return True
        except Exception as e:
            logger.error(f"AntiBotHandler: stealth 应用失败: {e}")
            return False

    async def is_stealth_applied(self) -> bool:
        """检查 stealth 是否已应用"""
        return self._stealth_applied

    async def add_random_delay(self, min_sec: float = 0.5, max_sec: float = 2.0) -> float:
        """
        添加随机延迟，模拟人类行为
        Returns:
            实际延迟时间
        """
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)
        return delay

    async def _get_page_text_snippet(self, max_chars: int = 2000) -> str:
        """获取页面文本片段用于关键词检测"""
        try:
            js = f"""
            (() => {{
                const body = document.body ? document.body.innerText : '';
                return body.slice(0, {max_chars}).toLowerCase();
            }})()
            """
            return await self.session.eval_js(js) or ""
        except Exception:
            return ""



class DynamicPageSupport:
    """
    动态页面统一支持类

    整合 SPA 路由监听、滚动加载检测、Cookie/Session 管理、反爬策略处理。

    用法示例：
        from src.core.dynamic_page_support import DynamicPageSupport

        dps = DynamicPageSupport(session, session_id="my_task")

        # 导航并等待 SPA 路由变化
        await dps.navigate_and_wait("https://example.com/results", check_elements=[".item"])

        # 滚动加载内容
        result = await dps.scroll_to_load(max_pages=5, item_selector=".product-item")
        print(f"加载了 {result[\"pages_loaded\"]} 页，新增 {result[\"items_added\"]} 项")

        # 保存/恢复 Session
        await dps.save_session("example.com")
        await dps.restore_session("example.com")

        # 反爬检测与应对
        detection = await dps.detect_anti_bot()
        if detection["risk"] == "high":
            await dps.apply_stealth()
            await dps.add_random_delay()
    """

    def __init__(
        self,
        session: Any,
        session_id: str = "default",
        storage_dir: str = "./data/sessions",
        auto_stealth: bool = False,
        auto_retry_on_captcha: bool = True,
    ):
        self.session = session
        self.session_id = session_id
        self.auto_stealth = auto_stealth
        self.auto_retry_on_captcha = auto_retry_on_captcha

        # 子模块
        self.route_listener = SPARouteListener(session)
        self.scroll_detector = ScrollLoadDetector(session)
        self.cookie_mgr = SessionManager(storage_dir=storage_dir)
        self.antibot_handler = AntiBotHandler(session)

    # =========================================================================
    # 导航与等待
    # =========================================================================

    async def navigate_and_wait(
        self,
        url: str,
        wait_for: Optional[str] = None,
        check_elements: Optional[List[str]] = None,
        timeout: float = 15.0,
        apply_stealth_first: bool = False,
    ) -> Dict[str, Any]:
        """
        导航到 URL 并等待页面稳定

        Args:
            url: 目标 URL
            wait_for: 等待的 URL 包含字符串
            check_elements: 等待出现的元素选择器列表
            timeout: 超时时间
            apply_stealth_first: 是否先应用 stealth

        Returns:
            {success, url, wait_time, route_changes}
        """
        start_time = time.time()
        result = {
            "success": False,
            "url": "",
            "wait_time": 0.0,
            "route_changes": [],
            "elements_found": [],
        }

        try:
            # 可选：先应用 stealth
            if apply_stealth_first:
                await self.antibot_handler.apply_stealth()
                await self.antibot_handler.add_random_delay(0.5, 1.5)

            # 导航
            await self.session.goto(url)
            result["url"] = await self.session.eval_js("window.location.href")

            # 启动路由监听
            await self.route_listener.start_listening()
            try:
                # 等待路由变化
                route_changes = await self.route_listener.wait_for_route_change(
                    timeout=timeout,
                    url_contains=wait_for,
                    check_elements=check_elements,
                )
                result["route_changes"] = [e.to_dict() for e in route_changes]

                # 检查等待的元素
                if check_elements:
                    for selector in check_elements:
                        exists = await self._element_exists(selector)
                        if exists:
                            result["elements_found"].append(selector)

                result["success"] = True
            finally:
                await self.route_listener.stop_listening()

        except Exception as e:
            logger.error(f"DynamicPageSupport: navigate_and_wait 失败: {e}")
            result["error"] = str(e)

        result["wait_time"] = time.time() - start_time
        return result

    # =========================================================================
    # 滚动加载
    # =========================================================================

    async def scroll_to_load(
        self,
        max_pages: int = 10,
        scroll_amount: int = 800,
        item_selector: Optional[str] = None,
        stability_threshold: int = 50,
    ) -> Dict[str, Any]:
        """
        滚动加载内容

        Returns:
            {pages_loaded, items_added, final_height, stabilized_at_page}
        """
        return await self.scroll_detector.detect_scroll_load(
            max_pages=max_pages,
            scroll_amount=scroll_amount,
            stability_threshold=stability_threshold,
            item_selector=item_selector,
        )

    async def wait_for_lazy_content(
        self,
        selector: str = None,
        timeout: float = 10.0,
    ) -> bool:
        """
        等待懒加载内容完成
        """
        return await self.scroll_detector.wait_for_lazy_content(
            selector=selector,
            timeout=timeout,
        )

    async def collect_virtual_list(
        self,
        container_selector: str,
        item_selector: str,
        max_items: int = 100,
    ) -> List[str]:
        """
        收集虚拟列表中的所有项
        """
        return await self.scroll_detector.collect_virtual_list(
            container_selector=container_selector,
            item_selector=item_selector,
            max_items=max_items,
        )

    # =========================================================================
    # Session 管理
    # =========================================================================

    async def save_session(self, domain: str) -> int:
        """
        保存当前 Session
        """
        return await self.cookie_mgr.save_session(
            session=self.session,
            session_id=self.session_id,
            domain=domain,
        )

    async def restore_session(self, domain: str) -> bool:
        """
        恢复 Session
        """
        return await self.cookie_mgr.restore_session(
            session=self.session,
            session_id=self.session_id,
            domain=domain,
        )

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有已保存的 Session"""
        return await self.cookie_mgr.list_sessions()

    async def delete_session(self, domain: str) -> bool:
        """删除指定 Session"""
        return await self.cookie_mgr.delete_session(
            session_id=self.session_id,
            domain=domain,
        )

    # =========================================================================
    # 反爬处理
    # =========================================================================

    async def detect_anti_bot(self) -> AntiBotDetection:
        """
        检测反爬措施
        """
        await self.antibot_handler.track_request()
        return await self.antibot_handler.detect_anti_bot()

    async def apply_stealth(self) -> bool:
        """
        应用反检测措施
        """
        return await self.antibot_handler.apply_stealth()

    async def add_random_delay(self, min_sec: float = 0.5, max_sec: float = 2.0) -> float:
        """
        添加随机延迟
        """
        return await self.antibot_handler.add_random_delay(min_sec, max_sec)

    # =========================================================================
    # 组合操作
    # =========================================================================

    async def scrape_sp_a_page(
        self,
        url: str,
        item_selector: str,
        max_pages: int = 5,
        timeout: float = 15.0,
    ) -> List[Dict]:
        """
        抓取 SPA 页面（导航 + 等待 + 滚动加载）

        Args:
            url: 目标 URL
            item_selector: 列表项选择器
            max_pages: 最大滚动页数
            timeout: 导航等待超时

        Returns:
            抓取到的项列表
        """
        items = []

        # 1. 导航并等待
        nav_result = await self.navigate_and_wait(
            url=url,
            check_elements=[item_selector],
            timeout=timeout,
        )

        if not nav_result["success"]:
            logger.error(f"scrape_sp_a_page: 导航失败 [{url}]")
            return items

        # 2. 滚动加载
        scroll_result = await self.scroll_to_load(
            max_pages=max_pages,
            item_selector=item_selector,
        )

        # 3. 提取内容
        js = f"""
        (() => {{
            return Array.from(document.querySelectorAll('{item_selector}')).map(el => ({{
                text: (el.innerText || '').trim().slice(0, 500),
                tag: el.tagName.toLowerCase(),
            }}));
        }})()
        """
        items = await self.session.eval_js(js) or []

        pages = scroll_result.get("pages_loaded", 0)
        logger.info(f"scrape_sp_a_page: 从 [{url}] 抓取 {len(items)} 条，滚动 {pages} 页")
        return items

    async def scrape_with_session(
        self,
        url: str,
        domain: str,
        item_selector: str,
        save_after: bool = True,
        restore_before: bool = True,
    ) -> List[Dict]:
        """
        带 Session 管理的抓取（自动恢复登录态）

        Args:
            url: 目标 URL
            domain: 域名
            item_selector: 列表项选择器
            save_after: 抓取后是否保存 Session
            restore_before: 抓取前是否恢复 Session

        Returns:
            抓取到的项列表
        """
        # 恢复 Session
        if restore_before:
            restored = await self.restore_session(domain)
            if restored:
                logger.info(f"scrape_with_session: 已恢复 Session [{domain}]")

        # 抓取
        items = await self.scrape_sp_a_page(url, item_selector)

        # 保存 Session
        if save_after and items:
            await self.save_session(domain)

        return items

    # =========================================================================
    # 辅助方法
    # =========================================================================

    async def _element_exists(self, selector: str) -> bool:
        try:
            result = await self.session.eval_js(f"document.querySelector('{selector}') !== null")
            return bool(result)
        except Exception:
            return False

    async def get_current_url(self) -> str:
        try:
            return await self.session.eval_js("window.location.href")
        except Exception:
            return ""

    async def get_page_title(self) -> str:
        try:
            return await self.session.eval_js("document.title")
        except Exception:
            return ""


# =====================================================================
# 便捷函数
# =====================================================================

async def wait_for_spa_route(
    session: Any,
    url_contains: Optional[str] = None,
    check_elements: Optional[List[str]] = None,
    timeout: float = 10.0,
) -> List[Dict]:
    """
    便捷函数：等待 SPA 路由变化
    """
    listener = SPARouteListener(session)
    await listener.start_listening()
    try:
        events = await listener.wait_for_route_change(
            timeout=timeout,
            url_contains=url_contains,
            check_elements=check_elements,
        )
        return [e.to_dict() for e in events]
    finally:
        await listener.stop_listening()


async def scroll_to_load_content(
    session: Any,
    max_pages: int = 10,
    item_selector: Optional[str] = None,
) -> Dict:
    """
    便捷函数：滚动加载内容
    """
    detector = ScrollLoadDetector(session)
    return await detector.detect_scroll_load(max_pages=max_pages, item_selector=item_selector)


async def save_browser_session(
    session: Any,
    session_id: str,
    domain: str,
    storage_dir: str = "./data/sessions",
) -> int:
    """
    便捷函数：保存浏览器 Session
    """
    mgr = SessionManager(storage_dir=storage_dir)
    return await mgr.save_session(session, session_id, domain)


async def restore_browser_session(
    session: Any,
    session_id: str,
    domain: str,
    storage_dir: str = "./data/sessions",
) -> bool:
    """
    便捷函数：恢复浏览器 Session
    """
    mgr = SessionManager(storage_dir=storage_dir)
    return await mgr.restore_session(session, session_id, domain)


async def detect_anti_bot_measures(
    session: Any,
) -> Dict:
    """
    便捷函数：检测反爬措施
    """
    handler = AntiBotHandler(session)
    detection = await handler.detect_anti_bot()
    return detection.to_dict()


async def apply_browser_stealth(
    session: Any,
) -> bool:
    """
    便捷函数：应用反检测措施
    """
    handler = AntiBotHandler(session)
    return await handler.apply_stealth()
