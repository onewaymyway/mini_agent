下面把"自我进化"这条线拆成一个有依赖顺序的整改计划。核心思路是把"自我进化"理解成一个闭环：**反思（产生经验信号）→ 沉淀（把经验变成可复用的 skill/memory）→ 验证（确认沉淀的东西真的有用）→ 安全应用（带版本控制）→ 可选的自主循环**。后面几个阶段都依赖前面阶段的产出，所以顺序本身就是优先级。

---

### Phase A：基础设施清债（前置，必须先做）

这一阶段不直接产生"进化"能力，但后面所有阶段都要在干净的数据结构上工作，欠着会越欠越多。

- **history 条目类型化**（即 future_todos_2 的 P2⑤）：给每条 history 加 `_type`（`user_input` / `tool_result` / `compressed` / `session_resume` 等）。这是 Phase B 反思机制能"看清一轮对话边界、区分用户意图和工具噪音"的前提，否则反思 prompt 里塞的全是字符串前缀猜测出来的内容，质量上不去。
- **SubAgent 输出去截断**（P1②）：超长输出写文件、返回路径。Phase E（SubAgent 协作）和 Phase C（从 SubAgent 产出沉淀 skill）都需要主 agent 能拿到完整产出，否则沉淀出来的 skill 内容本身就是残缺的。
- **config.py 拆分**（可选但建议）：Phase B/C/D 都会新增配置项（`reflection.*`、`skill_evolution.*`、`eval.*`），趁早拆成 `config/schema.py` / `loader.py` / `prompt_builders.py`，避免继续往 36K 的文件里堆。

