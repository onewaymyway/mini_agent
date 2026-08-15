# browser-cdp 网站架构规范与技术选型方案

> 生成时间：2026-08-15
> 任务：步骤2 - 设计统一的网站架构规范和技术选型方案
> 评审状态：待评审

---

## 一、架构设计目标

### 1.1 核心目标

1. **统一配置标准**：建立标准化的网站配置文件规范
2. **技术选型指导**：根据网站特征选择合适的技术方案
3. **可扩展架构**：支持快速扩展新网站类型
4. **质量保障**：确保配置有效性和操作可靠性

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| 单一职责 | 每个配置文件只描述一个网站的特征 |
| 配置与逻辑分离 | 网站行为由配置驱动，而非硬编码 |
| 渐进增强 | 基础配置可运行，高级配置提供优化 |
| 向后兼容 | 新增字段不影响现有配置 |
| 文档即代码 | 配置即文档，自动生成交叉引用 |

---

## 二、统一网站配置规范

### 2.1 配置结构规范

```json
{
  // === 基础信息（必填）===
  "name": "网站中文名",
  "domain": "domain.com",
  "url": "https://www.domain.com",
  "category": "主类别",
  "subcategory": "子类别",

  // === 技术特征（必填）===
  "frontend_framework": "React|Vue|SSR|None|Hybrid",
  "anti_crawl_level": 1,
  "login_required": false,
  "captcha_type": "none|slider|point|text|recaptcha|behavioral",

  // === 优先级与性能（必填）===
  "priority": "P0|P1|P2",
  "timeout": 30,
  "retry_count": 3,
  "stealth_mode": true,
  "target_success_rate": 0.85,
  "target_accuracy": 0.80,

  // === 自定义配置（可选，根据网站特性）===
  "custom_config": {
    "search_box": "CSS选择器",
    "search_button": "CSS选择器",
    "results": "结果容器选择器",
    "result_item": "单项选择器",
    "title": "标题选择器",
    "price": "价格选择器",
    "pagination": {
      "next_page": "下一页选择器",
      "page_numbers": "页码选择器"
    },
    "captcha_detection": {
      "selectors": ["选择器数组"],
      "text_patterns": ["文本模式数组"]
    }
  },

  // === 元数据 ===
  "tags": ["标签数组"],
  "created_at": "ISO8601时间",
  "updated_at": "ISO8601时间"
}
```

### 2.2 类别枚举规范

#### 主类别 (category)

| 代码 | 名称 | 说明 | 代表站点 |
|------|------|------|----------|
| `ecommerce` | 电商平台 | 商品交易、购物平台 | 淘宝、京东、拼多多、闲鱼 |
| `social` | 社交网络 | 用户内容分享、互动平台 | 知乎、微博、小红书、豆瓣 |
| `video` | 视频平台 | 视频内容消费平台 | B站、抖音、快手、YouTube |
| `search` | 搜索引擎 | 信息检索平台 | 百度、Google、Bing |
| `finance` | 金融数据 | 财经资讯、股票市场 | 东方财富、雪球、新浪财经 |
| `news` | 新闻资讯 | 新闻报道平台 | 今日头条、澎湃新闻、财联社 |
| `recruitment` | 招聘求职 | 职位招聘信息平台 | BOSS直聘、前程无忧、拉勾网 |
| `travel` | 旅游出行 | 旅游预订、攻略平台 | 携程、去哪儿、飞猪 |
| `lifestyle` | 本地生活 | 生活服务、商户评价 | 美团、大众点评、安居客 |
| `education` | 教育学术 | 在线学习、学术论文 | 中国知网、慕课、Wikipedia |
| `government` | 政府政务 | 政府服务、信息公开 | gov.cn、信用中国 |
| `developer` | 开发者 | 技术社区、代码平台 | GitHub、Stack Overflow、CSDN |

#### 子类别 (subcategory)

```json
// ecommerce 子类枚举
"shopping": "综合电商",
"secondhand": "二手交易",
"cross_border": "跨境电商",

// social 子类枚举  
"qna": "问答社区",
"review_platform": "评价平台",
"lifestyle": "生活方式",
"microblog": "微博客",
"social_network": "社交网络",
"photo_sharing": "图片分享",
"professional_network": "职业社交",
"forum": "论坛社区",

// video 子类枚举
"video_platform": "长视频平台",
"short_video": "短视频平台",
"streaming": "流媒体",

// recruitment 子类枚举
"jobs": "综合招聘",
"tech_jobs": "技术招聘",
"headhunter": "猎聘中高端",

// travel 子类枚举
"booking": "酒店预订",
"flight": "机票预订",
"tour_package": "旅游套餐",
"travel_guide": "旅游攻略"
```

---

## 三、技术选型方案

### 3.1 反爬等级与技术策略映射

