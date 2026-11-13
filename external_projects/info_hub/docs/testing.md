# 测试

## 运行

在 `external_projects/info_hub` 目录执行：

```bash
# 全部测试（约 1 分钟）
python -m unittest discover -s tests -t .

# 单个文件
python -m unittest tests.test_feed_swap

# 单个用例
python -m unittest tests.test_feed.BuildFeedTests.test_multi_source_event_lands_in_hot_section
```

依赖：`requirements.txt` 中的包。看板相关测试使用 Streamlit 的 `AppTest` 无头运行，不需要启动浏览器。

## 测试组织

| 文件 | 覆盖 |
|---|---|
| `test_feed.py`、`test_feed_p2.py`、`test_feed_p3.py`、`test_feed_p4.py` | 推荐的聚合、打分、分区、疲劳、探索、学习权重、LLM 增强 |
| `test_feed_snapshot.py`、`test_feed_swap.py`、`test_feed_display.py` 等 | 推荐页的看板交互：会话快照、换一批、显示数量设置 |
| `test_fetch_debug.py` | RSS 修复、验证页识别、失败调试文件、运行一次的结果面板 |
| `test_new_sources.py`、`test_hf_sources.py`、`test_builtin_sources.py` | 内置信息源的配置与解析（仿真响应） |
| `test_feedback_*.py`、`test_evaluation.py` | 反馈存储、看板反馈面板、评估指标 |
| `test_dedicated_browser.py`、`test_browser_session.py`、`test_extension_e2e.py` | 专用浏览器启动、打开地址、接收服务、插件端到端（有头 Chromium，环境不具备时跳过） |
| `test_dashboard_layout.py`、`test_streamlit_app.py` | 页签顺序与布局冒烟 |
| `test_entrypoints.py` | 入口脚本与运行账本 |

测试使用 `tests/helpers.py::make_project` 在临时目录中构造最小项目，不会修改真实的 `data/`。

## 仿真响应

涉及外部站点的测试都用仿真响应，不访问网络。因此：
- 测试通过不代表站点当前的真实返回格式正确。
- 真实站点的选择器与接口需要在本机抓取一次确认（见 `next_doc/info_hub_sources_expansion_plan.md`）。

## 已知的失败用例

以下 2 项在改动前的原始代码中即失败，与推荐、看板等功能无关：

- `tests.test_entrypoints.EntrypointTests.test_failed_run_is_recorded_as_failure_in_ledger`
- `tests.test_entrypoints.EntrypointTests.test_ledger_written_when_framework_available`

原因：这两个用例期望入口脚本在检测到 mini_agent 时写入 `.agent/run_status.jsonl`，而测试使用的临时项目里没有 `.agent` 目录，文件因此不存在。这是测试环境的问题，入口脚本本身的记账逻辑未被修改。

偶尔还会出现 `tests.test_merged_results` 的损坏文件用例失败，属于时序相关的已知不稳定项，重跑通常通过。

## 新增功能时的约定
- 每个阶段新增对应的测试文件，并在 `next_doc/info_hub_implementation_record.md` 中记录验证结果与未验证事项。
- 看板测试优先用 `AppTest`，通过 `key` 查找控件；设置 `at.session_state` 可以模拟“已有快照”等前置状态。
