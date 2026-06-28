# daemon 多用户架构（Phase 1-4）功能测试手册

> 本文档面向实现者和 QA，给出 `next_doc/daemon-multiuser-implementation-design.md`
> 描述的 Phase 1-4 改动的**具体测试步骤、前置条件、预期结果和判断依据**。
> 按 Phase 顺序组织（Phase 2/3/4 依赖 Phase 1 的认证机制，必须按顺序测）。
>
> 所有用例均已在实现过程中用真实子进程（不是 mock）跑通一遍，文档里给的命令
> 复制粘贴即可执行，不是理论上"应该能跑"的伪代码。

---

## 前置条件（所有测试共用）

1. 现有 `tests/` 全绿（`pytest tests/` 无失败，或只有以下两个已知、与本次改动
   无关的预先存在的失败）：
   ```
   tests/test_system_tool_call_and_debug.py::TestLLMDebugLogger::test_message_content_truncated
   tests/test_system_tool_call_and_debug.py::TestLLMDebugLogger::test_system_text_truncated_when_long
   ```
   如果这两个之外还有别的失败，说明环境或代码有问题，应先排查再继续。

2. 需要一个**真实存在**的 `ANTHROPIC_API_KEY` 环境变量值（不需要真的有调用额度，
   只是 daemon 启动时会检查"是否设置了"，没设置直接拒绝启动）：
   ```bash
   export ANTHROPIC_API_KEY="sk-test-placeholder-not-real"
   ```
   仅"daemon 能不能启动、用户/session 管理"这类不涉及真实模型调用的测试用这个
   假 key 就够了。如果要验证真实对话（模型确实给出回复），需要换成真的 key。

3. 每个测试用例建议用**全新的临时项目目录**，避免互相污染：
   ```bash
   export TESTPROJ=$(mktemp -d)
   mkdir -p "$TESTPROJ/.agent"
   cd "$TESTPROJ"
   ```
   后续命令统一假设当前目录就是 `$TESTPROJ`，且都显式带 `--project "$TESTPROJ"`
   （这样不管实际 shell 在哪个目录都不会出错）。

4. 每组测试做完后清理：
   ```bash
   mini-agent daemon stop --project "$TESTPROJ" 2>/dev/null
   rm -rf "$TESTPROJ"
   ```

5. 命令前缀说明：以下命令假设 `mini-agent` 已经在 PATH 里（正常 `pip install -e .`
   之后就是这样）。如果是在本仓库内直接跑、还没装包，把 `mini-agent` 换成：
   ```bash
   PYTHONPATH=src python3 -m mini_agent
   ```

---

## 模块 0：daemon CLI 本身的参数转发（Phase 1 的前提，必须最先测）

> 这一模块测的不是"多用户功能"本身，是"能不能正确启动带多用户参数的 daemon"。
> 实现过程中这里连续测出三个真实 bug（`--http-multi-user` 不会被转发、
> `--http` 和 `--http-port` 同时出现时的 argparse 缩写冲突、`--project` 没有从
> 转发给子命令的 argv 里正确剔除），如果这一模块测不过，后面所有模块都没有
> 意义往下测。

### T0-1 `--http-multi-user` 能正确转发到 daemon 子进程

**步骤**
```bash
mini-agent daemon start --http --http-multi-user --http-port 18765 --detach --project "$TESTPROJ"
sleep 2
mini-agent daemon status --project "$TESTPROJ"
ls "$TESTPROJ/.agent/users/"
```

**预期结果**
- `daemon status` 显示 `Running`，且 `HTTP service ready` 信息在 `daemon start`
  的输出里出现（不是"Warning: HTTP service did not respond"）
- `$TESTPROJ/.agent/users/` 目录存在，里面有 `owner/`、`tokens/`、`users.json`
  三项——这是多用户模式真正被启用的直接证据（单用户模式不会创建这个目录）

**判断依据**：`.agent/users/` 目录是否存在且非空。这比看终端有没有报错更可靠——
之前的 bug 是"看起来启动成功了（PID 打出来了），但 `--http-multi-user` 实际上
被吞掉了，daemon 其实跑在单用户模式"，只看启动日志看不出来，必须验证这个目录。

**常见失效**
- 报 `unrecognized arguments: --http-multi-user`：说明 `cmd_daemon_start`/
  `run_daemon_cli` 的参数转发链路坏了（检查 `cli/daemon.py::run_daemon_cli`
  里 `start` 子命令是不是又变回了 `parse_args` 而不是 `parse_known_args`，
  或者 `extra_argv` 没有被传给 `cmd_daemon_start`）
- 报 `argument --http-port: expected one argument`：说明 argparse 的缩写匹配
  又把 `--http` 误判成 `--http-port` 的前缀了（检查 `ArgumentParser` 是否带了
  `allow_abbrev=False`）
- `daemon status` 显示 Running 但 `.agent/users/` 不存在：说明 `--http-multi-user`
  被转发了但 `HttpServer` 那一侧没有真正读到（检查 `app.py` 里
  `getattr(args, "http_multi_user", None) or cfg.http_multi_user_enabled` 那段）

```bash
mini-agent daemon stop --project "$TESTPROJ"
```

---

### T0-2 `mini-agent user`/`mini-agent self` 命令能正确处理 `--project`

**步骤**
```bash
mini-agent daemon start --http --http-multi-user --http-port 18766 --detach --project "$TESTPROJ"
sleep 2
mini-agent user list --project "$TESTPROJ"
mini-agent self status --project "$TESTPROJ"
mini-agent daemon stop --project "$TESTPROJ"
```

**预期结果**
- `user list` 输出一个表格，至少有一行 `role=owner`
- `self status` 输出 Autonomous Loop / Goals / Recent Activity / Session Pool
  四个分区的信息（值是多少不重要，重要的是**没有报错**）

