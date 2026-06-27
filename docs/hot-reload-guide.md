# 热重载机制说明（Skills / Agent Profiles）

> 在 daemon 运行期间自动感知 `skills/` 和 `.agent/agents/` 目录的文件变化，无需重启即可加载新增或修改的 Skill 与 Agent Profile。

---

## 1. 设计目标

| 场景 | 原有行为 | 热重载后 |
|------|----------|----------|
| 新增 SKILL.md | 需重启才能被发现 | 下一个 turn 自动生效 |
| 修改 SKILL.md 内容 | 需重启 | 下一个 turn 自动重读 |
| 删除 SKILL.md | 需重启 | 下一个 turn 从可用列表移除 |
| 新增 `.agent/agents/xxx.md` | 需重启 | 下一个 turn 自动生效 |
| 手动立即刷新 | — | `/reload` 命令 |

---

## 2. 工作原理

### 2.1 轮询模型

热重载采用**纯 mtime 轮询**，无需 inotify / watchdog 等外部依赖：

```
_agentic_loop()
  └── 每个 turn 开始时
        └── HotReloader.poll()
              └── (debounce: 距上次检查 < 2s 则跳过)
              └── _DirectoryWatch.check()
                    └── 扫描目录内所有 .md 文件的 mtime
                    └── 对比快照：找出 added / modified / removed
                    └── 有变化 → 调用 reload_fn(dirs)
                              → 打印变更通知
                              → 清除 system prompt 缓存
```

### 2.2 增量更新

`SkillLoader.rediscover()` 和 `AgentProfileLoader.rediscover()` 均为增量操作：

- **新增文件** → 解析并加入 `_all`
- **修改文件** → 重新解析，覆盖 `_all` 中的旧版本
- **删除文件** → 从 `_all` 和 `_active` 中移除（已激活的 skill 同步取消激活）
- 重建 `SkillUsageDetector` 指纹（供使用检测用）

---

## 3. 使用方式

### 3.1 自动轮询（无需任何操作）

启动 mini-agent 后，向 skills 目录添加新的 SKILL.md 文件，下一次向 agent 发送消息时，变更通知会出现在终端：

```
[hot-reload] [skill] +1 added (my-new-skill)
```

若同时有多种变更：

```
[hot-reload] [skill] +1 added (sql-expert); ~1 modified (docx); -1 removed (old-tool)
```

### 3.2 `/reload` 手动强制刷新

```
/reload
```

跳过 debounce，立即重扫所有监视目录。即使文件没有变化，也会重新执行 reload 回调（用于排查加载问题）：

```
[reload:skill] +1 added (my-skill)
[reload:agent] reloaded (no file changes)
```

---

## 4. 配置

`AppConfig` 上的相关字段（通过 `agent_config.json` 或启动参数设置）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `hot_reload_interval_s` | `2.0` | 轮询 debounce 间隔（秒）。减小可提高响应速度，增大可减少磁盘 I/O |

> 当前该字段通过 `getattr(cfg, "hot_reload_interval_s", 2.0)` 读取，
> 如需自定义可直接在 `agent_config.json` 中加入：
> ```json
> { "hot_reload_interval_s": 1.0 }
> ```

---

## 5. 监视目录说明

`HotReloader` 在 `Agent.__init__` 中初始化，自动注册以下目录：

| 类别 | 监视目录 | reload 回调 |
|------|----------|-------------|
| Skill | `SkillLoader._dirs`（即启动时的 `skill_dirs` 列表） | `SkillLoader.rediscover()` |
| Agent Profile | `AgentProfileLoader._dirs`（全局 + 项目级 agents 目录） | `AgentProfileLoader.rediscover()` |

glob 模式为 `**/*.md`，递归匹配所有 Markdown 文件。

---

## 6. system prompt 缓存失效

发现变更后，`agent._cached_system` 会被置为 `None`，确保下一次 `_call_llm()` 中 `_build_system()` 重新构建 system prompt，新 skill/agent 内容立即注入。

---

## 7. 实现文件

| 文件 | 改动 |
|------|------|
| `perception/hot_reload.py` | **新增**。`HotReloader`、`_DirectoryWatch`、`ChangeReport` |
| `skills/__init__.py` | `SkillLoader` 新增 `rediscover(dirs)` 方法 |
| `orchestrator/agent_profiles.py` | `AgentProfileLoader` 新增 `rediscover(dirs)` 方法 |
| `agent.py` | `__init__` 初始化 `self._hot_reloader` 并注册监视器；`_agentic_loop` 每 turn 调 `poll()` |
| `cli/repl.py` | 新增 `/reload` slash 命令处理分支 |
| `cli/parser.py` | help 文本新增 `/reload` 说明 |

---

## 8. 扩展：监视其他目录

`HotReloader.register()` 是通用接口，可挂载任意目录和回调：

```python
agent._hot_reloader.register(
    dirs=[Path(".agent/prompts")],
    reload_fn=lambda dirs: pm.reload(),
    category="prompt",
    glob_pattern="*.md",
)
```
