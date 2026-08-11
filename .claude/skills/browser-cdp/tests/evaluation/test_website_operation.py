#!/usr/bin/env python3
"""
Website operation capability test script

Based on test_cases_design.md, covering 119 test cases.
Supports mock mode and real browser mode.

Usage:
    pytest tests/evaluation/test_website_operation.py -v --mock
    pytest tests/evaluation/test_website_operation.py -v --real
    pytest tests/evaluation/test_website_operation.py -v -m p0
"""

import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest

SKILL_DIR = Path(__file__).parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

logger = logging.getLogger(__name__)


@dataclass
class TestCaseDef:
    case_id: str
    name: str
    website: str
    capability: str
    steps: List[str]
    expected: str
    priority: str
    dimension: str
    pass_threshold: float


P0_TEST_CASES: List[TestCaseDef] = [
    TestCaseDef("NAV-001", "Homepage access test", "gov.cn", "OP-001",
                ["Navigate to https://www.gov.cn", "Wait for page load", "Verify HTTP 200"],
                "Page loads successfully with HTTP 200", "P0", "Page load capability", 0.95),
    TestCaseDef("NAV-002", "Search page access test", "Baidu", "OP-001",
                ["Navigate to https://www.baidu.com/s?wd=test", "Wait for search results"],
                "Search results page loads successfully", "P0", "Page load capability", 0.95),
    TestCaseDef("NAV-003", "Dynamic page load test", "Zhihu", "OP-001",
                ["Navigate to https://www.zhihu.com", "Wait for networkidle", "Verify content complete"],
                "Content complete after networkidle", "P0", "Page load capability", 0.95),
    TestCaseDef("NAV-004", "Timeout handling test", "stats.gov.cn", "OP-001",
                ["Set timeout 5s", "Navigate to http://www.stats.gov.cn", "Capture timeout exception"],
                "Returns error info on timeout", "P1", "Page load capability", 0.90),
    TestCaseDef("NAV-005", "Redirect handling test", "creditchina.gov.cn", "OP-001",
                ["Navigate to https://www.creditchina.gov.cn", "Track redirect chain"],
                "Auto-follows redirects", "P1", "Page load capability", 0.90),
    TestCaseDef("LOC-001", "CSS selector test", "Baidu", "OP-002",
                ["Locate search box with CSS selector", "Verify element visible"],
                "Successfully locates #kw element", "P0", "Element location", 0.90),
    TestCaseDef("LOC-002", "XPath test", "Zhihu", "OP-002",
                ["Locate login button with XPath", "Verify element clickable"],
                "Successfully locates login element", "P0", "Element location", 0.90),
    TestCaseDef("LOC-003", "Text match test", "Sina News", "OP-002",
                ["Locate link by text content"],
                "Successfully locates target link", "P0", "Element location", 0.90),
    TestCaseDef("LOC-004", "Attribute match test", "Douban", "OP-002",
                ["Locate element by data-id attribute"],
                "Successfully locates target element", "P1", "Element location", 0.85),
    TestCaseDef("LOC-005", "Relative location test", "Taobao", "OP-002",
                ["Locate product card based on adjacent element"],
                "Successfully locates product element", "P1", "Element location", 0.85),
    TestCaseDef("LOC-006", "Shadow DOM test", "Bilibili", "OP-002",
                ["Locate element inside Shadow DOM"],
                "Successfully locates nested element", "P2", "Element location", 0.70),
    TestCaseDef("LOC-007", "iframe element test", "EastMoney", "OP-002",
                ["Locate search box inside iframe"],
                "Successfully locates iframe element", "P2", "Element location", 0.70),
    TestCaseDef("CLK-001", "Link click test", "gov.cn", "OP-003",
                ["Click news link on homepage", "Verify page navigation"],
                "Navigates to news detail page", "P0", "Click interaction", 0.85),
    TestCaseDef("CLK-002", "Button click test", "Baidu", "OP-003",
                ["Click search button", "Verify search execution"],
                "Executes search and navigates to results", "P0", "Click interaction", 0.85),
    TestCaseDef("CLK-003", "Checkbox click test", "Zhihu", "OP-003",
                ["Click remember login checkbox", "Verify state toggle"],
                "Checkbox state toggles", "P0", "Click interaction", 0.85),
    TestCaseDef("CLK-004", "Dropdown select test", "Job site", "OP-003",
                ["Select dropdown option"],
                "Option selected correctly", "P1", "Click interaction", 0.85),
    TestCaseDef("CLK-005", "Dynamic element click", "Taobao", "OP-003",
                ["Click dynamically loaded product link"],
                "Successfully clicks and navigates", "P1", "Click interaction", 0.80),
    TestCaseDef("CLK-006", "Overlay element click", "JD", "OP-003",
                ["Click element blocked by popup"],
                "Auto-closes popup then clicks", "P2", "Click interaction", 0.70),
    TestCaseDef("INP-001", "Text input test", "Baidu", "OP-004",
                ["Type AI query in search box", "Verify input content"],
                "Input box shows correct text", "P0", "Input operation", 0.90),
    TestCaseDef("INP-002", "Clear input test", "Zhihu", "OP-004",
                ["Clear search box", "Verify empty"],
                "Input box is empty", "P0", "Input operation", 0.90),
    TestCaseDef("INP-003", "Special char input", "Job site", "OP-004",
                ["Type special characters"],
                "Special characters display correctly", "P1", "Input operation", 0.85),
    TestCaseDef("INP-004", "Date picker test", "12306", "OP-004",
                ["Select departure date"],
                "Date picker responds correctly", "P1", "Input operation", 0.85),
    TestCaseDef("INP-005", "Multi-line input", "Douban", "OP-004",
                ["Type multi-line text in comment box"],
                "Multi-line content displays correctly", "P2", "Input operation", 0.80),
    TestCaseDef("EXT-001", "Title extraction test", "gov.cn", "OP-005",
                ["Extract page title"],
                "Returns correct title text", "P0", "Data extraction", 0.85),
    TestCaseDef("EXT-002", "List extraction test", "Sina News", "OP-005",
                ["Extract news list", "Verify 10 items"],
                "Returns 10 news items", "P0", "Data extraction", 0.85),
    TestCaseDef("EXT-003", "Link extraction test", "Baidu", "OP-005",
                ["Extract search result links"],
                "Returns correct URL list", "P0", "Data extraction", 0.85),
    TestCaseDef("EXT-004", "Image extraction test", "Douban", "OP-005",
                ["Extract movie poster images"],
                "Returns image URL list", "P1", "Data extraction", 0.80),
    TestCaseDef("EXT-005", "Table data extraction", "stats.gov.cn", "OP-005",
                ["Extract statistical data table"],
                "Returns structured table data", "P1", "Data extraction", 0.80),
    TestCaseDef("EXT-006", "Dynamic content extraction", "Xueqiu", "OP-005",
                ["Extract real-time stock data"],
                "Returns latest price data", "P1", "Data extraction", 0.80),
    TestCaseDef("SHT-001", "Full page screenshot", "Baidu", "OP-006",
                ["Capture full page screenshot"],
                "Generates complete page screenshot", "P0", "Screenshot capability", 0.95),
    TestCaseDef("SHT-002", "Element screenshot", "Zhihu", "OP-006",
                ["Capture specified element"],
                "Generates element area screenshot", "P0", "Screenshot capability", 0.95),
    TestCaseDef("SHT-003", "Annotated screenshot", "gov.cn", "OP-006",
                ["Capture and annotate interactive elements"],
                "Generates annotated screenshot", "P1", "Screenshot capability", 0.90),
    TestCaseDef("SHT-004", "Scroll screenshot", "Sina News", "OP-006",
                ["Capture long page"],
                "Generates complete long page screenshot", "P1", "Screenshot capability", 0.90),
    TestCaseDef("SCR-001", "Scroll down test", "Zhihu", "OP-007",
                ["Scroll down 500px", "Verify scroll position"],
                "Page scrolls successfully", "P0", "Scroll operation", 0.90),
    TestCaseDef("SCR-002", "Infinite scroll test", "Weibo", "OP-007",
                ["Continuously scroll to load more"],
                "Loads more posts", "P1", "Scroll operation", 0.75),
    TestCaseDef("SCR-003", "Scroll to element", "Douban", "OP-007",
                ["Scroll to specified element"],
                "Element appears in viewport", "P1", "Scroll operation", 0.85),
    TestCaseDef("SCR-004", "Scroll load verification", "Taobao", "OP-007",
                ["Scroll to trigger lazy load"],
                "Images load completely", "P2", "Scroll operation", 0.70),
    TestCaseDef("TAB-001", "New tab open test", "Baidu", "OP-008",
                ["Open new tab", "Verify tab count"],
                "Successfully creates new tab", "P0", "Tab management", 0.90),
    TestCaseDef("TAB-002", "Tab switch test", "Zhihu", "OP-008",
                ["Switch to specified tab"],
                "Successfully switches", "P0", "Tab management", 0.90),
    TestCaseDef("TAB-003", "Tab close test", "gov.cn", "OP-008",
                ["Close current tab"],
                "Tab closes successfully", "P0", "Tab management", 0.90),
    TestCaseDef("TAB-004", "Multi-tab management", "Baidu", "OP-008",
                ["Open 3 tabs and switch"],
                "All tabs work normally", "P1", "Tab management", 0.85),
    TestCaseDef("WAI-001", "Element wait test", "Baidu", "OP-009",
                ["Wait for search box to appear", "Verify element visible"],
                "Continues after element appears", "P0", "Wait strategy", 0.85),
    TestCaseDef("WAI-002", "Network idle wait", "Zhihu", "OP-009",
                ["Wait for networkidle"],
                "Continues after network idle", "P0", "Wait strategy", 0.85),
    TestCaseDef("WAI-003", "Page stable wait", "Taobao", "OP-009",
                ["Wait for page stable"],
                "Continues after page stable", "P1", "Wait strategy", 0.80),
    TestCaseDef("WAI-004", "Timeout wait test", "stats.gov.cn", "OP-009",
                ["Set timeout wait"],
                "Returns correctly on timeout", "P1", "Wait strategy", 0.80),
    TestCaseDef("ERR-001", "Network error recovery", "gov.cn", "OP-010",
                ["Simulate network error then retry"],
                "Auto-retry succeeds", "P0", "Error recovery", 0.70),
    TestCaseDef("ERR-002", "Element消失 recovery", "Zhihu", "OP-010",
                ["Wait before element loads"],
                "Continues after element appears", "P0", "Error recovery", 0.70),
    TestCaseDef("ERR-003", "Page timeout recovery", "Taobao", "OP-010",
                ["Retry after page load timeout"],
                "Loads successfully after retry", "P1", "Error recovery", 0.65),
    TestCaseDef("ERR-004", "Connection recovery", "Baidu", "OP-010",
                ["Reconnect after CDP disconnect"],
                "Auto-reconnects successfully", "P1", "Error recovery", 0.65),
]
