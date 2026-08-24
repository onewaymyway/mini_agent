# browser-site-scraper 三档机制（script→skill→explore）测试指南（在 agent 里对话验证）

> 对应 `.claude/skills/browser-site-scraper/`（`generative-capability` skill
> 里唯一接了三档机制的试点领域）。
>
> 目的：验证 `next_doc/generative_capability_raw_result_and_hybrid_merge_
> plan.md` 第3节、`next_doc/generative_capability_three_tier_improvement_
> plan.md` 描述的 script→skill→explore 三档执行机制**在真实 agent 对话
> 场景里可用**——和 `test_cases/text-transform-capability-testing-guide.md`
> 一样，走真人对话输入，不是直接调引擎代码。
>
> **前置说明（很重要）**：三档机制里的 SKILL 档（`playbook_repo`/
> `skill_runner`）此前存在一个真实的接线缺口——`capability_engine.py`/
> `distiller.py` 代码本身已经实现，但 `tools/capability_call.py`（agent
> 对话唯一会调用的入口）此前从未把这两个依赖注入 `CapabilityEngine`，
> 导致 SKILL 档在真人对话里永远走不到，只在单元测试的桩环境里生效。这个
> 缺口现在已经修复：`capability_call.py` 默认按 `agent_config.json ->
> generative_capability.skill_tier_max_turns`（默认 **40**）注入
> `playbook_repo`/`skill_runner`，不再需要每个 skill 显式声明才能用上。
> 本指南就是在验证这次修复本身，以及修复后 SKILL 档、探索失败兜底产出
> playbook.md 这两条路径在真实对话里是否真的生效了。
>
> 需要一个能实际对话的 mini-agent 会话（配置好任意一个 provider 的 API
> key）。下面每一步都给出：**该在会话里输入什么** + **预期看到什么**。

---

## 前置条件

1. 已经能正常启动 mini-agent CLI 并进行对话，配置了可用的 LLM provider。
2. `.claude/skills/browser-site-scraper/` 在当前项目目录下存在（随仓库
   自带），已有两个真实 member：`baidu`、`zhihu`，`registry.json` 里都能
   看到 `success_count`/`fail_count`/`available_tiers` 等字段。
3. **步骤 1、4 依赖真实浏览器环境**（`browser-core` 底层要连一个真实的
   Chrome + 调试端口）。如果你本机/服务器没有可用的浏览器，这两步会在
   "浏览器层面"失败——这属于环境限制，不是本指南要验证的机制本身；可以
   跳过，直接看步骤 2、3、5、6（它们不依赖真实抓取是否成功）。
4. 步骤 2、3 需要你能直接读写仓库文件（用 mini-agent 自己的
   `read_file`/`write_file`/`bash` 工具在对话里操作即可，不需要跳出会话）。
5. 每一步都在**同一个会话**里连续输入即可，不需要每次重启（步骤 5 例外，
   它需要改一次 `agent_config.json` 后重启会话才能生效，见该步骤说明）。

---

## 步骤 1（可选，依赖真实浏览器）：script 档正常命中、执行

**输入：**

```
用 browser-site-scraper 抓一下百度搜索「mini-agent github」的搜索结果标题列表
```

**预期效果：**

- agent 调用 `capability_call(skill_name="browser-site-scraper", request=
  {...})`。
- `resolve_reason` 应为 `keyword_match` 或 `llm_match`，`member_id:
  "baidu"`——命中已有 script。
- 浏览器环境可用时 `status` 多半是 `success`；不可用/被反爬拦截时是
  `fail`，都属于正常表现，不是本步骤要验证的重点。

**这一步验证了什么**：三档机制接入后，最常见、成本最低的 script 档路径
没有被破坏——不应该因为新增了 SKILL 档就绕过 script 直接走别的档位。

---

## 步骤 2：制造"没有 script、只有 active playbook"场景，验证 SKILL 档被真正调用

这一步不依赖真实抓取是否成功，只验证"有 active playbook 时，
`capability_call` 会真的走到 SKILL 档、并且 `resolve_reason` 正确标记为
`skill_playbook`"这条此前有接线缺口、现在已修复的路径。

**输入（第一步，备份并移走 baidu 的 script.py）：**

