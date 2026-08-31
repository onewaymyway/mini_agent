## Protected files (do NOT delete, move, overwrite, or truncate)

The following paths are declared by the user as protected. This applies
regardless of how you might act on them — via bash commands, scripts, tool
calls, or any other means, direct or indirect:

{{protected_list}}

You must not delete, move, rename, overwrite, or clear the contents of any
of the above, or anything inside a protected directory, under any
circumstances — even as part of cleanup, refactoring, or a task that seems
to require it. If a task genuinely requires touching one of these paths,
stop and ask the user first instead of proceeding.
