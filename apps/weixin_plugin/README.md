# weixin-py

Python library for the [openclaw-weixin](https://github.com/Tencent/openclaw-weixin) WeChat bot protocol.
Mirrors the TypeScript `api.ts` / `types.ts` in pure Python with an async-first design.

## Architecture

```
weixin/
├── types.py          # Dataclasses mirroring the TS proto types
├── codec.py          # JSON <-> dataclass serialisation
├── api.py            # Low-level async HTTP API functions
├── bot.py            # High-level polling bot + handler framework
└── handlers/
    └── claude_code.py  # Claude Code CLI integration
```

## Prerequisites

1. Install [OpenClaw](https://docs.openclaw.ai/install)
2. Install the WeChat plugin and log in:

```bash
npx -y @tencent-weixin/openclaw-weixin-cli install
openclaw channels login --channel openclaw-weixin
openclaw gateway restart
```

3. Find your gateway URL and token in `~/.openclaw/openclaw.json`.

## Installation

```bash
pip install httpx           # optional but recommended
# or: pip install aiohttp   # alternative HTTP backend
# stdlib urllib is used as fallback with no extra deps
```

Copy the `weixin/` directory into your project, or install from this repo.

## Quick start — Echo bot

```python
import asyncio
from weixin import WeixinBot

bot = WeixinBot(base_url="http://localhost:8080", token="your-token")

@bot.on_text
async def echo(bot, msg, text):
    await bot.reply_text(msg, f"You said: {text}")

asyncio.run(bot.run())
```

## Claude Code integration

Requires the `claude` CLI on PATH (`npm install -g @anthropic-ai/claude-code`).

```python
import asyncio
from weixin import WeixinBot
from weixin.handlers.claude_code import ClaudeCodeHandler

bot = WeixinBot(base_url="http://localhost:8080", token="your-token")
bot.add_handler(ClaudeCodeHandler(
    system_prompt="You are a helpful WeChat assistant. Reply in Chinese.",
))
asyncio.run(bot.run())
```

## Handler patterns

### Decorator style

```python
@bot.on_text
async def handle(bot, msg, text):
    await bot.reply_text(msg, text.upper())

@bot.on_message          # raw — receives every WeixinMessage
async def log(bot, msg):
    print(msg.from_user_id, msg.message_type)
```

### Class style

```python
from weixin.bot import BaseHandler

class MyHandler(BaseHandler):
    async def on_text(self, bot, msg, text):
        await bot.reply_text(msg, f"Hello, {text}")

    async def on_message(self, bot, msg):
        print("raw:", msg)

bot.add_handler(MyHandler())
```

## Low-level API

All functions from `weixin.api` are available directly:

```python
from weixin.api import get_updates, send_message, make_text_message

resp = await get_updates(base_url="http://localhost:8080", token="tok", get_updates_buf="")
for msg in resp.msgs:
    print(msg.text())

reply = make_text_message(to_user_id=msg.from_user_id, text="Hi!", context_token=msg.context_token)
await send_message(base_url="http://localhost:8080", token="tok", msg=reply)
```

## Environment variables

| Variable | Description |
|---|---|
| `WEIXIN_BASE_URL` | Gateway base URL (e.g. `http://localhost:8080`) |
| `WEIXIN_TOKEN` | Bearer token from `~/.openclaw/openclaw.json` |

## Message types

```python
from weixin.types import MessageItemType

for item in msg.item_list:
    if item.type == MessageItemType.TEXT:
        print(item.text_item.text)
    elif item.type == MessageItemType.IMAGE:
        print(item.image_item.url)
    elif item.type == MessageItemType.VOICE:
        print(item.voice_item.text)  # speech-to-text
```

## HTTP backends

The library tries backends in order: **httpx → aiohttp → stdlib urllib**.
Install `httpx` or `aiohttp` for async I/O; the stdlib fallback is synchronous
(runs in a thread pool via `asyncio.run_in_executor`).

## Connecting to Claude Code

The `ClaudeCodeHandler` spawns a `claude --print` subprocess per message.
For production use, consider:

- Maintaining a per-user conversation history (pass it via `--continue` or a custom system prompt)
- Rate-limiting requests per user
- Using `chunk_size > 0` to stream partial replies back to WeChat as they are generated

## Connecting to mini_agent (this project)

Instead of shelling out to a CLI per message, `MiniAgentHandler` relays WeChat
messages to this project's own HTTP API (`src/mini_agent/api/`), reusing its
multi-user session pool, permission gate, and file-system endpoints.

```python
import asyncio
from weixin import WeixinBot
from weixin.handlers.mini_agent_handler import MiniAgentHandler
from permission_poller import PermissionPoller
from user_mapping import RoleRules

bot = WeixinBot(base_url="http://localhost:8080", token="your-weixin-token")

handler = MiniAgentHandler(
    mini_agent_base_url="http://localhost:8000",
    owner_token="your-mini-agent-owner-token",
    role_rules=RoleRules(owner_openids=set(), default_role="public"),
)
bot.add_handler(handler)

poller = PermissionPoller(bot=bot, handler=handler)

asyncio.run(asyncio.gather(bot.run(), poller.run()))
```

Or just run the ready-made entry point after copying `config.example.toml` to
`config.toml` and filling in `mini_agent.owner_token`:

```bash
cd apps/weixin_plugin
pip install httpx        # recommended
python run_mini_agent_bot.py
```

### Slash commands

| Command | Description |
|---|---|
| *(plain text)* | Chat with the current session |
| `/help` | Show help |
| `/status` | Show agent state, current turn, current session |
| `/interrupt` | Interrupt the currently running turn |
| `/sessions` | List your sessions (numbered, current marked with ●) |
| `/session new` | Create and switch to a new session |
| `/session use <n>` | Switch to session number `n` from the last `/sessions` listing |
| `/session del <n>` | Delete session number `n` |
| `/ls [path]` | List a directory (read-only) |
| `/cat <path>` | Read a file (read-only, truncated if long) |
| `/find <keyword>` | Search files by name |
| `/yes` / `/no` | Approve / deny the latest pending permission request once |
| `/always` / `/denyalways` | Approve / deny this kind of request permanently |

Each WeChat `openid` is automatically provisioned as its own mini_agent user
(via `POST /v1/users`, using the configured owner token) on first contact, and
the mapping is cached in a local sqlite file (`data/user_mapping.db`) so
restarts don't create duplicate accounts. Role assignment is configurable via
`[mini_agent.roles]` in `config.toml` — put trusted WeChat openids in
`owner_openids` to grant them the higher-trust `family` role; everyone else
gets `default_role` (`public` by default).

Permission requests (tool calls requiring approval) and other user-confirmation
prompts are picked up by `PermissionPoller`, which polls
`GET /v1/permissions/pending` per user (default every 4s) and proactively
pushes a WeChat message when a new one appears. Replying with `/yes`, `/no`,
`/always`, or `/denyalways` submits the decision back via
`POST /v1/permissions/{req_id}`. A pending request that goes unanswered for
10 minutes triggers a one-time reminder.

### Same-machine vs. cross-machine deployment

By default everything assumes the mini_agent server runs on the same machine
(`mini_agent.base_url = "http://localhost:8000"` in `config.toml`) — no extra
setup needed.

To run the WeChat bot on a different machine from the mini_agent server:

1. On the mini_agent server, enable multi-user HTTP auth
   (`http_multi_user_enabled`) so per-user tokens work correctly.
2. Either add the bot machine's outbound IP to mini_agent's IP allowlist, or
   rely on token-only auth if you disable the allowlist (weigh this against
   your threat model).
3. Put mini_agent behind HTTPS (nginx/caddy reverse proxy, or TLS termination
   directly) — Bearer tokens are sent as plain headers, so don't send them
   over plaintext HTTP across a public network.
4. Point `mini_agent.base_url` in `config.toml` (or the
   `MINI_AGENT_BASE_URL` env var) at the remote HTTPS URL, and set
   `MINI_AGENT_OWNER_TOKEN` / `mini_agent.owner_token` to the server's owner
   token.

### Known limitations (planned for a later iteration)

- Chat replies are fetched by polling `GET /v1/turns/{turn_id}` + reading
  `GET /v1/history` for the final answer, rather than streaming tokens live
  via SSE — WeChat doesn't render a typing effect anyway, so replies are sent
  as a single message once the turn finishes.
- `/v1/permissions/pending` is polled per user rather than subscribed to via
  SSE; this is simpler and more robust for a first version at the cost of a
  few seconds of latency.
- File operations are read-only (`/ls`, `/cat`, `/find`); there is no
  WeChat-side `/fs/write` or upload command yet, to avoid accidental edits
  from a chat interface.
- Commands are slash-only; there's no natural-language command routing yet.

