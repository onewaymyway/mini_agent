# Browser CDP 技能标准测试用例库 v2.0

**版本**: 2.0.0  
**最后更新**: 2026-08-08  
**维护者**: OneWay  
**状态**: 已验证

---

## 1. 概述

本测试用例库为 browser-cdp 技能提供完整的测试覆盖，包括：

- **核心功能测试**: browser_launch, browser_nav, browser_extract, browser_input, browser_screenshot
- **场景化测试**: 电商、新闻、搜索、社交、表单、登录等常见网站类型
- **专项测试**: 动态内容、SPA、AJAX、无限滚动、验证码等复杂交互场景
- **基类模板**: 统一的测试框架和断言方法
- **单元模块测试**: browser_console, browser_watch, cdp_client, retry_policy, circuit_breaker, utils

---

## 2. 测试用例分类体系

### 2.1 按模块分类

| 模块 | 文件 | 测试用例数 | 优先级 |
|------|------|-----------|--------|
| browser_launch | test_browser_launch.py | 6 | P0 |
| browser_nav | test_browser_nav.py | 5 | P0 |
| browser_extract | test_browser_extract.py | 4 | P0 |
| browser_input | test_browser_input.py | 6 | P0 |
| browser_screenshot | test_browser_screenshot.py | 5 | P0 |
| browser_console | test_browser_console.py | 15 | P0 |
| browser_watch | test_browser_watch.py | 10 | P0 |
| cdp_client | test_cdp_client.py | 20 | P0 |
| retry_policy | test_retry_policy.py | 12 | P0 |
| circuit_breaker | test_circuit_breaker.py | 8 | P0 |
| utils | test_utils.py | 6 | P0 |
| test_ecommerce_flow | test_ecommerce_flow.py | 8 | P1 |
| test_news_extraction | test_news_extraction.py | 10 | P1 |
| test_search_engine | test_search_engine.py | 12 | P1 |
| test_social_media | test_social_media.py | 12 | P1 |
| test_form_submission | test_form_submission.py | 15 | P1 |
| test_login_flow | test_login_flow.py | 8 | P0 |
| test_navigation | test_navigation.py | 12 | P0 |
| test_dynamic_content | test_dynamic_content.py | 10 | P2 |
| test_captcha | test_captcha.py | 6 | P1 |

### 2.2 按优先级分类

#### P0 (Critical) - 核心功能测试

**特点**: 直接影响核心功能，必须保证稳定，阻塞发布

| 模块 | 测试项 |
|------|--------|
| browser_launch | 专用实例管理、端口检测、Tab 创建与列表 |
| browser_nav | 页面导航、元素等待、前进后退刷新 |
| browser_extract | 文本/HTML/链接/元素提取、元数据获取 |
| browser_input | 输入、点击、滚动、选择、值获取 |
| browser_screenshot | 整页截图、元素截图、标注截图 |
| browser_console | JS执行、console日志、网络请求抓取、cookie管理 |
| browser_watch | URL/标题等待、轮询判断操作完成 |
| cdp_client | CDP客户端连接、tab管理、CDPSession命令发送 |
| retry_policy | 重试配置、可重试操作、with_retry装饰器 |
| circuit_breaker | 熔断状态机、失败追踪器、故障率计算 |
| utils | 连接辅助函数、JSON打印、URL解析 |
| test_login_flow | 登录流程、会话管理、验证码处理 |
| test_navigation | 页面跳转、前进后退、Tab管理 |

#### P1 (High) - 重要功能测试

**特点**: 影响用户体验，建议优先修复

| 场景 | 测试用例数 | 描述 |
|------|-----------|------|
| 电商购物流程 | 8 | 商品搜索、查看详情、加入购物车、购物车、结算 |
| 新闻资讯提取 | 10 | 文章正文、分页、元数据、图片提取 |
| 搜索引擎 | 12 | 查询构建、结果解析、自动补全、分页 |
| 社交媒体 | 12 | 无限滚动、发帖、点赞评论、通知 |
| 表单提交 | 15 | 字段填写、验证、文件上传、多步骤 |
| 验证码处理 | 6 | 滑块、短信、图形验证码 |

