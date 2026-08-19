# 数据提取

## 提取文本内容

```bash
python src/core/browser_extract.py --port 9333 --tab <id> --mode text --save content.txt
```

默认输出 20000 字符，可通过 `--max-chars` 调整。

## 提取 HTML 内容

```bash
python src/core/browser_extract.py --port 9333 --tab <id> --mode html --save page.html
```

## 提取可交互元素

```bash
python src/core/browser_extract.py --port 9333 --tab <id> --mode elements --save elements.json
```

输出格式：
```json
[
  {
    "index": 0,
    "tag": "a",
    "text": "Click me",
    "selector": "a.btn-primary",
    "rect": {"x": 100, "y": 200, "width": 100, "height": 30},
    "inViewport": true
  }
]
```

## 提取表单数据

```bash
python src/core/browser_extract.py --port 9333 --tab <id> --mode forms --save forms.json
```

## 提取链接

```bash
python src/core/browser_extract.py --port 9333 --tab <id> --mode links --save links.json
```

## 提取元数据

```bash
python src/core/browser_extract.py --port 9333 --tab <id> --mode meta --save meta.json
```

## 表格数据提取

```bash
# 提取表格内容
python src/core/browser_extract.py --port 9333 --tab <id> --mode elements --selector "table.data-grid" --save table.json

# 或使用 JS 提取
python src/core/browser_console.py --port 9333 --tab <id> --eval "Array.from(document.querySelectorAll('table tr')).map(row => Array.from(row.querySelectorAll('td')).map(cell => cell.innerText).join('|'))"
```

## JSON/API 响应提取

```bash
# 提取页面中的 JSON 数据
python src/core/browser_console.py --port 9333 --tab <id> --eval "window.__INITIAL_STATE__ || window.__DATA__"

# 或从特定元素提取
python src/core/browser_extract.py --port 9333 --tab <id> --mode text --selector ".json-data" --save data.json
```

## 列表数据提取

```bash
# 提取列表项
python src/core/browser_extract.py --port 9333 --tab <id> --mode elements --selector ".list-item" --save items.json

# 提取标题和链接
python src/core/browser_console.py --port 9333 --tab <id> --eval "Array.from(document.querySelectorAll('.list-item a')).map(a => ({title: a.innerText, url: a.href}))"
```

## 注意事项

1. **截断处理**：大页面注意 `--max-chars` 限制
2. **完整内容**：需要完整内容时使用 `--save` 写文件
3. **动态内容**：SPA 页面需等待网络空闲后再提取
4. **选择器**：使用具体选择器提高提取精度
