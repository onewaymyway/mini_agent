# patch_file_simple 工具说明

> 基于行锚点的文件编辑工具，为长段落替换场景设计，比 `patch_file` 更不容易出错。

---

## 1. 背景与动机

`patch_file` 要求提供完全精确的 `old_string`，当替换区域较长时（几十行），LLM 容易在制表符、尾随空格、细微措辞上产生差异，导致"找不到字符串"的错误。

`patch_file_simple` 改为**用起止行号 + 起止行内容做双重锚定**：
- 只需告知第一行和最后一行的内容及其行号
- 中间内容不参与匹配，完全用 `new_string` 替换
- 行号和内容必须**同时对上**才执行写入，确保不会误改错误位置

---

## 2. 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | ✓ | 目标文件路径 |
| `old_string_start` | string | ✓ | 替换区域**首行**的完整内容 |
| `old_string_start_line_num` | integer | ✓ | 首行的 1-based 行号 |
| `old_string_end` | string | ✓ | 替换区域**末行**的完整内容 |
| `old_string_end_line_num` | integer | ✓ | 末行的 1-based 行号（须 ≥ 首行号） |
| `new_string` | string | ✓ | 替换内容（替换整个首行到末行区域，含首末两行） |

> **行内容对比规则**：去除行尾 `\r\n` 后与参数做精确字符串比较，不忽略前导空格。

---

## 3. 验证逻辑

工具在执行写入前会依次检验：

1. **参数合法性**：`end_line_num >= start_line_num`，行号均 `>= 1`
2. **行号范围**：首行号和末行号均不超过文件总行数
3. **内容匹配**：`lines[start-1].rstrip()` 与 `old_string_start.rstrip()` 完全一致；末行同理

任何一项不通过，立即返回错误，**不执行任何写入**。

---

## 4. 输出格式

**成功**：返回 unified diff（与 `patch_file` 格式一致）+ 摘要行：

```
--- a/src/foo.py
+++ b/src/foo.py
@@ -10,5 +10,4 @@
 ...
-    old line A
-    old line B
+    new content
[replaced lines 10–11 in src/foo.py]
```

**失败**：返回错误类型、期望/实际内容，以及周边上下文（便于 LLM 自我纠错）：

```
[error: line content does not match expected value]

Line 10 content mismatch:
  expected: '    def old_method(self):'
  actual:   '    def new_method(self):'

File context (lines 8–13):
       8  ...
       9  ...
      10      def new_method(self):
      11  ...
```

---

## 5. 与 patch_file 的对比

| 对比项 | `patch_file` | `patch_file_simple` |
|--------|-------------|----------------------|
| 匹配方式 | 全文精确匹配 `old_string` | 首行 + 末行 + 行号三重锚定 |
| 适合场景 | 短段落、精确替换 | 长段落、LLM 容易引入细微差异时 |
| 中间内容 | 必须完全正确 | 不参与匹配，直接替换 |
| 误改风险 | 相同内容多处出现时可能误改 | 行号锚定，定位精确 |
| 实现文件 | `tools/builtin.py` | `tools/builtin.py` |

---

## 6. 使用示例

假设 `src/utils.py` 第 20–25 行为：

```python
def fetch_data(url):
    resp = requests.get(url)
    data = resp.json()
    time.sleep(1)
    return data
```

使用 `patch_file_simple` 替换整个函数体：

```json
{
  "path": "src/utils.py",
  "old_string_start": "def fetch_data(url):",
  "old_string_start_line_num": 20,
  "old_string_end": "    return data",
  "old_string_end_line_num": 25,
  "new_string": "def fetch_data(url, timeout=10):\n    resp = requests.get(url, timeout=timeout)\n    resp.raise_for_status()\n    return resp.json()"
}
```

---

## 7. 实现位置

| 文件 | 说明 |
|------|------|
| `src/mini_agent/tools/builtin.py` | 工具实现，`@tool(name="patch_file_simple", ...)` 装饰器注册 |
