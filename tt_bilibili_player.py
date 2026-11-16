"""Bilibili playback and a playlist for the TT media panel."""

import json

import httpx
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider

from tt_edge_research import BILIBILI_ID, canonical_video
from tt_background_task import BackgroundTask
from tt_share_content import bilibili_popular, video_card
from tt_share_player import WatchDialog


VIDEO_CONTROLS = r"""
(options => {
    const part = url => new URL(url).searchParams.get('p') || '1';
    const videoPath = url => new URL(url).pathname.replace(/\/$/, '');
    if (location.protocol !== 'https:' || location.hostname !== 'www.bilibili.com' ||
        videoPath(location.href) !== videoPath(options.url) || part(location.href) !== part(options.url)) {
        return null;
    }
    const video = document.querySelector('.bpx-player-video-wrap video, bwp-video video, video');
    if (!video) return null;

    const player = window.player;
    const setVolume = () => {
        const level = options.volume / 100;
        if (typeof player?.setVolume === 'function') player.setVolume(level);
        video.volume = level;
        video.muted = level === 0;
    };

    if (!video.ttMediaInitialized) {
        video.ttMediaInitialized = true;
        video.scrollIntoView({block: 'center'});
        setVolume();
    } else if (options.changeVolume) {
        setVolume();
    }
    if (options.play === true && video.paused) {
        video.ttMediaPlayError = false;
        const started = typeof player?.play === 'function' ? player.play() : video.play();
        Promise.resolve(started).catch(() => { video.ttMediaPlayError = true; });
    } else if (options.play === false) {
        if (typeof player?.pause === 'function') player.pause();
        video.pause();
    }
    return {
        paused: video.paused || video.ended,
        volume: video.muted ? 0 : Math.round(video.volume * 100),
        blocked: video.paused && !!video.ttMediaPlayError,
        title: document.title.replace(/_哔哩哔哩.*$/, '').slice(0, 160)
    };
})(OPTIONS)
"""


class PopularVideos(BackgroundTask):
    ready = Signal(list)

    def run(self):
        cards = []
        try:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'}
            with httpx.Client(timeout=httpx.Timeout(5, connect=3), headers=headers) as client:
                rows = bilibili_popular(client, self.cancelled)
            for row in rows:
                try:
                    cards.append(video_card(row['url'], row['title'], row['summary']))
                except ValueError:
                    continue
        except (OSError, httpx.RequestError):
            pass
        self.publish('ready', cards)


