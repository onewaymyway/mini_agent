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

## 多订阅源:抓取 + 验证前去重

`sources.json` 支持配置任意多个订阅源(见下面"订阅源类型"),`_do_refresh` 会依次抓取
所有源、合并、**验证之前先按 `protocol+server+port` 去重**——免费公共节点站之间经常
互相转发同一批节点,不去重的话验证阶段会重复起好几次本地代理进程去测同一个真实节点,
既浪费时间也容易撞上并发资源限制。

`refresh` 现在会打印去重前后的统计,方便判断"订阅源之间重叠率高不高":

```
fetching from 2 subscription source(s)...
fetched 340 raw node(s) across sources {'mibei77': 183, 'my-backup': 200}, 43 duplicate(s) removed -> 297 unique node(s)
validating (concurrency=8)...
```

去重逻辑在 `subscription.fetch_all(sources, return_stats=True)` 里,`return_stats=True`
时返回 `(节点列表, {"raw_total", "deduped_total", "duplicates_removed", "per_source"})`。

## 订阅源类型:可插拔注册表,方便以后接入新的抓取方式

`sources.json` 里每条配置是 `{"type": "...", ...}`,`type` 对应哪个
`SubscriptionSource` 实现,由 `subscription.py` 里的一个注册表决定,**不是**写死在
`proxy_ctl.py` 的 if/elif 里。目前内置三种:

| type          | 说明 |
|---------------|------|
| `url`         | 最通用: 给一个订阅 URL,GET 下来解析(`{"type": "url", "name": "...", "url": "..."}`) |
| `mibei77`     | 抓 mibei77.com 首页,自动定位当天最新帖子里的订阅直链 |
| `discovered`  | 读取 `discovered_sources.json` 里的地址列表(见下一节) |

**新增一种订阅源类型**只需要在 `subscription.py` 里写一个实现了 `fetch()` 的类,
再用装饰器注册进去,不需要碰 `proxy_ctl.py` 或 `cli/commands/proxy.py`:

```python
@register_source_type("my_new_type")
def _build_my_source(entry: dict, paths=None) -> SubscriptionSource | None:
    if "some_field" not in entry:
        return None  # 缺字段就跳过,不抛异常影响其它源
    return MyNewSource(entry["some_field"])
```

`_build_sources()` 在 `proxy_ctl.py` 里统一通过 `build_source_from_entry(entry, paths)`
查表构造,未知 `type` 会打印警告并跳过,不影响其它已识别的源。

## 让 agent 自己去发现订阅源地址(为未来的"发现订阅"skill 留的接口)

`DiscoveredSource`(`type: "discovered"`)读取 `.agent/proxy/discovered_sources.json`
(格式: `[{"name", "url", "discovered_at", "discovered_by"}, ...]`),这个文件和手动维护
的 `sources.json` 分开存放,专门给"自动发现"这条路径用:

- 一个专门的 skill(比如用 web_search / browsing 去找当天可用的订阅链接)只需要调用
  `DiscoveredSource.append_entry(paths.workdir_proxy_discovered_sources, name, url, discovered_by="skill_name")`
  把发现的地址追加进去(按 `(name, url)` 去重,重复调用不会产生重复条目),完全不需要
  懂 `proxy_ctl.py` 内部实现或碰 `sources.json`。
- 在 `sources.json` 里加一条 `{"type": "discovered"}`(用 `sources add-discovered` /
  `/proxy sources add-discovered` 一键添加)就能让 `refresh` 在抓取时读取这个文件里
  当时已有的所有地址。
- 这样"抓订阅源地址"和"用订阅源地址抓节点、验证"两件事彻底解耦:以后想换一种发现
  方式(不同的 skill、不同的爬取策略),只要保证最终写进 `discovered_sources.json`
  的格式一致即可,不需要改这条流水线的其它任何部分。

## 接入代理:三个开关,默认全部关闭,都可以用命令控制

对应下面"接入点 1/2/3"的三个开关集中在 `.agent/proxy/integration.json` 里,
**装了这个模块不会让任何请求悄悄开始走代理**——"要不要让 agent 的流量走一个不受控的
免费节点池"是需要用户显式打开的产品决策。

| 开关 | 默认值 | 对应 |
|------|--------|------|
| `llm_use_proxy` | `false` | 接入点 1: 主 LLM 请求是否走代理池 |
| `web_search_use_proxy` | `false` | 接入点 2: web_search/抓取工具是否走代理池并在被限流时轮换节点 |
| `fixed_entry_forwarder_enabled` | `false` | 接入点 3: 是否起固定端口转发给外部应用 |
| `fixed_entry_forwarder_port` | `1080` | 接入点 3 的监听端口 |

**查看/修改开关,不需要手动编辑 json 文件**,两种等价方式:

```bash
# 独立脚本
python scripts/proxy_ctl.py integration            # 查看所有开关
python scripts/proxy_ctl.py integration set llm_use_proxy true

# REPL 里
/proxy integration
/proxy integration set web_search_use_proxy true
```

代码里判断开关、取用代理集中在 `src/mini_agent/proxy/integration.py`:

```python
from mini_agent.proxy.integration import should_use_proxy_for_llm

proxy_url = pool.get_best_socks_url() if should_use_proxy_for_llm(paths) else None
client = httpx.AsyncClient(proxy=proxy_url, ...)   # None 时等价于不传 proxy,直连
```

