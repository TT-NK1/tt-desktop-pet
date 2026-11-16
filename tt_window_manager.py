"""Window lifetime and non-activating z-order repair for the TT desktop overlays."""
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication


GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x0008
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
KEEP_GEOMETRY_AND_FOCUS = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
WM_MOUSEACTIVATE = 0x0021
WM_WINDOWPOSCHANGED = 0x0047
WM_STYLECHANGED = 0x007D
WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218
WM_DPICHANGED = 0x02E0


class WindowPosition(ctypes.Structure):
    _fields_ = [('hwnd',wintypes.HWND),('after',wintypes.HWND),
                ('x',ctypes.c_int),('y',ctypes.c_int),('width',ctypes.c_int),('height',ctypes.c_int),
                ('flags',wintypes.UINT)]


class Windows:
    def __init__(self):
        self.api = ctypes.WinDLL('user32',use_last_error=True)
        self.api.GetWindowLongW.argtypes = (wintypes.HWND,ctypes.c_int)
        self.api.GetWindowLongW.restype = wintypes.LONG
        self.api.IsWindowVisible.argtypes = (wintypes.HWND,)
        self.api.IsIconic.argtypes = (wintypes.HWND,)
        self.api.GetAncestor.argtypes = (wintypes.HWND,wintypes.UINT)
        self.api.GetAncestor.restype = wintypes.HWND
        self.api.SetWindowPos.argtypes = (wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,
                                         ctypes.c_int,ctypes.c_int,wintypes.UINT)
        self.api.SetWindowPos.restype = wintypes.BOOL
        self.api.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
        self.api.RegisterWindowMessageW.restype = wintypes.UINT
        self.taskbar_created = self.api.RegisterWindowMessageW('TaskbarCreated')

    def topmost(self,hwnd):
        return bool(self.api.GetWindowLongW(hwnd,GWL_EXSTYLE) & WS_EX_TOPMOST)

    def visible(self,hwnd):
        return self.api.IsWindowVisible(hwnd) and not self.api.IsIconic(hwnd)

    def promote(self,hwnd):
        return bool(self.api.SetWindowPos(hwnd,HWND_TOPMOST,0,0,0,0,KEEP_GEOMETRY_AND_FOCUS))


class NativeEvents(QAbstractNativeEventFilter):
    def __init__(self,manager):
        super().__init__()
        self.manager = manager

    def nativeEventFilter(self,event_type,message):
        if event_type != b'windows_generic_MSG':
            return False,0
        msg = wintypes.MSG.from_address(int(message))
        manager = self.manager
        if msg.message == WM_MOUSEACTIVATE and manager.windows.api.GetAncestor(msg.hWnd,2) in manager.handles:
            return True,3  # MA_NOACTIVATE, deliver the click to the widget.
        if msg.hWnd not in manager.handles:
            return False,0
        if msg.message in (WM_DISPLAYCHANGE,WM_DPICHANGED):
            manager.request('display configuration',reposition=True)
        elif msg.message == WM_POWERBROADCAST and msg.wParam in (7,18):
            manager.request('resume',reposition=True)
        elif msg.message == manager.windows.taskbar_created:
            manager.request('Explorer taskbar recreated')
        elif msg.message == WM_STYLECHANGED and ctypes.c_int(msg.wParam).value == GWL_EXSTYLE:
            if not manager.windows.topmost(msg.hWnd):
                manager.request('native style changed')
        elif msg.message == WM_WINDOWPOSCHANGED and msg.lParam:
            pos = WindowPosition.from_address(msg.lParam)
            if not pos.flags & SWP_NOZORDER and not manager.windows.topmost(msg.hWnd):
                manager.request('native z-order changed')
        return False,0


