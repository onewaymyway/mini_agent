# Browser-CDP Skill 目标网站拓展清单（v2.0）

> 生成时间：2026-08-08
> 目标：拓展 browser-cdp skill 可抓取和浏览的网站覆盖范围，形成可靠的网站操作能力
> 状态：步骤1/6 - 确定目标新领域的具体细分赛道

---

## 一、现有覆盖情况总结

### 1.1 搜索器总数
- **总搜索器数量**：120 个 Python 文件
- **有效搜索器**：约 105 个（排除工具类文件）

### 1.2 已覆盖网站统计

| 领域 | 已覆盖网站数 | 代表网站 |
|------|------------|---------|
| 电商/购物 | 6 | 京东、淘宝、拼多多、Amazon、闲鱼 |
| 招聘/职场 | 6 | 51job、Boss直聘、拉勾、智联、猎聘、脉脉 |
| 房产 | 3 | 链家、贝壳、安居客 |
| 旅游/出行 | 6 | 携程、去哪儿、飞猪、马蜂窝、12306、高德地图 |
| 社交/内容 | 14 | 小红书、知乎、微博、豆瓣、抖音、快手、西瓜视频、爱奇艺、优酷、腾讯视频、哔哩哔哩、网易云音乐、微信、Reddit |
| 教育/学术 | 7 | arXiv、CNKI、Google Scholar、Semantic Scholar、学堂在线、慕课、多助语 |
| 新闻/资讯 | 5 | 新浪财经、财联社、澎湃新闻、网易新闻、今日头条 |
| 体育 | 7 | 虎扑、懂球帝、直播吧、体坛周报、新浪体育、腾讯体育、体育搜索 |
| 美食/餐饮 | 7 | 大众点评、美团、饿了么、下厨房、美食杰、美食搜索、美团外卖 |
| 音乐/娱乐 | 6 | 网易云音乐、QQ音乐、酷狗音乐、酷我音乐、咪咕音乐、豆瓣音乐 |
| 金融/投资 | 3 | 雪球、东方财富股吧、信用中国 |
| 医疗健康 | 7 | 好大夫在线、丁香园医院库、39就医助手、博禾医院库、医院搜索、医疗搜索 |
| 法律/政务 | 3 | 华律网、找法网、华律搜索 |
| 二手交易 | 4 | 闲鱼、转转、多抓鱼、爱回收 |
| 汽车 | 2 | 汽车之家、懂车帝 |
| 工具/搜索 | 10 | 百度、必应、Google、DuckDuckGo、搜狗、雅虎、Yandex、天气查询、高德POI |
| 开发者 | 2 | GitHub、Stack Overflow |
| 政府服务 | 6 | 中国政府网、国家政务服务平台、国家数据、中国裁判文书网、信用中国、国家企业信用 |

**总计已覆盖：约 94 个网站**

---

## 二、待拓展领域分析

### 2.1 高优先级缺口领域（P0）

#### 政府服务类（GOV）- 优先级：⭐⭐⭐⭐⭐

| 序号 | 网站名称 | URL | 反爬难度 | 数据价值 | 说明 |
|------|---------|-----|---------|---------|------|
| 1 | 国家政务服务平台 | https://www.gjzwfw.gov.cn/ | ⭐⭐ | ⭐⭐⭐⭐⭐ | 国家级政务入口，数据权威 |
| 2 | 中国政府网 | https://www.gov.cn/ | ⭐ | ⭐⭐⭐⭐ | 中央人民政府门户网站 |
| 3 | 国家数据 | http://www.stats.gov.cn/ | ⭐ | ⭐⭐⭐⭐⭐ | 国家统计局，经济数据权威 |
| 4 | 中国裁判文书网 | https://wenshu.court.gov.cn/ | ⭐⭐ | ⭐⭐⭐⭐⭐ | 司法数据，法律研究价值高 |
| 5 | 信用中国 | https://www.creditchina.gov.cn/ | ⭐⭐ | ⭐⭐⭐⭐ | 企业信用数据 |
| 6 | 全国政府信息公开 | https://www.gov.cn/gongkai/ | ⭐ | ⭐⭐⭐ | 政策文件查询 |
| 7 | 中国政府采购网 | https://www.ccgp.gov.cn/ | ⭐⭐ | ⭐⭐⭐ | 政府采购信息 |
| 8 | 国家企业信用信息公示系统 | https://www.gsxt.gov.cn/ | ⭐ | ⭐⭐⭐⭐ | 企业工商信息 |

