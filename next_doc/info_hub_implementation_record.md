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
| P3 | 共享浏览器会话、html_list 适配器、login 命令 | 待开始 |
| P4 | 定时报告生成 | 待开始 |
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
