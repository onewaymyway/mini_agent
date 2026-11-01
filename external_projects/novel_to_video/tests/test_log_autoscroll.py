"""章节日志面板：自动滚动到最新一行；内容转义，不破坏页面。"""
from ui.joblog_panel import build_log_html


def test_log_scrolls_to_newest_line():
    html = build_log_html("第一行\n第二行", 320)
    assert "el.scrollTop = el.scrollHeight" in html
    assert "toBottom();" in html


def test_log_text_is_escaped():
    html = build_log_html("<script>alert(1)</script> & 第3章", 200)
    assert "<script>alert(1)</script>" not in html.split("<pre")[1].split("</pre>")[0]
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; 第3章" in html


def test_height_is_applied():
    assert "height:320px" in build_log_html("x", 320)