#### P2 (Medium) - 辅助功能测试

**特点**: 边缘场景或优化项，时间允许时修复

| 场景 | 测试用例数 | 描述 |
|------|-----------|------|
| 动态内容专项 | 10 | 无限滚动、AJAX、SPA、懒加载、弹窗、WebSocket |

---

## 3. 详细测试用例说明

### 3.1 登录流程测试 (test_login_flow.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| LOGIN-01 | test_01_navigate_to_login | 导航到登录页面 | 浏览器实例已启动 | 成功加载登录页，URL 正确 |
| LOGIN-02 | test_02_fill_username | 填写用户名 | 登录页已加载 | 用户名输入框被正确填充 |
| LOGIN-03 | test_03_fill_password | 填写密码 | 登录页已加载 | 密码输入框被正确填充 |
| LOGIN-04 | test_04_submit_login | 提交登录表单 | 账号密码已填写 | 登录成功，跳转到首页或用户中心 |
| LOGIN-05 | test_05_handle_login_error | 处理登录失败 | 输入错误密码 | 显示错误提示信息 |
| LOGIN-06 | test_06_check_remember_me | 记住登录态 | 登录页已加载 | 勾选"记住我"后登录，下次自动登录 |
| LOGIN-07 | test_07_third_party_login | 第三方登录 | 第三方登录按钮可见 | 点击第三方登录按钮，跳转授权页 |
| LOGIN-08 | test_08_captcha_input | 验证码输入 | 验证码可见 | 正确输入验证码后登录成功 |

### 3.2 导航浏览测试 (test_navigation.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| NAV-01 | test_01_navigate_homepage | 导航到网站首页 | 浏览器实例已启动 | 成功加载首页，URL 正确 |
| NAV-02 | test_02_click_link | 点击页面链接 | 首页已加载 | 跳转到目标页面，URL 变化正确 |
| NAV-03 | test_03_browser_back | 浏览器后退 | 已访问多个页面 | 返回上一页，URL 正确 |
| NAV-04 | test_04_browser_forward | 浏览器前进 | 已后退 | 前进到下一页，URL 正确 |
| NAV-05 | test_05_refresh_page | 刷新页面 | 当前页面已加载 | 页面重新加载，内容一致 |
| NAV-06 | test_06_open_new_tab | 新标签页打开 | 页面含链接 | 新 Tab 创建成功，URL 正确 |
| NAV-07 | test_07_switch_tabs | 切换标签页 | 多个 Tab 已打开 | 成功切换到目标 Tab |
| NAV-08 | test_08_close_tab | 关闭标签页 | 多个 Tab 已打开 | 目标 Tab 关闭，其他 Tab 正常 |
| NAV-09 | test_09_url_change_detection | URL 变化检测 | 页面已加载 | 检测到 URL 变化 |
| NAV-10 | test_10_title_change_detection | 页面标题检测 | 页面已加载 | 检测到标题变化 |
| NAV-11 | test_11_anchor_navigation | 锚点跳转 | 页面含锚点链接 | 成功跳转到锚点位置 |
| NAV-12 | test_12_popup_handling | 弹窗处理 | 弹窗存在 | 弹窗关闭后页面可交互 |

