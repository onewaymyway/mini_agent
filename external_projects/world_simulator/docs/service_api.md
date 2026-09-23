# 作为独立外部服务调用（HTTP / 命令行）

这份文档回答"怎么在不打开 Streamlit 看板的情况下，从外部程序（脚本、
主 agent、别的服务）调用世界模拟器"。设计背景和取舍见
[`PROJECT.md`](../PROJECT.md) 对应变更日志；这里只讲怎么用。

## 三层结构

```
外部调用方（HTTP 请求 / 命令行 / 未来主 agent 直接 import）
        │                        │
        ▼                        ▼
world_simulator/server.py   world_simulator/service_cli.py
        └───────────┬────────────┘
                     ▼
          world_simulator/tool_api.py   ← 唯一承载业务逻辑的一层
                     │
                     ▼
          world_simulator/engine（+ html_export 等）
```

`tool_api.py` 是唯一真正做事的一层：入参只用基本类型，返回值统一是
`{"ok": true, "data": {...}}` 或
`{"ok": false, "error": {"type": "...", "message": "..."}}`，**不抛
异常**。`server.py`/`service_cli.py` 都只是把这个 dict 分别包成 HTTP
响应 / JSON 打到 stdout，不重复实现任何业务判断——两种调用方式行为
完全一致，选哪种只取决于"外部调用方方不方便发 HTTP 请求"。

## 命令行调用

不需要额外依赖，`pip install -r requirements.txt` 之后就能用：

```bash
# 创建一个模拟实例
python -m world_simulator.service_cli create-simulation \
    --intent "一个刚毕业的应届生找工作" --template life_sim

# 推进一步（不指定 --choice 时引擎给出默认走向）
python -m world_simulator.service_cli advance --sim-id life_sim_xxxx

# 连续推进最多 5 步，遇到需要人决策的重大节点就停
python -m world_simulator.service_cli fast-forward --sim-id life_sim_xxxx --max-steps 5

# 查询（--include-history 带上历史）
python -m world_simulator.service_cli get --sim-id life_sim_xxxx --include-history

# 列出所有实例
python -m world_simulator.service_cli list

# 导出网页存档
python -m world_simulator.service_cli export-html --sim-id life_sim_xxxx --output ./out.html
```

`--pretty` 放在子命令**之前**可以让输出带缩进（`python -m
world_simulator.service_cli --pretty list`），不加则是单行 JSON，
方便管道给 `jq`/脚本解析。**退出码**：成功 0，失败 1，脚本里可以先
看退出码再决定要不要解析 JSON 细节。完整子命令列表：
`python -m world_simulator.service_cli --help`。

## HTTP 服务调用

需要额外依赖：`pip install -r requirements.txt -r requirements-server.txt`

```bash
python -m world_simulator.server --host 0.0.0.0 --port 8731
# 或者：uvicorn world_simulator.server:app --host 0.0.0.0 --port 8731
```

主要路由（详见 `world_simulator/server.py` 里每个路由的 docstring）：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/simulations` | 创建实例 |
| GET | `/simulations` | 列出实例 |
| GET | `/simulations/{sim_id}` | 查询实例（`?include_history=true`） |
| POST | `/simulations/{sim_id}/advance` | 推进一步 |
| POST | `/simulations/{sim_id}/fast-forward` | 连续推进 |
| PUT | `/simulations/{sim_id}/status` | 设置 active/paused/ended |
| PUT | `/simulations/{sim_id}/pilot` | 设置手动挡/自动挡 |
| PATCH | `/simulations/{sim_id}/settings` | 更新 settings |
| PUT | `/simulations/{sim_id}/title` | 重命名 |
| DELETE | `/simulations/{sim_id}` | 删除（不可逆） |
| GET | `/simulations/{sim_id}/export.html` | 直接返回渲染好的网页 |

失败时用 HTTP 状态码表达大致类别（400 参数错误 / 404 找不到实例 /
409 引擎业务错误 / 502 场景生成失败 / 503 环境未就绪 / 500 未预期
错误），响应体始终是同一份 `tool_api.py` 的 JSON 结构，不因为状态码
不同就变换字段——不想按状态码判断的话，直接看 `ok` 字段即可。

服务启动后可以访问 `/docs`（FastAPI 自带的交互式文档）直接试调用，
不需要额外配置。

## 鉴权与部署边界

这一版不内置鉴权，目标是"同机/内网环境下方便主 agent 调用"，不是
"暴露到公网"。真要跑在不受信任的网络环境里，建议在这个进程前面挂一层
反向代理做鉴权/限流。

## 和 `entrypoints/` 的区别

`entrypoints/*.py` 是给 mini_agent 框架的 daemon 调度器用的（对应
`project.yaml` 里的定时任务，输出是给人看的文本格式），不是给"外部
服务"场景设计的；两者可以共存，互不影响，选哪个取决于你的调用方是
"daemon 定时调度"还是"随时按需调用"。
