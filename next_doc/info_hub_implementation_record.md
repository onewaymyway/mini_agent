# info_hub 实施记录

- **关联方案**：`next_doc/info_hub_external_project_plan.md`（v2.0）
- **项目位置**：`external_projects/info_hub/`
- **约定**：每完成一个阶段，更新本文件的阶段状态与实施要点，并在 `/mnt/user-data/outputs/` 生成该阶段的打包文件（路径相对 mini_agent 根目录，直接覆盖即可）。

## 阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 项目骨架、纯文本存储工具、配置加载 | 已完成 |
| P1 | 适配器框架、rss / json_api 适配器、信息源注册与 run 命令 | 已完成 |
| P2 | 归一化、去重、增量对比（hot_list 排名变化） | 已完成 |
| P3 | 共享浏览器会话、html_list 适配器、login 命令 | 已完成（有头登录未在沙箱验证） |
| P4 | 定时报告、调度判定、tick 命令 | 已完成 |
| P5 | 看板基础版 | 待开始 |
| P6 | 听报（Web Speech） | 待开始 |
| P7 | 添加信息源（表单 / 对话生成 / 查重） | 待开始 |
| P8 | 订阅源 | 待开始 |
| P9 | 健壮性与收尾 | 待开始 |

## 待确认事项的默认取值

用户确认"开始实施"时未逐条回复第 17 节的待确认事项，本实施按以下默认值推进，确认后可再调整：

1. 项目位置：`external_projects/info_hub/`
2. 订阅站点：P8 再决定，先做 Checker 接口
3. 登录站点：按"有头浏览器手动登录一次"实现（P3）
4. 报告计划：`rolling` 每 3 小时、`daily` 每天 08:00（已写入 `config/settings.yaml`）
5. LLM 摘要：不实现，保留配置位置但默认关闭
6. 生成脚本的 LLM：P7 前再决定

---

## P0：项目骨架（已完成）

### 交付内容
- `project.yaml`：对外契约，声明 serve / run_source / report 入口，默认不启用调度（opt-in）。
- `config/settings.yaml`：路径、服务端口、默认抓取间隔、报告计划。
- `config/categories.yaml`：分类定义（热点社区、视频、小说、技术、资讯、其他）。
- `sources/_index.json`：信息源注册表，当前为空。
- `core/store.py`：原子写入（临时文件 + os.replace）、JSON / JSONL 读写、基于 fcntl 的文件锁。
- `core/settings.py`：加载配置，解析路径，提供 `Paths` 与 `Settings`。
- `tests/test_store.py`、`tests/test_settings.py`：共 7 个用例，全部通过。
- `.gitignore`：排除 `data/`、`reports/`、`browser/profile/` 等运行产物。
- `PROJECT.md`：项目说明与命令约定。

### 关键决策
- 测试框架选用标准库 `unittest`，不引入 pytest，依赖只有 PyYAML。
- 存储全部为文本，JSON 使用 `ensure_ascii=False`，中文可直接阅读。
- 文件锁使用独立的 `.lock` 文件，避免锁文件与数据文件互相干扰。

### 验收结果
```
python -m unittest discover -s tests -t .
Ran 7 tests ... OK
```

### 已知限制
- 文件锁依赖 fcntl，Windows 下退化为无锁，当前按 Linux / Termux 环境设计。

---

## P1：适配器框架与首批适配器（已完成）

### 交付内容
- `adapters/base.py`：`Adapter` 基类、`SourceSpec`、`RawItem`、`FetchResult`、`FetchContext`。HTTP 通过 `FetchContext.http_get` 注入，测试可替换。
- `adapters/registry.py`：扫描 `adapters/` 包自动注册，`kind_name` 重复时报错。
- `adapters/rss.py`：RSS 2.0 与 Atom，仅用标准库 `xml.etree`。
- `adapters/json_api.py`：通过点号路径（如 `data.list`）取列表与字段，支持 heat、summary、published_at 可选字段。
- `core/sources.py`：读取 `_index.json` 与各源 `source.yaml`，做必填字段、id 一致性、分类存在性、kind 合法性、适配器自校验。单个源出错不影响其他源。
- `core/normalize.py`：生成统一 Item 结构，稳定 id 为 `源id:url哈希`（无 url 时用标题哈希）。
- `core/runner.py`：执行单源，写 `data/raw/<id>/<日期>/<时分秒>.json` 快照、追加 `data/items/<id>/<日期>.jsonl`、追加 `data/runs/<日期>.log.md` 运行记录。失败时不写条目，但记录错误。
- `cli.py`：`list`、`validate`、`run <id>`。
- `tests/helpers.py`：在临时目录构造最小项目，供集成测试使用。
- `tests/test_adapters.py`、`tests/test_sources_and_runner.py`。

