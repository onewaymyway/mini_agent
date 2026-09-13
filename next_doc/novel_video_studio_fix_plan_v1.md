# novel-video-studio 问题复盘与改进计划 v1

本文档记录一次真实项目（`永生协议` / `ai_test_v5`）跑 `novel-video-studio`
skill 过程中暴露出的问题、根因定位过程，以及后续要落地的修复方案。**先落
文档、评审通过后再改代码**，改完在本文件底部勾掉对应 checkbox 并注明改动
的 commit/文件。

## 背景

`macro_01` 最终合成的视频出现"语音/字幕/画面对不上"的现象，排查过程中
牵出三个独立问题，外加一个"脚本会卡死且没有过程输出，用户以为程序挂了"
的可用性问题。四个问题互不依赖，可以分别修，但建议一次性改完，避免同一
类问题在后续大场景（macro_03~macro_07 已经出现 `_b` 后缀 id 的苗头）里
重演。

---

## 问题1（根因，P0）：`tts_engine.py` 的时长估算 fallback 用错了变量，用文件名当文本估算时长

### 现象
`macro_01` 全部18个 micro_scene 的 `duration_sec` 只有 `7.25` 和 `8.0` 两个
值，跟每条文本长度（2字到64字不等）完全不相关。

### 根因
`tts_engine.py::_ffprobe_duration()` 的最后一级 fallback：

```python
# 最后 fallback：按文本字数估算（约 4 字/秒）
import re
chars = len(re.sub(r'\s', '', path.name))   # bug：这里是 path.name（文件名字符串）
return max(0.5, chars / 4.0)
```

`path.name` 是 `narration_seg_micro_16_00.wav` 这样的**文件名**，不是真正
要合成的**文本**。这类文件名长度普遍在 29~32 字符左右，`/4.0` 后正好落在
7.25~8.0 这个区间，与实测数据完全吻合。

### 触发条件
`ffprobe`（第一级）和 `mutagen`（第二级）都失败时才会走到这一级。本次
环境里 `check_clips_v2.py` 已经报过"未找到 ffprobe 或探测失败"，说明这台
机器的 `ffprobe` 确实不可用，进而触发了这个 bug。**更严重的是这个 fallback
不会报任何错，产出的假数据会被当成真实数据一路写进 `scene_detail.yaml`
并通过下游所有现有校验**——这是本次问题里危害最大的一环：错误被静默吞掉了。

### 修复方案
1. 删除"按文件名字数估算"这条逻辑，这不是"简陋但方向对"的近似，是一次
   传参错误，没有修复价值。
2. `ffprobe`/`mutagen` 都失败时，默认直接抛异常，交给调用方按"该 block
   配音失败"处理（会体现在 `errors` 里，不会被静默接受）。
3. 保留一个**默认关闭**的兜底开关 `--allow-estimated-duration`（对应
   `synthesize()` 的 `allow_estimated_duration: bool = False` 参数）：显式
   打开时才允许退回"按**文本**真实字数（不是文件名）"估算，且返回结果里
   带 `duration_estimated: True` 标记，调用方把这个信息汇总打印出来，不
   让估算值和真实值混在一起看不出区别。

- [ ] 未开始

---

## 问题2（P0）：修订流程里"删除旧 micro_scene + 在末尾追加新 id"导致 id 编号与叙事顺序脱节

### 现象
`macro_01` 的 `scene_detail.yaml` 里，`micro_scenes` 列表的实际书写顺序
（对应真实叙事顺序）是 `13,02,14,04,15,16,17,06,18,19,20,21,09,22,23,11,
24,25`，`id` 数字本身既不连续（缺 01/03/05/07/08/10/12）也不按列表顺序
递增。`macro_03`/`macro_07` 已经出现了 `micro_35b`/`micro_40b`/`micro_41b`
/`micro_42b`/`micro_69b` 这类后缀 id，是同一类操作手法的延续。

