# ============================================================================
# Test Case Definitions
# ============================================================================

# P0 Core Capability Test Cases
P0_TEST_CASES: List[TestCase] = [
    # OP-001 Page Loading Capability
    TestCase("NAV-001", "Homepage Access Test", "GovCN", "OP-001", "P0", "navigate", timeout=15),
    TestCase("NAV-002", "Search Page Access Test", "Baidu", "OP-001", "P0", "navigate", timeout=15),
    TestCase("NAV-003", "Dynamic Page Loading Test", "Zhihu", "OP-001", "P0", "navigate", timeout=20),
    TestCase("NAV-004", "Timeout Handling Test", "StatsCN", "OP-001", "P0", "navigate", timeout=10),
    TestCase("NAV-005", "Redirect Handling Test", "CredChina", "OP-001", "P0", "navigate", timeout=15),

    # OP-002 Element Location Capability
    TestCase("LOC-001", "CSS Selector Location", "Baidu", "OP-002", "P0", "locate", selector="#kw"),
    TestCase("LOC-002", "XPath Location", "Zhihu", "OP-002", "P0", "locate", selector="//button[contains(text(), 'Login')]"),
    TestCase("LOC-003", "Text Match Location", "SinaNews", "OP-002", "P0", "locate", selector="text=News"),
    TestCase("LOC-004", "Attribute Match Location", "Douban", "OP-002", "P0", "locate", selector="[data-id]"),
    TestCase("LOC-005", "Relative Location Test", "Taobao", "OP-002", "P0", "locate", selector=".item"),

    # OP-003 Click Interaction
    TestCase("CLK-001", "Link Click Test", "GovCN", "OP-003", "P0", "click", selector="a[href*='news']"),
    TestCase("CLK-002", "Button Click Test", "Baidu", "OP-003", "P0", "click", selector="#su"),
    TestCase("CLK-003", "Checkbox Click Test", "Zhihu", "OP-003", "P0", "click", selector="input[type='checkbox']"),
    TestCase("CLK-004", "Dropdown Select Test", "JobSite", "OP-003", "P0", "click", selector="select"),
    TestCase("CLK-005", "Dynamic Element Click", "Taobao", "OP-003", "P0", "click", selector=".item-click"),

    # OP-004 Input Operations
    TestCase("INP-001", "Text Input Test", "Baidu", "OP-004", "P0", "input", selector="#kw", expected_fields=["SearchBoxContent"]),
    TestCase("INP-002", "Clear Input Test", "Zhihu", "OP-004", "P0", "input", selector="input[type='text']"),
    TestCase("INP-003", "Special Character Input", "JobSite", "OP-004", "P0", "input", selector="input"),
    TestCase("INP-004", "Date Selection Test", "12306", "OP-004", "P0", "input", selector="input[type='date']"),
    TestCase("INP-005", "Multi-line Text Input", "Douban", "OP-004", "P0", "input", selector="textarea"),

    # OP-005 Data Extraction
    TestCase("EXT-001", "Title Extraction Test", "GovCN", "OP-005", "P0", "extract", expected_fields=["Title"]),
    TestCase("EXT-002", "List Extraction Test", "SinaNews", "OP-005", "P0", "extract", expected_fields=["Title", "Link", "Summary"]),
    TestCase("EXT-003", "Link Extraction Test", "Baidu", "OP-005", "P0", "extract", expected_fields=["URL"]),
    TestCase("EXT-004", "Image Extraction Test", "Douban", "OP-005", "P0", "extract", expected_fields=["ImageURL"]),
    TestCase("EXT-005", "Table Data Extraction", "StatsCN", "OP-005", "P0", "extract", expected_fields=["TableData"]),
    TestCase("EXT-006", "Dynamic Content Extraction", "Xueqiu", "OP-005", "P0", "extract", expected_fields=["StockPrice"]),

    # OP-006 Screenshot Capability
    TestCase("SHT-001", "Full Page Screenshot Test", "Baidu", "OP-006", "P0", "screenshot", expected_fields=["ScreenshotFile"]),
    TestCase("SHT-002", "Element Screenshot Test", "Zhihu", "OP-006", "P0", "screenshot", expected_fields=["ScreenshotFile"]),
    TestCase("SHT-003", "Annotated Screenshot Test", "GovCN", "OP-006", "P0", "screenshot", expected_fields=["AnnotatedScreenshot"]),

    # OP-007 Scroll Operations
    TestCase("SCR-001", "Scroll Down Test", "Zhihu", "OP-007", "P0", "scroll", expected_fields=["ScrollDistance"]),
    TestCase("SCR-002", "Infinite Scroll Test", "Weibo", "OP-007", "P0", "scroll", expected_fields=["LoadedContentCount"]),
    TestCase("SCR-003", "Scroll to Element Test", "Douban", "OP-007", "P0", "scroll", expected_fields=["ElementVisible"]),

    # OP-008 Tab Management
    TestCase("TAB-001", "New Tab Open Test", "Baidu", "OP-008", "P0", "new_tab", expected_fields=["TabCount"]),
    TestCase("TAB-002", "Tab Switch Test", "Zhihu", "OP-008", "P0", "switch_tab", expected_fields=["CurrentURL"]),
    TestCase("TAB-003", "Tab Close Test", "GovCN", "OP-008", "P0", "close_tab", expected_fields=["TabCount"]),

    # OP-009 Wait Strategy
    TestCase("WAI-001", "Element Appearance Wait Test", "Baidu", "OP-009", "P0", "wait", expected_fields=["WaitTime"]),
    TestCase("WAI-002", "Network Idle Wait Test", "Zhihu", "OP-009", "P0", "wait", expected_fields=["WaitTime"]),
    TestCase("WAI-003", "Page Stable Wait Test", "Taobao", "OP-009", "P0", "wait", expected_fields=["WaitTime"]),

    # OP-010 Error Recovery
    TestCase("ERR-001", "Network Error Recovery Test", "GovCN", "OP-010", "P0", "retry", expected_fields=["RetryCount"]),
    TestCase("ERR-002", "Element Disappear Recovery Test", "Zhihu", "OP-010", "P0", "retry", expected_fields=["RecoveryTime"]),
    TestCase("ERR-003", "Page Timeout Recovery Test", "Taobao", "OP-010", "P0", "retry", expected_fields=["RetryCount"]),
]

