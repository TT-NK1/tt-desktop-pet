"""Floating outfit-bound controls for local tracks and connected music clients."""
from base64 import b64decode
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSignalBlocker, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QProxyStyle, QSlider, QStyle, QVBoxLayout, QWidget

from tt_bubble import tail_direction
from tt_bubble_art import outline, paint_glass, paint_motif, palette_for, round_pen, tint
from tt_apple_music import apple_host
from tt_music_services import NETEASE_LOCAL_SOURCE, PLATFORMS, WAITING_CLIENTS
from tt_music_progress import PlaybackClock
from tt_apple_audio import AppleAudioVolume, WindowsAppVolume, netease_process
from tt_netease_now_playing import cover_url as netease_cover_url
import time


def icon(name, color, size=20):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    with QPainter(pixmap) as p:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 24, size / 24)
        p.setPen(round_pen(QColor(color), 1.65))
        if name in ('play', 'previous', 'next', 'pause'):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            if name == 'pause':
                for x in (6.5, 13.4):
                    p.drawRoundedRect(QRectF(x, 4.7, 4, 14.6), 1.4, 1.4)
            else:
                if name == 'previous':
                    p.translate(24, 0)
                    p.scale(-1, 1)
                points = [(7, 4.5), (20 if name == 'play' else 17, 12), (7, 19.5)]
                p.drawPolygon(QPolygonF([QPointF(*point) for point in points]))
                if name != 'play':
                    p.drawRoundedRect(QRectF(17, 4.6, 2.6, 14.8), 1, 1)
        elif name == 'volume':
            p.drawPolyline(QPolygonF([QPointF(*point) for point in ((4, 9), (7, 9), (11, 5),
                                                                  (11, 19), (7, 15), (4, 15), (4, 9))]))
            p.drawArc(QRectF(9, 7, 10, 10), -56 * 16, 112 * 16)
            p.drawArc(QRectF(5, 3, 18, 18), -48 * 16, 96 * 16)
        elif name == 'close':
            p.drawLine(7, 7, 17, 17)
            p.drawLine(17, 7, 7, 17)
        elif name == 'folder':
            folder = QPainterPath(QPointF(3, 7))
            folder.lineTo(10, 7)
            folder.lineTo(12, 9)
            folder.lineTo(21, 9)
            folder.lineTo(19, 18)
            folder.lineTo(4, 18)
            folder.closeSubpath()
            p.drawPath(folder)
        elif name == 'search':
            p.drawEllipse(QRectF(4, 4, 10, 10))
            p.drawLine(13, 13, 20, 20)
    return QIcon(pixmap)


