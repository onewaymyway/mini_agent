"""
mcp/manager.py — MCP 连接管理器

职责：
  1. 按配置连接所有 MCP server（支持 stdio / sse）
  2. 从每个 server 拉取工具列表，转换为 ToolDef 注册进 ToolRegistry
  3. 代理工具调用（ToolExecutor 调用 MCP 工具时路由到此）
  4. 优雅关闭（cleanup 释放所有子进程/连接）

设计要点：
  - 单个 server 连接失败不阻断启动，打印警告后跳过
  - 工具命名格式：mcp_{server_name}__{tool_name}（防命名冲突）
  - 工具分组：group="mcp:{server_name}"（支持 SubAgent subset 过滤）
  - 每次工具调用动态开启短连接（stdio server 不能长驻），或复用长连接（SSE）
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any, Optional

from .config import MCPServerConfig, MCPConfig
from .transport import create_transport


# ── 工具名辅助函数 ────────────────────────────────────────────────────────────

def make_tool_name(server_name: str, tool_name: str) -> str:
    """MCP 工具注册名：mcp_{server}__{tool}"""
    return f"mcp_{server_name}__{tool_name}"


def parse_tool_name(full_name: str) -> tuple[str, str] | None:
    """从注册名还原 (server_name, tool_name)，非 MCP 工具返回 None。"""
    if not full_name.startswith("mcp_"):
        return None
    rest = full_name[4:]  # 去掉 "mcp_"
    if "__" not in rest:
        return None
    server_name, tool_name = rest.split("__", 1)
    return server_name, tool_name


# ── MCPManager ────────────────────────────────────────────────────────────────

class MCPManager:
    """
    管理所有 MCP server 的生命周期与工具调用。

    典型用法（在 Agent.__init__ 中）：
        self._mcp = MCPManager(cfg.mcp, cfg.auto_approve)
        self._mcp.register_all(self.registry)   # 同步入口，内部驱动 asyncio

    工具调用（在 ToolExecutor 中）：
        if self._mcp and self._mcp.is_mcp_tool(name):
            result = self._mcp.call_tool_sync(name, tool_input)
    """

    def __init__(self, mcp_cfg: MCPConfig, global_auto_approve: bool = False) -> None:
        self._cfg = mcp_cfg
        self._global_auto_approve = global_auto_approve
        # server_name -> MCPServerConfig（只保留注册成功的）
        self._active_servers: dict[str, MCPServerConfig] = {}
        # server_name -> list of raw tool defs（缓存，供调试用）
        self._server_tools: dict[str, list[dict]] = {}

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def register_all(self, registry) -> None:
        """
        连接所有 enabled 的 MCP server，把工具注册进 ToolRegistry。
        同步方法，内部使用 asyncio 驱动（兼容无 event loop 的同步主循环）。
        """
        enabled = [s for s in self._cfg.servers if s.enabled]
        if not enabled:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # 在已有 event loop 中（例如 Jupyter / async main）
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(
                self._register_all_async(registry), loop
            )
            future.result(timeout=30)
        else:
            # 没有运行中的 event loop，新建一个跑完即可
            asyncio.run(self._register_all_async(registry))

    def is_mcp_tool(self, tool_name: str) -> bool:
        """判断工具名是否属于 MCP 工具。"""
        return tool_name.startswith("mcp_") and "__" in tool_name[4:]

    def call_tool_sync(self, tool_name: str, tool_input: dict) -> str:
        """
        同步调用 MCP 工具。在 ToolExecutor 的同步上下文中使用。
        每次调用开启短连接，调用完毕后关闭（stdio 不支持长驻）。
        """
        parsed = parse_tool_name(tool_name)
        if parsed is None:
            return f"[mcp] Invalid tool name: {tool_name!r}"

        server_name, raw_tool_name = parsed
        server_cfg = self._active_servers.get(server_name)
        if server_cfg is None:
            return f"[mcp] Server {server_name!r} not connected."

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                future = asyncio.run_coroutine_threadsafe(
                    self._call_tool_async(server_cfg, raw_tool_name, tool_input), loop
                )
                return future.result(timeout=server_cfg.timeout)
            else:
                return loop.run_until_complete(
                    self._call_tool_async(server_cfg, raw_tool_name, tool_input)
                )
        except RuntimeError:
            return asyncio.run(
                self._call_tool_async(server_cfg, raw_tool_name, tool_input)
            )
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.mcp.manager.MCPManager.call_tool_sync')
            return f"[mcp] Tool call failed ({tool_name}): {e}"

    def list_server_tools(self) -> dict[str, list[str]]:
        """返回 {server_name: [tool_name, ...]} 的可读摘要（用于调试）。"""
        return {
            name: [t["name"] for t in tools]
            for name, tools in self._server_tools.items()
        }

    # ── 内部异步实现 ──────────────────────────────────────────────────────────

    async def _register_all_async(self, registry) -> None:
        """并发连接所有 server，拉取工具并注册。"""
        tasks = [
            self._register_server(server_cfg, registry)
            for server_cfg in self._cfg.servers
            if server_cfg.enabled
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _register_server(self, server_cfg: MCPServerConfig, registry) -> None:
        """连接单个 server，拉取工具列表并注册进 registry。失败时打印警告。"""
        from mini_agent.tools.__init__ import ToolDef  # 延迟导入避免循环

        try:
            transport = create_transport(server_cfg)
            async with transport.connect() as session:
                result = await session.list_tools()
                tools = result.tools  # list[mcp.types.Tool]

            raw_tools = []
            for mcp_tool in tools:
                registered_name = make_tool_name(server_cfg.name, mcp_tool.name)
                group = f"mcp:{server_cfg.name}"

                # 把 MCP tool 的 inputSchema 转为 ToolDef.input_schema
                input_schema = _convert_schema(mcp_tool.inputSchema)

                # 构造调用闭包（捕获当前 server_cfg 和 tool name）
                def make_fn(srv_cfg=server_cfg, tname=mcp_tool.name):
                    def _fn(**kwargs) -> str:
                        return self.call_tool_sync(
                            make_tool_name(srv_cfg.name, tname), kwargs
                        )
                    _fn.__name__ = make_tool_name(srv_cfg.name, tname)
                    return _fn

                # auto_approve：server 级 > 全局 auto_approve
                needs_approval = not (server_cfg.auto_approve or self._global_auto_approve)

                tool_def = ToolDef(
                    name=registered_name,
                    description=mcp_tool.description or f"MCP tool: {mcp_tool.name}",
                    fn=make_fn(),
                    input_schema=input_schema,
                    requires_approval=needs_approval,
                    group=group,
                )
                registry.register(tool_def, override=True)
                raw_tools.append({
                    "name": mcp_tool.name,
                    "description": mcp_tool.description,
                })

            # 记录成功连接的 server
            self._active_servers[server_cfg.name] = server_cfg
            self._server_tools[server_cfg.name] = raw_tools

            _print_info(
                f"[mcp] Connected: {server_cfg.name!r} "
                f"({len(raw_tools)} tools: {', '.join(t['name'] for t in raw_tools)})"
            )

        except ImportError as e:
            _print_warning(f"[mcp] Skipping {server_cfg.name!r}: {e}")
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.mcp.manager.MCPManager._register_server')
            _print_warning(
                f"[mcp] Failed to connect {server_cfg.name!r} "
                f"({server_cfg.transport}): {e}"
            )

    async def _call_tool_async(
        self, server_cfg: MCPServerConfig, tool_name: str, tool_input: dict
    ) -> str:
        """异步调用 MCP 工具，返回文本结果。"""
        import asyncio as _asyncio

        transport = create_transport(server_cfg)
        async with transport.connect() as session:
            result = await _asyncio.wait_for(
                session.call_tool(tool_name, tool_input),
                timeout=server_cfg.timeout,
            )

        # 提取文本内容
        return _extract_text(result)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _convert_schema(mcp_schema) -> dict:
    """
    将 MCP tool 的 inputSchema（可能是 dict 或 pydantic model）
    转换为标准 JSON Schema dict。
    """
    if mcp_schema is None:
        return {"type": "object", "properties": {}}
    if isinstance(mcp_schema, dict):
        return mcp_schema
    # pydantic model 或带 model_dump / dict 方法的对象
    if hasattr(mcp_schema, "model_dump"):
        return mcp_schema.model_dump()
    if hasattr(mcp_schema, "dict"):
        return mcp_schema.dict()
    return {"type": "object", "properties": {}}


def _extract_text(call_result) -> str:
    """从 mcp.types.CallToolResult 中提取可读文本。"""
    if call_result is None:
        return ""
    # 标准 MCP result：.content 是 list[TextContent | ImageContent | ...]
    if hasattr(call_result, "content"):
        parts = []
        for item in call_result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
            elif hasattr(item, "data"):
                parts.append(f"[binary data, {len(item.data)} bytes]")
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(call_result)


def _print_info(msg: str) -> None:
    try:
        from mini_agent.ui.renderer import R
        R.print_info(msg)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.mcp.manager._print_info')
        print(msg)


def _print_warning(msg: str) -> None:
    try:
        from mini_agent.ui.renderer import R
        R.print_warning(msg)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.mcp.manager._print_warning')
        print(f"WARNING: {msg}")
