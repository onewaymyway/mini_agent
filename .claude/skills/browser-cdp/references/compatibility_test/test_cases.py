"""
兼容性测试用例定义

定义电商、新闻、社交、政务四类网站的测试用例。
"""

from .models import (
    AntiCrawlLevel,
    Category,
    ExpectedResult,
    Priority,
    Step,
    TestCase,
    WebsiteConfig,
)


def create_ecom_websites() -> list:
    """创建电商类网站配置"""
    return [
        WebsiteConfig(
            name="京东",
            url="https://www.jd.com/",
            category=Category.ECOM,
            subcategory="ECOM_B2C",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.STRONG,
            login_required=True,
            priority=Priority.P0,
            timeout=60,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="jd_search.py",
        ),
        WebsiteConfig(
            name="淘宝",
            url="https://www.taobao.com/",
            category=Category.ECOM,
            subcategory="ECOM_B2C",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.VERY_STRONG,
            login_required=True,
            priority=Priority.P0,
            timeout=60,
            retry_count=3,
            target_success_rate=0.90,
            target_accuracy=0.85,
            searcher_file="taobao_search.py",
        ),
        WebsiteConfig(
            name="拼多多",
            url="https://www.pinduoduo.com/",
            category=Category.ECOM,
            subcategory="ECOM_B2C",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.STRONG,
            login_required=False,
            priority=Priority.P0,
            timeout=60,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="pdd_search.py",
        ),
        WebsiteConfig(
            name="Amazon",
            url="https://www.amazon.com/",
            category=Category.ECOM,
            subcategory="ECOM_GLOBAL",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.STRONG,
            login_required=False,
            priority=Priority.P1,
            timeout=60,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="amazon_search.py",
        ),
        WebsiteConfig(
            name="闲鱼",
            url="https://www.xianyu.com/",
            category=Category.ECOM,
            subcategory="ECOM_C2C",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.STRONG,
            login_required=True,
            priority=Priority.P1,
            timeout=60,
            retry_count=3,
            target_success_rate=0.90,
            target_accuracy=0.85,
            searcher_file="xianyu_search.py",
        ),
        WebsiteConfig(
            name="携程",
            url="https://www.ctrip.com/",
            category=Category.ECOM,
            subcategory="TRAVEL_FLIGHT",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.STRONG,
            login_required=False,
            priority=Priority.P1,
            timeout=60,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="ctrip_search.py",
        ),
    ]


def create_news_websites() -> list:
    """创建新闻类网站配置"""
    return [
        WebsiteConfig(
            name="新浪财经",
            url="https://finance.sina.com.cn/",
            category=Category.NEWS,
            subcategory="NEWS_FINANCE",
            frontend_framework="SSR",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            priority=Priority.P0,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="sina_news.py",
        ),
        WebsiteConfig(
            name="财联社",
            url="https://www.cls.cn/",
            category=Category.NEWS,
            subcategory="NEWS_FINANCE",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            priority=Priority.P0,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="thp_news.py",
        ),
        WebsiteConfig(
            name="澎湃新闻",
            url="https://www.thepaper.cn/",
            category=Category.NEWS,
            subcategory="NEWS_GENERAL",
            frontend_framework="Vue",
            anti_crawl_level=AntiCrawlLevel.WEAK,
            login_required=False,
            priority=Priority.P0,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="wangyi_news.py",
        ),
        WebsiteConfig(
            name="网易新闻",
            url="https://news.163.com/",
            category=Category.NEWS,
            subcategory="NEWS_GENERAL",
            frontend_framework="SSR",
            anti_crawl_level=AntiCrawlLevel.WEAK,
            login_required=False,
            priority=Priority.P0,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="wangyi_open_search.py",
        ),
        WebsiteConfig(
            name="今日头条",
            url="https://www.toutiao.com/",
            category=Category.NEWS,
            subcategory="NEWS_TECH",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            priority=Priority.P1,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="toutiao_search.py",
        ),
        WebsiteConfig(
            name="虎嗅",
            url="https://www.huxiu.com/",
            category=Category.NEWS,
            subcategory="NEWS_TECH",
            frontend_framework="Vue",
            anti_crawl_level=AntiCrawlLevel.WEAK,
            login_required=False,
            priority=Priority.P2,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="",
        ),
    ]


