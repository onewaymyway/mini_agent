"""E1：热键设置窗口（PySide6）。

只负责输入与展示：校验用 hotkey.validate_hotkey_text；真正的保存与热键重注册由
app.py 通过 on_save 回调完成，回调返回 (ok, 文字说明) 显示在窗口底部。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget, QDialog,
)

from mini_agent_desktop.hotkey import validate_hotkey_text

OnSave = Callable[[bool, str], tuple]


class SettingsDialog(QDialog):
    def __init__(self, enabled: bool, hotkey: str, on_save: OnSave, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mini_agent 设置")
        self._on_save = on_save

        self.hotkey_enabled = QCheckBox("启用全局热键唤起快速面板")
        self.hotkey_enabled.setChecked(enabled)
        self.hotkey = QLineEdit(hotkey)
        self.hotkey.setPlaceholderText("例如 Ctrl+Alt+Space / Win+Shift+K")
        self.hint = QLabel(
            "格式：至少一个修饰键（Ctrl/Alt/Shift/Win）+ 一个普通键。\n"
            "保存后立即重新注册，无需重启。"
        )
        self.hint.setWordWrap(True)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.addRow(self.hotkey_enabled)
        form.addRow("组合键：", self.hotkey)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        self.hotkey.textChanged.connect(self._validate_live)
        self.hotkey_enabled.toggled.connect(self._validate_live)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self.hint)
        root.addWidget(self.status)
        root.addWidget(self.buttons)
        self.setMinimumWidth(420)
        self._validate_live()

    def _validate_live(self) -> None:
        """组合键非法且启用时，禁止保存并给出原因；关闭热键时不校验。"""
        problem = ""
        if self.hotkey_enabled.isChecked():
            problem = validate_hotkey_text(self.hotkey.text())
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not problem)
        self.status.setText(f"⚠ {problem}" if problem else "")

    def _save(self) -> None:
        enabled = self.hotkey_enabled.isChecked()
        combo = self.hotkey.text().strip()
        if enabled and validate_hotkey_text(combo):
            return
        ok, message = self._on_save(enabled, combo)
        self.status.setText(("✔ " if ok else "⚠ ") + message)
        if ok:
            self.accept()

    def values(self) -> tuple[bool, str]:
        return self.hotkey_enabled.isChecked(), self.hotkey.text().strip()
