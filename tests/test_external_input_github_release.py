"""tests/test_external_input_github_release.py — GithubReleaseInputSource（外部数据知识化计划 P5）测试

覆盖：
  1. 抓取 releases API 响应 -> 解析出结构化 tag_name/body/html_url
  2. 首次轮询：所有非 prerelease 的 release 产生事件
  3. 第二次轮询只对新增 tag 产生事件
  4. prerelease 默认被过滤，include_prerelease=true 时计入
  5. 缺少 repo 参数时抛错
  6. seen_tags 超过上限时只保留最近若干条
  7. registry：import builtin.github_release 后
     "github_release" 出现在 registered_source_types()
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from mini_agent.external_input.builtin.github_release import (
    GithubReleaseInputSource,
    GithubReleaseFetchError,
    fetch_releases,
)
from mini_agent.external_input.source import registered_source_types


def _fake_response(json_data, status_ok: bool = True):
    resp = SimpleNamespace()

    def _raise_for_status():
        if not status_ok:
            raise RuntimeError("boom")

    resp.raise_for_status = _raise_for_status
    resp.json = lambda: json_data
    return resp


RELEASES_TWO = [
    {
        "tag_name": "v1.0.0",
        "name": "v1.0.0",
        "body": "Initial release",
        "html_url": "https://github.com/acme/widget/releases/tag/v1.0.0",
        "published_at": "2024-01-01T00:00:00Z",
        "prerelease": False,
    },
    {
        "tag_name": "v1.1.0-rc1",
        "name": "v1.1.0-rc1",
        "body": "Release candidate",
        "html_url": "https://github.com/acme/widget/releases/tag/v1.1.0-rc1",
        "published_at": "2024-01-05T00:00:00Z",
        "prerelease": True,
    },
]

RELEASES_THREE = RELEASES_TWO + [
    {
        "tag_name": "v1.1.0",
        "name": "v1.1.0",
        "body": "Stable follow-up",
        "html_url": "https://github.com/acme/widget/releases/tag/v1.1.0",
        "published_at": "2024-01-10T00:00:00Z",
        "prerelease": False,
    },
]


class TestFetchReleases(unittest.TestCase):
    def test_parses_releases_correctly(self):
        with mock.patch("requests.get", return_value=_fake_response(RELEASES_TWO)):
            releases = fetch_releases("acme/widget")
        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0]["tag_name"], "v1.0.0")
        self.assertFalse(releases[0]["prerelease"])

    def test_http_failure_raises(self):
        with mock.patch("requests.get", side_effect=RuntimeError("down")):
            with self.assertRaises(GithubReleaseFetchError):
                fetch_releases("acme/widget")

    def test_non_list_response_raises(self):
        with mock.patch("requests.get", return_value=_fake_response({"message": "not found"})):
            with self.assertRaises(GithubReleaseFetchError):
                fetch_releases("acme/widget")


class TestGithubReleaseInputSourcePoll(unittest.TestCase):
    def test_missing_repo_raises(self):
        source = GithubReleaseInputSource()
        with self.assertRaises(GithubReleaseFetchError):
            source.poll({}, {})

    def test_first_poll_skips_prerelease_by_default(self):
        source = GithubReleaseInputSource()
        with mock.patch("requests.get", return_value=_fake_response(RELEASES_TWO)):
            events, new_state = source.poll({"repo": "acme/widget"}, {})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].fields["tag_name"], "v1.0.0")
        # prerelease 的 tag 也计入 seen_tags，避免以后 include_prerelease
        # 打开时把历史上早已见过的 prerelease 又当成"新"事件重复触发。
        self.assertIn("v1.1.0-rc1", new_state["seen_tags"])

    def test_include_prerelease_true(self):
        source = GithubReleaseInputSource()
        with mock.patch("requests.get", return_value=_fake_response(RELEASES_TWO)):
            events, _ = source.poll(
                {"repo": "acme/widget", "include_prerelease": True}, {},
            )
        self.assertEqual(len(events), 2)

    def test_second_poll_only_new_tag(self):
        source = GithubReleaseInputSource()
        with mock.patch("requests.get", return_value=_fake_response(RELEASES_TWO)):
            _, state = source.poll({"repo": "acme/widget"}, {})
        with mock.patch("requests.get", return_value=_fake_response(RELEASES_THREE)):
            events, new_state = source.poll({"repo": "acme/widget"}, state)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].fields["tag_name"], "v1.1.0")

    def test_seen_tags_truncated_to_max(self):
        source = GithubReleaseInputSource()
        state = {"seen_tags": [f"old-{i}" for i in range(300)]}
        with mock.patch("requests.get", return_value=_fake_response(RELEASES_TWO)):
            _, new_state = source.poll({"repo": "acme/widget"}, state)
        self.assertLessEqual(len(new_state["seen_tags"]), 200)

    def test_registered_in_source_registry(self):
        import mini_agent.external_input.builtin.github_release  # noqa: F401
        self.assertIn("github_release", registered_source_types())


if __name__ == "__main__":
    unittest.main()
