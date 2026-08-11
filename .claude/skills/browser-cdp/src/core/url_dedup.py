"""
URL去重模块 - 支持多种去重策略

提供URL标准化、Bloom Filter、SQLite持久化去重，支持增量爬取。
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class UrlNormalizer:
    """URL标准化器"""

    # 需要移除的查询参数
    IRRELEVANT_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "yclid", "msclkid", "ref", "referrer",
        "sih", "shareid", "tab", "from", "source", "site",
    }

    @classmethod
    def normalize(cls, url: str) -> str:
        """标准化URL"""
        try:
            parsed = urllib.parse.urlparse(url)
            # 统一大小写（scheme和host）
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            # 移除www前缀
            netloc = netloc.replace("www.", "", 1)
            # 移除端口号默认值
            if scheme == "http" and netloc.endswith(":80"):
                netloc = netloc[:-3]
            elif scheme == "https" and netloc.endswith(":443"):
                netloc = netloc[:-4]
            # 清理路径
            path = cls._clean_path(parsed.path)
            # 过滤无关查询参数
            query = cls._filter_params(parsed.query)
            # 移除片段
            fragment = ""
            return urllib.parse.urlunparse((scheme, netloc, path, "", query, fragment))
        except Exception:
            return url

    @classmethod
    def _clean_path(cls, path: str) -> str:
        """清理路径：移除尾部斜杠、规范化路径段"""
        path = path.rstrip("/") or "/"
        # 移除 .html 后缀（保留路径结构）
        path = re.sub(r"\.html/?$", "/", path)
        return path

    @classmethod
    def _filter_params(cls, query: str) -> str:
        """过滤无关查询参数"""
        if not query:
            return ""
        parts = urllib.parse.parse_qsl(query)
        filtered = [(k, v) for k, v in parts if k.lower() not in cls.IRRELEVANT_PARAMS]
        return urllib.parse.urlencode(filtered)

    @classmethod
    def get_domain(cls, url: str) -> str:
        """提取域名"""
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "", 1)
            return domain
        except Exception:
            return url


class BloomFilter:
    """Bloom Filter 实现，用于高效内存去重"""

    def __init__(self, capacity: int = 100_000, error_rate: float = 0.01):
        self.capacity = capacity
        self.error_rate = error_rate
        # 根据容量和错误率计算位数组大小和哈希函数数量
        import math
        self.bit_size = max(int(-capacity * math.log(error_rate) / (math.log(2) ** 2)), capacity * 8)
        self.hash_count = max(int(self.bit_size / capacity * math.log(2)), 1)
        self.bit_array = bytearray((self.bit_size + 7) // 8)
        self.count = 0

    def _hashes(self, item: str) -> List[int]:
        """生成多个哈希值"""
        h = hashlib.md5(item.encode()).digest()
        hashes = []
        for i in range(0, len(h), 4):
            if i + 4 <= len(h):
                val = int.from_bytes(h[i:i+4], "big")
                hashes.append(val % self.bit_size)
            if len(hashes) >= self.hash_count:
                break
        # 补充哈希（用SHA256）
        if len(hashes) < self.hash_count:
            sha = hashlib.sha256(item.encode()).digest()
            for i in range(0, len(sha), 4):
                if i + 4 <= len(sha):
                    val = int.from_bytes(sha[i:i+4], "big")
                    hashes.append(val % self.bit_size)
                if len(hashes) >= self.hash_count:
                    break
        return hashes[:self.hash_count]

    def add(self, item: str) -> None:
        """添加元素"""
        for pos in self._hashes(item):
            byte_idx = pos // 8
            bit_idx = pos % 8
            self.bit_array[byte_idx] |= (1 << bit_idx)
        self.count += 1

    def maybe_exists(self, item: str) -> bool:
        """判断元素是否存在（可能有误报）"""
        for pos in self._hashes(item):
            byte_idx = pos // 8
            bit_idx = pos % 8
            if not (self.bit_array[byte_idx] & (1 << bit_idx)):
                return False
        return True

    def reset(self) -> None:
        """重置"""
        self.bit_array = bytearray((self.bit_size + 7) // 8)
        self.count = 0

    @property
    def size(self) -> int:
        return self.count


class UrlDedupManager:
    """URL去重管理器 - Bloom Filter + SQLite持久化"""

    def __init__(
        self,
        storage_path: Optional[str] = None,
        bloom_capacity: int = 500_000,
        bloom_error_rate: float = 0.001,
        max_total_urls: int = 10_000_000,
        retention_days: int = 30,
    ):
        self.bloom = BloomFilter(capacity=bloom_capacity, error_rate=bloom_error_rate)
        self.max_total_urls = max_total_urls
        self.retention_days = retention_days
        self._db_path = storage_path or str(Path.home() / ".browser_cdp" / "url_dedup.db")
        self._lock = None  # 在需要时初始化
        self._in_memory_seen: Set[str] = set()
        self._db: Optional[sqlite3.Connection] = None
        self._init_database()
        self._load_from_disk()

    def _get_db_path(self) -> Path:
        """获取数据库路径"""
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _init_database(self) -> None:
        """初始化数据库"""
        self._db = sqlite3.connect(self._get_db_path())
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS crawled_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash TEXT NOT NULL UNIQUE,
                original_url TEXT NOT NULL,
                domain TEXT NOT NULL,
                content_type TEXT DEFAULT 'unknown',
                first_crawled_at REAL NOT NULL,
                last_crawled_at REAL NOT NULL,
                crawl_count INTEGER DEFAULT 1,
                status TEXT DEFAULT 'success'
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_url_hash ON crawled_urls(url_hash)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_domain ON crawled_urls(domain)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_last_crawled ON crawled_urls(last_crawled_at)")
        self._db.commit()
        logger.info(f"URL去重数据库初始化完成: {self._db_path}")

    def _load_from_disk(self) -> None:
        """从数据库加载已有URL到Bloom Filter"""
        try:
            cursor = self._db.execute("SELECT url_hash FROM crawled_urls")
            count = 0
            for (url_hash,) in cursor:
                self.bloom.add(url_hash)
                count += 1
                if count >= self.bloom.capacity:
                    break
            logger.info(f"从磁盘加载 {count} 个URL到Bloom Filter")
        except Exception as e:
            logger.warning(f"加载URL去重数据失败: {e}")

    def _hash_url(self, url: str) -> str:
        """计算URL的哈希"""
        normalized = UrlNormalizer.normalize(url)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def is_duplicate(self, url: str) -> bool:
        """
        判断URL是否已爬取过
        返回 True 表示重复，False 表示新URL
        """
        normalized = UrlNormalizer.normalize(url)
        url_hash = self._hash_url(url)

        # 先检查内存
        if normalized in self._in_memory_seen:
            return True

        # 再检查Bloom Filter
        if self.bloom.maybe_exists(url_hash):
            # 二次确认：检查数据库
            cursor = self._db.execute(
                "SELECT 1 FROM crawled_urls WHERE url_hash = ? LIMIT 1",
                (url_hash,)
            )
            if cursor.fetchone():
                return True
            # Bloom Filter误报，添加到数据库

        return False

    def mark_crawled(
        self,
        url: str,
        content_type: str = "unknown",
        status: str = "success",
    ) -> None:
        """标记URL已爬取"""
        normalized = UrlNormalizer.normalize(url)
        url_hash = self._hash_url(url)
        domain = UrlNormalizer.get_domain(url)
        now = time.time()

        # 更新内存
        self._in_memory_seen.add(normalized)

        # 更新Bloom Filter
        self.bloom.add(url_hash)

        # 更新数据库
        try:
            self._db.execute("""
                INSERT INTO crawled_urls (url_hash, original_url, domain, content_type, first_crawled_at, last_crawled_at, crawl_count, status)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(url_hash) DO UPDATE SET
                    last_crawled_at = excluded.last_crawled_at,
                    crawl_count = crawled_urls.crawl_count + 1,
                    status = excluded.status
            """, (url_hash, normalized, domain, content_type, now, now, status))
            self._db.commit()
        except Exception as e:
            logger.warning(f"标记URL已爬取失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """获取去重统计信息"""
        try:
            total = self._db.execute("SELECT COUNT(*) FROM crawled_urls").fetchone()[0]
            domains = self._db.execute("SELECT COUNT(DISTINCT domain) FROM crawled_urls").fetchone()[0]
            today = time.time() - 86400
            today_count = self._db.execute(
                "SELECT COUNT(*) FROM crawled_urls WHERE last_crawled_at >= ?", (today,)
            ).fetchone()[0]
            return {
                "total_crawled": total,
                "unique_domains": domains,
                "today_count": today_count,
                "bloom_size": self.bloom.size,
                "memory_seen": len(self._in_memory_seen),
            }
        except Exception as e:
            logger.warning(f"获取统计信息失败: {e}")
            return {"error": str(e)}

    def cleanup_old(self, days: Optional[int] = None) -> int:
        """清理过期URL"""
        cutoff = time.time() - (days or self.retention_days) * 86400
        cursor = self._db.execute(
            "DELETE FROM crawled_urls WHERE last_crawled_at < ?",
            (cutoff,)
        )
        deleted = cursor.rowcount
        self._db.commit()
        if deleted > 0:
            logger.info(f"清理了 {deleted} 条过期URL记录")
        return deleted

    def close(self) -> None:
        """关闭数据库连接"""
        if self._db:
            self._db.close()
            self._db = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