class BilibiliDialog(WatchDialog):
    def __init__(self, room, return_to_music):
        super().__init__(room)
        self.return_to_music = return_to_music
        self.setWindowTitle('TT 媒体 · B 站')
        self.heading.hide()
        self.playlist = {}
        self.job = None
        self.inflight = False
        self.play_request = None
        self.volume_requested = False
        self.paused = True

        links = QHBoxLayout()
        self.link = QLineEdit()
        self.link.setPlaceholderText('粘贴 B 站视频页链接或 BV 号')
        self.link.setMaxLength(2048)
        self.link.returnPressed.connect(self.add_link)
        links.addWidget(self.link, 1)
        add = QPushButton('添加并播放')
        add.clicked.connect(self.add_link)
        links.addWidget(add)
        self.popular = QPushButton('加入热门视频')
        self.popular.clicked.connect(self.load_popular)
        links.addWidget(self.popular)
        music = QPushButton('返回音乐')
        music.clicked.connect(self.show_music)
        links.addWidget(music)
        self.layout().insertLayout(0, links)

        controls = QHBoxLayout()
        self.previous = QPushButton('上一条')
        self.previous.clicked.connect(lambda: self.step(-1))
        self.play = QPushButton('播放')
        self.play.clicked.connect(self.toggle_playback)
        self.next = QPushButton('下一条')
        self.next.clicked.connect(lambda: self.step(1))
        self.queue = QComboBox()
        self.queue.setAccessibleName('B 站播放列表')
        self.queue.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.queue.setMinimumContentsLength(8)
        self.queue.activated.connect(self.select_video)
        for control in (self.previous, self.play, self.next):
            controls.addWidget(control)
        controls.addWidget(self.queue, 1)
        controls.addWidget(QLabel('音量'))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setAccessibleName('B 站视频音量')
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setFixedWidth(107)
        self.volume.valueChanged.connect(self.change_volume)
        controls.addWidget(self.volume)
        self.volume_label = QLabel('70%')
        self.volume_label.setMinimumWidth(self.volume_label.fontMetrics().horizontalAdvance('100%'))
        controls.addWidget(self.volume_label)
        self.layout().insertLayout(self.layout().count() - 1, controls)

        self.status.setText('输入 BV 号、视频链接，或加入热门视频。')
        self.timer = QTimer(self, interval=450)
        self.timer.timeout.connect(self.refresh_playback)
        self.update_navigation()
        self.play.setEnabled(False)

    def show_music(self):
        # Leaving the video source stops it before exposing music controls.
        self.close()
        self.return_to_music()

    def add_link(self):
        url = self.link.text().strip()
        if BILIBILI_ID.fullmatch(url):
            url = 'https://www.bilibili.com/video/' + url + '/'
        video = canonical_video(url)
        if not video or video[0] != '哔哩哔哩':
            self.status.setText('请粘贴 B 站视频页链接，或输入 BV 号。')
            return
        self.link.clear()
        self.select_video(self.add_item(video_card(video[2])))

    def add_item(self, item):
        index = self.queue.findData(item.url)
        if index < 0:
            self.playlist[item.url] = item
            self.queue.addItem(item.title, item.url)
            return self.queue.count() - 1
        return index

    def load_popular(self):
        self.job = PopularVideos()
        self.job.ready.connect(self.popular_ready)
        self.popular.setEnabled(False)
        self.popular.setText('正在获取热门…')
        self.job.start()

    def popular_ready(self, items):
        self.job = None
        self.popular.setEnabled(True)
        self.popular.setText('加入热门视频')
        if not items:
            self.status.setText('暂时无法获取 B 站热门视频，可以直接添加视频链接。')
            return
        for item in items:
            self.add_item(item)
        if self.item is None:
            self.select_video(0, autoplay=False)
        self.update_navigation()

    def select_video(self, index, autoplay=True):
        item = self.playlist.get(self.queue.itemData(index))
        if item is None:
            return
        self.queue.setCurrentIndex(index)
        if item is not self.item:
            self.inflight = False
            self.play.setEnabled(False)
            self.paused = True
            self.play.setText('播放')
            self.play_request = True if autoplay else None
            self.volume_requested = True
            self.set_item(item)
            self.timer.start()
        elif autoplay and self.paused:
            self.play_request = True
            self.refresh_playback()
        self.update_navigation()

    def update_navigation(self):
        index = self.queue.currentIndex()
        self.queue.setEnabled(self.queue.count() > 0)
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(0 <= index < self.queue.count() - 1)

    def step(self, offset):
        self.select_video(self.queue.currentIndex() + offset)

    def toggle_playback(self):
        self.play_request = self.paused
        self.refresh_playback()

    def change_volume(self, value):
        self.volume_label.setText(f'{value}%')
        self.volume_requested = True
        self.refresh_playback()

    def refresh_playback(self):
        item = self.item
        if self.closing or item is None or self.inflight:
            return
        url = canonical_video(self.page.url().toString())
        if url is None or url[2] != item.url:
            return
        options = {'url': item.url, 'play': self.play_request, 'volume': self.volume.value(),
                   'changeVolume': self.volume_requested}
        self.inflight = True
        self.page.runJavaScript(VIDEO_CONTROLS.replace('OPTIONS', json.dumps(options)),
                                lambda state: self.playback_state(item, options, state))

    def playback_state(self, item, options, state):
        if self.closing or self.item is not item:
            return
        self.inflight = False
        if not isinstance(state, dict):
            return
        if options['play'] == self.play_request:
            self.play_request = None
        self.paused = state['paused']
        self.play.setEnabled(True)
        self.play.setText('播放' if self.paused else '暂停')
        if self.volume.value() == options['volume']:
            self.volume_requested = False
            with QSignalBlocker(self.volume):
                self.volume.setValue(state['volume'])
            self.volume_label.setText(f"{state['volume']}%")
        index = self.queue.currentIndex()
        if BILIBILI_ID.fullmatch(self.queue.itemText(index)) and state.get('title'):
            self.queue.setItemText(index, state['title'])
        if state.get('blocked'):
            self.status.setText('B 站未允许直接播放。点击画面里的播放键后可用 TT 控制。')
        elif not self.paused and self.status.text().startswith('B 站未允许'):
            self.status.clear()

    def allowed(self, url):
        if not super().allowed(url):
            return False
        if self.item is None:
            return True
        current = canonical_video(url.toString())
        return current is None or current[2] == self.item.url

    def restore_video_url(self):
        if not self.allowed(self.view.url()):
            self.inflight = False
        super().restore_video_url()

    def closeEvent(self, event):
        self.timer.stop()
        if self.job:
            self.job.cancelled.set()
        super().closeEvent(event)
