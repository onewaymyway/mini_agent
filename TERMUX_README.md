# Mini Agent 在 Termux 上运行指南

本文介绍如何在 Android 的 Termux 环境下运行 Mini Agent，并提供完整的环境搭建、运行方式以及稳定性优化方案。

---

# 一、整体架构

Mini Agent 在 Android 上的运行结构如下：

```
Android
  │
Termux
  │
proot Debian
  │
Python 3.11.9（源码编译）
  │
Mini Agent
```

推荐设备：

- Android 10+
- RAM ≥ 6GB（最低 4GB 可运行但不稳定）
- 存储 ≥ 10GB

---

# 二、Termux 安装

建议从官方渠道安装最新版 Termux：

https://github.com/termux/termux-app/releases

安装后执行基础更新：

```bash
pkg update
pkg upgrade
```

安装基础工具：

```bash
pkg install git wget curl vim unzip
```

---

# 三、Termux:API（可选但强烈推荐）

用于让 Mini Agent 控制 Android 能力：

- TTS 语音播报
- 震动
- 通知
- 剪贴板
- 电池状态
- 系统信息

## 1. 安装 App

https://github.com/termux/termux-api/releases

或 F-Droid：

https://f-droid.org/packages/com.termux.api/

## 2. 安装 CLI 工具

```bash
pkg install termux-api
```

测试：

```bash
termux-tts-speak "Hello Mini Agent"
termux-vibrate
```

---

# 四、安装 Debian（proot）

```bash
pkg install proot-distro
```

安装 Debian：

```bash
proot-distro install debian
```

进入 Debian：

```bash
proot-distro login debian
```

---

# 五、安装 Python 3.11.9（源码编译）

## 1. 下载源码

```bash
wget https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tar.xz
tar -xf Python-3.11.9.tar.xz
cd Python-3.11.9
```

## 2. 安装依赖

```bash
apt update

apt install -y \
build-essential \
libssl-dev \
zlib1g-dev \
libbz2-dev \
libreadline-dev \
libsqlite3-dev \
libffi-dev \
liblzma-dev \
tk-dev
```

## 3. 编译安装

```bash
export MAKEFLAGS="-j$(nproc)"

./configure --enable-optimizations

make -j$(nproc)

make install
```

验证：

```bash
python3.11 --version
```

---

# 六、创建 Python 环境

进入 root：

```bash
cd /root
```

创建虚拟环境：

```bash
python3.11 -m venv venv
```

激活环境：

```bash
export PATH="$HOME/.pyenv/bin:$PATH"
source venv/bin/activate
```

---

# 七、下载 Mini Agent

```bash
git clone https://github.com/onewaymyway/mini_agent.git
cd mini_agent
```

---

# 八、安装依赖

```bash
pip install -r requirements.txt
```

---

# 九、配置 LLM Provider

复制配置文件：

```bash
cp providers.json.agnes.example providers.json
```

编辑：

```bash
vim providers.json
```

填写 API Key：

Agnes 官网（免费模型）：

https://agnes-ai.com/

---

# 十、启动 Mini Agent

```bash
python main.py \
  --debug-llm \
  --reminder-verbose \
  --raw-output
```

---

# 十一、Termux 最佳实践（稳定运行关键）

## 1. 防止 CPU 休眠

```bash
termux-wake-lock
```

关闭：

```bash
termux-wake-unlock
```

---

## 2. 使用 tmux（防止断开）

安装：

```bash
pkg install tmux
```

启动：

```bash
tmux
```

进入 Debian 后运行 Agent。

退出但不停止：

```
Ctrl + B  →  D
```

恢复：

```bash
tmux attach
```

---

## 3. 防止 Android 杀后台（非常重要）

不同品牌手机策略不同，建议全部设置：

### （1）关闭电池优化

设置路径：

```
设置 → 电池 → 电池优化 → Termux → 不优化
```

---

### （2）允许后台运行

```
设置 → 应用 → Termux
```

开启：

- 后台运行
- 自启动（如有）
- 不限制电池

---

### （3）锁定后台

打开最近任务：

- 长按 Termux
- 点击 🔒 锁定

---

### （4）关闭省电模式

关闭：

- 超级省电
- 智能省电

---

## 4. 文件访问

初始化：

```bash
termux-setup-storage
```

路径：

```
~/storage/shared/
```

对应手机：

```
/storage/emulated/0/
```

---

## 5. 网络稳定性

建议：

- Wi-Fi 常开
- VPN/代理允许后台运行
- 避免频繁切换网络

---

## 6. 推荐启动方式

```bash
termux-wake-lock

tmux

proot-distro login debian

source ~/venv/bin/activate

cd mini_agent

python main.py --debug-llm --reminder-verbose --raw-output
```

---

# 十二、推荐软件

| 软件 | 用途 |
|------|------|
| Termux | Linux 环境 |
| Termux:API | Android 控制能力 |
| F-Droid | 安装最新版工具 |
| ACode | 手机代码编辑 |
| tmux | 后台运行 |
| Git | 代码管理 |
| Clash / v2rayNG | 网络代理 |

---

## 1. ACode

推荐用于：

- 编辑 mini_agent 代码
- Git 操作
- 项目管理

路径：

```
/storage/emulated/0/projects/mini_agent
```

---

## 2. Git

```bash
git clone https://github.com/onewaymyway/mini_agent.git
git pull
```

---

## 3. 目录结构建议

推荐统一项目路径：

```
/storage/emulated/0/projects/mini_agent
```

Debian 内访问：

```
/storage/emulated/0/projects/mini_agent
```

Python venv：

```
/root/venv
```

---

# 十三、常见问题

## 1. signal 9（进程被杀）

原因：

- Android 回收后台进程

解决：

- 关闭电池优化
- 使用 tmux
- 开启 wake-lock
- 锁定后台

---

## 2. 熄屏后断网

- VPN 被杀
- Wi-Fi 省电策略

---

## 3. Python 版本错误

```bash
python --version
```

确认是否为 3.11.9

---

## 4. providers.json 不生效

确认文件名：

```
providers.json
```

---

# 十四、推荐开发流程

日常启动：

```bash
termux-wake-lock
tmux
proot-distro login debian
source ~/venv/bin/activate
cd /storage/emulated/0/projects/mini_agent
python main.py --debug-llm --reminder-verbose --raw-output
```

退出：

```
Ctrl + B → D
```

---

# 十五、总结

通过 Termux + Debian + Python 3.11 + Mini Agent，可以在 Android 手机上构建一个：

> 可移动、可语音、可联网、可自动化执行任务的个人 AI Agent 系统

它具备以下特点：

- 随身运行
- 低成本部署
- 支持 Android 原生能力
- 可长期后台运行
- 可扩展为自动化系统 / 智能助手 / 多模态 Agent

---