**现状**：已覆盖 6 个，待拓展 2 个（政府采购网、政府信息公开）

#### 医疗健康类（HEALTH）- 优先级：⭐⭐⭐⭐⭐

| 序号 | 网站名称 | URL | 反爬难度 | 数据价值 | 说明 |
|------|---------|-----|---------|---------|------|
| 9 | 好大夫在线 | https://www.haodf.com/ | ⭐⭐⭐ | ⭐⭐⭐⭐ | 已覆盖，需增强 |
| 10 | 丁香园医院库 | https://y.dxy.cn/hospital/ | ⭐⭐ | ⭐⭐⭐⭐ | 已覆盖 |
| 11 | 挂号网 | https://www.guahao.com/ | ⭐⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 12 | 39就医助手 | https://wapyyk.39.net/ | ⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 13 | 博禾医院库 | https://h.bohe.cn/ | ⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 14 | 百度健康 | https://health.baidu.com/ | ⭐⭐ | ⭐⭐⭐ | ❌ 未覆盖 |
| 15 | 健康之路 | https://www.yihu.com/ | ⭐⭐⭐ | ⭐⭐⭐ | ❌ 未覆盖 |
| 16 | 家庭医生在线 | https://www.familydoctor.com.cn/ | ⭐⭐ | ⭐⭐⭐ | ❌ 未覆盖 |

**现状**：已覆盖 5 个，待拓展 3 个

#### 法律类（LEGAL）- 优先级：⭐⭐⭐⭐⭐

| 序号 | 网站名称 | URL | 反爬难度 | 数据价值 | 说明 |
|------|---------|-----|---------|---------|------|
| 17 | 中国法律服务网 | https://www.12348.gov.cn/ | ⭐⭐ | ⭐⭐⭐⭐ | ❌ 未覆盖 |
| 18 | 华律网 | https://www.66law.cn/ | ⭐⭐ | ⭐⭐⭐⭐ | 已覆盖 |
| 19 | 找法网 | https://china.findlaw.cn/ | ⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 20 | 中法网 | http://www.cnlaw.net/ | ⭐⭐ | ⭐⭐⭐ | ❌ 未覆盖 |
| 21 | 中国律师网 | https://www.cnlawy.com/ | ⭐⭐ | ⭐⭐⭐ | ❌ 未覆盖 |
| 22 | 好律师网 | https://www.haolvshi.com.cn/ | ⭐⭐ | ⭐⭐⭐ | ❌ 未覆盖 |
| 23 | 全国律师执业诚信信息公示平台 | https://credit.acla.org.cn/ | ⭐ | ⭐⭐⭐⭐ | ❌ 未覆盖 |

**现状**：已覆盖 2 个，待拓展 5 个

---

### 2.2 中优先级缺口领域（P1）

#### 体育类（SPORTS）- 优先级：⭐⭐⭐⭐

| 序号 | 网站名称 | URL | 反爬难度 | 数据价值 | 说明 |
|------|---------|-----|---------|---------|------|
| 24 | 虎扑 | https://www.hupu.com/ | ⭐⭐ | ⭐⭐⭐⭐ | 已覆盖 |
| 25 | 懂球帝 | https://www.dongqiudi.com/ | ⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 26 | 直播吧 | https://www.zhibo8.cc/ | ⭐ | ⭐⭐⭐ | 已覆盖 |
| 27 | 体坛周报 | https://www.titan24.com/ | ⭐ | ⭐⭐⭐ | 已覆盖 |
| 28 | 新浪体育 | https://sports.sina.com.cn/ | ⭐ | ⭐⭐⭐ | 已覆盖 |
| 29 | 腾讯体育 | https://sports.qq.com/ | ⭐ | ⭐⭐⭐ | ❌ 未覆盖 |

**现状**：已覆盖 5 个，待拓展 1 个

#### 美食/餐饮类（FOOD）- 优先级：⭐⭐⭐⭐

