# browser-cdp 稳定性修复说明（修正版）

状态：已落地
关联：next_doc/workflow_python_step_and_zhihu_publish_plan.md §E

## 更正说明

这份文档的第一版把"登录态丢失"的根因归结为"专用实例 profile 目录放在项目内 `temp/`
下，容易被清理脚本清空"，并把默认目录改到了 `~/.cdp_skill/profiles/`。经过进一步排查，
**这个判断是错的**，已经把目录改回项目本地的 `temp_cdp/cdp_brower_data/`（没有用回原来
完全一样的 `temp/cdp_brower_data`，是为了在措辞上和"随意可丢弃"的临时目录区分开，但不再
主张"目录被清空"是登录态丢失的实际原因）。真正的根因见下面问题 1。

## 问题 1（修正后的真正根因）：重启后登录状态"看起来"丢了

**根因**：Chrome 进程如果是被强制结束的（`taskkill /F`、进程被杀、崩溃），会在
`user-data-dir` 下留下 `SingletonLock`/`SingletonSocket`/`SingletonCookie` 这几个单实例
锁文件，正常退出时这些文件会被 Chrome 自己清理掉，但异常退出时不会。下次用同一个
`user-data-dir` 再启动时，如果这些锁文件还在，Chrome 会认为"已经有另一个实例在用这个
profile"，从而**不会正常加载这个 profile 里的 cookies/session**——不是任何文件被删除，
profile 目录和里面的登录数据其实完好无损，只是这次启动没有真正用上它，表现出来就是
"重启后登录状态丢了"。

`browser_launch.py::spawn_browser()`（`--dedicated`/`--ensure --spawn` 两条路径共用）一直
都有清理这几个锁文件的逻辑（`_remove_singleton_locks()`），这条路径本身没问题。**遗漏在
另一个独立脚本 `launch_zhihu_logged_in.py`**——它有自己的一套启动逻辑（固定端口 9336、
固定 profile `temp_data/zhihu_logged_in_profile/`），拉起新 Chrome 进程之前完全没有做锁
文件清理，是这次登录态丢失问题的真正来源。

**修复**：给 `launch_zhihu_logged_in.py` 补上同样的锁文件清理逻辑
（`_remove_stale_singleton_locks()`），在真正要拉起新进程的分支（`port_in_use=False`）
调用一次，只删 `SingletonLock`/`SingletonSocket`/`SingletonCookie` 这三个锁文件，不动
cookies/sessions 等真实登录数据。

改动文件：`.claude/skills/browser-cdp/launch_zhihu_logged_in.py`。

**排查过程中顺带排除的一个疑点**：一开始怀疑 `launch_zhihu_logged_in.py`
（默认端口 9336）和 `zhihu_search_with_login.py`（起初以为默认走 `cdp_client.DEFAULT_PORT`
即 9222）用的端口不一致，导致两个脚本实际连到了两个不同的浏览器实例。核实代码后发现
`zhihu_search_with_login.py` 内部把 `DEFAULT_PORT` 在文件顶部重新定义成了 `9336`
（覆盖了从 `cdp_client` 可能拿到的 9222），和 `launch_zhihu_logged_in.py`/
`run_zhihu_search_auto.py` 是一致的，这条不是问题，排除。

## 问题 2：不能正确识别已经启动的调试浏览器

代码审查结论：`browser_launch.py::cmd_dedicated()` 判断"是否已有可用实例"这部分逻辑本身是
对的——`is_debug_port_alive()` 真实发 HTTP 请求探测 `/json/version`，不是只看内存/registry
记录，判断优先级也已经是"真实探测优先于 registry 状态"。但存在一个健壮性缺口：
**registry.json 是唯一的状态来源，它本身没有并发保护**，如果被其它调用（比如
`--stop-dedicated`）改动、或者文件本身损坏/被清空，即使浏览器进程和 profile 都还在，也会
因为 registry 里查不到条目而走到"新建一个"的分支，造成同一个 `--name` 下出现两个互不相关
的浏览器实例。

**修复**：新增 profile 目录下的锁文件 `<profile_dir>/.mini_agent_lock.json`（记录
port/pid/启动时间），作为 registry 之外的第二条线索。`cmd_dedicated()` 在 registry 查不到
对应 `--name` 的条目时，会先尝试读这个锁文件拿到端口号去做真实探测，探测成功就直接复用并
把信息回填进 registry，而不是直接判定"没有可用实例"就新建。这一条和"锁文件被误伤"的判断
互不冲突：`_read_profile_lock()`/`_write_profile_lock()` 是全新的独立文件，不受
`SingletonLock` 清理逻辑影响。

改动文件：`.claude/skills/browser-cdp/browser_launch.py`（新增 `_read_profile_lock()` /
`_write_profile_lock()`，`cmd_dedicated()` 里接入）。

## 使用侧同样要注意

