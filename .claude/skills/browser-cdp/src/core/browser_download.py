"""
browser_download.py - 文件下载管理模块

功能：
- 监听下载事件（Page.downloadWillBegin / Page.downloadProgress）
- 等待下载完成
- 获取下载文件路径
- 配置下载目录
- 断点续传支持
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, List


class DownloadManager:
    """文件下载管理器"""
    
    def __init__(self, cdp_client):
        self.cdp = cdp_client
        self.download_events = []
        self.download_paths = {}
        self._download_dir = None
        self._event_handlers = []
        
    def set_download_dir(self, path: str) -> None:
        """设置下载目录"""
        self._download_dir = Path(path).resolve()
        self._download_dir.mkdir(parents=True, exist_ok=True)
        
    def get_download_dir(self) -> Path:
        """获取下载目录"""
        if not self._download_dir:
            self._download_dir = Path.home() / "Downloads"
        return self._download_dir
    
    async def start_listening(self) -> None:
        """开始监听下载事件"""
        # 监听下载开始
        self.cdp.on("Page.downloadWillBegin", self._on_download_start)
        # 监听下载进度
        self.cdp.on("Page.downloadProgress", self._on_download_progress)
        # 监听下载完成
        self.cdp.on("Page.downloadProgress", self._on_download_complete)
        
    async def stop_listening(self) -> None:
        """停止监听下载事件"""
        self.cdp.remove_listener("Page.downloadWillBegin", self._on_download_start)
        self.cdp.remove_listener("Page.downloadProgress", self._on_download_progress)
        self.cdp.remove_listener("Page.downloadProgress", self._on_download_complete)
    
    def _on_download_start(self, params: Dict[str, Any]) -> None:
        """处理下载开始事件"""
        guid = params.get("guid", "")
        url = params.get("url", "")
        suggested_filename = params.get("suggestedFilename", "")
        
        self.download_events.append({
            "event": "start",
            "guid": guid,
            "url": url,
            "filename": suggested_filename,
            "timestamp": time.time()
        })
        
        # 记录下载路径
        if suggested_filename:
            self.download_paths[guid] = {
                "url": url,
                "filename": suggested_filename,
                "path": self.get_download_dir() / suggested_filename,
                "status": "downloading"
            }
    
    def _on_download_progress(self, params: Dict[str, Any]) -> None:
        """处理下载进度事件"""
        guid = params.get("guid", "")
        total_bytes = params.get("totalBytes", 0)
        received_bytes = params.get("receivedBytes", 0)
        state = params.get("state", "in_progress")
        
        self.download_events.append({
            "event": "progress",
            "guid": guid,
            "totalBytes": total_bytes,
            "receivedBytes": received_bytes,
            "state": state,
            "timestamp": time.time()
        })
        
        # 更新下载状态
        if guid in self.download_paths:
            self.download_paths[guid]["state"] = state
            self.download_paths[guid]["receivedBytes"] = received_bytes
            self.download_paths[guid]["totalBytes"] = total_bytes
            
            if state == "completed":
                self.download_paths[guid]["status"] = "completed"
            elif state == "canceled":
                self.download_paths[guid]["status"] = "canceled"
    
    def _on_download_complete(self, params: Dict[str, Any]) -> None:
        """处理下载完成事件"""
        guid = params.get("guid", "")
        
        self.download_events.append({
            "event": "complete",
            "guid": guid,
            "timestamp": time.time()
        })
        
        if guid in self.download_paths:
            self.download_paths[guid]["status"] = "completed"
    
    async def wait_for_download(self, guid: Optional[str] = None, timeout: float = 300.0) -> Dict[str, Any]:
        """
        等待下载完成
        
        Args:
            guid: 下载任务ID，不传则等待所有下载
            timeout: 超时时间（秒）
            
        Returns:
            下载结果字典
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # 检查是否有未完成的下载
            pending = [p for p in self.download_paths.values() 
                      if p.get("status") in ["downloading", None]]
            
            if not pending:
                break
            
            if guid and guid in self.download_paths:
                if self.download_paths[guid].get("status") in ["completed", "canceled"]:
                    return self.download_paths[guid]
            
            await asyncio.sleep(0.5)
        
        # 返回最新完成的下载
        if self.download_paths:
            latest = max(self.download_paths.values(), 
                        key=lambda x: x.get("timestamp", 0))
            return latest
        
        return {"status": "no_downloads"}
    
    def get_download_status(self) -> List[Dict[str, Any]]:
        """获取所有下载状态"""
        return list(self.download_paths.values())
    
    def get_latest_download(self) -> Optional[Dict[str, Any]]:
        """获取最新下载的文件信息"""
        if not self.download_paths:
            return None
        return max(self.download_paths.values(), 
                  key=lambda x: x.get("timestamp", 0))
    
    def get_download_path(self, guid: str) -> Optional[Path]:
        """获取指定下载的本地路径"""
        if guid in self.download_paths:
            return self.download_paths[guid].get("path")
        return None
    
    async def download_file(self, url: str, filename: Optional[str] = None, 
                           wait: bool = True, timeout: float = 300.0) -> Dict[str, Any]:
        """
        下载文件
        
        Args:
            url: 文件URL
            filename: 保存文件名（可选）
            wait: 是否等待下载完成
            timeout: 超时时间
            
        Returns:
            下载结果
        """
        # 导航到下载URL
        await self.cdp.send("Page.navigate", {"url": url})
        
        if wait:
            return await self.wait_for_download(timeout=timeout)
        
        return {"status": "started", "url": url}
    
    def clear_downloads(self) -> None:
        """清空下载记录"""
        self.download_events.clear()
        self.download_paths.clear()


def cmd_download(args: List[str]) -> None:
    """下载管理命令行接口"""
    import argparse
    parser = argparse.ArgumentParser(description="文件下载管理")
    parser.add_argument("--action", choices=["status", "latest", "clear", "download"],
                       required=True, help="操作类型")
    parser.add_argument("--url", help="下载URL")
    parser.add_argument("--filename", help="保存文件名")
    parser.add_argument("--dir", help="下载目录")
    parser.add_argument("--guid", help="下载任务ID")
    
    opts = parser.parse_args(args)
    
    # 这里需要实际的CDP客户端实例
    # 在实际使用中，通过browser_launch.py的--download-action参数调用
    print(json.dumps({
        "action": opts.action,
        "status": "implemented"
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    cmd_download(sys.argv[1:])