### 根因
修订某个已有 micro_scene 时，没有按 `revision_and_rollback.md` 的设计
"原地编辑该 id 的 content_blocks + 用 `invalidate.py` 清空其下游产物"，
而是把旧条目整体删掉，把新内容当成全新场景追加到列表末尾（拿全局最大
id+1，或者在原 id 后加 `b`）。这本身不会让现有脚本报错（`id` 只要求全局
唯一，`compose_macro_scene.py` 用的是列表顺序不是数字顺序），但：
- 掩盖了"这个大场景其实被大幅度重写过"这个事实，PROGRESS.md 里也没有
  留痕，后续 session 接手时容易忽略；
- 一旦哪次修订不小心把列表顺序也搞乱了（比如追加时插错了位置），现有
  校验完全发现不了，因为没有任何脚本检查"列表顺序是否等于 id 数字顺序"。

### 修复方案
1. **文档层面**（`revision_and_rollback.md`）：新增一条硬规则——修改已有
   micro_scene 内容必须原地编辑，禁止删除旧条目再以新 id 追加到列表末尾；
   如果确实需要把一个 micro_scene 拆成两个，新拆出来的那条允许使用
   `<原id>b` 这种后缀，但必须插入在紧邻原条目之后的列表位置上，不能追加
   到全局末尾。
2. **脚本层面**（`check_scene_detail.py`）：新增一条 warning——遍历
   `micro_scenes` 列表，比较"列表出现顺序"与"id 数字大小顺序"是否一致，
   不一致时报出具体是哪几条错位，提示"疑似修订时使用了删除+追加到末尾
   的方式，请确认列表顺序仍是叙事顺序"。这条放 warning 而不是 error，
   因为 `_b` 后缀本身是允许的合法用法，只有"顺序不连续"这个信号本身不能
   100% 判定为错误，需要 Agent 结合上下文确认。

- [ ] 未开始

---

## 问题3（P1）：`check_assets_and_audio_v2.py` 不支持按大场景过滤，且没有独立的"时长合理性"校验

### 现象
```
python check_assets_and_audio_v2.py <output_dir> --macro-id macro_01
error: unrecognized arguments: --macro-id macro_01
```
另外，即便问题1修好了，这个校验脚本目前也没有任何手段独立发现"时长看起
来不像真的"这种数据异常——它只检查 `duration_sec` 是否在 4-12 秒区间内，
不检查这个数字和对应文本长度是否匹配。

### 修复方案
1. 加 `--macro-id`（`nargs="*"`，可传多个，不传则查全部，参数风格对齐
   `check_clips_v2.py` 已有的写法），支持单场景定向校验。
2. 新增一条独立的合理性校验（不依赖问题1是否修好，作为兜底防线）：对每
   个 micro_scene，用 03 文档同款的粗估语速（4.5字/秒）算出理论时长，和
   实际 `duration_sec` 做比值，比值超出合理区间（建议：实际值不足理论值
   40% 或超过理论值250%）时报 warning，附带具体数字，方便一眼看出"这条
   时长和文本对不上，大概率是假数据或串号"。
3. 顺带加一条环境自检 warning：探测 `ffprobe` 是否能正常调用，探测不到
   时提示"当前环境 ffprobe 不可用，duration_sec 的真实性无法通过时长本身
   验证，建议先修好 ffprobe 再信任已有数据"。

- [ ] 未开始

---

## 问题4（P1，可用性）：`synthesize_scene_audio.py` 会在网络/模型调用上无限期卡住，且全程没有过程输出

### 现象
跑 `synthesize_scene_audio.py --macro-id macro_03` 时长时间没有任何输出，
且 bash 工具本身也是 `"timeout": -1`（不限时等待），从外部完全无法判断
是"正常运行中"还是"已经卡死"。

### 根因（两点叠加）
1. **无超时保护**：`tts_engine.py::_run_edge_tts()` 里
   `asyncio.run(_run())` 调用 `edge_tts.Communicate(...).save(...)`
   是一次在线网络请求，**全程没有设置任何超时**——一旦网络不通/被墙/
   DNS解析慢/edge-tts 服务端无响应，这一步会挂起，没有任何机制会把它
   打断。`_try_cosyvoice()` 里加载本地模型权重、跑推理，同样没有超时
   保护，如果模型文件很大或者跑在 CPU 上很慢，也会长时间没有反馈。
   对比之下，同文件里两处 `subprocess.run(ffprobe/ffmpeg, timeout=...)`
   反而是有超时的——**唯独最容易在真实网络环境里出问题的 edge-tts 网络
   调用没有超时**，这是明显的疏漏。
