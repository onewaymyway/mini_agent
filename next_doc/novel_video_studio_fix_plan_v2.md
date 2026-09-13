# novel-video-studio 问题复盘与改进计划 v2

本文档是 v1（`novel_video_studio_fix_plan_v1.md`，四个问题均已修完）之后，
针对 `compose_macro_scene.py` 等此前未覆盖脚本的第二轮排查，同样要求
**先落文档、评审通过后再改代码**，改完在本文件底部勾掉对应 checkbox 并
注明改动的 commit/文件。

评审已过一轮，每个问题下面记录了明确的处理结论（改/不改/怎么改），本轮
只落地"确认要改"的三个问题；"确认不改"的两个问题也记录在案，避免以后
重复排查、重复纠结。

---

## 问题1（P0）：`compose_macro_scene.py` 硬编码了 Windows 专属路径，换机器/换系统必炸

### 现象
文件顶部：

```python
_FFMPEG_CANDIDATES = [
    r"C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
]
_FFPROBE_CANDIDATES = [...]  # 同上
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
```

`_FFMPEG_CANDIDATES`/`_FFPROBE_CANDIDATES` 好歹有 `imageio_ffmpeg`
探测 + `shutil.which()` 兜底（`_resolve_bin()`），但 `FONT_PATH`
**没有任何兜底**，直接被 `ImageFont.truetype(FONT_PATH, ...)` 使用——
非 Windows 环境，或者 Windows 环境但字体路径/用户名不同（`onewa` 明显
是某个人的开发机用户名），会直接抛异常导致 Step 4 字幕渲染崩溃，整个
`compose_macro_scene.py` 跑不完。

### 修复方案（已评审确认）

**字体路径**：
1. 优先级：`--font-path` CLI 参数（已有，最高优先级，用户显式指定时
   永远尊重）→ 环境变量 `NOVEL_FONT_PATH`（次高优先级，免得每次都要
   敲一遍 CLI 参数）→ 跨平台常见字体候选列表 → 都找不到时**明确报错**
   并提示用户使用 `--font-path` 或设置 `NOVEL_FONT_PATH`，不再允许
   静默使用一个不存在的路径。
2. 候选列表按平台常见安装位置列举，至少覆盖：
   - Windows：`C:\Windows\Fonts\msyh.ttc`（微软雅黑）、
     `C:\Windows\Fonts\simhei.ttf`（黑体）；
   - Linux：`/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`、
     `/usr/share/fonts/truetype/wqy/wqy-microhei.ttc`（文泉驿微米黑，
     常见发行版直接可装）等 Noto CJK / WQY 系列常见路径；
   - macOS：`/System/Library/Fonts/PingFang.ttc`（苹方）、
     `/Library/Fonts/Arial Unicode.ttf`。
3. 报错信息要给出具体缺失原因和修复方式（"以上候选路径均未找到中文
   字体，请安装一个中文字体或设置 NOVEL_FONT_PATH 环境变量指向具体
   字体文件"），不要只报一个 `OSError: cannot open resource` 这类底层
   异常信息，让用户猜不出该干什么。
4. `references/05_scene_video_generation.md` 里现有的"非 Windows
   环境需要 `--font-path <本地中文字体路径>`"这句描述需要同步改写——
   加了自动探测之后，多数 Linux 环境如果装了常见中文字体应该能自动
   找到，`--font-path`/`NOVEL_FONT_PATH` 变成"自动探测失败时的兜底
   手段"而不是"非 Windows 环境的必填项"。