def create_social_websites() -> list:
    """创建社交类网站配置"""
    return [
        WebsiteConfig(
            name="知乎",
            url="https://www.zhihu.com/",
            category=Category.SOCIAL,
            subcategory="SOCIAL_FORUM",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=True,
            priority=Priority.P0,
            timeout=60,
            retry_count=3,
            target_success_rate=0.90,
            target_accuracy=0.85,
            searcher_file="zhihu_search.py",
        ),
        WebsiteConfig(
            name="小红书",
            url="https://www.xiaohongshu.com/",
            category=Category.SOCIAL,
            subcategory="SOCIAL_LIFESTYLE",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.VERY_STRONG,
            login_required=True,
            priority=Priority.P0,
            timeout=60,
            retry_count=3,
            target_success_rate=0.85,
            target_accuracy=0.80,
            searcher_file="xiaohongshu_search.py",
        ),
        WebsiteConfig(
            name="微博",
            url="https://weibo.com/",
            category=Category.SOCIAL,
            subcategory="SOCIAL_FORUM",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=True,
            priority=Priority.P0,
            timeout=60,
            retry_count=3,
            target_success_rate=0.90,
            target_accuracy=0.85,
            searcher_file="weibo_search.py",
        ),
        WebsiteConfig(
            name="豆瓣",
            url="https://www.douban.com/",
            category=Category.SOCIAL,
            subcategory="SOCIAL_FORUM",
            frontend_framework="Vue",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            priority=Priority.P1,
            timeout=60,
            retry_count=3,
            target_success_rate=0.90,
            target_accuracy=0.85,
            searcher_file="douban_search.py",
        ),
        WebsiteConfig(
            name="抖音",
            url="https://www.douyin.com/",
            category=Category.SOCIAL,
            subcategory="SOCIAL_SHORT_VIDEO",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.VERY_STRONG,
            login_required=True,
            priority=Priority.P1,
            timeout=60,
            retry_count=3,
            target_success_rate=0.85,
            target_accuracy=0.80,
            searcher_file="douyin_search.py",
        ),
        WebsiteConfig(
            name="哔哩哔哩",
            url="https://www.bilibili.com/",
            category=Category.SOCIAL,
            subcategory="SOCIAL_LONG_VIDEO",
            frontend_framework="Vue",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            priority=Priority.P1,
            timeout=60,
            retry_count=3,
            target_success_rate=0.90,
            target_accuracy=0.85,
            searcher_file="bilibili_search.py",
        ),
        WebsiteConfig(
            name="网易云音乐",
            url="https://music.163.com/",
            category=Category.SOCIAL,
            subcategory="SOCIAL_MUSIC",
            frontend_framework="React",
            anti_crawl_level=AntiCrawlLevel.STRONG,
            login_required=False,
            priority=Priority.P2,
            timeout=60,
            retry_count=3,
            target_success_rate=0.90,
            target_accuracy=0.85,
            searcher_file="music163_search.py",
        ),
        WebsiteConfig(
            name="微信",
            url="https://weixin.qq.com/",
            category=Category.SOCIAL,
            subcategory="SOCIAL_WECHAT",
            frontend_framework="混合架构",
            anti_crawl_level=AntiCrawlLevel.VERY_STRONG,
            login_required=True,
            priority=Priority.P2,
            timeout=60,
            retry_count=3,
            target_success_rate=0.85,
            target_accuracy=0.80,
            searcher_file="wechat_search.py",
        ),
    ]


