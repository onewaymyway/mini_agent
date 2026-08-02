"""
complex_dom.py - 复杂 DOM 结构处理模块

处理 Shadow DOM、iframe、Web Components 等复杂 DOM 结构。

核心功能：
- Shadow DOM 递归遍历
- iframe 内容访问
- Web Components 自定义元素处理
- 虚拟列表元素定位
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DOMScanConfig:
    """DOM 扫描配置"""
    include_shadow: bool = True  # 包含 Shadow DOM
    include_iframes: bool = True  # 包含 iframe
    max_depth: int = 10  # 最大递归深度
    timeout: float = 10.0  # 超时时间


class ComplexDOMHandler:
    """
    复杂 DOM 结构处理器
    
    处理：
    1. Shadow DOM（Web Components 封装）
    2. iframe（嵌入式内容）
    3. Web Components（自定义元素）
    4. 虚拟列表（Virtual Scroll）
    """
    
    def __init__(self, session, config: DOMScanConfig = None):
        self.session = session
        self.config = config or DOMScanConfig()
    
    # =========================================================================
    # Shadow DOM 处理
    # =========================================================================
    
    async def scan_shadow_dom(
        self,
        root_selector: str = "*",
        selector: str = None,
        max_depth: int = None
    ) -> List[Dict[str, Any]]:
        """
        递归扫描 Shadow DOM
        
        Args:
            root_selector: 根元素选择器
            selector: 目标元素选择器（可选）
            max_depth: 最大递归深度
        
        Returns:
            List[Dict]: 扫描到的元素列表
        """
        max_depth = max_depth or self.config.max_depth
        
        js = f"""
        (() => {{
            const results = [];
            const SEL = {selector!r} || [
                'a[href]', 'button', 'input', 'textarea', 'select',
                '[role="button"]', '[role="link"]', '[role="checkbox"]',
                '[role="tab"]', '[onclick]', '[contenteditable="true"]'
            ].join(',');
            
            function scanNode(node, depth = 0) {{
                if (depth > {max_depth}) return;
                
                // 处理 Shadow DOM
                if (node.shadowRoot) {{
                    Array.from(node.shadowRoot.querySelectorAll(SEL)).forEach(el => {{
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {{
                            results.push({{
                                tag: el.tagName.toLowerCase(),
                                text: (el.innerText || el.value || '').trim().slice(0, 100),
                                rect: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}},
                                shadow: true,
                                shadowRoot: node.tagName.toLowerCase()
                            }});
                        }}
                        scanNode(el, depth + 1);
                    }});
                }}
                
                // 处理普通 DOM
                Array.from(node.querySelectorAll(SEL)).forEach(el => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {{
                        results.push({{
                            tag: el.tagName.toLowerCase(),
                            text: (el.innerText || el.value || '').trim().slice(0, 100),
                            rect: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}},
                            shadow: false
                        }});
                    }}
                }});
            }}
            
            scanNode(document.querySelector('{root_selector}') || document);
            return results;
        }})()
        """
        
        results = await self.session.eval_js(js)
        logger.info(f"Shadow DOM 扫描完成，找到 {len(results) if results else 0} 个元素")
        return results or []
    
    # =========================================================================
    # iframe 处理
    # =========================================================================
    
    async def access_iframe(
        self,
        iframe_selector: str,
        timeout: float = None
    ) -> Optional[Dict[str, Any]]:
        """
        访问 iframe 内容
        
        注意：跨域 iframe 无法访问
        
        Args:
            iframe_selector: iframe 选择器
            timeout: 超时时间
        
        Returns:
            Dict: iframe 内容信息，失败返回 None
        """
        timeout = timeout or self.config.timeout
        
        js = f"""
        (() => {{
            const iframe = document.querySelector('{iframe_selector}');
            if (!iframe) return null;
            
            try {{
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                return {{
                    url: iframe.src,
                    title: doc.title,
                    body_text: doc.body ? doc.body.innerText.slice(0, 5000) : null,
                    links: Array.from(doc.querySelectorAll('a[href]')).map(a => a.href).slice(0, 50),
                    success: true
                }};
            }} catch (e) {{
                return {{
                    url: iframe.src,
                    error: 'Cross-origin iframe',
                    success: false
                }};
            }}
        }})()
        """
        
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            result = await self.session.eval_js(js)
            if result and result.get('success'):
                logger.info(f"iframe 访问成功: {result.get('url')}")
                return result
            await asyncio.sleep(0.5)
        
        logger.warning(f"iframe 访问超时: {iframe_selector}")
        return None
    
    async def scan_all_iframes(self) -> List[Dict[str, Any]]:
        """
        扫描所有 iframe
        
        Returns:
            List[Dict]: iframe 列表
        """
        js = """
        (() => {
            return Array.from(document.querySelectorAll('iframe')).map(iframe => ({
                src: iframe.src,
                id: iframe.id || null,
                class: iframe.className || null,
                width: iframe.width,
                height: iframe.height,
            }));
        })()
        """
        
        iframes = await self.session.eval_js(js)
        logger.info(f"找到 {len(iframes) if iframes else 0} 个 iframe")
        return iframes or []
    
    # =========================================================================
    # Web Components 处理
    # =========================================================================
    
    async def wait_for_custom_element(
        self,
        tag_name: str,
        timeout: float = None
    ) -> bool:
        """
        等待自定义元素定义
        
        Args:
            tag_name: 自定义元素标签名
            timeout: 超时时间
        
        Returns:
            bool: 是否定义完成
        """
        timeout = timeout or self.config.timeout
        
        js = f"""
        (() => {{
            if (customElements.get('{tag_name}')) {{
                return true;
            }}
            // 使用 MutationObserver 监听
            return new Promise(resolve => {{
                const observer = new MutationObserver(() => {{
                    if (customElements.get('{tag_name}')) {{
                        observer.disconnect();
                        resolve(true);
                    }}
                }});
                observer.observe(document.head, {{ childList: true }});
                // 超时取消
                setTimeout(() => {{
                    observer.disconnect();
                    resolve(false);
                }}, {timeout * 1000});
            }});
        }})()
        """
        
        result = await self.session.eval_js(js)
        logger.info(f"自定义元素 {tag_name}: {'已定义' if result else '未定义'}")
        return result
    
    async def get_custom_element_info(self, tag_name: str) -> Dict[str, Any]:
        """
        获取自定义元素信息
        
        Args:
            tag_name: 自定义元素标签名
        
        Returns:
            Dict: 元素信息
        """
        js = f"""
        (() => {{
            const tag = '{tag_name}';
            const definition = customElements.get(tag);
            if (!definition) return null;
            
            // 创建临时实例
            const el = document.createElement(tag);
            document.body.appendChild(el);
            
            const info = {{
                tag: tag,
                prototype: definition.prototype.constructor.name,
                observedAttributes: definition.observedAttributes || [],
                connectedCallback: typeof definition.prototype.connectedCallback,
                attributesChangedCallback: typeof definition.prototype.attributesChangedCallback,
            }};
            
            document.body.removeChild(el);
            return info;
        }})()
        """
        
        return await self.session.eval_js(js)
    
    # =========================================================================
    # 虚拟列表处理
    # =========================================================================
    
    async def detect_virtual_list(
        self,
        container_selector: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        检测虚拟列表
        
        Args:
            container_selector: 容器选择器
        
        Returns:
            Dict: 虚拟列表信息，未检测到返回 None
        """
        js = f"""
        (() => {{
            const container = document.querySelector({container_selector!r} || '[class*="virtual"], [class*="scroll-list"], [class*="list-view"]');
            if (!container) return null;
            
            // 检测虚拟列表特征
            const hasScrollContainer = container.scrollHeight > container.clientHeight;
            const items = container.querySelectorAll('[class*="item"], [role="option"]');
            const itemHeight = items.length > 0 ? items[0].offsetHeight : 0;
            const visibleCount = Math.floor(container.clientHeight / itemHeight);
            const totalCount = items.length;
            
            return {{
                isVirtual: totalCount < visibleCount * 3, // 元素数量远少于可见数量
                containerHeight: container.clientHeight,
                scrollHeight: container.scrollHeight,
                itemHeight: itemHeight,
                visibleCount: visibleCount,
                totalCount: totalCount,
                selector: '{container_selector}'
            }};
        }})()
        """
        
        result = await self.session.eval_js(js)
        if result:
            logger.info(f"检测到虚拟列表: {result}")
        return result
    
    # =========================================================================
    # 通用 DOM 扫描
    # =========================================================================
    
    async def scan_interactive_elements(
        self,
        include_shadow: bool = None,
        include_iframes: bool = None
    ) -> List[Dict[str, Any]]:
        """
        扫描所有可交互元素（增强版）
        
        Args:
            include_shadow: 是否包含 Shadow DOM
            include_iframes: 是否包含 iframe
        
        Returns:
            List[Dict]: 元素列表
        """
        include_shadow = include_shadow if include_shadow is not None else self.config.include_shadow
        include_iframes = include_iframes if include_iframes is not None else self.config.include_iframes
        
        # 主文档扫描
        main_elements = await self._scan_main_document()
        
        # Shadow DOM 扫描
        shadow_elements = []
        if include_shadow:
            shadow_elements = await self.scan_shadow_dom()
        
        # iframe 扫描
        iframe_elements = []
        if include_iframes:
            iframe_elements = await self._scan_iframes()
        
        # 合并结果
        all_elements = main_elements + shadow_elements + iframe_elements
        logger.info(f"扫描完成，共 {len(all_elements)} 个可交互元素")
        
        return all_elements
    
    async def _scan_main_document(self) -> List[Dict[str, Any]]:
        """扫描主文档"""
        js = """
        (() => {
            const SEL = [
                'a[href]', 'button', 'input', 'textarea', 'select',
                '[role="button"]', '[role="link"]', '[role="checkbox"]',
                '[role="tab"]', '[onclick]', '[contenteditable="true"]'
            ].join(',');
            
            const nodes = Array.from(document.querySelectorAll(SEL));
            const seen = new Set();
            const out = [];
            
            for (const el of nodes) {
                if (seen.has(el)) continue;
                seen.add(el);
                
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;
                
                const style = window.getComputedStyle(el);
                if (style.visibility === 'hidden' || style.display === 'none') continue;
                
                const tag = el.tagName.toLowerCase();
                let text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim();
                if (text.length > 80) text = text.slice(0, 80) + '...';
                
                out.push({
                    tag: tag,
                    type: el.getAttribute('type') || null,
                    text: text,
                    name: el.getAttribute('name') || null,
                    id: el.id || null,
                    href: el.getAttribute('href') || null,
                    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                    disabled: !!el.disabled,
                    shadow: false
                });
            }
            
            return out;
        })()
        """
        
        return await self.session.eval_js(js) or []
    
    async def _scan_iframes(self) -> List[Dict[str, Any]]:
        """扫描 iframe 内容"""
        js = """
        (() => {
            const results = [];
            const iframes = document.querySelectorAll('iframe');
            
            for (const iframe of iframes) {
                try {
                    const doc = iframe.contentDocument || iframe.contentWindow.document;
                    const elements = doc.querySelectorAll('a[href], button, input, textarea, select');
                    
                    for (const el of elements) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            results.push({
                                tag: el.tagName.toLowerCase(),
                                text: (el.innerText || el.value || '').trim().slice(0, 80),
                                rect: {x: rect.x, y: rect.y + iframe.getBoundingClientRect().y, width: rect.width, height: rect.height},
                                iframe_src: iframe.src,
                                shadow: false
                            });
                        }
                    }
                } catch (e) {
                    // 跨域 iframe 无法访问
                }
            }
            
            return results;
        })()
        """
        
        return await self.session.eval_js(js) or []