**ffmpeg/ffprobe 查找**（用户要求：不能只靠用户手填，要有自动识别
机制，尤其是 conda 环境）：
1. 查找优先级调整为：
   - 环境变量 `NOVEL_FFMPEG_PATH`/`NOVEL_FFPROBE_PATH`（显式覆盖，
     最高优先级）；
   - `imageio_ffmpeg.get_ffmpeg_exe()`（已有逻辑保留，这是最可靠的
     跨平台方式，因为它是 pip 包自带的独立二进制，不依赖系统环境）；
   - **conda 环境自动探测（新增）**：依次检查
     - 当前激活的 conda 环境：读 `CONDA_PREFIX` 环境变量，拼
       `$CONDA_PREFIX/bin/ffmpeg`（Linux/macOS）或
       `$CONDA_PREFIX/Library/bin/ffmpeg.exe`（Windows）；
     - 其它已存在的 conda 环境：用 `conda env list --json`（如果
       `conda`/`mamba` 命令可用）拿到所有环境路径，逐个拼上面同样的
       相对路径去探测是否存在 `ffmpeg` 可执行文件（不要求都激活，
       只要文件存在即可，因为很多用户会像 v1 问题4 提到的那样专门建一个
       `novel_tts_env`/类似命名的环境装依赖，`ffmpeg` 也可能装在某个
       非当前激活的环境里）；
     - 找到多个候选时，优先选**当前激活环境** > **名字包含
       `novel`/`mv`/`video` 等本项目相关关键词的环境** > 其它环境
       第一个匹配项，避免在有多个 conda 环境时选择顺序随机、结果不
       稳定；
   - 系统 `shutil.which("ffmpeg"/"ffprobe")`（原有逻辑保留）；
   - 都找不到时，和字体一样，**明确报错**并列出已经尝试过的查找方式
     （环境变量/imageio_ffmpeg/conda 环境列表/PATH），不要静默 fallback
     成一个不存在的路径（现在的 `_resolve_bin()` 在都找不到时会返回
     `candidates[-1]`，也就是一条铁定不存在的硬编码路径，同样是静默
     产出错误配置，调用方要等真正执行 ffmpeg 命令时才会报错，报错信息
     还不会直接指出"路径没配对"这个根因）。
2. 这套"conda 环境自动探测"逻辑通用性较强，其它脚本如果将来也需要
   查找 ffmpeg，应该复用同一份实现（见问题3的 `scripts/common.py`），
   不要每个脚本各自再抄一遍。

### 评审结论：暂不改的相关问题
- `_render_text_png()` 按字符截断换行、不考虑中英文混排/标点悬挂、
  没有行数上限保护——**保持现状，暂不改**。记录在案，如果后续真的
  出现字幕溢出问题再单独排查。

- [ ] 未开始

---

## 问题2（P1）：`--allow-missing-clips` 允许用"借用相邻 clip 强制拉伸"掩盖真实缺失

### 现象
`compose_macro_scene.py` 第314~331行，某个 micro_scene 没有对应 clip
文件、且传了 `--allow-missing-clips` 时，脚本会拿前一个（或后面第一个）
能用的 clip **强制拉伸时长**去顶替这一段画面，只在 stderr 打印一行
`[强制填补] ...`提示，最终产出的 `macro_scene_XX.mp4` 里这一段画面和
实际内容/配音完全对不上，用户不盯着日志根本发现不了——这和 v1 问题1
"静默产出假数据"是同一类风险，只是发生在合成阶段而不是配音阶段。

### 修复方案（已评审确认：直接去掉这个口子，而不是加更多提示）

用户明确要求：**不再允许"借用画面顶替"这种行为**，clip 缺失就是真实
问题，应该倒逼回上游解决，而不是在合成阶段用一份不相关的画面糊弄过去。

1. 移除 `--allow-missing-clips` 这个 CLI 参数（连同第314~331行的
   "借用 last_available_clip / 向后找一个能用的 clip 强制拉伸" 整段
   逻辑一起删除）。
2. 缺 clip 时**始终**报错终止（保留原有 `missing_clip_ids` 判断和
   报错信息本身，只是从"可选跳过"变成"没有例外"），提示信息保持
   现有的"请先运行 generate_scene_videos_v2.py 补齐（--micro-id 定向
   重跑），用 check_clips_v2.py 校验通过后再执行本命令"，指引用户回
   阶段5补齐真正缺失的画面，而不是在阶段6硬凑。
