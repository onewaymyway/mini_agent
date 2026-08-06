# 网站兼容性测试框架 - 使用指南

> 版本：1.0.0
> 最后更新：2026-08-07

---

## 一、快速开始

### 1.1 运行所有测试

```bash
cd .claude/skills/browser-cdp
python -m tests.compatibility
```

### 1.2 运行指定类别测试

```bash
# 仅运行电商类测试
python -m pytest tests/compatibility/test_ecommerce.py -v

# 仅运行新闻类测试
python -m pytest tests/compatibility/test_news.py -v

# 仅运行社交类测试
python -m pytest tests/compatibility/test_social.py -v

# 仅运行政务类测试
python -m pytest tests/compatibility/test_gov.py -v
```

### 1.3 生成测试报告

```bash
# 生成 HTML 报告
python -m pytest tests/compatibility -v --html=test_reports/report.html

# 生成 JSON 报告
python -m pytest tests/compatibility -v --json=test_reports/report.json
```

---

## 二、测试覆盖范围

### 2.1 电商类 (ECOM)

| 网站 | URL | 测试用例数 | 优先级 |
|------|-----|-----------|--------|
| 京东 | https://www.jd.com/ | 3 | P0 |
| 淘宝 | https://www.taobao.com/ | 3 | P0 |
| 拼多多 | https://www.pinduoduo.com/ | 2 | P1 |
| 天猫 | https://www.tmall.com/ | 2 | P1 |
| 亚马逊 | https://www.amazon.com/ | 2 | P1 |
| eBay | https://www.ebay.com/ | 2 | P2 |

**核心测试场景**：
- 首页加载与标题验证
- 搜索框定位与交互
- 商品列表页渲染
- 无限滚动加载
- 价格/销量数据提取

### 2.2 新闻类 (NEWS)

| 网站 | URL | 测试用例数 | 优先级 |
|------|-----|-----------|--------|
| 新华网 | https://www.xinhuanet.com/ | 2 | P0 |
| 人民网 | https://www.people.com.cn/ | 2 | P0 |
| 澎湃新闻 | https://www.thepaper.cn/ | 2 | P1 |
| 财新网 | https://www.caixin.com/ | 2 | P1 |
| 网易新闻 | https://news.163.com/ | 2 | P2 |
| 新浪新闻 | https://news.sina.com.cn/ | 2 | P2 |

**核心测试场景**：
- 首页加载与标题验证
- 新闻列表页渲染
- 文章详情页抓取
- 分类导航交互
- 实时新闻更新检测

### 2.3 社交类 (SOCIAL)

| 网站 | URL | 测试用例数 | 优先级 |
|------|-----|-----------|--------|
| 微博 | https://weibo.com/ | 3 | P0 |
| 知乎 | https://www.zhihu.com/ | 3 | P0 |
| 豆瓣 | https://www.douban.com/ | 2 | P1 |
| 小红书 | https://www.xiaohongshu.com/ | 2 | P1 |
| B站 | https://www.bilibili.com/ | 2 | P1 |
| 抖音 | https://www.douyin.com/ | 2 | P2 |
| 快手 | https://www.kuaishou.com/ | 2 | P2 |
| 脉脉 | https://maimai.cn/ | 2 | P2 |

**核心测试场景**：
- 首页加载与登录态检测
- 动态内容加载（无限滚动）
- 评论/点赞交互
- 搜索结果渲染
- 用户主页抓取

### 2.4 政务类 (GOV)

| 网站 | URL | 测试用例数 | 优先级 |
|------|-----|-----------|--------|
| 中国政府网 | https://www.gov.cn/ | 2 | P0 |
| 人民网 | https://www.people.com.cn/ | 2 | P0 |
| 新华网 | https://www.xinhuanet.com/ | 2 | P1 |
| 地方政府门户 | https://beijing.gov.cn/ | 2 | P1 |
| 政务服务网 | https://www.gov.cn/zhengce/ | 2 | P2 |
| 政策文件库 | https://www.gov.cn/zhengce/zhengceku/ | 2 | P2 |

