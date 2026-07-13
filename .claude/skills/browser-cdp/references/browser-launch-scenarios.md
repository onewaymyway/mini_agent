# 确保有可连接的浏览器：三种场景详解

## 场景 A（推荐用于"后续一系列自动化操作"）：打开一个专门的新 Chrome 实例

不依赖用户手动改快捷方式，直接由 Agent 拉起一个**独立的、专门供后续操作使用**的 Chrome：
独立 profile（不碰用户真实登录态）、独立调试端口（默认 9333，不与场景 B 的 9222 冲突）、
默认可见窗口（也可以 `--headless` 用于服务器场景），并会把实例信息记到本地注册表，
之后脚本随时用同一个 `--port` 复用它，不用每次重新启动。

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
# 输出里会给出 port 和首个 tab id，例如 --port 9333 --tab <id>
python browser_nav.py --port 9333 --tab <id> --goto "https://example.com"
```

常用管理命令：
```bash
python browser_launch.py --list-dedicated          # 查看已创建的专用实例（含是否存活）
python browser_launch.py --stop-dedicated work     # 用完关闭并从注册表移除
```

同一次任务里可以按需要开多个（用不同 --name），比如一个用来登录A站点、一个用来查B站点，互不干扰。
默认可见（非 headless），方便用户随时瞄一眼 Agent 在干什么；纯后台抓取不需要用户看时加 `--headless`。

## 场景 B：连接用户本机正在用的浏览器窗口（共享登录态）

Chrome 不允许对一个"已经在跑、没开调试端口"的实例远程接管，所以需要用户重新用调试端口打开一次：

1. 完全退出 Chrome（包括后台/托盘图标）
2. 用户运行（或让用户创建一个桌面快捷方式，目标改成下面这样）：
   ```
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
   ```
   这样打开的还是用户的默认 profile（保留登录态、书签等），只是多开了调试端口。
3. 之后正常用 `browser_launch.py --list` 之类命令连接 `127.0.0.1:9222` 即可。

告知用户：调试端口只监听本机 127.0.0.1，不会被外部网络访问，但仍建议用完后关闭该模式。

## 场景 C：无 GUI 服务器/沙盒环境，只做抓取，不需要可见窗口

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --headless          # 等价于场景A但不弹窗口
# 或临时用一次不留注册记录：
python browser_launch.py --ensure --spawn --headless
```

会自动探测 Chrome/Chromium 可执行文件。第一次探测失败时会提示用 `--binary` 指定路径。

## 检查/复用已有连接

```bash
python browser_launch.py --ensure     # 端口已通（默认9222）-> 直接打印版本信息；不通 -> 报错并给出上面几种指引
python browser_launch.py --list --port 9333   # 列出指定端口下的所有 tab，拿到 --tab 用的 id
```

## 启动失败/进程清理策略

- `--dedicated`/`--ensure --spawn` 启动失败（调试端口超时未就绪）时，脚本**只会杀掉本次自己刚拉起的
  那一个进程**（`Popen` 返回的 pid），绝不会去扫描或杀死任何其他 Chrome/Edge 进程，不会影响用户
  已经在用的浏览器窗口。
- 若某个 `--name` 对应的专用实例此前异常退出（进程崩了但没走 `--stop-dedicated` 清理），下次
  `--dedicated --name <同名>` 会先检查 registry 里记录的那个旧 pid 是否还活着——**只处理这一个
  被本技能记录过的 pid**，健在则先关闭它，再清理 profile 目录里的单例锁文件，然后才重新启动，
  避免"新旧两个进程抢同一个 profile 目录，实际生效的是旧进程"这种状态不一致问题。
- `--dedicated` 启动成功后不会只凭"调试端口通了"就报告成功，而是会**真正连上第一个 tab、
  轮询读取 `document.readyState/location.href/document.title`**，直到页面 `complete` 或超时，
  把读到的真实状态打印出来。判断"网页是否打开成功"应该看这行 `当前页面: url=... readyState=...`，
  而不是只看进程有没有报错。
