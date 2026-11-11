"""RSS / Atom 订阅（含通过 RSSHub 等转成 RSS 的小说、视频更新）。"""
from checkers.wrapped import WrappedListChecker


class FeedLatestChecker(WrappedListChecker):
    name = "feed_latest"
    wraps = "rss"