def create_gov_websites() -> list:
    """创建政务类网站配置"""
    return [
        WebsiteConfig(
            name="中国政府网",
            url="https://www.gov.cn/",
            category=Category.GOV,
            subcategory="GOV_PORTAL",
            frontend_framework="SSR",
            anti_crawl_level=AntiCrawlLevel.WEAK,
            login_required=False,
            priority=Priority.P0,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="gov_cn_search.py",
        ),
        WebsiteConfig(
            name="国家政务服务平台",
            url="https://www.gjzwfw.gov.cn/",
            category=Category.GOV,
            subcategory="GOV_SERVICE",
            frontend_framework="Vue",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            priority=Priority.P0,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="gov_service_search.py",
        ),
        WebsiteConfig(
            name="国家数据",
            url="http://www.stats.gov.cn/",
            category=Category.GOV,
            subcategory="GOV_STATS",
            frontend_framework="SSR",
            anti_crawl_level=AntiCrawlLevel.WEAK,
            login_required=False,
            priority=Priority.P0,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="stats_search.py",
        ),
        WebsiteConfig(
            name="中国政府采购网",
            url="https://www.ccgp.gov.cn/",
            category=Category.GOV,
            subcategory="GOV_PROCUREMENT",
            frontend_framework="SSR",
            anti_crawl_level=AntiCrawlLevel.WEAK,
            login_required=False,
            priority=Priority.P1,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="",
        ),
        WebsiteConfig(
            name="信用中国",
            url="https://www.creditchina.gov.cn/",
            category=Category.GOV,
            subcategory="GOV_CREDIT",
            frontend_framework="SSR",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            priority=Priority.P1,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="creditchina_search.py",
        ),
        WebsiteConfig(
            name="中国裁判文书网",
            url="https://wenshu.court.gov.cn/",
            category=Category.GOV,
            subcategory="LEGAL_COURT",
            frontend_framework="SSR",
            anti_crawl_level=AntiCrawlLevel.MEDIUM,
            login_required=False,
            priority=Priority.P1,
            timeout=30,
            retry_count=3,
            target_success_rate=0.95,
            target_accuracy=0.90,
            searcher_file="court_search.py",
        ),
    ]


