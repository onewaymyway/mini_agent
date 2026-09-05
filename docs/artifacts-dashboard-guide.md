# 产出物看板指南（Artifacts Dashboard）

> 面向"命令行不便展示的产出"（文档、图片等）的语义化查看方案。
> 涉及文件：`storage/paths.py`、`storage/artifacts.py`、
> `perception/artifact_detector.py`、`api/routes.py`（`/v1/artifacts/*`）、
> `apps/mini_agent_kanban/{app.py,client.py}`。

## 1. 背景与目标

命令行 REPL 对文档（docx/pptx/xlsx/pdf）、图片这类产出天生不友好——只能打印一个
文件路径，用户体验差。产出物看板要解决的问题：

1. 有一种**通用、与具体工具实现解耦**的方式登记"这次任务产出了什么"；
2. 看板能**语义化地**渲染这些产出（图片内联展示、文档给下载/预览），而不是
   靠遍历目录猜测哪个文件是"这次的产出"；
3. 能**直接给一个链接**跳转到看板查看某次产出，或查看某个 session 的所有产出。

与 Kanban 看板已有的「📁 产出物」Tab（基于 `/v1/fs/*` 遍历 `.agent/` 目录）不是一回事：
那个 Tab 是通用文件浏览器；本方案是**按任务/session 索引的产出物清单**，两者并存、
互不影响。

## 2. 核心概念：产出物 Manifest

每次需要展示产出时，登记一份 JSON 清单（manifest），看板只消费这份清单。

**存储位置**（`storage/paths.py` 新增的 `AgentPaths` 方法）：

```
<project_root>/.agent/sessions/<session_id>/artifacts/
    manifest_<manifest_id>.json    # 每次产出一个文件
<project_root>/.agent/artifacts_index.jsonl   # 全局索引，每行一条摘要（追加写）
```

- `session_artifacts_dir(session_id)` — 定位某 session 的 manifest 目录
- `artifacts_index()` — 定位全局索引文件

**Manifest 字段**（`storage/artifacts.py` 的 `ArtifactManifest`/`ArtifactFile`）：

```json
{
  "manifest_id": "20260706_153000_report_a1b2c3",
  "session_id": "sess_abc123",
  "user_id": "otz",
  "created_at": "2026-07-06T15:30:00+08:00",
  "title": "季度报告生成",
  "description": "根据销售数据生成的 Word 报告",
  "source": {"tool": "write_file", "auto_detected": true},
  "files": [
    {
      "path": "/abs/path/report.docx",
      "type": "document",
      "title": "季度报告.docx",
      "mime": null,
      "size": 20480,
      "preview": "auto"
    },
    {"path": "/abs/path/chart.png", "type": "image", "title": "销量趋势图", ...}
  ]
}
```

`type` 决定看板的渲染方式：`image` / `document` / `pdf` / `code` / `text` / `other`。
未显式指定时按文件后缀自动推断（见 `artifacts.py` 的 `_EXT_TYPE_MAP`）。

## 3. 登记方式

### 3.1 手动登记

任意工具/Agent 代码里调用：

```python
from mini_agent.storage.paths import AgentPaths
from mini_agent.storage.artifacts import record_artifact

paths = AgentPaths(project_root)
record_artifact(
    paths,
    session_id="sess_abc123",
    title="季度报告生成",
    files=["report.docx", {"path": "chart.png", "title": "销量趋势图"}],
    description="根据销售数据生成的 Word 报告",
    source={"tool": "my_custom_tool"},
)
```

`files` 支持三种写法（可混用）：字符串路径 / `{"path":..., "type":..., "title":...}`
字典 / `ArtifactFile` 实例。

### 3.2 自动侦测（默认关闭）

`write_file` / `create_file` / `patch_file` / `patch_file_simple` / `bash` 等工具
**成功执行后**，`ToolExecutor` 会调用 `perception/artifact_detector.py` 里的
`maybe_record_artifact()`：

- **有明确 `path` 参数的工具**（write_file 等）：直接检查该路径后缀是否属于
  image/document/pdf 三类（不含 code/text——这两类太常见，逐个登记会把看板刷成
  噪音，且本来就能在终端直接看）。
- **bash**：命令是黑盒，从命令字符串 + 输出文本里正则提取形如 `xxx.docx` /
  `xxx.png` 的路径 token，并要求文件 mtime 在最近 30 秒内才算"新产出"（避免
  命令里顺带提到的历史文件被误判）。
- 同一 `(路径, mtime)` 只登记一次（`ArtifactAutoDetector` 内部去重），防止重复
  读写同一文件时反复生成 manifest。
- 任何异常都静默吞掉、不影响工具调用主流程——产出登记是锦上添花，不应该因为
  它失败就让工具调用报错。

**开关**：`PerceptionConfig.artifact_auto_detect_enabled`，默认 `False`。

```json
// agent_config.json
{
  "artifact_auto_detect_enabled": true
}
```

或代码里：

```python
from mini_agent.config.loader import load_config
cfg = load_config(artifact_auto_detect_enabled=True)
```

**为什么默认关闭**：涉及对 bash 命令/输出做正则扫描 + 额外的文件系统 `stat()`
调用，且启发式扫描理论上有漏检/极小概率误检的可能，需要用户显式选择开启。
如果只想用手动登记（3.1），完全不需要打开这个开关。

**局限性**：
- 只覆盖内建的 `write_file`/`create_file`/`patch_file(_simple)`/`bash` 四个工具；
  自定义/MCP 工具若也会产出文件，需要在 `artifact_detector.py` 的
  `_PATH_ARG_TOOLS` 集合里加上工具名（如果是有明确 `path` 参数的工具），或者
  按 bash 的模式扩展正则扫描。