3. `references/05_scene_video_generation.md`（Step 2/合成说明里明确
   提到了"`--allow-missing-clips` 才允许借用相邻小场景画面强制拉伸
   填补"这句话，需要改成"缺 clip 时直接报错终止，回阶段5补齐"）里，
   涉及 `--allow-missing-clips` 的描述要同步删除/改写，避免文档还在
   教用户用一个已经被移除的参数（已确认只有这一处文档引用了这个
   参数，`references/06_final_compose.md`/`error_handling.md` 里没有
   提到）。
4. `缺少配音的小场景` 分支（第273~277行）本来就没有类似的"允许跳过"
   开关，一直是硬报错——这次改完之后，clip 和配音两类缺失在
   `compose_macro_scene.py` 里的处理方式会保持一致（都是硬报错，没有
   例外开关），不再有一个能跳过一个不能跳过的不一致状态。

- [ ] 未开始

---

## 问题3（P2）：多个脚本各自复制同一份工具函数/常量，容易漏改不同步

### 现象
排查后确认至少有以下重复：

- `_macro_dir_name()`/`_macro_scene_dir_name()`（把 `macro_01` 转成
  `macro_scene_01` 目录名的同一段逻辑）在 **5 个脚本**里各自复制了一份：
  `check_assets_and_audio_v2.py`、`check_clips_v2.py`、
  `check_scene_detail.py`、`compose_macro_scene.py`、
  `synthesize_scene_audio.py`。
- 中文口播粗估语速常量 `_ROUGH_CHARS_PER_SEC = 4.5` 在
  `check_scene_detail.py` 和 `check_assets_and_audio_v2.py` 里各自
  定义了一份（本次 v1 问题3 修复时新增的），数值目前一致，但没有
  任何机制保证以后改一处时另一处会同步改。
- 视频生成接口的硬性时长范围 `MIN_SEC=4`/`MAX_SEC=12` 在
  `generate_scene_videos_v2.py` 里定义，`check_scene_detail.py` 里
  又单独定义了一份 `_MIN_SEC`/`_MAX_SEC`（数值一致但命名风格都不一样），
  `check_assets_and_audio_v2.py` 里则是通过 CLI 参数
  `--min-sec`/`--max-sec` 传入、默认值又各自硬编码了一遍 `4.0`/`12.0`。

这类重复此前已经导致过实际问题：v1 问题3 给
`check_assets_and_audio_v2.py` 加 `--macro-id` 时，是靠人工去翻
`check_clips_v2.py` 才对齐了参数风格（`nargs="*"`），如果当时没想起来
去翻，两个脚本的 `--macro-id` 行为就会不一致（一个支持传多个，一个
只支持传一个）。

### 修复方案（已评审确认）
1. 新增 `scripts/common.py`，收敛以下内容：
   - `macro_scene_dir_name(macro_id: str) -> str`（统一命名，替换各文件
     里 `_macro_dir_name`/`_macro_scene_dir_name` 两种不同的函数名）；
   - 时长相关常量：`MIN_SEC = 4`、`MAX_SEC = 12`、
     `ROUGH_CHARS_PER_SEC = 4.5`（统一到一个值，去掉"命名风格不一样"
     和"各自硬编码一份默认值"的问题）；
   - 问题1新增的 ffmpeg/ffprobe/字体路径自动探测逻辑（`resolve_ffmpeg()`
     /`resolve_ffprobe()`/`resolve_font_path()`），供
     `compose_macro_scene.py`（以及将来如果有其它脚本需要）统一调用；
   - `--macro-id` 这个 CLI 参数目前两种风格并存（`check_clips_v2.py`/
     `check_assets_and_audio_v2.py` 是 `nargs="*"` 可传多个，
     `check_scene_detail.py`/`synthesize_scene_audio.py`/
     `compose_macro_scene.py` 是单值），本次不强行统一成一种（改参数
     签名属于行为变更，风险和收益需要单独评审），只在 `common.py` 的
     模块注释里如实记录这个已知不一致，供以后新增脚本时参照选择，
     不要凭感觉现造第三种风格。
