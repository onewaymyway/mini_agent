# 架空世界工坊（world_forge）外部项目方案

> 状态：**已确认，开始实施（M0–M4）**
> 项目路径：`external_projects/world_forge/`　看板端口：**8509**（8502 / 8504 / 8506 等已占用）
> 与 `world_simulator` 的关系：独立新项目，只借鉴其分支落盘设计，不 import 其代码。

## 1. 目标

用户给出一句初始概念，系统初始化一个架空世界；随后可以通过交互与自动方式持续完善世界观，
并推进世界的历史发展（自动生成角色与事件）。支持时间线回退与分支。世界中的事件可以直接
产出：小故事线（供 novel_writer 使用）、短篇小说、漫画（分镜脚本 + 真实生成的分镜图）。

## 2. 已确认的决策

| 事项 | 决定 |
|---|---|
| 项目形态 | 新建外部项目 `world_forge`，不扩展 world_simulator |
| 看板端口 | 8509 |
| 次要设定 | **自动写入**（地名、道具、普通组织细节、次要角色的非关键设定） |
| 冲突处理 | **阻止写入**：与宪章或既有设定冲突的写入被拦截，记入提案队列并标注冲突原因，用户可修改后重提 |
| 漫画 | 真实生成分镜图，图像接口封装参考 novel_to_video（`gen_image_with_text` skill，Agnes 图像模型） |
| 语言 | 默认中文（提示词、输出、界面） |

## 3. 数据模型

- **宪章 charter.json**：标题、题材、基调、硬规则（不可违背的世界规则）、概要。
- **设定实体 canon/entities.json**：`id / kind / name / summary / attrs / status / source`。
  `kind` ∈ {地理, 势力, 物种, 技术/魔法, 历史事件, 物品, 组织, 其它}。
  `source` ∈ {用户, 自动, 推进}，用于审计。
- **关系 canon/relations.json**：`src / dst / type / note`。
- **角色 cast/characters.json**：角色卡（身份、所属势力、动机、状态 alive/dead/unknown、位置），
  立绘路径 `cast/portraits/<id>.png`（漫画参考图）。
- **时间线 timeline/main.jsonl 与 timeline/branches/<bid>.jsonl**：事件只追加。
  事件：`id / t（时间标签）/ title / summary / involves / caused_by / tags / branch / created_at`。
  分支元数据 timeline/branches.json。
- **提案 proposals/queue.jsonl**：每条提案含 `kind / payload / risk（minor|major）/ status
  （applied|pending|blocked|rejected）/ reason`。所有写入都经过提案。
- **故事 stories/<sid>/**：arc.json（小故事线）、story.md（短篇）、storyboard.json（分镜）、
  panels/*.png（分镜图）、comic.html（漫画页）。
- **导出 exports/<name>/**：给 novel_writer 的输入包（outline.md、characters.json、setting.md）。

## 4. 核心机制

1. **初始化**：概念 → LLM 生成宪章草案与首批实体/角色 → 宪章以 major 提案写入（需确认），
   实体按自身风险分级写入。
2. **完善**
   - 缺口检测（确定性）：无关系的势力、无描述的实体、角色的所属势力不存在、事件引用了未定义实体。
   - 交互式补全：对指定缺口调用 LLM，结果进入提案。
   - 自动补全：按风险分级，minor 自动写入，major 进入待确认。
3. **一致性检查**（每次写入前）：名称与既有实体重复、引用不存在的 id、已死亡角色新增事件参与、
   违反宪章硬规则（由 LLM 对照判断，结果为 conflict 时阻止）。冲突 → status=blocked。
4. **推进**：以"时间片"为步长（默认一个纪元片段）。读取世界状态与近期事件，LLM 生成事件、
   新角色与设定更新 → 逐条过一致性检查 → 通过的写入时间线与提案，被拦截的记录原因。
5. **分支与回退**：在任一事件处 fork 出新分支（复制之前的历史），原分支不动；可切换、对比。
   "回退"即在历史节点开分支。当前设定库为全局（不分支），分支只分事件时间线；
   这一取舍在 6.2 节说明。
6. **故事**：从时间线选事件（区间或指定事件）→ 小故事线（arc）→ 短篇 / 分镜 → 漫画图。
   用户也可直接指定一个小事件，跳过时间线直接创作。

## 5. 图像与漫画

- `providers/image.py`：`ImageProvider` 接口（与 novel_to_video 一致的 `generate(prompt, save_path, ...)`，
  永不抛异常，返回 `ImageResult`）。实现：
  - `AgnesSkillImageProvider`：经 mini_agent 的 `gen_image_with_text` skill 调用真实模型，key 解析
    沿用 skill 规则（AGNES_API_KEYS → providers.json → AGNES_API_KEY）。
  - `StubImageProvider`：PIL 占位图，用于测试与无 key 的离线演示。
- 参考图：分镜中出现的角色若有立绘，取前 3 张作为参考图，保证角色外观一致。
- 漫画流程：storyboard.json → 每格生成画面提示词（LLM）→ 生成图片 → 拼装 comic.html（气泡/旁白）。

## 6. 取舍说明

6.1 **设定全局、事件分支**：分支只分开时间线，设定库是当前世界的"现行设定"。
分支的设定差异不做（会使一致性检查指数复杂）。如需对比设定，留待后续。

6.2 **自动写入的边界**：minor 包括地名、物品、次要组织的描述字段；major 包括宪章、
势力首领变更、角色死亡、删除、修改已有实体的核心字段。

## 7. 分期

- M0 脚手架、数据模型、health、宪章初始化
- M1 设定库、提案队列、一致性检查、缺口检测与补全
- M2 角色、时间线、推进、分支（fork/switch/compare）
- M3 故事（小故事线、短篇、指定事件创作）、novel_writer 导出
- M4 分镜与真实生图、漫画页拼装
- M5 看板（8509）、项目注册、文档

## 8. 已知限制（实施时记录在 PROJECT.md）

- LLM 真实调用需要本机配置；离线模式下使用规则生成器，只用于流程演示与测试。
- 漫画图的真实生成需要 Agnes API key；无 key 时自动降级为占位图并在结果中标注。
- 时间线的"回退"是开分支，不做原地删除；这是设计选择。

---

## 实施记录（2026-11-24）

- 已实现 M0–M5，代码位于 `external_projects/world_forge/`，项目说明见其 `PROJECT.md`，使用说明见 `docs/README.md`。
- 决策落实情况：新项目（不依赖 world_simulator 代码）；看板端口 8509；次要设定自动写入；
  与宪章或既有设定冲突的写入一律阻止并记录原因；漫画通过 `gen_image_with_text` skill 真实生图（无 key 时降级占位图并标注）；默认中文。
- 测试：`python -m pytest -q tests` 共 38 项通过（含看板 AppTest 冒烟与 CLI 子进程测试），离线模式全流程跑通。
- 未验证：真实文本模型与真实图像模型的端到端调用（本环境无 API key 与 provider 配置）。
- 未做：daemon 注册（需 `mini-agent projects register external_projects/world_forge`，注册后默认不开启定时任务）。
