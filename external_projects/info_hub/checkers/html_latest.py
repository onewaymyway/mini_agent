"""HTML 页面订阅（章节目录页、频道页）：parse.list_selector / parse.fields 与 html_list 一致，可走共享浏览器登录态。"""
from checkers.wrapped import WrappedListChecker


class HtmlLatestChecker(WrappedListChecker):
    name = "html_latest"
    wraps = "html_list"
    requires_browser = True
