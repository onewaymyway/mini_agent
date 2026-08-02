# CDP 反检测优化指南

> 本文件介绍如何通过指纹伪装、行为模拟、请求头管理等技术，提升对高防护网站的访问成功率。

---

## 1. 核心原理

现代网站通过多种手段检测自动化浏览器：

| 检测维度 | 检测内容 | 绕过方法 |
|----------|----------|----------|
| **Navigator 属性** | `navigator.webdriver` | CDP Runtime.evaluate 覆盖 |
| **WebGL 指纹** | GPU 渲染器信息 | 伪造 WebGL 返回值 |
| **Canvas 指纹** | 渲染差异 | 注入 Canvas 补丁 |
| **User-Agent** | 浏览器标识 | 启动参数 + 运行时覆盖 |
| **屏幕信息** | 分辨率、DPR | 启动参数设置视口 |
| **插件列表** | 浏览器插件 | 伪造 plugins 对象 |
| **权限查询** | 通知/定位权限 | 拦截 Permissions.query |
| **时序特征** | 操作间隔规律性 | 随机延迟 + 贝塞尔曲线 |

---

## 2. 快速开始

### 2.1 启动带反检测的浏览器

```bash
# 基础用法（自动选择默认指纹）
python browser_launch.py --dedicated --name anti_test --anti-detection

# 指定 User-Agent 类别
python browser_launch.py --dedicated --name anti_test --anti-detection --ua-category chrome_mac

# 使用固定种子（可重现的指纹）
python browser_launch.py --dedicated --name anti_test --anti-detection --seed 42

# 无头模式 + 反检测
python browser_launch.py --dedicated --name scraper --anti-detection --headless --seed 123
```

### 2.2 Python API 使用

```python
from anti_detection import (
    create_anti_detection_config,
    generate_launch_args,
    get_cdp_commands,
    HumanBehaviorSimulator,
)

# 1. 创建配置
config, behavior_config = create_anti_detection_config(
    ua_category="chrome_win",  # chrome_win/chrome_mac/chrome_linux/edge_win
    seed=42,                   # 固定种子用于可重现测试
)

# 2. 生成启动参数
args = generate_launch_args(config, headless=False)
print(f"User-Agent: {config.user_agent}")
print(f"启动参数: {' '.join(args[:5])}...")

# 3. 生成 CDP 命令（页面加载后执行）
cdp_commands = get_cdp_commands(config)
for cmd in cdp_commands:
    print(f"执行: {cmd['method']}")

# 4. 行为模拟
behavior = HumanBehaviorSimulator(behavior_config)
pause = behavior.random_pause(2, 5)  # 随机暂停 2-5 秒
delays = behavior.random_type_delay("hello")  # 打字延迟
steps, scroll_delays = behavior.random_scroll_steps()  # 滚动步数
```

---

## 3. 配置详解

### 3.1 AntiDetectionConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ua_category` | str | "chrome_win" | User-Agent 类别 |
| `custom_ua` | str | None | 自定义 User-Agent |
| `viewport` | dict | 自动选择 | 视口配置 |
| `resolution_index` | int | 0 | 分辨率索引 (0-5) |
| `languages` | list | 自动选择 | 语言列表 |
| `webgl_renderer` | str | 自动选择 | WebGL 渲染器 |
| `platform` | str | "Win32" | 平台标识 |
| `seed` | int | None | 随机种子 |

**可用 UA 类别**:
- `chrome_win`: Chrome on Windows
- `chrome_mac`: Chrome on macOS
- `chrome_linux`: Chrome on Linux
- `edge_win`: Edge on Windows

**可用分辨率**:
| 索引 | 分辨率 | DPR |
|------|--------|-----|
| 0 | 1920x1080 | 1 |
| 1 | 1366x768 | 1 |
| 2 | 1440x900 | 2 |
| 3 | 1536x864 | 2 |
| 4 | 1280x720 | 1 |
| 5 | 1600x900 | 1 |

### 3.2 BehaviorConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mouse_move_min_delay` | float | 0.01 | 鼠标移动最小延迟 (秒) |
| `mouse_move_max_delay` | float | 0.05 | 鼠标移动最大延迟 (秒) |
| `mouse_jitter` | float | 2.0 | 鼠标抖动幅度 (像素) |
| `click_hold_min` | float | 0.05 | 点击保持最小时间 (秒) |
| `click_hold_max` | float | 0.15 | 点击保持最大时间 (秒) |
| `type_min_delay` | float | 0.02 | 打字最小延迟 (秒) |
| `type_max_delay` | float | 0.08 | 打字最大延迟 (秒) |
| `type_error_rate` | float | 0.02 | 打字错误率 |
| `scroll_min_steps` | int | 3 | 最小滚动步数 |
| `scroll_max_steps` | int | 8 | 最大滚动步数 |
| `page_min_time` | float | 2.0 | 最小页面停留时间 (秒) |
| `page_max_time` | float | 8.0 | 最大页面停留时间 (秒) |

---

## 4. CDP 命令说明

### 4.1 Navigator 覆盖

```javascript
// 隐藏 webdriver 标志
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 伪造 plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => ({ length: 2, item: (i) => plugins[i], keys: () => [0, 1] })
});

// 覆盖 languages/platform/hardwareConcurrency
```

### 4.2 WebGL 覆盖

```javascript
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return '伪造的渲染器名称';
    if (param === 37446) return 'Intel Corporation';
    return getParameter.call(this, param);
};
```