| 序号 | 网站名称 | URL | 反爬难度 | 数据价值 | 说明 |
|------|---------|-----|---------|---------|------|
| 30 | 大众点评 | https://www.dianping.com/ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 已覆盖 |
| 31 | 美团美食 | https://www.meituan.com/ | ⭐⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 32 | 饿了么 | https://www.ele.me/ | ⭐⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 33 | 下厨房 | https://www.xiachufang.com/ | ⭐ | ⭐⭐⭐ | 已覆盖 |
| 34 | 美食杰 | https://www.meishij.net/ | ⭐ | ⭐⭐⭐ | ❌ 未覆盖 |

**现状**：已覆盖 4 个，待拓展 1 个

#### 二手交易类（SECONDHAND）- 优先级：⭐⭐⭐

| 序号 | 网站名称 | URL | 反爬难度 | 数据价值 | 说明 |
|------|---------|-----|---------|---------|------|
| 35 | 闲鱼 | https://www.xianyu.com/ | ⭐⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 36 | 转转 | https://www.zhuanzhuan.com/ | ⭐⭐ | ⭐⭐⭐ | 已覆盖 |
| 37 | 爱回收 | https://www.iaihuishou.com/ | ⭐⭐ | ⭐⭐⭐ | ❌ 未覆盖 |
| 38 | 多抓鱼 | https://duozhuayu.com/ | ⭐ | ⭐⭐⭐ | 已覆盖 |

**现状**：已覆盖 3 个，待拓展 1 个

---

## 三、目标网站清单（20+ 高权重网站）

### 3.1 P0 优先级（最高）- 政府服务、医疗健康、法律

| 序号 | 网站名称 | 领域 | URL | 反爬难度 | 数据价值 | 实施优先级 |
|------|---------|------|-----|---------|---------|-----------|
| 1 | 国家政务服务平台 | GOV | https://www.gjzwfw.gov.cn/ | ⭐⭐ | ⭐⭐⭐⭐⭐ | P0-1 |
| 2 | 中国政府网 | GOV | https://www.gov.cn/ | ⭐ | ⭐⭐⭐⭐ | P0-2 |
| 3 | 国家数据 | GOV | http://www.stats.gov.cn/ | ⭐ | ⭐⭐⭐⭐⭐ | P0-3 |
| 4 | 中国裁判文书网 | LEGAL | https://wenshu.court.gov.cn/ | ⭐⭐ | ⭐⭐⭐⭐⭐ | P0-4 |
| 5 | 信用中国 | FINANCE | https://www.creditchina.gov.cn/ | ⭐⭐ | ⭐⭐⭐⭐ | P0-5 |
| 6 | 百度健康 | HEALTH | https://health.baidu.com/ | ⭐⭐ | ⭐⭐⭐ | P0-6 |
| 7 | 中国法律服务网 | LEGAL | https://www.12348.gov.cn/ | ⭐⭐ | ⭐⭐⭐⭐ | P0-7 |
| 8 | 健康之路 | HEALTH | https://www.yihu.com/ | ⭐⭐⭐ | ⭐⭐⭐ | P0-8 |
| 9 | 家庭医生在线 | HEALTH | https://www.familydoctor.com.cn/ | ⭐⭐ | ⭐⭐⭐ | P0-9 |
| 10 | 中法网 | LEGAL | http://www.cnlaw.net/ | ⭐⭐ | ⭐⭐⭐ | P0-10 |

### 3.2 P1 优先级（高）- 体育、美食、二手交易

| 序号 | 网站名称 | 领域 | URL | 反爬难度 | 数据价值 | 实施优先级 |
|------|---------|------|-----|---------|---------|-----------|
| 11 | 腾讯体育 | SPORTS | https://sports.qq.com/ | ⭐ | ⭐⭐⭐ | P1-1 |
| 12 | 美食杰 | FOOD | https://www.meishij.net/ | ⭐ | ⭐⭐⭐ | P1-2 |
| 13 | 爱回收 | SECONDHAND | https://www.iaihuishou.com/ | ⭐⭐ | ⭐⭐⭐ | P1-3 |
| 14 | 中国律师网 | LEGAL | https://www.cnlawy.com/ | ⭐⭐ | ⭐⭐⭐ | P1-4 |
| 15 | 好律师网 | LEGAL | https://www.haolvshi.com.cn/ | ⭐⭐ | ⭐⭐⭐ | P1-5 |
| 16 | 全国律师执业诚信信息公示平台 | LEGAL | https://credit.acla.org.cn/ | ⭐ | ⭐⭐⭐⭐ | P1-6 |
| 17 | 中国政府采购网 | GOV | https://www.ccgp.gov.cn/ | ⭐⭐ | ⭐⭐⭐ | P1-7 |
| 18 | 全国政府信息公开 | GOV | https://www.gov.cn/gongkai/ | ⭐ | ⭐⭐⭐ | P1-8 |

