# novel_writer 巡检整改项：让巡检报告真正被用上

状态：**阶段一已完成（2026-10-21）**——结构化整改项 + 分流 + 界面展示；阶段二（闭环 + 确定性指标）待做。

## 1. 问题

精品档每 10 章的 `patrol:N` 只产出一份 md 报告（`修订记录/第N章_巡检报告.md`），下游没有任何步骤、上下文包或界面读取它；
耗时约 11 分钟的 LLM 调用，产出只能靠作者自己去目录里翻。

## 2. 方案（已确认：自动采纳后续约束；先做 1+2+界面展示，再做闭环与确定性指标）

原 `patrol:N` **保持不变**（不重跑昂贵的全书巡检，已生成的旧报告也能直接使用），其后新增两步：

| 步骤 | 类型 | 作用 |
|---|---|---|
| `patrol_extract:N` | LLM（revision-editor），只读已有报告，不重读正文 | 写出 `修订记录/第N章_巡检整改项.json`，产出校验卡格式 |
| `patrol_apply:N` | 确定性 Python，所有挡位自动 | 校验并并入账本 `修订记录/巡检整改项.json`（含状态） |

整改项字段：`dimension / severity(严重·一般) / title / directive(≤300字，可直接照做) / scope / chapters / foreshadowing / characters / avoid_phrases / level / until_chapter`。

### 按范围（scope）分流

| scope | 例 | 去向 |
|---|---|---|
| forward 约束后续章节 | 伏笔过载、配角消失、成长代价不足 | 已采纳 → 之后每章**上下文包**新增「巡检整改指令」段（≤5 条、≤700 字；只对巡检章之后生效，可设 `until_chapter`） |
| style 避免套路 | 铜钱反应套路化、对话单调 | 已采纳 → **风格提示**「近期套路」+ **机器校验提示**（出现 avoid_phrases 即提示，**不阻断**） |
| blueprint 蓝图待办 | 情感峰值分散、节奏平板 | 收件箱待办，由作者决定，系统不改蓝图 |
| retro 回改已写章节 | 某章需要 L2 重构 | 收件箱待办；界面按钮「创建修订请求」，作者确认后才执行，**绝不自动改正文** |

状态：`proposed 待决定 → adopted 已采纳 | dismissed 已忽略 → done 已处理`。
**挡位 G3/G4 自动采纳 forward 与 style**；blueprint、retro 永远由作者决定。作者亲自做过的决定，重新提取时不会被覆盖或丢弃。

### 保守默认（全部默认关闭）

设置 `patrol_feedback`：`extract`（提取与分流）/ `inject_forward`（注入后续约束）/ `inject_style`（注入避免套路）/ `to_inbox`（收件箱提醒）。
全关时计划、上下文包、风格提示、机器校验与升级前逐字节一致。

### 界面

审校页顶部「🩺 巡检整改项」：四个开关、按状态分组的整改项列表、采纳/忽略/标记已处理/撤销、retro 项的「创建修订请求」。

## 3. 实施记录（阶段一）

- 新增 `novel_writer/patrol_findings.py`（校验、账本、合并、注入渲染）、`ui/patrol.py`、`tests/test_patrol_feedback.py`（16 例）。
- 修改：`workspace.py`（2 个路径键）、`settings.py`（`patrol_feedback` 及校验）、`steps.py`（2 个步骤定义）、`pipeline.py`（开启时在 patrol 后插入）、`validators.py`（`patrol_findings` 产出校验）、`chapter_ops.py`（`patrol_apply` 处理器；上下文包/风格提示/机器校验的接入）、`inbox.py`、`tool_api.py`（`patrol_findings` / `patrol_set_status`）、`ui/review.py`、`ui/components.py`。
- 全量 484 通过，仅剩原有的 `test_output_names` 失败；界面用 Streamlit AppTest 做过冒烟（渲染、采纳按钮）。
- **未验证**：真实 LLM 下 `patrol_extract` 能否稳定产出合格 JSON（有产出校验与自动重试兜底）；已有的第 30 章报告需开启 `extract` 后运行 `patrol_extract:30` 才会生成整改项。

## 4. 阶段二（待做）

1. **闭环**：下一次巡检前先核对上次的整改项，输出整改率；未解决的继续标红，已解决的自动关闭。
2. **确定性指标**（零 LLM 成本，每章计算）：未兑现伏笔数、配角连续缺席章数、重复短语频次；超阈值即提前生成整改项，不必等每 10 章一次的巡检。
