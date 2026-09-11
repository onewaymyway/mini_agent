# API 错误处理规范（定妆图 / TTS / 视频生成通用）

本 skill 里会产生外部 API 调用的三处：`gen_image_with_text`（定妆图，
阶段4）、TTS 引擎（配音，阶段4）、`gen_video_with_text`（视频生成，
阶段5）。三者共享同一套底层重试/切换逻辑（`agnes_key_pool.py` +
各自的 `agnes_tools.py`，属于 `gen_image_with_text`/`gen_video_with_text`
skill 自带，本 skill 不重复实现），行为规律一致，按下面的方式解读和
处理即可，**不需要另外手写重试逻辑**。

## 两类错误，两种应对方式

### 1. 限流类（429 / 命中 "rate limit"/"quota exceeded" 等关键字）

脚本会**自动切换到下一把可用 API Key 立即重试**，不占用普通重试次数；
所有 key 都在冷却中时才会真正等待冷却。这一类错误**不需要 Agent 介入**，
终端上能看到类似：

```
[POST 失败] status_code=429，第 2/3 次重试，3.0s 后重试: {"error":{"message":
"You've reached the API rate limit for free users. ..."}}
```

看到这类日志继续等命令跑完即可；如果配置了多把 key（`AGNES_API_KEYS`
环境变量或 `providers.json` 里 `providers.agnes.api_keys`），实际会先
换 key 再重试，日志里换 key 是静默的（不计入这里打印的重试计数），
只有同一把 key 内的失败才会打这条日志。若所有 key 都耗尽，脚本会按
指数退避等待冷却后继续尝试，不会无限阻塞。

**Agent 唯一需要做的**：如果一个批量任务（比如
`generate_scene_videos_v2.py` 跑一整批 micro_scene）因为所有 key 长时间
限流导致耗时过长，可以如实告知用户"当前受限流影响，生成较慢"，不需要
中断任务，也不要因为看到限流日志就判定任务失败。

### 2. 非限流类（400 参数错误 / 鉴权失败 / 其它 4xx）

这类错误换 key 也无法解决，同一个请求重试多少次结果都一样，处理方式是
**两层都做了快速失败**：

- 底层 `gen_video_with_text`/`gen_image_with_text` 的 `agnes_tools.py`
  在 `_post`/`_get` 里一旦判定失败是非限流的 `400/401/403/404/422`，
  立即返回 `{"success": False, "error": ..., "non_retryable": True}`，
  不再消耗 `max_retries`（这两个 skill 是共享依赖，这个改动对所有调用
  方生效，不止本 skill）；
- 本 skill 的 `generate_scene_videos_v2.py` 收到 `non_retryable: True`
  后，**立即终止整个脚本**（`sys.exit(1)`），打印结构化 JSON 错误
  （含出错的 `macro_scene`/`scene_id`、错误详情、已经成功的场景清单、
  `action_required` 提示），不会继续处理其它场景——这类错误意味着
  配置本身有问题，带着错误继续跑完其它场景没有意义，也可能掩盖问题；
  已经成功生成的场景在退出前会先落盘 `status: done`，不受影响，下次
  重跑同一条命令会自动跳过。

典型日志：

```
[POST 不可重试错误] status_code=400，判定为参数/鉴权/内容类错误，立即停止重试:
{"code":"invalid_request","message":"invalid mode (request id: ...)","data":{"param":"mode"}}

[FAST-FAIL] macro_scene_02/micro_05 遇到不可重试错误，立即终止整个脚本，不再处理其它场景。
错误详情: status_code=400: invalid mode (request id: ...)
{
  "success": false,
  "fatal": true,
  "scene_id": "micro_05",
  "macro_scene": "macro_scene_02",
  "error": "status_code=400: invalid mode (request id: ...)",
  "already_succeeded": ["micro_01", "micro_02", "micro_03", "micro_04"],
  "action_required": "请检查 macro_scene_02/scene_detail.yaml 里 micro_05 的配置（常见如 video_mode/prompt_en 等字段），修正后用 --macro-id macro_02 --micro-id micro_05 重新运行本脚本，不需要重跑已成功的场景。"
}
```

**处理流程**：
1. 读打印出的结构化 JSON 里的 `error`/`action_required`，定位是哪个
   `macro_id`/`scene_id`、哪个字段传错了——上面的例子是 `mode` 参数
   无效，通常对应 `scene_detail.yaml` 里该 `micro_scene` 的
   `video_mode` 字段填了脚本不认识的值；
2. 只修正这一个条目对应的配置字段（不要动其它已成功的条目）；
3. 用 `--macro-id --micro-id` 定向重新运行同一条命令，**只重跑刚才
   失败的这一个场景**，已成功的场景会因为 clip 文件已存在被自动跳过；
4. 重跑后仍失败，把完整错误信息展示给用户，说明"这不是限流问题，是
   参数/内容问题，需要人工确认怎么改"，不要反复用相同的错误配置无脑
   重试，也不要绕过错误直接放弃这个场景。

**绝不允许**：看到非限流错误就当作限流去等待重试；或者忽略
`[FAST-FAIL]` 提示，直接重新整体跑一遍希望"这次能过"——参数错误不会
因为重跑就自己变好，必须先改配置，改完只重跑那一个失败条目。

## 已知局限（如实说明）

- `agnes_tools.py` 的快速失败判断按状态码 `400/401/403/404/422` 归类，
  如果 API 未来返回其它状态码承载同类"参数/内容"错误，脚本仍会把它当
  可重试错误走完 `max_retries` 才放弃——归类规则可能需要随 API 实际
  返回的状态码演进；
- `synthesize_scene_audio.py`（TTS 配音）走的是 CosyVoice/edge-tts 本地
  引擎，不经过 Agnes API，本次改动不涉及配音这一步的错误处理，TTS 失败
  仍按原有的降级/报错逻辑处理。

## 常见非限流错误对照

| 现象 | 常见原因 | 处理 |
|---|---|---|
| `invalid mode` / `invalid_request` + `param: mode` | `video_mode` 填了不支持的值 | 检查 `scene_detail.yaml` 该条目的 `video_mode`，改成 `reference`/`keyframe`/`text` 之一 |
| 提示"没有可用的参考图片"后自动降级 | `video_mode: reference` 但对应角色/地点 `asset_path` 为空 | 回阶段4补生成定妆图，再 `--force` 重跑该条目 |
| 鉴权失败 / 401 | `AGNES_API_KEY`/`AGNES_API_KEYS` 未设置或已过期 | 提示用户检查环境变量/`providers.json` 配置，不属于脚本能自动修复的问题 |
| prompt 触发内容审核类报错 | `prompt_en` 里有敏感措辞 | 改写该条目的 `prompt_en`，避免露骨/暴力等措辞，再定向重跑 |
| 网络超时/连接失败 | 本地网络问题或对端服务抖动 | 脚本本身有重试；持续失败可稍后再整体重跑一次失败清单 |

## 一个总原则

**限流 → 脚本自己解决，人不用管；参数/内容类 → 人必须读懂错误信息去
修配置，脚本不会替你猜对的值应该是什么。** 出现失败时先看错误信息里
是否有 429/"rate limit"/"quota"关键字来判断属于哪一类，再决定"等它
自愈"还是"去改配置"。