def time_text(ms):
    minutes, seconds = divmod(max(0, ms) // 1000, 60)
    return f'{minutes:02d}:{seconds:02d}'


class MusicSliderStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Slider_AbsoluteSetButtons:
            return Qt.MouseButton.LeftButton.value
        return super().styleHint(hint, option, widget, returnData)


class ProgressSlider(QSlider):
    """Commit a mouse seek only when the user releases the slider."""
    committed = Signal(int)
    gesture_active = False

    def keyPressEvent(self,event):
        old = self.value()
        super().keyPressEvent(event)
        if self.value()!=old:
            self.committed.emit(self.value())

    def wheelEvent(self,event):
        old = self.value()
        super().wheelEvent(event)
        if self.value()!=old:
            self.committed.emit(self.value())

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.gesture_active = True
            self.setSliderDown(True)
            with QSignalBlocker(self):
                super().mousePressEvent(event)
            self.sliderMoved.emit(self.sliderPosition())
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            target = self.sliderPosition()
            with QSignalBlocker(self):
                super().mouseReleaseEvent(event)
                self.setSliderDown(False)
                self.setValue(target)
            self.gesture_active = False
            self.committed.emit(target)
        else:
            super().mouseReleaseEvent(event)


class Cover(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(57, 57)
        self.setAccessibleName('专辑封面')
        self.image = QImage()

    def paintEvent(self, event):
        theme = self.parentWidget().theme
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            mask = QPainterPath()
            mask.addRoundedRect(rect, 13, 13)
            p.setClipPath(mask)
            if self.image.isNull():
                fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
                fill.setColorAt(0, QColor(theme.bottom if theme.dark else theme.accent))
                fill.setColorAt(.67, QColor(theme.top if theme.dark else theme.bottom))
                fill.setColorAt(1, QColor(theme.accent if theme.dark else theme.rim))
                p.fillRect(rect, fill)
                paint_motif(p, rect.center(), 29, theme)
            else:
                edge = min(self.image.width(), self.image.height())
                source = QRectF((self.image.width() - edge) / 2, (self.image.height() - edge) / 2, edge, edge)
                p.drawImage(rect, self.image, source)
            p.setClipping(False)
            p.setPen(QPen(tint(theme.rim, 192), 1.1))
            p.drawPath(mask)


class MusicBubble(QWidget):
    def __init__(self, pet):
        super().__init__(pet, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.pet = pet
        self.theme = palette_for(pet)
        self.services = pet.music_services
        self.services.pause_local = lambda: self.player.pause() if self.player.isPlaying() else None
        self.remote_art = None
        self.remote_cover_key = None
        self.art_reply = None
        self.last_art_attempt = float('-inf')
        self.remote_image = QImage()
        self.net = None
        self.setWindowTitle('TT 媒体')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(342, 253)
        self.tail_target = QPointF(0, 90)
        self.files = []
        self.file_dialog = None
        self.index = -1
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.70)
        self.player.setAudioOutput(self.audio)

        content = QVBoxLayout(self)
        content.setContentsMargins(41, 35, 41, 29)
        content.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.cover = Cover(self)
        top.addWidget(self.cover)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title = QLabel(self)
        self.title.setObjectName('song')
        self.title.setFixedWidth(145)
        self.title.setFixedHeight(22)
        self.artist = QLabel(self)
        self.artist.setObjectName('artist')
        self.artist.setFixedWidth(145)
        self.artist.setFixedHeight(16)
        text.addStretch()
        text.addWidget(self.title)
        text.addWidget(self.artist)
        text.addStretch()
        top.addLayout(text, 1)
        self.close_button = self.button('close', '关闭媒体气泡', self.hide, 26)
        shortcuts = QVBoxLayout()
        shortcuts.setSpacing(4)
        shortcuts.addWidget(self.close_button)
        bilibili = QPushButton('B站', self)
        bilibili.setObjectName('bilibili')
        bilibili.setToolTip('切换到 B 站视频')
        bilibili.setCursor(Qt.CursorShape.PointingHandCursor)
        bilibili.setFixedSize(26, 23)
        bilibili.clicked.connect(self.open_bilibili)
        shortcuts.addWidget(bilibili)
        top.addLayout(shortcuts)
        content.addLayout(top)
        content.addSpacing(7)

        self.progress = self.slider('播放进度', 0, ProgressSlider)
        self.progress.setTracking(False)
        self.progress.committed.connect(self.seek)
        self.progress.sliderMoved.connect(self.preview_seek)
        self.progress_clock = PlaybackClock()
        self.timeline_poll_at = 0.0
        self.timeline_anchor = (0,0)
        self.snapshot = None
        self.seek_wait = None
        self.pause_wait = 0.0
        self.volume_request = None
        self.volume_drag_source = None
        self.progress_timer = QTimer(self,interval=125,timerType=Qt.TimerType.PreciseTimer)
        self.progress_timer.timeout.connect(self.tick_progress)
        if hasattr(self.services.bridge,'timeline_received'):
            self.services.bridge.timeline_received.connect(self.timeline_sample)
        self.services.seek_result.connect(self.finish_seek_request)
        content.addWidget(self.progress)
        times = QHBoxLayout()
        self.elapsed = QLabel('00:00', self)
        self.duration = QLabel('00:00', self)
        for label in (self.elapsed, self.duration):
            label.setObjectName('time')
        times.addWidget(self.elapsed)
        times.addStretch()
        times.addWidget(self.duration)
        content.addLayout(times)
        content.addSpacing(3)

        transport = QHBoxLayout()
        transport.setSpacing(10)
        self.open_button = self.button('folder', '播放列表', self.open_queue, 28)
        queue_size = self.open_button.sizePolicy()
        queue_size.setRetainSizeWhenHidden(True)
        self.open_button.setSizePolicy(queue_size)
        transport.addWidget(self.open_button)
        transport.addStretch()
        self.previous = self.button('previous', '上一首', lambda: self.step(-1), 32)
        self.play = self.button('play', '播放', self.toggle_playback, 43)
        self.next = self.button('next', '下一首', lambda: self.step(1), 32)
        for button in (self.previous, self.play, self.next):
            transport.addWidget(button)
        transport.addStretch()
        self.catalog = self.button('search', '音乐来源', self.open_sources, 28)
        transport.addWidget(self.catalog)
        content.addLayout(transport)

        volume_row = QHBoxLayout()
        volume_row.setSpacing(9)
        self.volume_icon = QLabel(self)
        volume_row.addWidget(self.volume_icon)
        self.volume = self.slider('音量', 100)
        self.volume.setValue(70)
        self.remote_volume_timer = QTimer(self, singleShot=True, interval=170)
        self.remote_volume_timer.timeout.connect(self.set_remote_volume)
        self.volume.valueChanged.connect(self.volume_changed)
        self.volume.sliderPressed.connect(self.begin_volume_drag)
        self.volume.sliderReleased.connect(self.end_volume_drag)
        self.native_volume = AppleAudioVolume(self)
        self.native_volumes = {'apple': self.native_volume, 'netease': WindowsAppVolume(
            self, label=PLATFORMS['netease'].name, matches_process=netease_process, allow_inactive=True)}
        for controller in self.native_volumes.values():
            controller.changed.connect(self.update_volume)
            controller.failed.connect(self.service_feedback)
        self.native_volume_timer = QTimer(self,singleShot=True,interval=70)
        self.native_volume_timer.timeout.connect(self.send_native_volume)
        self.volume_value = QLabel('70%', self)
        self.volume_value.setObjectName('time')
        self.volume_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.volume_value.setFixedWidth(34)
        self.volume.valueChanged.connect(lambda value: self.volume_value.setText(f'{value}%'))
        volume_row.addWidget(self.volume, 1)
        volume_row.addWidget(self.volume_value)
        content.addLayout(volume_row)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.artist.setTextFormat(Qt.TextFormat.PlainText)

        self.player.metaDataChanged.connect(self.update_metadata)
        self.player.positionChanged.connect(self.update_local_position)
        self.player.durationChanged.connect(self.update_progress)
        self.player.seekableChanged.connect(self.update_progress)
        self.player.playbackStateChanged.connect(self.update_transport)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.errorOccurred.connect(self.media_error)
        self.services.changed.connect(self.source_changed)
        self.services.feedback.connect(self.service_feedback)
        self.follow = QTimer(self, interval=100)
        self.follow.timeout.connect(self.follow_pet)
        if not hasattr(pet,'window_manager'):
            pet.installEventFilter(self)
        pet.renderer.ready.connect(self.sync_outfit)
        self.apply_palette()
        self.update_progress()
        self.source_changed()

    def open_sources(self):
        menu = QMenu(self)
        menu.addAction('B 站视频', self.open_bilibili)
        menu.addSeparator()
        menu.addAction('音乐平台设置', self.pet.open_music_settings)
        for key in WAITING_CLIENTS:
            action = menu.addAction(f'{PLATFORMS[key].name}（Windows 客户端）',
                                    lambda _checked=False, source=key: self.services.attach(source, activate=True))
            action.setCheckable(True)
            action.setChecked(self.services.active == key)
        local = menu.addAction('使用本地音频', lambda: self.services.switch_to('local'))
        local.setCheckable(True)
        local.setChecked(self.services.active == 'local')
        menu.exec(self.catalog.mapToGlobal(QPoint(0,self.catalog.height())))

    def open_bilibili(self):
        if self.pet.chat.media.open_bilibili(self.pet.open_music_bubble):
            self.hide()

    def open_queue(self):
        source = self.services.active
        if source=='musickit':
            self.services.apple.show(self.pet.chat)
        elif source=='local':
            menu = QMenu(self)
            menu.addAction('添加本地音频…', self.choose_files)
            if self.files:
                menu.addSeparator()
            for index,file in enumerate(self.files):
                action = menu.addAction(Path(file).name, lambda _checked=False, i=index: self.load(i, play=True))
                action.setCheckable(True)
                action.setChecked(index==self.index)
            menu.exec(self.open_button.mapToGlobal(QPoint(0,self.open_button.height())))

    def begin_volume_drag(self):
        self.volume_drag_source = self.services.active

    def end_volume_drag(self):
        source, self.volume_drag_source = self.volume_drag_source, None
        if source == self.services.active:
            self.queue_remote_volume()
        else:
            self.update_volume()

    def volume_changed(self, value):
        source = self.services.active
        if self.volume_drag_source is not None and self.volume_drag_source != source:
            return
        if source=='local':
            self.audio.setVolume(value / 100)
        elif source in self.native_volumes:
            if not self.native_volume_timer.isActive():
                self.native_volume_timer.start()
        elif not self.volume.isSliderDown():
            self.queue_remote_volume()

    def queue_remote_volume(self):
        if self.services.active=='musickit':
            self.remote_volume_timer.start()
        elif self.services.active in self.native_volumes:
            self.native_volume_timer.stop()
            self.send_native_volume()

    def send_native_volume(self):
        controller = self.native_volumes.get(self.services.active)
        if controller and controller.enabled and self.volume.isEnabled() and self.isVisible():
            value = self.volume.value()
            if controller.value is not None and value!=controller.value:
                self.volume_request = (value,time.monotonic()+.5)
                controller.set(value)

    def set_remote_volume(self):
        if self.services.active=='musickit':
            self.services.control('volume',position=self.volume.value())

    def service_feedback(self, message):
        self.setToolTip(message)

    def source_changed(self):
        source = self.services.active
        if self.services.bubble_source != source:
            self.remote_volume_timer.stop()
            self.native_volume_timer.stop()
            self.volume_request = None
        self.services.bubble_source = source
        self.update_source_icon()
        self.update_metadata()
        self.update_transport()
        self.update_progress()
        self.update_volume()
        self.open_button.setVisible(source in ('local','musickit'))

    def update_volume(self, *_):
        source = self.services.active
        for key, controller in self.native_volumes.items():
            if key != source:
                controller.stop()
        controller = self.native_volumes.get(source)
        if controller:
            running = source == 'netease' and self.services.inventory.get(source, {}).get('running')
            available = self.isVisible() and (self.services.state().online or running)
            if available:
                controller.start()
            else:
                controller.stop()
                self.native_volume_timer.stop()
            value = controller.value if available else None
            self.volume.setEnabled(value is not None)
            name = PLATFORMS[source].name
            self.volume.setToolTip(f'{name}应用音量（Windows 音量混合器）' if value is not None else
                                   f'等待{name}应用音频会话，请先播放一首歌')
            if value is not None and (self.volume.isSliderDown() or self.native_volume_timer.isActive()):
                return
            if value is not None and self.volume_request:
                expected, deadline = self.volume_request
                if value!=expected and time.monotonic()<deadline:
                    return
            self.volume_request = None
            self.display_volume(value)
            return
        self.native_volume_timer.stop()
        self.volume_request = None
        value = round(self.audio.volume()*100) if source=='local' else self.services.apple.volume if source=='musickit' else None
        enabled = source=='local' or (source=='musickit' and value is not None and 'volume' in self.services.state().capabilities)
        self.volume.setEnabled(enabled)
        self.volume.setToolTip('播放器音量' if enabled else
                               '此媒体会话不提供应用音量')
        if enabled and (self.volume.isSliderDown() or (source=='musickit' and
                (self.remote_volume_timer.isActive() or self.services.apple.volume_pending))):
            return
        self.display_volume(value)

    def display_volume(self, value):
        if value is not None:
            with QSignalBlocker(self.volume):
                self.volume.setValue(value)
        self.volume_value.setText(f'{value}%' if value is not None else '—')

    def update_source_icon(self):
        source = self.services.active
        brand = Path(__file__).with_name('tt_music_icons') / f'{source}.ico'
        self.catalog.setIcon(QIcon(str(brand)) if source in WAITING_CLIENTS else icon('search',self.theme.ink,17))
        self.catalog.setToolTip('切换媒体来源 · B 站视频或音乐')

    def current_cover_url(self):
        source = self.services.active
        if source == 'musickit':
            url = self.services.apple.art_url
            target = QUrl(url)
            return url if apple_host(target) and target.host().endswith('.mzstatic.com') else ''
        if source == 'netease':
            return netease_cover_url(self.services.state().artwork_url)
        return ''

    def remote_cover(self):
        source = self.services.active
        url = self.current_cover_url()
        if not url:
            artwork = self.services.state().artwork
            if not self.remote_art or self.remote_art[0]!=artwork:
                try:
                    image = QImage.fromData(b64decode(artwork,validate=True)) if artwork else QImage()
                except ValueError:
                    image = QImage()
                self.remote_art=(artwork,image)
            return self.remote_art[1]
        key = (source, url)
        if key != self.remote_cover_key:
            self.remote_cover_key = key
            self.remote_image = QImage()
            self.last_art_attempt = float('-inf')
            if self.art_reply is not None:
                self.art_reply.abort()

        now = time.monotonic()
        if self.remote_image.isNull() and self.art_reply is None and now - self.last_art_attempt >= 15:
            self.last_art_attempt = now
            if self.net is None:
                self.net = QNetworkAccessManager(self)
            request = QNetworkRequest(QUrl(url))
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
            request.setTransferTimeout(5000)
            reply = self.art_reply = self.net.get(request)
            limit = 1_500_000

            def cap_download(received, total):
                if max(received, total) > limit:
                    reply.abort()

            def available():
                if self.art_reply is reply:
                    self.art_reply = None
                if (self.remote_cover_key == key and reply.error() == reply.NetworkError.NoError
                        and reply.bytesAvailable() <= limit):
                    self.remote_image = QImage.fromData(bytes(reply.readAll()))
                    if self.services.state().online and (self.services.active, self.current_cover_url()) == key:
                        self.cover.image = self.remote_image
                        self.cover.update()
                reply.deleteLater()

            reply.downloadProgress.connect(cap_download)
            reply.finished.connect(available)
        return self.remote_image

    def button(self, glyph, title, callback, size):
        button = QPushButton(self)
        button.setObjectName(glyph)
        button.setAccessibleName(title)
        button.setToolTip(title)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(size, size)
        button.clicked.connect(callback)
        return button

    def slider(self, name, maximum, widget=QSlider):
        control = widget(Qt.Orientation.Horizontal, self)
        control.setAccessibleName(name)
        control.setFixedHeight(15)
        control.setRange(0, maximum)
        style = MusicSliderStyle()
        style.setParent(control)
        control.setStyle(style)
        return control

    def apply_palette(self):
        t = self.theme
        strong = '255,255,255' if t.dark else '79,43,106'
        play_ink = '#2c1b3a' if t.dark else '#ffffff'
        gradient = t.rim if t.dark else t.accent
        play_top = t.accent if t.dark else QColor(t.accent).lighter(112).name()
        self.setStyleSheet(f'''
            QLabel {{ color: {t.ink}; font-size: 11px; background: transparent; }}
            QLabel#song {{ font-size: 16px; font-weight: 700; }}
            QLabel#artist, QLabel#time {{ color: {t.secondary}; }}
            QPushButton {{ background: rgba({strong},22); border: 1px solid transparent; border-radius: 12px; }}
            QPushButton:hover {{ background: rgba({strong},42); }}
            QPushButton:pressed {{ background: rgba({strong},63); }}
            QPushButton:focus {{ border: 1px solid {t.accent}; }}
            QPushButton#bilibili {{ color: {t.ink}; font-size: 11px; font-weight: 600; border-radius: 8px; }}
            QPushButton#play {{ border-radius: 21px; border: 1px solid {t.rim};
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {play_top},stop:1 {gradient}); }}
            QPushButton#play:hover {{ background: {QColor(play_top).lighter(116).name()}; }}
            QPushButton:disabled {{ background: transparent; }}
            QSlider::groove:horizontal {{ height: 4px; background: rgba({strong},61); border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ width: 10px; margin: -4px 0; background: {t.rim};
                border: 1px solid {t.accent}; border-radius: 6px; }}
            QSlider::handle:horizontal:disabled {{ background: rgba({strong},72); border-color: transparent; }}
        ''')
        for button in (self.close_button, self.open_button, self.previous, self.next, self.catalog):
            button.setIcon(icon(button.objectName(), t.ink, 17))
            button.setIconSize(QSize(17, 17))
        self.update_source_icon()
        self.play.setIconSize(QSize(23, 23))
        self.volume_icon.setPixmap(icon('volume', t.secondary, 16).pixmap(QSize(16, 16), 2.0))
        self.play_ink = play_ink
        self.update_metadata()
        self.update_transport()
        self.cover.update()
        self.update()

    def sync_outfit(self, _state=None):
        theme = palette_for(self.pet)
        if theme is not self.theme:
            self.theme = theme
            self.apply_palette()

    def label_text(self, label, text):
        label.setToolTip(text)
        label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, label.width()))

    def choose_files(self):
        if self.file_dialog is not None:
            self.file_dialog.raise_()
            self.file_dialog.activateWindow()
            return

        dialog = QFileDialog(self)
        # Keep the picker in TT's event loop instead of the Windows native modal loop.
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        dialog.setWindowTitle('选择音频')
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        dialog.setNameFilter('音频文件 (*.mp3 *.m4a *.wav *.flac *.ogg *.opus *.aac *.wma)')
        dialog.filesSelected.connect(self.set_files)
        dialog.finished.connect(self.file_dialog_finished)
        self.file_dialog = dialog
        dialog.open()

    def file_dialog_finished(self, _result):
        dialog, self.file_dialog = self.file_dialog, None
        dialog.deleteLater()

    def set_files(self, files):
        if self.services.active!='local' or self.services.auto_select_apple:
            self.services.switch_to('local')
            if self.services.active!='local':
                return
        self.files = list(dict.fromkeys(files))
        if self.files:
            self.load(0, play=True)

    def load(self, index, play):
        self.index = index
        self.player.setSource(QUrl.fromLocalFile(self.files[index]))
        self.update_metadata()
        self.update_transport()
        if play:
            self.player.play()

    def step(self, amount):
        if self.services.active!='local':
            self.services.control('next' if amount>0 else 'previous')
            return
        index = self.index + amount
        if 0 <= index < len(self.files):
            self.load(index, play=True)

    def toggle_playback(self):
        if self.services.active!='local':
            state = self.services.state()
            action = 'pause' if state.status=='Playing' else 'play'
            if self.services.active in PLATFORMS and action=='pause' and not self.services.pending:
                self.pause_wait = time.monotonic()+2
            self.services.control(action if action in state.capabilities else 'toggle')
            return
        if self.index < 0:
            self.choose_files()
        elif self.player.isPlaying():
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    def update_transport(self, *_):
        if self.services.active!='local':
            state=self.services.state()
            caps=state.capabilities if state.online else frozenset()
            playing=state.online and state.status=='Playing'
            action='pause' if playing else 'play'
            name='暂停' if playing else '播放'
            self.play.setIcon(icon(action,self.play_ink,23))
            can_play = action in caps or 'toggle' in caps
            self.play.setToolTip(name if can_play else '当前来源尚未开放此操作')
            self.play.setAccessibleName(name)
            self.play.setEnabled(can_play)
            self.previous.setEnabled('previous' in caps)
            self.next.setEnabled('next' in caps)
            return
        self.play.setEnabled(True)
        playing = self.player.isPlaying()
        name = '暂停' if playing else '播放' if self.index >= 0 else '选择音频并播放'
        self.play.setIcon(icon('pause' if playing else 'play', self.play_ink, 23))
        self.play.setToolTip(name)
        self.play.setAccessibleName(name)
        self.previous.setEnabled(self.index > 0)
        self.next.setEnabled(0 <= self.index < len(self.files) - 1)

    def update_metadata(self):
        source=self.services.active
        if source!='local':
            state=self.services.state()
            name=PLATFORMS[source].name if source in PLATFORMS else 'Apple Music'
            self.label_text(self.title,(state.title if state.online else '') or name)
            offline = '等待网易云媒体会话' if source == 'netease' else '媒体会话不可用'
            description = (state.artist or '已连接 · '+name) if state.online else offline
            self.label_text(self.artist,description)
            self.cover.image=self.remote_cover() if state.online else QImage()
            self.cover.update()
            return
        data = self.player.metaData()
        key = QMediaMetaData.Key
        self.label_text(self.title, data.stringValue(key.Title) or (
            Path(self.files[self.index]).stem if self.index >= 0 else '还没有在播放'))
        self.label_text(self.artist, data.stringValue(key.ContributingArtist) or data.stringValue(key.AlbumArtist)
                        or ('未知歌手' if self.index >= 0 else '选择本地歌曲'))
        cover = data.value(key.CoverArtImage)
        if not isinstance(cover, QImage) or cover.isNull():
            cover = data.value(key.ThumbnailImage)
        self.cover.image = cover if isinstance(cover, QImage) else QImage()
        self.cover.update()

    def update_position(self, ms):
        if not self.progress.isSliderDown():
            with QSignalBlocker(self.progress):
                self.progress.setValue(ms)
            self.preview_seek(ms)

    def preview_seek(self, ms):
        label = time_text(ms)
        if self.elapsed.text()!=label:
            self.elapsed.setText(label)

    def progress_identity(self, state):
        return (self.services.active,state.source,state.title,state.artist,state.album)

    def tick_progress(self):
        now = time.monotonic()
        source = self.services.active
        state = self.services.state()
        if (source in PLATFORMS and state.online and state.source != NETEASE_LOCAL_SOURCE
                and now >= self.timeline_poll_at and hasattr(self.services.bridge, 'request_timeline')):
            self.timeline_poll_at = now+.70
            self.services.bridge.request_timeline(state.source)
        if source!='local' and not self.progress.isSliderDown():
            if self.seek_wait and now>=self.seek_wait[2]:
                self.seek_wait = None
                self.service_feedback('播放器未确认跳转位置，已恢复实际进度。')
            if not self.seek_wait and now>=self.pause_wait:
                self.update_position(self.progress_clock.value(now))

    def timeline_sample(self, sample):
        source,status,position,duration,rate,revision,raw,at = sample
        state = self.services.state()
        if self.services.active not in PLATFORMS or not state.online or state.source!=source:
            return
        clock = self.progress_clock
        if status!='Playing':
            self.pause_wait = 0.0
        if at<clock.at or (state.duration and abs(state.duration-duration)>250 and status=='Playing'):
            return
        if clock.status==status and clock.revision is not None and revision!=clock.revision:
            # External seeks and track restarts are timeline discontinuities.
            reset = abs(position-clock.value(at))>2800 and abs(position-raw)<2200
        else:
            reset = False
        if status=='Playing' and clock.status==status and clock.revision==revision:
            # The provider has not published a new anchor. Advance its last
            # sampled position using elapsed monotonic time only.
            position = self.timeline_anchor[0] + (at-self.timeline_anchor[1])*rate*1000
        else:
            self.timeline_anchor = (position,at)
        if self.seek_wait and self.seek_wait[0]==self.services.active and self.seek_wait[3] and at>=self.seek_wait[3]:
            if abs(position-self.seek_wait[1])<1700:
                self.seek_wait = None
                reset = True
                self.service_feedback('已从播放器确认跳转位置。')
        clock.update(self.progress_identity(state),status,position,duration,at,
                     rate=rate,revision=revision,reset=reset)
        self.display_remote_progress(duration)

    def finish_seek_request(self, source, position, success):
        if not self.seek_wait or self.seek_wait[:2]!=(source,position):
            return
        if success:
            now = time.monotonic()
            self.seek_wait = (source,position,now+3,now)
            self.timeline_poll_at = 0
            if source=='musickit':
                # This packet contains both the command reply and the player's
                # immediately subsequent position.
                state = self.services.state()
                if abs(state.position-position)<1700:
                    self.seek_wait = None
                    self.progress_clock.update(self.progress_identity(state),state.status,state.position,state.duration,now,reset=True)
                    self.service_feedback('已从播放器确认跳转位置。')
        else:
            self.seek_wait = None
        self.tick_progress()

    def display_remote_progress(self, duration):
        with QSignalBlocker(self.progress):
            self.progress.setMaximum(min(duration,2_147_483_647))
        label = time_text(duration) if duration else '—'
        if self.duration.text()!=label:
            self.duration.setText(label)
        if not self.seek_wait and time.monotonic()>=self.pause_wait:
            self.update_position(self.progress_clock.value(time.monotonic()))

    def update_local_position(self, ms):
        if self.services.active=='local':
            self.update_position(ms)

    def update_progress(self, *_):
        if self.services.active!='local':
            state=self.services.state()
            duration = state.duration if state.online else 0
            identity = self.progress_identity(state)
            status = state.status if state.online else 'Stopped'
            sample = (identity,status,state.position,duration)
            clock = self.progress_clock
            if self.snapshot!=sample:
                self.snapshot=sample
                confirmed_seek = False
                if status!='Playing' or clock.identity!=identity:
                    self.pause_wait = 0.0
                if not self.seek_wait or self.seek_wait[0]!=self.services.active or clock.identity!=identity:
                    self.seek_wait = None
                elif self.seek_wait[3] and abs(state.position-self.seek_wait[1])<1700:
                    self.seek_wait = None
                    confirmed_seek = True
                    self.service_feedback('已从播放器确认跳转位置。')
                # The fast Windows sample owns position and playback rate between
                # metadata updates. Use the regular snapshot on transitions/fallback.
                timeline_fresh = clock.revision and time.monotonic()-clock.observed_at<1.2
                if (confirmed_seek or clock.identity!=identity or duration!=clock.duration or not timeline_fresh):
                    clock.update(identity,status,state.position if state.online else 0,duration,time.monotonic(),
                                 rate=clock.rate if clock.identity==identity else 1,
                                 reset=confirmed_seek or state.source == NETEASE_LOCAL_SOURCE)
            self.progress.setEnabled('seek' in state.capabilities and duration>0)
            self.progress.setToolTip('播放进度' if self.progress.isEnabled() else
                                     '进度来自网易云客户端，当前仅可查看' if state.source==NETEASE_LOCAL_SOURCE else
                                     'Apple Music Windows 当前会话未开放进度跳转' if self.services.active=='apple' else
                                     '当前播放器未开放进度跳转')
            self.display_remote_progress(duration)
            return
        self.snapshot = None
        self.seek_wait = None
        self.pause_wait = 0.0
        ms = self.player.duration()
        with QSignalBlocker(self.progress):
            self.progress.setMaximum(min(ms, 2_147_483_647))
        self.progress.setEnabled(ms > 0 and self.player.isSeekable())
        self.progress.setToolTip('播放进度')
        self.duration.setText(time_text(ms))
        self.update_position(self.player.position())

    def seek(self, ms):
        if self.progress.gesture_active:
            return
        if self.services.active!='local':
            if self.progress.isEnabled():
                self.preview_seek(ms)
                if self.seek_wait:
                    self.update_position(self.seek_wait[1])
                    return
                # Keep the user's preview while waiting for the command and
                # then give the provider a separate window to publish position.
                self.seek_wait = (self.services.active,ms,time.monotonic()+13,0)
                self.services.control('seek',position=ms)
            return
        if self.player.isSeekable():
            self.player.setPosition(ms)

    def media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and 0<=self.index+1<len(self.files):
            self.load(self.index+1,play=True)

    def media_error(self, _error, details):
        if self.services.active!='local':
            return
        self.label_text(self.artist, '无法播放此文件')
        self.artist.setToolTip(details)

    def eventFilter(self, watched, event):
        if watched is self.pet and event.type() == QEvent.Type.Hide:
            self.hide()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_outfit()
        self.follow_pet()
        if not hasattr(self.pet,'window_manager'):
            self.follow.start()
        self.progress_timer.start()
        self.tick_progress()
        self.update_volume()

    def hideEvent(self, event):
        self.follow.stop()
        self.progress_timer.stop()
        self.native_volume_timer.stop()
        self.volume_drag_source = None
        for controller in self.native_volumes.values():
            controller.stop()
        self.volume_request = None
        if self.file_dialog is not None:
            self.file_dialog.reject()
        super().hideEvent(event)

    def closeEvent(self,event):
        for controller in self.native_volumes.values():
            controller.close()
        super().closeEvent(event)

    def follow_pet(self):
        area = self.pet.screen().availableGeometry()
        anchor = self.pet.bubble_anchor_rect().translated(self.pet.x(), self.pet.y())
        w, h = self.width(), self.height()
        head_y = anchor.top() + min(anchor.height() * .28, 69)
        preferred = (anchor.right() + 8, anchor.left() - w - 8)
        x = next((x for x in preferred if area.left() <= x and x + w <= area.right() + 1), None)
        if x is None:
            x = max(area.left(), min(anchor.center().x() - w / 2, area.right() + 1 - w))
            above = anchor.top() - area.top() >= area.bottom() + 1 - anchor.bottom()
            y = anchor.top() - h - 6 if above else anchor.bottom() + 6
            target = QPointF(anchor.center().x(), anchor.top() if above else anchor.bottom())
        else:
            y = head_y - h * .38
            target = QPointF(anchor.right() if x >= anchor.right() else anchor.left(), head_y)
        y = max(area.top(), min(y, area.bottom() + 1 - h))
        origin = QPointF(round(x), round(y))
        if self.pos() != origin.toPoint() or self.tail_target != target - origin:
            self.tail_target = target - origin
            self.move(origin.toPoint())
            self.update()

    def paintEvent(self, event):
        body = QRectF(self.rect()).adjusted(20, 17, -20, -15)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_glass(p, body, self.theme, tail_direction(outline(body), body, self.tail_target))
            paint_motif(p, QPointF(body.right() - 35, body.top() + 4), 30, self.theme)
