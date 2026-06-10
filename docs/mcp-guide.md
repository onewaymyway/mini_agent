# MCP 集成指南

> 说明 mini-agent 的 MCP（Model Context Protocol）支持架构、配置方式、工具命名规范与扩展方法。

---

## 1. 概述

MCP（Model Context Protocol）是一种标准化协议，允许 Agent 通过统一接口调用外部工具服务。mini-agent 的 MCP 支持遵循以下设计原则：

- **零侵入**：Agent 核心循环无需感知 MCP 的存在，MCP 工具与内置工具在 `ToolRegistry` 中完全统一
- **启动时注册**：连接和工具发现在 Agent 启动时一次完成，运行期间无额外开销
- **失败容忍**：单个 server 连接失败只打印警告，不阻断 Agent 启动
- **可扩展传输层**：`BaseTransport` 抽象支持 stdio、SSE，后续可扩展更多协议

---

## 2. 整体架构

```
AppConfig
└── mcp: MCPConfig
      └── servers: list[MCPServerConfig]
            ├── name, transport, command/args  (stdio)
            └── name, transport, url           (sse)

Agent.__init__()
└── MCPManager.register_all(registry)
      ├── 并发连接所有 enabled server
      ├── 拉取每个 server 的工具列表
      └── 注册进 ToolRegistry（group="mcp:{server_name}"）

ToolExecutor.execute_all()
└── 工具调用时判断是否为 MCP 工具
      ├── 是 → MCPManager.call_tool_sync()
      └── 否 → registry.call()（内置工具原有路径）
```

### 模块文件

```
src/mini_agent/mcp/
├── __init__.py       ← 公开接口导出
├── config.py         ← MCPServerConfig / MCPConfig 数据类
├── transport.py      ← BaseTransport / StdioTransport / SSETransport
└── manager.py        ← MCPManager（核心：连接、注册、调用路由）

mcp_servers/
└── time_server.py    ← 示例 MCP 服务（用于测试）
```

---

## 3. 配置

在项目根目录的 `agent_config.json` 中添加 `mcp_servers` 数组：

```json
{
  "mcp_servers": [
    {
      "name": "time_server",
      "transport": "stdio",
      "command": "python",
      "args": ["mcp_servers/time_server.py"],
      "auto_approve": true,
      "timeout": 10.0,
      "enabled": true
    }
  ]
}
```

### MCPServerConfig 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | str | 必填 | server 唯一标识，用于工具命名空间，建议小写下划线 |
| `transport` | str | `"stdio"` | 传输协议：`"stdio"` 或 `"sse"` |
| `command` | str | `""` | stdio 专用：可执行命令，例如 `"python"` |
| `args` | list | `[]` | stdio 专用：命令行参数，例如 `["mcp_servers/my_server.py"]` |
| `env` | dict | `{}` | stdio 专用：注入子进程的额外环境变量 |
| `url` | str | `""` | sse 专用：SSE endpoint URL |
| `auto_approve` | bool | `false` | 此 server 所有工具免审批（覆盖全局 `auto_approve`） |
| `timeout` | float | `10.0` | 连接与调用超时（秒） |
| `enabled` | bool | `true` | `false` 时跳过此 server |

### SSE server 配置示例

```json
{
  "mcp_servers": [
    {
      "name": "remote_tools",
      "transport": "sse",
      "url": "http://localhost:9000/sse",
      "auto_approve": false,
      "timeout": 15.0
    }
  ]
}
```

---

## 4. 工具命名规范

MCP 工具注册到 ToolRegistry 时，名称格式为：

```
mcp_{server_name}__{tool_name}
```

例如，`time_server` 提供的 `get_current_time` 工具，注册名为：

```
mcp_time_server__get_current_time
```

**分组**：每个 server 的工具归入独立分组 `mcp:{server_name}`，可通过 `registry.subset()` 过滤：

```python
# SubAgent 只允许使用内置工具 + time_server 工具
sub_registry = registry.subset(["builtin", "mcp:time_server"])
```

**权限**：MCP 工具默认 `requires_approval=True`。将 server 的 `auto_approve` 设为 `true`，或启动时使用全局 `--yes`，可免审批。

---

## 5. 编写 MCP 服务

MCP 服务是一个独立进程，通过 stdio 或 SSE 与 Agent 通信，遵循 MCP JSON-RPC 2.0 协议。

项目内置了一个测试服务 `mcp_servers/time_server.py`，提供三个工具：

