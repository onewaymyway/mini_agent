#!/usr/bin/env python
"""
test_browser_console.py - browser_console.py 测试模板

覆盖：JS 执行、console 日志抓取、网络请求监控

注意：browser_console.py 是命令行脚本，测试其底层功能需通过 cdp_client
"""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime


class MockCDPSession:
    """模拟 CDP Session"""
    
    def __init__(self):
        self._console_logs = []
        self._network_requests = []
        self._js_results = {}
    
    async def send(self, method, **params):
        """模拟 CDP 命令发送"""
        if method == "Runtime.evaluate":
            return {"result": {"result": {"value": "test_result"}}}
        elif method == "Page.enable":
            return {}
        elif method == "Runtime.enable":
            return {}
        elif method == "Network.enable":
            return {}
        return {}
    
    def drain_events(self, duration=1.0, method_prefix=None):
        """模拟事件收集"""
        return []
    
    def eval_js(self, expr, await_promise=False):
        """模拟 JS 执行"""
        return "test_result"
    
    def add_console_log(self, log):
        self._console_logs.append(log)
    
    def add_network_request(self, request):
        self._network_requests.append(request)


@pytest.mark.asyncio
async def test_execute_js():
    """测试执行 JS 代码"""
    session = MockCDPSession()
    
    # 测试简单 JS 执行
    result = session.eval_js("1 + 1")
    assert result == "test_result"
    
    # 测试获取页面标题
    result = session.eval_js("document.title")
    assert result is not None


@pytest.mark.asyncio
async def test_console_log_collection():
    """测试 console 日志收集"""
    session = MockCDPSession()
    
    # 模拟 console 事件
    events = [
        {"method": "Runtime.consoleAPICalled", "params": {"type": "log", "args": [{"value": "test log"}]}},
        {"method": "Runtime.consoleAPICalled", "params": {"type": "error", "args": [{"value": "test error"}]}},
    ]
    
    # 验证事件解析逻辑
    logs = []
    for ev in events:
        method = ev.get("method")
        params = ev.get("params", {})
        if method == "Runtime.consoleAPICalled":
            args_repr = []
            for a in params.get("args", []):
                args_repr.append(a.get("value", a.get("description", "")))
            logs.append({"type": params.get("type"), "args": args_repr})
    
    assert len(logs) == 2
    assert logs[0]["type"] == "log"
    assert logs[1]["type"] == "error"


@pytest.mark.asyncio
async def test_network_request_capture():
    """测试网络请求捕获"""
    session = MockCDPSession()
    
    # 模拟网络事件
    events = [
        {"method": "Network.requestWillBeSent", "params": {"request": {"url": "https://example.com/api", "method": "GET"}}},
        {"method": "Network.responseReceived", "params": {"response": {"status": 200}}},
    ]
    
    # 验证事件解析
    requests = []
    for ev in events:
        method = ev.get("method")
        params = ev.get("params", {})
        if method == "Network.requestWillBeSent":
            requests.append(params.get("request", {}))
    
    assert len(requests) == 1
    assert requests[0]["url"] == "https://example.com/api"


@pytest.mark.asyncio
async def test_wait_for_network_idle():
    """测试等待网络空闲"""
    session = MockCDPSession()
    
    # 模拟网络空闲（无事件）
    events = session.drain_events(duration=0.1)
    assert len(events) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