2. **零过程输出**：`synthesize_scene_audio.py::run()` 从进入大场景循环
   到跑完，中间不管处理了多少个 micro_scene、多少个 content_block，
   一行 `print` 都没有，只有最后一次性 `print(json.dumps(result))`。
   即使脚本本身在正常处理（比如一个大场景18个小场景、每个小场景2-3个
   block，edge-tts 请求正常但网络慢一点，跑几分钟很正常），用户看到的
   现象也是"命令挂在那不动"，和真的卡死无法区分。

### 修复方案
1. **给 edge-tts 网络调用加超时**：用 `asyncio.wait_for(_run(), timeout=N)`
   包裹（建议 30-60 秒可配置），超时后抛 `TTSError`，走已有的
   "该 block 配音失败" 分支（会被记录进 `errors`，不会导致整个脚本挂死）。
2. **给 CosyVoice 本地推理加超时保护**：由于 CosyVoice 是同步阻塞调用，
   `asyncio.wait_for` 不适用，改用
   `concurrent.futures.ThreadPoolExecutor().submit(...).result(timeout=N)`
   包一层（跨平台，不依赖 Unix-only 的 `signal.alarm`），超时同样归为
   "该 block 走 CosyVoice 失败，交给 edge-tts 降级" 或整体失败。
3. **补齐过程打印**（`synthesize_scene_audio.py::run()`）：
   - 开始处理某个大场景前打印一行：`[macro_01] 开始配音，共 N 个
     micro_scene`；
   - 每处理完一个 content_block（不管成功/失败/复用已存在文件）打印一行：
     `[macro_01][micro_13][block 0/2][narration] 引擎=edge-tts 用时=1.8s
     时长=3.42s`（复用已存在音频时标注"复用已存在"）；
   - 每处理完一个 micro_scene 打印小结：
     `[macro_01][micro_13] 完成，duration_sec=7.25`；
   - 全部处理完，在原有的最终 JSON 之前，打印一行总耗时汇总。
   - 这些打印全部走 `print(..., file=sys.stderr)` 或显式 flush，保证在
     被外部工具捕获/重定向时也能实时看到，不要攒在缓冲区里最后一次性冒
     出来（Python 对非 tty 输出默认是全缓冲，需要 `flush=True` 或
     `-u`/`PYTHONUNBUFFERED=1`）。
4. 文档层面：在 `references/04_assets_and_audio.md` 和 `error_handling.md`
   里补一条说明——配音步骤是本 skill 里少数会产生"长时间无输出但仍在
   正常运行"现象的步骤，正常情况下应该能看到逐 block 的进度打印，如果
   打印停止超过一个超时周期（见上面配置的 timeout 值）仍没有新行，才
   应该判断为真正卡死，去检查网络/CosyVoice 环境。

- [ ] 未开始

---

## 实施顺序建议

1. 问题1（tts_engine.py 时长估算 bug）——独立、影响面最大，优先修。
2. 问题4（超时保护 + 过程打印）——同样在 `tts_engine.py`/
   `synthesize_scene_audio.py`，和问题1改的是同一批文件，一起改减少
   来回。
3. 问题3（check_assets_and_audio_v2.py 加 `--macro-id` + 合理性校验）——
   验证问题1/4改完之后能不能被这道校验兜住。
4. 问题2（check_scene_detail.py 顺序校验 + revision_and_rollback.md
   规则更新）——独立模块，随时可以并行做。

## 需要改动的文件清单（预告，供 review，不代表最终 diff）

- `scripts/tts_engine.py`（问题1 + 问题4 超时保护）
- `scripts/synthesize_scene_audio.py`（问题4 过程打印，透传
  `allow_estimated_duration`/超时参数）
- `scripts/check_assets_and_audio_v2.py`（问题3）
- `scripts/check_scene_detail.py`（问题2）
- `references/revision_and_rollback.md`（问题2 规则更新）
- `references/04_assets_and_audio.md`（问题1/3/4 相关说明更新）
- `references/error_handling.md`（问题4 说明更新）

改完之后按 diff-only 方式打包，只包含以上实际改动的文件。
