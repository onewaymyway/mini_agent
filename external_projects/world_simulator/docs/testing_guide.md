# 如何测试 world_simulator

本文档面向"想验证这个项目能不能正常工作"的使用者，分两条路径：

- **第一部分：自动化单元测试** —— 几秒钟出结果，不需要真实 LLM/网络，
  改完代码想快速确认"没搞坏东西"用这个。
- **第二部分：端到端手动验证** —— 需要真实 LLM 配置，按"输入什么 →
  预期看到什么 → 怎么判断通过"逐步走一遍主链路，验证的是"这个项目
  真的能用"而不只是"代码逻辑没错"。

两部分互补：单元测试打桩了 LLM 调用，验证的是"引擎传给 workflow 的
参数对不对、workflow 结果怎么解析回状态"；手动验证走真实 LLM，多验证
一层"提示词/模板产出的内容是否合理可用"。

---

## 第一部分：自动化单元测试

### 怎么跑

```bash
cd external_projects/world_simulator
pip install pytest
python -m pytest tests/ -v
```

### 预期结果

- 全部 38 个用例（7 个测试文件）应该全部通过（`38 passed`）。
- **前提**：需要能 `import mini_agent`（本项目在 mini_agent 仓库内跑
  测试时天然满足；如果单独把本项目搬到别的环境、还没装完整
  mini_agent 依赖（比如 `fastapi`），依赖 `mini_agent.workflow` 的
  几个测试文件会因为 `ImportError` 报错——这不代表本项目代码有问题，
  只是测试环境缺依赖，装好 `mini_agent` 及其依赖后重跑即可。不依赖
  `mini_agent.workflow` 的三个文件（见下表"无额外依赖"列）在任何
  环境下都应该能独立通过，可以用它们先确认"存储层/分支/删除/成就"
  这部分的代码没问题：

  ```bash
  python -m pytest tests/test_state_and_store.py \
                    tests/test_branch_manager.py \
                    tests/test_delete_and_achievements.py \
                    tests/test_config_llm_inheritance.py -v
  ```

  预期：`25 passed`。

### 各测试文件覆盖什么、怎么算通过

| 测试文件 | 是否需要 `mini_agent.workflow` | 覆盖什么 | 通过标准 |
|---|---|---|---|
| `test_state_and_store.py` | 否 | `SimState`/`SimManifest` 的序列化/反序列化往返；`SimStore` 落盘/读取当前状态与历史；读取不存在的实例报错 | 4 个用例全绿 |
| `test_branch_manager.py` | 否 | 分叉不销毁原时间线；不切换分叉（`switch=False`）；从非法 step 分叉报错；切换分支；切换到不存在的分支报错；跨实例对比时间线 | 7 个用例全绿 |
| `test_delete_and_achievements.py` | 否 | 删除实例后目录消失、不影响其它实例；删除不存在的实例报错；4 组成就解锁场景（初始状态无解锁/推进 1 步解锁"启程"/推进 10 步解锁到"长篇在望"/命中重大决策+自动挡+已结束同时解锁对应徽章） | 7 个用例全绿 |
| `test_config_llm_inheritance.py` | 否 | `world_simulator/config.py` 的"原地布局自动探测主项目根 + 设置 `MINI_AGENT_MAIN_PROJECT_ROOT`"逻辑：能识别 `src/mini_agent`/`agent_config.json` 两种信号、非该布局时不误判、已有环境变量时不覆盖 | 7 个用例全绿 |
| `test_spec_and_engine.py` | 是 | 意图→提案草稿的 skill 绑定与解析（打桩 LLM）；创建+推进的端到端闭环；`set_pilot_config`；`materialize_simulation` 不触发 LLM 调用；推进时传入未知选项 id 会被拒绝 | 5 个用例全绿 |
| `test_autopilot.py` | 是 | 自动挡代选记录、拒绝 LLM 编造的选项 id、未开启自动挡报错、重大决策触发暂停、批量推进跳过手动挡实例、单实例失败不中断整批 | 6 个用例全绿 |
| `test_multi_template.py` | 是 | 新模板（`group_evolution`）能正确绑定到对应 skill、端到端创建+推进跑通，验证"新增模板不改引擎代码" | 2 个用例全绿 |

**如何判断"没测通过"**：任何一条 `FAILED`（而不是因为上述 `fastapi`
之类的环境缺依赖导致的 `ImportError` collection error）都说明改动
引入了回归，需要看具体的 assertion 信息定位。

---

## 第二部分：端到端手动验证（需要真实 LLM）

适用场景：确认整个项目在真实环境下（真实 LLM 配置、真实看板）能正常
跑通主链路："意图 → 提案 → 确认创建 → 推进 → 分叉/对比 → 自动挡 →
存档管理 → 游戏化视图"。

