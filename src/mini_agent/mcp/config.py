"""
mcp/config.py — MCP 服务配置数据类

每个 MCP server 对应一个 MCPServerConfig 实例，
由 agent_config.json 的 mcp_servers 数组驱动。

支持传输协议：
  stdio  — 本地子进程（command + args），最常用
  sse    — 远程 HTTP SSE（url）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MCPServerConfig:
    """
    单个 MCP server 的连接配置。

    stdio 示例：
        MCPServerConfig(
            name="time_server",
            transport="stdio",
            command="python",
            args=["mcp_servers/time_server.py"],
        )

    sse 示例：
        MCPServerConfig(
            name="remote_tools",
            transport="sse",
            url="http://localhost:9000/sse",
        )
    """
    name: str                               # 服务唯一名称，用于工具命名空间
    transport: str = "stdio"                # "stdio" | "sse"

    # stdio 专用
    command: str = ""                       # 可执行命令，e.g. "python"
    args: list[str] = field(default_factory=list)   # 命令行参数
    env: dict[str, str] = field(default_factory=dict)  # 额外环境变量

    # sse 专用
    url: str = ""                           # SSE endpoint URL

    # 行为控制
    auto_approve: bool = False              # 此 server 的所有工具免审批
    timeout: float = 10.0                   # 连接 / 工具调用超时（秒）
    enabled: bool = True                    # False = 跳过此 server


@dataclass
class MCPConfig:
    """AppConfig 的 MCP 子配置块，持有所有 server 配置列表。"""
    servers: list[MCPServerConfig] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        """至少有一个启用的 server 时返回 True。"""
        return any(s.enabled for s in self.servers)
