#!/usr/bin/env python
"""entrypoints/smoke_real_llm.py — 真实 LLM 的最小冒烟（M1，需在本机手动运行）。

只跑一次 plan_blueprint（LLM 调用上限 1），用于确认：
  1. skill_agent 能加载 novel-planner；2. 回执 receipt.json 能写出并通过校验；
  3. 产出 大纲规划/小说蓝图.json 确实落在工作目录里。
用法：
    python entrypoints/smoke_real_llm.py --title 冒烟书 --intent "一个落魄少年修炼逆袭的三章短篇"
需要本机已配置 mini_agent 的 LLM（API key 等）。开发环境/CI 中没有密钥时请勿运行。
"""

from __future__ import annotations

import argparse
import json

import _common  # noqa: F401  (导入即配置 sys.path)

from novel_writer import tool_api
from novel_writer.config import ensure_dirs
from novel_writer.runner import FrameworkInvoker
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace


def main() -> int:
    parser = argparse.ArgumentParser(description="真实 LLM 冒烟：只跑 plan_blueprint 一步")
    parser.add_argument("--title", default="冒烟书")
    parser.add_argument("--intent", required=True, help="作为作者指导注入 plan_blueprint 的创作意图")
    args = parser.parse_args()

    ensure_dirs()
    created = tool_api.create_novel(args.title, gear="G3", quality="draft")
    if not created["ok"]:
        print(json.dumps(created, ensure_ascii=False))
        return 1
    root = created["data"]["workspace"]
    tool_api.update_settings(root, {"budget": {"max_llm_calls_per_run": 1, "max_chapters_per_run": 1}})
    tool_api.write_author_note(root, "plan_blueprint", args.intent)
    # 第一轮只跑 init_workspace；第二轮执行 plan_blueprint，它是强制检查点，执行后停在 waiting_review（冒烟到此为止）
    result = run_worker(Workspace(root), mode="single", invoker=FrameworkInvoker())
    result2 = run_worker(Workspace(root), mode="single", invoker=FrameworkInvoker())
    print(json.dumps({"workspace": root, "runs": [result, result2]}, ensure_ascii=False, indent=2))
    blueprint = Workspace(root).path("blueprint")
    print("blueprint_exists=" + str(blueprint.is_file()))
    return 0 if blueprint.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
