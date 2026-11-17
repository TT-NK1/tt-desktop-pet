"""Detect foreground Windows games, then track their lifetime across Alt+Tab."""

import ctypes
from ctypes import wintypes
import os
from pathlib import PureWindowsPath


GAME_DIRECTORIES = ('\\steamapps\\common\\', '\\xboxgames\\', '\\gog galaxy\\games\\',
                    '\\epic games\\', '\\riot games\\league of legends\\game\\',
                    '\\riot games\\valorant\\live\\shootergame\\')
GAME_EXECUTABLES = {'overwatch.exe', 'cs2.exe', 'valorant-win64-shipping.exe',
                    'league of legends.exe', 'minecraft.windows.exe', 'r5apex.exe',
                    'genshinimpact.exe', 'yuanshen.exe', 'starrail.exe', 'zenlesszonezero.exe'}
NON_GAME_EXECUTABLES = {'wallpaper32.exe', 'wallpaper64.exe', 'wallpaperui.exe',
                        'epicgameslauncher.exe', 'unrealeditor.exe'}


def game_image(image):
    path = str(PureWindowsPath(image)).casefold()
    name = PureWindowsPath(path).name
    return (name not in NON_GAME_EXECUTABLES
            and '\\wallpaper_engine\\' not in path
            and '\\epic games\\launcher\\' not in path
            and not name.startswith('unrealeditor')
            and (name in GAME_EXECUTABLES or any(folder in path for folder in GAME_DIRECTORIES)))


class GameActivity:
    def __init__(self):
        self.games = {}
        self.foreground_pid = 0
        self.supported = os.name == 'nt'
        if not self.supported:
            return
        self.user = ctypes.windll.user32
        self.kernel = ctypes.windll.kernel32
        self.shell = ctypes.windll.shell32
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        self.kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        self.kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.shell.SHQueryUserNotificationState.argtypes = (ctypes.POINTER(ctypes.c_int),)

    def active(self):
        if not self.supported:
            return False
        for pid, handle in tuple(self.games.items()):
            if self.kernel.WaitForSingleObject(handle, 0) != 258:  # WAIT_TIMEOUT means still running.
                self.kernel.CloseHandle(self.games.pop(pid))
                if self.foreground_pid == pid:
                    self.foreground_pid = 0

        pid = wintypes.DWORD()
        window = self.user.GetForegroundWindow()
        if window:
            self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        state = ctypes.c_int()
        exclusive = self.shell.SHQueryUserNotificationState(ctypes.byref(state)) == 0 and state.value == 3
        current = pid.value
        if current and current != os.getpid() and current not in self.games:
            if exclusive or current != self.foreground_pid:
                self.track(current, exclusive)
        self.foreground_pid = current
        return bool(self.games) or exclusive

    def track(self, pid, exclusive):
        # A process handle stays tied to this game even if Windows later reuses its PID.
        handle = self.kernel.OpenProcess(0x00101000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not handle:
            return
        image = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image))
        if exclusive or (self.kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length))
                         and game_image(image.value)):
            self.games[pid] = handle
        else:
            self.kernel.CloseHandle(handle)

    def clear(self):
        for handle in self.games.values():
            self.kernel.CloseHandle(handle)
        self.games.clear()
        self.foreground_pid = 0
