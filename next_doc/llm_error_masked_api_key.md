# LLM 报错时附带脱敏 API key

## 背景
调用模型报 403（账号被限制）等错误时，日志里看不出是哪把 key 出了问题，
多 key 轮转（ApiKeyPool）场景下尤其难排查。

## 改动
- `src/mini_agent/llm/base.py`：新增 `mask_api_key(key)`。
  - 长度 >= 16：前 4 位 + `...` + 后 4 位（`sk-a...1234`）
  - 8~15 位：仅保留后 3 位；< 8 位：`***`；空：`<empty>`
- `src/mini_agent/llm/providers/_base_mixin.py`：`_upgrade_error()` 末尾调用新增的
  `_attach_masked_key()`，给 LLM 层异常消息追加 ` [api_key=sk-a...1234]`。
  所有 provider 共用，重试日志 `LLM retry n/m ... — [Exception] ...` 因此自动带上。

## 行为约定
- 保持异常类型不变（重试 / fallback 依赖类型名判断）。
- 不重复追加；无 key（ollama 等）与 `LLMConfigError` 不追加。
- 失败时静默回退为原异常，不影响真实错误的传播。
- 展示的是**发起该次请求时**所用的 key（key 轮转后以当前 client 的 key 为准）。

## 效果示例
`LLM retry 1/3 (wait 10.0s, fixed(10.0s)) — [Exception] LLMProviderError: OpenAI API error: Error code: 403 - {...} [api_key=sk-a...1234]`

## 追加：403 时先换 key，再 fallback（2026-11-04）
现象：配置了 4 个 agnes key，某把 key 被 403（账号被限制）后，同一把 key 重试 3 次（共等 30s），
随后直接 fallback 到其它 provider，其余 3 把 key 完全没被尝试。

根因：
1. `OpenAIProvider._wrap_error` 把 401/403 当普通 `LLMProviderError`（仅 nvidia 归为
   `LLMPermanentError`），因此被当成可重试错误；
2. `from_config` 里 `key_switch_on` 默认只有 `LLMRateLimitError`，403 不触发换 key；
3. `_should_fallback` 只比较精确类名，不认父类。

修复：
- `providers/openai.py`：status 401/403 → `LLMPermanentError`（agnes/openrouter/deepseek 等继承者同样生效）。
- `client_pool.py`：
  - 建 `ApiKeyPool` 时 `switch_on` 始终并入 `LLMPermanentError`；
  - 新增 `_exc_matches()`，`key_switch_on` / `fallback_on` 按类名（含父类）匹配，
    `LLMContextWindowError` 例外，仍原样抛出以触发 compact；
  - ApiKeyPool 的换 key 日志改用 `mask_api_key()`（原先打印 key 末 8 位）。
- 行为：403 → 立即换下一把 key（不等待）→ 4 把全部冷却后 → 立即 fallback 下一个 provider。

## 追加：G-3 定妆图（图像 skill）同样需要换 key（2026-11-04）
G-3 走的不是 LLM 链路，而是 `gen_image_with_text` skill。该 skill 的 `_post` 未接入 key 池里已有的
“账号受限 → 换 key”能力（视频 skill 已接入），因此一把 key 被 403 就整体失败。
已对齐视频版实现，并补充脱敏的运行日志与可读的错误信息，详见 `external_projects/novel_to_video/PROJECT.md`。
