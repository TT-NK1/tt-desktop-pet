"""Appearance preferences and theme resources for the TT chat window."""

from dataclasses import dataclass
import sys
from string import Template

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu


OPTIONS = (
    ('light', '浅色', '淡紫樱花窗景', 'sun'),
    ('dark', '深色', '午夜暗紫', 'moon'),
    ('system', '跟随系统', '随 Windows 应用外观切换', 'monitor'),
)
MODES = frozenset(option[0] for option in OPTIONS)


@dataclass(frozen=True)
class ThemePalette:
    background: str
    surface: str
    card: str
    input: str
    button: str
    hover: str
    text: str
    muted: str
    disabled: str
    border: str
    accent: str
    highlight: str
    selected_text: str
    user: str
    assistant: str
    code: str
    glass: str
    welcome: str

    def css(self, source):
        return Template(source).substitute(vars(self))

    def qt(self, original):
        palette = QPalette(original)
        roles = QPalette.ColorRole
        for role, value in (
            (roles.Window, self.background), (roles.WindowText, self.text),
            (roles.Base, self.input), (roles.AlternateBase, self.card),
            (roles.Text, self.text), (roles.Button, self.button), (roles.ButtonText, self.text),
            (roles.PlaceholderText, self.muted), (roles.Link, self.accent),
            (roles.LinkVisited, self.highlight), (roles.Highlight, self.highlight),
            (roles.HighlightedText, self.selected_text), (roles.ToolTipBase, self.card),
            (roles.ToolTipText, self.text), (roles.Accent, self.accent),
            (roles.Mid, self.border), (roles.Dark, self.border),
            (roles.Light, self.hover), (roles.Shadow, self.background),
        ):
            palette.setColor(role, QColor(value))
        for role in (roles.WindowText, roles.Text, roles.ButtonText, roles.PlaceholderText):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(self.disabled))
        return palette


LIGHT = ThemePalette(
    background='#f4edf9', surface='#f9f5fc', card='#f0e8f7', input='#ffffff',
    button='#e9dcf4', hover='#dec9ee', text='#493957', muted='#6f5d7b', disabled='#756c7e',
    border='#ddcee9', accent='#795591', highlight='#795591', selected_text='#ffffff',
    user='#eee2f8', assistant='#ffffff', code='#ece5f4',
    glass='rgba(255,255,255,137)', welcome='rgba(252,248,255,224)',
)
DARK = ThemePalette(
    background='#191720', surface='#211d29', card='#292333', input='#24202d',
    button='#382e46', hover='#483956', text='#f1eaf8', muted='#b9a9c9', disabled='#a694b5',
    border='#493b59', accent='#b995e0', highlight='#d5b8f5', selected_text='#211829',
    user='#392d48', assistant='#292333', code='#342b40',
    glass='rgba(47,39,58,218)', welcome='rgba(53,42,65,238)',
)

DARK_UI = '''
QWidget#cozyWindow, QWidget#ttSettings, QDialog, QMessageBox {
    background-color: $background; color: $text;
}
QLabel, QCheckBox, QRadioButton, QGroupBox { color: $text; }
QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled { color: $disabled; }
QPlainTextEdit, QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit,
QDateEdit, QDateTimeEdit, QListView, QTreeView, QTableView, QAbstractItemView {
    background-color: $input; color: $text; border-color: $border;
    selection-background-color: $highlight; selection-color: $selected_text;
}
QAbstractItemView { alternate-background-color: $card; }
QHeaderView::section { background-color: $card; color: $text; border-color: $border; }
QPushButton, QToolButton { background-color: $button; color: $text; border-color: $border; }
QPushButton:hover, QToolButton:hover, QComboBox::drop-down:hover { background-color: $hover; }
QPushButton:disabled, QToolButton:disabled { background-color: $card; color: $disabled; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus { border-color: $accent; }
QMenu { background-color: $card; color: $text; border: 1px solid $border; }
QMenu::item:selected { background-color: $hover; color: $text; }
QMenu::item:disabled { color: $disabled; }
QToolTip { background-color: $card; color: $text; border: 1px solid $border; }
QScrollArea, QScrollArea > QWidget > QWidget {
    background-color: $surface; color: $text; border-color: $border;
}
QScrollBar { background-color: $surface; }
QScrollBar::handle { background-color: $hover; border-radius: 4px; }
QScrollBar::handle:hover { background-color: $accent; }
QScrollBar::add-page, QScrollBar::sub-page { background-color: $surface; }
QScrollBar::add-line, QScrollBar::sub-line { background-color: $card; }
'''


