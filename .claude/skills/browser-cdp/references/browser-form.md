# 浏览器表单自动化

本模块提供复杂的表单自动化处理能力，支持多步骤表单、动态表单、文件上传、表单验证等场景。

## 快速开始

### 填写单个字段

```bash
# 通过选择器填写
python src/core/browser_form.py --tab <id> --fill-selector "input[name='username']" --text "john"

# 填写密码
python src/core/browser_form.py --tab <id> --fill-selector "input[name='password']" --text "secret"

# 选择下拉框
python src/core/browser_form.py --tab <id> --fill-selector "select[name='country']" --text "CN"
```

### 文件上传

```bash
python src/core/browser_form.py --tab <id> --upload-file --fill-selector "input[type='file']" --file "/path/to/document.pdf"
```

### 提交表单

```bash
# 提交第一个表单
python src/core/browser_form.py --tab <id> --submit-form

# 提交特定表单
python src/core/browser_form.py --tab <id> --submit-form --submit-selector "button[type='submit']"

# 提交后等待页面变化
python src/core/browser_form.py --tab <id> --submit-form --wait-for networkidle

# 提交后等待 URL 包含特定字符串
python src/core/browser_form.py --tab <id> --submit-form --wait-url-contains "success"
```

### 保存和恢复表单状态

```bash
# 保存当前表单状态
python src/core/browser_form.py --tab <id> --save-form --out saved_form.json

# 恢复表单状态
python src/core/browser_form.py --tab <id> --restore-form --in saved_form.json
```

### 表单验证

```bash
# 验证表单
python src/core/browser_form.py --tab <id> --validate-form

# 验证特定表单
python src/core/browser_form.py --tab <id> --validate-form --validate-selector "#login-form"
```

## 批量表单填写

支持从 JSON 文件批量填写表单：

```json
{
  "fields": [
    {"selector": "input[name='username']", "value": "john"},
    {"selector": "input[name='password']", "value": "secret", "type": "password"},
    {"selector": "input[name='agree']", "value": true, "type": "checkbox"},
    {"selector": "select[name='country']", "value": "CN"},
    {"selector": "input[type='file']", "value": "/path/to/file.pdf", "type": "file"},
    {"selector": "textarea[name='bio']", "value": "Hello world"}
  ],
  "submit": {"selector": "button[type='submit']"},
  "wait_for": "networkidle",
  "wait_url_contains": "success"
}
```

```bash
python src/core/browser_form.py --tab <id> --fill-form form_def.json
```

## 动态表单处理

### AJAX 加载的下拉框

```python
from src.core.browser_form import fill_field
from src.core.smart_wait import SmartWait

# 等待下拉框选项加载
smart_wait = SmartWait(session)
await smart_wait.wait_for("networkidle", timeout=10)

# 填写下拉框
result = fill_field(session, "select[name='city']", "Beijing")
```

### 动态表单验证

```python
from src.core.browser_form import validate_form

result = validate_form(session)
if not result.get("valid"):
    print(f"验证失败: {result.get('message')}")
    print(f"缺少必填字段: {result.get('missingRequired')}")
```

## 多步骤表单

```python
from src.core.browser_form import fill_form, submit_form, save_form_state

# 步骤 1: 填写基本信息
step1_form = {
    "fields": [
        {"selector": "input[name='name']", "value": "John"},
        {"selector": "input[name='email']", "value": "john@example.com"}
    ]
}
fill_form(session, step1_form)
submit_form(session, wait_for="selector", timeout=10)

# 保存进度
save_form_state(session, selector="#step1-form", out="step1_state.json")

# 步骤 2: 填写详细信息
step2_form = {
    "fields": [
        {"selector": "input[name='phone']", "value": "1234567890"},
        {"selector": "input[name='address']", "value": "123 Main St"}
    ]
}
fill_form(session, step2_form)
submit_form(session, wait_for="networkidle")
```

## API 参考

### fill_field(session, selector, value, field_type=None)

填写单个表单字段。

**参数：**
- `session`: CDP 会话
- `selector`: CSS 选择器
- `value`: 要填写的值
- `field_type`: 字段类型（可选，自动检测）

**返回：**
```python
{
    "filled": True,
    "type": "text",  # text, checkbox, select, file
    "error": None
}
```

### upload_file(session, selector, file_path)

上传文件到文件输入框。

**参数：**
- `session`: CDP 会话
- `selector`: CSS 选择器
- `file_path`: 文件路径

**返回：**
```python
{
    "uploaded": True,
    "path": "/absolute/path/to/file.pdf"
}
```

### fill_form(session, form_def)

填写完整表单。

**参数：**
- `session`: CDP 会话
- `form_def`: 表单定义字典

**返回：**
```python
{
    "fields": [
        {"selector": "input[name='username']", "result": {...}}
    ],
    "errors": []
}
```

### submit_form(session, selector=None, wait_for=None, timeout=30.0)

提交表单并等待结果。

**参数：**
- `session`: CDP 会话
- `selector`: 提交按钮选择器（可选）
- `wait_for`: 等待策略（可选）
- `timeout`: 超时时间（秒）

**返回：**
```python
{
    "submitted": True,
    "type": "js"  # js, button
}
```

### save_form_state(session, selector=None)

保存当前表单状态。

**参数：**
- `session`: CDP 会话
- `selector`: 表单选择器（可选）

**返回：**
```python
{
    "action": "/submit",
    "method": "POST",
    "fields": [
        {
            "name": "username",
            "type": "text",
            "value": "john",
            "checked": None,
            "options": None
        }
    ]
}
```

### restore_form_state(session, form_state)

恢复表单状态。

**参数：**
- `session`: CDP 会话
- `form_state`: 表单状态字典

**返回：**
```python
{
    "fields": [...],
    "errors": []
}
```

### validate_form(session, selector=None)

验证表单。

**参数：**
- `session`: CDP 会话
- `selector`: 表单选择器（可选）

**返回：**
```python
{
    "valid": True,
    "message": "",
    "missingRequired": []
}
```

## 最佳实践

1. **动态表单**：填写前使用 `SmartWait` 等待 AJAX 加载完成
2. **文件上传**：优先使用 CDP 的 `Input.uploadFile`，失败时回退到 JS 方式
3. **表单验证**：提交前调用 `validate_form` 检查必填字段
4. **进度保存**：多步骤表单使用 `save_form_state` 保存中间状态
5. **错误处理**：检查返回结果中的 `errors` 字段

## 已知限制

1. **文件上传**：CDP 的 `Input.uploadFile` 在某些 Chrome 版本可能不可用，会回退到 JS 方式
2. **复杂验证**：自定义验证逻辑可能需要额外的 JS 处理
3. **Shadow DOM**：深层嵌套的 Shadow DOM 可能影响选择器匹配
