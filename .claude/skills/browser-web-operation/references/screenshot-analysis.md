# 截图分析

## 基础截图

```bash
# 可视区域截图
python src/core/browser_screenshot.py --port 9333 --tab <id> --out shot.png

# 整页截图
python src/core/browser_screenshot.py --port 9333 --tab <id> --out full.png --full-page

# 带编号标注的截图
python src/core/browser_screenshot.py --port 9333 --tab <id> --out annotated.png --annotate
```

## 元素级截图

```bash
# 截取指定编号元素
python src/core/browser_screenshot.py --port 9333 --tab <id> --out element.png --element-index 3
```

## 截图参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--out` | 输出文件路径 | 必填 |
| `--full-page` | 整页截图 | false |
| `--annotate` | 编号标注可交互元素 | false |
| `--element-index` | 只截取指定元素 | null |
| `--timeout` | 截图超时（秒） | 60 |

## 标注模式输出

使用 `--annotate` 时，会同时生成 `<out>.elements.json` 文件：

```json
[
  {
    "index": 0,
    "tag": "a",
    "text": "登录",
    "selector": "a.login-btn",
    "rect": {"x": 800, "y": 20, "width": 80, "height": 36},
    "inViewport": true
  },
  {
    "index": 1,
    "tag": "input",
    "text": "",
    "selector": "input[name='username']",
    "rect": {"x": 300, "y": 150, "width": 200, "height": 36},
    "inViewport": true
  }
]
```

## 使用流程

### 流程 1：看图点击

```bash
# 1. 截图并标注
python src/core/browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate

# 2. 查看元素编号
python src/core/browser_extract.py --port 9333 --tab <id> --mode elements

# 3. 根据截图编号点击
python src/core/browser_input.py --port 9333 --tab <id> --click-index 3
```

### 流程 2：视觉验证

```bash
# 1. 执行操作前截图
python src/core/browser_screenshot.py --port 9333 --tab <id> --out before.png

# 2. 执行操作
python src/core/browser_input.py --port 9333 --tab <id> --click-index 5

# 3. 执行操作后截图
python src/core/browser_screenshot.py --port 9333 --tab <id> --out after.png

# 4. 对比前后截图验证结果
```

## 注意事项

1. **DPR 处理**：`Page.captureScreenshot` 的 `clip` 坐标是 CSS 像素，不需要乘 DPR
2. **整页截图**：大页面整页截图可能较慢，建议分段截图
3. **标注编号**：页面变化后需重新截图，编号会重新生成
4. **文件路径**：优先使用绝对路径输出截图
