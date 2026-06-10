"""
mini_agent.mcp — MCP (Model Context Protocol) 支持模块

公开接口：
  MCPConfig        — AppConfig 的子配置块
  MCPServerConfig  — 单个 server 的连接配置
  MCPManager       — 连接管理器，负责注册工具和代理调用
"""

from .config import MCPConfig, MCPServerConfig
from .manager import MCPManager, make_tool_name, parse_tool_name

__all__ = [
    "MCPConfig",
    "MCPServerConfig",
    "MCPManager",
    "make_tool_name",
    "parse_tool_name",
]
