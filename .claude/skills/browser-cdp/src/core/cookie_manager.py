"""
cookie_manager.py - Cookie / Session 统一管理器

提供：
1. 按域名自动存储/恢复 Cookie
2. 持久化到磁盘（JSON 格式，支持多 session）
3. 自动过期清理
4. 敏感 Cookie 标记（HttpOnly / Secure / SameSite）
5. 与 EnhancedCDPSession / SPAScraper 无缝集成

用法示例：
    from src.core.cookie_manager import CookieManager
    mgr = CookieManager(storage_dir="./data/cookies")
    await mgr.save_cookies(session, "example.com")
    cookies = await mgr.load_cookies("example.com")
    await session.add_cookies(cookies)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# CDP SetCookie 字段名映射（Playwright/CDP 通用）
COOKIE_FIELDS = {
    "name": "name",
    "value": "value",
    "domain": "domain",
    "path": "path",
    "secure": "secure",
    "httpOnly": "httpOnly",
    "sameSite": "sameSite",
    "expires": "expires",
    "size": "size",
    "session": "session",
}


@dataclass
class CookieEntry:
    """单条 Cookie 记录"""
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    same_site: str = "Lax"  # Lax | Strict | None
    expires: Optional[float] = None  # Unix timestamp
    session: bool = True  # True = 会话 Cookie
    source_url: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "secure": self.secure,
            "httpOnly": self.http_only,
            "sameSite": self.same_site,
            "expires": self.expires,
            "session": self.session,
            "sourceUrl": self.source_url,
            "createdAt": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CookieEntry":
        return cls(
            name=d.get("name", ""),
            value=d.get("value", ""),
            domain=d.get("domain", ""),
            path=d.get("path", "/"),
            secure=d.get("secure", False),
            http_only=d.get("httpOnly", False),
            same_site=d.get("sameSite", "Lax"),
            expires=d.get("expires"),
            session=d.get("session", True),
            source_url=d.get("sourceUrl", ""),
            created_at=d.get("created_at", time.time()),
        )

    def is_expired(self) -> bool:
        """判断是否过期"""
        if self.session:
            return False  # 会话 Cookie 不检查过期
        if self.expires is None:
            return False
        return time.time() > self.expires

    def to_cdp_cookie(self) -> Dict[str, Any]:
        """转换为 CDP SetCookie 格式"""
        result: Dict[str, Any] = {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "secure": self.secure,
            "httpOnly": self.http_only,
            "sameSite": self.same_site,
        }
        if not self.session and self.expires is not None:
            result["expires"] = self.expires
        return result


@dataclass
class DomainCookies:
    """某域名的所有 Cookie 集合"""
    domain: str
    cookies: List[CookieEntry] = field(default_factory=list)
    loaded_at: float = field(default_factory=time.time)
    source_url: str = ""

    def add(self, cookie: CookieEntry) -> None:
        """添加或更新 Cookie（同名覆盖）"""
        for i, c in enumerate(self.cookies):
            if c.name == cookie.name and c.domain == cookie.domain:
                self.cookies[i] = cookie
                return
        self.cookies.append(cookie)

    def remove(self, name: str) -> int:
        """删除指定名称的 Cookie，返回删除数量"""
        before = len(self.cookies)
        self.cookies = [c for c in self.cookies if c.name != name]
        return before - len(self.cookies)

    def get(self, name: str) -> Optional[CookieEntry]:
        """按名称获取 Cookie"""
        for c in self.cookies:
            if c.name == name:
                return c
        return None

    def purge_expired(self) -> int:
        """清除过期 Cookie，返回清除数量"""
        before = len(self.cookies)
        self.cookies = [c for c in self.cookies if not c.is_expired()]
        return before - len(self.cookies)

    def to_cdp_list(self) -> List[Dict[str, Any]]:
        """转换为 CDP SetCookie 列表"""
        return [c.to_cdp_cookie() for c in self.cookies if not c.is_expired()]


class CookieManager:
    """
    Cookie / Session 统一管理器

    功能：
    - 按域名自动组织 Cookie
    - 持久化到 JSON 文件（支持多 session）
    - 自动清理过期 Cookie
    - 支持 CDP / Playwright 双格式转换
    """

    def __init__(
        self,
        storage_dir: str = "./data/cookies",
        auto_purge: bool = True,
        purge_interval_sec: float = 3600.0,
    ):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.auto_purge = auto_purge
        self.purge_interval_sec = purge_interval_sec
        self._last_purge: float = 0.0
        self._domains: Dict[str, DomainCookies] = {}
        self._lock: Optional[Any] = None  # threading.RLock（延迟导入）

    # ------------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------------

    async def save_cookies(
        self,
        session: Any,
        domain: str,
        source_url: str = "",
    ) -> int:
        """
        从 CDP session 获取当前域名 Cookie 并保存

        Args:
            session: CDP session 对象（有 eval_js 方法）
            domain: 目标域名
            source_url: 来源 URL

        Returns:
            保存的 Cookie 数量
        """
        js = f"""
        (() => {{
            const cookies = document.cookie.split('; ');
            return cookies.map(c => {{
                const [name, ...rest] = c.split('=');
                return {{ name, value: rest.join('=') }};
            }});
        }})()
        """
        try:
            raw = await session.eval_js(js)
        except Exception as e:
            logger.warning(f"CookieManager: 获取 Cookie 失败 [{domain}]: {e}")
            return 0

        entries: List[CookieEntry] = []
        for item in (raw or []):
            name = item.get("name", "")
            value = item.get("value", "")
            if not name:
                continue
            entries.append(CookieEntry(
                name=name,
                value=value,
                domain=domain,
                source_url=source_url,
                session=item.get("session", True),
                expires=item.get("expires"),
            ))

        dc = self._get_or_create(domain, source_url)
        for entry in entries:
            dc.add(entry)
        dc.purge_expired()
        await self._persist(domain)
        logger.debug(f"CookieManager: 保存 {len(dc.cookies)} 条 Cookie [{domain}]")
        return len(dc.cookies)

    async def load_cookies(
        self,
        domain: str,
    ) -> List[Dict[str, Any]]:
        """
        加载某域名的 Cookie，返回 CDP SetCookie 格式列表
        """
        self._maybe_purge()
        dc = self._domains.get(domain)
        if dc is None:
            await self._load_from_disk(domain)
            dc = self._domains.get(domain)
        if dc is None:
            return []
        return dc.to_cdp_list()

    async def add_cookies(
        self,
        session: Any,
        cookies: List[Dict[str, Any]],
        domain: str,
    ) -> int:
        """
        将 Cookie 写入浏览器（通过 CDP Network.setCookies）

        Args:
            session: CDP session 对象
            cookies: Cookie 列表（CDP 格式）
            domain: 目标域名

        Returns:
            成功写入数量
        """
        if not cookies:
            return 0
        try:
            await session.send("Network.setCookies", {
                "cookies": cookies,
            })
            dc = self._get_or_create(domain)
            for c in cookies:
                entry = CookieEntry(
                    name=c.get("name", ""),
                    value=c.get("value", ""),
                    domain=c.get("domain", domain),
                    path=c.get("path", "/"),
                    secure=c.get("secure", False),
                    http_only=c.get("httpOnly", False),
                    same_site=c.get("sameSite", "Lax"),
                    expires=c.get("expires"),
                    session=c.get("session", True),
                )
                dc.add(entry)
            await self._persist(domain)
            return len(cookies)
        except Exception as e:
            logger.warning(f"CookieManager: 写入 Cookie 失败 [{domain}]: {e}")
            return 0

    async def clear_cookies(self, domain: str) -> int:
        """清除某域名的所有 Cookie"""
        dc = self._domains.pop(domain, None)
        count = len(dc.cookies) if dc else 0
        cookie_file = self._get_cookie_file(domain)
        if cookie_file.exists():
            cookie_file.unlink()
        return count

    async def get_all_domains(self) -> List[str]:
        """返回所有已保存的域名列表"""
        self._maybe_purge()
        return list(self._domains.keys())

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _get_or_create(self, domain: str, source_url: str = "") -> DomainCookies:
        if domain not in self._domains:
            self._domains[domain] = DomainCookies(domain=domain, source_url=source_url)
        return self._domains[domain]

    async def _persist(self, domain: str) -> None:
        """持久化到 JSON 文件"""
        dc = self._domains.get(domain)
        if dc is None:
            return
        data = {
            "domain": dc.domain,
            "sourceUrl": dc.source_url,
            "loadedAt": dc.loaded_at,
            "cookies": [c.to_dict() for c in dc.cookies],
        }
        cookie_file = self._get_cookie_file(domain)
        try:
            cookie_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"CookieManager: 持久化失败 [{domain}]: {e}")

    async def _load_from_disk(self, domain: str) -> None:
        """从 JSON 文件加载"""
        cookie_file = self._get_cookie_file(domain)
        if not cookie_file.exists():
            return
        try:
            text = cookie_file.read_text(encoding="utf-8")
            data = json.loads(text)
            dc = DomainCookies(
                domain=data.get("domain", domain),
                source_url=data.get("sourceUrl", ""),
                loaded_at=data.get("loadedAt", time.time()),
            )
            for c in data.get("cookies", []):
                entry = CookieEntry.from_dict(c)
                if not entry.is_expired():
                    dc.add(entry)
            self._domains[domain] = dc
            logger.debug(f"CookieManager: 从磁盘加载 {len(dc.cookies)} 条 Cookie [{domain}]")
        except Exception as e:
            logger.warning(f"CookieManager: 加载失败 [{domain}]: {e}")

    def _get_cookie_file(self, domain: str) -> Path:
        """domain 中冒号/斜杠替换为下划线，保证文件名合法"""
        safe = domain.replace(":", "_").replace("/", "_").replace(".", "_")
        return self.storage_dir / f"{safe}.json"

    def _maybe_purge(self) -> None:
        """按需清除过期 Cookie"""
        if not self.auto_purge:
            return
        now = time.time()
        if now - self._last_purge < self.purge_interval_sec:
            return
        self._last_purge = now
        purged_total = 0
        for domain in list(self._domains.keys()):
            dc = self._domains[domain]
            n = dc.purge_expired()
            if n > 0:
                purged_total += n
                self._persist(domain)  # type: ignore[arg-type]
        if purged_total:
            logger.debug(f"CookieManager: 清理 {purged_total} 条过期 Cookie")

    async def export_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """导出所有域名的 Cookie（CDP 格式）"""
        result: Dict[str, List[Dict[str, Any]]] = {}
        for domain in list(self._domains.keys()):
            result[domain] = await self.load_cookies(domain)
        return result

    async def import_cookies(
        self,
        data: Dict[str, List[Dict[str, Any]]],
    ) -> int:
        """批量导入 Cookie"""
        total = 0
        for domain, cookies in data.items():
            n = await self.add_cookies(None, cookies, domain)  # type: ignore[arg-type]
            total += n
        return total

    @staticmethod
    def extract_domain(url: str) -> str:
        """从 URL 提取域名"""
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return url.split("/")[2] if "/" in url else url


# =====================================================================
# 便捷函数
# =====================================================================

async def save_session_cookies(
    session: Any,
    manager: CookieManager,
    domain: str,
    source_url: str = "",
) -> int:
    """快捷函数：保存当前页面 Cookie"""
    return await manager.save_cookies(session, domain, source_url)


async def restore_session_cookies(
    session: Any,
    manager: CookieManager,
    domain: str,
) -> int:
    """快捷函数：恢复 Cookie 到浏览器"""
    cookies = await manager.load_cookies(domain)
    if not cookies:
        return 0
    return await manager.add_cookies(session, cookies, domain)
