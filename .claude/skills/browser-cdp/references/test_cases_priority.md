# Browser CDP 技能测试用例优先级分组文档

本文件将所有测试用例按优先级分组（P0-P2），并标注依赖的浏览器环境、前置条件和预期结果。

## 优先级说明

- **P0 (Critical)**: 核心功能测试，必须通过，阻塞发布
- **P1 (High)**: 重要功能测试，建议修复，影响用户体验
- **P2 (Medium)**: 辅助功能测试，可选修复，不影响核心功能

---

## P0 级测试用例（核心功能）

### 1. browser_launch.py - 专用实例管理

| 测试用例 | 描述 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|------------|----------|----------|
| test_dedicated_port_fallback | 当 registry 中找不到 name 但显式指定端口时，复用该端口 | Chrome/Edge >= 90, remote-debugging-port 可用 | 无 | spawn_browser 不应被调用，registry 应被更新 |
| test_detect_running_debug_ports | 检测系统中已运行的调试浏览器端口 | Chrome/Edge 在运行中 | 无 | 返回正确的端口列表，不抛异常 |
| test_new_tab_creation | 创建新 Tab 并获取 tab ID | Chrome/Edge >= 90 | 浏览器实例已启动 | 返回有效的 tab ID，tab 状态正常 |
| test_list_tabs | 列出所有打开的 Tab | Chrome/Edge >= 90 | 浏览器实例已启动 | 返回包含所有 tab 信息的列表 |

### 2. browser_nav.py - 页面导航

| 测试用例 | 描述 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|------------|----------|----------|
| test_goto_url | 导航到指定 URL | Chrome/Edge >= 90 | 浏览器实例已启动 | 返回 True，URL 正确加载 |
| test_wait_element | 等待元素出现 | Chrome/Edge >= 90 | 页面已加载 | 元素出现时返回 True，超时后返回 False |
| test_go_back | 后退到上一页 | Chrome/Edge >= 90 | 至少有两个历史页面 | URL 返回到前一个页面 |
| test_go_forward | 前进到下一页 | Chrome/Edge >= 90 | 有前进历史 | URL 前进到下一个页面 |
| test_refresh | 刷新当前页面 | Chrome/Edge >= 90 | 页面已加载 | 页面重新加载，URL 不变 |

### 3. browser_extract.py - 内容抓取

| 测试用例 | 描述 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|------------|----------|----------|
| test_extract_text | 提取页面纯文本内容 | Chrome/Edge >= 90 | 页面已加载 | 返回非空文本内容，包含主要正文 |
| test_extract_html | 提取页面 HTML 内容 | Chrome/Edge >= 90 | 页面已加载 | 返回完整的 HTML 字符串 |
| test_extract_links | 提取页面中的所有链接 | Chrome/Edge >= 90 | 页面已加载 | 返回包含所有链接的列表 |
| test_extract_elements | 提取特定 CSS 选择器的元素 | Chrome/Edge >= 90 | 页面已加载 | 返回匹配元素的列表 |
| test_extract_meta | 提取页面元数据（title, description, H1） | Chrome/Edge >= 90 | 页面已加载 | 返回包含 title、description、h1 的字典 |

### 4. browser_input.py - 模拟交互

| 测试用例 | 描述 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|------------|----------|----------|
| test_type_selector | 向指定选择器输入文本 | Chrome/Edge >= 90 | 页面已加载且元素可见 | 输入框值被设置 |
| test_click_selector | 点击指定选择器元素 | Chrome/Edge >= 90 | 页面已加载且元素可点击 | 触发点击事件，可能改变页面状态 |
| test_scroll | 滚动页面到指定位置 | Chrome/Edge >= 90 | 页面可滚动 | 页面滚动到指定位置 |
| test_get_value | 获取输入框的值 | Chrome/Edge >= 90 | 输入框存在 | 返回输入框的当前值 |
| test_select | 选择下拉选项 | Chrome/Edge >= 90 | 下拉框存在且可交互 | 选项被选中 |

### 5. browser_screenshot.py - 截图功能

| 测试用例 | 描述 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|------------|----------|----------|
| test_capture_full_page | 截取整页截图 | Chrome/Edge >= 90, Pillow 安装 | 页面已加载 | 返回有效的截图文件路径 |
| test_capture_annotate | 带编号标注的截图 | Chrome/Edge >= 90, Pillow 安装 | 页面已加载 | 返回带标注的截图文件，包含可交互元素编号 |
| test_capture_element | 截取单个元素截图 | Chrome/Edge >= 90 | 页面已加载且元素存在 | 返回指定元素的截图 |

