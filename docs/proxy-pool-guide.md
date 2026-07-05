# 代理池设计与接入指南

## 整体流程

```
订阅源(可插拔) --fetch--> ProxyNode[] --validate(起本地xray+真实HTTP请求)--> 可用节点(按延迟排序)
                                                            |
                                                            v
                                          ProxyPool 常驻 top-N 个本地 SOCKS5 端口
                                          ├── get_best_socks_url()      给主 LLM 用
                                          └── get_rotating_socks_url()  给抓取工具轮换用
                                                            |
                                                            v
                                (可选) service.py 起固定入口端口(如 1080),
                                        对外统一转发到当前 best 节点 —— 其它应用零改造接入
```

## 依赖

**默认走纯 Python 实现(`local_proxy.py`),不需要装 xray / v2ray 任何外部可执行文件**:
- `ss`(shadowsocks 经典 AEAD: aes-128-gcm / aes-256-gcm / chacha20-ietf-poly1305)—— 自己实现了
  密钥派生(EVP_BytesToKey)+ HKDF 子密钥 + AEAD 分片加解密,基于 `cryptography` 库。
- `trojan` —— 本质是标准 TLS 连接 + 一个简单的文本头(密码的 SHA224 + 目标地址),
  用标准库 `ssl` + `asyncio` 就能实现,不需要额外加密逻辑。
- Python 依赖: `httpx`、`cryptography`(做 FastAPI 服务时再加 `fastapi`+`uvicorn`)。

**不支持的协议**: `vmess` / `vless`。这两种协议的握手/鉴权格式更复杂,自己重实现性价比不高,
遇到时会在验证阶段直接判定为不可用并跳过(不会误报"能用")。仓库里仍保留了旧版
`xray_runner.py`(基于 xray-core 子进程),接口和 `local_proxy.py` 一致,如果订阅源里
vmess/vless 占比很高,可以在 `pool.py` 里按协议类型分流: ss/trojan 用纯 Python,
vmess/vless 按需回退到 xray。

## 基本用法

```python
from mini_agent.proxy import ProxyPool, MiBei77Source, URLSubscriptionSource

pool = ProxyPool(
    sources=[
        MiBei77Source(),  # 从 mibei77.com 首页找当日订阅直链再抓取
        URLSubscriptionSource("my-backup", "https://example.com/my-sub"),
    ],
    keep_alive_count=3,       # 常驻保持3个可用节点
    refresh_interval_sec=1800,  # 每30分钟重新抓取+验证一次
)
await pool.refresh()            # 首次同步刷新
await pool.start_auto_refresh() # 后台定时刷新
```

## 接入点 1: 主 LLM 请求走代理

`llm/providers/*.py` 里创建 httpx client 的地方,注入:

```python
proxy_url = pool.get_best_socks_url()
client = httpx.AsyncClient(proxy=proxy_url, ...)
```

建议做成 `providers.json` 里的一个开关(如 `"use_proxy_pool": true`),而不是强制所有请求都走代理——
正常情况下直连 Anthropic/OpenAI API 大概率比经过一层不稳定的免费节点更快更稳。

## 接入点 2: 抓取/web_search 工具走代理并自动轮换

`web_search/providers/*.py` 或未来新增的 http 抓取工具里:

```python
proxy_url = pool.get_rotating_socks_url()
async with httpx.AsyncClient(proxy=proxy_url) as client:
    resp = await client.get(target_url)
    if resp.status_code in (403, 429):
        # 换下一个节点重试
        proxy_url = pool.get_rotating_socks_url()
        ...
```

## 接入点 3: 给其它应用(非本项目内)提供统一代理入口

不需要做 v2ray 那种 TUN 透明代理(需要 root、建虚拟网卡、改路由表,维护成本高,
且很多沙盒/容器环境根本没有 TUN 权限)。更实际的做法是跑 `service.py` 里的
`run_fixed_entry_forwarder`,起一个固定端口(如 1080),把系统或某个应用的
`http_proxy`/`all_proxy` 环境变量指向 `127.0.0.1:1080` 配置一次即可——
背后节点池怎么换节点、怎么测活,应用完全无感知。这个"固定端口转发"本质上
就是一个极简版的"专属代理服务",足够覆盖"应用无感知使用代理"这个需求,
不需要引入 v2ray/TUN 那一整套更重的机制。

