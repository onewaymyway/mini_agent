# article_to_video — 项目说明

> 把一篇 Markdown 文章改编成讲解视频（配音 + 字幕 + 动态画面 + 署名与水印）。
> 设计依据：`next_doc/article_to_video_external_project_plan.md`（Q1–Q10 按推荐值确认，作者名与水印可在界面填写）。

## 当前进度

| 期 | 内容 | 状态 |
|---|---|---|
| **P0** 脚手架与导入 | 项目骨架、`project.yaml`、Markdown 块解析（稳定 ID）、ASCII 链与回路识别、公式/表格/引用解析、作者解析、作者名与水印输入、项目列表与署名水印编辑、看板 | ✅ 完成（40 项测试通过） |
| **P1** 规划 | 分节摘要（S1）、主线与大纲（S2）、V1 覆盖校验、取舍报告、离线规则版、大纲确认页（C0） | ✅ 完成（74 项测试通过） |
| P2 口播稿 | 分段写稿、词典、忠实度校验 | 未开始 |
| P3 配音与时间轴 | 逐句 TTS、双声线、`timeline.json` | 未开始 |
| P4 视觉 | 分镜、卡片/图表渲染、插图预算 | 未开始 |
| P5 合成 | beat→章→成片、字幕、进度条、封面 | 未开始 |
| P6 署名与水印 | 片头/片尾/下三分之一条、水印叠加 | 未开始（P0 已保存配置） |
| P7 自动化 | G1/G2 挡位、多期拆分 | 未开始 |

## 目录

```text
article_to_video/
├── app.py                    Streamlit 界面（端口 8507）
├── project.yaml              daemon 契约
├── requirements.txt  pytest.ini  .gitignore
├── article_to_video/
│   ├── paths.py              项目集合根目录（ARTICLE_TO_VIDEO_HOME 可覆盖）
│   ├── project_store.py      project.json 读写（原子）、设置与署名/水印归一化、项目列表
│   ├── importer.py           导入流程：解析 → 作者 → 登记（不调用模型）
│   ├── source/
│   │   ├── md_parse.py       Markdown → 块序列（s{章}.p{序}，记录源行号）
│   │   ├── ascii_graph.py    代码块分类：chain / ascii_art / text，回路检测
│   │   └── author_meta.py    front matter 与“作者：”行；署名优先级
│   ├── planning/             P1
│   │   ├── chunks.py         分节组（≤4000 字符）与章节目录
│   │   ├── rules.py          离线规则版：摘要、论点、大纲（不调用模型）
│   │   ├── llm.py            LLM 版：分节摘要（map）与大纲（reduce），失败重试一次
│   │   ├── prompts.py        提示词（PROMPT_VERSION = p1-v1）
│   │   ├── spine_math.py     章划分、时长预算、编号
│   │   ├── outline_edit.py   C0 编辑：改名、钩子、时长、合并、拆分、恢复取舍
│   │   ├── runner.py         落盘与状态：生成、保存编辑、确认（C0）
│   │   └── errors.py / jsonio.py
│   ├── validators/
│   │   ├── fidelity.py       V2（P1 部分）：摘要中的原话与数字必须出现在原文
│   │   └── coverage.py       V1：每个小节恰好出现一次（章或取舍报告）
│   └── providers/text.py     文本 LLM（mini_agent 的 LLMHelper）
├── entrypoints/              import_article / list_projects / health / plan_outline
├── ui/                       import_page / outline_page（C0）/ projects_page
├── tests/                    74 项（P0 40 + P1 34）
└── docs/README.md
```

## 运行

```bash
pip install -r requirements.txt
streamlit run app.py --server.port 8507        # 界面
python entrypoints/import_article.py --file 文章.md --author "作者名" \
       --watermark-text "作者名 · 频道" --watermark-position top_right
python entrypoints/list_projects.py
python entrypoints/plan_outline.py --id <项目目录名> --engine rules     # 离线规则
python entrypoints/plan_outline.py --id <项目目录名> --engine miniagent # 模型（需 mini_agent 配置）
python entrypoints/health.py
python -m pytest -q
```

项目默认保存在 `<项目目录>/projects/`，可用环境变量 `ARTICLE_TO_VIDEO_HOME` 改到其它目录。

## P0 已确定的规则

- **作者名**：界面或命令行输入优先；留空时依次尝试 front matter（`author:` / `作者:`）、文首“作者：”行；都没有则留空并警告。**不让模型猜作者。**
- **水印**：文字水印，最多 60 字。水印文字为空时自动关闭。位置与模式不合法时取默认值并给出警告。
- **署名与水印可随时修改**：「📚 项目」页保存时只改 `project.json`，源文章与块数据保持不变，后续阶段据此判断是否需要重跑。
- **块 ID**：`s{章号:02d}.p{序号:02d}`，章号按一级标题递增，一级标题之前为 `s00`。
- **时长估算**：按 4.2 汉字/秒。参考文章 7,022 汉字，全文朗读约 27.9 分钟。

## P1 已确定的规则

- **两种生成方式**：`rules`（离线，确定性，不调用模型）与 `miniagent`（LLM）。规则版章标题取一级标题，论点取该章第一节的首个完整论点句，不做改写，质量有限，适合作为兜底与人工起点。
- **分节摘要**：按一级标题累加成组，每组不超过约 4000 字符；超长节在块边界切开。
- **忠实度**：摘要中的 `quotable` 与 `key_numbers` 必须是原文逐字片段（去空白与 Markdown 标记后比较），否则带问题清单重试一次；仍不合格则**不写任何结果**，原始输出存入 `plan/raw_*.txt`。
- **V1 覆盖**：每个小节必须且只能出现在一个章或取舍报告中；金句必须来自摘要登记的原话；章数超出 5～7 只给警告。
- **断点续跑**：LLM 摘要记录输入哈希（含提示词版本与原文），输入未变时复用，不重复调用。
- **C0 确认**：任何编辑都会取消确认；已确认的大纲默认不能被覆盖，需显式勾选。

## 已知限制（P1）

- LLM 路径在本环境只用假模型验证了流程（重试、失败不落盘、断点续跑），**未对真实模型做质量验证**，首次使用请留意。
- LLM 调用目前**不可中途停止**：与 novel_to_video 早期「立即停止」问题相同，需经 `cancel.call` 包装，留待 P2 统一处理。
- 大纲页的界面只做了 AppTest 冒烟，真实浏览器中的编辑体验需本机确认。
- 规则版的论点句偶尔是残句或长句（原文本身结构复杂时），属预期内的局限。
- 章划分尚未按时长权重做二次校正；`weight` 目前按字符数。

## 已知限制（P0）

- 界面的文件上传与表单提交只做了 Streamlit AppTest 冒烟（空表单报错路径）；带真实文件的提交靠 CLI 与导入函数测试覆盖，**真实浏览器中的体验需本机确认**。
- 尚未调用任何模型，`health` 中的 ffmpeg 与 pillow 仅提示，P4/P5 再强制。
- 水印的实际绘制（位置、透明度、`drift`/斜铺动效）在 P6 实现；P0 只保存配置。
