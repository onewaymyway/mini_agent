# info_hub 实施记录

- **关联方案**：`next_doc/info_hub_external_project_plan.md`（v2.0）
- **项目位置**：`external_projects/info_hub/`
- **约定**：每完成一个阶段，更新本文件的阶段状态与实施要点，并在 `/mnt/user-data/outputs/` 生成该阶段的打包文件（路径相对 mini_agent 根目录，直接覆盖即可）。

## 阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 项目骨架、纯文本存储工具、配置加载 | 已完成 |
| P1 | 适配器框架、rss / json_api 适配器、信息源注册与 run 命令 | 待开始 |
| P2 | 归一化、去重、增量对比（hot_list 排名变化） | 待开始 |
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