```
把 .claude/skills/browser-site-scraper/members/baidu/script.py 复制一份
备份成 script.py.bak，然后把原文件删掉（一会儿测完我们再恢复）
```

**输入（第二步，预置一份能保证命中的 playbook）：**

```
帮我在 .claude/skills/browser-site-scraper/playbooks/baidu/ 目录下创建
meta.json 和 v1.md：
meta.json 内容是 {"active_version": 1, "versions": {"1": {"status":
"active", "success_count": 0, "fail_count": 0, "consecutive_fail": 0}}}
v1.md 内容随便写几行步骤说明，比如"打开 https://www.baidu.com/s?wd=
{query}，等待搜索结果加载，用 browser_extract_content 提取结果标题，
按 {"results": [...]} 的形状返回"
```

> 如果你不确定 `meta.json` 该长什么样，可以先让 agent 读一下
> `src/mini_agent/hybrid_exec/playbook_repository.py` 里 `save_new_version`
> /`get_active_playbook` 的实现，照着现有格式手写一份效果完全一样——这一步
> 的目的是制造"存在 active playbook"这个前置条件，不是测试
> `PlaybookRepository` 本身（那部分已有独立单元测试
> `tests/test_hybrid_exec_playbook_repository.py` 覆盖）。

**输入（第三步，正式测试）：**

```
现在用 browser-site-scraper 抓一下百度搜索「mini-agent github」的结果，
告诉我这次 capability_call 返回的 resolve_reason 是什么
```

**预期效果：**

- `resolve_reason` 应为 **`skill_playbook`**（而不是 `keyword_match`/
  `llm_match` 命中 script——因为 script.py 已被移走；也不是直接进入全新
  `explore`——因为存在 active playbook，SKILL 档应该被优先尝试）。
- 由于 `v1.md` 只是简单步骤说明，`PlaybookRunner` 驱动的轻量 Agent 是否
  真的抓成功、拿到正确数据，取决于你写的步骤是否足够具体可执行、以及浏览
  器环境是否可用——**这不是本步骤的重点**，重点是确认调用链路确实经过了
  SKILL 档，而不是跳过它直接进入 explore。如果浏览器环境不可用，你应该
  仍然能看到 `resolve_reason: skill_playbook`，只是最终 `status` 是
  `fail`。
- 如果这一步看到的 `resolve_reason` 是别的值（比如直接触发了全新
  explore），说明 SKILL 档接线修复没有生效，或者 `agent_config.json` 里
  被显式配置成了关闭（见步骤 5），需要排查。

**输入（收尾，恢复现场）：**

```
把 script.py.bak 改回 script.py，删掉 playbooks/baidu/ 目录，恢复到测试前的状态
```

**这一步验证了什么**：script→skill→explore 优先级调度、以及此前
`capability_call.py` 未注入 `playbook_repo`/`skill_runner` 的接线缺口，
现在在真实对话链路里确认已经修复——不再需要预先在代码里手工构造引擎才能
触发 SKILL 档。

---

## 步骤 3：探索失败时自动产出 playbook.md 兜底（脚本蒸馏失败兜底路径）

对应 3.3d 节"脚本蒸馏三条路径全部失败、但探索本身成功且数据通过校验时，
自动整理成 playbook.md 落盘"。这条路径依赖一次真实成功的探索（浏览器
环境可用）+ 脚本蒸馏恰好失败这个组合，真人对话里较难稳定复现，因此本步骤
给出的是"如何在对话里观察这条路径是否被触发"，而不是保证每次都能触发。

**输入：**

```
用 browser-site-scraper 抓一下淘宝搜索「无线耳机」的商品列表（这是一个
还没有对应 member 的全新站点，会触发探索）
```

**预期效果（几种可能，都要如实观察）：**

- 探索成功、脚本蒸馏也成功 → 正常产出 `members/taobao/script.py`，
  `status: success`，与探索机制的既有行为一致，不是本步骤重点。
- **探索成功，但脚本蒸馏失败、自动落 playbook 兜底**（本步骤真正想验证
  的路径）→ 之后追问：

