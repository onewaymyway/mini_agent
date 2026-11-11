"""小红书网页版结构探测：用项目的浏览器登录态打开页面，输出可用于编写选择器的结构信息。

先登录一次：  python cli.py login xiaohongshu
运行探测：    python xhs_probe.py [URL ...]        （默认探测 explore 首页）
          python xhs_probe.py --show URL       （有头浏览器打开，便于观察页面）
输出：页面 HTML 保存到 data/debug/，并打印链接、标题候选、重复结构的 class 等信息。
把终端输出（以及 data/debug/ 下的 html 文件，如需要）发给我，用于编写选择器。
"""
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.browser_session import BrowserSession, LoginRequired  # noqa: E402
from core.settings import load_settings, load_sites  # noqa: E402

DEFAULT_URLS = ["https://www.xiaohongshu.com/explore"]


def _path_of(tag) -> str:
    parts = []
    node = tag
    for _ in range(4):
        if node is None or not getattr(node, "name", None) or node.name == "[document]":
            break
        cls = ".".join(node.get("class", [])[:3])
        parts.append(node.name + (f".{cls}" if cls else ""))
        node = node.parent
    return " > ".join(reversed(parts))


def probe(url: str, session: BrowserSession, headless: bool, site_cfg: dict) -> None:
    print(f"\n==== {url}")
    try:
        html = session.fetch_html(url, site="xiaohongshu", headless=headless,
                                  expired_pattern=site_cfg.get("expired_pattern"))
    except LoginRequired as exc:
        print(f"  需要登录：最终 URL {exc.final_url}。请先运行 python cli.py login xiaohongshu")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"  抓取失败: {type(exc).__name__}: {exc}")
        return

    out_dir = ROOT / "data" / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").split("/")[-1] or "index"
    out_file = out_dir / f"xhs_{name}.html"
    out_file.write_text(html, encoding="utf-8")
    print(f"  HTML 长度 {len(html)}，已保存 {out_file.relative_to(ROOT)}")
    if "登录" in html[:3000] and "扫码" in html:
        print("  提示：页面中出现登录提示，可能未登录或登录已失效。")

    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)
    print(f"  链接总数 {len(anchors)}")
    kinds = Counter()
    for a in anchors:
        href = a["href"]
        for key in ("/explore/", "/search_result", "/discovery/item/", "/user/profile/", "/topic/"):
            if key in href:
                kinds[key] += 1
    print(f"  链接类型统计: {dict(kinds)}")

    print("  重复结构（同一 class 出现 ≥5 次的元素，可能是列表项）:")
    cls_count = Counter()
    for el in soup.find_all(True):
        classes = el.get("class") or []
        if classes:
            cls_count[(el.name, ".".join(classes[:3]))] += 1
    for (tag, cls), n in cls_count.most_common(40):
        if n >= 5:
            print(f"    {tag}.{cls}  x{n}")

    print("  与“热”“榜”“趋势”“热门”相关的元素（class 或文字）:")
    hits = 0
    for el in soup.find_all(True):
        classes = " ".join(el.get("class") or [])
        text = el.get_text(" ", strip=True)[:40] if el.name in ("span", "div", "p", "h2", "h3") else ""
        if any(k in classes.lower() for k in ("hot", "rank", "trend", "topic")) or \
                (text and any(k in text for k in ("热榜", "热门", "热搜", "趋势")) and len(text) <= 20):
            print(f"    <{el.name} class='{classes[:60]}'> {text}")
            hits += 1
            if hits >= 25:
                break

    print("  前 5 个带链接的候选条目（标签路径、链接、文字）:")
    shown = 0
    for a in anchors:
        text = a.get_text(" ", strip=True)
        if not text or len(text) < 2:
            continue
        print(f"    {_path_of(a)}")
        print(f"      href={a['href'][:80]}  text={text[:40]!r}")
        shown += 1
        if shown >= 5:
            break


def main(argv: list[str]) -> int:
    headless = True
    if argv and argv[0] == "--show":
        headless = False
        argv = argv[1:]
    urls = argv or DEFAULT_URLS
    settings = load_settings(ROOT)
    site_cfg = load_sites(ROOT).get("xiaohongshu", {})
    session = BrowserSession(settings.paths.browser_profile_dir,
                             settings.paths.root / "browser" / "login_status.json")
    for url in urls:
        probe(url, session, headless, site_cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
