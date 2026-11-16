"""An on-demand player for a verified video, with a timestamped chat action."""

from base64 import b64decode
from binascii import Error as Base64Error
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QLibraryInfo, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QImageReader
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from shiboken6 import isValid

from tt_chat_images import ChatImage
from tt_edge_research import canonical_video
from tt_video_preview import PreviewJob


_native_video = False


def initialize_video_backend():
    """Select the Qt WebView2 plugin before QApplication is created."""
    global _native_video
    if sys.platform != 'win32':
        return
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    if not (plugins / 'webview' / 'qtwebview_webview2.dll').is_file():
        return
    try:
        from PySide6.QtWebView import QtWebView
    except ImportError:
        return

    os.environ['QT_WEBVIEW_PLUGIN'] = 'webview2'
    QtWebView.initialize()
    _native_video = True


PLAYBACK_POINT = """
(() => {
    const selector = '.bpx-player-video-wrap video, bwp-video video, #movie_player video';
    const playable = video => {
        const rect = video.getBoundingClientRect();
        return video.readyState >= 2 && !video.seeking && video.videoWidth && video.videoHeight
            && rect.width >= 32 && rect.height >= 32;
    };
    const video = Array.from(document.querySelectorAll(selector)).find(playable)
        || Array.from(document.querySelectorAll('video')).find(playable);
    const point = {time: -1, url: location.href};
    if (!video || !Number.isFinite(video.currentTime)) {
        return JSON.stringify(point);
    }
    point.time = video.currentTime;
    if (CAPTURE_FRAME) {
        try {
            const frame = document.createElement('canvas');
            const scale = Math.min(1, 960 / Math.max(video.videoWidth, video.videoHeight));
            frame.width = Math.max(1, Math.round(video.videoWidth * scale));
            frame.height = Math.max(1, Math.round(video.videoHeight * scale));
            frame.getContext('2d').drawImage(video, 0, 0, frame.width, frame.height);
            point.frame = frame.toDataURL('image/jpeg', 0.85);
        } catch (err) {
            // The player can forbid access to its pixels.
        }
    }
    return JSON.stringify(point);
})()
"""


EDGE_VIEW = b"""
import QtQuick
import QtWebView

Item {
    id: root
    property alias url: web.url
    signal loadFinished(bool ok)
    signal resultReady(int request, var value)

    function evaluate(request, script) {
        web.runJavaScript(script, function(value) { root.resultReady(request, value); })
    }
    function stop() { web.stop(); }

    WebView {
        id: web
        anchors.fill: parent
        settings.allowFileAccess: false
        settings.localContentCanAccessFileUrls: false
        onLoadingChanged: function(load) {
            if (load.status === WebView.LoadSucceededStatus || load.status === WebView.LoadFailedStatus)
                root.loadFinished(load.status === WebView.LoadSucceededStatus)
        }
    }
}
"""


class EdgePlayer(QWidget):
    loadFinished = Signal(bool)
    urlChanged = Signal(QUrl)

    def __init__(self, parent):
        super().__init__(parent)
        from PySide6.QtQml import QQmlComponent
        from PySide6.QtQuick import QQuickView

        self.quick = QQuickView()
        self.component = QQmlComponent(self.quick.engine())
        self.component.setData(QByteArray(EDGE_VIEW), QUrl())
        self.root = self.component.create()
        if self.root is None:
            raise RuntimeError('Qt WebView view initialization failed')
        self.quick.setContent(QUrl(), self.component, self.root)
        self.quick.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QWidget.createWindowContainer(self.quick, self))
        self.sequence = 0
        self.pending = {}
        self.root.loadFinished.connect(self.loadFinished)
        self.root.urlChanged.connect(lambda: self.urlChanged.emit(self.url()))
        self.root.resultReady.connect(self.javascript_result)

    def url(self):
        return self.root.property('url')

    def setUrl(self, url):
        self.pending.clear()
        self.root.setProperty('url', url)

    def stop(self):
        self.pending.clear()
        self.root.stop()

    def runJavaScript(self, script, callback):
        self.sequence += 1
        self.pending[self.sequence] = callback
        self.root.evaluate(self.sequence, script)

    def javascript_result(self, request, value):
        callback = self.pending.pop(request, None)
        if callback is not None:
            callback(value)