---

## P1 级测试用例（重要功能）

### 1. test_ecommerce_flow.py - 电商购物流程

| 测试用例 | 描述 | 优先级 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|--------|------------|----------|----------|
| test_01_navigate_to_store | 导航到电商网站首页 | P1 | Chrome/Edge >= 90 | 浏览器实例已启动 | 成功加载首页，URL 正确 |
| test_02_search_product | 搜索商品功能 | P1 | Chrome/Edge >= 90 | 首页已加载 | 搜索框输入关键词，提交后进入搜索结果页 |
| test_03_view_product_details | 查看商品详情页 | P1 | Chrome/Edge >= 90 | 搜索结果页已加载 | 点击商品链接，进入详情页 |
| test_04_add_to_cart | 加入购物车功能 | P1 | Chrome/Edge >= 90 | 商品详情页已加载 | 点击加入购物车按钮，购物车更新 |
| test_05_view_cart | 查看购物车内容 | P1 | Chrome/Edge >= 90 | 购物车页面已加载 | 显示购物车中的商品信息 |
| test_06_checkout_simulation | 结算流程模拟 | P1 | Chrome/Edge >= 90 | 购物车页面已加载 | 填写收货信息，提交订单 |
| test_07_capture_order_screenshot | 订单确认页截图 | P1 | Chrome/Edge >= 90, Pillow | 订单确认页已加载 | 生成截图文件 |
| test_08_extract_product_reviews | 提取商品评论 | P1 | Chrome/Edge >= 90 | 商品详情页已加载 | 提取评论列表和评分 |

### 2. test_news_extraction.py - 新闻资讯提取

| 测试用例 | 描述 | 优先级 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|--------|------------|----------|----------|
| test_01_load_article_page | 加载新闻文章页面 | P1 | Chrome/Edge >= 90 | 浏览器实例已启动 | 成功加载文章页面 |
| test_02_extract_article_title | 提取文章标题（H1） | P1 | Chrome/Edge >= 90 | 文章页面已加载 | 正确提取标题 |
| test_03_extract_article_body | 提取文章正文内容 | P1 | Chrome/Edge >= 90 | 文章页面已加载 | 提取完整正文文本 |
| test_04_extract_article_links | 提取文章内链接 | P1 | Chrome/Edge >= 90 | 文章页面已加载 | 提取所有内部和外部链接 |
| test_05_handle_pagination | 处理新闻分页导航 | P1 | Chrome/Edge >= 90 | 分页导航存在 | 点击下一页，URL 变化正确 |
| test_06_extract_article_metadata | 提取文章元数据 | P1 | Chrome/Edge >= 90 | 文章页面已加载 | 提取作者、日期等元数据 |
| test_07_capture_article_screenshot | 截取新闻文章截图 | P1 | Chrome/Edge >= 90, Pillow | 文章页面已加载 | 生成截图文件 |
| test_08_extract_related_articles | 提取相关文章列表 | P1 | Chrome/Edge >= 90 | 文章页面已加载 | 提取相关文章链接 |
| test_09_long_article_segmentation | 长文章分段提取 | P1 | Chrome/Edge >= 90 | 超长文章内容 | 正确处理截断或完整提取 |
| test_10_extract_image_urls | 提取文章中的图片URL | P1 | Chrome/Edge >= 90 | 文章页面含图片 | 提取所有图片 src 属性 |

### 3. test_search_engine.py - 搜索引擎测试