def create_ecom_test_cases() -> dict:
    """创建电商类测试用例"""
    return {
        "京东": [
            TestCase(
                case_id="JD-01",
                name="首页访问",
                description="验证京东首页能否正常加载",
                steps=[
                    Step(action="navigate", target="https://www.jd.com/", description="导航到京东首页"),
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
            ),
            TestCase(
                case_id="JD-02",
                name="商品搜索",
                description="验证京东商品搜索功能",
                steps=[
                    Step(action="navigate", target="https://www.jd.com/", description="导航到京东首页"),
                    Step(action="input", target="#searchInput", value="iPhone 15", description="输入搜索关键词"),
                    Step(action="click", target="#searchBtn", description="点击搜索按钮"),
                    Step(action="wait", target="", timeout=5, description="等待搜索结果加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="搜索结果页加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["元素定位准确率", "抓取成功率"],
                pass_criteria={"element_locate_accuracy": 0.90, "data_extraction_success_rate": 0.85},
            ),
            TestCase(
                case_id="JD-03",
                name="结果提取",
                description="验证商品信息提取能力",
                steps=[
                    Step(action="navigate", target="https://search.jd.com/Search?keyword=iPhone", description="导航到搜索结果页"),
                    Step(action="wait", target="", timeout=5, description="等待页面加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="成功提取至少5条商品信息",
                        expected_value=5,
                        check_type="greater_than",
                    ),
                ],
                evaluation_dimensions=["抓取成功率"],
                pass_criteria={"data_extraction_success_rate": 0.85},
            ),
        ],
        "淘宝": [
            TestCase(
                case_id="TB-01",
                name="首页访问",
                description="验证淘宝首页能否正常加载",
                steps=[
                    Step(action="navigate", target="https://www.taobao.com/", description="导航到淘宝首页"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="页面标题包含'淘宝'",
                        expected_value="淘宝",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["页面访问成功率"],
                pass_criteria={"page_access_success_rate": 0.90},
            ),
            TestCase(
                case_id="TB-02",
                name="商品搜索",
                description="验证淘宝商品搜索功能",
                steps=[
                    Step(action="navigate", target="https://www.taobao.com/", description="导航到淘宝首页"),
                    Step(action="input", target="#q", value="手机", description="输入搜索关键词"),
                    Step(action="click", target=".btn-search", description="点击搜索按钮"),
                    Step(action="wait", target="", timeout=5, description="等待搜索结果加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="搜索结果页加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["元素定位准确率", "抓取成功率"],
                pass_criteria={"element_locate_accuracy": 0.85, "data_extraction_success_rate": 0.80},
            ),
            TestCase(
                case_id="TB-03",
                name="登录检测",
                description="验证登录状态检测能力",
                steps=[
                    Step(action="navigate", target="https://www.taobao.com/", description="导航到淘宝首页"),
                    Step(action="wait", target="", timeout=3, description="等待页面加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="检测到登录状态",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["反检测能力"],
                pass_criteria={"anti_detection_ability": 0.80},
            ),
        ],
    }


def create_news_test_cases() -> dict:
    """创建新闻类测试用例"""
    return {
        "新浪财经": [
            TestCase(
                case_id="SINA-01",
                name="首页访问",
                description="验证新浪财经首页能否正常加载",
                steps=[
                    Step(action="navigate", target="https://finance.sina.com.cn/", description="导航到新浪财经首页"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="页面加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["页面访问成功率"],
                pass_criteria={"page_access_success_rate": 0.95},
            ),
            TestCase(
                case_id="SINA-02",
                name="列表提取",
                description="验证新闻列表提取能力",
                steps=[
                    Step(action="navigate", target="https://finance.sina.com.cn/", description="导航到新浪财经首页"),
                    Step(action="wait", target="", timeout=5, description="等待页面加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="成功提取至少15条新闻",
                        expected_value=15,
                        check_type="greater_than",
                    ),
                ],
                evaluation_dimensions=["抓取成功率"],
                pass_criteria={"data_extraction_success_rate": 0.90},
            ),
            TestCase(
                case_id="SINA-03",
                name="详情页访问",
                description="验证新闻详情页访问能力",
                steps=[
                    Step(action="navigate", target="https://finance.sina.com.cn/", description="导航到新浪财经首页"),
                    Step(action="click", target=".news-item:first-child a", description="点击第一条新闻"),
                    Step(action="wait", target="", timeout=5, description="等待详情页加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="成功进入详情页",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["元素定位准确率"],
                pass_criteria={"element_locate_accuracy": 0.90},
            ),
        ],
        "澎湃新闻": [
            TestCase(
                case_id="TP-01",
                name="首页访问",
                description="验证澎湃新闻首页能否正常加载",
                steps=[
                    Step(action="navigate", target="https://www.thepaper.cn/", description="导航到澎湃新闻首页"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="页面加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["页面访问成功率"],
                pass_criteria={"page_access_success_rate": 0.95},
            ),
            TestCase(
                case_id="TP-02",
                name="列表提取",
                description="验证新闻列表提取能力",
                steps=[
                    Step(action="navigate", target="https://www.thepaper.cn/", description="导航到澎湃新闻首页"),
                    Step(action="wait", target="", timeout=5, description="等待页面加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="成功提取至少15条新闻",
                        expected_value=15,
                        check_type="greater_than",
                    ),
                ],
                evaluation_dimensions=["抓取成功率"],
                pass_criteria={"data_extraction_success_rate": 0.90},
            ),
        ],
    }


def create_social_test_cases() -> dict:
    """创建社交类测试用例"""
    return {
        "知乎": [
            TestCase(
                case_id="ZHIHU-01",
                name="首页访问",
                description="验证知乎首页能否正常加载",
                steps=[
                    Step(action="navigate", target="https://www.zhihu.com/", description="导航到知乎首页"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="页面加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["页面访问成功率"],
                pass_criteria={"page_access_success_rate": 0.90},
            ),
            TestCase(
                case_id="ZHIHU-02",
                name="搜索查询",
                description="验证知乎搜索功能",
                steps=[
                    Step(action="navigate", target="https://www.zhihu.com/", description="导航到知乎首页"),
                    Step(action="input", target="#searchInput", value="Python", description="输入搜索关键词"),
                    Step(action="click", target="#searchBtn", description="点击搜索按钮"),
                    Step(action="wait", target="", timeout=5, description="等待搜索结果加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="搜索结果页加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["元素定位准确率", "抓取成功率"],
                pass_criteria={"element_locate_accuracy": 0.85, "data_extraction_success_rate": 0.80},
            ),
            TestCase(
                case_id="ZHIHU-03",
                name="登录态验证",
                description="验证登录状态检测能力",
                steps=[
                    Step(action="navigate", target="https://www.zhihu.com/", description="导航到知乎首页"),
                    Step(action="wait", target="", timeout=3, description="等待页面加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="检测到登录状态",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["反检测能力"],
                pass_criteria={"anti_detection_ability": 0.80},
            ),
        ],
        "小红书": [
            TestCase(
                case_id="XHS-01",
                name="首页访问",
                description="验证小红书首页能否正常加载",
                steps=[
                    Step(action="navigate", target="https://www.xiaohongshu.com/", description="导航到小红书首页"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="页面加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["页面访问成功率"],
                pass_criteria={"page_access_success_rate": 0.85},
            ),
            TestCase(
                case_id="XHS-02",
                name="搜索查询",
                description="验证小红书搜索功能",
                steps=[
                    Step(action="navigate", target="https://www.xiaohongshu.com/", description="导航到小红书首页"),
                    Step(action="input", target="#searchInput", value="美食", description="输入搜索关键词"),
                    Step(action="click", target="#searchBtn", description="点击搜索按钮"),
                    Step(action="wait", target="", timeout=5, description="等待搜索结果加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="搜索结果页加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["元素定位准确率", "抓取成功率"],
                pass_criteria={"element_locate_accuracy": 0.80, "data_extraction_success_rate": 0.75},
            ),
        ],
    }


def create_gov_test_cases() -> dict:
    """创建政务类测试用例"""
    return {
        "中国政府网": [
            TestCase(
                case_id="GOV-01",
                name="首页访问",
                description="验证中国政府网首页能否正常加载",
                steps=[
                    Step(action="navigate", target="https://www.gov.cn/", description="导航到中国政府网首页"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="页面加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["页面访问成功率"],
                pass_criteria={"page_access_success_rate": 0.95},
            ),
            TestCase(
                case_id="GOV-02",
                name="政策搜索",
                description="验证政策搜索功能",
                steps=[
                    Step(action="navigate", target="https://www.gov.cn/", description="导航到中国政府网首页"),
                    Step(action="input", target="#searchInput", value="人工智能", description="输入搜索关键词"),
                    Step(action="click", target="#searchBtn", description="点击搜索按钮"),
                    Step(action="wait", target="", timeout=5, description="等待搜索结果加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="搜索结果页加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["元素定位准确率", "抓取成功率"],
                pass_criteria={"element_locate_accuracy": 0.90, "data_extraction_success_rate": 0.85},
            ),
            TestCase(
                case_id="GOV-03",
                name="政策详情",
                description="验证政策详情页访问能力",
                steps=[
                    Step(action="navigate", target="https://www.gov.cn/", description="导航到中国政府网首页"),
                    Step(action="click", target=".news-item:first-child a", description="点击第一条政策"),
                    Step(action="wait", target="", timeout=5, description="等待详情页加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="成功进入详情页",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["元素定位准确率"],
                pass_criteria={"element_locate_accuracy": 0.90},
            ),
        ],
        "国家数据": [
            TestCase(
                case_id="STATS-01",
                name="首页访问",
                description="验证国家数据首页能否正常加载",
                steps=[
                    Step(action="navigate", target="http://www.stats.gov.cn/", description="导航到国家数据首页"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="页面加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["页面访问成功率"],
                pass_criteria={"page_access_success_rate": 0.95},
            ),
            TestCase(
                case_id="STATS-02",
                name="数据查询",
                description="验证数据查询功能",
                steps=[
                    Step(action="navigate", target="http://www.stats.gov.cn/", description="导航到国家数据首页"),
                    Step(action="click", target="#queryBtn", description="点击查询按钮"),
                    Step(action="wait", target="", timeout=5, description="等待查询结果加载"),
                ],
                expected_results=[
                    ExpectedResult(
                        condition="查询结果页加载成功",
                        check_type="contains",
                    ),
                ],
                evaluation_dimensions=["元素定位准确率", "抓取成功率"],
                pass_criteria={"element_locate_accuracy": 0.90, "data_extraction_success_rate": 0.85},
            ),
        ],
    }


def get_all_test_cases() -> dict:
    """
    获取所有测试用例

    Returns:
        按网站名称分组的测试用例字典
    """
    return {
        **create_ecom_test_cases(),
        **create_news_test_cases(),
        **create_social_test_cases(),
        **create_gov_test_cases(),
    }


def get_all_websites() -> list:
    """
    获取所有网站配置

    Returns:
        网站配置列表
    """
    return (
        create_ecom_websites()
        + create_news_websites()
        + create_social_websites()
        + create_gov_websites()
    )
