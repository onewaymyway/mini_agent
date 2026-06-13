# prompts/system/user_profile.md
#
# 变量: {{ user_profile }}
# 在存在用户画像（profile.derived.summary）时注入

## User profile (from past sessions)

The following is a profile of this user, derived from their past sessions.
Use it to tailor your tone, level of detail, and assumptions — but always
defer to what the user says in the current conversation if it conflicts.

{{ user_profile }}
