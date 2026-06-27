#!/usr/bin/env python3
"""
.agent/hooks/turn_end_notify.py — TurnEnd hook 示例：一轮对话结束提示

每次 Agent 完成一轮回复，等待用户输入之前，向终端打印一行简单提示。
这个 hook 不替代用户输入（不返回 user_input），只做通知演示。

未来扩展方向：
  - 改成发送系统通知（macOS: osascript、Linux: notify-send）
  - 改成触发手机震动 / 消息推送（通过外部 API）
  - 改成返回 {"user_input": "..."} 以让另一个 agent 接管对话

stdin payload:
  {
    "assistant_output": "<本轮 assistant 最终回复文本>",
    "history": [{"role": "user"|"assistant", "content": "..."}, ...]
  }

stdout 返回：
  {} — 不做任何替代，继续等待真实用户输入
"""

import json
import sys


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    history = payload.get("history", [])
    turn_count = sum(1 for m in history if m.get("role") == "user")
    assistant_output = payload.get("assistant_output", "")

    # 截取 assistant 输出的前 60 个字符作为预览
    preview = assistant_output.replace("\n", " ").strip()
    if len(preview) > 60:
        preview = preview[:57] + "..."

    # 向 stderr 打印提示（stderr 直接到终端，不被 hook runner 解析）
    print(
        f"\n✅ [Turn {turn_count} 结束] Agent 已回复"
        + (f"：{preview}" if preview else ""),
        file=sys.stderr,
    )

    # stdout 返回空 JSON：不替代用户输入，继续正常流程
    print("{}")


if __name__ == "__main__":
    main()