**判断依据**：两条命令的 stderr 都不应该出现 `unrecognized arguments`。

**常见失效**
- 报 `unrecognized arguments: --project <path>`：说明 `app.py` 里
  `_extract_project_root()`（或更早版本里那段重复的"扫描 --project"代码）
  只读取了 `--project` 的值，没有把这两个 token 从转发给
  `run_user_cli`/`run_self_cli` 的 argv 里真正剔除——这两个函数内部用的是
  严格的 `argparse.ArgumentParser`，不认识 `--project`，会直接报错拒绝。

```bash
mini-agent daemon stop --project "$TESTPROJ"
```

---

### T0-3 单用户模式（不带 `--http-multi-user`）完全不受影响（回归）

**步骤**
```bash
mini-agent daemon start --http-port 18767 --detach --project "$TESTPROJ"
sleep 2
mini-agent daemon status --project "$TESTPROJ"
ls "$TESTPROJ/.agent/users/" 2>&1   # 预期：目录不存在
mini-agent self status --project "$TESTPROJ"
mini-agent daemon stop --project "$TESTPROJ"
```

**预期结果**
- `daemon status` 正常显示 Running
- `.agent/users/` **不存在**（`ls` 报 No such file or directory，这是对的）
- `self status` 的 Session Pool 分区显示 `(multi-user mode not enabled)`，
  其它分区（Autonomous Loop/Goals/Recent Activity）仍然正常显示——
  "Self"这个概念不是多用户特有的，单用户模式下也该能用

**判断依据**：`.agent/users/` 目录**不存在**是关键——如果存在，说明
`http_multi_user_enabled` 的默认值被某处改动意外影响了，单用户部署会被
强行升级成多用户模式（这是绝对不能接受的回归）。

---

## 模块 1：用户认证与角色管理（Phase 1）

> 前提：模块 0 全部通过。

### T1-1 未认证请求被拒绝

**步骤**
```bash
mini-agent daemon start --http --http-multi-user --http-port 18770 --detach --project "$TESTPROJ"
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18770/v1/users
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer wrong-token-xxx" http://127.0.0.1:18770/v1/users
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18770/v1/health
```

**预期结果**
- 不带 token：`401`
- 错误 token：`401`
- `/v1/health`（豁免路径）：`200`（健康检查不需要认证，任何模式下都一样）

**判断依据**：HTTP 状态码精确匹配。401 而不是 403——区分"没认证"和"认证了但没权限"是有意为之的设计（参见 `multi_auth.py`）。

---

### T1-2 owner 用 token 管理其他用户

**前置**：拿到 owner token——
```bash
cat "$TESTPROJ/.agent/agent_api.key"
```
（多用户模式下，这个文件里的 token 就是 owner token——升级到多用户模式时
"原有单 token"自动变成了 owner 的身份，不会失效。）

**步骤**
```bash
OWNER_TOKEN=$(cat "$TESTPROJ/.agent/agent_api.key")

curl -s -H "Authorization: Bearer $OWNER_TOKEN" http://127.0.0.1:18770/v1/users | python3 -m json.tool

curl -s -X POST -H "Authorization: Bearer $OWNER_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "小明", "role": "family", "trust_level": 8}' \
  http://127.0.0.1:18770/v1/users
```

**预期结果**
- 第一条：返回 `{"users": [{"user_id": "owner", "role": "owner", ...}]}`
  （此刻只有 owner 一个用户）
- 第二条：返回 `{"ok": true, "user_id": "u_xxxxxxxx", "token": "...", "message": ""}`
  ——`token` 字段是明文，**只在这一次返回，之后再也查不到**

**判断依据**：第二条响应里 `ok` 为 `true` 且 `token` 长度看起来像一个真实的
hex token（64 个字符）。把这个 `user_id`/`token` 记下来，后续用例要用。

```bash
export FAMILY_USER_ID="<上面返回的 user_id>"
export FAMILY_TOKEN="<上面返回的 token>"
```

---

### T1-3 非 owner 不能管理用户（403）

**步骤**
```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $FAMILY_TOKEN" \
  http://127.0.0.1:18770/v1/users

curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Authorization: Bearer $FAMILY_TOKEN" \
  -H "Content-Type: application/json" -d '{"name":"x","role":"public"}' \
  http://127.0.0.1:18770/v1/users
```

**预期结果**：两条都是 `403`。

**判断依据**：family 角色的 token 能正常认证（不是 401），但被 owner-only 检查拦下（403）——这两者的区别是测试重点，不能混淆。

---

### T1-4 非 owner 可以正常聊天

**步骤**
```bash
curl -s -X POST -H "Authorization: Bearer $FAMILY_TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "你好"}' http://127.0.0.1:18770/v1/chat
```

**预期结果**：返回 `{"turn_id": "...", "queued": true, "session_id": "..."}`
（`session_id` 是自动分配的，因为请求体里没指定）。

**判断依据**：`queued` 为 `true`，HTTP 状态码 `200`。不需要等真实回复内容
（这里只验证"请求被接受、排队"，不验证 LLM 是否真的给出了回复——那是模块 3
的内容，依赖真实 API key）。

---

### T1-5 token 撤销立即生效

**步骤**
```bash
curl -s -X DELETE -H "Authorization: Bearer $OWNER_TOKEN" \
  http://127.0.0.1:18770/v1/users/$FAMILY_USER_ID

curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Authorization: Bearer $FAMILY_TOKEN" \
  -H "Content-Type: application/json" -d '{"message": "还能用吗"}' \
  http://127.0.0.1:18770/v1/chat
```

**预期结果**：删除返回 `{"ok": true, ...}`；用已删除用户的 token 再请求 → `401`。