| 测试用例 | 描述 | 优先级 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|--------|------------|----------|----------|
| test_01_load_search_home | 加载搜索引擎首页 | P1 | Chrome/Edge >= 90 | 浏览器实例已启动 | 成功加载首页 |
| test_02_build_search_query | 构建搜索查询并输入关键词 | P1 | Chrome/Edge >= 90 | 首页已加载 | 搜索框输入正确内容 |
| test_03_submit_search_query | 提交搜索查询 | P1 | Chrome/Edge >= 90 | 搜索框有内容 | 提交后进入搜索结果页 |
| test_04_parse_search_results | 解析搜索结果列表 | P1 | Chrome/Edge >= 90 | 搜索结果页已加载 | 提取结果列表 |
| test_05_extract_result_snippets | 提取搜索结果摘要 | P1 | Chrome/Edge >= 90 | 搜索结果页已加载 | 提取 snippet 文本 |
| test_06_handle_pagination | 处理搜索结果分页 | P1 | Chrome/Edge >= 90 | 分页导航存在 | 翻页后 URL 变化正确 |
| test_08_test_autocomplete | 测试搜索自动补全功能 | P1 | Chrome/Edge >= 90 | 搜索框聚焦 | 获取建议列表 |
| test_09_advanced_search_parameters | 高级搜索参数验证 | P1 | Chrome/Edge >= 90 | 搜索页面含过滤器 | 参数正确应用到 URL |
| test_10_search_with_site_filter | site: 限定搜索 | P1 | Chrome/Edge >= 90 | 搜索框有内容 | site: 参数在 URL 中 |
| test_11_extract_search_metadata | 提取搜索结果元数据 | P1 | Chrome/Edge >= 90 | 搜索结果页已加载 | 提取结果数量和耗时 |
| test_12_clear_search_query | 清除搜索查询 | P1 | Chrome/Edge >= 90 | 搜索框有内容 | 清空搜索框 |

### 4. test_social_media.py - 社交媒体测试

| 测试用例 | 描述 | 优先级 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|--------|------------|----------|----------|
| test_01_load_feed | 加载动态流首页 | P1 | Chrome/Edge >= 90 | 浏览器实例已启动 | 成功加载首页 |
| test_02_infinite_scroll_loading | 无限滚动加载更多内容 | P1 | Chrome/Edge >= 90 | 动态流页面已加载 | 滚动后加载更多帖子 |
| test_03_post_text_content | 发布纯文本内容 | P1 | Chrome/Edge >= 90 | 状态输入框可见 | 发布成功，输入框清空 |
| test_04_post_with_image | 发布带图片的内容 | P1 | Chrome/Edge >= 90 | 图片上传功能可用 | 图片上传并发布成功 |
| test_05_like_post | 点赞帖子 | P1 | Chrome/Edge >= 90 | 帖子可见 | 点赞数增加 |
| test_06_comment_on_post | 发表评论 | P1 | Chrome/Edge >= 90 | 评论框可见 | 评论显示在帖子下 |
| test_07_view_user_profile | 查看用户个人主页 | P1 | Chrome/Edge >= 90 | 用户名链接可见 | 跳转到个人主页 |
| test_08_follow_user | 关注用户 | P1 | Chrome/Edge >= 90 | 关注按钮可见 | 按钮状态变为 "Following" |
| test_09_view_notifications | 查看通知列表 | P1 | Chrome/Edge >= 90 | 通知图标可见 | 显示通知列表 |
| test_10_capture_feed_screenshot | 截取动态流截图 | P1 | Chrome/Edge >= 90, Pillow | 动态流页面已加载 | 生成截图文件 |
| test_11_extract_hashtags | 提取帖子中的标签 | P1 | Chrome/Edge >= 90 | 帖子含 hashtag | 提取所有 hashtag |
| test_12_verify_interaction_counts | 验证互动统计数据 | P1 | Chrome/Edge >= 90 | 帖子含互动数据 | 提取点赞、评论、分享数 |

### 5. test_form_submission.py - 表单提交测试

| 测试用例 | 描述 | 优先级 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|--------|------------|----------|----------|
| test_01_load_form_page | 加载表单页面 | P1 | Chrome/Edge >= 90 | 浏览器实例已启动 | 成功加载表单页 |
| test_02_fill_text_fields | 填写文本输入框 | P1 | Chrome/Edge >= 90 | 表单字段可见 | 字段值被正确设置 |
| test_03_fill_email_field | 填写邮箱字段并验证格式 | P1 | Chrome/Edge >= 90 | 邮箱字段可见 | 邮箱格式验证通过 |
| test_04_fill_textarea | 填写多行文本区域 | P1 | Chrome/Edge >= 90 | textarea 可见 | textarea 内容被设置 |
| test_05_select_dropdown_options | 选择下拉选项 | P1 | Chrome/Edge >= 90 | 下拉框可见 | 选项被选中 |
| test_06_choose_date | 选择日期 | P1 | Chrome/Edge >= 90 | 日期选择器可见 | 日期被正确选择 |
| test_07_check_radio_buttons | 单选按钮选择 | P1 | Chrome/Edge >= 90 | 单选按钮可见 | 选中状态正确 |
| test_08_check_checkboxes | 复选框选择 | P1 | Chrome/Edge >= 90 | 复选框可见 | 多个选项被选中 |
| test_09_validate_form_errors | 表单验证错误处理 | P1 | Chrome/Edge >= 90 | 表单未填写完整 | 显示错误信息 |
| test_10_successful_submission | 成功提交表单 | P1 | Chrome/Edge >= 90 | 表单填写完整 | 跳转到成功页 |
| test_11_upload_file | 文件上传功能 | P1 | Chrome/Edge >= 90 | 文件上传控件可见 | 文件上传成功 |
| test_12_multi_step_form | 多步骤表单流程 | P1 | Chrome/Edge >= 90 | 多步骤表单可见 | 各步骤跳转正确 |
| test_13_capture_form_screenshot | 截取表单截图 | P1 | Chrome/Edge >= 90, Pillow | 表单页面已加载 | 生成截图文件 |
| test_14_extract_form_fields | 提取表单字段信息 | P1 | Chrome/Edge >= 90 | 表单页面已加载 | 提取所有字段信息 |
| test_15_clear_form | 清空表单 | P1 | Chrome/Edge >= 90 | 表单已填写 | 所有字段清空 |