### 扩展机制的落地情况
- 新增适配器：在 `adapters/` 下新建模块并继承 `Adapter`，设置 `kind_name`，无需改注册表。
- 新增信息源：在 `sources/` 下建目录与 `source.yaml`，并在 `_index.json` 中登记。

### 验收结果
```
python -m unittest discover -s tests -t .
Ran 21 tests ... OK
python3 cli.py validate   -> 已加载 0 个信息源，0 个有错误；可用适配器: json_api, rss
```

### 与方案的偏差
- 方案中 `info_hub run` 写作命令名，实际实现为 `python cli.py run`，`info_hub` 入口待 P9 再包装。
- 当前 `items` 直接追加，未做去重。去重与增量对比按计划放在 P2。
- 真实外网抓取未在沙箱中验证（沙箱无外网），测试全部使用本地假数据。上线前需要在用户环境中用真实 RSS 地址验证一次。

---

## P2：归一化、去重与增量（已完成）

### 交付内容
- `core/pipeline.py`：
  - 状态文件 `data/state/<源id>.json`：记录已见条目（id → 首次见到时间）与上一次快照的排名。
  - 去重：已见条目不再写入 `items/*.jsonl`。
  - 过期清理：超过 `policy.retain_days` 的已见记录自动移除，防止状态文件无限增长。
  - hot_list 变化计算：新进（new）、上升（rising）、下降（falling）、掉出（dropped），并标记首次快照（first_snapshot）。
  - feed / recommend / custom：只输出 new。
- `core/runner.py` 集成流水线：状态读取、整理与写回在同一把文件锁内完成；原始快照新增 `new_count` 与 `changes` 字段；运行记录同时显示"抓取 X 条，新增 Y 条"。
- `tests/test_pipeline.py`：单元测试 5 个，集成测试 1 个（验证第二次抓取只写入新条目，且无重复）。

### 数据文件示例（人可直接阅读）
- `data/state/news_a.json`：`seen` 字典 + `last_snapshot.ranks`。
- `data/raw/news_a/2026-11-11/120000.json`：完整快照，含 `changes` 块。
- `data/items/news_a/2026-11-11.jsonl`：仅新增条目，每行一条 json。

### 验收结果
```
python -m unittest discover -s tests -t .
Ran 26 tests ... OK
```

### 备注
- 去重与清理阈值目前只有 `retain_days` 一个参数，`max_items` 仍由适配器截断。
- 增量对比目前基于内存中的单次快照比较，尚未做跨天的热榜趋势分析，该部分留给 P5 看板的"热榜"页面。

---

## P3：共享浏览器会话与 html_list（已完成，有头登录未在沙箱验证）

### 交付内容
- `core/browser_session.py`：`BrowserSession`
  - 所有站点共用同一个持久化 profile（`browser/profile/`），登录一次即可复用。
  - 所有 profile 操作在 `profile.lock` 内串行，避免两个浏览器进程同时占用同一 profile。
  - `fetch_html`：无头抓取；若最终 URL 包含站点的 `expired_pattern`，抛出 `LoginRequired`，并把状态记为 expired。
  - `interactive_login`：有头浏览器打开登录页，用户完成后回车，系统再用无头模式验证 `check_url`，成功则记为 logged_in。
  - `browser/login_status.json`：记录各站点状态与时间，不含任何凭据。
  - playwright 在方法内延迟导入，不使用浏览器的命令不会因此报错。
- `adapters/html_list.py`：基于 BeautifulSoup 的 CSS 选择器解析，字段支持 `选择器@属性`。`browser.required` 为 true 时经共享会话渲染；登录失效转为可读的错误结果，而不是静默失败。
- `config/sites.yaml`：站点登录配置（示例站点，需要替换为真实站点）。
- `core/runner.py`：新增 `default_context`，运行时自动注入浏览器会话与站点配置。
- `cli.py`：新增 `login <site>` 与 `login-status`。
- `adapters/base.py`：`FetchContext` 增加 `browser` 与 `sites` 字段。
- `core/settings.py`：新增 `load_sites`。

