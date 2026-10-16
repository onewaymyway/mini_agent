# novel_writer 使用说明（M0）

## 快速开始

```bash
cd external_projects/novel_writer
pip install -r requirements.txt
python entrypoints/health.py                       # 输出 ok 即 23 个 skill 完整、数据目录可写
python entrypoints/create_novel.py --title 青云传   # 创建工作目录 data/novels/青云传_<时间戳>/
python entrypoints/list_novels.py
streamlit run app.py                               # 书架 / 新建小说 / Skill 库
```

`create_novel` 可选参数：`--workspace <任意绝对路径>`（会登记到 `data/shelf.json`）、`--gear G0..G3`、
`--quality draft|standard|premium`、`--total-chapters N`。

## 工作目录（每本书一个）

skill 标准 6 个中文目录（大纲规划/正文/一致性检查/修订记录/章节总结/项目状态）+ `推演记录/`、`导出/`
+ 系统目录 `_studio/`（manifest、settings、pipeline_state、inbox、answers、author_notes、steps、snapshots）。
路径唯一来源：`novel_writer/workspace.py::PATHS`。初始化幂等：对已有目录再次初始化只补缺失文件，
不覆盖；非空且不是本项目的目录会被拒绝。

## 设置（`_studio/settings.json`）

默认保守：`gear=G0`（手动挡）、`quality=standard`、预算有限（单次最多 3 章/60 次 LLM 调用/120 分钟）、
不豁免蓝图确认。校验见 `novel_writer/settings.py`；挡位/闸门语义见方案第 6 节（M1 起生效）。

## 测试

`python -m pytest tests -q`（71 个用例：M0 39 + M1 32；含入口脚本子进程测试与 Streamlit AppTest 冒烟，均使用临时数据目录，
不污染真实 `data/`）。


## M1：引擎与后台运行

### 流水线怎么走

- 步骤来自 `novel_writer/steps.py`（定义）与 `pipeline.py`（展开）。展开依赖蓝图：主要角色 = 出现在 ≥3 章 `characters_present` 中的角色；章节数 = `structure.total_chapters`。
- 流水线线性执行，**前沿**是第一个未完成的步骤。可选步骤（蓝图快进推演）在手动挡下会被跳过，不阻塞。
- 步骤状态：`pending → running → waiting_review | waiting_user | passed | failed | blocked | skipped`，另有 `stale`（上游产物变化）与 `interrupted`（上次 worker 被杀）。

### 闸门（方案 6.1–6.3）

优先级：强制检查点 > 单步覆盖 `step_overrides` > 阶段挡位 `stage_gear_overrides` > 全局挡位 `gear`。
- 强制检查点（蓝图确认、前三章确认）不能被降级为 auto/skip；只有 `checkpoints.waive_blueprint_confirm` 能豁免蓝图确认。
- 修订/推演/灵感类（category `revise`）在所有挡位下都是手动。

### 运行与控制

worker 是独立子进程，不阻塞界面。每一轮：读控制指令 → 展开流水线 → 标脏 → 取前沿 → 查闸门与预算 → 执行 → 落盘。
需要人介入时自动退出，`worker.json` 的 `reason` 写明原因：

| reason | 含义 |
|---|---|
| `completed` | 流水线全部完成 |
| `waiting_review` | 某步执行完毕，等待 approve |
| `waiting_user` | skill 回执提出了 needs_user，等待 answer |
| `manual` | 前沿是手动闸门，等待 run（step_id） |
| `budget_chapters` / `budget_calls` / `budget_time` | 预算耗尽 = **暂停**，可直接再次运行续跑 |
| `paused` / `stopped` | 收到暂停/停止（在两步之间生效，不打断正在执行的 LLM 调用） |
| `failed` / `blocked` | 步骤失败（可 rerun）/ 执行器尚未实现 |

### 命令行

```bash
cd external_projects/novel_writer
python -m novel_writer.service_cli status   <工作目录>              # 流水线视图（JSON）
python -m novel_writer.service_cli run      <工作目录> --mode until_gate   # 后台推进
python -m novel_writer.service_cli run      <工作目录> --step plan_blueprint --mode single
python -m novel_writer.service_cli approve  <工作目录> <步骤ID>
python -m novel_writer.service_cli rerun    <工作目录> <步骤ID>
python -m novel_writer.service_cli answer   <工作目录> <步骤ID> --json '{"q1": "选A"}'
python -m novel_writer.service_cli note     <工作目录> <步骤ID> --text "下一步请……"   # 作者指导，注入 prompt
python -m novel_writer.service_cli pause    <工作目录>
python -m novel_writer.service_cli stop     <工作目录>
python entrypoints/run_novel.py --workspace <工作目录> --mode single   # 前台运行一个 worker（调试）
```

### 回执契约

每个 skill 步骤结束时，执行方把 `receipt.json` 写入结果目录：`status`（`done` / `needs_user`）、`summary`、`outputs`、`needs_user`。
引擎校验回执结构与声明的产出文件；不合格时在**新的 agent** 上重试（同一次执行内最多 2 次）。
每次执行的 prompt、回执都留在 `_studio/steps/<步骤>/run_<n>/`，用量记在 `_studio/usage.jsonl`。

### 真实 LLM 冒烟（需要本机密钥）

```bash
python entrypoints/smoke_real_llm.py --title 冒烟书 --intent "一个落魄少年修炼逆袭的三章短篇"
```

只跑一次 plan_blueprint。成功的标志：输出的 JSON 里 runs 的 executed 含 `plan_blueprint` 且状态为 `waiting_review`，并且工作目录的 `大纲规划/小说蓝图.json` 存在。
