# 知乎搜索使用指南（已登录模式）

## 概述

本指南说明如何使用 `browser-cdp` skill 进行知乎真实问题搜索。由于知乎需要登录才能查看搜索结果，我们采用**固定用户数据目录**的方式，实现一次登录、长期有效。

## 核心脚本

| 脚本 | 用途 |
|------|------|
| `launch_zhihu_logged_in.py` | 启动带固定数据目录的浏览器，用于登录知乎 |
| `zhihu_search_with_login.py` | 使用已登录的浏览器执行真实搜索 |

## 使用步骤

### 第一步：首次登录（只需做一次）

```bash
cd .claude/skills/browser-cdp
python launch_zhihu_logged_in.py
```

脚本会：
1. 启动一个专用浏览器实例（端口 9336）
2. 自动打开知乎首页
3. 使用固定的用户数据目录：`.claude/skills/browser-cdp/temp_data/zhihu_logged_in_profile`

**在打开的浏览器中：**
- 扫码或账号登录知乎
- 确认可以正常浏览知乎内容
- **不要关闭浏览器**（或关闭后数据会保留）

### 第二步：执行搜索

#### 单次搜索

```bash
python zhihu_search_with_login.py "影视推荐工具"
```

#### 批量搜索（所有 Agent 方向）

```bash
python zhihu_search_with_login.py --batch
```

#### 自定义参数

```bash
# 指定输出文件
python zhihu_search_with_login.py --batch --output my_results.json

# 指定最大结果数
python zhihu_search_with_login.py "比价工具" --max-results 15

# 使用不同端口（如果 9336 被占用）
python launch_zhihu_logged_in.py --port 9337
python zhihu_search_with_login.py --port 9337 --batch
```

### 第三步：查看结果

搜索结果保存在 `search_results/` 目录下：

```bash
search_results/
└── zhihu_real_questions.json  # 默认输出文件
```

JSON 格式示例：
```json
[
  {
    "content_id": "agent_topic_010",
    "content_title": "全网比价与智能购物决策 Agent",
    "query": "比价工具",
    "question_title": "有什么好用的比价工具或 APP？",
    "question_url": "https://www.zhihu.com/question/264838855"
  },
  ...
]
```

## 后续使用

### 保持登录态

**下次使用时，直接运行搜索脚本即可**，无需重新登录：

```bash
# 如果浏览器还在运行
python zhihu_search_with_login.py --batch

# 如果浏览器已关闭，先重新启动
python launch_zhihu_logged_in.py  # 快速启动，自动保持登录态
python zhihu_search_with_login.py --batch
```

### 清除登录态（重新登录）

如果需要切换账号或清除登录态：

```bash
python launch_zhihu_logged_in.py --reset
```

这会删除用户数据目录，下次启动时需要重新登录。

## 常见问题

### Q: 浏览器启动失败？

**A:** 检查 Chrome/Edge 路径是否正确：
```bash
python launch_zhihu_logged_in.py --browser "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

### Q: 端口被占用？

**A:** 使用不同端口：
```bash
python launch_zhihu_logged_in.py --port 9337
python zhihu_search_with_login.py --port 9337 --batch
```

### Q: 搜索结果为空？

**A:** 可能原因：
1. 未登录知乎 - 运行 `launch_zhihu_logged_in.py` 并手动登录
2. 网络问题 - 检查网络连接
3. 知乎反爬 - 减少搜索频率，增加 `--max-results` 间隔

### Q: 如何知道浏览器是否已登录？

**A:** 运行 `launch_zhihu_logged_in.py` 后，观察浏览器是否显示"登录/注册"按钮：
- 显示"登录/注册" → 未登录，需要手动登录
- 显示用户名/头像 → 已登录，可以直接搜索

## 与 publish-system 集成

将搜索结果用于 publish-system 的匹配测试：

```bash
# 1. 获取真实问题
python zhihu_search_with_login.py --batch --output real_questions.json

# 2. 运行匹配测试（需要修改 test 脚本读取真实数据）
cd ../publish-system
python test_with_real_data.py ../../browser-cdp/search_results/real_questions.json
```

## 技术细节

### 固定用户数据目录

```python
USER_DATA_DIR = SKILL_DIR / "temp_data" / "zhihu_logged_in_profile"
```

这个目录包含：
- Cookie 和登录态
- 浏览器缓存
- 本地存储（LocalStorage）
- 扩展数据

**优点**：一次登录，长期有效  
**注意**：不要手动删除此目录，除非想清除登录态

### 调试端口

默认端口 `9336`，与 `browser-cdp` 其他脚本的默认端口（9222/9333）不冲突，可以同时运行多个实例。

---

**开始使用**：
```bash
# 首次使用
python launch_zhihu_logged_in.py  # 登录知乎
# 新开终端
python zhihu_search_with_login.py --batch
```
