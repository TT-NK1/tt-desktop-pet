"""Client-area presets and geometry preferences for the TT chat room."""

import sys

from PySide6.QtCore import QObject, QPoint, QRect, QSize, QTimer
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QSizeGrip, QToolButton

from tt_theme import theme_icon


PRESETS = {
    'mini': ('迷你', 760, 620),
    'compact': ('紧凑', 840, 680),
    'standard': ('标准', 960, 760),
}
MINIMUM = QSize(480, 500)


def dashboard_mode(size):
    width, height = size.width(), size.height()
    if width < 800 or height < 650:
        return 'mini'
    if width < 900 or height < 725:
        return 'compact'
    if width >= 1190 and height >= 830:
        return 'large'
    return 'wide' if width >= 1060 else 'standard'


class WindowLayoutManager(QObject):
    setting = 'chat_window_layout'

    def __init__(self, room):
        super().__init__(room)
        self.room = room
        self.store = None
        self.preset = 'standard'
        self.screen = None
        self.handle = None
        self.restored = False
        self.applying = False
        self.preset_size = None
        self.last_saved = None
        self.settle = QTimer(self, singleShot=True, interval=70)
        self.settle.timeout.connect(self.confirm_preset_geometry)
        self.timer = QTimer(self, singleShot=True, interval=250)
        self.timer.timeout.connect(self.save)
        self.button = QToolButton()
        self.button.setObjectName('windowSizeButton')
        self.button.setFixedSize(30, 27)
        self.button.setIconSize(QSize(18, 18))
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.button.setAccessibleName('切换窗口尺寸')
        menu = QMenu(self.button)
        group = QActionGroup(menu)
        self.button.setMenu(menu)
        self.actions = {}
        for key, (label, width, height) in PRESETS.items():
            title = f'{label}  {width} × {height}'
            action = menu.addAction(title)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _, selected=key: self.apply(selected))
            self.actions[key] = action
        self.grip = QSizeGrip(room)
        self.grip.setFixedSize(17, 17)
        self.grip.setToolTip('拖动调整窗口大小')
        self.grip.setAccessibleName('调整窗口大小')
        self.update_controls()

    def set_color(self, color):
        self.button.setIcon(theme_icon('monitor', color, self.button))

    def bind(self, store):
        self.store = store

    def unbind(self):
        self.save()
        self.store = None

    def show(self):
        self.attach_handle()
        if self.restored:
            return
        self.restored = True
        saved = self.store.get_setting(self.setting, {}) if self.store else {}
        saved = saved if isinstance(saved, dict) else {}
        geometry = saved.get('geometry')
        valid = (isinstance(geometry, list) and len(geometry) == 4 and
                 all(type(value) is int for value in geometry) and
                 all(-(2**30) < value < 2**30 for value in geometry) and
                 all(value > 0 for value in geometry[2:]))
        rect = QRect(*geometry) if valid else None
        preset = saved.get('preset')
        preset = preset if isinstance(preset, str) and preset in PRESETS else None
        target = QApplication.screenAt(rect.center()) if rect else None
        target = target or self.room.screen() or QApplication.primaryScreen()
        if preset:
            self.set_geometry(preset, target, rect.topLeft() if rect and
                              target.availableGeometry().contains(rect.center()) else None)
        elif rect and target.availableGeometry().contains(rect.center()):
            self.set_geometry(preset, target, rect.topLeft(), rect.size())
        elif rect:
            self.set_geometry(preset, target, manual_size=rect.size())
        else:
            self.set_geometry('standard', target)

    def watch_screen(self, screen):
        if screen is None or screen == self.screen:
            return
        if self.screen is not None and self.screen in QApplication.screens():
            self.screen.availableGeometryChanged.disconnect(self.work_area_changed)
        self.screen = screen
        screen.availableGeometryChanged.connect(self.work_area_changed)
        if not self.applying:
            self.limit_window(screen)

    def work_area_changed(self, _available):
        if self.room.isVisible() and not self.room.isMaximized():
            self.set_geometry(self.preset, self.screen, self.room.frameGeometry().topLeft(), self.room.size())

    def frame_margins(self):
        return self.room.windowHandle().frameMargins()

    def native_resize_hit(self, event_type, message):
        if sys.platform != 'win32' or bytes(event_type) != b'windows_generic_MSG':
            return None
        from ctypes import WinDLL, byref, c_short, wintypes

        msg = wintypes.MSG.from_address(int(message))
        if msg.message != 0x0084 or self.room.isMaximized() or self.room.isFullScreen():
            return None
        user = WinDLL('user32')
        frame, origin = wintypes.RECT(), wintypes.POINT()
        if not user.GetClientRect(msg.hWnd, byref(frame)) or not user.ClientToScreen(msg.hWnd, byref(origin)):
            return None
        x, y = c_short(msg.lParam).value-origin.x, c_short(msg.lParam >> 16).value-origin.y
        border = max(4, round(5*self.room.devicePixelRatioF()))
        if not (0 <= x < frame.right and 0 <= y < frame.bottom):
            return None
        horizontal = -1 if x < frame.left+border else 1 if x >= frame.right-border else 0
        vertical = -1 if y < frame.top+border else 1 if y >= frame.bottom-border else 0
        return {(-1,0):10, (1,0):11, (0,-1):12, (-1,-1):13, (1,-1):14,
                (0,1):15, (-1,1):16, (1,1):17}.get((horizontal,vertical))

    def attach_handle(self):
        handle = self.room.windowHandle()
        if handle is not None and handle is not self.handle:
            self.handle = handle
            handle.screenChanged.connect(self.watch_screen)

    def limit_window(self, screen):
        available = screen.availableGeometry()
        margin = self.frame_margins()
        maximum = QSize(max(1, available.width()-margin.left()-margin.right()),
                        max(1, available.height()-margin.top()-margin.bottom()))
        minimum = MINIMUM.boundedTo(maximum)
        if self.room.minimumSize() != minimum:
            self.room.setMinimumSize(minimum)
        if self.room.maximumSize() != maximum:
            self.room.setMaximumSize(maximum)
        return maximum

    def apply(self, preset):
        if preset not in PRESETS:
            return
        if self.room.isMaximized() or self.room.isFullScreen():
            self.room.showNormal()
        self.attach_handle()
        self.set_geometry(preset, self.room.screen(), self.room.frameGeometry().topLeft())

    def set_geometry(self, preset, screen, position=None, manual_size=None):
        self.applying = True
        try:
            self.watch_screen(screen)
            maximum = self.limit_window(screen)
            if preset:
                _, width, height = PRESETS[preset]
                ratio = min(1, maximum.width()/width, maximum.height()/height)
                size = QSize(round(width*ratio), round(height*ratio))
            else:
                size = manual_size
            size = size.expandedTo(self.room.minimumSize()).boundedTo(maximum)
            area = screen.availableGeometry()
            margin = self.frame_margins()
            frame = QSize(size.width()+margin.left()+margin.right(), size.height()+margin.top()+margin.bottom())
            point = position if position is not None else area.center() - QPoint(frame.width()//2, frame.height()//2)
            self.room.move(max(area.left(),min(point.x(), area.right()-frame.width()+1)),
                           max(area.top(),min(point.y(), area.bottom()-frame.height()+1)))
            self.room.resize(size)
            self.preset, self.preset_size = preset, size
        finally:
            self.applying = False
        self.update_controls()
        if preset:
            self.settle.start()
        self.timer.start()

    def confirm_preset_geometry(self):
        # Windows can report a second client-area size when it updates the native caption.
        if self.preset and self.room.size() != self.preset_size and not self.room.isMaximized():
            self.applying = True
            try:
                self.room.resize(self.preset_size)
            finally:
                self.applying = False
            self.update_controls()

    def resized(self):
        self.grip.move(self.room.width()-self.grip.width(), self.room.height()-self.grip.height())
        self.grip.raise_()
        if self.restored and not self.applying and not self.room.isMaximized() and not self.room.isFullScreen():
            if self.room.size() != self.preset_size and not self.settle.isActive():
                self.preset = None
            self.timer.start()
        if not self.applying:
            self.update_controls()

    def moved(self):
        if self.restored and not self.applying:
            self.timer.start()

    def update_controls(self):
        for key, action in self.actions.items():
            checked = key == self.preset
            if action.isChecked() != checked:
                action.setChecked(checked)
        self.button.setToolTip(f'窗口尺寸 · {self.room.width()} × {self.room.height()}')

    def save(self):
        self.timer.stop()
        if not self.store or not self.restored or self.room.isMaximized() or self.room.isFullScreen() or self.room.isMinimized():
            return
        frame = self.room.frameGeometry()
        data = {'geometry': [frame.x(), frame.y(), self.room.width(), self.room.height()],
                'preset': self.preset}
        if data != self.last_saved:
            self.store.save_setting(self.setting, data)
            self.last_saved = data