# P1 Advanced Capability Test Cases
P1_TEST_CASES: List[TestCase] = [
    # OP-101 Dynamic Content Handling
    TestCase("DYN-001", "SPA Route Detection Test", "Taobao", "OP-101", "P1", "navigate", expected_fields=["URLChange"]),
    TestCase("DYN-002", "AJAX Request Monitor Test", "Zhihu", "OP-101", "P1", "extract", expected_fields=["RequestData"]),
    TestCase("DYN-003", "Dynamic Content Wait Test", "Weibo", "OP-101", "P1", "extract", expected_fields=["LoadedContent"]),

    # OP-102 Infinite Scroll
    TestCase("INF-001", "Weibo Infinite Scroll Test", "Weibo", "OP-102", "P1", "scroll", expected_fields=["LoadedWeiboCount"]),
    TestCase("INF-002", "Zhihu Feed Test", "Zhihu", "OP-102", "P1", "scroll", expected_fields=["LoadedAnswerCount"]),
    TestCase("INF-003", "Xiaohongshu Note Load Test", "Xiaohongshu", "OP-102", "P1", "scroll", expected_fields=["LoadedNoteCount"]),

    # OP-103 Shadow DOM/iframe
    TestCase("SDO-001", "Shadow DOM Location Test", "Bilibili", "OP-103", "P1", "locate", expected_fields=["ElementLocation"]),
    TestCase("SDO-002", "iframe Element Location Test", "EastMoney", "OP-103", "P1", "locate", expected_fields=["ElementLocation"]),

    # OP-104 Captcha Handling
    TestCase("CAP-001", "Captcha Detection Test", "Taobao", "OP-104", "P1", "navigate", expected_fields=["CaptchaType"]),
    TestCase("CAP-002", "Slider Captcha Test", "JD", "OP-104", "P1", "click", expected_fields=["VerificationResult"]),

    # OP-105 Anti-detection
    TestCase("ANTI-001", "Stealth Mode Test", "Taobao", "OP-105", "P1", "navigate", expected_fields=["AccessResult"]),
    TestCase("ANTI-002", "Fingerprint Spoofing Test", "Xueqiu", "OP-105", "P1", "navigate", expected_fields=["FingerprintConsistency"]),

    # OP-106 Request Header Spoofing
    TestCase("HDR-001", "Custom Request Header Test", "Baidu", "OP-106", "P1", "navigate", expected_fields=["RequestHeader"]),
    TestCase("HDR-002", "Sec-Fetch Header Test", "Zhihu", "OP-106", "P1", "navigate", expected_fields=["RequestHeader"]),

    # OP-107 Rate Control
    TestCase("RTL-001", "Token Bucket Control Test", "Baidu", "OP-107", "P1", "navigate", expected_fields=["RequestRate"]),
    TestCase("RTL-002", "Exponential Backoff Retry Test", "Zhihu", "OP-107", "P1", "retry", expected_fields=["RetryInterval"]),

    # OP-108 Connection Pool Management
    TestCase("CPL-001", "Connection Pool Health Check Test", "Baidu", "OP-108", "P1", "navigate", expected_fields=["HealthyConnections"]),
    TestCase("CPL-002", "Connection Timeout Eviction Test", "Zhihu", "OP-108", "P1", "navigate", expected_fields=["EvictedConnections"]),
]