### 3.3 P2 优先级（中）- 补充覆盖

| 序号 | 网站名称 | 领域 | URL | 反爬难度 | 数据价值 | 实施优先级 |
|------|---------|------|-----|---------|---------|-----------|
| 19 | 豆瓣电影 | SOCIAL | https://movie.douban.com/ | ⭐⭐ | ⭐⭐⭐ | P2-1 |
| 20 | 豆瓣图书 | SOCIAL | https://book.douban.com/ | ⭐⭐ | ⭐⭐⭐ | P2-2 |
| 21 | 豆瓣小组 | SOCIAL | https://www.douban.com/group/ | ⭐⭐ | ⭐⭐⭐ | P2-3 |
| 22 | 豆瓣活动 | SOCIAL | https://www.douban.com/event/ | ⭐⭐ | ⭐⭐ | P2-4 |
| 23 | 知乎专栏 | SOCIAL | https://zhuanlan.zhihu.com/ | ⭐⭐ | ⭐⭐⭐ | P2-5 |
| 24 | 知乎热榜 | SOCIAL | https://www.zhihu.com/hot | ⭐⭐ | ⭐⭐⭐ | P2-6 |

---

## 四、优先级排序与实施建议

### 4.1 优先级矩阵

| 优先级 | 领域 | 网站数量 | 理由 |
|--------|------|---------|------|
| P0（最高） | 政府服务 | 8 | 数据权威，反爬友好，政务价值高 |
| P0（最高） | 医疗健康 | 8 | 民生需求大，好大夫已验证可行 |
| P0（最高） | 法律 | 7 | 法律服务需求大，网站结构规范 |
| P1（高） | 体育 | 6 | 数据丰富，反爬较弱 |
| P1（高） | 美食/餐饮 | 6 | 大众点评已覆盖，扩展性强 |
| P2（中） | 二手交易 | 4 | 闲鱼已覆盖，扩展性强 |
| P2（中） | 社交内容 | 6 | 豆瓣系列补充 |

### 4.2 实施路线图

**Phase 1（第1-2周）：政府服务类 P0**
- [ ] 国家政务服务平台 (gjzwfw_search.py)
- [ ] 中国政府网 (gov_cn_search.py - 增强)
- [ ] 国家数据 (stats_search.py - 增强)
- [ ] 中国政府采购网 (ccgp_search.py)

**Phase 2（第3-4周）：医疗健康类 P0**
- [ ] 百度健康 (baidu_health_search.py)
- [ ] 健康之路 (yihu_search.py)
- [ ] 家庭医生在线 (familydoctor_search.py)

**Phase 3（第5-6周）：法律类 P0**
- [ ] 中国法律服务网 (12348_search.py)
- [ ] 中法网 (cnlaw_search.py)
- [ ] 中国律师网 (cnlawyer_search.py)
- [ ] 好律师网 (haolvshi_search.py)
- [ ] 律师诚信平台 (acla_search.py)

**Phase 4（第7-8周）：体育类 P1**
- [ ] 腾讯体育 (qq_sports_search.py)

**Phase 5（第9-10周）：美食/餐饮类 P1**
- [ ] 美食杰 (meishij_search.py)

**Phase 6（第11-12周）：二手交易类 P1**
- [ ] 爱回收 (aihui_search.py - 增强)

**Phase 7（第13-14周）：社交内容类 P2**
- [ ] 豆瓣电影 (douban_movie_search.py - 增强)
- [ ] 豆瓣图书 (douban_book_search.py - 增强)
- [ ] 豆瓣小组 (douban_group_search.py - 增强)
- [ ] 豆瓣活动 (douban_event_search.py - 增强)
- [ ] 知乎专栏 (zhihu_column_search.py - 增强)
- [ ] 知乎热榜 (zhihu_hot.py - 增强)

---

## 五、技术评估要点

### 5.1 反爬机制分类