真正"拿到一个能用的本地 socks5 端口"这件事仍然由调用方持有的 `ProxyPool` 实例负责
(`get_best_socks_url()` / `get_rotating_socks_url()`),`integration.py` 只负责
"要不要问 pool 要"这一层判断,不重新实现选节点逻辑,避免和 `pool.py` 出现两套不一致
的实现。

## 接入点 1: 主 LLM 请求走代理

`llm/providers/*.py` 里创建 httpx client 的地方,注入:

```python
from mini_agent.proxy.integration import should_use_proxy_for_llm

proxy_url = pool.get_best_socks_url() if should_use_proxy_for_llm(paths) else None
client = httpx.AsyncClient(proxy=proxy_url, ...)
```

开关默认关闭,用 `/proxy integration set llm_use_proxy true`(或
`proxy_ctl.py integration set llm_use_proxy true`)显式打开——正常情况下直连
Anthropic/OpenAI API 大概率比经过一层不稳定的免费节点更快更稳。

## 接入点 2: 抓取/web_search 工具走代理并自动轮换

`web_search/providers/*.py` 或未来新增的 http 抓取工具里:

```python
from mini_agent.proxy.integration import should_use_proxy_for_web_search

async with httpx.AsyncClient(
    proxy=pool.get_rotating_socks_url() if should_use_proxy_for_web_search(paths) else None
) as client:
    resp = await client.get(target_url)
    if resp.status_code in (403, 429) and should_use_proxy_for_web_search(paths):
        # 换下一个节点重试
        resp = await client.get(target_url, proxy=pool.get_rotating_socks_url())
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
# 配置订阅源(可以加多个: 通用 URL、mibei77 页面抓取、或 agent/skill 自动发现的地址)
python scripts/proxy_ctl.py sources add-mibei77
python scripts/proxy_ctl.py sources add my-backup https://example.com/my-sub
python scripts/proxy_ctl.py sources add-discovered
python scripts/proxy_ctl.py sources list

# 抓取 + 去重 + 验证 + 生成可用列表 (<project_root>/.agent/proxy/available.json)
python scripts/proxy_ctl.py refresh --keep-alive 3 --concurrency 8

# 查看最近一次 refresh 的结果
python scripts/proxy_ctl.py status

# 查看/修改代理接入其它模块的开关(默认全部关闭)
python scripts/proxy_ctl.py integration
python scripts/proxy_ctl.py integration set llm_use_proxy true

# 起一个固定端口(默认1080),转发到 available.json 里延迟最低的节点
# 其它应用把代理配置指向 socks5://127.0.0.1:1080 即可,零改造接入
python scripts/proxy_ctl.py serve --listen-port 1080 --keep-alive 3
```

生成的文件都在 `<project_root>/.agent/proxy/`(通过 `storage/paths.py` 的 `AgentPaths` 统一管理,
和项目里其它全局状态一致的路径规范):`sources.json`、`available.json`、`proxy.log`。

### 集成到 mini_agent 命令: `/proxy`

在 REPL 交互模式里可以直接用 slash 命令,不用切出去开终端:

```
/proxy                          等价于 /proxy status
/proxy status                   查看最近一次 refresh 的可用节点
/proxy refresh                  立即重新抓取+验证(阻塞,可能要几十秒)
/proxy sources                  列出已配置的订阅源
/proxy sources add-mibei77      添加 mibei77.com 作为订阅源
/proxy sources add-discovered   接入 discovered_sources.json(agent/skill 自动发现的地址)
/proxy integration              查看代理接入其它模块的开关(默认全部关闭)
/proxy integration set <key> <value>   设置一个开关,例如 llm_use_proxy true
```

`/proxy` 及其全部子命令已经注册进 `ui/terminal.py` 的命令补全表(`_COMMANDS`),
在 REPL 里输入 `/proxy ` 按 Tab 能列出 `status` / `refresh` / `sources` /
`integration` 这几个子命令候选,`/help` 里也能看到完整的用法说明,不需要记忆。

实现上 `cli/commands/proxy.py` 直接复用 `scripts/proxy_ctl.py` 里的核心函数
(`_do_refresh` / `_load_sources_config` 等)和 `proxy/integration.py` 里的开关
读写函数,遵循仓库里 `evolution/state_repo.py` 从 `scripts.protected_paths` 里
import 的先例,没有重复实现一遍逻辑。

### 尚未接入 provider 代码本身的部分

`integration.py` 里的三个开关和 `should_use_proxy_for_*()` 判断函数已经就绪、
可以直接用命令控制,但 `llm/providers/*.py` 和 `web_search/providers/*.py` 里
创建 httpx client 的地方**还没有实际调用这些函数**——这两处需要各自改动多个 provider
文件,属于下一步再做的事。目前先把"数据从哪来、怎么验证、怎么落盘、开关怎么控制"
这条链路跑通、可独立测试,provider 侧接入时按上面接入点 1/2 的代码示例改造即可,
不需要再设计新的开关机制。

## 节点验证为什么不能只测 TCP connect

很多被墙节点能建立 TCP 连接、甚至能过 TLS 握手,但实际数据层面会被 RST 或
限速到不可用。所以 `validator.py` 里是"起本地 SOCKS5(纯 Python 实现,见
`local_proxy.py`)-> 真实发一次 HTTP 请求(默认打 `www.gstatic.com/generate_204`)
-> 记录延迟",这样得到的可用性判断才是准的。代价是验证一批节点比单纯 ping 慢,
所以做了并发度限制(`concurrency` 参数)避免同时起太多本地 server。