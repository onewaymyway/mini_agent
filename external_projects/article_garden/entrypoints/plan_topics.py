"""entrypoints/plan_topics.py — 缺口检测与选题（P4）。

用法：
  python entrypoints/plan_topics.py <domain_id>                      检测缺口并生成候选选题
  python entrypoints/plan_topics.py <domain_id> --approve t-0003     放行一个待审选题
  python entrypoints/plan_topics.py <domain_id> --reject t-0003 --reason "重复已有内容"
说明：缺口检测是纯规则。模型只用于两件事：中等相似度的判重；开放命题（需在设置中打开）。
模型未就绪时仍会生成选题，但标记“未判重（需模型）”。
"""

from __future__ import annotations

import json
import sys

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains, joblog, settings  # noqa: E402
from article_garden.planning import states, topics  # noqa: E402
from article_garden.providers.text import MiniAgentTextLLM, text_model_status  # noqa: E402


def _opt(args, flag):
    if flag in args:
        i = args.index(flag)
        try:
            val = args[i + 1]
        except IndexError:
            raise ValueError(f"{flag} 后需要一个值")
        del args[i:i + 2]
        return val
    return None


def main(argv=None) -> int:
    force_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        approve = _opt(args, "--approve")
        reject = _opt(args, "--reject")
        reason = _opt(args, "--reason")
        if not args:
            print(__doc__, file=sys.stderr)
            return 2
        ctx = domains.DomainContext(args[0])
        if approve:
            row = states.approve(ctx, approve)
            print(json.dumps({"ok": True, "id": row["id"], "status": row["status"]}, ensure_ascii=False))
            return 0
        if reject:
            row = states.reject(ctx, reject, reason or "")
            print(json.dumps({"ok": True, "id": row["id"], "status": row["status"]}, ensure_ascii=False))
            return 0
        llm = None
        ready, why = text_model_status()
        if ready:
            eff, _ = settings.effective_settings(ctx)
            llm = MiniAgentTextLLM(use_system_proxy=eff["llm"]["use_system_proxy"])
        else:
            joblog.log(ctx.root, "plan", f"模型未就绪，选题未判重：{why}", level="warn")
        summary = topics.run(ctx, llm)
        joblog.log(ctx.root, "plan", f"选题：新增 {summary['proposed']} 个，待判重 {summary['pending_mid']} 个",
                   level="ok")
        print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2))
        return 0
    except (domains.DomainError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
