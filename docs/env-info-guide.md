# env_info 扩展指南

## 自定义 Provider

实现 `EnvInfoProvider` 接口，放在项目任意位置：

```python
# myplugins/git_info.py
import subprocess
from mini_agent.env_info import EnvInfoProvider

class GitInfoProvider(EnvInfoProvider):
    name = "myplugins.git"

    def collect(self) -> dict:
        try:
            branch = subprocess.check_output(
                ["git", "branch", "--show-current"],
                text=True, timeout=2, stderr=subprocess.DEVNULL
            ).strip()
            status = subprocess.check_output(
                ["git", "status", "--porcelain"],
                text=True, timeout=2, stderr=subprocess.DEVNULL
            ).strip()
            changed = len(status.splitlines()) if status else 0
            value = branch
            if changed:
                value += f" ({changed} changed files)"
            return {"Git": value}
        except Exception:
            return {}
```

## 注册到 agent_config.json

```json
{
  "env_info": {
    "enabled": true,
    "providers": [
      "builtin.system",
      "builtin.runtime",
      "builtin.locale",
      "myplugins.git_info.GitInfoProvider"
    ],
    "provider_kwargs": {
      "builtin.system": {
        "include_hostname": true
      }
    }
  }
}
```

## 内置 Provider

| 标识 | 采集字段 | 默认启用 |
|------|----------|--------|
| `builtin.system` | OS、Arch、Hostname\*、User\* | ✓ |
| `builtin.runtime` | Python、Venv、CWD | ✓ |
| `builtin.locale` | Timezone、Locale | ✓ |

\* 隐私敏感，默认关闭，需在 `provider_kwargs` 中设 `include_hostname: true`

## 禁用

```json
{ "env_info": { "enabled": false } }
```
