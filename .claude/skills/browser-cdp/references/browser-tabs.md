# 浏览器多标签页管理

本模块提供多标签页管理能力，支持标签页列表、切换、批量操作、标签页组管理等场景。

## 快速开始

### 列出所有标签页

```bash
python src/core/browser_tabs.py --port 9333 --list
```

### 创建新标签页

```bash
python src/core/browser_tabs.py --port 9333 --new "https://example.com"
```

### 切换标签页

```bash
python src/core/browser_tabs.py --port 9333 --activate <target_id>
```

### 关闭标签页

```bash
python src/core/browser_tabs.py --port 9333 --close <target_id>
```

### 关闭所有标签页（保留 N 个）

```bash
python src/core/browser_tabs.py --port 9333 --close-all --keep 1
```

## 批量操作

### 批量导航

```bash
python src/core/browser_tabs.py --port 9333 --batch-goto "https://example1.com,https://example2.com,https://example3.com"
```

### 批量截图

```bash
python src/core/browser_tabs.py --port 9333 --batch-screenshot --out-dir ./screenshots
```

### 批量提取内容

```bash
python src/core/browser_tabs.py --port 9333 --batch-extract --mode text --out-dir ./extracted
```

## 标签页组管理

### 创建标签页组

```bash
python src/core/browser_tabs.py --port 9333 --create-group research --group-tabs '{"tabs": [{"url": "https://example1.com", "title": "Page 1"}, {"url": "https://example2.com", "title": "Page 2"}]}'
```

### 切换到标签页组

```bash
python src/core/browser_tabs.py --port 9333 --switch-group research
```

### 关闭标签页组

```bash
python src/core/browser_tabs.py --port 9333 --close-group research
```

## API 参考

### list_tabs_info(host, port)

列出所有标签页及其状态。

**参数：**
- `host`: 主机地址（默认 127.0.0.1）
- `port`: 调试端口（默认 9222）

**返回：**
```python
[
    {
        "target_id": "ABC123...",
        "url": "https://example.com",
        "title": "Example",
        "type": "page",
        "ws_url": "ws://...",
        "info": {
            "url": "https://example.com",
            "title": "Example",
            "readyState": "complete",
            ...
        }
    }
]
```

### create_tab(url, host, port)

创建新标签页。

**参数：**
- `url`: 目标 URL（默认 about:blank）
- `host`: 主机地址
- `port`: 调试端口

**返回：**
```python
{
    "target_id": "ABC123...",
    "url": "https://example.com",
    "title": "Example",
    "ws_url": "ws://...",
    "created_at": 1234567890.0
}
```

### switch_tab(target_id, host, port)

切换到指定标签页。

**参数：**
- `target_id`: 标签页 ID
- `host`: 主机地址
- `port`: 调试端口

**返回：**
```python
{
    "switched_to": "ABC123...",
    "timestamp": 1234567890.0
}
```

### close_tab_by_id(target_id, host, port)

关闭指定标签页。

**参数：**
- `target_id`: 标签页 ID
- `host`: 主机地址
- `port`: 调试端口

**返回：**
```python
True  # 成功
False  # 失败
```

### close_all_tabs(host, port, keep=0)

关闭所有标签页，保留指定数量。

**参数：**
- `host`: 主机地址
- `port`: 调试端口
- `keep`: 保留的标签页数量（默认 0）

**返回：**
```python
[
    {"target_id": "ABC123...", "url": "...", "title": "..."}
]
```

### batch_goto(urls, host, port)

批量导航到多个 URL（每个 URL 一个标签页）。

**参数：**
- `urls`: URL 列表
- `host`: 主机地址
- `port`: 调试端口

**返回：**
```python
[
    {"index": 0, "url": "https://example1.com", "target_id": "ABC123...", "status": "created"}
]
```

### batch_screenshot(host, port, out_dir, full_page=False)

批量截图所有标签页。

**参数：**
- `host`: 主机地址
- `port`: 调试端口
- `out_dir`: 输出目录
- `full_page`: 是否整页截图

**返回：**
```python
[
    {"target_id": "ABC123...", "url": "...", "screenshot": "/path/to/screenshot.png", "status": "ok"}
]
```

### batch_extract(host, port, mode, out_dir)

批量提取所有标签页内容。

**参数：**
- `host`: 主机地址
- `port`: 调试端口
- `mode`: 提取模式（html/text/elements/forms/links/meta）
- `out_dir`: 输出目录

**返回：**
```python
[
    {"target_id": "ABC123...", "url": "...", "content_path": "/path/to/content.txt", "status": "ok"}
]
```

### create_tab_group(name, tabs, host, port)

创建标签页组。

**参数：**
- `name`: 组名称
- `tabs`: 标签页定义列表
- `host`: 主机地址
- `port`: 调试端口

**返回：**
```python
{
    "name": "research",
    "tabs": [
        {"target_id": "ABC123...", "url": "https://example1.com", "title": "Page 1"}
    ],
    "created_at": 1234567890.0
}
```

### switch_to_group(group, host, port)

切换到标签页组中的第一个标签页。

**参数：**
- `group`: 标签页组字典
- `host`: 主机地址
- `port`: 调试端口

**返回：**
```python
{
    "switched_to": "ABC123...",
    "group": "research"
}
```

### close_group(group, host, port)

关闭标签页组中的所有标签页。

**参数：**
- `group`: 标签页组字典
- `host`: 主机地址
- `port`: 调试端口

**返回：**
```python
[
    {"target_id": "ABC123...", "url": "...", "title": "..."}
]
```

## 最佳实践

1. **批量操作**：使用 `batch_goto` 一次性创建多个标签页，避免逐个创建
2. **标签页组**：使用标签页组管理相关标签页，方便批量操作
3. **清理**：任务完成后使用 `close_all_tabs` 清理标签页，避免资源泄漏
4. **保留**：使用 `--keep` 参数保留必要的标签页（如主页面）
5. **错误处理**：批量操作时检查每个标签页的状态，处理失败情况

## 已知限制

1. **标签页数量**：Chrome 有标签页数量限制（通常 1000+），大量标签页可能影响性能
2. **内存使用**：每个标签页都会占用内存，注意控制标签页数量
3. **网络请求**：批量导航时注意速率控制，避免触发反爬机制
4. **标签页组**：标签页组定义需要手动管理，没有自动持久化