### 验证方式
- `tests/test_browser_session.py` 使用**真实无头 Chromium** 与本地 HTTP 服务器：
  - 第一个会话写入持久 cookie，第二个全新会话在同一 profile 下能访问登录后页面。
  - 没有登录态时抛出 LoginRequired，状态记为 expired。
  - `interactive_login` 的流程（用无头模式替代有头模式）能记录 logged_in。
  - html_list 通过真实浏览器完成端到端抓取。
- Chromium 无法启动时这些用例自动跳过。
- `tests/test_html_list.py` 使用假浏览器测试解析与错误转换。

### 验证中发现并修正的问题
- 测试服务器最初发的是会话 cookie（无过期时间），Chromium 关闭时会丢弃它，导致登录态无法跨次保留。这是真实风险：如果目标站点也只发会话 cookie，就需要额外的处理方式。已在 PROJECT.md 中注明，测试改为发持久 cookie。
- 测试中的 html 字段选择器写成了非法的 `"."`，已修正。

### 未在沙箱验证的部分
- **有头登录**：沙箱没有显示器，无法打开有头窗口。登录流程的代码路径已用无头模式覆盖，但真正的有头窗口需要在你的机器上验证一次：`python cli.py login <site>`。
- **真实站点**：知乎、微博、小红书等站点的选择器与登录判定都需要在真实环境中调整，示例配置只是占位。

---

## P4：定时报告与调度（已完成）

### 交付内容
- `core/scheduler.py`：
  - `parse_interval`（支持 Nm / Nh / Nd）、`parse_at`（HH:MM）。
  - `is_due`：`every` 类计划按间隔判定；`at` 类计划每天在指定时刻之后触发一次。
  - `source_is_due`：信息源抓取间隔判定。
- `core/digest.py`：
  - 按时间窗口收集条目（窗口外的过滤掉）。
  - 每个源取窗口内最新快照的热榜变化（新进、上升、下降、掉出）。
  - 收集窗口内的运行失败记录作为"异常"。
  - 按分类分节输出 markdown 与 json；写入 `reports/latest.json` 指针。
- `core/tick.py`：一次调度。运行所有到期的启用信息源，生成所有到期的报告；调度状态写入 `data/state/_scheduler.json`，重复调用安全。
- `core/runner.py`：运行记录改为固定的 `|` 分隔格式，便于解析。
- `cli.py`：新增 `report <plan_id>` 与 `tick`。

### 与方案的偏差
- 报告文件名为 `reports/<日期>/<HHMM>-<plan_id>.md`，方案中写的是 `<HHMM>.md`。加上 plan_id 后，同一时刻生成的多份不同计划不会互相覆盖。
- 调度采用"外部周期调用 tick"的方式（推荐交给系统 cron），而不是常驻 serve 循环。常驻循环与看板一起在 P5 的 serve 中实现。
- 定时表达式只支持 `Nm / Nh / Nd` 与 `HH:MM`，不支持 cron 表达式。
- 失败的源同样更新调度时间，避免失败时每次 tick 都重试造成风控风险。

### 验收结果
```
python -m unittest discover -s tests -t .
Ran 53 tests ... OK
```

端到端冒烟测试（在项目副本中、用本地 HTTP 服务器作为真实信息源）：
- `validate` 通过 2 个源。
- `tick` 同时运行两个到期的源，并生成到期的报告。
- 紧接着再次 `tick`，没有任何任务到期，什么都没执行。
- `report rolling` 生成的 markdown 中，RSS 源与 HTML 热榜源的条目都正确出现。
- 运行记录与状态文件均为纯文本，可直接阅读。

冒烟测试中发现并修正的问题：运行记录的错误信息与统计信息混在一起，导致异常小节把"抓取 0 条"也当成了错误内容。已改为固定的分隔格式并补充测试。

### 打包说明
P3 与 P4 的改动中，`cli.py` 与 `core/runner.py` 两个文件同时包含两个阶段的修改，无法干净地拆分。因此 P3 与 P4 合并为一个压缩包 `info_hub_P3-P4.zip` 交付，而不是分别打包。
