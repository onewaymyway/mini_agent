"""
Web界面层 - FastAPI服务
提供REST API接口和Web控制台
"""
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import asyncio
import logging
from pathlib import Path

from .auth_module import AuthManager, WebsiteManager
from .content_service import ContentDetailService

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Browser-CDP Enhanced API",
    description="可扩展网站抓取和浏览系统API",
    version="2.0.0"
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局实例
auth_manager: Optional[AuthManager] = None
content_service: Optional[ContentDetailService] = None


class SearchRequest(BaseModel):
    """搜索请求"""
    website: str
    query: str
    filters: Optional[Dict[str, Any]] = None
    limit: int = 20


class CrawlRequest(BaseModel):
    """爬取请求"""
    url: str
    wait_time: int = 3
    screenshot: bool = False
    extract_links: bool = True
    extract_images: bool = False


class LoginRequest(BaseModel):
    """登录请求"""
    website: str
    username: str
    password: str
    captcha_solution: Optional[str] = None


class SearchResult(BaseModel):
    """搜索结果"""
    success: bool
    data: List[Dict[str, Any]]
    total: int
    query: str
    website: str
    took_ms: int


class CrawlResult(BaseModel):
    """爬取结果"""
    success: bool
    url: str
    title: str
    content: str
    links: List[str]
    screenshot_path: Optional[str] = None
    took_ms: int


@app.on_event("startup")
async def startup():
    """启动时初始化服务"""
    global auth_manager, content_service
    
    auth_manager = AuthManager(Path("data/auth"))
    content_service = ContentDetailService()
    
    logger.info("Browser-CDP Enhanced API started")


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "sessions": len(auth_manager.sessions) if auth_manager else 0,
        "version": "2.0.0"
    }


@app.post("/search", response_model=SearchResult)
async def search(request: SearchRequest):
    """执行搜索"""
    if not content_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    start_time = asyncio.get_event_loop().time()
    
    try:
        results = await content_service.search(
            website=request.website,
            query=request.query,
            filters=request.filters,
            limit=request.limit
        )
        
        took_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
        
        return SearchResult(
            success=True,
            data=results.get("results", []),
            total=len(results.get("results", [])),
            query=request.query,
            website=request.website,
            took_ms=took_ms
        )
    
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/crawl", response_model=CrawlResult)
async def crawl(request: CrawlRequest):
    """爬取网页"""
    if not content_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    start_time = asyncio.get_event_loop().time()
    
    try:
        result = await content_service.crawl(
            url=request.url,
            wait_time=request.wait_time,
            screenshot=request.screenshot,
            extract_links=request.extract_links,
            extract_images=request.extract_images
        )
        
        took_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
        
        return CrawlResult(
            success=True,
            url=result.get("url", ""),
            title=result.get("title", ""),
            content=result.get("content", ""),
            links=result.get("links", []),
            screenshot_path=result.get("screenshot_path"),
            took_ms=took_ms
        )
    
    except Exception as e:
        logger.error(f"Crawl error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/login")
async def login(request: LoginRequest):
    """执行登录"""
    if not session_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    try:
        session_id = await session_manager.login(
            website=request.website,
            username=request.username,
            password=request.password,
            captcha_solution=request.captcha_solution
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "message": "Login successful"
        }
    
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions")
async def list_sessions():
    """列出所有会话"""
    if not session_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    sessions = []
    for sid, session in session_manager.sessions.items():
        sessions.append({
            "session_id": sid,
            "website": session.website,
            "is_authenticated": session.is_authenticated,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "last_used": session.last_used.isoformat() if session.last_used else None
        })
    
    return {"sessions": sessions, "total": len(sessions)}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    if not session_manager:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    success = session_manager.close_session(session_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"success": True, "message": f"Session {session_id} deleted"}


@app.get("/stats")
async def get_stats():
    """获取统计信息"""
    if not session_manager or not content_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    return {
        "total_sessions": len(session_manager.sessions),
        "authenticated_sessions": sum(1 for s in session_manager.sessions.values() if s.is_authenticated),
        "cache_size": len(content_service.cache),
        "uptime_seconds": 0
    }
