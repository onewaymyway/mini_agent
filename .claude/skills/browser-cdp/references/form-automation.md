# 表单自动化

## 单字段填写

```bash
python src/core/browser_form.py --port 9333 --tab <id> --fill-selector "input[name='username']" --text "john"
```

## 文件上传

```bash
python src/core/browser_form.py --port 9333 --tab <id> --upload-file --fill-selector "input[type='file']" --file "/path/to/file.pdf"
```

## 批量填写表单

```bash
python src/core/browser_form.py --port 9333 --tab <id> --fill-form form_def.json
```

`form_def.json` 格式：
```json
{
  "fields": [
    {"selector": "input[name='username']", "value": "john"},
    {"selector": "input[name='email']", "value": "john@example.com"},
    {"selector": "textarea[name='message']", "value": "Hello World"}
  ]
}
```

## 提交表单

```bash
python src/core/browser_form.py --port 9333 --tab <id> --submit-form --wait-for networkidle
```

## 保存/恢复表单状态

```bash
# 保存当前表单状态
python src/core/browser_form.py --port 9333 --tab <id> --save-form --out saved.json

# 恢复表单状态
python src/core/browser_form.py --port 9333 --tab <id> --restore-form --in saved.json
```

## 多步骤表单

```bash
# 步骤 1：填写基本信息
python src/core/browser_form.py --port 9333 --tab <id> --fill-selector "input[name='name']" --text "John"
python src/core/browser_form.py --port 9333 --tab <id> --fill-selector "input[name='email']" --text "john@example.com"
python src/core/browser_form.py --port 9333 --tab <id> --submit-form --wait-for selector --wait-selector ".step-2"

# 步骤 2：填写详细信息
python src/core/browser_form.py --port 9333 --tab <id> --fill-selector "input[name='address']" --text "123 Main St"
python src/core/browser_form.py --port 9333 --tab <id> --fill-selector "input[name='phone']" --text "123-456-7890"
python src/core/browser_form.py --port 9333 --tab <id> --submit-form --wait-for networkidle
```

## 动态表单处理

```bash
# 等待动态内容加载
python src/core/browser_nav.py --port 9333 --tab <id> --wait-for networkidle --timeout 30

# 填写表单
python src/core/browser_form.py --port 9333 --tab <id> --fill-form form_def.json

# 提交并等待验证
python src/core/browser_form.py --port 9333 --tab <id> --submit-form --wait-for stable

# 检查验证结果
python src/core/browser_extract.py --port 9333 --tab <id> --mode elements --save validation.json
```

## 表单验证处理

```bash
# 提交表单
python src/core/browser_form.py --port 9333 --tab <id> --submit-form

# 等待错误消息出现
python src/core/browser_nav.py --port 9333 --tab <id> --wait-selector ".error-message" --timeout 5

# 提取错误信息
python src/core/browser_extract.py --port 9333 --tab <id> --mode text --selector ".error-message" --save errors.txt

# 修正后重新提交
python src/core/browser_form.py --port 9333 --tab <id> --fill-selector "input[name='email']" --text "correct@example.com"
python src/core/browser_form.py --port 9333 --tab <id> --submit-form
```

## 注意事项

1. **文件路径**：使用绝对路径避免路径问题
2. **表单状态**：复杂表单建议先保存状态，便于调试
3. **动态内容**：AJAX 表单需等待网络空闲后再操作
4. **验证错误**：提交后检查错误消息，及时修正