class WatchDialog(QDialog):
    def __init__(self, room, item=None):
        super().__init__(room)
        self.room = room
        self.item = None
        self.session = room.session, room.identity
        self.video = None
        self.closing = False
        self.discuss_request = None
        self.preview_job = None
        self.native = _native_video
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('和 TT 一起看')
        self.resize(840, 620)
        layout = QVBoxLayout(self)
        self.heading = QLabel('')
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        layout.addWidget(self.make_player(), 1)
        tools = QHBoxLayout()
        self.status = QLabel('正在加载视频…')
        self.status.setWordWrap(True)
        tools.addWidget(self.status, 1)
        self.include_frame = QCheckBox('附视频画面')
        self.include_frame.setChecked(True)
        self.include_frame.setToolTip('点击「聊聊这一段」后，仅把当前视频的一帧画面发送给聊天模型。B 站可使用邻近的公开视频预览帧。')
        tools.addWidget(self.include_frame)
        self.discuss_button = QPushButton('聊聊这一段')
        self.discuss_button.clicked.connect(self.discuss)
        tools.addWidget(self.discuss_button)
        self.external = QPushButton('打开原网页')
        self.external.clicked.connect(self.open_original)
        tools.addWidget(self.external)
        layout.addLayout(tools)
        self.load_timer = QTimer(self, singleShot=True, interval=15000)
        self.load_timer.timeout.connect(lambda: self.status.setText('网页仍在加载，可以点击「打开原网页」。'))
        self.discuss_timer = QTimer(self, singleShot=True, interval=6000)
        self.discuss_timer.timeout.connect(self.discuss_timeout)
        self.include_frame.toggled.connect(self.frame_option_changed)
        for control in (self.include_frame, self.discuss_button, self.external):
            control.setEnabled(False)
        if item is not None:
            self.set_item(item)

    def set_item(self, item):
        self.cancel_discussion()
        self.item = item
        self.video = canonical_video(item.url)[:2]
        self.heading.setText(item.title)
        self.status.setText('正在加载视频…')
        for control in (self.include_frame, self.discuss_button, self.external):
            control.setEnabled(True)
        self.view.stop()
        self.load_timer.start()
        self.view.setUrl(QUrl(item.url))

    def open_original(self):
        if self.item:
            QDesktopServices.openUrl(QUrl(self.item.url))

    def allowed(self, url):
        if url.isEmpty() or url.toString() == 'about:blank':
            return True
        if self.video is None or url.scheme() != 'https':
            return False
        if self.video[0] == 'YouTube' and url.host().lower() == 'consent.google.com':
            return True
        current = canonical_video(url.toString())
        return current is not None and current[:2] == self.video

    def make_player(self):
        if self.native:
            try:
                self.view = self.page = EdgePlayer(self)
            except (ImportError, RuntimeError):
                self.native = False
            else:
                self.view.urlChanged.connect(self.native_url_changed)
                self.view.loadFinished.connect(self.loaded)
                return self.view

        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView

        class PlayerPage(QWebEnginePage):
            def acceptNavigationRequest(page, url, _kind, main):
                return not main or self.allowed(url)

            def createWindow(page, _kind):
                return None

        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.page = PlayerPage(self.profile, self.view)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
        self.view.setPage(self.page)
        self.view.loadFinished.connect(self.loaded)
        return self.view

    def native_url_changed(self, url):
        if not self.closing and not self.allowed(url):
            QTimer.singleShot(0, self, self.restore_video_url)

    def restore_video_url(self):
        if not self.closing and not self.allowed(self.view.url()):
            self.view.stop()
            self.view.setUrl(QUrl(self.item.url if self.item else 'about:blank'))

    def loaded(self, ok):
        if self.closing or self.item is None:
            return
        self.load_timer.stop()
        self.status.setText('点播放开始。可以把当前时间点带回聊天。' if ok
                            else '站点播放器加载失败，可以打开原网页。')

    def discuss(self):
        if self.item is None or self.discuss_request is not None or self.preview_job is not None:
            return
        if self.session != (self.room.session, self.room.identity):
            self.status.setText('当前聊天已切换。请在对应聊天里重新打开这个视频。')
            return
        video = canonical_video(self.page.url().toString())
        if not video or video[:2] != self.video:
            self.status.setText('请等视频页载入，再发送当前进度。')
            return
        if not self.chat_ready():
            return
        capture = self.include_frame.isChecked()
        request = self.discuss_request = object()
        self.discuss_button.setEnabled(False)
        self.status.setText('正在读取当前画面…' if capture else '正在读取播放进度…')
        self.discuss_timer.start()
        self.page.runJavaScript(PLAYBACK_POINT.replace('CAPTURE_FRAME', 'true' if capture else 'false'),
                                lambda point: self.point_ready(request, point))

    def chat_ready(self):
        if not self.room.consent.isChecked() or not self.room.models.currentData() or self.room.busy():
            self.status.setText('请先选择可用的聊天模型，并等这轮回复结束。')
            return False
        return True

    def cancel_discussion(self):
        self.discuss_request = None
        self.discuss_timer.stop()
        if self.preview_job:
            self.preview_job.cancelled.set()
            self.preview_job = None
        self.discuss_button.setEnabled(self.item is not None and not self.closing)

    def discuss_timeout(self):
        self.cancel_discussion()
        self.status.setText('播放器暂未返回进度，请等画面出现后重试。')

    def frame_option_changed(self, _checked):
        if self.discuss_request is not None or self.preview_job is not None:
            self.cancel_discussion()
            self.status.setText('画面选项已更新，请重新点击「聊聊这一段」。')

    def point_ready(self, request, result):
        if not isValid(self) or self.closing or self.discuss_request is not request:
            return
        self.cancel_discussion()
        if self.session != (self.room.session, self.room.identity) or not self.chat_ready():
            return
        try:
            point = json.loads(result) if isinstance(result, str) else result
        except (TypeError, ValueError):
            point = {}
        if not isinstance(point, dict):
            point = {}
        source = canonical_video(point.get('url'))
        if not source:
            self.status.setText('还没有读到视频画面，请播放到想聊的片段后重试。')
            return
        if source[2] != self.item.url:
            self.status.setText('视频页面已切换，请重新打开要讨论的视频。')
            return
        seconds = point.get('time')
        if type(seconds) not in (float, int) or not 0 <= seconds < 86400:
            self.status.setText('请先播放到想聊的片段，待画面出现后再试。')
            return
        if not self.include_frame.isChecked():
            self.send_point(seconds)
            return
        frame = self.frame_image(point)
        if not frame.isNull():
            self.send_point(seconds, frame)
        elif source[0] == '哔哩哔哩':
            self.status.setText('正在查找这个时间点附近的 B 站公开视频预览…')
            self.discuss_button.setEnabled(False)
            self.preview_job = PreviewJob(self.item.url, seconds)
            self.preview_job.ready.connect(self.preview_ready, Qt.ConnectionType.QueuedConnection)
            self.preview_job.start()
        else:
            self.frame_unavailable()

    def preview_ready(self, job, frame):
        if self.closing or self.preview_job is not job:
            return
        self.preview_job = None
        self.discuss_button.setEnabled(True)
        if self.session != (self.room.session, self.room.identity) or not self.chat_ready():
            return
        if frame is None:
            self.frame_unavailable()
            return
        self.send_point(job.seconds, frame.image, preview_seconds=frame.seconds)

    def frame_unavailable(self):
        self.status.setText('暂时取不到这个时间点的画面。可以重试，或取消勾选「附视频画面」后只发送链接和时间。')

    @staticmethod
    def time_label(seconds):
        mins, secs = divmod(int(seconds), 60)
        return f'{mins:02d}:{secs:02d}'

    def send_point(self, seconds, frame=None, *, preview_seconds=None):
        stamp = self.time_label(seconds)
        prompt = '我们一起聊聊这个视频'
        prompt += f'的 {stamp} 附近'
        if self.item.detail:
            prompt += f'。卡片简介：{self.item.detail}'
        prompt += f'。\n{self.item.title}\n{self.item.url}'
        images = (ChatImage(frame),) if frame is not None else ()
        status = f'已发送 {stamp} 的链接和时间点。'
        if preview_seconds is not None:
            preview = self.time_label(preview_seconds)
            prompt += f'\n随消息附上 B 站公开视频预览帧（{preview}），请按这张画面聊这一段。'
            status = f'已发送 {stamp} 的时间点，随附 {preview} 的 B 站公开视频预览帧。'
        elif images:
            prompt += f'\n随消息附上播放器在 {stamp} 的画面，请根据可见内容聊这一段。'
            status = f'已发送 {stamp} 的视频画面。'
        self.room.send_prompt(prompt, clear_input=False, images=images)
        self.status.setText(status)

    @staticmethod
    def frame_image(point):
        url = point.get('frame')
        prefix = 'data:image/jpeg;base64,'
        if isinstance(url, str) and url.startswith(prefix) and len(url) < 2_000_000:
            try:
                buffer = QBuffer()
                buffer.setData(QByteArray(b64decode(url[len(prefix):], validate=True)))
                buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                reader = QImageReader(buffer, b'jpeg')
                size = reader.size()
                if not size.isEmpty() and max(size.width(), size.height()) <= 960:
                    return reader.read()
            except Base64Error:
                pass
        return QImage()

    def closeEvent(self, event):
        self.closing = True
        self.cancel_discussion()
        self.load_timer.stop()
        self.view.stop()
        self.page.setUrl(QUrl('about:blank'))
        super().closeEvent(event)
