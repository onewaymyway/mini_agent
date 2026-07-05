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
- `vless`(tcp/ws + tls/none)—— 头部本身不加密(安全性依赖外层 TLS),实现风险比 vmess 低很多。
  不支持 `xtls-rprx-vision` 这类 flow。
- Python 依赖: `httpx[socks]`(注意是 `[socks]` extra,不是裸 `httpx`——验证节点时是通过本地
  SOCKS5 端口发真实 HTTP 请求,httpx 走 SOCKS5 代理必须装 `socksio` 包,漏装会报
  `the 'socksio' package is not installed`)、`cryptography`(做 FastAPI 服务时再加 `fastapi`+`uvicorn`)。

**`vmess` / `vless(reality+vision)` / `hysteria2` 的处理方式(外部引擎 fallback)**:

这几种情况纯 Python 明确做不了,原因各不相同:
- `vmess`:自定义 AEAD 头部加密(自定义 KDF 链 + AES-ECB 认证 ID + 长度域单独加密),
  没有真实服务端反复联调很难保证做对。
- `vless` 带 `flow=xtls-rprx-vision`:需要按 Vision 规则做流量填充/拼接来抵抗流量特征识别。
- `vless` 带 `security=reality`(REALITY):需要伪造"借用"真实网站证书的 TLS ClientHello,
  依赖类似 uTLS 的指纹伪装能力。
- `hysteria2`:基于 **QUIC(UDP)** 的协议,和 TCP 系的 ss/vless/trojan 完全是两套技术栈,
  需要 `aioquic` 库从头实现认证+流复用,工作量不比 vmess 小。

这几个都属于"手搓风险远大于收益"的情况——做错了是最难排查的那种错误(看起来实现了,
实际连不上)。所以采用外部引擎 fallback,优先用 **sing-box**
(https://github.com/SagerNet/sing-box/releases),因为它一个二进制就能覆盖上面全部
协议(包括 hysteria2 和 vless-reality-vision,这是选它而不是 xray-core 的原因——
xray-core 不支持 hysteria2)。如果机器上只有 `xray` 没有 `sing-box`,`vmess` 和
"不带 reality/vision 的普通 vless" 仍可以退化用 xray 处理,但 hysteria2 和
vless-reality-vision 只有 sing-box 能覆盖。

**下载哪个版本 / 放在哪里**:

- Windows 64位(现在的电脑基本都是,Win10/11)选 `sing-box-x.x.x-windows-amd64.zip`;
  `-386` 是 32 位系统专用,`-legacy-windows` 是给 Windows 7/8 用的兼容版,`-arm64` 是
  ARM 芯片设备,一般用不到。
- 解压后把 `sing-box.exe` 放到 **项目根目录下的 `tools/` 文件夹**(即
  `<project_root>/tools/sing-box.exe`),不需要加系统 `PATH` 环境变量——
  `external_engine.py` / `xray_runner.py` 除了查 `PATH`,也会自动在
  `<project_root>/tools/` 下找同名可执行文件(Windows 自动补 `.exe` 后缀)。
  也支持子目录形式 `tools/sing-box/sing-box.exe`(有些发行包解压后自带一层文件夹,
  懒得手动挪出来也行)。
- `tools/` 目录建议加进 `.gitignore`(二进制文件不该进版本库)。

判断"这个节点纯 Python 能不能处理"用的是 `local_proxy.can_handle_pure_python(node)`——
**按节点的具体参数判断,不是按协议名**:同样是 vless,不带 flow/reality 的能处理,
带的不能,所以不能用"协议名在不在支持列表里"这种粗粒度判断,否则会把能处理的
vless 也一并跳过。

## 数据保存位置(项目本地,不是全局 `~/.agent/`)

`sources.json` / `available.json` / `all_nodes.json` / `proxy.log` 都保存在
**`<project_root>/.agent/proxy/`**(通过 `storage/paths.py` 的 `AgentPaths.workdir_proxy_*`
系列属性管理),不是用户主目录下的全局 `~/.agent/`——这样不同项目/不同工作目录的代理池
数据互不影响,也方便直接 `.gitignore` 掉(已经加了 `.agent/proxy` 到 `.gitignore`)。

- `all_nodes.json`:**订阅里解析出的全部代理配置**,不管是否验证通过,包含
  `protocol_breakdown`(各协议数量统计)。用来排查"到底是协议没支持,还是节点本身失效"。
- `available.json`:验证通过、按延迟排序的可用节点,同样带 `protocol_breakdown`。

## 为什么"抓到很多节点,验证后 0 个可用"是可能发生的,但要看具体原因

`refresh` 的日志会把原因拆成两类,不要混着看:

1. **"协议/特性不支持,被跳过"**——比如订阅里 183 个节点,112 个是 vless 但带
   `flow=xtls-rprx-vision`(通常搭配 REALITY),70 个是 hysteria2,本机没装
   sing-box/xray,这 182 个会直接被跳过,不会消耗时间去连。这不是"节点失效",
   是"这个环境目前处理不了这个协议/特性组合"。装 sing-box(优先,覆盖面最广)
   能把这部分节点用起来,见上面"外部引擎 fallback"一节。
2. **"协议支持,但实际测试连不上"**——这才是真正的"节点失效"。免费公共节点站的节点
   通常是共享给大量用户使用的,存活率天然就低(容易被限速/封禁,很多几小时到一两天就失效),
   一批节点里只有一小部分能连是正常现象,和我们的实现是否正确关系不大。

**注意**:如果你用 v2rayN/Clash 等成熟客户端测试同一批节点发现"有些能连",但这边全部
显示"跳过"或"连不上",大概率不是节点问题,而是协议覆盖率问题——先看
`all_nodes.json` 里的 `protocol_breakdown`,如果 vless 节点大多带 `flow` 字段或
`security=reality`,或者有大量 hysteria2,那就是纯 Python 暂不支持这些特性导致的,
装个 sing-box 基本能解决。

`refresh` 命令现在会分别打印这两个数字(`X 个跳过:协议不支持` / `Y 个测试但连不上`),
`all_nodes.json` 里的 `protocol_breakdown` 也能让你一眼看出"这批订阅到底以什么协议为主",
从而判断值不值得为了提高可用率去装 sing-box、或者换一个协议分布更友好的订阅源。

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

# 抓取 + 验证 + 生成可用列表 (<project_root>/.agent/proxy/available.json)
python scripts/proxy_ctl.py refresh --keep-alive 3 --concurrency 8

# 查看最近一次 refresh 的结果
python scripts/proxy_ctl.py status

# 起一个固定端口(默认1080),转发到 available.json 里延迟最低的节点
# 其它应用把代理配置指向 socks5://127.0.0.1:1080 即可,零改造接入
python scripts/proxy_ctl.py serve --listen-port 1080 --keep-alive 3
```

生成的文件都在 `<project_root>/.agent/proxy/`(通过 `storage/paths.py` 的 `AgentPaths` 统一管理,
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
  httpx client 的地方,可以读取 `AgentPaths().workdir_proxy_available_list`
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