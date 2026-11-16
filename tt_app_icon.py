"""Reuse the Windows desktop shortcut artwork for TT's windows."""

import sys
import json
import subprocess
from pathlib import Path

from PySide6.QtCore import QFileInfo, QStandardPaths
from PySide6.QtWidgets import QFileIconProvider
from PySide6.QtGui import QIcon, QImage, QPixmap


def shortcut_artwork(shortcut):
    """Read the shortcut's explicit IconLocation, without shell overlays."""
    if sys.platform != "win32":
        return QIcon()
    # Pass the path as data, never interpolate it into PowerShell source.
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$path = [Console]::In.ReadToEnd()
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
[Console]::Out.Write((ConvertTo-Json -Compress -InputObject $link.IconLocation))
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            input=str(shortcut), capture_output=True, text=True,
            encoding="utf-8", timeout=10, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        location = json.loads(result.stdout)
        if not location:
            return QIcon()
        filename, separator, index = location.rpartition(",")
        if not separator:
            filename, index = location, "0"
        import os
        filename = os.path.expandvars(filename.strip().strip('"'))
        if Path(filename).suffix.lower() not in (".exe", ".dll", ".icl"):
            return QIcon(filename)
        # IconLocation can reference an indexed executable/DLL resource.
        import ctypes
        from ctypes import wintypes
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        shell.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                        ctypes.POINTER(wintypes.HICON),
                                        ctypes.POINTER(wintypes.HICON), wintypes.UINT]
        shell.ExtractIconExW.restype = wintypes.UINT
        user.DestroyIcon.argtypes = [wintypes.HICON]
        large, small = wintypes.HICON(), wintypes.HICON()
        icon = QIcon()
        try:
            shell.ExtractIconExW(filename, int(index), ctypes.byref(large),
                                 ctypes.byref(small), 1)
            for handle in (large, small):
                if handle.value:
                    icon.addPixmap(QPixmap.fromImage(QImage.fromHICON(handle.value)))
            return icon
        finally:
            for handle in (large, small):
                if handle.value:
                    user.DestroyIcon(handle)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return QIcon()


def desktop_shortcuts():
    """Include redirected user desktops and the shared Windows desktop."""
    folders = list(QStandardPaths.standardLocations(QStandardPaths.DesktopLocation))
    if sys.platform == "win32":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        # CSIDL_COMMON_DESKTOPDIRECTORY, resolved by Windows rather than guessed.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x19, None, 0, buffer) == 0:
            folders.append(buffer.value)
    for folder in dict.fromkeys(folders):
        try:
            for path in sorted(Path(folder).iterdir()):
                if path.suffix.lower() == ".lnk" and path.stem.casefold() == "tt":
                    yield path
        except OSError:
            continue


def prepare_taskbar():
    """Keep TT's windows out of the generic Python taskbar group."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TT.DesktopPet")


def apply_desktop_icon(app):
    provider = QFileIconProvider()
    for shortcut in desktop_shortcuts():
        icon = shortcut_artwork(shortcut)
        if not icon.isNull():
            app.setWindowIcon(icon)
            return True
        info = QFileInfo(str(shortcut))
        # Qt resolves Windows .lnk files through symLinkTarget(). Reading the
        # target avoids the shortcut-arrow overlay supplied by the shell.
        target = info.symLinkTarget()
        if target:
            info = QFileInfo(target)
        icon = provider.icon(info)
        if not icon.isNull():
            app.setWindowIcon(icon)
            return True
    return False