### 3.3 表单操作测试 (test_form_submission.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| FORM-01 | test_01_load_form_page | 加载表单页面 | 浏览器实例已启动 | 成功加载表单页 |
| FORM-02 | test_02_fill_text_fields | 填写文本输入框 | 表单字段可见 | 字段值被正确设置 |
| FORM-03 | test_03_fill_email_field | 填写邮箱字段并验证格式 | 邮箱字段可见 | 邮箱格式验证通过 |
| FORM-04 | test_04_fill_textarea | 填写多行文本区域 | textarea 可见 | textarea 内容被设置 |
| FORM-05 | test_05_select_dropdown_options | 选择下拉选项 | 下拉框可见 | 选项被选中 |
| FORM-06 | test_06_choose_date | 选择日期 | 日期选择器可见 | 日期被正确选择 |
| FORM-07 | test_07_check_radio_buttons | 单选按钮选择 | 单选按钮可见 | 选中状态正确 |
| FORM-08 | test_08_check_checkboxes | 复选框选择 | 复选框可见 | 多个选项被选中 |
| FORM-09 | test_09_validate_form_errors | 表单验证错误处理 | 表单未填写完整 | 显示错误信息 |
| FORM-10 | test_10_successful_submission | 成功提交表单 | 表单填写完整 | 跳转到成功页 |
| FORM-11 | test_11_upload_file | 文件上传功能 | 文件上传控件可见 | 文件上传成功 |
| FORM-12 | test_12_multi_step_form | 多步骤表单流程 | 多步骤表单可见 | 各步骤跳转正确 |
| FORM-13 | test_13_capture_form_screenshot | 截取表单截图 | 表单页面已加载 | 生成截图文件 |
| FORM-14 | test_14_extract_form_fields | 提取表单字段信息 | 表单页面已加载 | 提取所有字段信息 |
| FORM-15 | test_15_clear_form | 清空表单 | 表单已填写 | 所有字段清空 |

### 3.4 验证码处理测试 (test_captcha.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| CAPTCHA-01 | test_01_detect_captcha | 检测验证码类型 | 验证码存在 | 识别验证码类型（图形/滑块/短信） |
| CAPTCHA-02 | test_02_solve_captcha | 解决图形验证码 | 图形验证码可见 | 正确输入验证码 |
| CAPTCHA-03 | test_03_solve_slider_captcha | 解决滑块验证码 | 滑块验证码可见 | 拖动滑块到正确位置 |
| CAPTCHA-04 | test_04_sms_captcha | 短信验证码处理 | 短信验证码可用 | 获取并输入验证码 |
| CAPTCHA-05 | test_05_captcha_timeout | 验证码超时处理 | 验证码即将过期 | 刷新验证码或提示超时 |
| CAPTCHA-06 | test_06_captcha_retry | 验证码重试机制 | 验证码错误 | 自动刷新并重试 |

### 3.5 电商购物流程测试 (test_ecommerce_flow.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| ECOM-01 | test_01_navigate_to_store | 导航到电商网站首页 | 浏览器实例已启动 | 成功加载首页，URL 正确 |
| ECOM-02 | test_02_search_product | 搜索商品功能 | 首页已加载 | 搜索框输入关键词，提交后进入搜索结果页 |
| ECOM-03 | test_03_view_product_details | 查看商品详情页 | 搜索结果页已加载 | 点击商品链接，进入详情页 |
| ECOM-04 | test_04_add_to_cart | 加入购物车功能 | 商品详情页已加载 | 点击加入购物车按钮，购物车更新 |
| ECOM-05 | test_05_view_cart | 查看购物车内容 | 购物车页面已加载 | 显示购物车中的商品信息 |
| ECOM-06 | test_06_checkout_simulation | 结算流程模拟 | 购物车页面已加载 | 填写收货信息，提交订单 |
| ECOM-07 | test_07_capture_order_screenshot | 订单确认页截图 | 订单确认页已加载 | 生成截图文件 |
| ECOM-08 | test_08_extract_product_reviews | 提取商品评论 | 商品详情页已加载 | 提取评论列表和评分 |

