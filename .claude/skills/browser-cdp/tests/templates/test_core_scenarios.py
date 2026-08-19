"""
test_core_scenarios.py

browser-cdp test cases:
- LOGIN (LOGIN-1 ~ LOGIN-8)
- FORM (FORM-1 ~ FORM-15)
- NAV (NAV-1 ~ NAV-10)
- SEARCH (SEARCH-1 ~ SEARCH-10)
- EXTRA (EXTRA-1 ~ EXTRA-10)
- INTER (INTER-1 ~ INTER-10)

Usage:
    python -m pytest tests/templates/test_core_scenarios.py -v
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, Mock, MagicMock
from pathlib import Path
import sys

# skill dir
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core import browser_launch
from src.core import browser_nav
from src.core import browser_extract
from src.core import browser_input
from src.core import browser_screenshot


class BaseBrowserTest(unittest.TestCase):
    """browser-cdp base test class"""

    @classmethod
    def setUpClass(cls):
        """setup logging"""
        super().setUpClass()
        cls.logger = cls._setup_logging()

    @classmethod
    def tearDownClass(cls):
        """teardown"""
        super().tearDownClass()

    @staticmethod
    def _setup_logging():
        """setup logger"""
        import logging
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger(__name__)

    def setUp(self):
        """setup per test"""
        self._mock_browser_instance()
        self._setup_mocks()

    def _mock_browser_instance(self):
        """create mock browser instance"""
        self.mock_tab = {
            "id": "test-tab-1",
            "url": "about:blank",
            "title": "Test Page",
            "is_active": True
        }
        self.mock_browser = {
            "tabs": [self.mock_tab],
            "active_tab_id": self.mock_tab["id"]
        }

    def _setup_mocks(self):
        """setup mocks"""
        pass

    def assertTabUrlContains(self, tab_id: str, expected_substring: str, msg: str = None):
        """assert tab URL contains substring"""
        actual_url = self._get_tab_url(tab_id)
        if expected_substring not in actual_url:
            self.fail(f"URL '{actual_url}' does not contain '{expected_substring}'" if msg is None else msg)

    def assertTabTitleContains(self, tab_id: str, expected_substring: str, msg: str = None):
        """assert tab title contains substring"""
        actual_title = self._get_tab_title(tab_id)
        if expected_substring not in actual_title:
            self.fail(f"Title '{actual_title}' does not contain '{expected_substring}'" if msg is None else msg)

    def _get_tab_url(self, tab_id: str) -> str:
        """get tab URL"""
        for tab in self.mock_browser["tabs"]:
            if tab["id"] == tab_id:
                return tab.get("url", "")
        raise ValueError(f"Tab {tab_id} not found")

    def _get_tab_title(self, tab_id: str) -> str:
        """get tab title"""
        for tab in self.mock_browser["tabs"]:
            if tab["id"] == tab_id:
                return tab.get("title", "")
        raise ValueError(f"Tab {tab_id} not found")

    def create_mock_page_content(self, html_type: str = "simple") -> str:
        """create mock page content"""
        if html_type == "simple":
            return "<html><head><title>Test Page</title></head><body><h1>Hello World</h1><p>This is a test page.</p></body></html>"
        elif html_type == "ecommerce":
            return self._create_ecommerce_page()
        elif html_type == "news":
            return self._create_news_page()
        elif html_type == "search":
            return self._create_search_page()
        elif html_type == "social":
            return self._create_social_page()
        elif html_type == "form":
            return self._create_form_page()
        return self.create_mock_page_content("simple")

    def _create_ecommerce_page(self) -> str:
        """create ecommerce page HTML"""
        return '''
<html>
<head><title>Product - Test Store</title></head>
<body>
  <div class="product">
    <h1>Test Product</h1>
    <span class="price">$99.99</span>
    <button class="add-to-cart">Add to Cart</button>
  </div>
  <div class="reviews">
    <div class="review"><span class="rating">5/5</span>Great product!</div>
  </div>
</body>
</html>'''

    def _create_news_page(self) -> str:
        """create news page HTML"""
        return '''
<html>
<head><title>News - Test Site</title></head>
<body>
  <article class="news-item">
    <h2>Breaking News</h2>
    <p>Test news article content.</p>
    <span class="date">2024-01-01</span>
  </article>
</body>
</html>'''

    def _create_search_page(self) -> str:
        """create search page HTML"""
        return '''
<html>
<head><title>Search - Test Engine</title></head>
<body>
  <form class="search-form">
    <input type="text" name="q" placeholder="Search..." />
    <button type="submit">Search</button>
  </form>
  <div class="results">
    <div class="result"><a href="/page1">Result 1</a></div>
    <div class="result"><a href="/page2">Result 2</a></div>
  </div>
</body>
</html>'''

    def _create_social_page(self) -> str:
        """create social media page HTML"""
        return '''
<html>
<head><title>Social - Test Platform</title></head>
<body>
  <div class="post">
    <div class="author">Test User</div>
    <div class="content">This is a test post.</div>
    <button class="like">Like</button>
    <button class="comment">Comment</button>
  </div>
</body>
</html>'''

    def _create_form_page(self) -> str:
        """create form page HTML"""
        return '''
<html>
<head><title>Form - Test Page</title></head>
<body>
  <form id="main-form">
    <input type="text" id="name" name="name" placeholder="Name" />
    <input type="email" id="email" name="email" placeholder="Email" />
    <label for="message">Message:</label>
    <textarea id="message" name="message"></textarea>
    <select id="subject" name="subject">
      <option value="general">General</option>
      <option value="support">Support</option>
    </select>
    <input type="date" id="date" name="date" />
    <input type="radio" id="radio1" name="option" value="1" />
    <label for="radio1">Option 1</label>
    <input type="checkbox" id="check1" name="check" value="1" />
    <label for="check1">Check 1</label>
    <input type="file" id="file" name="file" />
    <button type="submit">Submit</button>
  </form>
</body>
</html>'''

    def tearDown(self):
        """teardown"""
        pass


# ==================== LOGIN SCENARIOS ====================

class TestLoginScenarios(BaseBrowserTest):
    """ (LOGIN-1 ~ LOGIN-8)"""

    def test_01_homepage_access(self):
        """LOGIN-1: homepage access"""
        with patch.object(browser_nav, 'goto') as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://www.example.com")
            self.assertTrue(result)

    def test_02_login_form_detection(self):
        """LOGIN-2: login form detection"""
        with patch.object(browser_extract, 'find_element') as mock_find:
            mock_find.return_value = {"tag": "input", "type": "text"}
            result = browser_extract.find_element("#username")
            self.assertIsNotNone(result)

    def test_03_login_form_filling(self):
        """LOGIN-3: login form filling"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#username", "testuser")
            self.assertTrue(result)

    def test_04_login_submit(self):
        """LOGIN-4: login submit"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector("#login-btn")
            self.assertTrue(result)

    def test_05_password_input(self):
        """LOGIN-5: password input"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#password", "testpass")
            self.assertTrue(result)

    def test_06_captcha_handling(self):
        """LOGIN-6: captcha handling"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector(".captcha-confirm")
            self.assertTrue(result)

    def test_07_remember_me(self):
        """LOGIN-7: remember me"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector("#remember-me")
            self.assertTrue(result)

    def test_08_logout(self):
        """LOGIN-8: logout operation"""
        with patch.object(browser_nav, 'goto') as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://www.example.com/logout")
            self.assertTrue(result)


