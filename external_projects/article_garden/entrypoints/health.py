"""entrypoints/health.py — 环境自检（JSON）。required=False 的项只提示，不影响 ok。不访问网络、不调用模型。"""

from __future__ import annotations

import json
import sys
from importlib.util import find_spec

from _common import force_utf8_stdio  # noqa: E402

from article_garden import domains, paths, settings  # noqa: E402


def main() -> int:
    force_utf8_stdio()
    checks = [
        {"name": "python>=3.9", "ok": sys.version_info >= (3, 9), "detail": sys.version.split()[0]},
        {"name": "PyYAML", "ok": find_spec("yaml") is not None, "detail": "读写章程与设置"},
        {"name": "streamlit", "ok": find_spec("streamlit") is not None, "required": False,
         "detail": "看板界面需要：pip install streamlit"},
    ]
    root = paths.data_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append({"name": "data_root_writable", "ok": True, "detail": str(root)})
    except OSError as exc:
        checks.append({"name": "data_root_writable", "ok": False, "detail": f"{root}: {exc}"})

    try:
        rows = domains.list_domains(include_archived=True)
        bad = []
        for d in rows:
            try:
                ctx = domains.DomainContext(d["id"])
                if not ctx.root.is_dir():
                    bad.append(f"{d['id']}（目录缺失）")
                    continue
                _, w1 = ctx.load_charter()
                _, w2 = settings.effective_settings(ctx)
                if w1 or w2:
                    bad.append(f"{d['id']}（配置有 {len(w1) + len(w2)} 条警告）")
            except domains.DomainError as exc:
                bad.append(f"{d['id']}（{exc}）")
        checks.append({"name": "domains", "ok": not bad, "required": False,
                       "detail": f"{len(rows)} 个文章库" + (f"；问题：{'；'.join(bad)}" if bad else "")})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "domains", "ok": False, "required": False, "detail": f"{type(exc).__name__}: {exc}"})

    if find_spec("mini_agent") is None and not (paths.mini_agent_root() / "src" / "mini_agent").is_dir():
        checks.append({"name": "mini_agent", "ok": False, "required": False,
                       "detail": "未找到 mini_agent：P1 起的摘要/成文需要它提供模型与搜索；P0 功能不受影响"})
    else:
        checks.append({"name": "mini_agent", "ok": True, "required": False, "detail": str(paths.mini_agent_root())})

    ok = all(c["ok"] for c in checks if c.get("required", True))
    print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
