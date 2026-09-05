---
name: gen_video_with_text
description: 要用文字/首尾帧/参考图片或音频生成视频时，使用本 skill。基于 Agnes Video 2.5 Flash（agnes-video-2.5-flash）异步任务接口，支持 prompt 字符串或 prompt_file 文件两种输入方式。
triggers: 视频生成，生成视频，gen_video, prompt_file, 文生视频, 首尾帧, keyframe, reference
---

# Generate Video with Text

此 skill 允许你使用 `agnes_tools.py` 中的 `AgnesVideoClient` 调用 **Agnes Video 2.5 Flash**（`agnes-video-2.5-flash`）模型，根据文本描述、首尾帧或参考图片/音频生成视频。

Agnes Video 2.5 Flash 是**异步任务接口**：先 `POST /v1/videos` 创建任务拿到 `video_id`，再轮询 `GET /agnesapi?video_id=...&model_name=agnes-video-2.5-flash` 直到 `status` 变为 `completed` 或 `failed`。`gen_video.py` / `AgnesVideoClient.generate_video()` 已经封装好了创建 + 轮询 + 下载的完整流程。

## 快速开始

```bash
# 设置 API 密钥
export AGNES_API_KEY="your-api-key"          # Linux/Mac
$env:AGNES_API_KEY="your-api-key"            # Windows PowerShell

# 文生视频
python .claude/skills/gen_video_with_text/gen_video.py text "雨后的未来城市街道，霓虹灯倒映在地面，一辆银色跑车缓慢驶过，电影级运镜" --save-path output.mp4
```

## 三种生成模式

| `mode` | 用途 | 必需参数 | 说明 |
|--------|------|----------|------|
| `text` | 纯文本生成视频 | `prompt` | 不能传首尾帧/参考图片/音频 |
| `keyframe` | 首帧、尾帧或首尾帧控制 | `first_frame` 和/或 `last_frame` 至少一个 | 不能传 `images`/`audios` |
| `reference` | 图片或音频参考生成 | `images` 或 `audios` 至少一类非空 | 不能传首尾帧；Flash 最多 5 张图片、3 段音频 |

## 两种 Prompt 输入方式

`gen_video()` 函数支持两种 prompt 输入方式，**二者只需提供其一**：

| 方式 | 参数 | 适用场景 |
|------|------|----------|
| **直接传入字符串** | `prompt="描述文字"` | 简短 prompt，直接在代码或命令行中使用 |
| **从文件读取** | `prompt_file="prompt.txt"` | 长 prompt、需要版本管理、或批量生成时使用 |

> **注意**：`prompt` 和 `--prompt-file` 互斥，不能同时指定。如果两者都为空，会返回错误。

### 函数调用示例

```python
from gen_video import gen_video

# 方式一：文生视频，直接传入 prompt 字符串
result = gen_video(prompt="夜晚森林中三只猫组成微型铜管乐队向前行进，镜头平稳后退", mode="text", save_path="output.mp4")

# 方式二：从文件读取 prompt
result = gen_video(prompt_file="prompts/my_prompt.txt", mode="text", save_path="output.mp4")

# 首尾帧控制
result = gen_video(
    prompt="人物从首帧姿态自然转身走向窗边，镜头缓慢推进并平滑过渡到尾帧",
    mode="keyframe",
    first_frame="https://example.com/first.png",
    last_frame="https://example.com/last.png",
    save_path="output.mp4",
)

# 图片参考生成
result = gen_video(
    prompt="以 <Picture 1> 中的角色和美术风格为参考，角色在花田中自然奔跑，保持外观一致",
    mode="reference",
    images=["https://example.com/character.png"],
    save_path="output.mp4",
)

# 音频参考生成
result = gen_video(
    prompt="以 <Audio 1> 的节奏和环境氛围作为参考，生成电影感夜间驾驶画面",
    mode="reference",
    audios=["https://example.com/reference-audio.mp3"],
    save_path="output.mp4",
)
```

### 命令行示例

```bash
# 文生视频
python gen_video.py text "prompt" --save-path output.mp4

# 从文件读取 prompt
python gen_video.py text --prompt-file prompts/my_prompt.txt --save-path output.mp4

# 首尾帧控制
python gen_video.py keyframe "prompt" --first-frame https://example.com/first.png --last-frame https://example.com/last.png --save-path output.mp4

# 图片参考（最多 5 张）
python gen_video.py reference "prompt" --images https://example.com/a.png https://example.com/b.png --save-path output.mp4

# 音频参考（最多 3 段）
python gen_video.py reference "prompt" --audios https://example.com/a.mp3 --save-path output.mp4
```

