"""external_input/builtin/github_release.py — GithubReleaseInputSource（外部数据知识化计划 P5，可选）

设计背景见
next_doc/external_knowledge_wiki_and_self_improvement_plan.md §3 P5：
`watch` source 现有的 `html_diff`/`json_api` fetcher 都不是为"追踪某个
repo 的版本更新"这类场景设计的（需要自己拼 GitHub API 路径、自己判断
"是不是发布了新版本"）。本 source 直接对接 GitHub Releases API，产生
结构化的 `signal="new_release"` 事件（tag/版本说明/发布时间）。

产生的事件同样走 `channel: agent_watch`（`sources.yaml` 里配置），依旧
被 `external_input/knowledge_extractor.py` 消费（P1），不需要改动下游
任何消费链路。

严格遵循 `ExternalInputSource` 扩展点（`@register_source`），实现方式
参考 `builtin/watch.py`/`builtin/weather.py`：不调用 LLM，只做纯脚本
抓取 + 去重判断；跨轮询状态（已见过的 release tag 集合）全部通过
`state` dict 传递，不使用实例属性。

`params` 支持的键：

- ``repo``（必填，如 ``"anthropics/anthropic-sdk-python"``）：
  `owner/name` 形式的仓库路径。
- ``include_prerelease``（可选，默认 false）：是否把标记为
  prerelease 的版本也计入。
- ``max_results``（可选，默认 10）：单次拉取的 release 条数上限。
- ``github_token``（可选）：未认证请求受 GitHub API 较低的速率限制，
  配置后会加到请求头 `Authorization: Bearer <token>`，提高限额；
  不配置也能正常工作，只是限额更低。
"""

from __future__ import annotations

from typing import Optional

from mini_agent.external_input.source import (
    ExternalInputEvent,
    ExternalInputSource,
    register_source,
)

_DEFAULT_TIMEOUT = 15
_DEFAULT_MAX_RESULTS = 10
_DEFAULT_MAX_SEEN_TAGS = 200
_GITHUB_API_BASE = "https://api.github.com"


class GithubReleaseFetchError(RuntimeError):
    """抓取/解析 GitHub Releases API 失败。直接向上抛给 GatewayPoller，
    交由其统一的退避熔断处理（跟 watch.py::WatchFetchError 同样的分工），
    本文件不重复实现重试逻辑。"""


def fetch_releases(
    repo: str, *, max_results: int = _DEFAULT_MAX_RESULTS,
    github_token: Optional[str] = None, timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict]:
    """调用 GitHub Releases API，返回按发布时间倒序的
    [{"tag_name","name","body","html_url","published_at","prerelease"}, ...]。
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - 环境应始终已安装
        raise GithubReleaseFetchError("github_release source 需要 requests 库") from exc

    headers = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        resp = requests.get(
            f"{_GITHUB_API_BASE}/repos/{repo}/releases",
            params={"per_page": max_results},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise GithubReleaseFetchError(f"抓取 GitHub releases 失败 (repo={repo!r}): {exc}") from exc

    try:
        data = resp.json()
    except Exception as exc:
        raise GithubReleaseFetchError(f"GitHub releases 响应解析失败: {exc}") from exc

    if not isinstance(data, list):
        raise GithubReleaseFetchError(f"GitHub releases 响应格式异常 (repo={repo!r})")

    releases: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        tag_name = str(item.get("tag_name") or "").strip()
        if not tag_name:
            continue
        releases.append({
            "tag_name": tag_name,
            "name": str(item.get("name") or tag_name).strip(),
            "body": str(item.get("body") or "").strip(),
            "html_url": str(item.get("html_url") or ""),
            "published_at": str(item.get("published_at") or ""),
            "prerelease": bool(item.get("prerelease")),
        })
    return releases


@register_source("github_release")
class GithubReleaseInputSource(ExternalInputSource):
    """追踪某个 GitHub repo 的 Release/Tag 更新。

    示例 `sources.yaml` 片段::

        sources:
          - id: mini_agent_upstream_release
            type: github_release
            interval_seconds: 21600   # 6 小时轮询一次即可
            channel: agent_watch       # 复用 P1 抽取管道消费的频道
            params:
              repo: anthropics/anthropic-sdk-python
              include_prerelease: false
              max_results: 10
    """

    source_type = "github_release"

    def poll(
        self, params: dict, state: dict,
    ) -> tuple[list[ExternalInputEvent], dict]:
        repo = params.get("repo")
        if not repo:
            raise GithubReleaseFetchError("github_release source 需要在 params 里配置 repo")

        max_results = int(params.get("max_results", _DEFAULT_MAX_RESULTS))
        include_prerelease = bool(params.get("include_prerelease", False))
        github_token = params.get("github_token")

        releases = fetch_releases(
            str(repo), max_results=max_results, github_token=github_token,
        )

        seen_tags = set(state.get("seen_tags") or [])
        source_id = str(params.get("source_id", ""))

        events: list[ExternalInputEvent] = []
        newly_seen: list[str] = []
        for r in releases:
            if r["tag_name"] in seen_tags:
                continue
            newly_seen.append(r["tag_name"])
            if r["prerelease"] and not include_prerelease:
                continue
            events.append(
                ExternalInputEvent(
                    id=f"{repo}:{r['tag_name']}",
                    source_id=source_id,
                    source_type=self.source_type,
                    signal="new_release",
                    title=f"{repo} 发布新版本 {r['tag_name']}（{r['name']}）",
                    detail=r["body"][:500],
                    url=r["html_url"] or None,
                    fields={
                        "repo": repo,
                        "tag_name": r["tag_name"],
                        "published_at": r["published_at"],
                        "prerelease": r["prerelease"],
                        "fetcher": "github_release",
                    },
                    suggested_tier="tick",
                )
            )

        # 跟 arxiv_api.py/watch.py 同款策略：只保留最近若干条 tag，防止
        # state 里的 seen_tags 无限增长。
        all_seen = list(seen_tags) + newly_seen
        new_state = dict(state)
        new_state["seen_tags"] = all_seen[-_DEFAULT_MAX_SEEN_TAGS:]
        return events, new_state


__all__ = [
    "GithubReleaseInputSource",
    "GithubReleaseFetchError",
    "fetch_releases",
]