### 前置条件

- 已安装 mini_agent 框架，且能加载到有效的 LLM 配置（`agent_config.
  json`/`providers.json`，或者本项目已经 `mini-agent projects
  register` 到某个能提供配置继承的宿主）。
- `cd external_projects/world_simulator && pip install -r requirements.txt`

### 路径 A：命令行验证（最快，适合先确认"能不能跑通"）

| 步骤 | 输入 | 预期结果 | 怎么算通过 |
|---|---|---|---|
| 1. 健康检查 | `python entrypoints/health.py` | 标准输出打印 `ok`，退出码 0 | 不需要 LLM 就能过；如果这一步都失败，说明存储层/依赖本身有问题，先解决这个再往下走 |
| 2. 创建实例 | `python entrypoints/create_simulation.py "模拟一个刚毕业、在读研和工作之间犹豫的年轻人的人生" --template life_sim` | 标准输出打印 `sim_id=life_sim_xxxxxx` 和 `title=...`；`data/<sim_id>/manifest.json`、`state_current.json`、`state_history.jsonl` 三个文件被创建，`state_history.jsonl` 只有一行（step 0） | 命令退出码为 0，且 `data/` 下确实多出对应目录；`title`/`summary`（可用 `cat data/<sim_id>/state_current.json` 查看）应该是和输入意图相关的合理内容，而不是空的或明显文不对题 |
| 3. 查看列表 | `python entrypoints/list_simulations.py` | 输出一行，形如 `life_sim_xxxxxx  template=life_sim  status=active  step=0  pilot_mode=manual  <title>` | 步骤 2 创建的实例出现在列表里，字段值符合预期（`status=active`、`step=0`） |
| 4. 推进一步（默认走向） | `python entrypoints/advance_simulation.py <sim_id>`（`<sim_id>` 换成步骤 2 拿到的值） | 输出 `step=1`、`summary=...`，可能还有 `narrative=...` 和若干 `option: <id> — <label>：<description>` 行 | `step` 从 0 变成 1；`summary`/`narrative` 内容应该是"承接上一步状态、合理往后发展"的叙事，不是不相关的胡言乱语；如果本步给出了候选选项，选项应该是"在当前处境下说得通"的几个方向 |
| 5. 推进一步（指定选项） | 先跑一遍步骤 4 拿到某个 `option id`，再执行 `python entrypoints/advance_simulation.py <sim_id> --choice <option_id>` | 输出 `step=2`，`summary`/`narrative` 内容应该明显承接"你选的那个方向"，而不是另一个方向的走向 | 对照 `--choice` 传入的选项描述，看新状态是不是真的往那个方向发展了 |
| 6. 再次查看列表 | `python entrypoints/list_simulations.py` | 该实例的 `step=2` | `step` 字段与步骤 5 后的实际推进次数一致 |

**批量自动挡验证**（可选，验证 `batch_advance_daily` 对应的代码路径）：

1. 用看板（见路径 B）或直接改 `data/<sim_id>/manifest.json` 把
   `pilot_mode` 改成 `"autopilot"`、`autopilot.enabled` 改成 `true`、
   补上 `principles`（数组，随便写一两条原则）。
2. 执行 `python entrypoints/advance_simulation.py --all-autopilot --steps 1`。
3. **预期**：输出 `<sim_id>: ok, next_step=<原 step + 1>`；如果
   `review_mode` 是 `pause_on_major_decision` 且这一步被判定为重大
   决策，输出后面会带 `（触发暂停等待确认）`，且该实例的
   `manifest.json.status` 会变成 `paused`。
4. **怎么算通过**：新增的历史节点里 `chosen_by` 字段是 `"autopilot"`
   且带有 `chosen_reason`（可以 `cat data/<sim_id>/state_history.jsonl`
   查看最后一行确认）。

### 路径 B：看板验证（更直观，覆盖分支/对比/存档/游戏化视图）

```bash
streamlit run app.py --server.port 8502
```

浏览器打开 `http://localhost:8502` 后按下表操作：