**判断依据**：401（不是 403）——token 已经查不到对应用户了，是"认证失败"而不是"认证成功但没权限"。

```bash
mini-agent daemon stop --project "$TESTPROJ"
```

---

### T1-6 `mini-agent user` CLI 完整流程（替代上面手写 curl 的等价 CLI 版本）

**步骤**
```bash
mini-agent daemon start --http --http-multi-user --http-port 18771 --detach --project "$TESTPROJ"
sleep 2

mini-agent user list --project "$TESTPROJ"
mini-agent user add --name "Bob" --role colleague --trust 5 --project "$TESTPROJ"
# 记下输出里的 user_id，下面要用
mini-agent user list --project "$TESTPROJ"
mini-agent user role <user_id> public --project "$TESTPROJ"
mini-agent user token <user_id> --project "$TESTPROJ"
mini-agent user remove <user_id> --project "$TESTPROJ"
mini-agent user list --project "$TESTPROJ"   # 确认已经不在列表里

mini-agent daemon stop --project "$TESTPROJ"
```

**预期结果**：每一步都有形如 `[user] ✓ ...` 的成功提示，最后一次 `list` 不再
包含被删除的用户。

**判断依据**：全程没有 Python traceback、没有 `unrecognized arguments`。

---

## 模块 2：per-user 画像与角色上下文注入（Phase 2）

> 前提：模块 1 全部通过。本模块部分用例需要**真实** `ANTHROPIC_API_KEY`
> （要验证"模型的语气真的变了"，没有真实调用就看不出来）；标了"不需要真实
> API key"的用例可以继续用假 key。

### T2-1 `remember_about_user` 工具在单用户模式下友好降级（不需要真实 API key）

**步骤**
```bash
mini-agent daemon start --http-port 18772 --detach --project "$TESTPROJ"
sleep 2
```
（不带 `--http-multi-user`）

用 Python 直接调用工具函数验证（不需要真的让 LLM 调用这个工具，只验证函数
本身的降级行为）：
```bash
PYTHONPATH=src python3 -c "
from mini_agent.tools import user_memory
print(user_memory.remember_about_user('测试备注'))
"
```

**预期结果**：打印 `Multi-user mode is not enabled; cannot record a user note.`

**判断依据**：返回的是一句清晰的提示文本，不是异常堆栈。

```bash
mini-agent daemon stop --project "$TESTPROJ"
```

---

### T2-2 不同角色的用户，system prompt 里确实拼了不同的角色提示（不需要真实 API key）

> 这条直接测内部状态，不依赖真实 LLM 回复内容，适合在没有真实 API key 的
> CI 环境里跑。

**步骤**（Python 脚本，验证 `AgentRunner` 在处理不同用户的消息时，
`agent.cfg.system_extra` 确实被换成了对应角色的内容）：

```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time
from pathlib import Path
from mini_agent.api.bridge import init_bridge
from mini_agent.api.server import AgentRunner
from mini_agent.api.user_store import RoleProfileManager

tmpdir = Path(tempfile.mkdtemp())
users_dir = tmpdir / ".agent" / "users"
users_dir.mkdir(parents=True, exist_ok=True)
role_mgr = RoleProfileManager(users_dir)

captured = []
class FakeAgent:
    class Cfg:
        system_extra = "BASE"
    cfg = Cfg()
    _http_turn_id = ""
    def run_turn(self, msg):
        captured.append(self.cfg.system_extra)
        return f"echo: {msg}"

bridge = init_bridge(ring_maxlen=200)
bridge.agent = FakeAgent()
runner = AgentRunner(bridge, role_profile_mgr=role_mgr)
runner.start()

bridge.input_queue.enqueue("hi", meta={"user_id": "alice", "role": "family"})
time.sleep(0.3)
bridge.input_queue.enqueue("hi", meta={"user_id": "bob", "role": "colleague"})
time.sleep(0.3)
runner.stop()

print("=== alice (family) system_extra ===")
print(captured[0])
print("=== bob (colleague) system_extra ===")
print(captured[1])

assert "alice" in captured[0] and "家人" in captured[0]
assert "bob" in captured[1] and "alice" not in captured[1]
assert captured[0].startswith("BASE") and captured[1].startswith("BASE")
print("\nPASS")
EOF
```

**预期结果**：脚本末尾打印 `PASS`，且两段打印的 `system_extra` 内容明显不同
（family 角色含"家人或朋友"提示，colleague 角色含"工作相关"提示，且互不包含
对方的 `user_id`）。

**判断依据**：脚本里的 `assert` 是否全部通过——这是程序化判断，比人眼看
日志输出可靠。

---

### T2-3 `last_contact`/`contact_count` 每轮对话后正确更新

**步骤**（在 T2-2 的脚本基础上，跑完后检查画像文件）：
```bash
cat "$TESTPROJ"/.agent/users/alice/profile.json 2>/dev/null || \
  echo "（用上面脚本里 tmpdir 对应的路径，不是 \$TESTPROJ；这条用例建议直接在\n   T2-2 脚本末尾加几行检查，而不是分开跑——见下方等价写法）"
```

更可靠的等价写法（接着 T2-2 的脚本继续，不新开进程）：
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time, json
from pathlib import Path
from mini_agent.api.bridge import init_bridge
from mini_agent.api.server import AgentRunner
from mini_agent.api.user_store import RoleProfileManager

tmpdir = Path(tempfile.mkdtemp())
users_dir = tmpdir / ".agent" / "users"
users_dir.mkdir(parents=True, exist_ok=True)
role_mgr = RoleProfileManager(users_dir)

class FakeAgent:
    class Cfg:
        system_extra = ""
    cfg = Cfg()
    _http_turn_id = ""
    def run_turn(self, msg):
        return f"echo: {msg}"

