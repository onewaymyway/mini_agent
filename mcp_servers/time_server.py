#!/usr/bin/env python3
"""
mcp_servers/time_server.py — 测试用 MCP 服务

提供三个工具，覆盖 MCP 集成的典型场景：
  get_current_time(timezone?)  — 获取当前时间（可选时区）
  calculate(expression)        — 安全计算数学表达式
  echo(message)                — 原样返回消息（最简连通测试）

运行方式：
  python mcp_servers/time_server.py

agent_config.json 配置：
  {
    "mcp_servers": [
      {
        "name": "time_server",
        "transport": "stdio",
        "command": "python",
        "args": ["mcp_servers/time_server.py"],
        "auto_approve": true
      }
    ]
  }
"""

import ast
import math
import operator
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 使用官方 mcp SDK
try:
    import mcp.server.stdio
    import mcp.types as types
    from mcp.server import Server
except ImportError:
    print(
        "ERROR: mcp SDK not installed.\n"
        "Run: pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)


# ── Server 初始化 ─────────────────────────────────────────────────────────────

app = Server("time_server")


# ── 工具列表声明 ──────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_current_time",
            description=(
                "获取当前日期和时间。可选传入 IANA 时区名称（如 Asia/Shanghai、"
                "America/New_York）；不传时返回系统本地时间。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA 时区名称，例如 Asia/Shanghai。留空则使用系统时区。",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="calculate",
            description=(
                "安全计算数学表达式，支持四则运算、幂次（**）、"
                "取模（%）以及常用数学函数（sqrt、log、sin、cos、tan、abs、round）。"
                "不支持代码执行，防止注入。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要计算的数学表达式，例如：(3 + 4) * 2 或 sqrt(144)",
                    }
                },
                "required": ["expression"],
            },
        ),
        types.Tool(
            name="echo",
            description="将输入消息原样返回，附加服务器标识。用于测试 MCP 通道连通性。",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "要回显的消息内容",
                    }
                },
                "required": ["message"],
            },
        ),
    ]


# ── 工具实现 ──────────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    if name == "get_current_time":
        return [types.TextContent(type="text", text=_get_current_time(arguments))]
    elif name == "calculate":
        return [types.TextContent(type="text", text=_calculate(arguments))]
    elif name == "echo":
        return [types.TextContent(type="text", text=_echo(arguments))]
    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name!r}")]


# ── 工具逻辑 ──────────────────────────────────────────────────────────────────

def _get_current_time(args: dict) -> str:
    tz_name = args.get("timezone", "").strip()
    try:
        if tz_name:
            tz = ZoneInfo(tz_name)
            now = datetime.now(tz)
            tz_label = tz_name
        else:
            now = datetime.now().astimezone()
            tz_label = str(now.tzinfo or "local")

        return (
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({tz_label}, 星期{['一','二','三','四','五','六','日'][now.weekday()]})"
        )
    except ZoneInfoNotFoundError:
        return f"错误：未知时区 {tz_name!r}。请使用 IANA 格式，例如 Asia/Shanghai。"
    except Exception as e:
        return f"获取时间失败：{e}"


# 安全计算器：只允许白名单节点
_SAFE_NAMES = {
    "sqrt": math.sqrt,
    "log": math.log,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "round": round,
    "pi": math.pi,
    "e": math.e,
}

_SAFE_NODE_TYPES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
    ast.Mod, ast.Pow, ast.USub, ast.UAdd,
    ast.Call, ast.Name,
)


def _safe_eval(expr: str) -> float:
    """解析并安全求值数学表达式，不允许任何代码执行。"""
    tree = ast.parse(expr.strip(), mode="eval")

    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_NODE_TYPES):
            raise ValueError(f"不支持的操作：{type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _SAFE_NAMES:
            raise ValueError(f"未知标识符：{node.id!r}")

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            ops = {
                ast.Add: operator.add, ast.Sub: operator.sub,
                ast.Mult: operator.mul, ast.Div: operator.truediv,
                ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
                ast.Pow: operator.pow,
            }
            fn = ops.get(type(node.op))
            if fn is None:
                raise ValueError(f"未知运算符：{type(node.op).__name__}")
            return fn(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return -_eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +_eval(node.operand)
        if isinstance(node, ast.Call):
            fn_name = node.func.id if isinstance(node.func, ast.Name) else None
            if fn_name not in _SAFE_NAMES:
                raise ValueError(f"不允许调用：{fn_name!r}")
            fn = _SAFE_NAMES[fn_name]
            return fn(*[_eval(a) for a in node.args])
        if isinstance(node, ast.Name):
            if node.id in _SAFE_NAMES:
                return _SAFE_NAMES[node.id]
        raise ValueError(f"无法计算节点：{ast.dump(node)}")

    return _eval(tree)


def _calculate(args: dict) -> str:
    expr = args.get("expression", "").strip()
    if not expr:
        return "错误：表达式不能为空。"
    try:
        result = _safe_eval(expr)
        # 整数结果去掉小数点
        if isinstance(result, float) and result == int(result):
            return f"{expr} = {int(result)}"
        return f"{expr} = {result}"
    except ZeroDivisionError:
        return "错误：除数不能为零。"
    except ValueError as e:
        return f"计算错误：{e}"
    except Exception as e:
        return f"计算失败：{e}"


def _echo(args: dict) -> str:
    message = args.get("message", "")
    return f"[time_server echo] {message}"


# ── 入口 ──────────────────────────────────────────────────────────────────────

async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