---

## P2 级测试用例（辅助功能）

### 1. test_dynamic_content.py - 动态内容专项测试

| 测试用例 | 描述 | 优先级 | 浏览器环境 | 前置条件 | 预期结果 |
|----------|------|--------|------------|----------|----------|
| test_01_infinite_scroll_loading | 无限滚动加载更多内容 | P2 | Chrome/Edge >= 90 | 动态内容页面已加载 | 滚动后加载更多数据 |
| test_02_ajax_request_simulation | 模拟 AJAX 请求与响应 | P2 | Chrome/Edge >= 90 | API 端点可用 | 成功获取 JSON 数据 |
| test_03_spa_route_detection | SPA 路由变化检测 | P2 | Chrome/Edge >= 90 | SPA 应用已加载 | URL 变化被检测到 |
| test_04_lazy_image_loading | 图片懒加载 | P2 | Chrome/Edge >= 90 | 页面含懒加载图片 | 滚动后图片加载完成 |
| test_05_dynamic_element_waiting | 动态元素的等待与超时处理 | P2 | Chrome/Edge >= 90 | 动态元素存在 | 元素出现时返回 True |
| test_06_handle_popups_and_modals | 弹窗和模态框的处理 | P2 | Chrome/Edge >= 90 | 弹窗存在 | 弹窗关闭后消失 |
| test_07_fetch_api_data | 直接调用 API 获取数据 | P2 | Chrome/Edge >= 90 | API 端点可用 | 成功获取数据 |
| test_08_handle_loading_spinners | 等待加载 spinner 消失 | P2 | Chrome/Edge >= 90 | 加载 spinner 存在 | spinner 消失后页面可交互 |
| test_09_websocket_connection | WebSocket 连接监控 | P2 | Chrome/Edge >= 90 | WebSocket 连接可用 | 收到实时消息 |
| test_09_handle_iframes | iframe 内页面的切换 | P2 | Chrome/Edge >= 90 | 页面含 iframe | 成功切换到 iframe |
| test_10_handle_tabs_and_windows | 多 Tab 和新窗口管理 | P2 | Chrome/Edge >= 90 | 浏览器支持多 Tab | 新 Tab 创建成功 |

---

## 测试执行说明

### 1. 测试环境准备

```bash
# 进入 tests 目录
cd tests

# 安装依赖
pip install unittest mock requests pillow websocket-client

# 运行单个测试模块
python -m pytest browser-cdp/templates/test_ecommerce_flow.py -v

# 运行所有测试
python -m pytest browser-cdp/templates/ -v
```

### 2. 测试用例编写规范

- 所有测试继承自 `BaseBrowserTest` 基类
- 使用 `unittest.TestCase` 框架
- 使用 `unittest.mock` 进行 mock 测试
- 每个测试方法以 `test_` 开头
- 使用 `setUp` 方法设置测试环境
- 使用 `tearDown` 方法清理测试资源
- 断言使用 `self.assert*` 系列方法

### 3. 优先级判断标准

- **P0**: 直接影响核心功能，必须保证稳定
- **P1**: 影响用户体验，建议优先修复
- **P2**: 边缘场景或优化项，时间允许时修复

### 4. 浏览器兼容性要求

- 最低版本：Chrome/Edge >= 90
- 必需特性：remote-debugging-port, CDP 支持
- 可选特性：Pillow（用于截图标注）

---

*文档最后更新时间：2026-07-28*