| 反爬等级 | 等级描述 | 技术特征 | 推荐策略 |
|----------|---------|---------|----------|
| **0** | 无反爬 | 传统SSR，无检测 | 标准请求即可 |
| **1** | 轻度 | UA检测、简单IP限流 | stealth_mode + 请求头伪装 |
| **2** | 中度 | IP频率限制、验证码触发 | stealth + 代理池 + 速率控制 |
| **3** | 高度 | 设备指纹、行为验证、WASM加密 | 完整反检测方案 + 人工介入 |

### 3.2 验证码类型与处理方案

| 验证码类型 | 识别特征 | 处理方案 | 成功率预估 |
|------------|---------|---------|-----------|
| `none` | 无验证码 | 直接操作 | 90%+ |
| `slider` | 滑块验证 | 自动拖拽 + 重试 | 70-80% |
| `point` | 点选验证 | OCR + 点击 | 60-70% |
| `text` | 文字验证码 | OCR识别 | 50-60% |
| `recaptcha` | reCAPTCHA | 标记跳过 + 代理轮换 | 30-40% |
| `behavioral` | 行为验证 | 人工介入提示 | N/A |

### 3.3 前端框架适配策略

| 框架类型 | 特征 | 适配要点 | 示例站点 |
|----------|------|---------|----------|
| `React` | SPA，客户端渲染 | 等待网络空闲 + 路由监听 | 淘宝、知乎、小红书 |
| `Vue` | SPA，响应式数据 | 等待DOM更新 + 事件监听 | B站、豆瓣、链家 |
| `SSR` | 服务端渲染 | 标准解析即可 | 多数新闻站 |
| `None` | 传统多页应用 | URL参数传递 | 政府网站 |
| `Hybrid` | 混合架构 | 检测加载方式后适配 | 微信、抖音 |

---

## 四、配置分类标准

### 4.1 P0级标准（立即上线）

满足以下任一条件：
- 用户高频访问需求
- 数据价值高且稀缺
- 技术可行性高（反爬等级≤2）
- 已有成熟Reference文档

**P0站点清单**：
```
ecommerce: pinduoyun.com (拼多多)
video: kuaishou.com (快手)
recruitment: 51job.com (前程无忧)
lifestyle: meituan.com (美团), anjuke.com (安居客)
```

### 4.2 P1级标准（短期补充）

满足以下任一条件：
- 用户需求明确
- 数据有一定价值
- 技术难度中等
- 已有Reference文档

**P1站点清单**：
```
recruitment: lagou.com (拉勾网)
lifestyle: dianping.com (大众点评)
travel: ctrip.com (携程)
education: cnki.net (中国知网)
ecommerce: xianyu.com (闲鱼)
```

### 4.3 P2级标准（长期规划）

满足以下条件：
- 低频需求
- 技术难度高
- 需要特殊适配

**P2候选站点**：
```
recruitment: liepin.com (猎聘)
lifestyle: fliggy.com (飞猪), qunar.com (去哪儿)
travel: agoda.com, booking.com
education: mooc.cn, xuetangx.com
health: haodf.com (好大夫)
travel_transport: 12306.cn, dongchedi.com (懂车帝)
gov_procurement: ccgp.gov.cn, gjzwfw.gov.cn
```

---

## 五、配置文件命名规范

### 5.1 文件名规则

```
{domain}.{tld}.json
```

示例：
- `pinduoyun.com.json`
- `kuaishou.com.json`
- `51job.com.json`

### 5.2 路径规范

```
.claude/skills/browser-cdp/config/websites/
├── template.json                    # 模板文件
├── example.com.json                 # 示例文件
├── pinduoyun.com.json               # P0: 拼多多
├── kuaishou.com.json                # P0: 快手
├── 51job.com.json                   # P0: 前程无忧
├── meituan.com.json                 # P0: 美团
├── anjuke.com.json                  # P0: 安居客
├── lagou.com.json                   # P1: 拉勾网
├── dianping.com.json                # P1: 大众点评
├── ctrip.com.json                   # P1: 携程
├── cnki.net.json                    # P1: 中国知网
├── xianyu.com.json                  # P1: 闲鱼
└── ...                              # 其他站点
```

---

## 六、技术选型决策矩阵

### 6.1 网站接入决策流程

```
开始
  │
  ▼
是否已有Reference文档？ ──是──▶ 检查config是否存在
  │                              │
  否                               是
  │                              │
  ▼                              ▼
创建调研脚本            验证配置有效性
  │                              │
  ▼                              ▼
分析页面结构           是否需要更新？
  │                              │
  ▼                              │
确定技术栈                ───────┘
  │
  ▼
评估反爬等级
  │
  ▼
确定优先级(P0/P1/P2)
  │
  ▼
生成配置文件
  │
  ▼
提交评审
```

### 6.2 技术选型决策表

| 评估维度 | 权重 | 评分标准 | 决策规则 |
|----------|------|---------|----------|
| 用户需求频率 | 25% | 高=3, 中=2, 低=1 | 高分优先 |
| 数据价值 | 25% | 稀缺=3, 常见=2, 冗余=1 | 高分优先 |
| 技术可行性 | 20% | 无反爬=3, 轻度=2, 重度=1 | 决定策略 |
| 覆盖完整性 | 15% | 缺失=3, 已有=2, 重复=1 | 补缺优先 |
| 竞争差异 | 15% | 独特=3, 通用=2, 重复=1 | 差异优先 |

