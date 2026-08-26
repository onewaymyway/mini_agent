# prompts/fragments/permission_labels.md
#
# 权限交互界面中使用的文本片段
# 格式: KEY: value（每行一条，供 PromptManager 解析）

DANGEROUS_LABEL: ⚠ DANGEROUS
SAFE_LABEL: 🔧
SANDBOX_BLOCKED: 🏖️  Sandbox mode — {tool_name} was blocked
SANDBOX_WOULD_HAVE: Would have executed
CHOICE_HINT: (y)es  (a)lways  (n)o  (d)eny-always  (e)dit  (s)how
SESSION_DENIED_MSG: ⛔  {tool_name} is denied for this session.
GIT_PUSH_BLOCKED_AUTO: ⛔  git push blocked — daemon/auto-approve sessions may not push to remote without an explicit user instruction in an interactive session. The change stays committed locally; report to the user that a push is pending.
