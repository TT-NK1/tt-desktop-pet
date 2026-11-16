"""All TT desktop entry points share one per-user process lock."""

from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths
from PySide6.QtWidgets import QApplication


def acquire_lock(path):
    lock = QLockFile(str(path))
    # A long-running TT must never lose its lock merely because of its age.
    # Qt can still recover a stale lock whose owning process has exited.
    lock.setStaleLockTime(0)
    if lock.tryLock(0):
        return lock
    if lock.error() == QLockFile.LockError.LockFailedError:
        return None
    raise OSError("无法创建 TT 单实例锁，请检查用户配置目录的写入权限。")


def create_application(argv):
    folder = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericConfigLocation
    )) / "TT-desktop-pet"
    folder.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(folder / "instance.lock")
    if lock is None:
        return None
    # Reject duplicate launches before Qt initializes the desktop UI.
    try:
        app = QApplication(argv)
    except BaseException:
        lock.unlock()
        raise
    # Keep the lock alive for the entire application lifetime, including startup.
    app._tt_instance_lock = lock
    return app
