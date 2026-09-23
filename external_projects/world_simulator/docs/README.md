# world_simulator 项目文档

> 本目录是 `world_simulator` 的面向使用者的文档集合，回答"这是什么 /
> 怎么用 / 怎么验证它是好的"这三个问题。更偏工程/设计取向的内容
> （为什么这样设计、复用了宿主的哪些机制、分阶段实施记录）在项目根目录
> 的 [`PROJECT.md`](../PROJECT.md)；最初的完整方案讨论在仓库
> `next_doc/world_simulator_external_project_plan.md`。这两份不重复
> 抄写，`docs/` 下的文档需要背景时会直接链接过去。

## 目录

- [`overview.md`](./overview.md) —— **项目说明文档**：这是什么、能做
  什么、核心概念（模拟实例/时间步/分支/自动挡）、目录结构、怎么启动。
  新人第一次接触这个项目，从这份开始看。
- [`testing_guide.md`](./testing_guide.md) —— **如何测试当前项目**：
  分两条路径——① 跑自动化单元测试（不需要真实 LLM，几秒钟出结果）；
  ② 端到端手动验证主链路（需要真实 LLM 配置，逐条列出"输入什么 →
  预期看到什么 → 怎么判断通过/失败"）。想验证"我改的代码有没有搞坏
  东西"或者"这个项目到底能不能跑起来"，看这份。
- [`service_api.md`](./service_api.md) —— **作为独立外部服务调用**：
  不打开 Streamlit 看板，从命令行或 HTTP 请求创建/推进/查询/管理
  模拟实例（供脚本、未来的主 agent 等外部调用方使用）。

## 30 秒速览

```
一句话意图 → 生成提案草稿 → 编辑确认 → 落盘为模拟实例（step 0）
                                          │
                                    推进下一步（可选一个方向）
                                          │
                              next_state + 叙事 + 新的候选方向
                                          │
                          （重复推进 / 分叉分支 / 切自动挡托管）
```

两种用法：
1. **命令行 / headless**：`python entrypoints/create_simulation.py "..."`，
   适合脚本化、daemon 调度、CI 里跑冒烟测试。
2. **独立看板**：`streamlit run app.py`，"夜航日志"主题，模拟列表 /
   创建向导 / 实例详情 / 对比视图 / 存档管理 / 游戏化视图 六个页面，
   日常交互推荐走这条路。

两条路径背后是同一套 `world_simulator/` 业务代码（`engine.py` /
`store.py` / `spec_generator.py` / `branch_manager.py` /
`autopilot.py` / `achievements.py`），互不冲突，随时切换。
