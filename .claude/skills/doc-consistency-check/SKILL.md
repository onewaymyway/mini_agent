---
name: doc-consistency-check
description: 分析项目文档与实际代码的一致性，找出文档缺失、内容滞后、命令列表不全等问题。当用户说"文档不一致"、"文档过时"、"检查文档"、"文档和代码不同步"时使用。
triggers: 文档不一致, 文档过时, 检查文档, 文档同步, 文档更新, doc consistency, 文档落后, 代码文档不同步, 文档缺失, 文档审查
---

# 文档一致性检查 Skill

## 概述

系统化分析项目中文档与实际代码的不一致问题，通过 git 提交记录时间对比和内容关键词匹配，定位文档缺失、内容滞后、功能未记录等问题。

## 分析流程

### 第一步：建立文档-代码映射

明确每个文档对应的代码模块：

| 文档 | 对应代码模块 |
|------|-------------|
| README.md | 全局概览、命令列表、功能特性 |
| docs/config-guide.md | config/models.py, config/loader.py |
| docs/commands-and-tools-reference.md | cli/repl.py, cli/commands/*, tools/* |
| docs/system-overview.md | 全局架构、各模块 |
| docs/llm-failover-guide.md | llm/client_pool.py, llm/base.py, llm/retry.py |
| docs/http-api-guide.md | api/routes.py, api/server.py |
| docs/terminal-io-guide.md | ui/terminal.py, ui/raw_key_listener.py |
| docs/introspection-guide.md | tools/introspection.py |
| docs/skill-system-guide.md | skills/__init__.py |
| docs/storage-design.md | storage/paths.py |
| .claude/skills/*/SKILL.md | 对应 skill 目录下的 *.py 脚本 |

### 第二步：获取代码和文档的最后修改时间

```bash
# 获取核心代码文件最后修改时间
for f in src/mini_agent/agent.py src/mini_agent/config/loader.py ...; do
    echo "$(git log -1 --format='%ai' -- $f) $f"
done

# 获取文档最后修改时间
for f in README.md docs/config-guide.md ...; do
    echo "$(git log -1 --format='%ai' -- $f) $f"
done

# 获取 SKILL.md 及对应脚本修改时间
for skill_dir in .claude/skills/*/; do
    skill_name=$(basename "$skill_dir")
    md_time=$(git log -1 --format='%ai' -- "$skill_dir/SKILL.md")
    py_latest=$(find "$skill_dir" -name '*.py' -type f | xargs git log -1 --format='%ai' -- | sort -r | head -1)
    echo "$skill_name | SKILL.md=$md_time | 脚本=$py_latest"
done
```

### 第三步：时间差对比，找出滞后文档

规则：**代码最后修改时间 > 文档最后修改时间** → 文档可能滞后

重点关注：
- 时间差 > 1 天的 → 高可疑
- 时间差 > 3 天的 → 严重滞后
- 文档从未创建的 → 完全缺失

### 第四步：内容关键词匹配验证

对高可疑文档做内容检查，确认是否真的不一致：

```bash
# 检查文档是否包含新功能关键词
grep -n 'raw_output\|FormatCorrection\|tool_call_retry\|auto_compact' docs/config-guide.md

# 检查 README 是否覆盖新工具
grep -n 'search_knowledge\|introspect\|agent_status\|agent_inspect' README.md

# 对比 README 命令列表 vs 实际命令模块
ls src/mini_agent/cli/commands/
grep -oP '\| `/[a-z_]+`' README.md | sort -u
```

### 第五步：检查未提交的文档

```bash
# 检查 SKILL.md 是否纳入 git
git status --short .claude/skills/

# 检查新增代码文件（无文档覆盖）
git log --since='30 days ago' --diff-filter=A --name-only --format='' -- src/ | sort -u
```

### 第六步：检查只改代码没改文档的提交

```bash
# 找出最近只修改代码未修改文档的提交
git log --since='30 days ago' --format='%ai %s' --name-only --diff-filter=M \
  | awk '/^[0-9]/{commit=$0; next} 
         /^src\//{code_files[commit] = code_files[commit] " " $0} 
         /^docs\/|^README|^CLAUDE|^\.claude\/skills\/.*SKILL\.md/{doc_files[commit] = doc_files[commit] " " $0} 
     END{for(c in code_files){ if(!(c in doc_files)){ print c; print "  代码:" code_files[c] } }}'
```

## 结果分级

### 🔴 高优先级：功能已上线但文档完全缺失
- 新功能/新模块已合并到主分支，但没有任何文档描述
- README 中完全未提及的重要功能
- 新增配置项未出现在 config-guide.md

### 🟡 中优先级：文档内容滞后于代码
- 文档最后更新时间早于对应代码的最后修改时间
- 文档中缺少新功能的描述（但旧功能描述仍然正确）

### 🟠 列表不全
- README 命令表格缺少实际存在的命令
- 工具列表缺少新注册的工具
- 配置项列表缺少新增的配置

### 🔵 低优先级：版本控制问题
- SKILL.md 未纳入 git 追踪
- 文档文件权限或编码问题

## 输出格式

生成报告保存到 `docs/doc-consistency-report-YYYY-MM-DD.md`，包含：
1. 按优先级分级的表格
2. 每个问题标注文档名、缺失内容、代码变更时间
3. 汇总统计
4. 优先更新建议

## 注意事项

- **时间差只是信号，不是结论**：代码改了不一定影响文档（如 bug 修复、重构），需要内容验证确认
- **关注功能性变更**：新增功能、新增配置项、新增命令 → 必须更新文档；内部重构、bug 修复 → 视情况更新
- **SKILL.md 特殊性**：未提交到 git 的 SKILL.md 无法追踪变更历史，应优先纳入版本控制
- **定期执行**：建议每次大版本发布前执行一次一致性检查
- **排除噪音**：`__pycache__`、`.git`、`venv`、`node_modules` 等目录应排除