# P2 Scenario Capability Test Cases
P2_TEST_CASES: List[TestCase] = [
    # OP-201 Search Query
    TestCase("SRCH-001", "Keyword Search Test", "Baidu", "OP-201", "P2", "search", expected_fields=["SearchResults"]),
    TestCase("SRCH-002", "Advanced Search Parameter Test", "Bing", "OP-201", "P2", "search", expected_fields=["FilteredResults"]),
    TestCase("SRCH-003", "Search Autocomplete Test", "Baidu", "OP-201", "P2", "autocomplete", expected_fields=["SuggestionList"]),

    # OP-202 Product Search
    TestCase("ECOM-001", "Product Search Test", "Taobao", "OP-202", "P2", "search", expected_fields=["ProductList"]),
    TestCase("ECOM-002", "Price Extraction Test", "JD", "OP-202", "P2", "extract", expected_fields=["PriceData"]),
    TestCase("ECOM-003", "Product Detail Extraction Test", "PDD", "OP-202", "P2", "extract", expected_fields=["ProductInfo"]),

    # OP-203 News Scraping
    TestCase("NEWS-001", "News List Extraction Test", "SinaNews", "OP-203", "P2", "extract", expected_fields=["NewsList"]),
    TestCase("NEWS-002", "News Article Extraction Test", "ThePaper", "OP-203", "P2", "extract", expected_fields=["ArticleContent"]),
    TestCase("NEWS-003", "News Metadata Extraction Test", "NetEaseNews", "OP-203", "P2", "extract", expected_fields=["Metadata"]),

    # OP-204 Social Content
    TestCase("SOC-001", "Feed Extraction Test", "Weibo", "OP-204", "P2", "extract", expected_fields=["WeiboList"]),
    TestCase("SOC-002", "Like Comment Extraction Test", "Zhihu", "OP-204", "P2", "extract", expected_fields=["StatsData"]),
    TestCase("SOC-003", "User Profile Extraction Test", "Xiaohongshu", "OP-204", "P2", "extract", expected_fields=["UserProfile"]),

    # OP-205 Form Submission
    TestCase("FORM-001", "Search Form Submission Test", "Baidu", "OP-205", "P2", "submit", expected_fields=["SearchResults"]),
    TestCase("FORM-002", "Multi-step Form Test", "12306", "OP-205", "P2", "submit", expected_fields=["FormStatus"]),
    TestCase("FORM-003", "Form Validation Error Test", "JobSite", "OP-205", "P2", "submit", expected_fields=["ErrorMessage"]),

    # OP-206 Login Flow
    TestCase("LOGIN-001", "Account Password Login Test", "Zhihu", "OP-206", "P2", "login", expected_fields=["LoginStatus"]),
    TestCase("LOGIN-002", "Session Management Test", "Douban", "OP-206", "P2", "navigate", expected_fields=["SessionValidity"]),
    TestCase("LOGIN-003", "Captcha Login Test", "JD", "OP-206", "P2", "login", expected_fields=["LoginResult"]),
]

# All test cases
ALL_TEST_CASES = P0_TEST_CASES + P1_TEST_CASES + P2_TEST_CASES
