"""
cli/commands/notepad.py — /notepad slash 命令处理

/notepad              — 显示当前记事本内容
/notepad show         — 同上
/notepad clear        — 清空当前记事本（用户手动操作，agent 不会自动调用）
/notepad remove <id>  — 删除指定条目
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_notepad_cmd(args: list[str]) -> None:
    from mini_agent.tools.notepad import get_current_notepad, is_notepad_enabled

    if not is_notepad_enabled():
        R.print_info("Notepad is disabled (notepad_enabled=false in config).")
        return

    store = get_current_notepad()
    if store is None:
        R.print_info("Notepad is not available yet (no active session).")
        return

    if not args or args[0] == "show":
        if store.is_empty():
            R.print_info("Notepad is empty — nothing recorded yet.")
        else:
            R.console.print(f"[bold]Notepad[/bold] ({len(store.entries)} entries, "
                             f"{store.total_chars()} chars)")
            R.console.print(store.render(), markup=False)

    elif args[0] == "clear":
        n = store.clear()
        R.print_success(f"Notepad cleared ({n} entries removed).")

    elif args[0] == "remove" and len(args) >= 2:
        ok = store.remove(args[1])
        if ok:
            R.print_success(f"Removed notepad entry {args[1]!r}.")
        else:
            R.print_error(f"No notepad entry with id {args[1]!r}.")

    else:
        R.print_error("Usage: /notepad | /notepad show | /notepad clear | /notepad remove <id>")
