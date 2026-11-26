"""E1 热键设置：校验纯函数 + 设置窗口（离屏 Qt）。"""

import pytest

from mini_agent_desktop.hotkey import validate_hotkey_text


@pytest.mark.parametrize("spec", ["Ctrl+Alt+Space", "win+shift+k", "Ctrl+F5"])
def test_valid_specs_return_empty_message(spec):
    assert validate_hotkey_text(spec) == ""


@pytest.mark.parametrize("spec", ["", "Space", "Ctrl+A+B", "Ctrl+Foo", "   "])
def test_invalid_specs_return_reason(spec):
    msg = validate_hotkey_text(spec)
    assert msg and isinstance(msg, str)


def _dialog(enabled, hotkey, on_save):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.settings_dialog import SettingsDialog

    QApplication.instance() or QApplication([])
    return SettingsDialog(enabled, hotkey, on_save)


def test_save_disabled_while_hotkey_invalid_and_enabled():
    dlg = _dialog(True, "Space", lambda e, c: (True, "ok"))
    from PySide6.QtWidgets import QDialogButtonBox
    save = dlg.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save.isEnabled() is False
    assert "⚠" in dlg.status.text()
    dlg.hotkey.setText("Ctrl+Alt+K")
    assert save.isEnabled() is True
    assert dlg.status.text() == ""


def test_disabled_hotkey_skips_validation():
    dlg = _dialog(False, "Space", lambda e, c: (True, "ok"))
    from PySide6.QtWidgets import QDialogButtonBox
    assert dlg.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled() is True


def test_successful_save_passes_values_and_accepts():
    got = []

    def on_save(enabled, combo):
        got.append((enabled, combo))
        return True, "已注册热键 Ctrl+Alt+K"

    dlg = _dialog(False, "", on_save)
    dlg.hotkey_enabled.setChecked(True)
    dlg.hotkey.setText(" Ctrl+Alt+K ")
    dlg._save()
    assert got == [(True, "Ctrl+Alt+K")]
    assert dlg.result() == 1          # QDialog.Accepted


def test_failed_registration_keeps_dialog_open_with_reason():
    def on_save(enabled, combo):
        return False, "热键未注册：已被占用"

    dlg = _dialog(False, "", on_save)
    dlg.hotkey_enabled.setChecked(True)
    dlg.hotkey.setText("Ctrl+Alt+K")
    dlg._save()
    assert dlg.result() == 0
    assert "已被占用" in dlg.status.text()


def test_tray_settings_menu_item_calls_back():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.state import TrayModel
    from mini_agent_desktop.tray import TrayUi

    QApplication.instance() or QApplication([])
    called = []
    ui = TrayUi(TrayModel(), "http://x", on_start_daemon=lambda: None,
                on_reconnect=lambda: None, on_quit=lambda: None,
                on_open_settings=lambda: called.append(1))
    assert "设置…" in [a.text() for a in ui.menu.actions()]
    ui.settings_action.trigger()
    assert called == [1]
