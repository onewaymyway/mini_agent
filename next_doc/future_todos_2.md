上下文管理方面，system prompt 每次 LLM 调用都重新组装，流程设计合理，有 turn 级缓存和 skill 目录缓存。但 skill_chunking 是个半成品——开关有了，SkillLoader 的实现完全没有按 query 过滤段落，等于白费。
工具结果维护最严重的问题是 bash 结果被永久缓存，git status、ls 这类命令执行两次会拿到同一个旧结果。截断策略也有方向性错误，bash 错误信息在尾部，结果却被保留头 70%。
SubAgent 交互最核心的缺陷是输出 [:3000] 硬截断——SubAgent 费了好几轮写出来的代码或报告，主 Agent 只能看到残缺版本，而且完全不知道被截了。
信息继承关系是问题最集中的地方，继承清单里 6 个字段是 ✗（skill_loader、tool_cache、memory、project_snapshot、history、file_watcher），主从之间的信息鸿沟很深——SubAgent 是在完全不知道主 Agent 已做了什么、已激活了哪些 skill 的情况下独立运行的。
改进优先级：① 工具缓存按类型分层（bash 永不缓存，read_file 按 mtime 失效）→ ② 超长输出写文件返回路径 → ③ SubAgent 继承激活 skill → 后续再做共享 cache 和 history 类型化。


P1 ① 按工具类型设置缓存策略（最高优先级）
bash / glob / list_dir 等状态依赖型工具应绕过缓存；read_file / grep 的缓存 key 需加入文件 mtime 使其自动失效。

NEVER_CACHE = {"bash", "list_dir", "glob"}
MTIME_CACHE = {"read_file", "grep"}

def put(self, tool_name, input, result):
    if tool_name in NEVER_CACHE:
        return  # 永不缓存
    key = self._make_key(tool_name, input)
    if tool_name in MTIME_CACHE:
        path = input.get("path", "")
        mtime = Path(path).stat().st_mtime if path else 0
        key = f"{key}:{mtime:.3f}"
    self._cache[key] = (result, time.time())
P1 ② 修复 SubAgent 输出截断（3000 字硬限制）
超长输出应写文件后返回路径，或分摘要+详情结构，绝不粗暴截断。主 Agent 可按需 read_file 拿完整结果。

if len(output) > 3000:
    out_path = task_dir / f"task_{task_id}_output.txt"
    out_path.write_text(output, encoding="utf-8")
    data["output"] = output[:400] + f"\n...[完整结果已写入 {out_path}，请用 read_file 读取]"
    data["output_file"] = str(out_path)
else:
    data["output"] = output
P1 ③ SubAgent 继承主 Agent 激活的 Skill
spawn_agent 时将当前激活 skill 名称列表写入 Task，SubAgent 在 _build_agent 中根据名称激活对应 SKILL.md，保持主从上下文一致。

# orchestration.py — spawn_agent 工具内部
active = [s["name"] for s in skill_loader.get_catalog() if s["active"]]
task = Task(..., inherited_skills=active)

# sub_agent.py — _build_agent
if task.inherited_skills:
    for name in task.inherited_skills:
        skill_loader.activate(name)  # 按名称激活
P2 ④ 共享 ToolResultCache（线程安全）
将主 Agent 的 ToolResultCache 实例（加读写锁）通过 TaskManager 传递给 SubAgent。并发处理同一代码库时文件缓存命中率显著提升，I/O 放大问题消除。

P2 ⑤ history 条目类型化，消除字符串前缀依赖
在 history 条目中加入 _type 字段明确区分消息来源，压缩策略无需靠前缀判断 turn 边界，可精确切割。

# 存储时加入类型标记
{"role":"user","content":"<tool_result...>", "_type":"tool_result"}
{"role":"user","content":"帮我写个测试",        "_type":"user_input"}

# 压缩时精确识别
real_turns = [m for m in history if m.get("_type") == "user_input"]
P2 ⑥ project_snapshot 改为懒注入 + 状态提示
扫描未完成时在 system prompt 头部注入一行提示 [项目结构扫描中，请稍后…]，而非静默缺失。扫描完成后通知用户可重问，避免 Agent 基于不完整认知误判。