2. 逐个替换 5 个脚本里重复的 `_macro_dir_name`/`_macro_scene_dir_name`
   定义，改成 `from common import macro_scene_dir_name`，函数调用点
   同步改名；`check_scene_detail.py`/`check_assets_and_audio_v2.py`/
   `generate_scene_videos_v2.py` 里重复的时长常量同样改成从
   `common.py` 导入，删除各自的本地定义。
3. `common.py` 放在 `scripts/` 目录下，和其它脚本同级，各脚本用
   `from common import ...`（和现有 `from tts_engine import ...`、
   `from voice_mapping import ...` 的引用方式保持一致，不引入新的
   打包/路径约定）。
4. 这是一次纯重构（提取重复代码，不改变任何脚本的输入输出行为），
   改完后需要对被改动的每个脚本至少跑一次现有的 fixture/冒烟测试，
   确认行为和改动前完全一致（尤其是被替换掉的 `_macro_dir_name` 系列
   函数在个别脚本里可能有细微的既有差异，合并前需要逐一比对确认真的
   是同一份逻辑，不能想当然）。

### 评审结论：暂不改的相关问题
- `narration_wav`/`joined` 视频对齐时用 `setpts=scale*PTS` 整体拉伸，
  `ALIGN_EPS=0.02` 秒容差、超过容差无条件硬调、没有上限报错保护——
  **保持现状，暂不改**。
- `generate_scene_videos_v2.py` 是否存在和 v1 问题4 类似的"大批量调用
  无过程输出/无超时"的可用性隐患——**保持现状，暂不改**，如果以后
  实际用出问题再单独排查，不在本轮预防性处理。

- [ ] 未开始

---

## 实施顺序建议

1. 问题3（`scripts/common.py` 提取）——虽然优先级标 P2，但问题1的
   ffmpeg/ffprobe/字体自动探测逻辑本来就打算放进 `common.py`，先把
   `common.py` 的骨架和现有重复逻辑的收敛做完，问题1可以直接在这个
   骨架上加新函数，避免"先在 compose_macro_scene.py 里改一版，问题3
   再挪一遍"的返工。
2. 问题1（字体/ffmpeg 自动探测）——在 `common.py` 基础上实现，改完
   `compose_macro_scene.py` 里对应的引用。
3. 问题2（移除 `--allow-missing-clips`）——独立改动，和前两个问题
   没有依赖关系，可以并行做，但建议放在最后，避免和问题1同时改同一个
   文件时互相冲突增加 review 难度。

## 需要改动的文件清单（预告，供 review，不代表最终 diff）

- `scripts/common.py`（新增，问题1+问题3）
- `scripts/compose_macro_scene.py`（问题1：改用 common.py 的自动探测；
  问题2：移除 `--allow-missing-clips`）
- `scripts/check_assets_and_audio_v2.py`（问题3：改用 common.py 的
  `macro_scene_dir_name`/时长常量）
- `scripts/check_clips_v2.py`（问题3：同上）
- `scripts/check_scene_detail.py`（问题3：同上）
- `scripts/synthesize_scene_audio.py`（问题3：同上）
- `scripts/generate_scene_videos_v2.py`（问题3：改用 common.py 的
  `MIN_SEC`/`MAX_SEC`）
- `references/05_scene_video_generation.md`（问题1：改写"非 Windows
  环境需要 `--font-path`"这句描述，说明自动探测机制；问题2：删除/
  改写"缺 clip 时默认拒绝合成，`--allow-missing-clips` 才允许借用相邻
  小场景画面强制拉伸填补"这句描述，改为"缺 clip 时始终报错终止，回
  阶段5用 `--macro-id --micro-id` 补齐"）

改完之后按 diff-only 方式打包，只包含以上实际改动的文件。