class WindowManager(QObject):
    EVENTS = (QEvent.Type.Show,QEvent.Type.Hide,QEvent.Type.WinIdChange,QEvent.Type.WindowStateChange)

    def __init__(self,pet,log_path):
        super().__init__(pet)
        self.pet = pet
        self.overlays = {}
        self.handles = {}
        self.identities = {}
        self.failures = set()
        self.app = QApplication.instance()
        self.windows = Windows() if sys.platform=='win32' and self.app.platformName()=='windows' else None
        self.native = NativeEvents(self) if self.windows else None
        self.log = self.make_log(log_path)
        self.reason = 'startup'
        self.reposition = False
        self.context_lost = False
        self.stopped = False
        self.pending = QTimer(self,interval=0,singleShot=True)
        self.pending.timeout.connect(self.check)
        self.placement = QTimer(self,interval=0,singleShot=True)
        self.placement.timeout.connect(self.place_overlays)
        self.watch = QTimer(self,interval=15000)
        self.watch.timeout.connect(self.check)
        self.track(pet,'pet')
        self.app.screenAdded.connect(self.screen_changed)
        self.app.screenRemoved.connect(self.screen_changed)
        if self.native:
            self.app.installNativeEventFilter(self.native)
            self.watch.start()

    @staticmethod
    def make_log(path):
        logger = logging.Logger('tt.window_manager')
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            handler = RotatingFileHandler(path,maxBytes=256*1024,backupCount=2,encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        except OSError:
            handler = logging.NullHandler()
        logger.addHandler(handler)
        return logger

    def track(self,widget,role):
        self.overlays[widget] = role
        widget.installEventFilter(self)
        self.record(widget,'registered')

    def record(self,widget,event):
        hwnd = int(widget.effectiveWinId() or 0)
        previous = self.identities.get(widget,0)
        if hwnd != previous and previous:
            self.handles.pop(previous,None)
            self.log.info('hwnd changed role=%s previous=%#x next=%#x',self.overlays[widget],previous,hwnd)
        if hwnd:
            self.handles[hwnd] = widget
        self.identities[widget] = hwnd
        self.log.info('%s role=%s hwnd=%#x visible=%s minimized=%s flags=%#x native_topmost=%s',
                      event,self.overlays[widget],hwnd,widget.isVisible(),widget.isMinimized(),
                      int(widget.windowFlags()),self.windows.topmost(hwnd) if self.windows and hwnd else None)

    def eventFilter(self,widget,event):
        kind = event.type()
        if kind == QEvent.Type.Resize or (widget is self.pet and kind == QEvent.Type.Move):
            self.schedule_placement()
        if kind in self.EVENTS:
            self.record(widget,kind.name)
            if widget is self.pet and (kind == QEvent.Type.Hide or widget.isMinimized()):
                for overlay in self.overlays:
                    if overlay is not self.pet and not overlay.isHidden():
                        overlay.hide()
            elif kind != QEvent.Type.Hide:
                self.request(kind.name)
        return False

    def screen_changed(self,_screen):
        self.request('screen added or removed',reposition=True)

    def show_overlay(self,widget):
        if self.pet.isVisible() and not self.pet.isMinimized() and not self.pet.closing:
            widget.show()
            self.request('overlay shown')

    def show_panel(self,widget):
        if not self.pet.isVisible() or self.pet.isMinimized() or self.pet.closing:
            return
        for other,role in self.overlays.items():
            if other is not widget and role in ('music','weather') and not other.isHidden():
                other.hide()
        self.show_overlay(widget)

    def schedule_placement(self):
        if not self.stopped and not self.placement.isActive():
            self.placement.start()

    def place_overlays(self):
        for widget,role in self.overlays.items():
            if role != 'pet' and widget.isVisible():
                if role == 'weather':
                    widget.place()
                else:
                    widget.follow_pet()

    def request(self,reason,*,reposition=False):
        if self.stopped:
            return
        self.reason = reason
        self.reposition |= reposition
        if not self.pending.isActive():
            self.pending.start()

    def check(self):
        reason,self.reason = self.reason,'watchdog'
        if not self.windows or not self.pet.isVisible() or self.pet.isMinimized() or self.pet.closing:
            return
        if self.reposition:
            self.reposition = False
            self.pet.move_clamped(self.pet.x(),self.pet.y())
            self.pet.recheck_art_anchor()
            self.schedule_placement()
        for widget,role in self.overlays.items():
            hwnd = self.identities.get(widget,0)
            if not hwnd or not widget.isVisible() or widget.isMinimized() or not self.windows.visible(hwnd):
                continue
            if self.windows.topmost(hwnd):
                self.failures.discard(hwnd)
                continue
            if self.windows.promote(hwnd) and self.windows.topmost(hwnd):
                self.failures.discard(hwnd)
                self.log.info('topmost restored role=%s hwnd=%#x trigger=%s',role,hwnd,reason)
            elif hwnd not in self.failures:
                self.log.warning('topmost restore failed role=%s hwnd=%#x trigger=%s winerror=%s',
                                 role,hwnd,reason,ctypes.get_last_error())
                self.failures.add(hwnd)

    def observe_context(self,lost):
        if lost != self.context_lost:
            self.context_lost = lost
            self.log.info('WebGL context %s hwnd=%#x','lost' if lost else 'restored',
                          self.identities.get(self.pet,0))

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.pending.stop()
        self.placement.stop()
        self.watch.stop()
        self.app.screenAdded.disconnect(self.screen_changed)
        self.app.screenRemoved.disconnect(self.screen_changed)
        if self.native:
            self.app.removeNativeEventFilter(self.native)
            self.native = None
        for widget in self.overlays:
            widget.removeEventFilter(self)
        for handler in tuple(self.log.handlers):
            handler.close()
            self.log.removeHandler(handler)
