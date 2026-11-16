"""Desktop reminder, existing AI request and existing IndexTTS queue integration."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout

from tt_tarot import TarotManager, build_tarot_detail_tts_text, build_tarot_summary_tts_text
from tt_tarot_ui import TarotDialog, TarotSettings, ThemedTarotDialog, button, label


def windows_busy():
    """Leave the user alone after recent input and during foreground full-screen use."""
    if sys.platform != "win32":
        active = QApplication.activeWindow()
        return bool(active and active.isFullScreen())

    class LastInput(ctypes.Structure):
        _fields_ = [("size", wintypes.UINT), ("tick", wintypes.DWORD)]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("screen", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user = ctypes.windll.user32
    kernel = ctypes.windll.kernel32
    kernel.GetTickCount.restype = wintypes.DWORD
    event = LastInput(ctypes.sizeof(LastInput))
    if user.GetLastInputInfo(ctypes.byref(event)) and (kernel.GetTickCount() - event.tick) % 2**32 < 12_000:
        return True
    for function in (user.GetForegroundWindow, user.GetShellWindow, user.GetDesktopWindow,
                     user.MonitorFromWindow):
        function.restype = wintypes.HWND
    foreground = user.GetForegroundWindow()
    if not foreground or foreground in (user.GetShellWindow(), user.GetDesktopWindow()):
        return False
    rect = wintypes.RECT()
    display = MonitorInfo(ctypes.sizeof(MonitorInfo))
    monitor = user.MonitorFromWindow(wintypes.HWND(foreground), 2)
    if not monitor or not (user.GetMonitorInfoW(wintypes.HWND(monitor), ctypes.byref(display))
                           and user.GetWindowRect(wintypes.HWND(foreground), ctypes.byref(rect))):
        return False
    screen = display.screen
    return (rect.left <= screen.left and rect.top <= screen.top
            and rect.right >= screen.right and rect.bottom >= screen.bottom)


class TarotCompanion(QObject):
    ai_result = Signal(object, str, bool)
    voice_changed = Signal()

    def __init__(self, pet, *, manager=None, worker_factory=None, busy=windows_busy):
        super().__init__(pet)
        self.pet = pet
        self.chat = pet.chat
        self.theme_manager = getattr(self.chat, "theme", None)
        self.manager = manager if manager is not None else TarotManager(pet.root, pet.manager.db)
        self.worker_factory, self.system_busy = worker_factory, busy
        self.window = None
        self.reminder = None
        self.reminder_day = None
        self.ai_pending = None
        self.voice_owner = None
        self.current_tts_mode = None
        self.closed = False
        self.current_day = None
        self.ai_result.connect(self.finish_ai, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self, interval=30_000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.voice_timer = QTimer(self, interval=180)
        self.voice_timer.timeout.connect(self.sync_voice_state)
        QTimer.singleShot(3500, self, self.tick)

    def create_settings(self, parent):
        return TarotSettings(self, parent)

    def open(self, *_):
        self.dismiss_reminder()
        self.ensure_window()
        self.window.show_today()
        self.show_window()

    def ensure_window(self):
        if self.window is None:
            self.window = TarotDialog(self, self.pet)

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def open_history(self):
        self.dismiss_reminder()
        self.ensure_window()
        self.window.reset_history()
        self.window.tabs.setCurrentIndex(1)
        self.show_window()

    def clear_history(self):
        self.manager.clear_history()
        if self.window:
            if self.window.draw and not self.manager.find(self.window.draw.day):
                self.window.draw = None
                self.window.refresh()
            self.window.reset_history()

    def settings_changed(self):
        if not self.manager.setting("enabled") or not self.manager.setting("reminders"):
            self.dismiss_reminder()
        if self.window and self.window.follow_today and self.window.tabs.currentIndex() == 0:
            self.window.show_today()
        self.tick()

    def blocked(self):
        bubble = getattr(self.pet, "quick_bar", None)
        active_bubble = (bubble and bubble.isVisible() and
                         getattr(bubble, "_reply_until", 0) > time.monotonic())
        return (
            self.pet.weather_busy() or bool(active_bubble)
            or self.pet.drag_start is not None or QApplication.activeModalWidget() is not None
            or QApplication.activePopupWidget() is not None
            or bool(self.window and self.window.isVisible()) or self.system_busy()
        )

    def tick(self):
        if self.closed:
            return
        now = self.manager.now()
        day = now.date().isoformat()
        if day != self.current_day:
            self.current_day = day
            self.dismiss_reminder()
            self.manager.today(now)
            if self.window and self.window.isVisible() and self.window.follow_today and self.window.tabs.currentIndex() == 0:
                self.window.show_today()
        if not self.manager.setting("enabled"):
            return
        draw = self.manager.find(day) or self.manager.today(now)
        if self.manager.reminder_due(draw, now) and not self.blocked():
            self.manager.remember_notification(day)
            self.show_reminder(day)
            if self.manager.setting("voice"):
                self.speak(draw, automatic=True)

    def show_reminder(self, day):
        self.dismiss_reminder()
        self.reminder_day = day
        self.reminder = ThemedTarotDialog(
            self.pet, self.theme_manager, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        prompt = self.reminder
        prompt.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        root = QVBoxLayout(prompt)
        root.addWidget(label("今天的塔罗牌已经准备好啦，要看看吗？"))
        row = QHBoxLayout()
        button("看看今天的牌", self.open, row)
        button("稍后提醒", self.snooze, row)
        button("今天不再提醒", self.dismiss_reminder, row)
        root.addLayout(row)
        prompt.adjustSize()
        area = self.pet.screen().availableGeometry()
        position = self.pet.frameGeometry().topLeft()
        prompt.move(max(area.left(), min(position.x(), area.right() - prompt.width() + 1)),
                    max(area.top(), position.y() - prompt.height() - 10))
        prompt.show()
        QTimer.singleShot(25_000, prompt, lambda: self.dismiss_reminder() if self.reminder is prompt else None)

    def snooze(self):
        if self.reminder_day:
            self.manager.remember_notification(self.reminder_day, self.manager.now().timestamp() + 30 * 60)
        self.dismiss_reminder()

    def dismiss_reminder(self):
        prompt, self.reminder = self.reminder, None
        self.reminder_day = None
        if prompt:
            prompt.close()
            prompt.deleteLater()

    def request_ai(self, draw):
        if not draw or self.manager.reading(draw.day, draw.theme):
            return ""
        if self.ai_pending or self.chat.busy():
            return "TT 正在处理一个请求，请稍后再试。"
        model = self.chat.models.currentData()
        if not self.chat.consent.isChecked() or not model or not self.chat.identity:
            return "当前显示固定牌义。使用 AI 解读前，请在聊天设置中完成授权并选择模型。"
        if self.worker_factory is None:
            from desktop_pet_chat import RequestWorker
            worker_factory = RequestWorker
        else:
            worker_factory = self.worker_factory
        job = (draw.day, draw.theme)
        worker = worker_factory("chat", self.chat.identity, self.manager.ai_payload(draw, draw.theme, model))
        self.ai_pending = (job, worker)
        worker.completed.connect(lambda content: self.ai_result.emit(job, content, True))
        worker.failed.connect(lambda _message: self.ai_result.emit(job, "", False))
        # Reuse the worker's request logic without owning another native QThread on shutdown.
        threading.Thread(target=worker.run, daemon=True, name="tt-tarot-reading").start()
        return "正在生成，先为你保留固定牌义。"

    def finish_ai(self, job, answer, ok):
        if self.closed or not self.ai_pending or job != self.ai_pending[0]:
            return
        self.ai_pending = None
        if ok and answer.strip():
            self.manager.save_reading(*job, answer.strip())
        if self.window:
            draw = self.window.draw
            if draw and (draw.day, draw.theme) == job:
                self.window.status.setText("" if ok and answer.strip() else "AI 解读暂时不可用，已保留固定牌义。")
                if self.window.tabs.currentIndex() == 1:
                    self.window.refresh_tab(1)
            self.window.refresh()

    def sync_voice_state(self):
        owner = self.voice_owner
        if owner is not None and not self.chat.pro_voice.player.has_audio((owner,)):
            self.set_voice_state(None, None)

    def set_voice_state(self, owner, mode):
        self.voice_owner, self.current_tts_mode = owner, mode
        if owner is None:
            self.voice_timer.stop()
        else:
            self.voice_timer.start()
        self.voice_changed.emit()

    def speak(self, draw, automatic=False, *, mode="summary"):
        if not draw:
            return ""
        from tt_voice_profiles import with_reference
        voice = self.chat.pro_voice
        self.sync_voice_state()
        switching = self.voice_owner is not None and not automatic
        if not switching and (self.chat.busy() or voice.active):
            return "TT 正在回复或朗读，请稍后。"
        if switching and self.current_tts_mode == mode:
            return "这段内容正在朗读。"
        cfg = with_reference(self.chat.db, self.chat.pro_settings)
        if not cfg["speech_enabled"] or cfg["muted"] or not cfg["volume"]:
            return "请先打开 TT 语音并取消静音。"
        if not cfg["voice_reference"] or not Path(cfg["voice_reference"]).is_file():
            return "请在 TT 语音设置中选择有效的本地音色。"
        if mode == "detail":
            text = build_tarot_detail_tts_text(self.manager.reading(draw.day, draw.theme))
            if not text:
                return "请先生成当前主题的详细解读。"
        else:
            text = build_tarot_summary_tts_text(
                self.manager.cards[draw.card_id], draw,
                today=draw.day == self.manager.now().date().isoformat(),
            )
        if switching:
            voice.player.stop_owner(self.voice_owner)
        owner = voice.play(text, dict(cfg, voice=True), kind="tarot",
                           message_id=("tarot", draw.day) if automatic else None)
        if owner and voice.player.has_audio((owner,)):
            self.set_voice_state(owner, mode)
            return "已加入 TT 语音队列。"
        self.set_voice_state(None, None)
        return voice.status

    def stop_voice(self):
        owner = self.voice_owner
        if owner is None:
            return "当前没有塔罗朗读。"
        stopped = self.chat.pro_voice.player.stop_owner(owner)
        self.set_voice_state(None, None)
        return "已停止塔罗朗读。" if stopped else "当前没有塔罗朗读。"

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.voice_timer.stop()
        self.dismiss_reminder()
        if self.ai_pending:
            self.ai_pending[1].cancelled.set()
        if self.window:
            self.window.close()
