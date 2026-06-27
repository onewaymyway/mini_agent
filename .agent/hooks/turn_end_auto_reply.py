#!/usr/bin/env python3
"""
.agent/hooks/turn_end_auto_reply.py — TurnEnd hook 进阶示例：自动接管用户输入

演示如何通过 TurnEnd hook 让另一个 agent（或脚本逻辑）替代真实用户输入。

使用方法（在 .agent/hooks.json 中配置）：
  {
    "TurnEnd": [
      {
        "command": "python3 .agent/hooks/turn_end_auto_reply.py",
        "timeout": 10
      }
    ]
  }

stdout 协议：
  {}                        → 继续等待真实用户输入
  {"user_input": "..."}     → 用这个字符串替代用户输入，直接驱动下一轮
  {"context": "..."}        → 向 hook runner 输出上下文（TurnEnd 暂不注入对话，仅记录）

典型场景：
  1. Agent-to-Agent：本 agent 回复后，外部 orchestrator agent 读取历史，
     决定下一步指令并通过此 hook 注入。
  2. 自动化测试：测试脚本按剧本逐步注入用户输入，无需人工交互。
  3. 条件触发：检测到特定回复模式时，自动追加一条后续问题。
"""

import json
import os
import sys


# ── 配置区 ────────────────────────────────────────────────────────────────────

# 最大自动轮次（防止死循环）
MAX_AUTO_TURNS = int(os.environ.get("MINI_AGENT_AUTO_TURNS", "0"))

# 自动回复队列文件路径（外部进程写入，本 hook 消费）
AUTO_REPLY_QUEUE = os.environ.get(
    "MINI_AGENT_AUTO_REPLY_QUEUE",
    "/tmp/mini_agent_auto_reply.txt",
)

# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    history = payload.get("history", [])
    user_turn_count = sum(1 for m in history if m.get("role") == "user")

    # ── 方式 1：从队列文件消费下一条自动回复 ──────────────────────────────
    # 外部进程（另一个 agent / 测试脚本）把要注入的用户输入写入队列文件，
    # 每行一条，本 hook 每次消费第一条。
    if os.path.isfile(AUTO_REPLY_QUEUE):
        try:
            lines = open(AUTO_REPLY_QUEUE, encoding="utf-8").readlines()
            if lines:
                next_input = lines[0].rstrip("\n")
                remaining = lines[1:]
                if remaining:
                    open(AUTO_REPLY_QUEUE, "w", encoding="utf-8").writelines(remaining)
                else:
                    os.remove(AUTO_REPLY_QUEUE)
                if next_input.strip():
                    print(json.dumps({"user_input": next_input}, ensure_ascii=False))
                    return
        except Exception:
            pass

    # ── 方式 2：MAX_AUTO_TURNS 内自动追加固定提示（演示用） ───────────────
    if MAX_AUTO_TURNS > 0 and user_turn_count < MAX_AUTO_TURNS:
        auto_msg = f"[Auto Turn {user_turn_count + 1}] 请继续。"
        print(json.dumps({"user_input": auto_msg}, ensure_ascii=False))
        return

    # ── 默认：不替代，继续等待真实用户输入 ───────────────────────────────
    print("{}")


if __name__ == "__main__":
    main()
