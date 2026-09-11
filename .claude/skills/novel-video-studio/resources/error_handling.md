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

这类错误换 key 也无法解决，同一个请求重试多少次结果都一样。
`generate_scene_videos_v2.py` 对每个场景外层最多尝试
`MAX_RETRIES_PER_SCENE`（3）次，但内置了**快速失败判断**
（`is_non_retryable_error`）：一旦某次失败的 `status_code` 落在
`{400, 401, 403, 404, 422}` 且不是限流关键字，**立即停止对这个场景的
剩余重试**，打印明确提示后直接放弃这一条、继续处理批次里的其它场景，
不会白白再耗剩余的 attempt 次数。（注：调用方 `gen_video_with_text`/
`gen_image_with_text` 自身的 `agnes_tools.py._post` 内部仍会先重试几次
才把结果返回给本脚本——这一层不是本 skill 的代码，本 skill 只保证在
拿到明确的非限流失败结果后不再在外层瞎重试，不做二次浪费。）典型
日志：

```
[POST 失败] status_code=400，第 3/3 次重试，4.5s 后重试: {"code":"invalid_request",
"message":"invalid mode (request id: ...)","data":{"param":"mode"}}
```

批量脚本（`generate_scene_videos_v2.py`/`synthesize_scene_audio.py`）
最终会在 stdout 打印类似结构：

```json
{"success": false, "succeeded": [...], "failed": ["micro_07"],
 "errors": {"micro_07": "status_code=400: invalid mode (request id: ...)"}}
```

**处理流程**：
1. 读错误信息里的 `data.param` / `message`，定位是哪个字段传错了——
   上面的例子是 `mode` 参数无效，通常对应 `scene_detail.yaml` 里该
   `micro_scene` 的 `video_mode` 字段填了脚本不认识的值（比如手误写
   成拼写错误，或者用了当前 API 版本不支持的模式）；
2. 只修正 `errors` 里列出的那几个条目对应的配置字段（不要动其它已
   成功的条目）；
3. 用 `--macro-id --micro-id`（视频/配音脚本都支持）定向重跑**只有
   这几个失败的条目**，不要整体重跑一遍批次；
4. 重跑后仍失败，把完整错误信息展示给用户，说明"这不是限流问题，是
   参数/内容问题，需要人工确认怎么改"，不要反复用相同的错误配置无脑
   重试。

**绝不允许**：看到失败就假设是限流然后干等重试；或者反过来看到失败
就直接跳过该条目不处理导致最终产物有缺口——批量脚本本身允许"部分
失败、继续处理其它"，但**收尾时必须回到失败清单逐条修复**，不能带着
`failed` 非空的状态进入下一阶段。

## 已知局限（如实说明，不夸大修复范围）

`generate_scene_videos_v2.py` 的快速失败判断只作用于**本脚本自己的外层
重试循环**——它调用的 `gen_video_with_text`/`gen_image_with_text` 底层
`agnes_tools.py._post` 是这两个独立 skill 自带的通用 HTTP 客户端，内部
对所有非限流失败也会先按同一把 key 重试几次才把最终结果返回上来，这一层
不属于本 skill 的代码，本次没有改动（改了会影响其它使用这两个 skill的
场景）。也就是说：单次调用内部仍可能有几次"重试注定失败"的 HTTP 请求，
但本 skill 保证**外层不会在拿到明确的非限流失败结果后继续瞎重试**，
把浪费限制在这一层之内。如果这一点仍然造成明显的时间/额度浪费，需要
到 `gen_video_with_text`/`gen_image_with_text` 各自的 `agnes_tools.py`
里同样加上"非限流错误不重试"的判断（改动会影响这两个 skill 的所有
调用方，建议单独评估后再改）。

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
