"""阶段B（快速面板完整显示）测试。

- 纯几何计算（不依赖 Qt）：任何光标位置、任何屏幕尺寸下，整窗都必须落在可用区域内；
- Qt 离屏：open_panel 后窗口几何确实被夹紧，且回复区有实际高度。
"""

import pytest

from mini_agent_desktop.panel_geometry import (
    CURSOR_OFFSET_Y, MARGIN, PANEL_HEIGHT, PANEL_WIDTH, Rect, fit_panel,
)


def _inside(r: Rect, avail: Rect) -> bool:
    return (r.x >= avail.x and r.y >= avail.y
            and r.x + r.w <= avail.x + avail.w
            and r.y + r.h <= avail.y + avail.h)


SCREEN = Rect(0, 0, 1920, 1040)      # 1080p 扣掉 40px 任务栏


def test_centered_when_cursor_in_middle():
    r = fit_panel((960, 300), SCREEN)              # 光标靠上，整窗放得下，不需要夹紧
    assert (r.w, r.h) == (PANEL_WIDTH, PANEL_HEIGHT)
    assert r.x == 960 - PANEL_WIDTH // 2
    assert r.y == 300 - CURSOR_OFFSET_Y


def test_cursor_near_bottom_is_pulled_up_so_whole_panel_visible():
    r = fit_panel((960, 1035), SCREEN)
    assert _inside(r, SCREEN)
    assert r.y + r.h == SCREEN.h - MARGIN          # 贴住可用区域底边（任务栏之上）


def test_cursor_near_right_edge_is_pulled_left():
    r = fit_panel((1915, 300), SCREEN)
    assert _inside(r, SCREEN)
    assert r.x + r.w == SCREEN.w - MARGIN


def test_cursor_near_top_left_corner():
    r = fit_panel((0, 0), SCREEN)
    assert _inside(r, SCREEN)
    assert r.x == MARGIN and r.y == MARGIN


def test_small_screen_shrinks_panel_instead_of_overflowing():
    small = Rect(0, 0, 800, 560)                   # 小屏，可用高度不足 640
    r = fit_panel((400, 540), small)
    assert _inside(r, small)
    assert r.h == 560 - 2 * MARGIN
    assert r.w == PANEL_WIDTH                      # 宽度够，保持目标宽度


def test_secondary_monitor_with_negative_origin():
    left_screen = Rect(-1920, 0, 1920, 1040)       # 左侧副屏
    r = fit_panel((-1915, 1030), left_screen)
    assert _inside(r, left_screen)


@pytest.mark.parametrize("cursor", [(0, 0), (1919, 1039), (960, 520), (5, 1035), (1915, 3)])
def test_every_corner_and_middle_stays_inside(cursor):
    r = fit_panel(cursor, SCREEN)
    assert _inside(r, SCREEN)
    assert r.w <= PANEL_WIDTH and r.h <= PANEL_HEIGHT


def test_qt_open_panel_clamps_to_screen(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop import quick_panel as qp

    app = QApplication.instance() or QApplication([])

    class _FakeCursor:
        @staticmethod
        def pos():
            return QPoint(app.primaryScreen().availableGeometry().right(),
                          app.primaryScreen().availableGeometry().bottom())

    monkeypatch.setattr(qp, "QCursor", _FakeCursor)
    panel = qp.QuickPanel(lambda: None)
    panel.open_panel()
    app.processEvents()

    avail = app.primaryScreen().availableGeometry()
    fg = panel.frameGeometry()
    assert fg.left() >= avail.left() and fg.top() >= avail.top()
    assert fg.right() <= avail.right() and fg.bottom() <= avail.bottom()
    # 回复区要有实际高度，否则“只显示一小部分”的问题仍在
    assert panel.reply.height() >= 60
    assert panel.minimumHeight() <= panel.height()
    panel.hide()