| 反爬类型 | 涉及网站 | 应对策略 |
|---------|---------|---------|
| 无/弱反爬 | 政府网站、新闻网站 | 直接抓取，注意频率控制 |
| 中等反爬 | 体育、音乐、二手平台 | 使用 stealth 模式，控制请求频率 |
| 强反爬 | 医疗挂号、法律平台 | 需登录态，结合 captcha 处理 |
| 极强反爬 | 大众点评、携程 | 需逆向签名，优先使用登录态 |

### 5.2 技术挑战

1. **政府网站**：部分网站使用老旧技术，需兼容处理
2. **医疗平台**：挂号流程复杂，需模拟完整用户流程
3. **法律平台**：部分网站需要登录态，需处理验证码
4. **体育平台**：数据更新频繁，需处理动态内容
5. **音乐平台**：部分网站有 DRM 保护，需处理音频数据

---

## 六、预期成果

### 6.1 覆盖目标

- **新增网站数量**：24 个
- **新增领域数量**：3 个（政府服务、医疗健康、法律）
- **总覆盖网站数量**：118 个（94 + 24）
- **总覆盖领域数量**：18 个（全部覆盖）

### 6.2 能力提升

1. **政务数据能力**：可抓取政府公开数据、统计数据、司法数据 ⭐⭐⭐⭐⭐
2. **医疗健康能力**：可查询医院信息、预约挂号、健康资讯 ⭐⭐⭐⭐⭐
3. **法律服务能力**：可查询律师信息、法律咨询、法律法规 ⭐⭐⭐⭐⭐
4. **体育数据能力**：可获取体育新闻、赛事数据、社区内容 ⭐⭐⭐⭐
5. **美食数据能力**：可获取餐厅信息、菜谱数据、外卖信息 ⭐⭐⭐⭐
6. **二手交易能力**：可获取二手商品信息、价格数据 ⭐⭐⭐
7. **社交内容能力**：可获取豆瓣、知乎等内容平台数据 ⭐⭐⭐

---

## 七、风险评估

| 风险类型 | 风险描述 | 缓解措施 |
|---------|---------|---------|
| 法律风险 | 部分网站数据受版权保护 | 仅抓取公开数据，遵守 robots.txt |
| 技术风险 | 部分网站反爬机制升级 | 持续监控，及时更新策略 |
| 数据风险 | 部分网站数据更新频繁 | 建立数据 freshness 检查机制 |
| 合规风险 | 部分网站需要用户授权 | 优先使用公开数据，避免敏感数据 |

---

## 八、结论

本次拓展计划覆盖 **3 个新领域**（政府服务、医疗健康、法律），新增 **24 个目标网站**，使 browser-cdp skill 的总覆盖网站达到 **118 个**，总覆盖领域达到 **18 个（全部覆盖）**。

**核心策略**：
1. **优先拓展高价值领域**：政府服务、医疗健康、法律
2. **复用已有能力**：利用已验证的搜索器架构和反爬策略
3. **分阶段实施**：按优先级分 7 个阶段，每阶段 2 周
4. **持续优化**：建立监控机制，及时更新策略

**预期收益**：
- 政务数据抓取能力：⭐⭐⭐⭐⭐
- 医疗健康数据能力：⭐⭐⭐⭐⭐
- 法律数据能力：⭐⭐⭐⭐⭐
- 体育数据能力：⭐⭐⭐⭐
- 美食数据能力：⭐⭐⭐⭐
- 二手交易能力：⭐⭐⭐
- 社交内容能力：⭐⭐⭐

---

## 附录：现有搜索器完整清单

### A.1 政府服务类（GOV）
- gov_cn_search.py - 中国政府网
- gov_service_search.py - 国家政务服务平台
- stats_search.py - 国家数据
- gsxt_search.py - 国家企业信用信息公示系统
- creditchina_search.py - 信用中国
- court_search.py - 中国裁判文书网

### A.2 医疗健康类（HEALTH）
- haodf_search.py - 好大夫在线
- dxy_hospital_search.py - 丁香园医院库
- guahaoo_search.py - 挂号网
- 39yy_search.py - 39就医助手
- bohe_search.py - 博禾医院库
- hospital_search.py - 医院搜索
- medical_search.py - 医疗搜索

### A.3 法律类（LEGAL）
- law66_search.py - 华律网
- legal_search.py - 找法网
- huilv_search.py - 华律搜索

