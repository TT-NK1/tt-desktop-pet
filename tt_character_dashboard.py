"""Conversation header backed by the existing character and media controllers."""
from datetime import datetime
from math import ceil
import time

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import (
    QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QMenu,
    QPushButton, QSizePolicy, QVBoxLayout,
)

from tt_bubble_art import palette_for
from tt_theme import DARK, LIGHT
from tt_window_layout import dashboard_mode


STYLE = '''
QFrame#characterDashboard {
    background: transparent;
    border: 1px solid #ddcce9; border-radius: 15px;
}
QFrame#dashboardDetails { background: rgba(255,255,255,137); border: 1px solid #e6d9ef; border-radius: 11px; }
QLabel#dashboardName { color: #4b3260; font-size: 24px; font-weight: 700; }
QLabel#dashboardWelcome { color: #5e496b; font-size: 13px; }
QLabel#dashboardMeta { color: #715e7d; font-size: 11px; }
QLabel#dashboardStatusKey { color: #826b91; font-size: 11px; }
QLabel#dashboardStatusValue { color: #503a60; font-size: 12px; font-weight: 600; }
QLabel#characterState { color: #634477; background: #eee1f7; border: 1px solid #e4d4ef;
    padding: 2px 7px; border-radius: 9px; font-size: 11px; }
QPushButton#dashboardAction, QPushButton#dashboardToggle {
    color: #5b406d; background: rgba(255,255,255,189); border: 1px solid #ded0eb;
    border-radius: 8px; padding: 4px 8px; font-size: 12px;
}
QPushButton#dashboardAction:hover, QPushButton#dashboardToggle:hover { background: #eee0f8; border-color: #baa0cf; }
QPushButton#dashboardAction:disabled { color: #897891; background: #f5edf8; }
'''

WELCOME = {
    'first': '今天想和我聊些什么？',
    'return': '欢迎回来，接着聊吧。',
    'new': '新的话题从这里开始。',
    'replied': '想接着聊的话，我就在这里。',
}

DARK_STYLE = '''
QFrame#characterDashboard { border-color: $border; }
QFrame#dashboardDetails { background: $glass; border-color: $border; }
QLabel#dashboardName, QLabel#dashboardWelcome, QLabel#dashboardStatusValue { color: $text; }
QLabel#dashboardMeta, QLabel#dashboardStatusKey { color: $muted; }
QLabel#characterState { color: $highlight; background: $button; border-color: $border; }
QPushButton#dashboardAction, QPushButton#dashboardToggle {
    background: $button; color: $text; border-color: $border;
}
QPushButton#dashboardAction:hover, QPushButton#dashboardToggle:hover {
    background: $hover; border-color: $accent;
}
QPushButton#dashboardAction:disabled { color: $disabled; background: $card; }
'''