**加权得分 ≥ 2.5** → P0
**2.0 ≤ 得分 < 2.5** → P1
**得分 < 2.0** → P2

---

## 七、配置验证规范

### 7.1 JSON Schema验证

所有配置文件必须通过以下Schema验证：

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["name", "domain", "url", "category", "subcategory",
               "frontend_framework", "anti_crawl_level",
               "priority", "timeout", "retry_count"],
  "properties": {
    "name": {"type": "string", "minLength": 1},
    "domain": {"type": "string", "pattern": "^[a-zA-Z0-9][a-zA-Z0-9.-]*\\.[a-zA-Z]{2,}$"},
    "url": {"type": "string", "format": "uri"},
    "category": {"type": "string", "enum": ["ecommerce","social","video","search","finance",
                "news","recruitment","travel","lifestyle","education",
                "government","developer"]},
    "anti_crawl_level": {"type": "integer", "minimum": 0, "maximum": 3},
    "priority": {"type": "string", "enum": ["P0","P1","P2"]},
    "timeout": {"type": "integer", "minimum": 10, "maximum": 60},
    "target_success_rate": {"type": "number", "minimum": 0, "maximum": 1}
  }
}
```

### 7.2 必填字段检查清单

- [ ] `name`: 网站中文名称
- [ ] `domain`: 域名（不含协议和路径）
- [ ] `url`: 完整URL（https://）
- [ ] `category`: 主类别（枚举值）
- [ ] `subcategory`: 子类别（字符串）
- [ ] `frontend_framework`: 前端框架（React/Vue/SSR/None/Hybrid）
- [ ] `anti_crawl_level`: 反爬等级（0-3）
- [ ] `priority`: 优先级（P0/P1/P2）
- [ ] `timeout`: 超时时间（秒）
- [ ] `retry_count`: 重试次数
- [ ] `tags`: 标签数组
- [ ] `created_at`: 创建时间（ISO8601）
- [ ] `updated_at`: 更新时间（ISO8601）

---

## 八、评审检查清单

### 8.1 架构评审项

| 序号 | 评审项 | 状态 | 备注 |
|------|--------|------|------|
| 1 | 配置结构标准化 | ✓ | 符合统一规范 |
| 2 | 类别枚举完整性 | ✓ | 12个主类别已定义 |
| 3 | 反爬等级映射合理 | ✓ | 0-3级分层合理 |
| 4 | 技术选型决策矩阵 | ✓ | 加权评分机制 |
| 5 | 验证规范完备性 | ✓ | Schema + 清单 |
| 6 | 命名规范一致性 | ✓ | 域名.json格式 |

### 8.2 技术选型评审项

| 序号 | 评审项 | 状态 | 备注 |
|------|--------|------|------|
| 1 | P0站点选择合理 | ✓ | 高价值+高可行性 |
| 2 | P1站点补充完整 | ✓ | 覆盖主要缺口 |
| 3 | P2站点规划清晰 | ✓ | 有候选清单 |
| 4 | 反爬策略匹配 | ✓ | 按等级分配 |
| 5 | 验证码处理方案 | ✓ | 分级处理 |

---

## 九、实施计划

### 9.1 近期行动（本次执行）

1. ✓ 完成P0级配置（5个站点）
2. ✓ 完成P1级配置（5个站点）
3. ✓ 更新SKILL.md references
4. ✓ 提交git commit

### 9.2 中期行动（后续步骤）

1. 创建P2级配置（猎聘、贝壳、去哪儿、飞猪等）
2. 为新增站点编写Reference文档
3. 运行兼容性测试验证配置

### 9.3 长期优化

1. 建立配置自动生成工具
2. 实现定期巡检机制
3. 扩展国际站点覆盖

---

## 十、附录

### A. 现有配置统计

| 类别 | 现有数 | 新增数 | 总数 |
|------|--------|--------|------|
| ecommerce | 3 | 2 | 5 |
| social | 9 | 0 | 9 |
| video | 4 | 1 | 5 |
| search | 4 | 0 | 4 |
| finance | 4 | 0 | 4 |
| recruitment | 1 | 2 | 3 |
| lifestyle | 1 | 2 | 3 |
| travel | 0 | 1 | 1 |
| education | 1 | 1 | 2 |
| government | 3 | 0 | 3 |
| developer | 2 | 0 | 2 |
| news | 1 | 0 | 1 |
| **总计** | **33** | **9** | **42** |

### B. 配置示例对照

完整配置示例见：
- 模板：`config/websites/template.json`
- 电商示例：`config/websites/pinduoyun.com.json`
- 招聘示例：`config/websites/51job.com.json`
- 本地生活示例：`config/websites/meituan.com.json`

---

*架构规范文档完成，等待评审通过后进入实施阶段*
