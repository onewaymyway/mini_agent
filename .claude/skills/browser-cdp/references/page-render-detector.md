# 页面渲染完成检测模块（page_render_detector.py）

> 多维度渲染状态检测：DOM 变化 + 内容哈希 + 字体加载 + 动画完成。

---

## 1. 核心功能

| 功能 | 方法 | 说明 |
|------|------|------|
| 组合检测 | `await wait_for_ready(timeout, require_all)` | DOM+字体+图片综合检测 |
| DOM稳定 | `await wait_for_dom_stable(timeout, stable_samples)` | 连续N次快照无变化 |
| 内容稳定 | `await wait_for_content_stable(selector, timeout)` | 指定元素内容稳定 |
| 字体加载 | `await wait_for_fonts_loaded(timeout)` | 等待自定义字体加载完成 |
| 图片加载 | `await wait_for_images_loaded(timeout)` | 等待所有图片加载完成 |
| 动画检测 | `await wait_for_animations_done(timeout)` | 等待CSS动画/过渡完成 |
| 网络空闲 | `await wait_for_network_idle(idle_sec, timeout)` | 委托 NetworkIdleDetector |
| 停止监听 | `await stop()` | 停止所有监听器 |

---

## 2. 检测策略

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| `"dom"` | 仅检测 DOM 变化稳定 | 纯静态页面 |
| `"network"` | 仅检测网络空闲 | SPA 应用 |
| `"visual"` | 仅检测动画/过渡完成 | 动画丰富的页面 |
| `"auto"` | 自动选择最优策略 | 通用场景（推荐） |
| `"stable"` | DOM+网络+内容综合稳定 | 复杂动态页面 |
| `"comprehensive"` | 全维度检测（最严格） | 关键页面抓取 |

---

## 3. 配置选项

| 参数 | 默认 | 说明 |
|------|------|------|
| `timeout` | 30s | 总体超时时间 |
| `strategy` | "auto" | 检测策略 |
| `require_all` | True | 是否要求所有子检测都通过 |
| `dom_stable_samples` | 3 | DOM稳定所需连续快照数 |
| `content_hash_window` | 5s | 内容哈希比对窗口 |
| `idle_seconds` | 0.5s | 网络空闲阈值 |---

## 4. RenderResult 数据结构

```python
from src.core.page_render_detector import RenderResult

# 检测结果包含：
result.success        # bool - 是否检测通过
result.elapsed        # float - 耗时（秒）
result.strategy       # str - 使用的策略
result.details        # dict - 详细信息
result.dom_changes    # int - DOM 变化次数
result.content_hash   # str - 内容哈希（前16位）
result.fonts_loaded   # int - 已加载字体数
result.images_loaded  # int - 已加载图片数
result.animations_done # bool - 动画是否完成
```

---

## 5. 快速使用

```python
from src.core.page_render_detector import (
    PageRenderDetector,
    RenderConfig,
    create_render_detector,
    wait_for_page_ready,
)

# 方式一：工厂函数（推荐）
detector = create_render_detector(session, timeout=30, strategy="auto")
result = await detector.wait_for_ready()

# 方式二：便捷函数
result = await wait_for_page_ready(session, timeout=30, strategy="stable")

# 方式三：自定义配置
config = RenderConfig(
    timeout=20,
    strategy="comprehensive",
    require_all=True,
    dom_stable_samples=5,
)
detector = PageRenderDetector(session, config)
result = await detector.wait_for_ready()
```

---

## 6. 组合使用示例

```python
from src.core.explicit_wait import ExplicitWait
from src.core.network_idle_detector import NetworkIdleConfig
from src.core.page_render_detector import wait_for_page_ready

# 完整流程：等待网络空闲 → 等待 DOM 稳定 → 等待内容渲染
result_net = await wait_for_network_idle(session, idle_seconds=1.0, timeout=30)
if result_net.idle:
    result_render = await wait_for_page_ready(session, timeout=15, strategy="stable")
    if result_render.success:
        print(f"页面完全就绪，耗时 {result_render.elapsed:.2f}s")
```