## 可用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `mode` (位置参数) | `text` / `keyframe` / `reference` | 必填 |
| `prompt` | 视频描述文本（与 `--prompt-file` 二选一） | - |
| `--prompt-file` | 包含 prompt 文本的文件路径（与 `prompt` 二选一） | - |
| `--seconds` | 视频时长，字符串 `"4"`–`"12"` | `"5"` |
| `--size` | 视频分辨率，Flash 仅支持 `"720P"` | `"720P"` |
| `--aspect-ratio` | 画幅比例：`21:9`/`16:9`/`4:3`/`1:1`/`3:4`/`9:16` | `"16:9"` |
| `--first-frame` | 首帧图片 URL（`keyframe` 模式） | - |
| `--last-frame` | 尾帧图片 URL（`keyframe` 模式） | - |
| `--images` | 参考图片 URL 列表，空格分隔，最多 5 个（`reference` 模式） | - |
| `--audios` | 参考音频 URL 列表，空格分隔，最多 3 个（`reference` 模式） | - |
| `--seed` | 随机种子 | - |
| `--save-path` | 保存视频的路径 | 不保存 |

## 视频尺寸与画幅

`size` 必须使用 `"720P"`。具体输出像素通过 `aspect_ratio` 选择：

| `aspect_ratio` | 输出像素 |
|----------------|----------|
| `21:9` | `1680x720` |
| `16:9` | `1280x704`（2026 年 9 月实测值） |
| `4:3` | `960x720` |
| `1:1` | `720x720` |
| `3:4` | `720x960` |
| `9:16` | `720x1280` |

## Flash 专属限制

| 校验项 | Flash 规则 | 校验失败响应 |
|--------|------------|--------------|
| `size` | 仅支持 `"720P"` | HTTP 400：`size must be 720P` |
| `reference` 图片数量 | `images` 最多 5 张 | HTTP 400：`images length must not exceed 5` |
| `reference` 音频数量 | `audios` 最多 3 段 | HTTP 400：`audios length must not exceed 3` |
| `reference` 视频输入 | 不支持 `videos` | HTTP 400：`videos is not supported` |

`gen_video.py` 在发起请求前已经对 `size`、`images` 数量、`audios` 数量、必需字段做了本地校验，避免产生不必要的失败任务。

## 异步任务与轮询

1. `create_video()` 调用 `POST /v1/videos` 创建任务，返回的响应中 `video_id`（或 `id`/`task_id`）用于查询。
2. `query_video()` 调用 `GET https://apihub.agnes-ai.com/agnesapi?video_id=<VIDEO_ID>&model_name=agnes-video-2.5-flash` 查询任务状态。
3. `generate_video()`（高层封装，`gen_video.py` 默认调用）每隔 `2` 秒轮询一次，直到 `status` 为 `completed`（下载视频并可选保存）或 `failed`（返回错误），或超过 `max_wait_seconds`（默认 1800 秒）超时。

如需自行控制轮询逻辑，可直接使用 `create_video()` 和 `query_video()` 两个低层方法。

## API 密钥如何传递

代码从环境变量读取 `AGNES_API_KEY` 并传给 `AgnesVideoClient`，与 `gen_image_with_text` skill 完全一致：

```python
# 1. 从系统环境变量获取 API Key
api_key = os.environ.get("AGNES_API_KEY")

# 2. 传给 AgnesVideoClient 构造函数
client = AgnesVideoClient(api_key=api_key)

# 3. client 使用此 key 在 headers 中发起请求
# headers = {"Authorization": f"Bearer {self.api_key}"}
```

**设置方式**：

**Windows PowerShell（会话生效）**：
```powershell
$env:AGNES_API_KEY="sk-your-api-key-here"
python .claude/skills/gen_video_with_text/gen_video.py text "prompt" --save-path output.mp4
```

**Windows Bash / Git Bash**：
```bash
AGNES_API_KEY="sk-your-api-key-here" python .claude/skills/gen_video_with_text/gen_video.py text "prompt" --save-path output.mp4
```

**永久设置（Windows）**：
1. 右键"此电脑" → 属性 → 高级系统设置 → 环境变量
2. 用户变量 → 新建 → 变量名：`AGNES_API_KEY`，变量值：你的 API Key
3. 重启终端生效

## 提示

1. **API 密钥**: 确保 `AGNES_API_KEY` 环境变量已正确设置
2. **Prompt 语言**: 英文 prompt 通常能获得更好的生成效果
3. **目录创建**: `--save-path` 指定的目录会自动创建
4. **模式互斥**: `text`/`keyframe`/`reference` 三种模式的媒体参数互不通用，混用会返回 HTTP 400
5. **参考素材指代**: `reference` 模式下可在 prompt 中使用 `<Picture N>` 和 `<Audio N>` 指代对应的第 N 个 `images`/`audios` 素材
6. **素材有效性**: 所有媒体 URL 都应可公开访问，并在任务完成前保持有效
7. **计费**: 当前 Agnes Video 2.5 Flash 限时免费（原价 `$0.025/秒`），如需最新价格以官方公告为准
8. **Windows 环境**：使用 `$env:VAR_NAME="value"` 设置环境变量，或在命令前直接 `VAR=value`（通过 bash）
9. **验证 API 密钥**：如果生成失败，先确认 API Key 是否有效且未过期
10. **超时**：默认最长等待 1800 秒，超时会返回 `success: False` 及最后一次查询结果
