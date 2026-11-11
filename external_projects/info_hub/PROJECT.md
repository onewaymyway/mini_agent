# info_hub 信息聚合与播报

独立外部项目，负责定时抓取信息源、整理为纯文本数据、生成报告，并在看板上听报。
设计见 `next_doc/info_hub_external_project_plan.md`（主项目目录下），实施记录见 `next_doc/info_hub_implementation_record.md`。

## 目录约定
- `sources/_index.json`：信息源注册表
- `sources/<id>/source.yaml`：单个信息源声明
- `adapters/`：适配器类型，自动发现
- `core/`：存储、流水线、报告等核心逻辑
- `data/`、`reports/`、`browser/profile/`：运行产物，已加入 .gitignore，browser/profile 含登录凭据，勿分享

## 命令（当前阶段）
```
python cli.py list                 # 列出信息源
python cli.py validate             # 校验全部信息源配置
python cli.py run <source_id>      # 立即抓取一个信息源
```

## 运行环境
- Python 3.10+，依赖 PyYAML（见 requirements.txt）
- 测试：`python -m unittest discover -s tests -t .`
