"""
database.py - 内容网站数据库层

提供基于SQLite的内容存储、查询和索引功能。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple

from .models import (
    Article,
    ArticleSearchResults,
    Author,
    Category,
    ContentType,
    ContentSource,
    ContentSiteProfile,
    Tag,
)

logger = logging.getLogger(__name__)


class ContentDatabase:
    """内容网站数据库管理器"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or os.path.join(
            os.path.dirname(__file__), '..', '..', 'data', 'content_db.sqlite'
        )
        self._db_path = os.path.normpath(self._db_path)
        self._init_database()

    def _init_database(self) -> None:
        """初始化数据库表结构"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 文章表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS articles (
                    article_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    content TEXT,
                    excerpt TEXT,
                    content_type TEXT DEFAULT 'blog',
                    source_type TEXT DEFAULT 'original',
                    source_domain TEXT,
                    scraped_at TEXT,
                    published_at TEXT,
                    word_count INTEGER DEFAULT 0,
                    read_time_minutes REAL DEFAULT 0,
                    like_count INTEGER DEFAULT 0,
                    comment_count INTEGER DEFAULT 0,
                    share_count INTEGER DEFAULT 0,
                    view_count INTEGER DEFAULT 0,
                    quality_score REAL DEFAULT 0,
                    is_spam INTEGER DEFAULT 0,
                    raw_data TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 作者表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS authors (
                    author_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    username TEXT,
                    avatar_url TEXT,
                    bio TEXT,
                    follower_count INTEGER DEFAULT 0,
                    article_count INTEGER DEFAULT 0,
                    website TEXT,
                    is_verified INTEGER DEFAULT 0,
                    scraped_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 分类表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    category_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    description TEXT,
                    parent_category_id TEXT,
                    article_count INTEGER DEFAULT 0,
                    sort_order INTEGER DEFAULT 0,
                    FOREIGN KEY (parent_category_id) REFERENCES categories(category_id)
                )
            ''')
            
            # 标签表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tags (
                    tag_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    article_count INTEGER DEFAULT 0,
                    description TEXT,
                    parent_tag_id TEXT,
                    FOREIGN KEY (parent_tag_id) REFERENCES tags(tag_id)
                )
            ''')
            
            # 文章-分类关联表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS article_categories (
                    article_id TEXT,
                    category_id TEXT,
                    PRIMARY KEY (article_id, category_id),
                    FOREIGN KEY (article_id) REFERENCES articles(article_id),
                    FOREIGN KEY (category_id) REFERENCES categories(category_id)
                )
            ''')
            
            # 文章-标签关联表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS article_tags (
                    article_id TEXT,
                    tag_id TEXT,
                    PRIMARY KEY (article_id, tag_id),
                    FOREIGN KEY (article_id) REFERENCES articles(article_id),
                    FOREIGN KEY (tag_id) REFERENCES tags(tag_id)
                )
            ''')
            
            # 网站档案表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS site_profiles (
                    domain TEXT PRIMARY KEY,
                    name TEXT,
                    site_type TEXT,
                    language TEXT DEFAULT 'zh',
                    description TEXT,
                    article_count INTEGER DEFAULT 0,
                    health_score REAL DEFAULT 1.0,
                    anti_crawl_level INTEGER DEFAULT 1,
                    requires_login INTEGER DEFAULT 0,
                    last_crawled TEXT,
                    config_data TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 全文搜索虚拟表
            cursor.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                    title,
                    content,
                    excerpt,
                    content='articles',
                    content_rowid='rowid'
                )
            ''')
            
            # 创建索引
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_articles_domain ON articles(source_domain)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_articles_type ON articles(content_type)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_articles_quality ON articles(quality_score)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_articles_scraped ON articles(scraped_at)')
            
            conn.commit()
        
        logger.info(f"Database initialized at {self._db_path}")

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """获取数据库连接上下文管理器"""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    # ─── 文章操作 ───

    def save_article(self, article: Article) -> bool:
        """保存文章到数据库"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 插入文章
                cursor.execute('''
                    INSERT OR REPLACE INTO articles (
                        article_id, title, url, content, excerpt,
                        content_type, source_type, source_domain,
                        scraped_at, published_at, word_count,
                        read_time_minutes, like_count, comment_count,
                        share_count, view_count, quality_score,
                        is_spam, raw_data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    article.article_id,
                    article.title[:500],
                    article.url,
                    article.content[:100000],
                    article.excerpt[:500],
                    article.content_type.value if isinstance(article.content_type, ContentType) else article.content_type,
                    article.source_type.value if isinstance(article.source_type, ContentSource) else article.source_type,
                    article.source_domain,
                    article.scraped_at,
                    article.published_at.isoformat() if article.published_at else None,
                    article.word_count,
                    article.read_time_minutes,
                    article.like_count,
                    article.comment_count,
                    article.share_count,
                    article.view_count,
                    article.quality_score,
                    1 if article.is_spam else 0,
                    json.dumps(article.metadata) if article.metadata else None,
                ))
                
                # 保存作者
                if article.author:
                    self._save_author(cursor, article.author)
                
                # 保存分类
                for category in article.categories:
                    self._save_category(cursor, category)
                    cursor.execute('''
                        INSERT OR IGNORE INTO article_categories (article_id, category_id)
                        VALUES (?, ?)
                    ''', (article.article_id, category.category_id))
                
                # 保存标签
                for tag in article.tags:
                    self._save_tag(cursor, tag)
                    cursor.execute('''
                        INSERT OR IGNORE INTO article_tags (article_id, tag_id)
                        VALUES (?, ?)
                    ''', (article.article_id, tag.tag_id))
                
                # 更新FTS索引
                cursor.execute('''
                    INSERT INTO articles_fts (rowid, title, content, excerpt)
                    VALUES (?, ?, ?, ?)
                ''', (cursor.lastrowid, article.title, article.content[:10000], article.excerpt[:500]))
                
                # 更新网站档案
                self._update_site_profile(cursor, article.source_domain)
                
                return True
        except Exception as e:
            logger.error(f"Failed to save article: {e}")
            return False

    def _save_author(self, cursor: sqlite3.Cursor, author: Author) -> None:
        """保存作者信息"""
        cursor.execute('''
            INSERT OR REPLACE INTO authors (
                author_id, name, username, avatar_url, bio,
                follower_count, article_count, website, is_verified, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            author.author_id or f"auth_{hash(author.name)}",
            author.name[:100],
            author.username[:50],
            author.avatar_url[:500],
            author.bio[:500],
            author.follower_count,
            author.article_count,
            author.website[:500],
            1 if author.is_verified else 0,
            author.scraped_at,
        ))

    def _save_category(self, cursor: sqlite3.Cursor, category: Category) -> None:
        """保存分类信息"""
        cursor.execute('''
            INSERT OR REPLACE INTO categories (
                category_id, name, slug, description,
                parent_category_id, article_count, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            category.category_id or f"cat_{hash(category.name)}",
            category.name[:100],
            category.slug[:50],
            category.description[:300],
            category.parent_category_id,
            category.article_count,
            category.sort_order,
        ))

    def _save_tag(self, cursor: sqlite3.Cursor, tag: Tag) -> None:
        """保存标签信息"""
        cursor.execute('''
            INSERT OR REPLACE INTO tags (
                tag_id, name, slug, article_count, description, parent_tag_id
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            tag.tag_id or f"tag_{hash(tag.name)}",
            tag.name[:50],
            tag.slug[:50],
            tag.article_count,
            tag.description[:200],
            tag.parent_tag_id,
        ))

    def _update_site_profile(self, cursor: sqlite3.Cursor, domain: str) -> None:
        """更新网站档案统计"""
        cursor.execute('SELECT COUNT(*) FROM articles WHERE source_domain = ?', (domain,))
        count = cursor.fetchone()[0]
        
        cursor.execute('''
            INSERT OR REPLACE INTO site_profiles (
                domain, article_count, last_crawled, updated_at
            ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ''', (domain, count, datetime.now().isoformat()))

    def get_article(self, article_id: str) -> Optional[Article]:
        """根据ID获取文章"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM articles WHERE article_id = ?', (article_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return self._row_to_article(row, cursor)

    def get_article_by_url(self, url: str) -> Optional[Article]:
        """根据URL获取文章"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM articles WHERE url = ?', (url,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return self._row_to_article(row, cursor)

    def _row_to_article(self, row: sqlite3.Row, cursor: Optional[sqlite3.Cursor] = None) -> Article:
        """将数据库行转换为Article对象"""
        article = Article(
            article_id=row['article_id'],
            title=row['title'],
            url=row['url'],
            content=row['content'] or '',
            excerpt=row['excerpt'] or '',
            content_type=ContentType(row['content_type']) if row['content_type'] in [e.value for e in ContentType] else ContentType.BLOG,
            source_type=ContentSource(row['source_type']) if row['source_type'] in [e.value for e in ContentSource] else ContentSource.ORIGINAL,
            source_domain=row['source_domain'],
            scraped_at=row['scraped_at'],
            word_count=row['word_count'] or 0,
            read_time_minutes=row['read_time_minutes'] or 0,
            like_count=row['like_count'] or 0,
            comment_count=row['comment_count'] or 0,
            share_count=row['share_count'] or 0,
            view_count=row['view_count'] or 0,
            quality_score=row['quality_score'] or 0,
            is_spam=bool(row['is_spam']),
            metadata=json.loads(row['raw_data']) if row['raw_data'] else {},
        )
        
        if row['published_at']:
            try:
                article.published_at = datetime.fromisoformat(row['published_at'])
            except ValueError:
                pass
        
        # 加载关联数据
        if cursor:
            article.author = self._load_author(cursor, article.article_id)
            article.categories = self._load_categories(cursor, article.article_id)
            article.tags = self._load_tags(cursor, article.article_id)
        
        return article

    def _load_author(self, cursor: sqlite3.Cursor, article_id: str) -> Optional[Author]:
        """加载文章作者"""
        # 直接查询authors表，不关联articles
        cursor.execute('''
            SELECT * FROM authors
            WHERE author_id = (
                SELECT author_id FROM articles WHERE article_id = ? LIMIT 1
            )
        ''', (article_id,))
        row = cursor.fetchone()
        
        if not row:
            return None
        
        return Author(
            author_id=row['author_id'],
            name=row['name'],
            username=row['username'],
            avatar_url=row['avatar_url'],
            bio=row['bio'],
            follower_count=row['follower_count'] or 0,
            article_count=row['article_count'] or 0,
            website=row['website'],
            is_verified=bool(row['is_verified']),
        )

    def _load_categories(self, cursor: sqlite3.Cursor, article_id: str) -> List[Category]:
        """加载文章分类"""
        cursor.execute('''
            SELECT c.* FROM categories c
            JOIN article_categories ac ON c.category_id = ac.category_id
            WHERE ac.article_id = ?
        ''', (article_id,))
        
        return [Category(
            category_id=row['category_id'],
            name=row['name'],
            slug=row['slug'],
            description=row['description'],
            article_count=row['article_count'] or 0,
        ) for row in cursor.fetchall()]

    def _load_tags(self, cursor: sqlite3.Cursor, article_id: str) -> List[Tag]:
        """加载文章标签"""
        cursor.execute('''
            SELECT t.* FROM tags t
            JOIN article_tags at ON t.tag_id = at.tag_id
            WHERE at.article_id = ?
        ''', (article_id,))
        
        return [Tag(
            tag_id=row['tag_id'],
            name=row['name'],
            slug=row['slug'],
            article_count=row['article_count'] or 0,
            description=row['description'],
        ) for row in cursor.fetchall()]

    # ─── 搜索操作 ───

    def search_articles(
        self,
        query: str,
        page: int = 1,
        page_size: int = 20,
        content_type: Optional[str] = None,
        domain: Optional[str] = None,
        min_quality: float = 0.0,
    ) -> ArticleSearchResults:
        """搜索文章（FTS5优先，中文回退LIKE）"""
        import re as _re
        start_time = time.time()
        articles = []
        total_count = 0
        used_fts = False

        # 判断是否为中文为主的查询
        cn_chars = len(_re.findall(r'[\u4e00-\u9fff]', query))
        is_chinese_query = cn_chars > 0 and len(query) <= 20

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 构建基础查询条件
            conditions = ['quality_score >= ?', 'is_spam = 0']
            params = [min_quality]

            if domain:
                conditions.append('source_domain = ?')
                params.append(domain)

            if content_type:
                conditions.append('content_type = ?')
                params.append(content_type)

            where_clause = ' AND '.join(conditions)

            if query and not is_chinese_query:
                # FTS5路径（英文/混合查询）
                try:
                    cursor.execute(f'''
                        SELECT articles.* FROM articles_fts
                        JOIN articles ON articles_fts.rowid = articles.rowid
                        WHERE articles_fts MATCH ?
                        AND {where_clause}
                        ORDER BY articles_fts.rank, articles.quality_score DESC, articles.scraped_at DESC
                        LIMIT ? OFFSET ?
                    ''', [query, page_size, (page - 1) * page_size] + params)
                    articles = [self._row_to_article(row, cursor) for row in cursor.fetchall()]
                    cursor.execute(f'''
                        SELECT COUNT(*) FROM articles_fts
                        JOIN articles ON articles_fts.rowid = articles.rowid
                        WHERE articles_fts MATCH ?
                        AND {where_clause}
                    ''', [query] + params)
                    total_count = cursor.fetchone()[0]
                    used_fts = True
                except Exception:
                    pass

            if not used_fts and query and is_chinese_query:
                # 中文回退：LIKE匹配title/content/excerpt
                like_clause = ' OR '.join([
                    f'title LIKE "%{query}%"',
                    f'content LIKE "%{query}%"',
                    f'excerpt LIKE "%{query}%"',
                ])
                full_where = f'({where_clause}) AND ({like_clause})'
                cursor.execute(f'''
                    SELECT * FROM articles WHERE {full_where}
                    ORDER BY quality_score DESC, scraped_at DESC
                    LIMIT ? OFFSET ?
                ''', params + [page_size, (page - 1) * page_size])
                articles = [self._row_to_article(row, cursor) for row in cursor.fetchall()]
                cursor.execute(f'SELECT COUNT(*) FROM articles WHERE {full_where}', params)
                total_count = cursor.fetchone()[0]
            elif not query:
                cursor.execute(f'''
                    SELECT * FROM articles
                    WHERE {where_clause}
                    ORDER BY quality_score DESC, scraped_at DESC
                    LIMIT ? OFFSET ?
                ''', params + [page_size, (page - 1) * page_size])
                articles = [self._row_to_article(row, cursor) for row in cursor.fetchall()]
                cursor.execute(f'SELECT COUNT(*) FROM articles WHERE {where_clause}', params)
                total_count = cursor.fetchone()[0]
            elif used_fts:
                cursor.execute(f'''
                    SELECT COUNT(*) FROM articles_fts
                    JOIN articles ON articles_fts.rowid = articles.rowid
                    WHERE articles_fts MATCH ?
                    AND {where_clause}
                ''', [query] + params)
                total_count = cursor.fetchone()[0]

        latency_ms = (time.time() - start_time) * 1000

        return ArticleSearchResults(
            success=True,
            query=query,
            articles=articles,
            total_count=total_count,
            page=page,
            page_size=page_size,
            latency_ms=round(latency_ms, 2),
        )

    # ─── 统计操作 ───

    def get_site_stats(self, domain: Optional[str] = None) -> Dict[str, Any]:
        """获取网站统计信息"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if domain:
                cursor.execute('''
                    SELECT 
                        COUNT(*) as article_count,
                        AVG(quality_score) as avg_quality,
                        SUM(word_count) as total_words,
                        MAX(scraped_at) as last_scraped
                    FROM articles
                    WHERE source_domain = ?
                ''', (domain,))
                row = cursor.fetchone()
                
                return {
                    'domain': domain,
                    'article_count': row[0] or 0,
                    'avg_quality': round(row[1] or 0, 2),
                    'total_words': row[2] or 0,
                    'last_scraped': row[3],
                }
            else:
                cursor.execute('''
                    SELECT 
                        source_domain,
                        COUNT(*) as article_count,
                        AVG(quality_score) as avg_quality
                    FROM articles
                    GROUP BY source_domain
                    ORDER BY article_count DESC
                ''')
                
                stats = {}
                for row in cursor.fetchall():
                    if row[0]:
                        stats[row[0]] = {
                            'article_count': row[1],
                            'avg_quality': round(row[2] or 0, 2),
                        }
                
                return stats

    def get_top_articles(self, limit: int = 10) -> List[Article]:
        """获取质量最高的文章"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM articles
                WHERE quality_score >= 0.7
                AND is_spam = 0
                ORDER BY quality_score DESC, scraped_at DESC
                LIMIT ?
            ''', (limit,))
            
            return [self._row_to_article(row, cursor) for row in cursor.fetchall()]

    # ─── 数据库维护 ───

    def optimize_database(self) -> bool:
        """优化数据库性能"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('PRAGMA optimize')
            cursor.execute('PRAGMA incremental Vacuum')
            return True

    def cleanup_old_articles(self, days: int = 365) -> int:
        """清理过期文章"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cutoff_date = (datetime.now().timestamp() - days * 86400)
            
            # 删除文章
            cursor.execute('''
                DELETE FROM articles
                WHERE scraped_at < datetime(?, 'unixepoch', 'localtime')
            ''', (datetime.fromtimestamp(cutoff_date).isoformat(),))
            deleted = cursor.rowcount
            
            # 清理FTS索引
            cursor.execute('DELETE FROM articles_fts WHERE rowid NOT IN (SELECT rowid FROM articles)')
            
            return deleted

    def get_all_articles(self, limit: int = 1000) -> List[Article]:
        """获取所有文章（用于数据完整性验证）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM articles ORDER BY scraped_at DESC LIMIT ?',
                (limit,),
            )
            rows = cursor.fetchall()
            articles = []
            for row in rows:
                article = self._row_to_article(row, None)
                articles.append(article)
            return articles

    def get_database_info(self) -> Dict[str, Any]:
        """获取数据库基本信息"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 文章总数
            cursor.execute('SELECT COUNT(*) FROM articles')
            total_articles = cursor.fetchone()[0]

            # 域名数量
            cursor.execute(
                'SELECT COUNT(DISTINCT source_domain) FROM articles WHERE source_domain IS NOT NULL'
            )
            total_domains = cursor.fetchone()[0]

            # 数据库文件大小
            db_size = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0

            return {
                "database_path": self._db_path,
                "total_articles": total_articles,
                "total_domains": total_domains,
                "database_size_mb": round(db_size / 1024 / 1024, 2),
                "last_optimized": None,
            }


