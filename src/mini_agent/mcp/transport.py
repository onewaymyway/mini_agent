"""
mcp/transport.py — 传输层抽象

定义 BaseTransport 接口，提供两个实现：
  StdioTransport  — 通过本地子进程 stdin/stdout 通信
  SSETransport    — 通过 HTTP SSE 与远程 server 通信

后续扩展新协议只需继承 BaseTransport，MCPManager 无需改动。

协议格式遵循 MCP 规范（mcp Python SDK）：
  https://github.com/modelcontextprotocol/python-sdk
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncIterator

from .config import MCPServerConfig


class BaseTransport(ABC):
    """所有 MCP 传输协议的抽象基类。"""

    def __init__(self, server_cfg: MCPServerConfig) -> None:
        self.cfg = server_cfg

    @abstractmethod
    @asynccontextmanager
    async def connect(self) -> AsyncIterator:
        """
        异步上下文管理器，进入时建立连接并 yield mcp.ClientSession，
        退出时自动清理资源。

        用法：
            async with transport.connect() as session:
                tools = await session.list_tools()
        """
        ...


class StdioTransport(BaseTransport):
    """通过子进程 stdin/stdout 与本地 MCP server 通信。"""

    @asynccontextmanager
    async def connect(self) -> AsyncIterator:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            raise ImportError(
                "mcp SDK not installed. Run: pip install mcp"
            )

        # 合并父进程环境变量 + server 自定义 env
        env = {**os.environ, **self.cfg.env} if self.cfg.env else None

        server_params = StdioServerParameters(
            command=self.cfg.command,
            args=self.cfg.args,
            env=env,
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


class SSETransport(BaseTransport):
    """通过 HTTP SSE 与远程 MCP server 通信。"""

    @asynccontextmanager
    async def connect(self) -> AsyncIterator:
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
        except ImportError:
            raise ImportError(
                "mcp SDK not installed. Run: pip install mcp"
            )

        async with sse_client(self.cfg.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def create_transport(server_cfg: MCPServerConfig) -> BaseTransport:
    """工厂函数：根据 transport 字段创建对应的传输层实例。"""
    transport = server_cfg.transport.lower()
    if transport == "stdio":
        return StdioTransport(server_cfg)
    elif transport == "sse":
        return SSETransport(server_cfg)
    else:
        raise ValueError(
            f"[mcp] Unknown transport type: {transport!r} "
            f"(server={server_cfg.name!r}). Supported: stdio, sse"
        )