| 页面 | 操作（输入什么） | 预期结果 | 怎么算通过 |
|---|---|---|---|
| 模拟列表 | 打开页面 | 看到路径 A 创建的实例卡片（标题/状态/步数/推进模式） | 卡片信息与 CLI 查到的一致 |
| 创建向导 | 输入一句话意图，选模板，点「生成提案草稿」 | 几秒后出现可编辑的草稿（标题/摘要/关键变量 JSON/初始候选方向） | 草稿内容与输入意图相关；点「确认创建」后跳转到该实例的详情页 |
| 实例详情 | 点某个候选方向卡片，或点「按默认走向推进」 | 出现推进中的 spinner，几秒后时间线顶部新增一个"章节卡片"，`当前第 N 步` 数字 +1 | 新增章节的摘要/叙事承接了你的选择；如果卡片渲染失败或报错，说明这一步没通过 |
| 实例详情 → 分支 | 展开「从历史节点开一条新分支」，选一个较早的 step，点「创建分支并切换过去」 | 出现新的分支 id（如 `br_xxxxxx`），当前活跃分支变成它 | 原分支仍在分支列表里、切回去后历史没有丢失内容 |
| 对比视图 | 分别为「实例 1」「实例 2」选好 实例+分支 组合（可以是同一实例的两条分支） | 并排展示两条时间线的章节卡片，下方是按 step 对齐的关键变量表格 | 两侧内容确实来自不同分支/实例，且在分叉点之前的历史一致、之后开始分叉 |
| 实例详情 → 推进模式 | 展开「配置自动挡」，勾选「开启自动挡」，填几条原则，选风险偏好，点「保存自动挡配置」 | 页面提示保存成功，「当前：自动挡（代理代选）」文案出现 | 用「手动触发自动挡推进一步」按钮跑一次，时间线里新增的章节带有"→ 代理选择了「…」"和"理由：…"字样 |
| 存档管理 | 打开页面，看到全部实例列表；点某个不再需要的测试实例的「🗑 删除」→ 勾选「我确认要删除这个实例」→「确认删除」 | 提示"已删除「…」"，该实例从列表消失 | 用 `ls data/` 确认对应目录确实不在了；**注意这是破坏性操作，只在测试实例上做** |
| 游戏化视图 | 在实例详情页点「📖 游戏化视图」 | 顶部出现成就徽章墙（已解锁的高亮、未解锁的灰显）+ 进度条；下方是可翻页的章节回顾 | 至少推进过 1 步的实例应该点亮"启程"徽章；点「上一章/下一章」或拖动"跳到章节"滑块，展示内容随之切换且与该 step 的实际历史一致 |

### 判断"整个项目是好的"的最低标准

如果你只有几分钟时间，建议至少做到：

1. 第一部分的四个"无额外依赖"测试文件全绿（`25 passed`）——确认
   存储/分支/删除/成就/LLM 配置继承这些不依赖 LLM 的核心逻辑没问题。
2. 路径 A 的步骤 1-4 走一遍——确认真实 LLM 环境下"创建→推进"这条
   最核心的链路能跑通、产出内容合理。
3. 路径 B 打开看板确认页面能正常加载（无报错堆栈），且步骤 2 创建的
   实例能在列表里看到、能打开详情页。

三条都过，说明这个项目在你的环境里是可用的。

---

## 常见报错排查

### `generate_scenario workflow 执行未成功……Anthropic requires an API key`

说明"LLM 调用本身能发起"，卡在最后一步"找不到可用的 API key"。按顺序
排查：

1. **主项目本身配好 LLM 了吗？** 去 mini_agent 主仓库根目录确认
   `providers.json`（或 `agent_config.json` 里的 `llm_fallback_chain`）
   配了 key，或者启动看板/CLI 的这个终端会话里导出了对应环境变量（如
   `ANTHROPIC_API_KEY`）。如果主项目自己都没配，这一步会一直失败，
   属于预期行为——先在主项目层面配好。
2. **本项目找得到主项目吗？** `world_simulator/config.py::
   load_llm_cfg()` 会依次尝试：环境变量 `MINI_AGENT_MAIN_PROJECT_ROOT`
   → 已注册到 daemon 的记录 → "原地布局自动探测"（本项目仍挂在某个
   mini_agent 主仓库的 `external_projects/` 下时自动生效，不需要手动
   配置）。如果本项目已经被搬到独立路径、也没注册过，以上都找不到，
   需要显式 `export MINI_AGENT_MAIN_PROJECT_ROOT=<主项目路径>` 或先
   `mini-agent projects register <本项目路径>` 一下。
3. **验证方法**：`python -c "from world_simulator.config import
   load_llm_cfg; load_llm_cfg()"` 能正常返回（不报
   `ModuleNotFoundError: mini_agent`）就说明"能找到主项目"这一步没
   问题，报错会具体停在哪一步（找不到 mini_agent 包 / 加载配置失败 /
   真正发起 LLM 调用时缺 key）。

### `ModuleNotFoundError: No module named 'mini_agent'`

本项目所在的 Python 环境没有安装 mini_agent 框架本身（或缺它的某个
依赖，比如 `fastapi`）。这与"LLM 配置继承"是两个独立问题——需要先让
`import mini_agent` 本身能成功（通常是 `pip install -e .` 装好主项目
及其依赖），再回头看上面那条排查 API key 的问题。
