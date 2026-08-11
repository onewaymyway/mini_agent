"""
评估配置模块

定义测试网站配置、评估参数和输出路径。
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class WebsiteConfig:
    """单个网站的评估配置"""
    name: str
    url: str
    priority: str  # P0, P1, P2, P3
    category: str  # 搜索引擎、新闻资讯、电商平台等
    tech_stack: str  # 静态、动态、SPA、强反爬等
    difficulty: str  # L1, L2, L3, L4
    expected_score: int  # 预期评分
    scenarios: List[Dict[str, str]] = field(default_factory=list)  # 测试场景列表
    expected_fields: List[str] = field(default_factory=list)  # 预期提取字段
    search_selector: Optional[str] = None  # 网站特定搜索框选择器
    click_selector: Optional[str] = None  # 网站特定可点击链接选择器


# 测试网站配置列表
WEBSITE_CONFIGS: List[WebsiteConfig] = [
    # P0 级 - 核心能力验证
    WebsiteConfig(
        name="百度",
        url="https://www.baidu.com",
        priority="P0",
        category="搜索引擎",
        tech_stack="动态页面",
        difficulty="L1-L2",
        expected_score=85,
        scenarios=[
            {"id": "BDU-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "BDU-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"},
            {"id": "BDU-03", "name": "结果提取", "action": "extract", "dimension": "抓取成功率"},
            {"id": "BDU-04", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"},
            {"id": "BDU-05", "name": "自动补全", "action": "autocomplete", "dimension": "反检测能力"},
        ],
        expected_fields=["标题", "URL", "摘要", "来源"]
    ),
    WebsiteConfig(
        name="Bing",
        url="https://www.bing.com",
        priority="P0",
        category="搜索引擎",
        tech_stack="SPA 应用",
        difficulty="L1-L2",
        expected_score=85,
        scenarios=[
            {"id": "BING-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "BING-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"},
            {"id": "BING-03", "name": "结果提取", "action": "extract", "dimension": "抓取成功率"},
            {"id": "BING-04", "name": "图片搜索", "action": "switch_tab", "dimension": "交互成功率"},
            {"id": "BING-05", "name": "视频搜索", "action": "switch_tab", "dimension": "元素定位准确率"},
        ],
        expected_fields=["标题", "URL", "摘要", "来源", "发布时间"]
    ),
    WebsiteConfig(
        name="新浪新闻",
        url="https://news.sina.com.cn",
        priority="P0",
        category="新闻资讯",
        tech_stack="动态页面",
        difficulty="L1",
        expected_score=80,
        scenarios=[
            {"id": "SINA-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "SINA-02", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "SINA-03", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "SINA-04", "name": "正文提取", "action": "extract_article", "dimension": "抓取成功率"},
            {"id": "SINA-05", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"},
        ],
        expected_fields=["标题", "发布时间", "作者", "正文", "图片URL"]
    ),
    WebsiteConfig(
        name="网易新闻",
        url="https://news.163.com",
        priority="P0",
        category="新闻资讯",
        tech_stack="动态页面",
        difficulty="L1",
        expected_score=80,
        scenarios=[
            {"id": "WY-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "WY-02", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "WY-03", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "WY-04", "name": "正文提取", "action": "extract_article", "dimension": "抓取成功率"},
            {"id": "WY-05", "name": "评论提取", "action": "extract_comments", "dimension": "抓取成功率"},
        ],
        expected_fields=["标题", "发布时间", "作者", "正文", "评论数"]
    ),
    WebsiteConfig(
        name="财联社",
        url="https://www.cls.cn",
        priority="P1",
        category="新闻资讯",
        tech_stack="动态页面",
        difficulty="L1-L2",
        expected_score=75,
        search_selector="input[placeholder*='搜索'], .search-input input, #searchInput",
        scenarios=[
            {"id": "CLS-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "CLS-02", "name": "快讯列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "CLS-03", "name": "专题页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "CLS-04", "name": "实时行情提取", "action": "extract_realtime", "dimension": "抓取成功率"},
            {"id": "CLS-05", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"},
        ],
        expected_fields=["标题", "发布时间", "内容摘要", "股票代码", "实时价格"]
    ),
    WebsiteConfig(
        name="知乎",
        url="https://www.zhihu.com",
        priority="P0",
        category="社交媒体",
        tech_stack="SPA 应用",
        difficulty="L2",
        expected_score=75,
        scenarios=[
            {"id": "ZHIHU-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "ZHIHU-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"},
            {"id": "ZHIHU-03", "name": "结果提取", "action": "extract", "dimension": "抓取成功率"},
            {"id": "ZHIHU-04", "name": "问题详情页", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "ZHIHU-05", "name": "回答提取", "action": "extract_answers", "dimension": "抓取成功率"},
            {"id": "ZHIHU-06", "name": "登录态检测", "action": "check_login", "dimension": "反检测能力"},
        ],
        expected_fields=["问题标题", "问题描述", "回答内容", "作者", "点赞数", "评论数"]
    ),
    WebsiteConfig(
        name="微博",
        url="https://weibo.com",
        priority="P1",
        category="社交媒体",
        tech_stack="SPA 应用",
        difficulty="L2-L3",
        expected_score=65,
        search_selector="input[placeholder*='搜索'], .search-input input, #searchInput",
        scenarios=[
            {"id": "WB-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "WB-02", "name": "热搜提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "WB-03", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"},
            {"id": "WB-04", "name": "微博列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "WB-05", "name": "评论提取", "action": "extract_comments", "dimension": "抓取成功率"},
            {"id": "WB-06", "name": "登录态检测", "action": "check_login", "dimension": "反检测能力"},
        ],
        expected_fields=["微博内容", "发布时间", "点赞数", "评论数", "转发数", "作者"]
    ),
    # P1 级 - 扩展覆盖
    WebsiteConfig(
        name="淘宝",
        url="https://www.taobao.com",
        priority="P1",
        category="电商平台",
        tech_stack="SPA 应用",
        difficulty="L2",
        expected_score=70,
        scenarios=[
            {"id": "TB-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "TB-02", "name": "搜索商品", "action": "search", "dimension": "元素定位准确率"},
            {"id": "TB-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "TB-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "TB-05", "name": "价格提取", "action": "extract_price", "dimension": "抓取成功率"},
            {"id": "TB-06", "name": "分页浏览", "action": "paginate", "dimension": "稳定性"},
        ],
        expected_fields=["商品标题", "价格", "销量", "店铺名称", "图片URL"]
    ),
    WebsiteConfig(
        name="京东",
        url="https://www.jd.com",
        priority="P1",
        category="电商平台",
        tech_stack="SPA 应用",
        difficulty="L2",
        expected_score=70,
        scenarios=[
            {"id": "JD-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "JD-02", "name": "搜索商品", "action": "search", "dimension": "元素定位准确率"},
            {"id": "JD-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "JD-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "JD-05", "name": "价格提取", "action": "extract_price", "dimension": "抓取成功率"},
            {"id": "JD-06", "name": "规格提取", "action": "extract_specs", "dimension": "抓取成功率"},
        ],
        expected_fields=["商品标题", "价格", "自营标识", "规格参数", "图片URL"]
    ),
    WebsiteConfig(
        name="拼多多",
        url="https://www.pinduoduo.com",
        priority="P1",
        category="电商平台",
        tech_stack="SPA 应用",
        difficulty="L2",
        expected_score=65,
        search_selector="input[placeholder*='搜索'], .search-input input, #searchInput",
        scenarios=[
            {"id": "PDD-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "PDD-02", "name": "商品搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "PDD-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "PDD-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "PDD-05", "name": "拼团价格提取", "action": "extract_group_price", "dimension": "抓取成功率"},
        ],
        expected_fields=["商品标题", "价格", "拼团价格", "销量", "图片URL"]
    ),
    WebsiteConfig(
        name="Boss直聘",
        url="https://www.zhipin.com",
        priority="P1",
        category="招聘平台",
        tech_stack="动态页面",
        difficulty="L3",
        expected_score=65,
        scenarios=[
            {"id": "ZP-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "ZP-02", "name": "职位搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "ZP-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "ZP-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "ZP-05", "name": "职位信息提取", "action": "extract_job", "dimension": "抓取成功率"},
            {"id": "ZP-06", "name": "反爬检测", "action": "check_anti_crawl", "dimension": "反检测能力"},
        ],
        expected_fields=["职位名称", "薪资范围", "公司名称", "工作地点", "学历要求", "经验要求"]
    ),
    WebsiteConfig(
        name="拉勾",
        url="https://www.lagou.com",
        priority="P1",
        category="招聘平台",
        tech_stack="动态页面",
        difficulty="L3",
        expected_score=65,
        search_selector="input[placeholder*='职位'], .search-input input, [class*='search'] input",
        click_selector="a.job-card, .job-item a, [class*='job'] a[href]",
        scenarios=[
            {"id": "LG-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "LG-02", "name": "职位搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "LG-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "LG-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "LG-05", "name": "公司信息提取", "action": "extract_company", "dimension": "抓取成功率"},
        ],
        expected_fields=["职位名称", "薪资范围", "公司名称", "公司规模", "融资阶段"]
    ),
    WebsiteConfig(
        name="小红书",
        url="https://www.xiaohongshu.com",
        priority="P1",
        category="社交媒体",
        tech_stack="SPA 应用",
        difficulty="L2-L3",
        expected_score=60,
        search_selector="input[placeholder*='搜索'], .search-bar input, [class*='search'] input",
        click_selector="a.note-card, .note-item a, [class*='note'] a[href]",
        scenarios=[
            {"id": "XHS-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "XHS-02", "name": "搜索笔记", "action": "search", "dimension": "元素定位准确率"},
            {"id": "XHS-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "XHS-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "XHS-05", "name": "笔记内容提取", "action": "extract_note", "dimension": "抓取成功率"},
            {"id": "XHS-06", "name": "登录态检测", "action": "check_login", "dimension": "反检测能力"},
        ],
        expected_fields=["笔记标题", "正文内容", "作者", "点赞数", "评论数", "标签"]
    ),
    # P2 级 - 深度覆盖
    WebsiteConfig(
        name="链家",
        url="https://www.lianjia.com",
        priority="P2",
        category="房产平台",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=70,
        scenarios=[
            {"id": "LJ-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "LJ-02", "name": "房源搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "LJ-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "LJ-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "LJ-05", "name": "房源信息提取", "action": "extract_house", "dimension": "抓取成功率"},
        ],
        expected_fields=["小区名称", "房价", "户型", "面积", "朝向", "楼层"]
    ),
    WebsiteConfig(
        name="安居客",
        url="https://www.anjuke.com",
        priority="P2",
        category="房产平台",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=65,
        scenarios=[
            {"id": "AJK-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "AJK-02", "name": "房源搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "AJK-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "AJK-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "AJK-05", "name": "地图交互", "action": "switch_map", "dimension": "交互成功率"},
        ],
        expected_fields=["小区名称", "租金/售价", "户型", "面积", "位置"]
    ),
    WebsiteConfig(
        name="知网",
        url="https://www.cnki.net",
        priority="P2",
        category="学术资源",
        tech_stack="动态页面",
        difficulty="L1-L2",
        expected_score=75,
        scenarios=[
            {"id": "CNKI-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "CNKI-02", "name": "论文搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "CNKI-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "CNKI-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "CNKI-05", "name": "摘要提取", "action": "extract_abstract", "dimension": "抓取成功率"},
        ],
        expected_fields=["论文标题", "作者", "期刊", "发表时间", "摘要", "引用次数"]
    ),
    WebsiteConfig(
        name="arXiv",
        url="https://arxiv.org",
        priority="P2",
        category="学术资源",
        tech_stack="静态页面",
        difficulty="L1",
        expected_score=80,
        scenarios=[
            {"id": "ARXIV-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "ARXIV-02", "name": "论文搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "ARXIV-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "ARXIV-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "ARXIV-05", "name": "PDF 下载", "action": "download_pdf", "dimension": "交互成功率"},
        ],
        expected_fields=["论文标题", "作者", "摘要", "发表日期", "PDF URL"]
    ),
    WebsiteConfig(
        name="B站",
        url="https://www.bilibili.com",
        priority="P2",
        category="视频平台",
        tech_stack="SPA 应用",
        difficulty="L2",
        expected_score=65,
        scenarios=[
            {"id": "BILIBILI-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "BILIBILI-02", "name": "视频搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "BILIBILI-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "BILIBILI-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "BILIBILI-05", "name": "评论提取", "action": "extract_comments", "dimension": "抓取成功率"},
        ],
        expected_fields=["视频标题", "UP主", "播放量", "弹幕数", "发布时间", "评论列表"]
    ),
    # P3 级 - 专项突破
    WebsiteConfig(
        name="大众点评",
        url="https://www.dianping.com",
        priority="P3",
        category="生活服务",
        tech_stack="动态页面",
        difficulty="L3",
        expected_score=60,
        search_selector="input[placeholder*='搜索'], .search-box input, [class*='search'] input",
        click_selector="a.shop-item, .shop-list a, [class*='shop'] a[href]",
        scenarios=[
            {"id": "DP-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "DP-02", "name": "商户搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "DP-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "DP-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "DP-05", "name": "评价提取", "action": "extract_reviews", "dimension": "抓取成功率"},
        ],
        expected_fields=["商户名称", "评分", "人均消费", "地址", "评价内容"]
    ),
    WebsiteConfig(
        name="飞猪",
        url="https://www.fliggy.com",
        priority="P3",
        category="旅行平台",
        tech_stack="SPA 应用",
        difficulty="L2",
        expected_score=60,
        scenarios=[
            {"id": "FP-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "FP-02", "name": "机票搜索", "action": "search_flight", "dimension": "元素定位准确率"},
            {"id": "FP-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "FP-04", "name": "价格提取", "action": "extract_price", "dimension": "抓取成功率"},
            {"id": "FP-05", "name": "酒店搜索", "action": "switch_hotel", "dimension": "交互成功率"},
        ],
        expected_fields=["航班号", "出发时间", "到达时间", "价格", "航空公司"]
    ),
    WebsiteConfig(
        name="东方财富",
        url="https://www.eastmoney.com",
        priority="P3",
        category="金融数据",
        tech_stack="动态页面",
        difficulty="L1-L2",
        expected_score=65,
        scenarios=[
            {"id": "EM-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "EM-02", "name": "股票搜索", "action": "search_stock", "dimension": "元素定位准确率"},
            {"id": "EM-03", "name": "实时数据提取", "action": "extract_realtime", "dimension": "抓取成功率"},
            {"id": "EM-04", "name": "历史数据提取", "action": "extract_history", "dimension": "抓取成功率"},
            {"id": "EM-05", "name": "图表验证", "action": "check_chart", "dimension": "元素定位准确率"},
        ],
        expected_fields=["股票名称", "当前价格", "涨跌幅", "成交量", "成交额"]
    ),
    WebsiteConfig(
        name="雪球",
        url="https://xueqiu.com",
        priority="P3",
        category="金融社区",
        tech_stack="SPA 应用",
        difficulty="L2",
        expected_score=60,
        scenarios=[
            {"id": "XQ-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "XQ-02", "name": "股票搜索", "action": "search_stock", "dimension": "元素定位准确率"},
            {"id": "XQ-03", "name": "实时数据提取", "action": "extract_realtime", "dimension": "抓取成功率"},
            {"id": "XQ-04", "name": "讨论区访问", "action": "click_discuss", "dimension": "元素定位准确率"},
            {"id": "XQ-05", "name": "帖子提取", "action": "extract_posts", "dimension": "抓取成功率"},
        ],
        expected_fields=["股票名称", "当前价格", "涨跌幅", "讨论内容", "点赞数"]
    ),
    # ========== 新增 P0 级搜索器（步骤 3 创建）==========
    WebsiteConfig(
        name="国家政务服务平台",
        url="https://gjzwfw.www.gov.cn",
        priority="P0",
        category="政务服务",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=70,
        search_selector="input[placeholder*='搜索'], input[name='keyword'], #searchInput",
        scenarios=[
            {"id": "GOV-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "GOV-02", "name": "政务服务搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "GOV-03", "name": "事项列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "GOV-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "GOV-05", "name": "办事指南提取", "action": "extract_article", "dimension": "抓取成功率"},
        ],
        expected_fields=["事项名称", "办理部门", "办理时限", "申请材料", "办理地点"]
    ),
    WebsiteConfig(
        name="中国政府网",
        url="https://www.gov.cn",
        priority="P0",
        category="政务服务",
        tech_stack="动态页面",
        difficulty="L1",
        expected_score=80,
        search_selector="input[placeholder*='搜索'], input[name='keyword'], #searchInput",
        scenarios=[
            {"id": "GC-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "GC-02", "name": "政策搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "GC-03", "name": "政策列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "GC-04", "name": "政策详情访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "GC-05", "name": "政策正文提取", "action": "extract_article", "dimension": "抓取成功率"},
            {"id": "GC-06", "name": "PDF下载", "action": "download_pdf", "dimension": "交互成功率"},
        ],
        expected_fields=["政策标题", "发布时间", "发文单位", "正文内容", "附件链接"]
    ),
    WebsiteConfig(
        name="中国裁判文书网",
        url="https://wenshu.court.gov.cn",
        priority="P1",
        category="政务服务",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=70,
        search_selector="input[placeholder*='搜索'], input[name='keyword'], #searchInput",
        scenarios=[
            {"id": "CW-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "CW-02", "name": "案例搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "CW-03", "name": "案例列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "CW-04", "name": "案例详情访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "CW-05", "name": "文书内容提取", "action": "extract_article", "dimension": "抓取成功率"},
            {"id": "CW-06", "name": "筛选条件测试", "action": "paginate", "dimension": "交互成功率"},
        ],
        expected_fields=["案例标题", "案号", "法院名称", "裁判日期", "文书正文", "案由"]
    ),
    WebsiteConfig(
        name="12306铁路购票",
        url="https://www.12306.cn",
        priority="P0",
        category="交通出行",
        tech_stack="动态页面",
        difficulty="L3",
        expected_score=65,
        search_selector="input#fromStationText, input#toStationText, input#train_date",
        scenarios=[
            {"id": "TRAIN-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "TRAIN-02", "name": "车次查询", "action": "search", "dimension": "元素定位准确率"},
            {"id": "TRAIN-03", "name": "车次列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "TRAIN-04", "name": "票价信息提取", "action": "extract_price", "dimension": "抓取成功率"},
            {"id": "TRAIN-05", "name": "余票信息提取", "action": "extract_seats", "dimension": "抓取成功率"},
        ],
        expected_fields=["车次", "出发站", "到达站", "出发时间", "到达时间", "票价", "余票"]
    ),
    WebsiteConfig(
        name="好大夫在线",
        url="https://www.haodf.com",
        priority="P0",
        category="医疗健康",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=70,
        search_selector="input[placeholder*='医生'], input[name='keyword'], .search-input",
        scenarios=[
            {"id": "HAODF-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "HAODF-02", "name": "医生搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "HAODF-03", "name": "医生列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "HAODF-04", "name": "医生详情访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "HAODF-05", "name": "患者评价提取", "action": "extract_reviews", "dimension": "抓取成功率"},
        ],
        expected_fields=["医生姓名", "医院", "科室", "职称", "患者评价数", "好评率"]
    ),
    WebsiteConfig(
        name="汽车之家",
        url="https://www.autohome.com.cn",
        priority="P0",
        category="汽车消费",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=75,
        search_selector="input#searchKey, input[placeholder*='搜索车型'], .search-input",
        scenarios=[
            {"id": "AUTO-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "AUTO-02", "name": "车型搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "AUTO-03", "name": "车型列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "AUTO-04", "name": "车型详情访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "AUTO-05", "name": "参数配置提取", "action": "extract_article", "dimension": "抓取成功率"},
        ],
        expected_fields=["车型名称", "厂商", "指导价", "排量", "变速箱", "油耗"]
    ),
    WebsiteConfig(
        name="闲鱼二手",
        url="https://www.xianyu.com",
        priority="P0",
        category="二手交易",
        tech_stack="SPA 应用",
        difficulty="L3",
        expected_score=60,
        search_selector="input[placeholder*='搜索'], .search-input input, #searchInput",
        scenarios=[
            {"id": "XY-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "XY-02", "name": "商品搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "XY-03", "name": "商品列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "XY-04", "name": "商品详情访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "XY-05", "name": "价格信息提取", "action": "extract_price", "dimension": "抓取成功率"},
        ],
        expected_fields=["商品名称", "价格", "卖家", "成色", "所在地", "浏览量"]
    ),
    WebsiteConfig(
        name="学堂在线",
        url="https://www.xuetangx.com",
        priority="P0",
        category="在线教育",
        tech_stack="动态页面",
        difficulty="L1-L2",
        expected_score=75,
        search_selector="input[placeholder*='搜索课程'], .search-input input",
        scenarios=[
            {"id": "XTX-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "XTX-02", "name": "课程搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "XTX-03", "name": "课程列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "XTX-04", "name": "课程详情访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "XTX-05", "name": "课程信息提取", "action": "extract_article", "dimension": "抓取成功率"},
        ],
        expected_fields=["课程名称", "授课机构", "讲师", "学习人数", "课程难度", "是否免费"]
    ),
    WebsiteConfig(
        name="网易公开课",
        url="https://open.163.com",
        priority="P0",
        category="在线教育",
        tech_stack="动态页面",
        difficulty="L1",
        expected_score=80,
        search_selector="input[placeholder*='搜索'], .search-input input, #searchInput",
        scenarios=[
            {"id": "WYOPEN-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "WYOPEN-02", "name": "课程搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "WYOPEN-03", "name": "课程列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "WYOPEN-04", "name": "视频播放", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "WYOPEN-05", "name": "课程信息提取", "action": "extract_article", "dimension": "抓取成功率"},
        ],
        expected_fields=["课程名称", "来源", "时长", "播放量", "简介", "讲师"]
    ),
    WebsiteConfig(
        name="懂车帝",
        url="https://www.dongchedi.com",
        priority="P0",
        category="汽车消费",
        tech_stack="SPA 应用",
        difficulty="L2",
        expected_score=70,
        search_selector="input[placeholder*='搜索'], .search-bar input, #searchInput",
        scenarios=[
            {"id": "DCD-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "DCD-02", "name": "车型搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "DCD-03", "name": "车型列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "DCD-04", "name": "车型详情访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "DCD-05", "name": "参数配置提取", "action": "extract_article", "dimension": "抓取成功率"},
        ],
        expected_fields=["车型名称", "厂商", "指导价", "续航", "百公里加速", "电池类型"]
    ),
    # ========== 新增 P0 级网站（步骤 2 新增）==========
    WebsiteConfig(
        name="豆瓣",
        url="https://www.douban.com",
        priority="P0",
        category="文化社区",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=75,
        search_selector="input[placeholder*='搜索'], .search-input input, #searchInput",
        scenarios=[
            {"id": "DB-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "DB-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"},
            {"id": "DB-03", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "DB-04", "name": "详情页访问", "action": "click_detail", "dimension": "元素定位准确率"},
            {"id": "DB-05", "name": "评价提取", "action": "extract_reviews", "dimension": "抓取成功率"},
        ],
        expected_fields=["标题", "评分", "评论数", "简介", "封面图"]
    ),
    WebsiteConfig(
        name="抖音",
        url="https://www.douyin.com",
        priority="P0",
        category="短视频",
        tech_stack="SPA 应用",
        difficulty="L3",
        expected_score=60,
        search_selector="input[placeholder*='搜索'], .search-input input",
        scenarios=[
            {"id": "DY-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "DY-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"},
            {"id": "DY-03", "name": "无限滚动", "action": "infinite_scroll", "dimension": "稳定性"},
            {"id": "DY-04", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "DY-05", "name": "视频信息提取", "action": "extract_article", "dimension": "抓取成功率"},
        ],
        expected_fields=["视频标题", "作者", "点赞数", "评论数", "播放量"]
    ),
    WebsiteConfig(
        name="快手",
        url="https://www.kuaishou.com",
        priority="P0",
        category="短视频",
        tech_stack="SPA 应用",
        difficulty="L3",
        expected_score=60,
        search_selector="input[placeholder*='搜索'], .search-input input",
        scenarios=[
            {"id": "KS-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "KS-02", "name": "搜索查询", "action": "search", "dimension": "元素定位准确率"},
            {"id": "KS-03", "name": "无限滚动", "action": "infinite_scroll", "dimension": "稳定性"},
            {"id": "KS-04", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "KS-05", "name": "视频信息提取", "action": "extract_article", "dimension": "抓取成功率"},
        ],
        expected_fields=["视频标题", "作者", "点赞数", "评论数", "播放量"]
    ),
    # ========== 新增 P1 级网站（步骤 2 新增）==========
    WebsiteConfig(
        name="携程",
        url="https://www.ctrip.com",
        priority="P1",
        category="旅行平台",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=70,
        search_selector="input[placeholder*='搜索'], .search-input input",
        scenarios=[
            {"id": "CT-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "CT-02", "name": "机票搜索", "action": "search_flight", "dimension": "元素定位准确率"},
            {"id": "CT-03", "name": "填写表单", "action": "fill_form", "dimension": "交互成功率"},
            {"id": "CT-04", "name": "结果提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "CT-05", "name": "价格信息提取", "action": "extract_price", "dimension": "抓取成功率"},
        ],
        expected_fields=["航班号", "出发地", "目的地", "出发时间", "到达时间", "价格"]
    ),
    WebsiteConfig(
        name="美团",
        url="https://www.meituan.com",
        priority="P1",
        category="生活服务",
        tech_stack="动态页面",
        difficulty="L2",
        expected_score=70,
        search_selector="input[placeholder*='搜索'], .search-input input",
        scenarios=[
            {"id": "MT-01", "name": "首页访问", "action": "navigate", "dimension": "页面访问成功率"},
            {"id": "MT-02", "name": "商户搜索", "action": "search", "dimension": "元素定位准确率"},
            {"id": "MT-03", "name": "应用筛选", "action": "apply_filter", "dimension": "交互成功率"},
            {"id": "MT-04", "name": "列表提取", "action": "extract_list", "dimension": "抓取成功率"},
            {"id": "MT-05", "name": "商户详情访问", "action": "click_detail", "dimension": "元素定位准确率"},
        ],
        expected_fields=["商户名称", "评分", "价格", "地址", "评论数", "营业时间"]
    ),
]


# 评估参数配置
EVAL_CONFIG = {
    # 超时设置（秒）
    "timeout": {
        "page_load": 30,           # 页面加载超时
        "element_wait": 10,        # 元素等待超时
        "interaction": 15,         # 交互操作超时
        "total": 300,              # 单次评估总超时
    },
    # 重试设置
    "retry": {
        "max_attempts": 3,         # 最大重试次数
        "delay": 2,                # 重试延迟（秒）
        "backoff": 2,              # 退避倍数
    },
    # 代理设置
    "proxy": {
        "enabled": True,           # 是否启用代理
        "pool_size": 5,            # 代理池大小
        "rotate_interval": 60,     # 轮换间隔（秒）
    },
    # 输出设置
    "output": {
        "json_dir": "output/eval_results",
        "markdown_dir": "output/eval_reports",
        "screenshot_dir": "output/screenshots",
        "log_dir": "logs/eval",
    },
    # 性能监控
    "monitoring": {
        "enable_performance": True,  # 是否监控性能
        "enable_memory": True,       # 是否监控内存
        "sample_interval": 5,        # 采样间隔（秒）
    },
}


def get_website_by_name(name: str) -> Optional[WebsiteConfig]:
    """根据名称获取网站配置"""
    for config in WEBSITE_CONFIGS:
        if config.name == name:
            return config
    return None


def get_websites_by_priority(priority: str) -> List[WebsiteConfig]:
    """根据优先级获取网站列表"""
    return [w for w in WEBSITE_CONFIGS if w.priority == priority]


def get_websites_by_category(category: str) -> List[WebsiteConfig]:
    """根据分类获取网站列表"""
    return [w for w in WEBSITE_CONFIGS if w.category == category]


def get_all_scenarios() -> List[Dict[str, str]]:
    """获取所有测试场景"""
    scenarios = []
    for config in WEBSITE_CONFIGS:
        for scenario in config.scenarios:
            scenarios.append({
                **scenario,
                "website": config.name,
                "priority": config.priority,
            })
    return scenarios


def ensure_output_dirs():
    """确保输出目录存在"""
    for dir_path in EVAL_CONFIG["output"].values():
        full_path = os.path.join(os.path.dirname(__file__), "..", dir_path)
        os.makedirs(full_path, exist_ok=True)



