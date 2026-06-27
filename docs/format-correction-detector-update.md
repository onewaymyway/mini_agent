# 工具调用格式纠错——检测规则扩展说明

> 本文档是 [tool_call_format_correction.md](tool_call_format_correction.md) 的补充，
> 说明新增的两条检测规则，覆盖此前漏检的非标准标签变体场景。

---

## 1. 问题：漏检的案例 3

原有规则能检测到以下两类格式错误：

- `tag_role_confusion`：`<tool_result>` 和 `</tool_use>` 混用
- `unclosed_tool_use`：`<tool_use>` 数量多于 `</tool_use>`
- `malformed_json_in_tool_use`：标签内 JSON 损坏
- `legacy_fence_unclosed`：` ```tool_call ` 未闭合

但以下案例全部漏检：

```
路径不对，让我确认：<tool_call>bash<arg_key>command": "pwd && find . -name "*.py"",
    "timeout": 10
}
</tool_use>
```

特征：
- 使用了非标准开标签 `<tool_call>`（而非 `<tool_use>`）
- 闭合用的是 `</tool_use>`
- 结果：`<tool_use>` 计数 = 0，`</tool_use>` 计数 = 1，所有原有规则均不命中

---

## 2. 新增规则

### 2.1 `orphan_close_tag`

**触发条件**：存在任意闭合标签（`</tool_use>`、`</tool_call>`、`</tool_invoke>` 等），但规范开标签 `<tool_use>` 的数量少于闭合标签数量。

**典型场景**：
- 模型用 `<tool_call>` 开头，用 `</tool_use>` 闭合（案例 3）
- 闭合标签写了两次但开标签只有一次

**错误提示**：
> A `</tool_use>` (or similar closing tag) was found, but there is no matching `<tool_use>` opening tag before it. This usually means the opening tag used a non-standard name (e.g. `<tool_call>`) or was accidentally omitted.

### 2.2 `tool_call_alias_tag`

**触发条件**：存在非标准开标签变体（`<tool_call>`、`<tool_invoke>`），且没有任何规范 `<tool_use>` 开标签。

**典型场景**：
- 模型用 `<tool_call>` 开头，内容还未写完就截断（无闭合标签）
- 模型持续混用非标准标签名

**错误提示**：
> A non-standard tag variant such as `<tool_call>` or `<tool_invoke>` was used instead of `<tool_use>`. Only `<tool_use>` is recognized — please resend the tool call using the correct tag.

> **注意**：若模型同时写了 `<tool_use>` 开标签，则交由其他规则处理，`tool_call_alias_tag` 不重复触发。

---

## 3. 辅助正则

模块级新增两个复用正则，供现有规则和新规则共同使用：

```python
# 任意 tool 系列开标签（use / call / invoke 变体）
_ANY_TOOL_OPEN_RE  = re.compile(r"<tool_(?:use|call|invoke)\b[^>]*>", IGNORECASE)
# 任意 tool 系列闭合标签
_ANY_TOOL_CLOSE_RE = re.compile(r"</tool_(?:use|call|invoke)>",         IGNORECASE)
```

---

## 4. 规则触发矩阵

| 场景 | `<tool_use>` | `</tool_use>` | `<tool_call>` | 命中规则 |
|------|:---:|:---:|:---:|----------|
| 正常完整标签 | 1 | 1 | 0 | — |
| 重复开标签 | 2 | 1 | 0 | `unclosed_tool_use` |
| 标签角色混淆 | 0 | 1 | 0 | `tag_role_confusion` |
| **案例 3**：call 开 + use 闭 | 0 | 1 | 1 | **`orphan_close_tag`** |
| **案例 4**：纯 call 开无闭合 | 0 | 0 | 1 | **`tool_call_alias_tag`** |

---

## 5. 修改文件

| 文件 | 改动 |
|------|------|
| `perception/format_correction_detector.py` | 新增 `_ANY_TOOL_OPEN_RE` / `_ANY_TOOL_CLOSE_RE` 正则；新增 `_detect_orphan_close_tag()` / `_detect_tool_call_alias_tag()` 检测函数；在 `_RULES` 列表末尾追加两条规则 |

---

## 6. 扩展方式

如需增加新的格式错误检测，仍只需在 `_RULES` 末尾追加一个三元组：

```python
(
    "my_new_rule",          # issue_type 标识符
    _detect_my_new_rule,    # 检测函数：(text: str) -> bool
    "提示模型的错误说明文字\n",  # 注入到对话的纠错提示
),
```
