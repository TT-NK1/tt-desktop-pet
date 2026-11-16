"""Run network work without making application exit wait for a socket read."""

from threading import Event, Thread

from PySide6.QtCore import QObject, Signal
from shiboken6 import isValid


class BackgroundTask(QObject):
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancelled = Event()

    def start(self):
        Thread(target=self._work, name=type(self).__name__, daemon=True).start()

    def _work(self):
        try:
            if not self.cancelled.is_set():
                self.run()
        finally:
            self._emit("finished")

    def publish(self, name, *args):
        if not self.cancelled.is_set():
            self._emit(name, *args)

    def _emit(self, name, *args):
        if isValid(self):
            try:
                getattr(self, name).emit(*args)
            except RuntimeError:
                if isValid(self):
                    raise