def qt_file_dialog(widget):
    """Use the themed Qt picker if the app and Windows use different colors."""
    while widget is not None:
        dark = widget.property('ttThemeDark')
        if dark is not None:
            if dark or QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                return QFileDialog.Option.DontUseNativeDialog
            break
        widget = widget.parentWidget()
    return QFileDialog.Option(0)


def theme_icon(symbol, color, widget):
    dpr = widget.devicePixelRatioF()
    icon = QPixmap(round(18 * dpr), round(18 * dpr))
    icon.setDevicePixelRatio(dpr)
    icon.fill(Qt.GlobalColor.transparent)
    with QPainter(icon) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if symbol == 'sun':
            painter.drawEllipse(QPointF(9, 9), 3.2, 3.2)
            for angle in range(0, 360, 45):
                painter.save()
                painter.translate(9, 9)
                painter.rotate(angle)
                painter.drawLine(QPointF(0, -5.3), QPointF(0, -7.4))
                painter.restore()
        elif symbol == 'moon':
            painter.drawArc(QRectF(2.8, 2.4, 12.5, 12.5), 95 * 16, 237 * 16)
            painter.drawArc(QRectF(7.1, 1.7, 9.7, 9.7), 124 * 16, 169 * 16)
        else:
            painter.drawRoundedRect(QRectF(2, 3, 14, 9.4), 1.7, 1.7)
            painter.drawLine(QPointF(9, 12.5), QPointF(9, 15.5))
            painter.drawLine(QPointF(6, 15.5), QPointF(12, 15.5))
    return QIcon(icon)


class ThemeManager(QObject):
    changed = Signal(str, bool)
    setting = 'appearance_theme'

    def __init__(self, room):
        super().__init__(room)
        self.room = room
        self.store = None
        self.mode = 'light'
        self.dark = False
        self.palette = LIGHT
        self.hints = QApplication.styleHints()
        self.app_palette = QApplication.style().standardPalette()
        self.light_palette = (QApplication.palette() if self.hints.colorScheme() == Qt.ColorScheme.Light
                              else LIGHT.qt(self.app_palette))
        self.hints.colorSchemeChanged.connect(self.system_changed)
        QApplication.instance().installEventFilter(self)
        self.motion_menu = None

    def widget_palette(self):
        return DARK.qt(self.app_palette) if self.dark else self.light_palette

    def bind(self, settings):
        self.store = settings
        mode = settings.get_setting(self.setting, 'light')
        self.mode = mode if isinstance(mode, str) and mode in MODES else 'light'
        self.update_theme(self.hints.colorScheme(), force=True)

    def set_mode(self, mode):
        if not isinstance(mode, str) or mode not in MODES or mode == self.mode:
            return
        if self.store is not None:
            self.store.save_setting(self.setting, mode)
        self.mode = mode
        self.update_theme(self.hints.colorScheme(), force=True)

    def system_changed(self, scheme):
        if self.mode == 'system':
            self.update_theme(scheme)

    def update_theme(self, scheme, force=False):
        dark = self.mode == 'dark' or self.mode == 'system' and scheme == Qt.ColorScheme.Dark
        if not force and dark == self.dark:
            return
        self.dark = dark
        self.palette = DARK if dark else LIGHT
        self.changed.emit(self.mode, dark)
        self.update_caption(self.room)
        for popup in self.room.findChildren(QDialog):
            if popup.isVisible():
                self.polish_popup(popup)
        for popup in self.room.findChildren(QMenu):
            if popup.isVisible():
                self.polish_popup(popup)
        if self.motion_menu is not None:
            self.style_motion_menu()

    def update_caption(self, window):
        if sys.platform != 'win32' or not window.isVisible():
            return
        from ctypes import byref, c_int, c_void_p, sizeof, windll
        value = c_int(self.dark)
        try:
            windll.dwmapi.DwmSetWindowAttribute(c_void_p(int(window.winId())), 20, byref(value), sizeof(value))
        except (AttributeError, OSError):
            pass

    def polish_popup(self, popup):
        popup.setPalette(self.widget_palette())
        if isinstance(popup, QDialog):
            self.update_caption(popup)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.ShowToParent):
            if watched is self.room:
                self.update_caption(self.room)
            elif isinstance(watched, (QDialog, QMenu)) and self.room.isAncestorOf(watched):
                self.polish_popup(watched)
        return False

    def style_motion_menu(self):
        self.motion_menu.setPalette(self.widget_palette())
        self.motion_menu.setStyleSheet(self.palette.css(DARK_UI) if self.dark else '')

    def exec_motion_menu(self, menu, position):
        original_style, original_palette = menu.styleSheet(), menu.palette()
        self.motion_menu = menu
        self.style_motion_menu()
        try:
            menu.exec(position)
        finally:
            self.motion_menu = None
            menu.setStyleSheet(original_style)
            menu.setPalette(original_palette)