- bash 场景的正则提取是启发式的：命令里用变量拼接的路径（如
  `python gen.py --out $OUT_FILE`）识别不到；反过来，如果命令字符串里凑巧提到
  一个刚好在最近 30 秒内被别的进程修改过的同名文件，也可能被误判为本次产出。
  如果后续发现命中率不理想，可以考虑限定只在项目内某些"产出目录"（如
  `outputs/`）下才自动侦测，进一步降噪。

## 4. HTTP API

```
GET  /v1/artifacts                     列出产出物摘要（?session_id=&limit=&offset=）
GET  /v1/artifacts/{manifest_id}       获取单个 manifest 详情（含文件明细）
GET  /v1/artifacts/{manifest_id}/file  取 manifest 内某个文件（?index=0，?download=true 走附件下载）
```

详见 [HTTP API 指南](http-api-guide.md) 的"产出物 Artifacts"一节。

`manifest_id/file` 端点只接受 manifest 里登记过的路径（不接受调用方传入任意
路径），避免任意文件读取。

## 5. 看板：产出预览 Tab

Kanban 看板（`apps/mini_agent_kanban/app.py`）新增「🖼️ 产出预览」Tab：

- 可按 `session_id` 过滤，留空看全部产出，按时间倒序展示。
- 每条产出可展开查看文件明细：
  - `image` → `st.image()` 内联展示
  - `pdf` → 提供"新标签页打开预览"+"下载"链接
  - `code` / `text` → `st.code()` 内联预览（通过 `/v1/fs/read` 取内容）+ 下载链接
  - `document`（docx/pptx/xlsx 等）→ 提供下载链接（这几类无法在浏览器内简单预览）
- **深链接**：看板启动时通过 `st.query_params` 读取 URL 参数：
  - `?manifest_id=xxx` → 自动展开对应的产出详情
  - `?session_id=xxx` → 自动填入 session 过滤框
  - 每条产出详情下方也提供"🔗 分享链接参数"文本，方便复制拼接。

对应封装在 `apps/mini_agent_kanban/client.py`：`list_artifacts()` /
`get_artifact()` / `artifact_file_url()`。

详见 [Kanban 看板使用指南](kanban-dashboard-guide.md) 的"🖼️ 产出预览 Tab"一节。

**「💬 对话」Tab 里也能直接看/下载**：`render_chat_tab` 新增了共用函数
`_render_session_artifacts_block`（`app.py`），在两处渲染当前 session 的
产出物列表——① 对话消息流末尾内联展示（新增条目默认展开）；② 右侧栏
"事件流"上方的独立"📦 本次对话产出物"面板（始终可展开，不需要等新条目
或滚动对话）。两处都复用 §5 提到的 `_render_artifact_file` 做单文件预览/
下载，无需跳转到「产出预览」Tab。详见
[Kanban 看板使用指南](kanban-dashboard-guide.md) 的"💬 对话 Tab"一节。

## 5.1 新版看板（React，`mini_agent_kanban_x`）：对话页内联 + 独立面板

新版 React 看板的「💬 对话」页（`apps/mini_agent_kanban_x/src/pages/Chat/index.tsx`）
把产出物直接接入了对话本身，不需要跳到「产出物」全局浏览页：

- **对话流内联**：消息列表末尾（滚动区域底部，输入框上方）展示
  「📦 本次会话产出物」，随最新一条消息一起可见——一轮工具调用结束
  （`turn_end`）后会自动 invalidate 对应 query 并刷新。
- **独立面板**：右侧栏「事件流」上方新增「📦 本次对话产出物」卡片，
  始终列出当前 session 的全部产出物，不随聊天区域滚动，可以在阅读
  消息的同时随时展开查看。
- 两处复用同一个组件 `components/ChatArtifactsPanel.tsx`：先拉
  `GET /v1/artifacts?session_id=xxx` 的摘要列表（`useArtifactsList`），
  点开某一条才用 `useArtifactDetail` 懒加载该 manifest 的文件明细，
  避免打开对话页时对着每条产出都发一次详情请求。
- 文件预览/下载逻辑抽成 `components/ArtifactPreview.tsx`（`ArtifactFileRow`
  等），与「产出物」全局浏览页（`pages/Artifacts/index.tsx`）共用同一套
  渲染规则：`image` 内联缩略图 + 下载原图链接，其它类型给"下载 / 查看"
  链接。

> **顺带修复**：`pages/Artifacts/index.tsx` 之前假设列表接口返回
> `{manifests: [...]}`、文件对象有 `name` 字段——但后端 `GET /v1/artifacts`
> 实际返回 `{items: [...], count}`（摘要用 `title`/`file_count`/`types`，
> 见 `storage/artifacts.py::ArtifactManifest.to_summary()`），文件对象
> 也只有 `path`/`title`/`type` 没有 `name`。这次一并改正了 `api/types.ts`
> 里的 `ArtifactManifest`/`ArtifactsListResponse` 类型定义和该页面的渲染
> 逻辑。

## 6. 后续可扩展方向（未实现）

- `PostToolUse` 用户级 hooks（`hooks.md` 描述的外部 shell 钩子）里也调用一次
  自动侦测，覆盖非内建工具。
- CLI `/artifacts` 命令：列出当前 session 的产出并直接打印看板深链接。
- 图片/文档缩略图预生成，避免看板每次都拉取原图。
- bash 场景改为白名单目录侦测（如只在 `outputs/` 下扫描）以进一步降噪。

## 相关文档

- [Kanban 看板使用指南](kanban-dashboard-guide.md)
- [HTTP API 指南](http-api-guide.md)
- [配置指南](config-guide.md) — `PerceptionConfig.artifact_auto_detect_enabled`

---

*最后更新：2026-09*
