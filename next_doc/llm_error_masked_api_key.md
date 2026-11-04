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
