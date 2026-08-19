"""
src/adapters/mixins/admin_mixin.py

后台管理系统通用混入：Session管理、CSRF Token提取、ECharts数据提取。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AdminMixin:
    """
    后台管理系统通用混入，提供以下能力：
    1. Session管理：Cookie/JWT登录态的保存与恢复
    2. CSRF Token自动提取：从meta tag/Cookie中提取
    3. ECharts数据提取：读取图表实例数据
    4. 动态菜单导航：按文本匹配定位功能入口
    """
    
    CSRF_SELECTORS = [
        'meta[name="csrf-token"]',
        'meta[name="csrf_token"]',
        'input[name="_token"]',
        'input[name="csrf_token"]',
    ]
    CSRF_COOKIE_NAMES = ["csrf_token", "csrf", "_csrf", "XSRF-TOKEN"]
    
    def __init__(self, **kwargs):
        self._session_store: Dict[str, Any] = {}
        self._csrf_token: Optional[str] = None
        self._token_name = kwargs.get("token_name", "token")
        self._auth_header = kwargs.get("auth_header", "Authorization")
    
    # ==================== Session 管理 ====================
    
    async def save_session(self, page) -> Dict[str, Any]:
        """保存当前页面登录态"""
        cookies = await page.context.cookies()
        token = await page.evaluate(f"localStorage.getItem('{self._token_name}')")
        return {
            "cookies": cookies,
            "token": token,
            "saved_at": __import__('datetime').datetime.now().isoformat(),
        }
    
    async def restore_session(self, page, session_data: Dict[str, Any]) -> bool:
        """恢复登录态并验证有效性"""
        try:
            if session_data.get("cookies"):
                await page.context.clear_cookies()
                await page.context.add_cookies(session_data["cookies"])
            
            if session_data.get("token"):
                await page.evaluate(f"localStorage.setItem('{self._token_name}', '{session_data['token']}')")
            
            return await self._verify_auth(page)
        except Exception as e:
            logger.warning(f"Session 恢复失败: {e}")
            return False
    
    async def _verify_auth(self, page, check_url: str = "/api/user/info") -> bool:
        """验证登录态是否有效"""
        try:
            async with page.expect_response(
                lambda r: check_url in r.url,
                timeout=10000
            ) as resp_info:
                await page.goto(f"about:blank")
                await page.goto(page.url)  # 触发请求
            response = await resp_info.value
            if response.status == 200:
                data = await response.json()
                return data.get("code", 0) == 0 or data.get("success", False)
        except Exception as e:
            logger.debug(f"Auth verification error: {e}")
        return False
    
    async def perform_login(self, page, username: str, password: str,
                            username_selector: str = 'input[name="username"]',
                            password_selector: str = 'input[name="password"]',
                            submit_selector: str = 'button[type="submit"]') -> bool:
        """执行登录流程并保存Session"""
        await page.fill(username_selector, username)
        await page.fill(password_selector, password)
        await page.click(submit_selector)
        
        # 等待登录成功跳转
        await asyncio.sleep(2)
        
        # 尝试保存Session
        try:
            session = await self.save_session(page)
            if session["token"] or session["cookies"]:
                logger.info("登录成功，Session已保存")
                return True
        except Exception as e:
            logger.warning(f"登录Session保存失败: {e}")
        
        return await self._verify_auth(page)
    
    # ==================== CSRF Token ====================
    
    async def get_csrf_token(self, page) -> Optional[str]:
        """自动从页面提取 CSRF Token"""
        # 方式1: meta tag
        for selector in self.CSRF_SELECTORS:
            token = await page.evaluate(f"document.querySelector('{selector}')?.content || document.querySelector('{selector}')?.value")
            if token:
                logger.debug(f"从 {selector} 提取 CSRF Token")
                self._csrf_token = token
                return token
        
        # 方式2: cookie
        cookies = await page.context.cookies()
        for c in cookies:
            if c["name"].lower() in self.CSRF_COOKIE_NAMES:
                logger.debug(f"从 Cookie 提取 CSRF Token: {c['name']}")
                self._csrf_token = c["value"]
                return c["value"]
        
        # 方式3: window.__INITIAL_STATE__ (常见于 SSR 框架)
        try:
            state = await page.evaluate("window.__INITIAL_STATE__ || window.__NUXT__")
            if state and isinstance(state, dict):
                for key in ["csrfToken", "_csrf", "csrf"]:
                    if key in state:
                        self._csrf_token = state[key]
                        return state[key]
        except Exception:
            pass
        
        logger.warning("未能提取 CSRF Token")
        return None
    
    async def build_auth_headers(self) -> Dict[str, str]:
        """构建带认证的请求头"""
        headers = {}
        if self._csrf_token:
            headers["X-CSRF-Token"] = self._csrf_token
        token = self._session_store.get("token")
        if token:
            headers[self._auth_header] = f"Bearer {token}"
        return headers
    
    # ==================== ECharts 数据提取 ====================
    
    async def extract_chart_data(self, page, selector: str = ".echarts-container") -> List[Dict]:
        """从 ECharts 实例提取图表数据"""
        try:
            data = await page.evaluate(f'''
                () => {{
                    const containers = document.querySelectorAll('{selector}');
                    const results = [];
                    containers.forEach(el => {{
                        let instance = null;
                        if (typeof echarts !== 'undefined') {{
                            instance = echarts.getInstanceByDom(el);
                        }} else if (el._echarts_instance_) {{
                            instance = el._echarts_instance_;
                        }}
                        if (instance) {{
                            try {{
                                results.push(instance.getOption());
                            }} catch(e) {{}}
                        }}
                    }});
                    return results;
                }}
            ''')
            return data if data else []
        except Exception as e:
            logger.warning(f"ECharts 数据提取失败: {e}")
            return []
    
    async def extract_antv_chart_data(self, page, selector: str = ".chart-wrap") -> List[Dict]:
        """从 AntV G2 实例提取图表数据"""
        try:
            data = await page.evaluate(f'''
                () => {{
                    const containers = document.querySelectorAll('{selector}');
                    const results = [];
                    containers.forEach(el => {{
                        const chart = el.chart || el.__chart__;
                        if (chart) {{
                            try {{
                                results.push({{
                                    title: chart.title?.text || '',
                                    data: chart.geometries?.[0]?.data || [],
                                }});
                            }} catch(e) {{}}
                        }}
                    }});
                    return results;
                }}
            ''')
            return data if data else []
        except Exception as e:
            logger.warning(f"AntV 数据提取失败: {e}")
            return []
    
    # ==================== 动态菜单导航 ====================
    
    async def find_menu_by_text(self, page, text: str, timeout: float = 10.0) -> Optional[str]:
        """
        按文本匹配定位菜单入口，返回点击后的 URL。
        用于动态路由的后台系统。
        """
        try:
            async with page.expect_navigation(timeout=int(timeout * 1000)) as nav_info:
                links = await page.query_selector_all(f'a:has-text("{text}"), .menu-item:has-text("{text}")')
                if links:
                    await links[0].click()
                    response = await nav_info.value
                    return response.url if response else page.url
        except Exception as e:
            logger.warning(f"菜单导航失败: {e}")
            # 无导航时返回当前 URL
            return page.url
        return None
    
    # ==================== 数据表格提取 ====================
    
    async def extract_table_data(self, page, selector: str = "table", limit: int = 100) -> List[Dict]:
        """从后台表格提取数据"""
        try:
            data = await page.evaluate(f'''
                (selector, limit) => {{
                    const tables = document.querySelectorAll(selector);
                    const allRows = [];
                    tables.forEach(table => {{
                        const rows = table.querySelectorAll('tbody tr, tr');
                        rows.forEach(row => {{
                            const cells = row.querySelectorAll('td, th');
                            const rowData = [];
                            cells.forEach(cell => rowData.push(cell.textContent.trim()));
                            if (rowData.length > 0 && !allRows.includes(rowData.join('|')))
                                allRows.push(rowData);
                            if (allRows.length >= limit) return;
                        }});
                    }});
                    return allRows;
                }}
            ''', selector, limit)
            return data[:limit] if data else []
        except Exception as e:
            logger.warning(f"表格提取失败: {e}")
            return []
    
    # ==================== Hook 集成 ====================
    
    def register_hooks(self, descriptor) -> None:
        if not hasattr(descriptor, "hooks") or descriptor.hooks is None:
            descriptor.hooks = {}
        descriptor.hooks["save_session"] = self.save_session
        descriptor.hooks["restore_session"] = self.restore_session
        descriptor.hooks["get_csrf"] = self.get_csrf_token
        descriptor.hooks["extract_charts"] = self.extract_chart_data
        descriptor.hooks["find_menu"] = self.find_menu_by_text


__all__ = ["AdminMixin"]
