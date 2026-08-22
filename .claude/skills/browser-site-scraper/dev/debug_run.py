"""
browser-site-scraper/dev/debug_run.py — 开发期调试工具，不进 `_index.json`
检索（对应方案文档第 10 节"tests/、scripts/eval_*.py 等开发期工具从 skill
目录中移出/不进入 skill 索引"的既有约定；本文件放在 `dev/` 而非 `members/`，
引擎的 resolve/execute 都不会扫描到它）。

用途：对指定 member（如 baidu/zhihu）或直接给一个 URL，跑一次真实的
execute()，失败时自动附带阶段十六新增的调试快照（url/title/正文摘要/
截图路径），省去手动在探索循环里一步步试。

用法：
    python dev/debug_run.py --member baidu --request '{"target": {"url": "https://www.baidu.com/s?wd=test"}, "query": "test"}'
    python dev/debug_run.py --member zhihu  --request '{"target": {"url": "..."}, "query": "..."}' --headed

调试循环（因阶段十六的热更新修复，无需重启进程）：
    1. 跑一次上面的命令，看返回的 error + debug 快照
    2. 直接编辑 members/<id>/script.py 或 ../browser-core/impl/*.py
    3. 重新跑同一条命令——下一次调用读到的就是刚编辑过的最新代码
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENGINE_SRC = SKILL_DIR.parent.parent.parent / "src"
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))


def main() -> None:
    parser = argparse.ArgumentParser(description="browser-site-scraper 调试运行入口")
    parser.add_argument("--member", required=True, help="member id，如 baidu/zhihu")
    parser.add_argument("--request", required=True, help="JSON 格式的 request，如 '{\"target\": {\"url\": \"...\"}}'")
    parser.add_argument("--headless", action="store_true", help="覆盖为无头模式（默认走 session_manager 的 auto/有界面兜底）")
    args = parser.parse_args()

    try:
        request = json.loads(args.request)
    except json.JSONDecodeError as e:
        print(f"--request 不是合法 JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if args.headless:
        request.setdefault("session", {})["mode"] = "launch_headless"

    from mini_agent.skills.generative_capability import CapabilityEngine
    from mini_agent.skills.generative_capability.real_tools import build_default_tool_executor

    tool_executor = build_default_tool_executor(skill_dir=SKILL_DIR)
    engine = CapabilityEngine(SKILL_DIR, tool_executor=tool_executor)

    result = engine.execute(args.member, request)
    print(json.dumps(
        {
            "status": result.status,
            "data": result.data,
            "error": result.error,
            "member_id": result.member_id,
        },
        ensure_ascii=False,
        indent=2,
    ))
    if result.status != "success":
        print(
            "\n提示：如果 error 里带 debug 字段，看 url/title/body_excerpt 判断"
            "选择器/页面结构是否符合预期；也可以直接改 members/{}/script.py"
            "或 browser-core/impl/*.py 后重跑本命令（热更新，无需重启）。".format(args.member),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