### 3.6 新闻资讯提取测试 (test_news_extraction.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| NEWS-01 | test_01_load_article_page | 加载新闻文章页面 | 浏览器实例已启动 | 成功加载文章页面 |
| NEWS-02 | test_02_extract_article_title | 提取文章标题（H1） | 文章页面已加载 | 正确提取标题 |
| NEWS-03 | test_03_extract_article_body | 提取文章正文内容 | 文章页面已加载 | 提取完整正文文本 |
| NEWS-04 | test_04_extract_article_links | 提取文章内链接 | 文章页面已加载 | 提取所有内部和外部链接 |
| NEWS-05 | test_05_handle_pagination | 处理新闻分页导航 | 分页导航存在 | 点击下一页，URL 变化正确 |
| NEWS-06 | test_06_extract_article_metadata | 提取文章元数据 | 文章页面已加载 | 提取作者、日期等元数据 |
| NEWS-07 | test_07_capture_article_screenshot | 截取新闻文章截图 | 文章页面已加载 | 生成截图文件 |
| NEWS-08 | test_08_extract_related_articles | 提取相关文章列表 | 文章页面已加载 | 提取相关文章链接 |
| NEWS-09 | test_09_long_article_segmentation | 长文章分段提取 | 超长文章内容 | 正确处理截断或完整提取 |
| NEWS-10 | test_10_extract_image_urls | 提取文章中的图片URL | 文章页面含图片 | 提取所有图片 src 属性 |

### 3.7 搜索引擎测试 (test_search_engine.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| SEARCH-01 | test_01_load_search_home | 加载搜索引擎首页 | 浏览器实例已启动 | 成功加载首页 |
| SEARCH-02 | test_02_build_search_query | 构建搜索查询并输入关键词 | 首页已加载 | 搜索框输入正确内容 |
| SEARCH-03 | test_03_submit_search_query | 提交搜索查询 | 搜索框有内容 | 提交后进入搜索结果页 |
| SEARCH-04 | test_04_parse_search_results | 解析搜索结果列表 | 搜索结果页已加载 | 提取结果列表 |
| SEARCH-05 | test_05_extract_result_snippets | 提取搜索结果摘要 | 搜索结果页已加载 | 提取 snippet 文本 |
| SEARCH-06 | test_06_handle_pagination | 处理搜索结果分页 | 分页导航存在 | 翻页后 URL 变化正确 |
| SEARCH-08 | test_08_test_autocomplete | 测试搜索自动补全功能 | 搜索框聚焦 | 获取建议列表 |
| SEARCH-09 | test_09_advanced_search_parameters | 高级搜索参数验证 | 搜索页面含过滤器 | 参数正确应用到 URL |
| SEARCH-10 | test_10_search_with_site_filter | site: 限定搜索 | 搜索框有内容 | site: 参数在 URL 中 |
| SEARCH-11 | test_11_extract_search_metadata | 提取搜索结果元数据 | 搜索结果页已加载 | 提取结果数量和耗时 |
| SEARCH-12 | test_12_clear_search_query | 清除搜索查询 | 搜索框有内容 | 清空搜索框 |

### 3.8 社交媒体测试 (test_social_media.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| SOCIAL-01 | test_01_load_feed | 加载动态流首页 | 浏览器实例已启动 | 成功加载首页 |
| SOCIAL-02 | test_02_infinite_scroll_loading | 无限滚动加载更多内容 | 动态流页面已加载 | 滚动后加载更多帖子 |
| SOCIAL-03 | test_03_post_text_content | 发布纯文本内容 | 状态输入框可见 | 发布成功，输入框清空 |
| SOCIAL-04 | test_04_post_with_image | 发布带图片的内容 | 图片上传功能可用 | 图片上传并发布成功 |
| SOCIAL-05 | test_05_like_post | 点赞帖子 | 帖子可见 | 点赞数增加 |
| SOCIAL-06 | test_06_comment_on_post | 发表评论 | 评论框可见 | 评论显示在帖子下 |
| SOCIAL-07 | test_07_view_user_profile | 查看用户个人主页 | 用户名链接可见 | 跳转到个人主页 |
| SOCIAL-08 | test_08_follow_user | 关注用户 | 关注按钮可见 | 按钮状态变为 "Following" |
| SOCIAL-09 | test_09_view_notifications | 查看通知列表 | 通知图标可见 | 显示通知列表 |
| SOCIAL-10 | test_10_capture_feed_screenshot | 截取动态流截图 | 动态流页面已加载 | 生成截图文件 |
| SOCIAL-11 | test_11_extract_hashtags | 提取帖子中的标签 | 帖子含 hashtag | 提取所有 hashtag |
| SOCIAL-12 | test_12_verify_interaction_counts | 验证互动统计数据 | 帖子含互动数据 | 提取点赞、评论、分享数 |

