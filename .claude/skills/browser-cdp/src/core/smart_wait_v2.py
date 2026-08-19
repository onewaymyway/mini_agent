"""
smart_wait_v2.py - SmartWait v2 增强等待策略

新增策略：
- request: 等待特定 URL 模式的网络请求完成
- virtual_list: 等待虚拟列表加载到指定数量项
- lazy_image_v2: 多重检测懒加载图片完成
- route_change: 拦截 History API 检测 SPA 路由变化
- iframe_ready: 等待 iframe 内容可访问
- form_submitted: 等待表单提交后页面稳定
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional, List, Dict, Any, Callable, Union

logger = logging.getLogger(__name__)


class SmartWaitV2:
    """
    SmartWait v2 — 在 SmartWait 基础上新增的高级等待策略
    
    设计原则：
    - 不破坏现有 SmartWait 接口
    - 所有新方法返回 WaitResult（兼容）
    - 复用 SmartWait 的 session 和 config
    """
    
    def __init__(self, smart_wait):
        """
        Args:
            smart_wait: SmartWait 实例（获取 session 和 config）
        """
        self.session = smart_wait.session
        self.config = smart_wait.config
        self._request_listeners: Dict[str, dict] = {}
    
    def get_effective_timeout(self) -> float:
        """根据页面复杂度计算有效超时时间"""
        if not self.config.adaptive_timeout:
            return self.config.timeout
        multipliers = {"low": 0.8, "medium": 1.0, "high": 1.5, "auto": 1.0}
        return self.config.timeout * multipliers.get(self.config.page_complexity, 1.0)
    
    # =========================================================================
    # 1. wait_for_request — 等待特定 URL 模式的网络请求完成
    # =========================================================================
    
    async def wait_for_request(
        self,
        url_pattern: str,
        method: str = None,
        timeout: float = None,
    ) -> dict:
        """
        等待特定 URL 模式的网络请求完成
        
        使用 CDP Network.requestWillBeSent + Network.responseReceived 事件监听
        
        Args:
            url_pattern: URL 正则模式
            method: HTTP 方法（GET/POST，可选）
            timeout: 超时时间
        
        Returns:
            dict: {success, strategy, elapsed, details}
        """
        effective_timeout = timeout or self.get_effective_timeout()
        pattern = re.compile(url_pattern, re.IGNORECASE)
        matched_requests = []
        event_set = asyncio.Event()
        start_time = time.time()
        
        def on_request_will_be_sent(params: dict) -> None:
            request = params.get('request', {})
            url = request.get('url', '')
            req_method = request.get('method', 'GET')
            
            if pattern.search(url):
                if method and req_method.upper() != method.upper():
                    return
                matched_requests.append({
                    'url': url,
                    'method': req_method,
                    'timestamp': time.time(),
                    'request_id': params.get('requestId'),
                })
        
        def on_response_received(params: dict) -> None:
            if matched_requests and params.get('requestId') == matched_requests[-1].get('request_id'):
                event_set.set()
        
        # 注册 CDP 事件监听
        self.session.subscribe('Network.requestWillBeSent', on_request_will_be_sent)
        self.session.subscribe('Network.responseReceived', on_response_received)
        try:
            self.session.send('Network.enable')
        except Exception:
            pass  # Network domain 可能已启用
        
        try:
            await asyncio.wait_for(event_set.wait(), timeout=effective_timeout)
            elapsed = time.time() - start_time
            logger.info(f"wait_for_request 完成: {matched_requests[-1]['url']} ({elapsed:.2f}s)")
            return {
                'success': True,
                'strategy': 'request',
                'elapsed': elapsed,
                'details': {'matched_requests': matched_requests},
            }
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.warning(f"wait_for_request 超时: pattern={url_pattern} ({elapsed:.2f}s)")
            return {
                'success': False,
                'strategy': 'request',
                'elapsed': elapsed,
                'details': {'error': 'timeout', 'pattern': url_pattern},
            }
        finally:
            self.session.unsubscribe('Network.requestWillBeSent', on_request_will_be_sent)
            self.session.unsubscribe('Network.responseReceived', on_response_received)
            try:
                self.session.send('Network.disable')
            except Exception:
                pass
    
    # =========================================================================
    # 2. wait_for_virtual_list — 等待虚拟列表加载
    # =========================================================================
    
    async def wait_for_virtual_list(
        self,
        item_selector: str,
        min_items: int = 20,
        timeout: float = None,
    ) -> dict:
        """
        等待虚拟列表加载到指定数量项
        
        通过 data-row-index / data-list-index 等数据属性判断
        是否已滚动到新内容（而非高度变化）
        
        Args:
            item_selector: 列表项选择器
            min_items: 最小项数
            timeout: 超时时间
        
        Returns:
            dict: {success, strategy, elapsed, details}
        """
        effective_timeout = timeout or self.get_effective_timeout()
        
        check_js = f"""
        (function() {{
            var items = document.querySelectorAll({item_selector!r});
            if (items.length === 0) return {{ count: 0, hasIndex: false, lastIndex: 0 }};
            
            var hasIndex = false;
            var lastIndex = 0;
            var lastItems = Array.from(items).slice(-5);
            lastItems.forEach(function(el) {{
                var idx = el.getAttribute('data-row-index') || 
                          el.getAttribute('data-list-index') ||
                          el.getAttribute('data-item-key');
                if (idx) {{
                    hasIndex = true;
                    lastIndex = Math.max(lastIndex, parseInt(idx));
                }}
            }});
            
            return {{
                count: items.length,
                hasIndex: hasIndex,
                lastIndex: lastIndex,
                totalHeight: document.documentElement.scrollHeight
            }};
        }})()
        """
        
        start_time = time.time()
        last_count = 0
        last_height = 0
        no_change_count = 0
        
        while time.time() - start_time < effective_timeout:
            try:
                result = await self.session.eval_js(check_js)
            except Exception as e:
                logger.debug(f"virtual_list 检测出错: {e}")
                await asyncio.sleep(0.3)
                continue
            
            count = result.get('count', 0)
            height = result.get('totalHeight', 0)
            
            if count >= min_items:
                if count == last_count and height == last_height:
                    no_change_count += 1
                    if no_change_count >= 3:
                        elapsed = time.time() - start_time
                        logger.info(f"virtual_list 完成: {count} 项, lastIndex={result.get('lastIndex', 0)} ({elapsed:.2f}s)")
                        return {
                            'success': True,
                            'strategy': 'virtual_list',
                            'elapsed': elapsed,
                            'details': {
                                'items_count': count,
                                'last_index': result.get('lastIndex', 0),
                                'has_data_index': result.get('hasIndex', False),
                            },
                        }
                else:
                    no_change_count = 0
            
            last_count = count
            last_height = height
            await asyncio.sleep(0.3)
        
        elapsed = time.time() - start_time
        logger.warning(f"virtual_list 超时: 最终 {last_count} 项 (需要 {min_items})")
        return {
            'success': False,
            'strategy': 'virtual_list',
            'elapsed': elapsed,
            'details': {'error': 'timeout', 'final_count': last_count, 'required': min_items},
        }
    
    # =========================================================================
    # 3. wait_for_images_complete_v2 — 多重检测懒加载图片
    # =========================================================================
    
    async def wait_for_images_complete(
        self,
        selector: str = "img[data-src]",
        timeout: float = None,
    ) -> dict:
        """
        等待懒加载图片完全加载（v2: 多重检测）
        
        检测策略:
        1. data-src 已转移到 src
        2. img.naturalWidth > 0 (图片已解码)
        3. 无 .blur/.placeholder class
        4. CSS background-image 已设置（针对背景图懒加载）
        
        Args:
            selector: 图片选择器
            timeout: 超时时间
        
        Returns:
            dict: {success, strategy, elapsed, details}
        """
        effective_timeout = timeout or self.get_effective_timeout()
        
        check_js = f"""
        (function() {{
            var imgs = document.querySelectorAll({selector!r});
            if (imgs.length === 0) return {{ total: 0, loaded: 0, loading: 0 }};
            
            var total = imgs.length;
            var loaded = 0;
            var loading = 0;
            
            imgs.forEach(function(img) {{
                var hasSrc = img.src && img.src.length > 0 && img.src !== 'about:blank';
                var isDecoded = img.naturalWidth > 0 && img.naturalHeight > 0;
                var noPlaceholder = img.className.indexOf('blur') === -1 && 
                                    img.className.indexOf('placeholder') === -1 &&
                                    img.className.indexOf('lazy') === -1;
                
                if (hasSrc && isDecoded && noPlaceholder) {{
                    loaded++;
                }} else {{
                    loading++;
                }}
            }});
            
            return {{ total: total, loaded: loaded, loading: loading }};
        }})()
        """
        
        start_time = time.time()
        
        while time.time() - start_time < effective_timeout:
            try:
                result = await self.session.eval_js(check_js)
            except Exception:
                await asyncio.sleep(0.5)
                continue
            
            total = result.get('total', 0)
            loaded = result.get('loaded', 0)
            loading = result.get('loading', 0)
            
            if total == 0:
                elapsed = time.time() - start_time
                return {
                    'success': True,
                    'strategy': 'lazy_image_v2',
                    'elapsed': elapsed,
                    'details': {'total': 0, 'note': 'no images found'},
                }
            
            if loading == 0:
                elapsed = time.time() - start_time
                logger.info(f"lazy_image_v2 完成: {loaded}/{total} 图片已加载 ({elapsed:.2f}s)")
                return {
                    'success': True,
                    'strategy': 'lazy_image_v2',
                    'elapsed': elapsed,
                    'details': {'total': total, 'loaded': loaded},
                }
            
            await asyncio.sleep(0.5)
        
        elapsed = time.time() - start_time
        return {
            'success': False,
            'strategy': 'lazy_image_v2',
            'elapsed': elapsed,
            'details': {'error': 'timeout', 'loaded': loaded, 'total': total},
        }
    
    # =========================================================================
    # 4. wait_for_route_change — SPA 路由变化监听
    # =========================================================================
    
    async def wait_for_route_change(
        self,
        expected_pattern: str = None,
        timeout: float = None,
    ) -> dict:
        """
        等待 SPA 路由变化稳定
        
        拦截 History API (pushState/replaceState) 事件
        
        Args:
            expected_pattern: 期望的 URL 模式（可选）
            timeout: 超时时间
        
        Returns:
            dict: {success, strategy, elapsed, details}
        """
        effective_timeout = timeout or self.get_effective_timeout()
        
        # 注入路由监听脚本
        inject_js = """
        (function() {
            if (window.__browserCdpRouteListener) return { injected: false, already: true };
            window.__browserCdpRouteChanges = [];
            var origPush = history.pushState.bind(history);
            var origReplace = history.replaceState.bind(history);
            history.pushState = function(state, title, url) {
                window.__browserCdpRouteChanges.push({
                    type: 'push',
                    url: url ? (url.startsWith('http') ? url : new URL(url, window.location).href) : window.location.href,
                    time: Date.now()
                });
                return origPush(state, title, url);
            };
            history.replaceState = function(state, title, url) {
                window.__browserCdpRouteChanges.push({
                    type: 'replace',
                    url: url ? (url.startsWith('http') ? url : new URL(url, window.location).href) : window.location.href,
                    time: Date.now()
                });
                return origReplace(state, title, url);
            };
            window.__browserCdpRouteListener = true;
            return { injected: true, currentUrl: window.location.href };
        })()
        """
        
        try:
            await self.session.eval_js(inject_js)
        except Exception as e:
            logger.debug(f"路由监听注入失败: {e}")
        
        start_time = time.time()
        last_change_count = 0
        stable_count = 0
        pattern = re.compile(expected_pattern, re.IGNORECASE) if expected_pattern else None
        
        while time.time() - start_time < effective_timeout:
            try:
                changes = await self.session.eval_js("window.__browserCdpRouteChanges || []")
                current_url = await self.session.eval_js("window.location.href")
            except Exception:
                await asyncio.sleep(0.3)
                continue
            
            current_count = len(changes) if isinstance(changes, list) else 0
            
            if current_count > last_change_count:
                last_change_count = current_count
                stable_count = 0
            else:
                stable_count += 1
                if stable_count >= 3:
                    # 路由稳定，检查是否符合预期
                    if pattern:
                        if pattern.search(current_url):
                            elapsed = time.time() - start_time
                            logger.info(f"route_change 完成: URL={current_url} ({elapsed:.2f}s)")
                            return {
                                'success': True,
                                'strategy': 'route_change',
                                'elapsed': elapsed,
                                'details': {'changes': current_count, 'url': current_url},
                            }
                        # URL 不符合预期，继续等待
                    else:
                        elapsed = time.time() - start_time
                        logger.info(f"route_change 完成: {current_count} 次变化, URL={current_url} ({elapsed:.2f}s)")
                        return {
                            'success': True,
                            'strategy': 'route_change',
                            'elapsed': elapsed,
                            'details': {'changes': current_count, 'url': current_url},
                        }
            
            await asyncio.sleep(0.3)
        
        elapsed = time.time() - start_time
        return {
            'success': False,
            'strategy': 'route_change',
            'elapsed': elapsed,
            'details': {'error': 'timeout', 'final_changes': last_change_count},
        }
    
    # =========================================================================
    # 5. wait_for_iframe_content — 等待 iframe 内容可访问
    # =========================================================================
    
    async def wait_for_iframe_content(
        self,
        selector: str = "iframe",
        timeout: float = None,
    ) -> dict:
        """
        等待 iframe 内容加载完成并可访问
        
        Args:
            selector: iframe 选择器
            timeout: 超时时间
        
        Returns:
            dict: {success, strategy, elapsed, details}
        """
        effective_timeout = timeout or self.get_effective_timeout()
        
        check_js = f"""
        (function() {{
            var iframes = document.querySelectorAll({selector!r});
            if (iframes.length === 0) return {{ total: 0, loaded: 0, error: 'no iframes' }};
            
            var total = iframes.length;
            var loaded = 0;
            var errors = [];
            
            for (var i = 0; i < iframes.length; i++) {{
                var iframe = iframes[i];
                try {{
                    var doc = iframe.contentDocument || iframe.contentWindow.document;
                    if (doc && doc.readyState === 'complete') {{
                        loaded++;
                    }} else if (doc) {{
                        errors.push('not_complete');
                    }} else {{
                        errors.push('cross_origin');
                    }}
                }} catch(e) {{
                    errors.push('access_error: ' + e.message);
                }}
            }}
            
            return {{ total: total, loaded: loaded, errors: errors }};
        }})()
        """
        
        start_time = time.time()
        
        while time.time() - start_time < effective_timeout:
            try:
                result = await self.session.eval_js(check_js)
            except Exception as e:
                await asyncio.sleep(0.5)
                continue
            
            total = result.get('total', 0)
            loaded = result.get('loaded', 0)
            errors = result.get('errors', [])
            
            if total == 0:
                return {
                    'success': True,
                    'strategy': 'iframe_ready',
                    'elapsed': time.time() - start_time,
                    'details': {'total': 0, 'note': 'no iframes found'},
                }
            
            if loaded == total:
                elapsed = time.time() - start_time
                logger.info(f"iframe_ready 完成: {loaded}/{total} 已加载 ({elapsed:.2f}s)")
                return {
                    'success': True,
                    'strategy': 'iframe_ready',
                    'elapsed': elapsed,
                    'details': {'total': total, 'loaded': loaded, 'errors': errors},
                }
            
            await asyncio.sleep(0.5)
        
        elapsed = time.time() - start_time
        return {
            'success': False,
            'strategy': 'iframe_ready',
            'elapsed': elapsed,
            'details': {'error': 'timeout', 'loaded': loaded, 'total': total, 'errors': errors},
        }
    
    # =========================================================================
    # 6. wait_for_form_submission — 等待表单提交后页面稳定
    # =========================================================================
    
    async def wait_for_form_submission(
        self,
        submit_selector: str = "button[type='submit']",
        result_indicator: str = None,
        timeout: float = None,
    ) -> dict:
        """
        等待表单提交后页面稳定
        
        策略：提交按钮消失 + 新内容出现 或 网络空闲
        
        Args:
            submit_selector: 提交按钮选择器
            result_indicator: 结果页面的特征选择器（可选）
            timeout: 超时时间
        
        Returns:
            dict: {success, strategy, elapsed, details}
        """
        effective_timeout = timeout or self.get_effective_timeout()
        
        start_time = time.time()
        
        # 等待提交按钮消失
        wait_submit_gone_js = f"""
        (function() {{
            var btn = document.querySelector({submit_selector!r});
            return btn === null || btn.offsetParent === null;
        }})()
        """
        
        deadline = start_time + effective_timeout
        while time.time() < deadline:
            try:
                gone = await self.session.eval_js(wait_submit_gone_js)
                if gone:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        
        # 提交按钮消失后，等待结果
        if result_indicator:
            # 等待结果元素出现
            wait_result_js = f"""
            (function() {{
                return document.querySelector({result_indicator!r}) !== null;
            }})()
            """
            deadline = time.time() + effective_timeout
            while time.time() < deadline:
                try:
                    found = await self.session.eval_js(wait_result_js)
                    if found:
                        elapsed = time.time() - start_time
                        logger.info(f"form_submitted 完成: 结果元素已出现 ({elapsed:.2f}s)")
                        return {
                            'success': True,
                            'strategy': 'form_submitted',
                            'elapsed': elapsed,
                            'details': {'result_selector': result_indicator},
                        }
                except Exception:
                    pass
                await asyncio.sleep(0.3)
        else:
            # 没有指定结果选择器，等待网络空闲
            from .smart_wait import SmartWait
            sw = SmartWait(self.session)
            result = await sw.wait_for('networkidle', timeout=effective_timeout)
            elapsed = time.time() - start_time
            return {
                'success': result.get('success', False),
                'strategy': 'form_submitted',
                'elapsed': elapsed,
                'details': result.get('details', {}),
            }
        
        elapsed = time.time() - start_time
        return {
            'success': False,
            'strategy': 'form_submitted',
            'elapsed': elapsed,
            'details': {'error': 'timeout'},
        }
    
    # =========================================================================
    # 7. wait_for_page_ready_v2 — 增强版页面就绪检测
    # =========================================================================
    
    async def wait_for_page_ready_v2(
        self,
        selector: str = None,
        network_idle: bool = True,
        images: bool = True,
        timeout: float = None,
    ) -> dict:
        """
        增强版页面就绪检测
        
        组合等待：网络空闲 + 指定元素出现 + 图片加载完成
        
        Args:
            selector: 关键元素选择器（可选）
            network_idle: 是否等待网络空闲
            images: 是否等待图片加载
            timeout: 超时时间
        
        Returns:
            dict: {success, strategy, elapsed, details}
        """
        effective_timeout = timeout or self.get_effective_timeout()
        start_time = time.time()
        results = {}
        remaining = effective_timeout
        
        # 1. 等待网络空闲
        if network_idle:
            from .smart_wait import SmartWait
            sw = SmartWait(self.session)
            net_result = await sw.wait_for('networkidle', timeout=remaining)
            results['network_idle'] = net_result
            if net_result.get('success'):
                remaining -= net_result.get('elapsed', 0)
            else:
                elapsed = time.time() - start_time
                return {
                    'success': False,
                    'strategy': 'page_ready_v2',
                    'elapsed': elapsed,
                    'details': {'error': 'network_idle_failed'},
                }
        
        # 2. 等待指定元素
        if selector and remaining > 0:
            from .smart_wait import SmartWait
            sw = SmartWait(self.session)
            sel_result = await sw.wait_for('selector', selector=selector, timeout=remaining)
            results['selector'] = sel_result
            if sel_result.get('success'):
                remaining -= sel_result.get('elapsed', 0)
            else:
                elapsed = time.time() - start_time
                return {
                    'success': False,
                    'strategy': 'page_ready_v2',
                    'elapsed': elapsed,
                    'details': {'error': 'selector_not_found', 'selector': selector},
                }
        
        # 3. 等待图片加载
        if images and remaining > 0:
            img_result = await self.wait_for_images_complete(timeout=remaining)
            results['images'] = img_result
            # 图片加载失败不阻断，只记录
        
        elapsed = time.time() - start_time
        logger.info(f"page_ready_v2 完成: {elapsed:.2f}s, results={results}")
        return {
            'success': True,
            'strategy': 'page_ready_v2',
            'elapsed': elapsed,
            'details': results,
        }


# 便捷函数
def create_smart_wait_v2(smart_wait_instance):
    """从 SmartWait 实例创建 SmartWaitV2"""
    return SmartWaitV2(smart_wait_instance)
