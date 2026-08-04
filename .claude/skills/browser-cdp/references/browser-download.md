# 文件下载管理

## 概述

通过 CDP 监听浏览器下载事件，管理文件下载流程。

## 核心功能

### 1. 下载事件监听

```python
# 监听下载开始
Page.downloadWillBegin

# 监听下载进度
Page.downloadProgress
```

### 2. 下载管理命令

```bash
# 查看下载状态
python src/core/browser_download.py --action status

# 获取最新下载
python src/core/browser_download.py --action latest

# 清空下载记录
python src/core/browser_download.py --action clear

# 下载文件
python src/core/browser_download.py --action download --url <url> --filename <name>
```

### 3. 配置下载目录

```bash
# 设置下载目录
python src/core/browser_launch.py --download-dir ./downloads
```

## 使用场景

### 场景1：下载搜索结果附件

```python
# 1. 启动浏览器并配置下载目录
browser_launch --headless --download-dir ./downloads

# 2. 导航到下载页面
browser_nav --url "https://example.com/download"

# 3. 触发下载
browser_input --click "#download-btn"

# 4. 等待下载完成
browser_download --action wait --timeout 60

# 5. 获取下载文件
browser_download --action latest
```

### 场景2：批量下载文件

```python
# 1. 遍历列表并下载
for url in download_urls:
    browser_nav --url url
    browser_input --click ".download-link"
    await wait_for_download()

# 2. 检查下载结果
browser_download --action status
```

## 注意事项

1. **下载目录权限**：确保下载目录有写入权限
2. **文件名冲突**：同名文件会被覆盖，建议添加时间戳
3. **大文件下载**：设置合理的超时时间
4. **断点续传**：部分浏览器支持，需检查 CDP 版本

## 相关命令

- `browser_launch.py` — 启动浏览器时配置下载目录
- `browser_nav.py` — 导航到下载页面
- `browser_input.py` — 点击下载按钮