即使锁文件问题修好了，如果每次调用 `--dedicated` 时传的 `--name` 不固定（比如每次让 agent
临时想一个名字，或者不同 step 用了不同名字），同样会造成"看起来登录状态丢了"——因为不同
name 对应不同 profile 目录，本来就是两个完全独立的浏览器，不是 bug。`SKILL.md` 已经加了
醒目提示：同一个任务/workflow 内所有涉及浏览器的 step 必须用同一个固定的 `--name`（本次
知乎发布 workflow 统一用 `zhihu_session`，见
`.agent/workflows/zhihu_content_publish/prompts/02_search_zhihu.md` /
`prompts/04_enrich_questions.md`）。

## 未改动但记录在案的部分（排查过程中确认不是根因）

- `is_debug_port_alive()` 的探测方式（直接 HTTP GET `/json/version`，1 秒超时）审查后判断
  没有问题，本地回环请求正常情况下远快于 1 秒，不是导致误判的原因。
- `--ensure`（attach 场景，默认端口 9222）设计上就是假设用户已经手动开了一个带调试端口的
  浏览器，探测失败时给出的指引信息是对的；这次的知乎 workflow 已经改为统一走 `--dedicated`
  （见 SKILL.md 的醒目提示），不依赖用户手动操作这一步，从使用方式上绕开了这个场景的固有
  限制。
- 专用实例 profile 目录的存放位置（`temp_cdp/cdp_brower_data/`）不是登录态丢失的根因，
  见文档开头的更正说明。

## 问题 3（用户后续反馈追加）：应该有"先检测是否已启动，启动了就不再新建"的通用机制

在问题 1/2 修好之后，用户进一步指出：`--ensure`/`--dedicated` 原有的检测都局限在"本技能自己
知道的范围"——`--ensure` 只看固定端口（默认 9222）通不通，`--dedicated` 只看 registry/锁文件
里记录过的实例。如果调试浏览器是用户手动开的、是之前某次会话遗留的、或者根本不是本技能启动
的，只要端口/名字对不上，就会被误判成"没有可用实例"，从而不必要地又启动一个新的。

**修复**：新增 `find_running_debug_chrome_ports()`——扫描系统进程列表（Linux/mac 用
`ps -eo command`，Windows 用 `wmic process ... get CommandLine`），找出所有携带
`--remote-debugging-port=NNNN` 参数的 chrome/chromium/edge 进程，提取端口号后逐个用
`is_debug_port_alive()` 做真实探测确认。

- `cmd_ensure()`：请求的端口不通时，先用这个扫描兜底，找到任何一个真实存活的调试浏览器就
  直接复用（打印出实际应该用的端口），不再执行"报错退出"或"新建一个"。
- 新增 `--list-running` 诊断命令：单独列出当前系统里检测到的所有调试浏览器（端口+存活状态
  +浏览器版本信息），方便人工排查。
- `--dedicated` **有意不接入**这个系统级扫描：它的语义是"固定 `--name` 对应固定 profile"，
  如果为了"复用一个已有浏览器"而把一个跟目标 profile/登录态完全无关的浏览器实例安到某个
  `--name` 上，会制造出更隐蔽的问题（比如以为是 `zhihu_session` 但其实连到的是另一个跟知乎
  毫不相关的浏览器）。`--dedicated` 的"识别已有实例"仍然只信任 registry.json 和自己写的
  `.mini_agent_lock.json`。

改动文件：`.claude/skills/browser-cdp/browser_launch.py`（新增
`find_running_debug_chrome_ports()`/`_extract_debug_ports_from_cmdlines()`/
`cmd_list_running()`，`cmd_ensure()` 里接入，新增 `--list-running` CLI 参数）。

单测：`tests/test_browser_cdp_detect_running.py`（纯字符串解析逻辑
`_extract_debug_ports_from_cmdlines()` 的 5 个用例 + 1 个"真实扫描不抛异常"的冒烟测试）。

## 验收方式

1. 用 `python launch_zhihu_logged_in.py` 启动浏览器并登录一次知乎。
2. 用 `taskkill /F`（或 Linux/mac 下 `kill -9`）强制结束该 Chrome 进程，模拟异常退出
   （不通过正常关闭窗口，这样才会残留 `SingletonLock` 等锁文件）。
3. 再次运行 `python launch_zhihu_logged_in.py`，验证知乎登录状态被正确保留（不需要重新
   登录）——修复前，这一步在锁文件残留的情况下会表现为"登录态丢了"。
4. `browser_launch.py --dedicated --name zhihu_session` 的验收方式：起一个实例并登录 →
   `kill -9` 强制结束 → 删除/清空 `~/.cdp_skill/registry.json` 里对应条目（模拟 registry
   状态丢失，保留 profile 目录）→ 重新 `--dedicated --name zhihu_session`，验证能通过
   `.mini_agent_lock.json` 正确探测复用，而不是新建一个。
5. 手动用 `chrome --remote-debugging-port=9400`（一个跟本技能默认端口都不同的、"外部"启动
   的调试浏览器）开一个窗口，然后跑 `python browser_launch.py --ensure`（默认请求 9222，
   探测不到），验证会自动扫描到 9400 并直接复用，而不是报错或新建。