bridge = init_bridge(ring_maxlen=200)
bridge.agent = FakeAgent()
runner = AgentRunner(bridge, role_profile_mgr=role_mgr)
runner.start()

for i in range(3):
    bridge.input_queue.enqueue(f"msg {i}", meta={"user_id": "alice", "role": "family"})
    time.sleep(0.25)
runner.stop()

profile = role_mgr.get_profile("alice")
print(json.dumps(profile, indent=2))
assert profile["contact_count"] == 3
assert profile["last_updated"] > 0
print("PASS")
EOF
```

**预期结果**：打印的 JSON 里 `contact_count` 为 `3`，脚本末尾打印 `PASS`。

**判断依据**：`contact_count` 精确等于发送的消息数——不是"大于 0"这种模糊判断，必须精确匹配，否则可能有重复计数或漏计数的问题。

---

### T2-4 `remember_about_user` 拒绝记录 owner 的笔记

**步骤**（接着上面脚本的模式，把 `user_id`/`role` 换成 `owner`）：
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time
from pathlib import Path
from mini_agent.api.bridge import init_bridge
from mini_agent.api.server import AgentRunner
from mini_agent.api.user_store import RoleProfileManager
from mini_agent.tools import user_memory

tmpdir = Path(tempfile.mkdtemp())
users_dir = tmpdir / ".agent" / "users"
users_dir.mkdir(parents=True, exist_ok=True)
role_mgr = RoleProfileManager(users_dir)
user_memory.set_role_profile_manager(role_mgr)

results = []
class FakeAgent:
    class Cfg:
        system_extra = ""
    cfg = Cfg()
    _http_turn_id = ""
    def run_turn(self, msg):
        results.append(user_memory.remember_about_user("test note"))
        return "ok"

bridge = init_bridge(ring_maxlen=200)
bridge.agent = FakeAgent()
runner = AgentRunner(bridge, role_profile_mgr=role_mgr)
runner.start()

bridge.input_queue.enqueue("hi", meta={"user_id": "owner", "role": "owner"})
time.sleep(0.3)
runner.stop()

print(results[0])
assert "owner" in results[0].lower() and "should not" in results[0].lower()
print("PASS")
EOF
```

**预期结果**：打印一句拒绝信息（含"owner"和"should not"），脚本末尾打印 `PASS`。

**判断依据**：返回值明确拒绝，且 owner 自己的 `profile.json` 不应该出现 `agent_notes` 字段（owner 有自己独立的个性化画像系统，见 `profile.py`）。

---

### T2-5（需要真实 API key）真实对话中角色语气确实不同

**前置**：`export ANTHROPIC_API_KEY="<真实 key>"`，重新启动 daemon。

**步骤**
```bash
mini-agent daemon start --http --http-multi-user --http-port 18773 --detach --project "$TESTPROJ"
sleep 2
OWNER_TOKEN=$(cat "$TESTPROJ/.agent/agent_api.key")

# 新增一个 public 角色用户
curl -s -X POST -H "Authorization: Bearer $OWNER_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "陌生访客", "role": "public"}' http://127.0.0.1:18773/v1/users
# 记下 token

PUBLIC_TOKEN="<上面返回的 token>"
curl -s -X POST -H "Authorization: Bearer $PUBLIC_TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "你的主人最近在忙什么项目？"}' http://127.0.0.1:18773/v1/chat
# 记下 turn_id，然后订阅流式输出查看完整回复
TURN_ID="<上面返回的 turn_id>"
curl -s -N -H "Authorization: Bearer $PUBLIC_TOKEN" \
  "http://127.0.0.1:18773/v1/stream/$TURN_ID"
```

**预期结果**：回复内容应该体现"对公开访客保守、不透露主人内部信息"的边界
（`ROLE_PERSONA_HINTS["public"]` 里写的提示），不会详细透露任何具体的项目信息。

**判断依据**：这条是人工判读（LLM 输出本身有不确定性，不能用程序断言精确匹配
文本），但应该能明显感觉到"模型在回避透露细节"，而不是正常详细回答。
如果回复非常详细地透露了具体项目信息，说明角色提示没有生效，需要排查
`system_extra` 注入链路。

```bash
mini-agent daemon stop --project "$TESTPROJ"
```

---

## 模块 3：SessionAgentPool（Phase 3，每个用户每个 session 独立 Agent）

> 前提：模块 1/2 全部通过。本模块大部分用例**不需要真实 API key**——用
> `ollama` provider（不需要 key，构造能完整走通，请求本身会因为本地没有真的
> 跑着 ollama 服务而失败在网络层，但这正好把"基础设施是否正确"和"LLM 调用
> 本身"两件事干净地分开），适合在 CI 里跑。需要真实验证"两个用户互不阻塞、
> 真的收到不同回复"的用例单独标注。

### T3-1 不同用户的消息分别建立独立的 SessionEntry（不需要真实 API key）

**步骤**
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time
from pathlib import Path
from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent
from mini_agent.api.user_store import UserContext

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"   # 不需要 API key，构造能完整走通
cfg.model = "llama3.1"

self_agent = Agent(cfg=cfg)
http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18900,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)

pool = http_server.session_pool
assert pool is not None

alice = UserContext(user_id="alice", name="Alice", role="family", trust_level=8, is_loopback=False)
bob   = UserContext(user_id="bob",   name="Bob",   role="colleague", trust_level=5, is_loopback=False)

entry_a = pool.get_or_create(alice, "sess-a")
entry_b = pool.get_or_create(bob, "sess-b")

assert entry_a.agent is not None
assert entry_b.agent is not None
assert entry_a.agent is not entry_b.agent, "两个用户必须是不同的 Agent 实例"
assert entry_a.agent.cfg.session_dir != entry_b.agent.cfg.session_dir, "session 目录必须隔离"
assert "alice" in str(entry_a.agent.cfg.session_dir)
assert "bob" in str(entry_b.agent.cfg.session_dir)