### 4.3 Canvas 指纹保护

```javascript
// 注入 Canvas 补丁，使指纹与真实浏览器一致
const canvas = document.createElement('canvas');
const gl = canvas.getContext('webgl');
// ... 详细实现见 anti_detection.py
```

### 4.4 Chrome 对象覆盖

```javascript
// 伪造 window.chrome 对象
Object.defineProperty(window, 'chrome', {
    value: {
        app: { isInstalled: false },
        runtime: { OnInstalledReason: {...} },
        loadTimes: function() {},
        csi: function() {}
    }
});
```

---

## 5. 行为模拟

### 5.1 鼠标轨迹

```python
# 生成贝塞尔曲线鼠标路径
path = behavior.random_mouse_path(
    start=(100, 100),
    end=(500, 300),
    steps=10
)
# 返回: [(100, 100), (135, 112), (178, 131), ..., (500, 300)]
```

### 5.2 打字行为

```python
# 为每个字符生成随机延迟
delays = behavior.random_type_delay("Hello World")
# 返回: [0.03, 0.02, 0.05, ..., 0.03] (每个字符一个延迟)
```

### 5.3 滚动行为

```python
# 生成随机滚动步数和延迟
steps, delays = behavior.random_scroll_steps()
# steps: 5, delays: [0.08, 0.12, 0.05, 0.09, 0.07]
```

### 5.4 页面停留

```python
# 随机暂停，模拟人类阅读时间
pause = behavior.random_pause(min_time=2, max_time=5)
# 实际暂停 2-5 秒之间的随机时间
```

---

## 6. 检测脚本

内置检测脚本用于验证反检测效果：

```python
from anti_detection import run_all_detections

# 运行所有检测
results = run_all_detections(session)

# 输出示例:
# {
#   "webdriver": {"webdriver": undefined, "chrome": true, ...},
#   "webgl": {"renderer": "ANGLE (Intel, ...)", "vendor": "Intel"},
#   "canvas": "data:image/png;base64,...",
#   "permissions": {"notification": "denied", ...}
# }
```

---

## 7. 最佳实践

### 7.1 高防护网站策略

```bash
# 1. 使用固定种子保持指纹一致
python browser_launch.py --dedicated --name secure_site --anti-detection --seed 42

# 2. 选择常见分辨率和 UA
python browser_launch.py --dedicated --name secure_site --anti-detection --ua-category chrome_win --seed 42

# 3. 启用无头模式（某些网站对无头更宽松）
python browser_launch.py --dedicated --name scraper --anti-detection --headless --seed 42
```

### 7.2 批量抓取策略

```python
from anti_detection import create_anti_detection_config, generate_launch_args

# 为每个任务生成不同指纹
for i in range(10):
    config, _ = create_anti_detection_config(seed=i * 1000)
    args = generate_launch_args(config, headless=True)
    # 启动浏览器...
```

### 7.3 与 browser_input 集成

```python
from anti_detection import HumanBehaviorSimulator
from browser_input import click, type_text

behavior = HumanBehaviorSimulator()

# 带随机延迟的点击
def smart_click(session, x, y):
    # 随机鼠标移动路径
    path = behavior.random_mouse_path((0, 0), (x, y))
    for px, py in path:
        # 移动鼠标...
        time.sleep(behavior.get_mouse_move_delay())
    # 点击
    time.sleep(behavior.get_click_hold_time())
    # 执行点击...
```

---

## 8. 常见问题

### Q1: 为什么启用了反检测还是被识别？

**可能原因**:
1. 网站使用了更高级的检测（如行为分析、IP 信誉）
2. CDP 命令执行时机不对（应在页面加载前执行）
3. 指纹与其他检测点不一致（如 UA 与分辨率不匹配）

**解决方案**:
```python
# 确保 CDP 命令在导航前执行
session.send("Page.enable")
for cmd in get_cdp_commands(config):
    session.send(cmd["method"], cmd["params"])
# 然后导航
session.send("Page.navigate", {"url": target_url})
```

### Q2: 如何验证反检测是否生效？

```python
from anti_detection import check_detection

# 检测 webdriver 属性
result = check_detection(session, "webdriver")
print(result)  # {"webdriver": undefined, ...}

# 检测 WebGL
result = check_detection(session, "webgl")
print(result)  # {"renderer": "...", "vendor": "..."}
```

### Q3: 无头模式会影响反检测效果吗？

无头模式本身不会降低反检测效果，但某些网站对无头浏览器有更严格的检测。建议：
- 优先使用 `--headless=new`（新版无头模式更难检测）
- 配合反检测参数使用
- 必要时切换到有头模式测试

---

## 9. 性能优化

### 9.1 指纹缓存

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_cached_config(seed: int):
    return create_anti_detection_config(seed=seed)
```

### 9.2 批量启动优化

```python
import asyncio
from anti_detection import create_anti_detection_config, generate_launch_args

async def batch_launch(sources: list[str], seeds: list[int]):
    tasks = []
    for source, seed in zip(sources, seeds):
        config, _ = create_anti_detection_config(seed=seed)
        args = generate_launch_args(config, headless=True)
        tasks.append(start_browser(args))
    return await asyncio.gather(*tasks)
```

---

> **注意**: 反检测技术应仅用于合法的自动化测试和数据采集场景，请勿用于绕过安全验证或从事非法活动。
