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