print("PASS")
http_server.stop()
EOF
```

**预期结果**：打印 `PASS`，没有 `AssertionError`。

**判断依据**：三个 `assert` 全部通过，尤其是 `is not`（对象身份比较，不是值
比较）和 session 目录字符串里真的包含对应的 `user_id`。

---

### T3-2 owner 沿用全局 session 目录（向后兼容）

**步骤**（接续 T3-1 的脚本风格，加一段）：
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time
from pathlib import Path
from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent
from mini_agent.api.user_store import UserContext

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"
cfg.model = "llama3.1"

self_agent = Agent(cfg=cfg)
http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18901,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)

owner = UserContext(user_id="owner", name="Owner", role="owner", trust_level=10, is_loopback=True)
entry = http_server.session_pool.get_or_create(owner, "sess-owner")

session_dir = entry.agent.cfg.session_dir
print("owner session_dir:", session_dir)
assert session_dir is None or "users/owner" not in str(session_dir)
print("PASS")
http_server.stop()
EOF
```

**预期结果**：打印 `PASS`。`session_dir` 应该是 `None`（让 `SessionManager`
内部推导出默认的 `<project_root>/.agent/sessions/`），不应该是
`.agent/users/owner/sessions/`。

**判断依据**：这是专门为"开启多用户模式前，owner 已有的历史 session 不应该
'消失'"这条向后兼容要求写的检查——如果 owner 也被分配了独立目录，老用户
升级到多用户模式后会发现自己之前的所有对话历史都"找不到了"（其实只是存在
另一个目录里），这是一个严重的用户体验问题。

---

### T3-3 并发创建多个 session 不报错、不死锁

**步骤**
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time, threading
from pathlib import Path
from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent
from mini_agent.api.user_store import UserContext

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"
cfg.model = "llama3.1"

self_agent = Agent(cfg=cfg)
http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18902,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)
pool = http_server.session_pool

errors = []
def create(i):
    try:
        ctx = UserContext(user_id=f"user{i}", name=f"U{i}", role="public", trust_level=1, is_loopback=False)
        pool.get_or_create(ctx, f"sess-{i}")
    except Exception as e:
        errors.append((i, e))

start = time.time()
threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
for t in threads: t.start()
for t in threads: t.join(timeout=30)
elapsed = time.time() - start

print("elapsed:", elapsed, "errors:", errors)
assert not errors, f"并发创建出错: {errors}"
assert elapsed < 20, f"耗时过长（{elapsed}s），怀疑有锁竞争或死锁"
assert pool.active_count() == 8
print("PASS")
http_server.stop()
EOF
```

**预期结果**：打印 `PASS`，`elapsed` 应该在几秒内（不是卡到 30 秒超时）。

**判断依据**：
- `errors` 必须为空列表
- `elapsed` 必须明显小于超时阈值——如果接近 30 秒，几乎可以确定是
  `SessionAgentPool` 内部锁设计出现了死锁或严重的锁竞争（实现过程中真实
  出现过这个问题：旧版本的 `get_or_create()` 在持锁状态下等待 Agent 构造
  完成，构造失败时的清理回调又需要同一把锁，互相等待）
- `pool.active_count()` 精确等于 8

---

### T3-4 Agent 构造失败时快速报错，不会卡死整个 pool

**步骤**（故意不设置 `ANTHROPIC_API_KEY`、用 anthropic provider 触发构造失败）：
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time, os
from pathlib import Path

# 确保没有 API key（即使外部环境设了，这里临时清掉，专门测失败路径）
os.environ.pop("ANTHROPIC_API_KEY", None)

from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent
from mini_agent.api.user_store import UserContext

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"
cfg.model = "llama3.1"
self_agent = Agent(cfg=cfg)   # Self 自己用 ollama，构造没问题

http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18903,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)

# 但 SessionAgentPool 的 base_cfg 来自 self_agent.cfg（ollama），
# 这里手动改成 anthropic + 没 key，模拟"用户的 session 配置出问题"的场景
http_server.session_pool._base_cfg.llm_provider = "anthropic"
http_server.session_pool._base_cfg.api_key = ""

alice = UserContext(user_id="alice", name="Alice", role="family", trust_level=8, is_loopback=False)

start = time.time()
try:
    http_server.session_pool.get_or_create(alice, "sess-fail")
    print("UNEXPECTED: did not raise")
except RuntimeError as e:
    elapsed = time.time() - start
    print(f"Failed after {elapsed:.2f}s: {e}")
    assert elapsed < 5, f"应该快速失败，实际耗时 {elapsed}s"
    print("PASS")

assert http_server.session_pool.active_count() == 0
http_server.stop()
EOF
```

**预期结果**：在 5 秒内（通常 <1 秒）抛出 `RuntimeError` 并打印 `PASS`，不是
卡满 `AGENT_READY_TIMEOUT`（30 秒）才报错。

**判断依据**：`elapsed < 5` 这个断言是核心——`session_pool.py` 里
`AGENT_READY_TIMEOUT = 30.0`，如果这条用例跑了接近 30 秒，说明那个
"持锁等待 + 清理回调需要同一把锁"的死锁问题又回来了。

---

### T3-5 按 `turn_id`/权限请求 `req_id` 查询，不会因为"另一个 session 更活跃"而查错