| 工具 | 描述 |
|------|------|
| `get_current_time` | 获取当前时间，支持 IANA 时区名称 |
| `calculate` | 安全计算数学表达式（白名单 AST，无代码执行风险） |
| `echo` | 原样返回消息，用于测试连通性 |

### 快速运行测试服务

1. 安装协议实现依赖（目前 `time_server.py` 使用官方 SDK）：

```bash
pip install mcp
```

2. `agent_config.json` 已预配置，直接启动 Agent 即可：

```bash
python -m mini_agent
```

3. 启动时会看到连接成功的提示：

```
[mcp] Connected: 'time_server' (3 tools: get_current_time, calculate, echo)
```

4. 向 Agent 提问即可触发工具：

```
你好，现在几点了？上海时区
```

### 编写自己的 MCP 服务（协议规范）

MCP 服务本质是 JSON-RPC 2.0 协议，通过 stdio 的 stdin/stdout 通信：

```
Client → Server: {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
Server → Client: {"jsonrpc":"2.0","id":1,"result":{"capabilities":{...}}}

Client → Server: {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
Server → Client: {"jsonrpc":"2.0","id":2,"result":{"tools":[...]}}

Client → Server: {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"xxx","arguments":{...}}}
Server → Client: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"..."}]}}
```

任何能实现该协议的语言都可以编写 MCP 服务，不限于 Python。

---

## 6. 扩展传输协议

如需添加新的传输协议（如 WebSocket），只需继承 `BaseTransport`：

```python
# src/mini_agent/mcp/transport.py
class WebSocketTransport(BaseTransport):
    @asynccontextmanager
    async def connect(self) -> AsyncIterator:
        # 建立 WebSocket 连接
        async with websocket_client(self.cfg.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
```

然后在 `create_transport()` 工厂函数中注册：

```python
def create_transport(server_cfg: MCPServerConfig) -> BaseTransport:
    transport = server_cfg.transport.lower()
    if transport == "stdio":
        return StdioTransport(server_cfg)
    elif transport == "sse":
        return SSETransport(server_cfg)
    elif transport == "websocket":           # ← 新增
        return WebSocketTransport(server_cfg)
    ...
```

`MCPManager` 无需任何改动。

---

## 7. 调试

### 查看已注册的 MCP 工具

```python
# 在代码中
mcp_manager.list_server_tools()
# 返回 {"time_server": ["get_current_time", "calculate", "echo"]}

# 查看 registry 中的 MCP 分组
registry.names_in_group("mcp:time_server")
# 返回 ["mcp_time_server__get_current_time", ...]
```

### 启用 verbose 模式

在 `agent_config.json` 中设置 `"verbose": true`，工具调用时会输出详细的入参和返回值。

### 连接失败排查

常见原因：
- `command` 路径不对：确认 `python mcp_servers/time_server.py` 在项目根目录可正常运行
- 依赖未安装：MCP 服务需要其自身的依赖，例如 `mcp` SDK
- 超时过短：网络远程服务适当调大 `timeout`

连接失败时 Agent 会打印：
```
WARNING: [mcp] Failed to connect 'time_server' (stdio): <错误信息>
```

---

## 8. 与内置工具的对比

| 维度 | 内置工具 | MCP 工具 |
|------|---------|---------|
| 定义方式 | `@tool()` 装饰器，Python 函数 | 独立进程，JSON-RPC |
| 注册时机 | 模块 import 时 | Agent 启动时动态注册 |
| 工具命名 | 直接名称（如 `bash`） | 带命名空间（如 `mcp_time_server__get_current_time`） |
| 调用路径 | `registry.call()` | `MCPManager.call_tool_sync()` |
| 权限默认 | 按工具声明 | 默认需审批，可按 server 关闭 |
| 工具分组 | `"builtin"` 等 | `"mcp:{server_name}"` |
| 语言限制 | Python 仅 | 任何语言 |
| 进程隔离 | 同进程 | 独立进程，崩溃不影响 Agent |

---

## 9. 相关文档

- [配置系统指南](config-guide.md) — `MCPConfig` 子配置块与 `agent_config.json` 加载
- [代码结构指南](code-structure-guide.md) — `mcp/` 模块在项目中的位置
- [系统设计概述](system-overview.md) — MCP 在整体架构中的位置
- [权限系统指南](permission-guide.md) — MCP 工具的审批机制

---

*最后更新：2026-06（初版，随 MCP 支持功能新增）*