如果确实需要连"完全没有代理设置能力的应用"都无感接入(比如某些不支持配置
代理的桌面软件),那才需要上 TUN 模式,需要额外的系统权限和路由表管理,
成本会高不少,建议只在明确有这个硬需求时再做。

## 控制脚本与命令集成

### 独立脚本: `scripts/proxy_ctl.py`

完整的"订阅抓取 -> 验证 -> 生成可用列表 -> 常驻服务"流程被封装成一个独立脚本,
不依赖 agent 主循环,方便用 cron/定时任务单独跑:

```bash
# 配置订阅源(可以加多个: 通用 URL 或 mibei77 页面抓取)
python scripts/proxy_ctl.py sources add-mibei77
python scripts/proxy_ctl.py sources add my-backup https://example.com/my-sub
python scripts/proxy_ctl.py sources list

# 抓取 + 验证 + 生成可用列表 (~/.agent/proxy/available.json)
python scripts/proxy_ctl.py refresh --keep-alive 3 --concurrency 8

# 查看最近一次 refresh 的结果
python scripts/proxy_ctl.py status

# 起一个固定端口(默认1080),转发到 available.json 里延迟最低的节点
# 其它应用把代理配置指向 socks5://127.0.0.1:1080 即可,零改造接入
python scripts/proxy_ctl.py serve --listen-port 1080 --keep-alive 3
```

生成的文件都在 `~/.agent/proxy/`(通过 `storage/paths.py` 的 `AgentPaths` 统一管理,
和项目里其它全局状态一致的路径规范):`sources.json`、`available.json`、`proxy.log`。

### 集成到 mini_agent 命令: `/proxy`

在 REPL 交互模式里可以直接用 slash 命令,不用切出去开终端:

```
/proxy                      等价于 /proxy status
/proxy status               查看最近一次 refresh 的可用节点
/proxy refresh              立即重新抓取+验证(阻塞,可能要几十秒)
/proxy sources              列出已配置的订阅源
/proxy sources add-mibei77  添加 mibei77.com 作为订阅源
```

实现上 `cli/commands/proxy.py` 直接复用 `scripts/proxy_ctl.py` 里的核心函数
(`_do_refresh` / `_load_sources_config` 等),遵循仓库里 `evolution/state_repo.py`
从 `scripts.protected_paths` 里 import 的先例,没有重复实现一遍逻辑。

### 尚未接入、留好扩展点的部分

以下两处目前还是手动接线,没有默认打开,因为"要不要让 agent 的请求默认走一个
免费代理"是个产品决策,不适合在这里替用户做主:

- **主 LLM 请求走代理**:`llm/providers/*.py` 或 `llm/client_pool.py` 里创建
  httpx client 的地方,可以读取 `AgentPaths().global_proxy_available_list`
  里延迟最低的节点,建一个 `providers.json` 里的开关(如 `"use_proxy_pool": true`)。
- **抓取类工具走代理并自动轮换**:`web_search/providers/*.py` 里请求失败/被限流时,
  从 `available.json` 换下一个节点重试。

这两处需要改动多个 provider 文件,属于下一步再做的事,目前先把"数据从哪来、
怎么验证、怎么落盘"这条链路跑通、可独立测试。

## 节点验证为什么不能只测 TCP connect

很多被墙节点能建立 TCP 连接、甚至能过 TLS 握手,但实际数据层面会被 RST 或
限速到不可用。所以 `validator.py` 里是"起本地 SOCKS5(纯 Python 实现,见
`local_proxy.py`)-> 真实发一次 HTTP 请求(默认打 `www.gstatic.com/generate_204`)
-> 记录延迟",这样得到的可用性判断才是准的。代价是验证一批节点比单纯 ping 慢,
所以做了并发度限制(`concurrency` 参数)避免同时起太多本地 server。