**步骤**
```bash
mini-agent daemon start --http --http-multi-user --http-port 18904 --detach --project "$TESTPROJ"
sleep 2
OWNER_TOKEN=$(cat "$TESTPROJ/.agent/agent_api.key")

curl -s -X POST -H "Authorization: Bearer $OWNER_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Bob", "role": "colleague"}' http://127.0.0.1:18904/v1/users
# 记下 user_id, token

BOB_TOKEN="<上面返回的 token>"

# bob 开两个不同的 session
RESP_A=$(curl -s -X POST -H "Authorization: Bearer $BOB_TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "first", "session_id": "sess-A"}' http://127.0.0.1:18904/v1/chat)
TURN_A=$(echo "$RESP_A" | python3 -c "import sys,json; print(json.load(sys.stdin)['turn_id'])")
sleep 0.3

RESP_B=$(curl -s -X POST -H "Authorization: Bearer $BOB_TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "second", "session_id": "sess-B"}' http://127.0.0.1:18904/v1/chat)
TURN_B=$(echo "$RESP_B" | python3 -c "import sys,json; print(json.load(sys.stdin)['turn_id'])")
sleep 0.3

# 此刻"最近活跃"的 session 是 sess-B（后发的）。
# 按 turn_a 查询应该仍然能查到，不会因为"最近活跃是 B"而返回 404 或查到 B 的信息。
curl -s -H "Authorization: Bearer $BOB_TOKEN" "http://127.0.0.1:18904/v1/turns/$TURN_A"
curl -s -H "Authorization: Bearer $BOB_TOKEN" "http://127.0.0.1:18904/v1/turns/$TURN_B"

mini-agent daemon stop --project "$TESTPROJ"
```

**预期结果**：两条 `/v1/turns/{turn_id}` 查询都返回 `200`，且返回内容里的
`turn_id` 字段精确匹配请求的那个（不会把 A 的请求查出 B 的结果）。

**判断依据**：`curl` 输出的 JSON 里 `"turn_id"` 字段必须和 URL 里的
`$TURN_A`/`$TURN_B` 完全一致。这条用例对应实现里专门修过的一个问题——
最初的实现里这类端点是按"该用户最近活跃的 session"兜底解析的，会导致
查询"不是最近那个 session"的 turn 时查到错误的会话甚至 404。

---

### T3-6 一个 session 崩溃不影响其他 session（不需要真实 API key）

**步骤**
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time
from pathlib import Path
from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent
from mini_agent.api.user_store import UserContext

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"
cfg.model = "llama3.1"
self_agent = Agent(cfg=cfg)

http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18905,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)
pool = http_server.session_pool

alice = UserContext(user_id="alice", name="Alice", role="family", trust_level=8, is_loopback=False)
bob   = UserContext(user_id="bob",   name="Bob",   role="colleague", trust_level=5, is_loopback=False)

entry_a = pool.get_or_create(alice, "sess-a")
entry_b = pool.get_or_create(bob, "sess-b")
time.sleep(0.3)

# 模拟 bob 的 session 崩溃：直接 kill 掉它的 runner 线程对应的状态
# （更真实的做法是让 run_turn 抛异常，这里用更直接的方式验证 pool 的兜底巡检）
import threading
entry_b.runner._stop_evt.set()   # 强制让它的主循环退出，模拟"线程意外结束"
entry_b.runner.join(timeout=3)

# 等健康巡检发现（_monitor_loop 默认 15s 一轮，测试用更短间隔验证逐项行为，
# 这里直接调用内部方法模拟巡检触发，不等真实 15s）
pool._check_health()

assert pool.get("sess-b") is None, "崩溃的 session 应该已经被从 pool 移除"
assert pool.get("sess-a") is not None, "alice 的 session 不应该被影响"
assert entry_a.is_alive, "alice 的 session 应该仍然存活"

print("PASS")
http_server.stop()
EOF
```

**预期结果**：打印 `PASS`。

**判断依据**：`sess-b` 从 pool 中消失，`sess-a` 完全不受影响且仍然存活——
这是"故障隔离"这条核心设计目标的直接验证。

---

## 模块 4：Self ↔ SessionAgent 通信（Phase 4）

> 前提：模块 1/2/3 全部通过。本模块全部用例**不需要真实 API key**。

### T4-1 Self 的 AgentRunner 确实拿到了 `self_message_bus`

> 这是专门为"消费端代码写对了，但构造时漏传参数，从未被真正触发"这个真实
> bug写的回归检查。

**步骤**
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time
from pathlib import Path
from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"
cfg.model = "llama3.1"
self_agent = Agent(cfg=cfg)

http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18950,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)

assert http_server._self_message_bus is not None
assert http_server._runner._self_message_bus is http_server._self_message_bus, \
    "BUG: Self 的 AgentRunner 没有拿到正确的 self_message_bus 实例！"
print("PASS")
http_server.stop()
EOF
```

**预期结果**：打印 `PASS`。

**判断依据**：`is`（对象身份），不是 `==`——必须是**同一个**实例，不是"看起来相等的另一个实例"。

---

### T4-2 session 正常结束后，Self 收到摘要并更新用户画像

**步骤**
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time, json
from pathlib import Path
from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent
from mini_agent.api.user_store import UserContext

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"
cfg.model = "llama3.1"
self_agent = Agent(cfg=cfg)

http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18951,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)

alice = UserContext(user_id="alice", name="Alice", role="family", trust_level=8, is_loopback=False)
http_server.session_pool.get_or_create(alice, "sess-summary-test")
time.sleep(0.3)

ok = http_server.session_pool.suspend("sess-summary-test")
assert ok

# 等 Self 的 AgentRunner 下一个 idle 周期 drain 到消息
time.sleep(2.0)

