"""Independent Cubism upper-body camera driven by the desktop's final frame state."""
import json
import math
import time

from PySide6.QtCore import QEvent, Qt, QTimer, QUrlQuery, Signal
from PySide6.QtGui import QColor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QWidget, QLabel, QToolButton, QMenu, QDialog,
                              QFormLayout, QSpinBox, QDialogButtonBox)

from tt_character_state import CharacterStateManager
from tt_indextts_resources import ResourceManager


class ChatPortraitViewport(QWidget):
    metrics_changed = Signal()
    feedback_text = Signal(str)

    def __init__(self, room):
        super().__init__(room)
        self.room = room
        self.state_manager = None
        self.view = None
        self.url = ''
        self.model_key = None
        self.model_loads = 0
        self.channel = ''
        self.metrics = {}
        self.metrics_pending = False
        self.closed = False
        self.effects = True
        self.speaking = False
        self.was_speaking = False
        self.speech_closed_at = 0.0
        self.bubble_until = 0.0
        self.last_mode = 'idle'
        self.dashboard_feedback = False
        self.preferred_size = (250, 220)
        self.frame_limit = None
        self.setFixedSize(250, 220)
        self.setAccessibleName('TT 实时 Live2D 人物')
        self.notice = QLabel('正在连接 TT…', self)
        self.notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice.setWordWrap(True)
        self.bubble = QLabel('', self)
        self.bubble.setStyleSheet('background:rgba(255,250,255,210); color:#715580; border-radius:7px; padding:3px 7px; font-size:11px')
        self.bubble.hide()
        self.voice = QLabel('', self)
        self.voice.setStyleSheet('color:#796087; background:rgba(255,250,255,205); border-radius:6px; font-size:10px')
        self.voice.hide()
        self.fx = QLabel('', self)
        self.fx.setStyleSheet('color:rgba(183,122,166,150); background:transparent; font-size:17px')
        self.fx.hide()
        for label in (self.bubble, self.voice, self.fx):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.options = QToolButton(self)
        self.options.setText('⋯')
        self.options.setToolTip('人物窗口设置')
        self.options.setAccessibleName('人物窗口设置')
        self.options.setFixedSize(26, 22)
        self.options.clicked.connect(self.open_options)
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self.update_feedback)
        self.poll = QTimer(self, interval=700)
        self.poll.timeout.connect(self.poll_metrics)
        self.release_timer = QTimer(self, singleShot=True)
        self.release_timer.timeout.connect(self.release_hidden_view)
        room.installEventFilter(self)

    def bind(self, pet):
        if self.state_manager:
            return
        self.state_manager = CharacterStateManager(pet, self)
        self.effects = bool(pet.manager.get_setting('chat_portrait_effects', True))
        size = pet.manager.get_setting('chat_portrait_size', [250, 220])
        if isinstance(size, list) and len(size) == 2:
            self.set_portrait_size(*size, save=False)
        self.state_manager.source_ready()

    def set_outfit(self, _outfit):
        if self.state_manager:
            self.state_manager.source_ready()

    def presentation_visible(self):
        return not self.closed and self.isVisible() and not self.window().isMinimized()

    def load_source(self, url, profile):
        if self.closed or self.url == url.toString():
            return
        query = QUrlQuery(url)
        self.channel = query.queryItemValue('stateChannel')
        model_key = (url.host(), url.port(), query.queryItemValue('model'))
        if self.view is not None and model_key == self.model_key:
            # All five outfits share the same MOC3/head. Rebind state, retain GPU resources.
            self.url = url.toString()
            self.metrics = {}
            self.notice.setText('正在同步 TT…')
            self.notice.show()
            channel = self.channel
            self.view.page().runJavaScript('window.sdkProbe && sdkProbe.setCharacterSubscriber(' + json.dumps(channel) + ')')
            return
        if self.view is None:
            self.view = QWebEngineView(self)
            page = QWebEnginePage(profile, self.view)
            page.setBackgroundColor(QColor(0, 0, 0, 0))
            self.view.setPage(page)
            self.view.setStyleSheet('background:transparent')
            self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.view.loadFinished.connect(self.loaded)
            self.view.renderProcessTerminated.connect(self.render_failed)
            self.view.show()
        else:
            self.view.page().runJavaScript('window.sdkProbe && sdkProbe.dispose()')
        self.url = url.toString()
        self.model_key = model_key
        self.model_loads += 1
        self.metrics = {}
        self.notice.setText('正在同步 TT…')
        self.notice.show()
        self.view.load(url)
        self.arrange()

    def loaded(self, ok):
        if not ok:
            self.notice.setText('人物连接中断，可从 ⋯ 重新连接')
            self.notice.show()
        elif self.presentation_visible():
            self.view.page().runJavaScript('window.sdkProbe && sdkProbe.setCharacterSubscriber(' + json.dumps(self.channel) + ')')
            self.resume()

    def render_failed(self, *_):
        self.notice.setText('人物渲染已停止，可从 ⋯ 重新连接')
        self.notice.show()
        self.poll.stop()

    def suspend(self):
        self.timer.stop()
        self.poll.stop()
        if self.view:
            self.view.page().runJavaScript('window.sdkProbe && sdkProbe.setFps(0)')
            self.view.page().setVisible(False)
            if not self.closed and not self.presentation_visible() and not self.release_timer.isActive():
                delay = 5_000 if ResourceManager.memory_headroom() < 2560 else 60_000
                self.release_timer.start(delay)

    def release_hidden_view(self):
        if not self.presentation_visible():
            self.release_view()

    def resume(self):
        if not self.presentation_visible():
            return
        self.release_timer.stop()
        self.timer.start()
        self.poll.start()
        if self.view:
            self.view.page().setVisible(True)
            self.view.page().runJavaScript('window.sdkProbe && sdkProbe.setFps(60)')

    def showEvent(self, event):
        super().showEvent(event)
        if self.state_manager:
            self.state_manager.refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.state_manager:
            self.state_manager.refresh()
        self.suspend()

    def eventFilter(self, watched, event):
        if watched is self.room and event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(0, lambda: self.state_manager.refresh() if self.state_manager else None)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()

    def arrange(self):
        # The render camera keeps a fixed aspect; arbitrary frame sizes letterbox.
        width = min(self.width(), round(self.height() * 1.25))
        height = round(width / 1.25)
        if self.view:
            self.view.setGeometry((self.width()-width)//2, (self.height()-height)//2, width, height)
        self.notice.setGeometry(self.rect())
        self.options.move(self.width()-28, self.height()-24)
        self.voice.setGeometry(7, self.height()-24, 105, 20)
        self.bubble.adjustSize()
        self.bubble.move(4, 6)
        self.fx.setGeometry(self.width()-45, 25, 40, 32)
        for widget in (self.notice, self.bubble, self.voice, self.fx, self.options):
            widget.raise_()

    def poll_metrics(self):
        if not self.view or self.metrics_pending or not self.presentation_visible():
            return
        self.metrics_pending = True
        url = self.url
        view = self.view
        def received(raw):
            if view is not self.view:
                return
            self.metrics_pending = False
            if self.closed or url != self.url:
                return
            try:
                self.metrics = json.loads(raw or '{}') or {}
            except (ValueError, TypeError):
                self.metrics = {}
            if self.metrics.get('applied', 0) > 0:
                self.notice.hide()
            self.metrics_changed.emit()
        view.page().runJavaScript('JSON.stringify(window.sdkProbe && sdkProbe.characterState())', received)

    def speech_mouth(self):
        voice = getattr(self.room, 'pro_voice', None)
        player = getattr(getattr(voice, 'player', None), 'player', None)
        self.speaking = bool(player and player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        if self.speaking:
            self.was_speaking = True
            seconds = player.position()/1000
            return .07+.62*max(0, math.sin(math.tau*4*seconds))**1.5*(.72+.28*math.sin(math.tau*1.23*seconds))
        if self.was_speaking:
            self.was_speaking = False
            self.speech_closed_at = time.monotonic()
        if time.monotonic()-self.speech_closed_at < .35:
            return 0.0
        return None

    def update_feedback(self):
        now = time.monotonic()
        self.speech_mouth()
        mode = getattr(self, 'mode', 'idle')
        if mode != self.last_mode:
            self.last_mode = mode
            message = {'thinking':'稍等一下', 'search':'我找找看', 'happy':'嗯 ♪',
                       'shy':'嗯…', 'comfort':'我在听哦', 'idle':'我在这里'}.get(mode, '嗯？')
            self.bubble.setText(message)
            self.bubble_until = now+2.8
            self.arrange()
            if self.dashboard_feedback and mode != 'idle':
                self.feedback_text.emit(message)
        self.bubble.setVisible(not self.dashboard_feedback and self.effects and now < self.bubble_until)
        self.fx.setText({'happy':'✧', 'shy':'〃', 'angry':'⌁'}.get(mode, ''))
        self.fx.setVisible(self.effects and bool(self.fx.text()) and now < self.bubble_until)
        bars = ('▂▅▃▆', '▅▂▆▃', '▃▆▂▅')[int(now*8)%3]
        self.voice.setText(f' {bars}  正在说话')
        self.voice.setVisible(self.speaking and not self.dashboard_feedback)

    def set_portrait_size(self, width, height, save=True):
        self.preferred_size = (max(190, min(440, int(width))), max(150, min(340, int(height))))
        self.apply_frame_size()
        if save and self.state_manager:
            self.state_manager.pet.manager.save_setting('chat_portrait_size', list(self.preferred_size))

    def limit_frame(self, width, height):
        self.frame_limit = (width, height)
        self.apply_frame_size()

    def apply_frame_size(self):
        width, height = self.preferred_size
        if self.frame_limit:
            if self.dashboard_feedback:
                width, height = map(min, zip((round(width*1.18), round(height*1.18)), self.frame_limit))
            else:
                width, height = map(min, zip(self.preferred_size, self.frame_limit))
        self.setFixedSize(width, height)

    def edit_size(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('人物窗口大小')
        layout = QFormLayout(dialog)
        width, height = QSpinBox(), QSpinBox()
        width.setRange(190, 440); width.setValue(self.preferred_size[0]); width.setSuffix(' px')
        height.setRange(150, 340); height.setValue(self.preferred_size[1]); height.setSuffix(' px')
        layout.addRow('宽度', width); layout.addRow('高度', height)
        layout.addRow(QLabel('人物比例和上半身取景自动保持。'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_portrait_size(width.value(), height.value())

    def reconnect(self):
        self.url = ''
        self.model_key = None
        if self.state_manager:
            self.state_manager.refresh()

    def open_options(self):
        menu = QMenu(self)
        menu.addAction('调整人物窗口大小…', self.edit_size)
        action = menu.addAction('显示状态气泡与轻量情绪特效')
        action.setCheckable(True); action.setChecked(self.effects)
        def toggle(enabled):
            self.effects = enabled
            if self.state_manager:
                self.state_manager.pet.manager.save_setting('chat_portrait_effects', enabled)
            self.update_feedback()
        action.toggled.connect(toggle)
        menu.addAction('重新连接人物', self.reconnect)
        menu.exec(self.options.mapToGlobal(self.options.rect().bottomLeft()))

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.suspend()
        if self.state_manager:
            self.state_manager.close()
        self.release_view()

    def release_view(self):
        self.release_timer.stop()
        view, self.view = self.view, None
        self.url = ''
        self.model_key = None
        self.metrics = {}
        self.metrics_pending = False
        if view:
            import shiboken6
            view.loadFinished.disconnect(self.loaded)
            view.renderProcessTerminated.disconnect(self.render_failed)
            view.hide()
            page = view.page()
            view.setPage(None)
            shiboken6.delete(page)
            view.deleteLater()
