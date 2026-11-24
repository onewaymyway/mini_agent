# 架空世界工坊 · 使用说明

## 启动看板

```bash
pip install -r requirements.txt
streamlit run app.py --server.port 8509
```

## 典型流程

0. **世界观简介**：在「总览」生成一篇背景介绍，快速了解当前世界观；世界推进后可重新生成。
1. **新建**：输入一句概念，生成宪章草案与首批设定。
2. **设定与提案**：批准宪章（`set_charter`）。可用「自动完善世界观」一键补全缺口并扩展；或在「按要求扩展」中用一句话提出需求（如「北方增加一个冰原部族」）。查看待确认提案。
3. **推进**：选择步数推进历史。每步生成事件、新角色与设定更新；被拦截的写入会注明原因。
4. **时间线**：在任一事件之后开新分支（即回退），可在分支间切换与对比。
5. **故事与漫画**：选择事件生成小故事线，成文短篇或直接创作；生成分镜、生成漫画图；导出给 novel_writer。

## 命令行

```bash
python entrypoints/cli.py health
python entrypoints/cli.py init --concept "蒸汽与符文并存的海上帝国"
python entrypoints/cli.py approve --world <wid> --proposal <pid>
python entrypoints/cli.py advance --world <wid> --steps 2
python entrypoints/cli.py fill --world <wid>
python entrypoints/cli.py expand --world <wid> --request "北方增加一个冰原部族"
python entrypoints/cli.py enrich --world <wid>
python entrypoints/cli.py overview --world <wid>
python entrypoints/cli.py arc --world <wid> --from 0 --to 30
python entrypoints/cli.py short --world <wid> --arc <sid>
python entrypoints/cli.py board --world <wid> --story <sid>
python entrypoints/cli.py comic --world <wid> --story <sid>
python entrypoints/cli.py export --world <wid> --story <sid>
```

## 环境变量

| 变量 | 作用 |
|---|---|
| `WORLD_FORGE_PROVIDERS=stub` | 全部离线（规则文本 + 占位图），不消耗额度 |
| `WORLD_FORGE_DATA_DIR` | 数据目录，默认 `data/` |
| `MINI_AGENT_ROOT` | mini_agent 仓库根，用于找到 LLMHelper 与 gen_image_with_text skill |
| `WORLD_FORGE_SKILLS_DIR` | 手动指定 `.claude/skills` 目录 |
| `AGNES_API_KEYS` / `AGNES_API_KEY` | 图像模型 key（与 skill 规则相同） |

## 测试

```bash
python -m pytest -q tests
```