### A.4 体育类（SPORTS）
- hupu_search.py - 虎扑
- dongqiudi_search.py - 懂球帝
- zhibo8_search.py - 直播吧
- titan24_search.py - 体坛周报
- sina_sports_search.py - 新浪体育
- tencent_sports_search.py - 腾讯体育
- sports_search.py - 体育搜索

### A.5 美食/餐饮类（FOOD）
- dianping_search.py - 大众点评
- meituan_search.py - 美团
- eleme_search.py - 饿了么
- xiachufang_search.py - 下厨房
- meishi_search.py - 美食杰
- food_search.py - 美食搜索
- meituan_waimai_search.py - 美团外卖
- taobao_waimai_search.py - 淘宝外卖

### A.6 音乐/娱乐类（MUSIC）
- music163_search.py - 网易云音乐
- qq_music_search.py - QQ音乐
- kugou_search.py - 酷狗音乐
- kuwo_search.py - 酷我音乐
- migu_search.py - 咪咕音乐
- douban_music_search.py - 豆瓣音乐

### A.7 二手交易类（SECONDHAND）
- xianyu_search.py - 闲鱼
- zhuanzhuan_search.py - 转转
- duozhuayu_search.py - 多抓鱼
- aihui_search.py - 爱回收

### A.8 旅游/出行类（TRAVEL）
- ctrip_search.py - 携程
- qunar_search.py - 去哪儿
- fliggy_search.py - 飞猪
- mafengwo_search.py - 马蜂窝
- train_search.py - 12306
- amap_poi_search.py - 高德地图

### A.9 招聘/职场类（JOB）
- 51job_search.py - 51job
- boss_zhipin_search.py - Boss直聘
- lagou_search.py - 拉勾网
- zhilian_search.py - 智联招聘
- liepin_search.py - 猎聘
- maimai_search.py - 脉脉

### A.10 房产类（REAL_ESTATE）
- lianjia_search.py - 链家
- beike_search.py - 贝壳找房
- anjuke_search.py - 安居客

### A.11 电商/购物类（ECOM）
- jd_search.py - 京东
- taobao_search.py - 淘宝
- pdd_search.py - 拼多多
- amazon_search.py - Amazon
- xianyu_search.py - 闲鱼

### A.12 新闻/资讯类（NEWS）
- sina_news.py - 新浪财经
- thp_news.py - 财联社
- wangyi_news.py - 澎湃新闻
- wangyi_open_search.py - 网易新闻
- toutiao_search.py - 今日头条

### A.13 社交/内容类（SOCIAL）
- xiaohongshu_search.py - 小红书
- zhihu_search.py - 知乎
- weibo_search.py - 微博
- douban_search.py - 豆瓣
- douyin_search.py - 抖音
- kuaishou_search.py - 快手
- xigua_search.py - 西瓜视频
- iqiyi_search.py - 爱奇艺
- youku_search.py - 优酷
- tencent_video_search.py - 腾讯视频
- bilibili_search.py - 哔哩哔哩
- music163_search.py - 网易云音乐
- wechat_search.py - 微信
- reddit_search.py - Reddit

### A.14 教育/学术类（EDU）
- arxiv_search.py - arXiv
- cnki_search.py - CNKI
- scholar_search.py - Google Scholar
- sematic_scholar_search.py - Semantic Scholar
- xuetangx_search.py - 学堂在线
- mooc_search.py - 慕课
- duozhuayu_search.py - 多助语

### A.15 汽车类（AUTO）
- autohome_search.py - 汽车之家
- dongchedi_search.py - 懂车帝

### A.16 工具/搜索类（TOOL）
- baidu_search.py - 百度
- bing_search.py - 必应
- google_search.py - Google
- duckduckgo_search.py - DuckDuckGo
- sogou_search.py - 搜狗
- yahoo_search.py - 雅虎
- yandex_search.py - Yandex
- weather_search.py - 天气查询
- amap_poi_search.py - 高德POI

### A.17 开发者类（DEV）
- github_search.py - GitHub
- stackoverflow_search.py - Stack Overflow

### A.18 金融/投资类（FINANCE）
- xueqiu_search.py - 雪球
- eastmoney_guba.py - 东方财富股吧
- creditchina_search.py - 信用中国

---

**文档版本**：v2.0  
**最后更新**：2026-08-08  
**下一步**：步骤2 - 研究目标网站的技术特征和反爬机制