```
刚才那次抓淘宝的探索，最后是蒸馏成了 script.py 还是 playbook.md？帮我看看
.claude/skills/browser-site-scraper/ 下 members/taobao/ 和 playbooks/taobao/
两个目录分别有没有对应文件
```

  agent 应该能读出 `playbooks/taobao/v1.md` 存在、`members/taobao/` 下没有
  `script.py`（或 `meta.json` 里 `distill_source_kind: "playbook"`/
  `registry.json` 里 `execution_tier: "skill_only"` 这类标记），说明这次
  探索的沉淀物确实是 playbook 而不是脚本。
- 探索本身就失败（反爬/登录墙/环境问题）→ `status: not_implemented`，
  `members/taobao/`、`playbooks/taobao/` 都不会有新文件，这是正常的诚实
  失败，不算本步骤失败，只是没能触发到"蒸馏失败兜底"这条具体路径，换一个
  更容易被反爬拦截、或结构更复杂的站点重试更容易复现。

**这一步验证了什么**：三档机制"降级"方向（脚本蒸馏失败退化为 playbook）
是自动化的、不需要人工介入的——这与步骤 2"人工预置 playbook"形成对照：
步骤 2 验证的是"有 playbook 时会不会用"，步骤 3 验证的是"playbook 会不会
在探索失败时自动产生"。

---

## 步骤 4（可选，依赖真实浏览器 + 较多轮次）：SKILL 档证明可靠后自动升级为 script.py

这条路径成本最高（需要同一个 member 连续多次通过 SKILL 档成功执行，达到
`skill_upgrade_success_threshold`，默认 3 次），且依赖真实浏览器多轮交互，
不适合作为常规回归测试，这里只给出验证思路，不强制要求每次都跑：

**前置**：延续步骤 2 制造的"baidu 只有 playbook、没有 script"场景（不要
在步骤 2 末尾恢复现场），连续 3 次成功调用 `capability_call` 命中
`baidu` 走 SKILL 档且都成功。

**预期效果**：第 3 次成功后，`members/baidu/` 目录下应该出现新的
`script.py`（自动蒸馏产物），之后第 4 次调用会重新命中 `keyword_match`/
`llm_match` 走 script 档而不再是 `skill_playbook`。可以追问 agent：

```
帮我看看现在 members/baidu/script.py 是不是新生成的（对比一下修改时间和
内容），以及 registry.json 里 baidu 的 available_tiers 是不是变成了
["script", "skill"]
```

**这一步验证了什么**：三档机制"升级"方向（SKILL 档证明可靠后固化为
script）同样是自动触发的。如果浏览器环境不可用导致 SKILL 档执行总是失败，
这一步天然无法复现，属于环境限制，不代表机制本身有问题。

---

## 步骤 5：`skill_tier_max_turns` 可以通过 `agent_config.json` 配置（默认 40）

**输入（查看当前默认值）：**

```
帮我看一下当前 agent_config.json 里有没有配置 generative_capability 这个
块，如果没有的话，SKILL 档现在用的回合预算默认是多少？
```

**预期效果**：agent 应该能说出——没配置时 `AppConfig.generative_capability
.skill_tier_max_turns` 默认是 **40**，`capability_call.py` 会用这个值构造
`PlaybookRunner`，不需要用户手动配置才能用上 SKILL 档（这是本次接线修复的
核心行为：默认启用，而不是默认关闭）。

**输入（修改配置并验证生效，需要重启会话）：**

```
帮我在 agent_config.json 里加一段：
"generative_capability": {"skill_tier_max_turns": 5}
保存后我会重启会话
```

重启会话后输入：

```
现在 SKILL 档的回合预算配置成多少了？
```

**预期效果**：agent 读取新的 `agent_config.json` 后，应该能确认
`skill_tier_max_turns` 变成了 5（可以让它读一下 `AppConfig.
generative_capability` 或直接追踪 `capability_call.py` 里的取值逻辑来
确认）。如果你继续做一次步骤 2 那样的 SKILL 档调用，`PlaybookRunner` 内部
轻量 Agent 的回合预算应该受限于 5 轮（对复杂抓取任务大概率不够用，容易在
拿到结果前就打满预算失败——这是预期内的，用来确认这个数字确实生效了，不是
说 5 是一个推荐值）。

**输入（验证 0/负数=显式关闭）：**

```
把 agent_config.json 里的 skill_tier_max_turns 改成 0，保存后重启会话，
然后重复步骤 2 的测试（baidu 没有 script 但有 active playbook），这次
resolve_reason 应该是什么？
```

