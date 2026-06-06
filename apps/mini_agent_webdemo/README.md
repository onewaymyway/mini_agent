# Mini Agent Web Demo

基于 Streamlit 的 mini-agent HTTP 服务 Web 交互界面。

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Demo
streamlit run app.py
```

默认访问: http://localhost:8501

## 使用说明

### 1. 连接配置

在左侧栏填写：
- **API 地址**: mini-agent HTTP 服务地址，默认 `http://127.0.0.1:8765/v1`
- **Bearer Token**: 认证令牌

**Token 两种获取方式**：
- 手动输入：直接粘贴 token 字符串
- 从文件读取：自动检测或手动指定 `agent_api.key` 路径（支持 `--key-file` 参数配置）

点击 **🔌 连接** 按钮建立连接。

### 2. 对话交互

- 在底部文本框输入消息，点击 **📨 发送**
- 支持多轮对话，自动等待 Agent 完成后展示回复
- 点击 **📋 同步历史** 从服务端拉取完整对话历史
- 点击 **📊 查看 Turns** 查看所有 turn 记录和状态

### 3. 实时事件流

右侧面板展示 Agent 运行时的实时事件：
- 🔧 工具调用（tool_call）
- ✅ 工具结果（tool_result）
- ▶ Turn 开始/结束
- ⚠️ 权限请求
- ❌ 错误信息

支持按事件类型过滤，点击 **🔔 拉取事件** 获取最新事件。

### 4. 权限审批

当 Agent 需要执行高权限工具时，页面顶部会显示审批卡片：
- 查看工具名称、描述和参数
- 点击 **✅ 批准** 或 **❌ 拒绝**

### 5. 文件系统浏览

开启左侧栏「文件系统」开关，可以：
- 浏览目录
- 查看文件内容
- 导航到子目录

### 6. 中断执行

点击 **⏹ 中断执行** 可以立即中止 Agent 当前正在执行的任务。

## 命令行参数（启动 mini-agent 服务端）

```bash
# 启动 HTTP 服务
python -m mini_agent --http

# 指定 Token
python -m mini_agent --http --http-token my-secret-token

# 自定义端口
python -m mini_agent --http --http-port 8765

# Token 会自动写入 agent_api.key 文件
```

## 架构说明

```
Streamlit App (浏览器)
    │
    ├── REST API  →  /v1/chat, /v1/status, /v1/history ...
    │
    ├── SSE 轮询  →  /v1/events (轮询方式获取实时事件)
    │
    └── 权限审批  →  /v1/permissions/pending + POST
         │
         └── mini-agent HTTP Server (FastAPI)
                  │
                  └── Agent Core
```
