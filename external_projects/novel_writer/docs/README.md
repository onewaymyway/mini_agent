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

`python -m pytest tests -q`（39 个用例；含入口脚本子进程测试与 Streamlit AppTest 冒烟，均使用临时数据目录，
不污染真实 `data/`）。
