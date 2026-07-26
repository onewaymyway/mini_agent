# browser-cdp 稳定性修复说明

状态：已落地
关联：next_doc/workflow_python_step_and_zhihu_publish_plan.md §E

## 问题 1（根因已定位）：新建的浏览器记不住登录状态

**根因**：`browser_launch.py` 里专用实例（`--dedicated`）的 profile 目录默认是
`DEFAULT_PROFILE_ROOT = os.path.join("temp", "cdp_brower_data")`——一个相对当前工作目录的
`temp/` 子目录。`temp/` 这个名字在几乎所有工程约定里都代表"可随时清空的临时目录"，很多
workflow/CI/构建脚本会在每次运行前 `rm -rf temp/` 之类清理，这会把整个 Chrome profile
（包含登录 cookies/session storage）连同锁文件一起删掉。表现出来就是"每次新建的浏览器都不
记得之前的登录状态"——但根本不是 CDP 连接或 Chrome 本身的问题，是 profile 存放位置选错了。

**修复**：把 `DEFAULT_PROFILE_ROOT` 改到 `~/.cdp_skill/profiles/`（与 `registry.json` 同级，
都在 `SKILL_HOME` 下），这是本技能专属、不属于任何项目 `temp/` 目录的稳定位置，不会被项目
自身的清理流程误伤。

改动文件：`.claude/skills/browser-cdp/browser_launch.py`（`DEFAULT_PROFILE_ROOT` 定义处）。

**使用侧同样要注意**：即使 profile 目录本身稳定了，如果每次调用 `--dedicated` 时传的
`--name` 不固定（比如每次让 agent 临时想一个名字，或者不同 step 用了不同名字），也会导致
"看起来登录状态丢了"——因为不同 name 对应不同 profile 目录，本来就是两个完全独立的浏览器。
`SKILL.md` 已经加了醒目提示：同一个任务/workflow 内所有涉及浏览器的 step 必须用同一个固定
的 `--name`（本次知乎发布 workflow 统一用 `zhihu_session`，见
`.agent/workflows/zhihu_content_publish/prompts/02_search_zhihu.md` /
`prompts/04_enrich_questions.md`）。

## 问题 2：不能正确识别已经启动的调试浏览器

代码审查结论：`cmd_dedicated()` 判断"是否已有可用实例"这部分逻辑本身是对的——`is_debug_port_alive()`
真实发 HTTP 请求探测 `/json/version`，不是只看内存/registry 记录，判断优先级也已经是
"真实探测优先于 registry 状态"。但存在一个健壮性缺口：**registry.json 是唯一的状态来源，
它本身没有并发保护**，如果被其它调用（比如 `--stop-dedicated`）改动、或者文件本身损坏/被清空，
即使浏览器进程和 profile 都还在，也会因为 registry 里查不到条目而走到"新建一个"的分支，
造成同一个 `--name` 下出现两个互不相关的浏览器实例。

**修复**：新增 profile 目录下的锁文件 `<profile_dir>/.mini_agent_lock.json`（记录
port/pid/启动时间），作为 registry 之外的第二条线索。`cmd_dedicated()` 在 registry 查不到
对应 `--name` 的条目时，会先尝试读这个锁文件拿到端口号去做真实探测，探测成功就直接复用并
把信息回填进 registry，而不是直接判定"没有可用实例"就新建。

改动文件：`.claude/skills/browser-cdp/browser_launch.py`（新增 `_read_profile_lock()` /
`_write_profile_lock()`，`cmd_dedicated()` 里接入）。

## 未改动但记录在案的部分（不是本次问题的根因，先排除掉）

- `is_debug_port_alive()` 的探测方式（直接 HTTP GET `/json/version`，1 秒超时）审查后判断
  没有问题，本地回环请求正常情况下远快于 1 秒，不是导致误判的原因。
- `--ensure`（attach 场景，默认端口 9222）设计上就是假设用户已经手动开了一个带调试端口的
  浏览器，探测失败时给出的指引信息是对的；这次的知乎 workflow 已经改为统一走 `--dedicated`
  （见 SKILL.md 的醒目提示），不依赖用户手动操作这一步，从使用方式上绕开了这个场景的固有限制。

## 验收方式

1. 手动跑 `python browser_launch.py --dedicated --name zhihu_session`，在弹出的窗口里登录
   一次知乎；关闭该浏览器进程（不通过 `--stop-dedicated`，模拟异常退出）。
2. 清空/删除 `~/.cdp_skill/registry.json` 里对应 `zhihu_session` 的条目（模拟 registry 状态
   丢失），保留 profile 目录。
3. 再次 `python browser_launch.py --dedicated --name zhihu_session` 且浏览器进程恰好还活着时，
   验证能通过锁文件正确探测到并复用，而不是新建一个。
4. 完整重跑：`--dedicated --name zhihu_session` → 关闭 → 重新 `--dedicated --name zhihu_session`，
   验证知乎登录状态被保留（不需要重新登录）。