def _glow(painter, x, y, radius, color):
    gradient = QRadialGradient(x, y, radius)
    gradient.setColorAt(0, color)
    gradient.setColorAt(.52, QColor(color.red(), color.green(), color.blue(), color.alpha() // 3))
    gradient.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QPointF(x, y), radius, radius)


def _window_light(painter, width, height):
    painter.save()
    span = min(width * .32, 252)
    painter.translate(width - span, 0)
    painter.scale(span / 220, height / 206)
    _glow(painter, 186, 63, 163, QColor(255, 251, 245, 141))

    # Individual panes fall diagonally across the wall.
    painter.translate(75, -22)
    painter.shear(-.31, .09)
    for y, pane_height in ((0, 75), (82, 110)):
        for x, pane_width in ((0, 56), (63, 70), (140, 58)):
            light = QLinearGradient(0, y, 0, y + pane_height)
            light.setColorAt(0, QColor(255, 252, 249, 62))
            light.setColorAt(1, QColor(255, 252, 249, 18))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(light)
            painter.drawRoundedRect(QRectF(x, y, pane_width, pane_height), 4, 4)
    painter.restore()


def _sakura(painter, width, height, opacity=1.0):
    painter.save()
    painter.setOpacity(opacity)
    scale = min(width * .002, height / 163, 1.12)
    painter.scale(scale, scale)
    branch = QPainterPath(QPointF(-13, 57))
    branch.cubicTo(32, 50, 53, 24, 93, 17)
    branch.cubicTo(114, 13, 135, 13, 155, -1)
    branch.moveTo(14, 48)
    branch.cubicTo(35, 32, 37, 15, 40, -4)
    branch.moveTo(58, 29)
    branch.cubicTo(77, 30, 96, 37, 111, 28)
    branch.moveTo(98, 16)
    branch.cubicTo(102, 9, 112, 3, 123, 1)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(122, 79, 133, 33), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawPath(branch)

    petal = QPainterPath(QPointF(0, 0))
    petal.cubicTo(-4, -4, -5, -11, -2, -12)
    petal.quadTo(0, -14, 1, -11)
    petal.quadTo(4, -14, 5, -10)
    petal.cubicTo(7, -6, 3, -3, 0, 0)
    painter.setPen(Qt.PenStyle.NoPen)
    for x, y, size, angle in (
        (14, 43, .80, 10), (29, 32, 1.07, 11), (36, 17, .62, 35),
        (47, 34, .88, -6), (59, 22, .72, 48), (76, 17, 1.12, 19),
        (90, 31, .78, 0), (109, 27, .57, -12), (113, 11, .84, 52),
        (130, 9, .72, 36), (145, 2, .61, 19),
    ):
        painter.save()
        painter.translate(x, y)
        painter.scale(size, size)
        painter.rotate(angle)
        for index in range(5):
            painter.setBrush(QColor(208, 132, 180, 28 if index % 2 else 35))
            painter.drawPath(petal)
            painter.rotate(72)
        painter.setBrush(QColor(173, 111, 156, 34))
        painter.drawEllipse(QPointF(0, 0), 1.7, 1.7)
        painter.restore()
    painter.restore()

    painter.save()
    painter.setOpacity(opacity)
    painter.setPen(Qt.PenStyle.NoPen)
    for x, y, angle, size in (
        (13, height * .64, -37, .57), (32, height - 13, 125, .65),
        (width - 51, 16, 109, .64), (width - 16, height * .53, 65, .54),
        (width - 72, height - 13, -58, .66), (width - 20, height - 19, 146, .77),
    ):
        painter.save()
        painter.translate(x, y)
        painter.rotate(angle)
        painter.scale(size, size)
        painter.setBrush(QColor(210, 137, 184, 29))
        painter.drawPath(petal)
        painter.restore()
    painter.restore()


def _room_background(width, height, dpr, dark):
    surface = QPixmap(ceil(width * dpr), ceil(height * dpr))
    surface.setDevicePixelRatio(dpr)
    surface.fill(Qt.GlobalColor.transparent)
    painter = QPainter(surface)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    card = QRectF(0, 0, width, height)
    clip = QPainterPath()
    clip.addRoundedRect(card, 15, 15)
    painter.setClipPath(clip)

    if dark:
        painter.fillRect(card, QColor(DARK.card))
        _glow(painter, width * .13, height * .48, min(width * .39, 235), QColor(168, 130, 218, 43))
        _glow(painter, width * .96, height * .13, min(width * .27, 205), QColor(166, 135, 213, 25))
        _sakura(painter, width, height, opacity=.45)
    else:
        base = QLinearGradient(0, 0, width * .72, height)
        base.setColorAt(0, QColor('#eee3f8'))
        base.setColorAt(.43, QColor('#faf4fe'))
        base.setColorAt(1, QColor('#eee4f8'))
        painter.fillRect(card, base)
        _glow(painter, width * .45, 4, min(width * .40, 280), QColor(255, 253, 255, 118))
        _glow(painter, 49, height - 21, 66, QColor(255, 239, 249, 69))
        _window_light(painter, width, height)
        _sakura(painter, width, height)

    # A thin inner highlight gives the card a glass edge at any device scale.
    rim = QLinearGradient(0, 0, 0, height)
    rim.setColorAt(0, QColor(255, 255, 255, 28 if dark else 177))
    rim.setColorAt(.52, QColor(255, 255, 255, 7 if dark else 20))
    rim.setColorAt(1, QColor(255, 255, 255, 16 if dark else 104))
    painter.setPen(QPen(rim, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(card.adjusted(1.5, 1.5, -1.5, -1.5), 13, 13)
    painter.end()
    return surface


class CharacterDashboard(QFrame):
    def __init__(self, room):
        super().__init__(room)
        self.room = room
        self.avatar = room.avatar
        self.pet = None
        self.music = None
        self.music_playing_before = False
        self.compact = False
        self.wide = None
        self.layout_mode = None
        self.detail_width = 103
        self.opened = False
        self.last_open = 0.0
        self.greeting_event = 'first'
        self.outfit = None
        self.last_chat = None
        self._background_key = None
        self._background = None
        self.dark_theme = False
        self.bubble_timer = QTimer(self, singleShot=True, interval=3200)
        self.setObjectName('characterDashboard')
        self.setStyleSheet(STYLE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(10, 8, 12, 8)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(5)
        self.grid.addWidget(self.avatar, 0, 0, Qt.AlignmentFlag.AlignCenter)
        self.avatar.dashboard_feedback = True
        self.avatar.feedback_text.connect(self.say)
        self.avatar.metrics_changed.connect(self.refresh)

        self.info = QFrame(self)
        self.info.setMinimumWidth(0)
        words = self.words = QVBoxLayout(self.info)
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(5)
        words.addStretch()
        headline = QHBoxLayout()
        headline.setSpacing(4)
        self.name = QLabel('TT 的小窝')
        self.name.setObjectName('dashboardName')
        headline.addWidget(self.name)
        headline.addStretch()
        self.toggle_button = self.button('收起', self.toggle, name='dashboardToggle')
        headline.addWidget(self.toggle_button)
        words.addLayout(headline)
        self.state = QLabel('待机')
        self.state.setObjectName('characterState')
        words.addWidget(self.state, 0, Qt.AlignmentFlag.AlignLeft)
        self.welcome = QLabel(WELCOME['first'])
        self.welcome.setObjectName('dashboardWelcome')
        self.welcome.setWordWrap(True)
        self.welcome.setMinimumWidth(0)
        words.addWidget(self.welcome)
        self.bubble_slot = QFrame(self.info)
        self.bubble_slot.setFixedHeight(25)
        slot = QHBoxLayout(self.bubble_slot)
        slot.setContentsMargins(0, 0, 0, 0)
        self.bubble = QLabel('')
        self.bubble.setObjectName('dashboardBubble')
        self.bubble.setTextFormat(Qt.TextFormat.PlainText)
        shadow = QGraphicsDropShadowEffect(self.bubble, blurRadius=8, xOffset=0, yOffset=2,
                                          color=QColor(116, 71, 143, 38))
        self.bubble.setGraphicsEffect(shadow)
        self.bubble.hide()
        self.bubble_timer.timeout.connect(self.bubble.hide)
        slot.addWidget(self.bubble, 0, Qt.AlignmentFlag.AlignLeft)
        words.addWidget(self.bubble_slot)

        self.actions = QFrame(self.info)
        buttons = self.button_grid = QGridLayout(self.actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(5)
        self.voice_button = self.button('语音', self.open_voice)
        self.voice_button.setToolTip('朗读最近回复或打开语音设置')
        self.motion_button = self.button('切换动作', self.open_motions)
        self.music_button = self.button('打开媒体', self.open_music)
        self.quick_buttons = (self.voice_button, self.motion_button, self.music_button)
        for index, button in enumerate(self.quick_buttons):
            buttons.addWidget(button, index//2, index%2)
        self.more_button = QPushButton('更多操作')
        self.more_button.setObjectName('dashboardAction')
        self.more_button.setAccessibleName('人物卡片更多操作')
        menu = QMenu(self.more_button)
        self.more_button.setMenu(menu)
        self.more_actions = tuple(menu.addAction(button.text(), lambda b=button: b.click())
                                  for button in self.quick_buttons)
        buttons.addWidget(self.more_button, 0, 0)
        self.more_button.hide()
        words.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignLeft)
        self.meta = QLabel('当前对话 · 新话题')
        self.meta.setObjectName('dashboardMeta')
        words.addWidget(self.meta)
        words.addStretch()
        self.grid.addWidget(self.info, 0, 1)
        self.grid.setColumnStretch(1, 1)

        self.details = QFrame(self)
        self.details.setObjectName('dashboardDetails')
        rows = QGridLayout(self.details)
        rows.setContentsMargins(9, 7, 9, 7)
        rows.setHorizontalSpacing(7)
        rows.setVerticalSpacing(6)
        self.values = {}
        self.keys = []
        self.detail_grid = rows
        for icon, key in (('◇','服装'),('↻','动作'),('◖','语音'),('♪','音乐')):
            field = QLabel(f'{icon}  {key}')
            field.setObjectName('dashboardStatusKey')
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setObjectName('dashboardStatusValue')
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setMinimumWidth(60)
            self.keys.append(field)
            self.values[key] = label
        self.animation = QVariantAnimation(self, duration=190)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.size_avatar)
        self.animation.finished.connect(self.finish_transition)

    def paintEvent(self, event):
        width, height, dpr = self.width(), self.height(), self.devicePixelRatioF()
        if width > 0 and height > 0:
            key = (width, height, dpr, self.dark_theme)
            if key != self._background_key:
                self._background = _room_background(*key)
                self._background_key = key
            painter = QPainter(self)
            painter.drawPixmap(0, 0, self._background)
            painter.end()
        super().paintEvent(event)

    def apply_theme(self, dark):
        if dark != self.dark_theme:
            self.dark_theme = dark
            self.setStyleSheet(STYLE + (DARK.css(DARK_STYLE) if dark else ''))
            self.style_bubble()
            self.update()

    def style_bubble(self):
        palette = DARK if self.dark_theme else LIGHT
        tint = palette.accent if self.dark_theme else palette_for(self.pet).accent
        text = palette.text if self.dark_theme else '#5a406b'
        self.bubble.setStyleSheet(f'background: {palette.welcome}; color: {text}; '
                                  f'border: 1px solid {tint}; border-radius: 9px; padding: 2px 8px;')

    @staticmethod
    def button(text, callback, name='dashboardAction'):
        button = QPushButton(text)
        button.setObjectName(name)
        button.clicked.connect(callback)
        return button

    def bind(self, pet):
        self.pet = pet
        pet.renderer.ready.connect(self.refresh)
        voice = self.room.pro_voice
        voice.listeners.append(self.refresh)
        voice.player.player.playbackStateChanged.connect(self.refresh)
        self.refresh()

    def load_conversation_info(self):
        room = self.room
        latest = room.db.execute(
            'SELECT MAX(created) FROM turns WHERE session=? AND account=? AND status=?',
            (room.session, room.identity, 'completed'),
        ).fetchone()[0]
        self.last_chat = datetime.fromtimestamp(latest).strftime('%m/%d %H:%M') if latest else None
        self.refresh()

    def bind_music(self, bubble):
        self.music = bubble.player
        self.music.playbackStateChanged.connect(self.media_changed)
        self.music.metaDataChanged.connect(self.refresh)
        self.music.sourceChanged.connect(self.refresh)
        self.media_changed()

    def open_voice(self):
        room = self.room
        if not hasattr(room, 'pro_voice'):
            return
        if room.busy():
            return
        latest = room.db.execute(
            "SELECT id,answer FROM turns WHERE session=? AND account=? "
            "AND status IN ('completed', 'proactive') AND trim(answer)<>'' ORDER BY id DESC LIMIT 1",
            (room.session, room.identity),
        ).fetchone()
        if not latest:
            room.pro_dialog()
            return
        menu = QMenu(self)
        menu.addAction('朗读最近回复', lambda: room.read_reply(*latest))
        menu.addAction('语音设置', room.pro_dialog)
        menu.exec(self.action_anchor(self.voice_button))

    def open_motions(self):
        for entry in self.pet.menu.actions():
            menu = entry.menu()
            if menu and menu.title().startswith('动作'):
                position = self.action_anchor(self.motion_button)
                self.room.theme.exec_motion_menu(menu, position)
                return

    def open_music(self):
        self.pet.open_music_bubble()

    def action_anchor(self, button):
        anchor = self.more_button if self.layout_mode == 'mini' else button
        return anchor.mapToGlobal(QPoint(0, anchor.height()))

    def opened_window(self):
        now = time.monotonic()
        if not self.opened or now - self.last_open > 12:
            self.greeting_event = 'return' if self.opened else 'first'
            self.say('欢迎回来！' if self.opened else '嗨，我在这里。')
        self.opened = True
        self.last_open = now
        self.refresh()

    def conversation_event(self, event):
        self.greeting_event = event
        if event == 'new':
            self.last_chat = None
        self.refresh()

    def say(self, message):
        if self.compact or not self.avatar.effects or not message:
            return
        self.bubble.setText(message)
        self.bubble.show()
        self.bubble_timer.start()

    def expression_changed(self, name):
        messages = {'smile':'见到你真好。', 'happy':'好开心呀！', 'focused':'让我想想。', 'shy':'唔……'}
        message = messages.get(name)
        if message:
            self.say(message)
        elif self.bubble.text() in messages.values():
            self.bubble_timer.stop()
            self.bubble.hide()

    def media_changed(self, *_):
        playing, _, _ = self.music_info()
        if playing and not self.music_playing_before:
            self.say('音乐开始啦。')
        elif not playing and self.bubble.text() == '音乐开始啦。':
            self.bubble_timer.stop()
            self.bubble.hide()
        self.music_playing_before = playing
        self.refresh()

    def music_info(self):
        service = getattr(self.pet, 'music_services', None)
        if service and service.active!='local':
            state = service.state()
            return state.online and state.status=='Playing', state.online and state.status=='Paused', state.title
        if not self.music:
            return False, False, ''
        return (self.music.isPlaying(), self.music.playbackState()==QMediaPlayer.PlaybackState.PausedState,
                self.pet.music_bubble.title.toolTip() if not self.music.source().isEmpty() else '')

    def value(self, key, text):
        label = self.values[key]
        label.setToolTip(text)
        label.setText(QFontMetrics(label.font()).elidedText(text, Qt.TextElideMode.ElideRight, self.detail_width))

    def refresh(self, *_):
        room = self.room
        key = self.pet.manager.selected if self.pet else None
        if key != self.outfit:
            self.outfit = key
            self.style_bubble()
        self.value('服装', self.pet.manager.config[key]['name'] if key else '正在加载')
        motion = '正在加载'
        if self.pet and self.pet.loaded:
            config = self.pet.manager.config[key]
            pose = self.avatar.metrics.get('pose')
            motion_id = next((action for action, entry in config['motions'].items() if pose and entry['group'] == pose), None)
            if motion_id is None and self.pet.current_motion is not None and self.pet.current_motion.value == 'look_around':
                motion_id = 'look_around'
            motion = self.pet.manager.action_labels(key).get(motion_id or 'idle', '自然待机')
        self.value('动作', motion)

        voice = getattr(room, 'pro_voice', None)
        settings = getattr(room, 'pro_settings', {})
        playback = voice.player.player.playbackState() if voice else None
        speaking = playback == QMediaPlayer.PlaybackState.PlayingState
        response = room.pending_id is not None
        music_playing, music_paused, title = self.music_info()
        if speaking:
            voice_state = '正在播放语音'
        elif playback == QMediaPlayer.PlaybackState.PausedState:
            voice_state = '已暂停'
        elif voice and voice.active:
            voice_state = '正在合成'
        elif settings.get('muted'):
            voice_state = '已静音'
        else:
            voice_state = '待机' if settings.get('speech_enabled') else '未开启'
        self.value('语音', voice_state)
        music_status = '尚未播放'
        if music_playing:
            music_status = '播放中' + (f' · {title}' if title else '')
        elif music_paused:
            music_status = '已暂停' + (f' · {title}' if title else '')
        self.value('音乐', music_status)
        busy_caption = '正在回复' if room.pending_answer else '正在思考'
        self.state.setText('正在说话' if speaking else busy_caption if response else '音乐播放中' if music_playing else '在这里')
        greeting = WELCOME[self.greeting_event]
        if speaking:
            greeting = '正在把回复念给你听。'
        elif response:
            greeting = '稍等一下，TT 正在想。' if not room.pending_answer else 'TT 正在回复你。'
        elif music_playing:
            greeting = '要不要一起听会儿音乐？'
        elif self.layout_mode == 'mini':
            greeting = '想聊点什么？' if self.greeting_event in ('first', 'new') else '我在这里。'
        self.welcome.setText(greeting)
        self.meta.setText(f'本对话上次回复 · {self.last_chat}' if self.last_chat else '当前对话 · 新话题')
        self.voice_button.setEnabled(voice is not None and not response)
        self.motion_button.setEnabled(bool(self.pet and self.pet.loaded))
        self.music_button.setEnabled(bool(self.pet and hasattr(self.pet, 'open_music_bubble')))
        for action, button in zip(self.more_actions, self.quick_buttons):
            action.setEnabled(button.isEnabled())

    def layout_for_room(self):
        mode = dashboard_mode(self.room.size())
        wide = self.room.width() >= 670
        if mode != self.layout_mode or wide != self.wide:
            self.wide = wide
            self.layout_mode = mode
            mini = mode == 'mini'
            compact = mode == 'compact'
            self.detail_width = 79 if mini and wide else 107 if compact or not wide else 118
            self.grid.removeWidget(self.details)
            if wide:
                self.grid.addWidget(self.details, 0, 2, Qt.AlignmentFlag.AlignVCenter)
                self.details.setFixedWidth(146 if mini else 165 if compact else 194 if mode in ('wide','large') else 178)
            else:
                self.grid.addWidget(self.details, 1, 0, 1, 3)
                self.details.setMinimumWidth(0)
                self.details.setMaximumWidth(16777215)
            self.grid.setHorizontalSpacing(8 if mini else 10 if compact else 12)
            self.grid.setContentsMargins(9, 5 if mini else 8, 11, 5 if mini else 8)
            self.words.setSpacing(3 if mini or compact or not wide else 5)
            self.detail_grid.setVerticalSpacing(2 if mini else 4 if compact or not wide else 6)
            self.detail_grid.setContentsMargins(8, 4 if mini else 7, 8, 4 if mini else 7)
            self.set_details_visible(not self.compact and not self.transition_running())
            self.style_title()
            for column in range(4):
                self.detail_grid.setColumnStretch(column, 0)
            for index, (key, value) in enumerate(self.values.items()):
                row, column = (index, 0) if wide else (index//2, (index%2)*2)
                self.detail_grid.removeWidget(self.keys[index])
                self.detail_grid.removeWidget(value)
                self.detail_grid.addWidget(self.keys[index], row, column)
                self.detail_grid.addWidget(value, row, column+1)
                self.detail_grid.setColumnStretch(column+1, 1)
            columns = 2 if compact else len(self.quick_buttons)
            self.more_button.setVisible(mini)
            for index, button in enumerate(self.quick_buttons):
                button.setVisible(not mini)
                self.button_grid.removeWidget(button)
                self.button_grid.addWidget(button, index//columns, index%columns)
            self.refresh()
        if not self.transition_running():
            self.size_avatar(self.target_height())

    def transition_running(self):
        return self.animation.state() == QVariantAnimation.State.Running

    def target_height(self):
        if self.compact:
            return 69
        if self.layout_mode == 'mini':
            return min(114, max(83, round(self.room.height()*.176)))
        if self.layout_mode == 'compact':
            return min(138, max(111, round(self.room.height()*.193)))
        return min(208, round(self.room.height()*.235))

    def size_avatar(self, height):
        limit = 295 if self.wide else max(135, round((self.room.width()-46)*.43))
        self.avatar.limit_frame(min(limit, round(height*1.25)), int(height))

    def set_compact(self, compact, animate=False):
        changed = compact != self.compact
        self.compact = compact
        self.toggle_button.setText('展开' if compact else '收起')
        self.toggle_button.setAccessibleName('展开人物' if compact else '收起人物')
        self.style_title()
        if changed:
            self.avatar.options.setVisible(not compact)
        if compact:
            self.bubble.hide()
        if animate and changed and self.isVisible():
            self.set_details_visible(False)
            self.animation.stop()
            self.animation.setStartValue(self.avatar.height())
            self.animation.setEndValue(self.target_height())
            self.animation.start()
        else:
            self.finish_transition()

    def set_details_visible(self, visible):
        for widget in (self.details, self.welcome, self.actions):
            widget.setVisible(visible)
        self.bubble_slot.setVisible(visible and self.layout_mode in ('standard','wide','large'))
        self.meta.setVisible(visible and self.layout_mode in ('standard','wide','large'))
        self.bubble.setVisible(visible and self.bubble_timer.isActive() and self.avatar.effects)

    def style_title(self):
        size = 17 if self.compact else 18 if self.layout_mode == 'mini' else 21 if self.layout_mode == 'compact' else 24
        self.name.setStyleSheet(f'font-size: {size}px')

    def finish_transition(self):
        self.set_details_visible(not self.compact)
        self.layout_for_room()

    def toggle(self):
        self.set_compact(not self.compact, animate=True)