profile = http_server.role_profile_mgr.get_profile("alice")
print(json.dumps(profile, indent=2, ensure_ascii=False))
assert "recent_sessions" in profile
assert len(profile["recent_sessions"]) == 1
assert profile["recent_sessions"][0]["session_id"] == "sess-summary-test"
print("PASS")
http_server.stop()
EOF
```

**预期结果**：打印的 JSON 含 `recent_sessions` 数组，里面有一条
`session_id` 为 `sess-summary-test` 的记录，脚本末尾打印 `PASS`。

**判断依据**：`len(profile["recent_sessions"]) == 1` 精确匹配（不是"大于
0"）——如果同一条消息被处理了两次（比如消息总线重复投递），这里会是 2 或更多。

---

### T4-3 owner 的 session 不会被记录进 `RoleProfileManager`

**步骤**（接 T4-2 脚本风格，换成 owner）：
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time
from pathlib import Path
from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent
from mini_agent.api.user_store import UserContext

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"
cfg.model = "llama3.1"
self_agent = Agent(cfg=cfg)

http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18952,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)

owner = UserContext(user_id="owner", name="Owner", role="owner", trust_level=10, is_loopback=True)
http_server.session_pool.get_or_create(owner, "sess-owner-test")
time.sleep(0.3)
http_server.session_pool.suspend("sess-owner-test")
time.sleep(1.5)

owner_profile = http_server.role_profile_mgr.get_profile("owner")
print("owner recent_sessions:", owner_profile.get("recent_sessions"))
assert not owner_profile.get("recent_sessions"), "owner 的 session 不应该被记录（owner 有自己独立的画像系统）"
print("PASS")
http_server.stop()
EOF
```

**预期结果**：打印 `owner recent_sessions: None`（或空列表），脚本末尾打印 `PASS`。

**判断依据**：owner 的 `profile.json`（如果存在）不应该出现 `recent_sessions`
字段——这是有意的设计：owner 已经有 `profile.py` 那一套独立的、跨项目的
个性化画像系统，不应该和 `RoleProfileManager` 的角色画像混在一起。

---

### T4-4 session 崩溃后，Self 收到通知并写入活动日志

**步骤**
```bash
PYTHONPATH=src python3 << 'EOF'
import tempfile, time, json
from pathlib import Path
from mini_agent.api.server import HttpServer
from mini_agent.config.loader import load_config
from mini_agent.agent import Agent
from mini_agent.api.session_pool import SelfMessage

tmpdir = Path(tempfile.mkdtemp())
(tmpdir / ".agent").mkdir(parents=True, exist_ok=True)
cfg = load_config(project_root=tmpdir)
cfg.llm_provider = "ollama"
cfg.model = "llama3.1"
self_agent = Agent(cfg=cfg)

http_server = HttpServer(
    agent=self_agent, project_root=tmpdir, host="127.0.0.1", port=18953,
    configured_token="", allowed_ips=[], multi_user_enabled=True,
)
http_server.start()
time.sleep(0.5)

http_server._self_message_bus.send(SelfMessage(
    from_id="pool", to_id="self", msg_type="session_crashed",
    payload={"session_id": "sess-x", "user_id": "bob", "role": "colleague", "error": "RuntimeError: boom"},
))
time.sleep(2.0)

from mini_agent.storage.paths import AgentPaths
paths = AgentPaths(tmpdir)
digest_path = paths.workdir_dir / "activity_digest.jsonl"
assert digest_path.exists(), "activity_digest.jsonl 应该已经被创建"

records = [json.loads(l) for l in digest_path.read_text(encoding="utf-8").strip().split("\n")]
crash_records = [r for r in records if r.get("type") == "session_crashed"]
print(json.dumps(crash_records, indent=2, ensure_ascii=False))
assert len(crash_records) == 1
assert crash_records[0]["session_id"] == "sess-x"
assert crash_records[0]["user_id"] == "bob"
print("PASS")
http_server.stop()
EOF
```

**预期结果**：`activity_digest.jsonl` 文件存在，含一条 `type` 为
`session_crashed` 的记录，`session_id`/`user_id` 精确匹配，脚本末尾打印 `PASS`。

**判断依据**：文件是否真的被创建是关键判断点——这条用例对应实现里发现的
一个真实 bug（之前 `_handle_session_crashed` 读取 `agent._paths` 这个
根本不存在的属性，导致这段代码静默 no-op，文件从来没被创建过）。

---

### T4-5 `mini-agent self status` 端到端可用

**步骤**
```bash
mini-agent daemon start --http --http-multi-user --http-port 18954 --detach --project "$TESTPROJ"
sleep 2
mini-agent self status --project "$TESTPROJ"
mini-agent daemon stop --project "$TESTPROJ"
```

**预期结果**：输出四个分区：
```
Autonomous Loop
  autonomy_level : passive
  last_tick_at   : <时间戳>
  tick_count     : <数字，应该 >= 1>
  ...
Goals
  (no active goals/objectives)   # 全新项目，没有 goal 是正常的
Recent Activity (last 24h, 0 record(s))
  (none)
Session Pool
  active sessions: 0
```

**判断依据**：
- `Autonomous Loop` 分区不应该显示 `(not available — AutonomousLoop failed
  to initialize)`——如果出现这句话，说明 `HttpServer._build_autonomous_loop()`
  又回归到"读取 `agent._paths` 这个不存在的属性"的老 bug 了
- `tick_count >= 1`：daemon 跑了几秒之后，第一次 tick 应该已经发生（`AutonomousLoop`
  的 tick 间隔配置是 60 秒，但 `AgentRunner` 主循环里 `should_tick()` 的
  判断逻辑是"距上次 tick 是否已经过了 tick_interval"，**首次**判断时
  `_last_tick_at` 初始为 0，所以启动后第一次 idle 周期就会立刻 tick 一次）

---

## 回归测试

### R-1 完整 pytest 套件全绿

```bash
cd <repo_root>
pytest tests/ -q
```

**预期结果**：除了前置条件里提到的两个已知无关失败，其余全部通过。

