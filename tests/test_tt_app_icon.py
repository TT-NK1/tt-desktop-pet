import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

import tt_app_icon


class AppIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_shortcuts_only_select_tt(self):
        with patch.object(tt_app_icon.sys, "platform", "linux"), \
                patch.object(tt_app_icon.QStandardPaths, "standardLocations",
                             return_value=["/desktop", "/desktop"]), \
                patch.object(Path, "iterdir", return_value=iter([
                    Path("/desktop/TT.lnk"), Path("/desktop/Python.lnk"),
                    Path("/desktop/TT.txt"),
                ])):
            self.assertEqual(list(tt_app_icon.desktop_shortcuts()),
                             [Path("/desktop/TT.lnk")])

    def test_application_icon_is_inherited_by_independent_windows(self):
        original = self.app.windowIcon()
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor("purple"))
        icon = QIcon(pixmap)
        window = None
        try:
            with patch.object(tt_app_icon, "desktop_shortcuts",
                              return_value=[Path("TT.lnk")]), \
                    patch.object(tt_app_icon, "QFileIconProvider") as provider:
                provider.return_value.icon.return_value = icon
                self.assertTrue(tt_app_icon.apply_desktop_icon(self.app))
            window = QWidget()
            self.assertEqual(window.windowIcon().cacheKey(), icon.cacheKey())
        finally:
            if window is not None:
                window.close()
            self.app.setWindowIcon(original)

    def test_custom_artwork_precedes_python_target(self):
        original = self.app.windowIcon()
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor("green"))
        icon = QIcon(pixmap)
        try:
            with patch.object(tt_app_icon, "desktop_shortcuts",
                              return_value=[Path("TT.lnk")]), \
                    patch.object(tt_app_icon, "shortcut_artwork", return_value=icon), \
                    patch.object(tt_app_icon, "QFileInfo") as file_info:
                self.assertTrue(tt_app_icon.apply_desktop_icon(self.app))
                file_info.assert_not_called()
                self.assertEqual(self.app.windowIcon().cacheKey(), icon.cacheKey())
        finally:
            self.app.setWindowIcon(original)

    def test_missing_shortcut_preserves_current_icon(self):
        original = self.app.windowIcon().cacheKey()
        with patch.object(tt_app_icon, "desktop_shortcuts", return_value=[]):
            self.assertFalse(tt_app_icon.apply_desktop_icon(self.app))
        self.assertEqual(self.app.windowIcon().cacheKey(), original)

    def test_shortcut_icon_is_read_from_target_without_overlay(self):
        with patch.object(tt_app_icon, "desktop_shortcuts",
                          return_value=[Path("TT.lnk")]), \
                patch.object(tt_app_icon, "QFileInfo") as file_info, \
                patch.object(tt_app_icon, "QFileIconProvider") as provider:
            shortcut_info = file_info.return_value
            shortcut_info.symLinkTarget.return_value = "C:/Python/pythonw.exe"
            provider.return_value.icon.return_value.isNull.return_value = True
            self.assertFalse(tt_app_icon.apply_desktop_icon(self.app))
            self.assertEqual(file_info.call_args_list[-1].args,
                             ("C:/Python/pythonw.exe",))
            provider.return_value.icon.assert_called_once_with(file_info.return_value)
