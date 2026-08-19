# 元素交互详解

## 点击操作

### 通过编号点击

```bash
python src/core/browser_input.py --port 9333 --tab <id> --click-index 3
```

编号来自截图标注或元素扫描结果。

### 通过坐标点击

```bash
python src/core/browser_input.py --port 9333 --tab <id> --click-xy 400 300
```

### 通过选择器点击

```bash
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "button.submit"
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "#login-btn"
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "a[href='/dashboard']"
```

## 输入操作

### 通过编号输入

```bash
python src/core/browser_input.py --port 9333 --tab <id> --type-index 5 --text "hello world"
```

### 通过选择器输入

```bash
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input[name='username']" --text "john"
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "textarea" --text "Long text content"
```

### 清空后输入

```bash
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input[name='search']" --text "new value" --clear-first
```

## 按键操作

```bash
# Enter 键
python src/core/browser_input.py --port 9333 --tab <id> --key Enter

# Tab 键
python src/core/browser_input.py --port 9333 --tab <id> --key Tab

# Escape 键
python src/core/browser_input.py --port 9333 --tab <id> --key Escape

# Backspace 键
python src/core/browser_input.py --port 9333 --tab <id> --key Backspace
```

## 悬停操作

```bash
python src/core/browser_input.py --port 9333 --tab <id> --hover-index 2
```

## 滚动操作

### 滚动到指定元素

```bash
python src/core/browser_input.py --port 9333 --tab <id> --scroll-to-index 8
```

### 按像素滚动

```bash
# 向下滚动 600 像素
python src/core/browser_input.py --port 9333 --tab <id> --scroll-by 0 600

# 向上滚动 300 像素
python src/core/browser_input.py --port 9333 --tab <id> --scroll-by 0 -300
```

## 组合操作示例

### 搜索流程

```bash
# 1. 点击搜索框
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "input.search-box"

# 2. 输入关键词
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input.search-box" --text "关键词"

# 3. 按 Enter 提交
python src/core/browser_input.py --port 9333 --tab <id> --key Enter

# 4. 等待结果加载
python src/core/browser_nav.py --port 9333 --tab <id> --wait-selector ".results" --timeout 10
```

### 表单填写流程

```bash
# 1. 点击用户名输入框
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "input[name='username']"

# 2. 输入用户名
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input[name='username']" --text "john_doe"

# 3. 点击密码输入框
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "input[name='password']"

# 4. 输入密码
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input[name='password']" --text "secret123"

# 5. 点击登录按钮
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "button[type='submit']"
```

## 注意事项

1. **编号依赖**：元素编号基于当次截图/扫描，页面变化后需重新截图
2. **选择器优先级**：选择器比编号更稳定，推荐优先使用选择器
3. **输入延迟**：默认每字符延迟 20ms，模拟真实打字节奏
4. **清空操作**：`--clear-first` 会全选后删除，兼容各种输入框类型