**核心测试场景**：
- 首页加载与标题验证
- 政策文件列表抓取
- 文件详情页解析
- 搜索功能验证
- 无障碍访问检测

---

## 三、评估指标体系

### 3.1 核心指标

| 指标 | 权重 | 计算方式 |
|------|------|----------|
| 页面访问成功率 | 20% | 成功加载页面数 / 总页面数 |
| 元素定位准确率 | 20% | 成功定位元素数 / 总定位数 |
| 数据提取成功率 | 30% | 成功提取数据数 / 总提取数 |
| 稳定性 | 20% | 连续测试通过率 |
| 反检测能力 | 10% | 绕过反爬机制比例 |

### 3.2 综合评分

```
综合评分 = 页面访问成功率 × 0.20 +
           元素定位准确率 × 0.20 +
           数据提取成功率 × 0.30 +
           稳定性 × 0.20 +
           反检测能力 × 0.10
```

### 3.3 评级标准

| 评级 | 综合评分 | 说明 |
|------|----------|------|
| A+ | >= 95% | 优秀，完全支持 |
| A | 90-95% | 良好，基本支持 |
| B | 80-90% | 中等，部分支持 |
| C | 70-80% | 较差，需优化 |
| D | < 70% | 不支持，需重构 |

---

## 四、测试用例设计

### 4.1 用例结构

```python
TestCase(
    case_id="JD-01",
    name="首页访问",
    description="验证京东首页能否正常加载",
    steps=[
        Step(action="navigate", target="https://www.jd.com/"),
    ],
    expected_results=[
        ExpectedResult(
            condition="页面标题包含'京东'",
            expected_value="京东",
            check_type="contains",
        ),
    ],
    evaluation_dimensions=["页面访问成功率"],
    pass_criteria={"page_access_success_rate": 0.95},
)
```

### 4.2 步骤类型

| 动作 | 说明 | 参数 |
|------|------|------|
| `navigate` | 导航到 URL | `target`: URL |
| `click` | 点击元素 | `target`: 选择器 |
| `input` | 输入文本 | `target`: 选择器, `value`: 文本 |
| `wait` | 等待 | `timeout`: 超时时间 |
| `scroll` | 滚动页面 | `target`: 滚动方向 |
| `screenshot` | 截图 | `target`: 截图区域 |

### 4.3 检查类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `contains` | 包含 | 页面标题包含"京东" |
| `equals` | 等于 | 状态码等于 200 |
| `greater_than` | 大于 | 提取结果数 > 5 |
| `less_than` | 小于 | 响应时间 < 5s |

---

## 五、扩展指南

### 5.1 添加新网站

1. 在 `test_cases.py` 中添加网站配置
2. 添加测试用例
3. 实现对应的搜索器（可选）

```python
# 在 create_xxx_websites() 中添加
WebsiteConfig(
    name="新网站",
    url="https://www.example.com/",
    category=Category.ECOM,
    subcategory="ECOM_B2C",
    frontend_framework="React",
    anti_crawl_level=AntiCrawlLevel.MEDIUM,
    login_required=False,
    priority=Priority.P1,
    timeout=30,
    retry_count=3,
    target_success_rate=0.95,
    target_accuracy=0.90,
    searcher_file="example_search.py",
),
```

### 5.2 添加新测试用例

```python
TestCase(
    case_id="NEW-01",
    name="新功能测试",
    description="验证新功能",
    steps=[...],
    expected_results=[...],
    evaluation_dimensions=[...],
    pass_criteria={...},
)
```

---

## 六、注意事项

1. **浏览器实例**：测试需要浏览器实例，确保 Chrome/Edge 已安装
2. **登录态**：需要登录的网站，需要提前配置好登录态
3. **反爬策略**：强反爬网站需要启用反检测模式
4. **性能优化**：高并发测试需要注意资源限制
5. **网络环境**：确保网络稳定，避免测试中断

---

## 七、相关链接

- [框架设计文档](./compatibility-test-framework-design.md)
- [测试用例规格](./website-compatibility-test-spec.md)
- [网站技术栈调研](../website-tech-stack-research.md)
- [网站分类标签体系](../website-classification.md)