### 3.9 动态内容专项测试 (test_dynamic_content.py)

| 用例 ID | 测试方法 | 描述 | 前置条件 | 预期结果 |
|---------|----------|------|----------|----------|
| DYN-01 | test_01_infinite_scroll_loading | 无限滚动加载更多内容 | 动态内容页面已加载 | 滚动后加载更多数据 |
| DYN-02 | test_02_ajax_request_simulation | 模拟 AJAX 请求与响应 | API 端点可用 | 成功获取 JSON 数据 |
| DYN-03 | test_03_spa_route_detection | SPA 路由变化检测 | SPA 应用已加载 | URL 变化被检测到 |
| DYN-04 | test_04_lazy_image_loading | 图片懒加载 | 页面含懒加载图片 | 滚动后图片加载完成 |
| DYN-05 | test_05_dynamic_element_waiting | 动态元素的等待与超时处理 | 动态元素存在 | 元素出现时返回 True |
| DYN-06 | test_06_handle_popups_and_modals | 弹窗和模态框的处理 | 弹窗存在 | 弹窗关闭后消失 |
| DYN-07 | test_07_fetch_api_data | 直接调用 API 获取数据 | API 端点可用 | 成功获取数据 |
| DYN-08 | test_08_handle_loading_spinners | 等待加载 spinner 消失 | 加载 spinner 存在 | spinner 消失后页面可交互 |
| DYN-09 | test_09_websocket_connection | WebSocket 连接监控 | WebSocket 连接可用 | 收到实时消息 |
| DYN-10 | test_10_handle_tabs_and_windows | 多 Tab 和新窗口管理 | 浏览器支持多 Tab | 新 Tab 创建成功 |

---

## 4. 测试执行规范

### 4.1 运行前准备

```bash
# 进入 tests 目录
cd tests

# 安装依赖
pip install pytest pytest-html playwright websocket-client

# 安装 Playwright 浏览器
playwright install chromium
```

### 4.2 执行命令

```bash
# 运行单个测试模块
pytest tests/compatibility/test_login_flow.py -v

# 运行所有兼容性测试
pytest tests/compatibility/ -v

# 按优先级筛选测试
pytest tests/compatibility/ -k "P0"

# 生成 HTML 报告
pytest tests/compatibility/ --html=report.html --self-contained-html

# Mock 模式（默认，无需真实浏览器）
pytest tests/compatibility/ -v --mock

# 真实浏览器模式（需要 Chrome 调试端口）
pytest tests/compatibility/ -v --no-mock
```

### 4.3 测试输出格式

```bash
============================= test session starts =============================
collected 8 items

test_login_flow.py ........                                         [100%]

=========================== 8 passed in 2.34 seconds ===========================
```

---

## 5. 失败用例记录模板

当测试执行发现失败用例时，请按以下格式记录：

| 测试用例 | 优先级 | 失败原因 | 错误信息 | 修复方案 | 状态 |
|----------|--------|----------|----------|----------|------|
| LOGIN-04 | P0 | mock 设置不正确 | AttributeError: 'NoneType' object has no attribute 'click_selector' | 检查 mock 对象返回值 | 待修复 |

---

## 6. 变更日志

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|----------|
| 1.0.0 | 2026-07-28 | Orzooo | 初始版本，创建完整测试用例库 |
| 1.0.1 | 2026-07-29 | Orzooo | 新增单元测试文件：test_browser_console.py, test_browser_watch.py, test_cdp_client.py, test_retry_policy.py, test_circuit_breaker.py, test_utils.py |
| 2.0.0 | 2026-08-08 | Orzooo | 新增登录流程测试、导航浏览测试、验证码处理测试；完善场景覆盖矩阵 |

---

*此文档纳入版本控制，请随测试用例库更新同步修订*
