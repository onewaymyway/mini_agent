#!/usr/bin/env python
"""entrypoints/fetch_iwencai_cookie.py — 看板「▶️ 手动触发」入口，包装
`tools/fetch_iwencai_cookie.py`：通过 CDP 连接真实 Chrome，引导用户手动
完成问财（iwencai）登录/验证，检测到 `hexin-v` cookie 后写入
`config/secrets.local.yaml`。

为什么需要这一层包装，而不是直接把 `tools/` 下的脚本注册进
`project.yaml`：

  1. `tools/fetch_iwencai_cookie.py` 是给人交互式跑的工具，用 argparse
     定义的是 `--port`/`--spawn`/`--timeout` 这些**选项型**参数；而
     `project.yaml` 的 `params` 机制是按声明顺序把值拼成**位置参数**
     追加在 `cmd` 后面（见 `external_projects_kanban_integration_plan.md`
     阶段6，与 `run_stock_analysis.py`/`change_pool_state.py` 现有写法
     一致）。两套参数风格对不上，直接注册会导致看板传来的位置参数被
     `argparse` 当成未知位置参数报错。这层包装负责把"看板按声明顺序传入
     的位置参数"翻译成 `tools` 脚本认识的 `--flag` 形式。
  2. `tools/` 下的脚本按设计不接入 `_common.run_entrypoint()` 账本机制
     （原因见该文件模块 docstring：它是人工交互式工具，不是
     daemon/cron 无人值守调度的对象）；但看板「▶️ 手动触发」调用的是
     `project.yaml` 声明的 entrypoint，触发本身（不代表登录/验证一定
     成功）需要走统一账本记录，所以用 `entrypoints/` 下的标准写法包一层，
     和其它 entrypoint 保持一致的可观测性。

前提条件（与本项目其它 entrypoint 不同的一点）：运行这个 entrypoint 的
机器需要有能显示窗口的桌面环境（脚本会打开一个真实 Chrome 窗口，让用户
在里面手动完成登录/验证）。如果 daemon 部署在无显示器的纯服务器上，这个
entrypoint 会直接失败（连不上调试端口 / 找不到 Chrome 可执行文件）——
详见 `PROJECT.md`"已知限制"一节。因此 `project.yaml` 里没有给它声明
`schedule`：这本来就是一次性的人工操作，不适合定时无人值守跑。

用法（与直接跑 `tools/fetch_iwencai_cookie.py` 效果等价，只是参数改成
位置式，供看板「▶️ 手动触发」渲染输入框）：

    python entrypoints/fetch_iwencai_cookie.py [port] [spawn] [timeout]

    port    可选，Chrome 调试端口，留空则用默认值 9222。
    spawn   可选，"1"/"true" 表示让脚本自己拉起一个带独立临时 profile 的
            新 Chrome 实例；留空/"0" 表示假设用户已经用调试端口手动启动
            了 Chrome（复用其默认 profile 和已有登录态）。
    timeout 可选，最多等待用户完成登录/验证的秒数，留空则用默认值 120。

命令行手动跑时如果想用更多选项（比如 `--host`/`--secrets-path`），直接
调用 `tools/fetch_iwencai_cookie.py` 本体即可，不必经过这层包装。
"""

from __future__ import annotations

import sys

import _common  # noqa: F401

_TOOLS_DIR = _common.PROJECT_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import fetch_iwencai_cookie as _tool  # noqa: E402


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else _tool.DEFAULT_PORT
    spawn = _parse_bool(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else False
    timeout = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else 120

    tool_argv = ["fetch_iwencai_cookie.py", "--port", str(port), "--timeout", str(timeout)]
    if spawn:
        tool_argv.append("--spawn")

    # 看板收到的位置参数（sys.argv[1:]）经常和使用者以为自己填的值不一致
    # ——比如某个前面的可选参数留空时，`manifest.py::build_cmd_with_params()`
    # 会连同它后面所有参数一起不追加（位置参数语义）。把"看板实际传来的
    # 原始位置参数"和"翻译后真正调用 tools 脚本的完整命令"都打印到
    # stderr，方便对照排查"我明明填了 spawn=true，怎么没生效"这类问题
    # （执行账本的 detail 字段会带上失败时的 stderr 尾部）。
    print(f"[fetch_iwencai_cookie] 收到的位置参数 sys.argv[1:]={sys.argv[1:]!r}", file=sys.stderr)
    print(
        "[fetch_iwencai_cookie] 实际调用: python tools/fetch_iwencai_cookie.py "
        + " ".join(tool_argv[1:]),
        file=sys.stderr,
    )

    old_argv = sys.argv
    sys.argv = tool_argv
    try:
        return _tool.main()
    except Exception as exc:  # noqa: BLE001 - 转成 detail，便于账本记录失败原因
        _common.set_run_detail(f"fetch_iwencai_cookie 异常: {exc}")
        raise
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("fetch_iwencai_cookie", main, trigger="manual"))