**判断依据**：失败列表和"前置条件"第 1 条里列出的两个测试名**完全一致**——
多一个、少一个、或是不同的测试名都说明引入了新的回归，需要排查。

---

### R-2 单用户模式（完全不开 `--http-multi-user`）所有现有行为不变

```bash
mini-agent daemon start --http-port 18999 --detach --project "$TESTPROJ"
sleep 2
mini-agent daemon status --project "$TESTPROJ"
mini-agent --project "$TESTPROJ"   # 普通 CLI 连接模式，验证之前修过的两个 bug 仍然有效：
                                     # 1. 提示符应该显示 "You ❯"，不是 agent 名字
                                     # 2. 回复结束后应该出现新的 "You ❯" 提示，
                                     #    不会卡死（哪怕回复很短）
```
在 CLI 里输入 `exit` 退出，然后：
```bash
mini-agent daemon stop --project "$TESTPROJ"
```

**预期结果**：CLI 连接体验和本次多用户改造之前完全一样，没有任何变化。

**判断依据**：人工观察提示符文案和"回复后是否正确出现下一个输入提示"——
这是最早修的两个 bug，本次改造过程中多次重构了 `AgentRunner`/`daemon.py`，
理应在每个 Phase 完成后都验证一次没有回归，这里作为最终的把关检查。

---

## 附录：测试场景与对应实现文件

| 测试模块 | 对应实现 | 核心验证点 |
|---------|---------|----------|
| 模块 0 | `cli/daemon.py::run_daemon_cli`、`cli/app.py::_extract_project_root` | CLI 参数转发链路本身的正确性 |
| 模块 1 | `api/multi_auth.py`、`api/user_store.py::UserStore`、`api/routes.py` 的 `/v1/users` 端点 | 认证、owner 权限、token 生命周期 |
| 模块 2 | `api/server.py::AgentRunner.run`（system_extra 注入）、`tools/user_memory.py`、`api/user_store.py::RoleProfileManager` | 角色画像注入、记忆工具、画像更新 |
| 模块 3 | `api/session_pool.py::SessionAgentPool`、`api/server.py::AgentRunner`（`agent_factory`/`on_crash`） | 会话隔离、并发安全、故障隔离、按 turn/req_id 路由 |
| 模块 4 | `api/server.py::AgentRunner._drain_self_messages`、`api/session_pool.py::SelfMessageBus`、`cli/commands/self_cmd.py` | Self 消费会话消息、CLI 状态查看 |

---

## 附录：实现过程中发现并修复的真实 bug 清单（供回归测试时重点关注）

以下问题均不是设计层面的假设错误，而是写自动化测试时**实际跑出来**才发现的，
人工 code review 没有发现——记录在这里，方便以后改动这些模块时优先针对这些
点补充测试，避免回归：

1. `cli/daemon.py::AgentRunner`（注：实际是 `api/server.py`）的 `self._stop`
   命名遮蔽了 `threading.Thread._stop()`，导致 `.join()` 崩溃（仅在真正调用
   `.join()` 时才暴露，Phase 1/2 从未调用过）。
2. `SessionAgentPool.get_or_create()` 持锁等待 Agent 构造完成，与构造失败回调
   需要同一把锁产生死锁（仅在 Agent 构造失败时才暴露）。
3. `ChatRequest`/`StatusResponse` 缺少 `session_id` 字段，CLI 早就在发/读这个
   字段，但服务端模型没声明，一直被静默忽略/留空。
4. `/v1/stream/{turn_id}`、`/v1/turns/{turn_id}`、`/v1/permissions/{req_id}`
   按"该用户最近活跃的 session"兜底解析，在用户同时有多个 session 时会查错
   （仅在同一用户存在 ≥2 个 session 时才暴露）。
5. `HttpServer._build_autonomous_loop()` 和 `AgentRunner._handle_session_crashed()`
   都错误假设 `Agent` 实例有 `_paths` 缓存属性（实际从不存在），导致
   `AutonomousLoop` 在 daemon 模式下从未被真正构造过、`session_crashed` 摘要
   从未被写入活动日志（这两个问题用 `getattr(obj, "wrong_name", None)` 的
   写法时不会报错，只会静默走 fallback 分支，必须靠端到端测试断言具体产出
   才能发现）。
6. `HttpServer.__init__` 构造 Self 的 `AgentRunner` 时漏传
   `self_message_bus` 参数，导致 Phase 4 的消费端代码从未被真正触发
   （同样是"逻辑写对了但没接上电"的静默失效模式）。
7. `cli/daemon.py::run_daemon_cli` 的 `start` 子命令用 `parse_args()`（不是
   `parse_known_args()`），导致 `--http-multi-user` 这个本该转发给 daemon
   子进程的参数被直接拒绝；同一处还有 `argparse` 默认的缩写匹配
   （`allow_abbrev=True`）把独立的 `--http` 误判成 `--http-port` 的前缀。
8. `cli/app.py` 里 `daemon`/`user`/`self` 三处子命令短路逻辑，扫描
   `--project` 参数后只是读取了值，没有把这两个 token 从转发给子命令处理
   函数的 argv 里剔除，导致 `mini-agent user ... --project <path>` 这种
   最基本用法报 `unrecognized arguments`。

**这份清单本身就是最好的论据**：所有这些 bug 都属于"代码逻辑没问题，但因为
一个错误的属性/参数假设，从一开始就没有被真正执行过"——这类问题只看代码
review 基本看不出来，必须有端到端测试断言具体产出（文件是否被创建、字段
是否被填充、对象是否是同一个实例）才能发现。本文档里的每一条用例都尽量
体现这一点：判断依据优先选择"具体的、可程序化验证的产出"，而不是"看起来
没报错"。
