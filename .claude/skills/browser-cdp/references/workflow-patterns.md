# 工作流模式

## 多步骤流程

### 登录 → 导航 → 提取流程

```bash
# 步骤 1：启动浏览器
python src/core/browser_launch.py --dedicated --name workflow_session --start-url "https://example.com/login"

# 步骤 2：填写登录表单
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input[name='username']" --text "user"
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input[name='password']" --text "pass"
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "button[type='submit']"

# 步骤 3：等待登录完成
python src/core/browser_nav.py --port 9333 --tab <id> --wait-selector ".user-profile" --timeout 10

# 步骤 4：导航到目标页面
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com/dashboard" --wait-for networkidle

# 步骤 5：提取数据
python src/core/browser_extract.py --port 9333 --tab <id> --mode text --save dashboard.txt

# 步骤 6：截图保存
python src/core/browser_screenshot.py --port 9333 --tab <id> --out dashboard.png --full-page

# 步骤 7：关闭浏览器
python src/core/browser_launch.py --stop-dedicated workflow_session
```

## 条件分支

### 根据页面状态选择操作

```bash
# 检查页面状态
STATUS=$(python src/core/browser_extract.py --port 9333 --tab <id> --mode meta)

# 根据状态执行不同操作
if echo "$STATUS" | grep -q "error"; then
    # 处理错误状态
    python src/core/browser_input.py --port 9333 --tab <id> --click-selector ".retry-btn"
else
    # 正常流程
    python src/core/browser_input.py --port 9333 --tab <id> --click-selector ".next-btn"
fi
```

## 错误处理

### 重试机制

```bash
# 带重试的导航
for i in 1 2 3; do
    if python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com" --wait-for networkidle; then
        echo "[ok] 导航成功"
        break
    else
        echo "[warn] 第 $i 次尝试失败，重试..."
        sleep 2
    fi
done
```

### 超时处理

```bash
# 设置超时
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com" --timeout 30

# 等待元素超时
python src/core/browser_nav.py --port 9333 --tab <id> --wait-selector ".content" --timeout 15
```

## 批量操作

### 多标签页批量导航

```bash
# 批量导航到多个 URL
python src/core/browser_tabs.py --port 9333 --batch-goto "url1,url2,url3"

# 批量截图
python src/core/browser_tabs.py --port 9333 --batch-screenshot --out-dir ./screenshots

# 批量提取
python src/core/browser_tabs.py --port 9333 --batch-extract --mode text --out-dir ./extracted
```

### 循环操作

```bash
# 循环点击下一页
for page in 1 2 3 4 5; do
    python src/core/browser_extract.py --port 9333 --tab <id> --mode text --save page_${page}.txt
    python src/core/browser_input.py --port 9333 --tab <id> --click-selector "a.next-page"
    python src/core/browser_nav.py --port 9333 --tab <id> --wait-selector ".results" --timeout 10
done
```

## 状态保存与恢复

### 保存浏览器状态

```bash
# 保存表单状态
python src/core/browser_form.py --port 9333 --tab <id> --save-form --out state.json

# 保存当前 URL
URL=$(python src/core/browser_extract.py --port 9333 --tab <id> --mode meta | grep -o '"url":"[^"]*"' | cut -d'"' -f4)
echo "$URL" > current_url.txt
```

### 恢复浏览器状态

```bash
# 恢复表单状态
python src/core/browser_form.py --port 9333 --tab <id> --restore-form --in state.json

# 恢复到指定 URL
URL=$(cat current_url.txt)
python src/core/browser_nav.py --port 9333 --tab <id> --goto "$URL" --wait-for stable
```

## 最佳实践

1. **实例管理**：使用固定 `--name` 保持登录态
2. **等待策略**：SPA 页面用 `networkidle`，传统页面用 `stable`
3. **错误处理**：关键操作添加重试和超时
4. **状态保存**：复杂流程保存中间状态
5. **资源释放**：完成后及时关闭浏览器