**预期效果**：`resolve_reason` **不应该**是 `skill_playbook`——`max_turns
<=0` 时 `capability_call.py` 不会构造 `playbook_repo`/`skill_runner`，
`_try_skill()` 会静默跳过，直接进入 explore（或如实返回
`not_implemented`），等同于修复前"SKILL 档完全不可用"的状态——这条是
留给用户的显式关闭开关，不是 bug。

**测试完成后记得把 `agent_config.json` 里的 `generative_capability` 块
删掉或改回默认值，恢复正常配置。**

**这一步验证了什么**：`skill_tier_max_turns` 默认值（40）在没有任何配置
时就让 SKILL 档可用，同时保留了用户按需调整、甚至显式关闭的能力——对应你
提出的"配置里没有的时候应该用默认值而不是不开启"这个要求在代码里确实是
这样实现的。

---

## 步骤 6：raw_result 落盘化——大段输出用路径取回，不是裸 id

**输入：**

```
用 bash 工具跑一下 find . -name "*.py"（在 mini-agent 项目根目录下），
把完整结果列出来
```

**预期效果：**

- 原始输出被截断/摘要后，末尾应该有一句形如 `Full output saved to
  <path>. Use read_file to inspect it.` 的提示，`<path>` 是具体文件路径
  （形如 `<project_root>/.agent/raw_results/<session_id>/<result_id>.txt`），
  **不是**裸 `result_id`。

**之后追问：**

```
帮我看看完整结果
```

**预期效果**：agent 调用 `view_raw_result(path="...")` 或 `read_file
(path=...)`，参数是 `path` 不是 `result_id`，返回完整未截断的原始输出。

**这一步验证了什么**：`RawResultStore` 落盘化（`next_doc/
generative_capability_raw_result_and_hybrid_merge_plan.md` 第1节）在真实
对话链路里生效——这一步与三档机制本身无关，但同属这两份方案文档的改动
范围，一并覆盖，避免遗漏。

---

## 小结：本指南覆盖的机制点对照表

| 步骤 | 覆盖的机制点 | 对应文档章节 |
|---|---|---|
| 1 | script 档命中、三档机制未破坏既有最常见路径 | raw_result_and_hybrid_merge_plan.md 3.2 |
| 2 | SKILL 档被真正调用、`resolve_reason: skill_playbook`；**接线缺口修复的核心验证** | 3.3c；修复见 `capability_call.py` |
| 3 | 探索失败自动产出 playbook.md 兜底（降级方向） | 3.3d |
| 4（可选） | SKILL 档证明可靠后自动升级为 script.py（升级方向） | 3.3e |
| 5 | `skill_tier_max_turns` 默认值 40，可通过 `agent_config.json` 配置，`<=0` 显式关闭 | three_tier_improvement_plan.md + 本次接线修复 |
| 6 | raw_result 落盘化，路径而非 id | 第1节 |

如果步骤 2 能看到 `resolve_reason: skill_playbook`、步骤 5 能确认
`skill_tier_max_turns` 默认生效且可配置，说明"SKILL 档默认对所有
generative-capability skill 可用"这次修复在真实对话场景里确实成立，
不再是"代码实现了但对话里永远走不到"的状态。步骤 3、4 依赖真实探索成功，
条件允许时应该跑一遍确认自动化闭环没问题，条件不允许时可以先跳过，只要
步骤 2、5 通过就说明本次修复本身是有效的。

---

## 附：与其它测试指南的关系

- `test_cases/text-transform-capability-testing-guide.md`：验证
  `generative-capability` 机制最基础的一层（skill 发现、一级/二级匹配、
  诚实失败），用零外部依赖的 `text-transform-capability` 作为试点，
  不涉及三档机制。
- 本文档：验证在此之上的 script→skill→explore 三档手段调度、以及此前
  存在、现已修复的 SKILL 档接线缺口，用 `browser-site-scraper` 作为试点
  （目前唯一接入三档机制的领域）。
- 如果后续 `text-transform-capability`/`doc-template-generation` 等其它
  generative-capability skill 也接入了三档机制，应在对应 skill 下补充
  等价的步骤，而不是假设所有 skill 行为一致。