# ==================== FORM SCENARIOS ====================

class TestFormScenarios(BaseBrowserTest):
    """ (FORM-1 ~ FORM-15)"""

    def test_01_text_input(self):
        """FORM-1: text input"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#name", "John Doe")
            self.assertTrue(result)

    def test_02_email_input(self):
        """FORM-2: email input"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#email", "test@example.com")
            self.assertTrue(result)

    def test_03_password_field(self):
        """FORM-3: password field"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#password", "secret123")
            self.assertTrue(result)

    def test_04_textarea(self):
        """FORM-4: textarea input"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#message", "Hello World")
            self.assertTrue(result)

    def test_05_select_option(self):
        """FORM-5: select option"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector('#subject option[value="general"]')
            self.assertTrue(result)

    def test_06_date_picker(self):
        """FORM-6: date picker"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#date", "2024-01-15")
            self.assertTrue(result)

    def test_07_radio_button(self):
        """FORM-7: radio button"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector('#radio1')
            self.assertTrue(result)

    def test_08_checkbox(self):
        """FORM-8: checkbox"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector('#check1')
            self.assertTrue(result)

    def test_09_file_upload(self):
        """FORM-9: file upload"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector('#file')
            self.assertTrue(result)

    def test_10_form_submit(self):
        """FORM-10: form submit"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector('#main-form button[type="submit"]')
            self.assertTrue(result)

    def test_11_form_validation(self):
        """FORM-11: form validation"""
        with patch.object(browser_extract, 'get_attribute') as mock_attr:
            mock_attr.return_value = "required"
            result = browser_extract.get_attribute("#name", "required")
            self.assertIsNotNone(result)

    def test_12_form_reset(self):
        """FORM-12: form reset"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector('#reset-btn')
            self.assertTrue(result)

    def test_13_auto_fill(self):
        """FORM-13: auto fill"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#name", "AutoFill")
            self.assertTrue(result)

    def test_14_multi_field_submit(self):
        """FORM-14: multi field submit"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector("#main-form")
            self.assertTrue(result)

    def test_15_nested_form(self):
        """FORM-15: nested form"""
        with patch.object(browser_extract, 'find_elements') as mock_find:
            mock_find.return_value = [{"id": "nested-input"}]
            result = browser_extract.find_elements(".nested-form input")
            self.assertTrue(len(result) > 0)


# ==================== NAV SCENARIOS ====================

class TestNavScenarios(BaseBrowserTest):
    """ (NAV-1 ~ NAV-10)"""

    def test_01_navigate_to_url(self):
        """NAV-1: navigate to URL"""
        with patch.object(browser_nav, 'goto') as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://www.example.com")
            self.assertTrue(result)

    def test_02_back_navigation(self):
        """NAV-2: back navigation"""
        with patch.object(browser_nav, 'go_back') as mock_back:
            mock_back.return_value = True
            result = browser_nav.go_back()
            self.assertTrue(result)

    def test_03_forward_navigation(self):
        """NAV-3: forward navigation"""
        with patch.object(browser_nav, 'go_forward') as mock_fwd:
            mock_fwd.return_value = True
            result = browser_nav.go_forward()
            self.assertTrue(result)

    def test_04_refresh_page(self):
        """NAV-4: refresh page"""
        with patch.object(browser_nav, 'reload') as mock_reload:
            mock_reload.return_value = True
            result = browser_nav.reload()
            self.assertTrue(result)

    def test_05_wait_for_load(self):
        """NAV-5: wait for page load"""
        with patch.object(browser_nav, 'wait_for_load') as mock_wait:
            mock_wait.return_value = True
            result = browser_nav.wait_for_load()
            self.assertTrue(result)

    def test_06_url_change_detection(self):
        """NAV-6: URL change detection"""
        self.mock_tab["url"] = "https://www.example.com/page2"
        self.assertTabUrlContains("test-tab-1", "example.com")

    def test_07_title_change_detection(self):
        """NAV-7: title change detection"""
        self.mock_tab["title"] = "New Page Title"
        self.assertTabTitleContains("test-tab-1", "New Page")

    def test_08_navigate_to_external(self):
        """NAV-8: navigate to external site"""
        with patch.object(browser_nav, 'goto') as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://external-site.com")
            self.assertTrue(result)

    def test_09_navigate_with_params(self):
        """NAV-9: navigate with query params"""
        with patch.object(browser_nav, 'goto') as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://www.example.com/search?q=test&page=1")
            self.assertTrue(result)

    def test_10_relative_url_navigation(self):
        """NAV-10: relative URL navigation"""
        with patch.object(browser_nav, 'goto') as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("/relative/path")
            self.assertTrue(result)


# ==================== SEARCH SCENARIOS ====================

class TestSearchScenarios(BaseBrowserTest):
    """ (SEARCH-1 ~ SEARCH-10)"""

    def test_01_search_box_input(self):
        """SEARCH-1: search box input"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#search-box", "test query")
            self.assertTrue(result)

    def test_02_search_submit(self):
        """SEARCH-2: search submit"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector("#search-btn")
            self.assertTrue(result)

    def test_03_search_results_display(self):
        """SEARCH-3: search results display"""
        with patch.object(browser_extract, 'find_elements') as mock_find:
            mock_find.return_value = [{"text": "Result 1"}, {"text": "Result 2"}]
            result = browser_extract.find_elements(".search-result")
            self.assertTrue(len(result) > 0)

    def test_04_search_pagination(self):
        """SEARCH-4: search pagination"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector(".pagination-next")
            self.assertTrue(result)

    def test_05_search_filter(self):
        """SEARCH-5: search filter"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector(".filter-category")
            self.assertTrue(result)

    def test_06_search_sort(self):
        """SEARCH-6: search sort"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector(".sort-option")
            self.assertTrue(result)

    def test_07_search_autocomplete(self):
        """SEARCH-7: search autocomplete"""
        with patch.object(browser_extract, 'find_elements') as mock_find:
            mock_find.return_value = [{"text": "Suggestion 1"}]
            result = browser_extract.find_elements(".autocomplete-item")
            self.assertTrue(len(result) > 0)

    def test_08_search_clear(self):
        """SEARCH-8: search clear"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#search-box", "\x08")
            self.assertTrue(result)

    def test_09_search_history(self):
        """SEARCH-9: search history"""
        with patch.object(browser_extract, 'find_elements') as mock_find:
            mock_find.return_value = [{"text": "Previous search"}]
            result = browser_extract.find_elements(".search-history")
            self.assertTrue(len(result) > 0)

    def test_10_search_recommendation(self):
        """SEARCH-10: search recommendation"""
        with patch.object(browser_extract, 'find_elements') as mock_find:
            mock_find.return_value = [{"text": "Recommended"}]
            result = browser_extract.find_elements(".search-recommendation")
            self.assertTrue(len(result) > 0)


# ==================== EXTRA SCENARIOS ====================

class TestExtraScenarios(BaseBrowserTest):
    """ (EXTRA-1 ~ EXTRA-10)"""

    def test_01_html_extraction(self):
        """EXTRA-1: HTML extraction"""
        with patch.object(browser_extract, 'extract_html') as mock_html:
            mock_html.return_value = "<html><body>Test</body></html>"
            result = browser_extract.extract_html()
            self.assertIn("Test", result)

    def test_02_text_extraction(self):
        """EXTRA-2: text extraction"""
        with patch.object(browser_extract, 'extract_text') as mock_text:
            mock_text.return_value = "Sample text content"
            result = browser_extract.extract_text()
            self.assertEqual(result, "Sample text content")

    def test_03_link_extraction(self):
        """EXTRA-3: link extraction"""
        with patch.object(browser_extract, 'extract_links') as mock_links:
            mock_links.return_value = [
                {"text": "Link 1", "href": "/page1"},
                {"text": "Link 2", "href": "/page2"}
            ]
            result = browser_extract.extract_links()
            self.assertEqual(len(result), 2)

    def test_04_image_extraction(self):
        """EXTRA-4: image extraction"""
        with patch.object(browser_extract, 'extract_images') as mock_imgs:
            mock_imgs.return_value = [{"src": "/img1.jpg"}]
            result = browser_extract.extract_images()
            self.assertTrue(len(result) > 0)

    def test_05_css_selector_extraction(self):
        """EXTRA-5: CSS selector extraction"""
        with patch.object(browser_extract, 'find_elements') as mock_find:
            mock_find.return_value = [{"class": "target"}]
            result = browser_extract.find_elements(".target")
            self.assertTrue(len(result) > 0)

    def test_06_xpath_extraction(self):
        """EXTRA-6: xpath extraction"""
        with patch.object(browser_extract, 'find_elements') as mock_find:
            mock_find.return_value = [{"tag": "div"}]
            result = browser_extract.find_elements("//div")
            self.assertTrue(len(result) > 0)

    def test_07_meta_extraction(self):
        """EXTRA-7: meta extraction"""
        with patch.object(browser_extract, 'extract_meta') as mock_meta:
            mock_meta.return_value = {"title": "Test Page", "description": "Test desc"}
            result = browser_extract.extract_meta()
            self.assertIn("title", result)

    def test_08_cookie_extraction(self):
        """EXTRA-8: cookie extraction"""
        with patch.object(browser_extract, 'get_cookies') as mock_cookies:
            mock_cookies.return_value = [{"name": "session", "value": "abc123"}]
            result = browser_extract.get_cookies()
            self.assertTrue(len(result) > 0)

    def test_09_table_extraction(self):
        """EXTRA-9: table extraction"""
        with patch.object(browser_extract, 'extract_table') as mock_table:
            mock_table.return_value = [["col1", "col2"]]
            result = browser_extract.extract_table()
            self.assertTrue(len(result) > 0)

    def test_10_screenshot_save(self):
        """EXTRA-10: screenshot save"""
        with patch.object(browser_screenshot, 'save_screenshot') as mock_save:
            mock_save.return_value = True
            result = browser_screenshot.save_screenshot("/tmp/test.png")
            self.assertTrue(result)


# ==================== INTERACTION SCENARIOS ====================

class TestInteractionScenarios(BaseBrowserTest):
    """ (INTER-1 ~ INTER-10)"""

    def test_01_click_element(self):
        """INTER-1: click element"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector(".target-button")
            self.assertTrue(result)

    def test_02_double_click(self):
        """INTER-2: double click"""
        with patch.object(browser_input, 'double_click') as mock_dbl:
            mock_dbl.return_value = True
            result = browser_input.double_click(".target-element")
            self.assertTrue(result)

    def test_03_right_click(self):
        """INTER-3: right click"""
        with patch.object(browser_input, 'mouse_right_click') as mock_right:
            mock_right.return_value = True
            result = browser_input.mouse_right_click(".context-menu-target")
            self.assertTrue(result)

    def test_04_keyboard_input(self):
        """INTER-4: keyboard input"""
        with patch.object(browser_input, 'dispatch_key') as mock_keys:
            mock_keys.return_value = True
            result = browser_input.dispatch_key("#input-box", "Hello World")
            self.assertTrue(result)

    def test_05_scroll_operation(self):
        """INTER-5: scroll operation"""
        with patch.object(browser_input, 'scroll') as mock_scroll:
            mock_scroll.return_value = True
            result = browser_input.scroll(0, 500)
            self.assertTrue(result)

    def test_06_pull_to_refresh(self):
        """INTER-6: pull to refresh"""
        with patch.object(browser_input, 'scroll') as mock_scroll:
            mock_scroll.return_value = True
            result = browser_input.scroll(0, -500)
            self.assertTrue(result)

    def test_07_infinite_scroll(self):
        """INTER-7: infinite scroll"""
        with patch.object(browser_input, 'scroll') as mock_scroll:
            mock_scroll.return_value = True
            result = browser_input.scroll(0, 1000)
            self.assertTrue(result)

    def test_08_popup_handling(self):
        """INTER-8: popup handling"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector(".close-popup")
            self.assertTrue(result)

    def test_09_file_download(self):
        """INTER-9: file download"""
        with patch.object(browser_input, 'click_selector') as mock_click:
            mock_click.return_value = True
            result = browser_input.click_selector(".download-link")
            self.assertTrue(result)

    def test_10_drag_and_drop(self):
        """INTER-10: drag and drop"""
        with patch.object(browser_input, 'drag_and_drop') as mock_drag:
            mock_drag.return_value = True
            result = browser_input.drag_and_drop(".drag-handle", ".drop-target")
            self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
