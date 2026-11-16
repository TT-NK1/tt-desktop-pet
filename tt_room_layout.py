
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QActionGroup


class EnterToSendFilter(QObject):
    def __init__(self, window):
        super().__init__(window.input)
        self.window = window
        self.composing = False
        self.committing = False

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.InputMethod:
            self.composing = bool(event.preeditString())
            self.committing = bool(event.commitString())
            return False
        if kind == QEvent.Type.FocusOut:
            self.composing = False
            self.committing = False
        if kind == QEvent.Type.KeyRelease:
            self.committing = False
        if kind == QEvent.Type.KeyPress:
            if (event.key() == Qt.Key.Key_Escape and self.window.pending_id is not None
                    and QApplication.activePopupWidget() is None
                    and QApplication.activeModalWidget() is None):
                self.window.stop()
                return True
            enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            if not enter or watched is not self.window.input:
                self.committing = False
                return False
            if self.composing or self.committing:
                return False
            modifiers = event.modifiers()
            if modifiers & (
                Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            ):
                return False
            if not event.isAutoRepeat() and self.window.send_button.isEnabled():
                self.window.send()
            return True
        return False

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLayout, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)
from tt_conversation import ConversationUI
from tt_conversation_style import DARK_STYLE, STYLE
from tt_clash_proxy import AUTO, CONNECTION_PREFERENCE, DIRECT, routes
from tt_theme import DARK, DARK_UI, LIGHT, OPTIONS, ThemeManager, theme_icon
from tt_window_layout import MINIMUM, WindowLayoutManager

WINDOW_SIZE_STYLE = """
QToolButton#windowSizeButton { background: #eee6f5; border: 1px solid #ddcee9; border-radius: 7px; }
QToolButton#windowSizeButton:hover { background: #e9dcf3; }
QToolButton#windowSizeButton::menu-indicator { image: none; width: 0; }
"""

def install(base, window_class):
    class RoomWindow(ConversationUI, window_class):
        def __init__(self):
            super().__init__()
            self.theme = ThemeManager(self)
            settings = QWidget()
            settings.setObjectName('ttSettings')
            settings.setLayout(self.layout())
            self.settings_page = settings
            header = settings.layout().itemAt(0).widget()
            conversation = QWidget()
            conversation.setObjectName('conversation')
            self.build_conversation(conversation, header)
            self.enter_filter = EnterToSendFilter(self)
            self.input.installEventFilter(self.enter_filter)
            self.stop_button.installEventFilter(self.enter_filter)
            self.tabs = QTabWidget()
            self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            self.tabs.addTab(conversation, "聊天")
            self.settings_scroll = QScrollArea()
            self.settings_scroll.setWidgetResizable(True)
            self.settings_scroll.setWidget(settings)
            self.tabs.addTab(self.settings_scroll, "聊天与语音设置")
            settings.layout().addWidget(self.build_features())
            self.window_layout = WindowLayoutManager(self)
            self.setup_appearance()
            layout = QVBoxLayout(self)
            layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.addWidget(self.tabs)
            self._light_style = self.styleSheet() + STYLE + WINDOW_SIZE_STYLE
            self.theme.changed.connect(self.apply_theme)
            self.apply_theme('light', False)
            self.setMinimumSize(MINIMUM)
            self.layout_portrait()

        def build_features(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(8)
            network = QHBoxLayout()
            network.addWidget(QLabel("聊天网络"))
            self.network_connection = QComboBox()
            self.network_connection.addItem("自动（直连优先）", AUTO)
            self.network_connection.addItem("仅直连", DIRECT)
            network.addWidget(self.network_connection, 1)
            layout.addLayout(network)
            choice = self.db.execute("SELECT value FROM tt_preferences WHERE name=?",
                                     (CONNECTION_PREFERENCE,)).fetchone()
            routes.mode = DIRECT if choice == (DIRECT,) else AUTO
            self.network_connection.setCurrentIndex(self.network_connection.findData(routes.mode))
            self.network_connection.currentIndexChanged.connect(self.save_network_connection)
            route_note = QLabel("自动模式在直连失败时接入 Clash。Clash 已关闭时，TT 会按需启动自己的代理。")
            route_note.setWordWrap(True)
            layout.addWidget(route_note)
            self.web_enabled = QCheckBox("启用自动联网查")
            self.web_enabled.setChecked(self.db.execute(
                "SELECT value FROM tt_preferences WHERE name='web_research'"
            ).fetchone() == ('yes',))
            self.web_enabled.toggled.connect(self.save_web_enabled)
            layout.addWidget(self.web_enabled)
            note = QLabel("开启后，TT 会判断问题是否需要查证，并自动读取公开网页或视频链接。"
                          "点击回复里的来源可在 Edge 打开。")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch()
            return page

        def add_service_page(self, page, title):
            return self.tabs.insertTab(self.tabs.indexOf(self.settings_scroll), page, title)

        def save_web_enabled(self, enabled):
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO tt_preferences VALUES (?,?)",
                                ("web_research", "yes" if enabled else "no"))

        def save_network_connection(self, _index):
            mode = self.network_connection.currentData()
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO tt_preferences VALUES (?,?)",
                                (CONNECTION_PREFERENCE, mode))
            routes.mode = mode

        def setup_appearance(self):
            self.theme_controls = {}
            self.theme_button = QToolButton(self.tabs)
            self.theme_button.setObjectName('themeButton')
            self.theme_button.setFixedSize(30, 27)
            self.theme_button.setIconSize(QSize(18, 18))
            self.theme_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.theme_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(self.theme_button)
            group = QActionGroup(menu)
            self.theme_button.setMenu(menu)
            corner = QWidget(self.tabs)
            tools = QHBoxLayout(corner)
            tools.setContentsMargins(0, 0, 0, 0)
            tools.setSpacing(4)
            tools.addWidget(self.window_layout.button)
            tools.addWidget(self.theme_button)
            self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
            for mode, label, _, symbol in OPTIONS:
                action = menu.addAction(label)
                action.setCheckable(True)
                group.addAction(action)
                action.triggered.connect(lambda _checked, value=mode: self.theme.set_mode(value))
                self.theme_controls[mode] = (action, symbol)

        def apply_theme(self, mode, dark):
            palette = DARK if dark else LIGHT
            self.setProperty('ttThemeDark', dark)
            stylesheet = self._light_style
            if dark:
                stylesheet += palette.css(DARK_UI + DARK_STYLE + """
QToolButton#windowSizeButton { background: $button; border-color: $border; }
QToolButton#windowSizeButton:hover { background: $hover; }
""")
            self.setPalette(self.theme.widget_palette())
            self.setStyleSheet(stylesheet)
            self.dashboard.apply_theme(dark)
            self.chat_messages.apply_theme(palette)
            if hasattr(self, 'music_page'):
                self.music_page.apply_theme(palette)
            for value, (action, _) in self.theme_controls.items():
                action.setChecked(value == mode)
            action, symbol = self.theme_controls[mode]
            self.theme_button.setIcon(theme_icon(symbol, palette.accent, self.theme_button))
            label = action.text()
            system = (' · 当前深色' if dark else ' · 当前浅色') if mode == 'system' else ''
            self.theme_button.setToolTip(f'外观 · {label}{system}')
            self.theme_button.setAccessibleName(f'切换主题 · {label}{system}')
            self.window_layout.set_color(palette.accent)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if hasattr(self, 'window_layout'):
                self.window_layout.resized()
                self.layout_portrait()

        def moveEvent(self, event):
            super().moveEvent(event)
            if hasattr(self, 'window_layout'):
                self.window_layout.moved()

        def nativeEvent(self, event_type, message):
            if hasattr(self, 'window_layout'):
                hit = self.window_layout.native_resize_hit(event_type, message)
                if hit is not None:
                    return True, hit
            return super().nativeEvent(event_type, message)

        def showEvent(self, event):
            super().showEvent(event)
            self.window_layout.show()
            self.dashboard.opened_window()

        def hideEvent(self, event):
            self.window_layout.save()
            super().hideEvent(event)

        def keyPressEvent(self, event):
            if (event.key() == Qt.Key.Key_Escape and self.tabs.currentIndex() == 0
                    and self.pending_id is not None):
                self.stop()
                event.accept()
                return
            super().keyPressEvent(event)

    original_pet = base.ChatPet

    class DockPet(original_pet):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.chat.window_layout.bind(self.manager)
            self.chat.theme.bind(self.manager)
            self.chat.dashboard.bind(self)
            from tt_music_services import MusicServiceManager
            from tt_music_settings import MusicPlatformsPage
            self.music_services = MusicServiceManager(self.manager, self)
            self.music_page = self.chat.music_page = MusicPlatformsPage(self.music_services, self.chat)
            self.chat.add_service_page(self.music_page, '音乐平台')
            self.music_page.apply_theme(self.chat.theme.palette)
            self.music_services.changed.connect(self.chat.dashboard.media_changed)
            from tt_bubble import ThoughtBubble
            if not hasattr(self, "bubble"):
                self.bubble = ThoughtBubble(self)
                self.window_manager.track(self.bubble,"thought")
            self.quick_bar = self.bubble
            self.quick_bar.setObjectName("ttQuickBar")
            dock_layout = self.quick_bar.content
            self.quick_actions = QWidget(self.quick_bar)
            grid = QGridLayout(self.quick_actions)
            grid.setContentsMargins(0, 0, 0, 0)
            dock_layout.addWidget(self.quick_actions, alignment=Qt.AlignmentFlag.AlignHCenter)
            for index, (label, callback) in enumerate((
                ("聊天", lambda: self.open_room(0)),
                ("媒体", self.open_music_bubble),
                ("天气", self.open_weather_bubble),
                ("塔罗", self.open_tarot),
            )):
                button = QPushButton(label)
                if label == "媒体":
                    button.setToolTip("打开媒体气泡")
                elif label == "塔罗":
                    button.setToolTip("打开每日塔罗")
                button.clicked.connect(callback)
                grid.addWidget(button, index // 2, index % 2)
            settings_shortcut = QPushButton("⚙", self.quick_bar)
            settings_shortcut.clicked.connect(lambda: self.open_room(self.chat.tabs.indexOf(self.chat.settings_scroll)))
            self.quick_bar.settings_shortcut = settings_shortcut
            self.quick_actions.hide()
            self.quick_bar.resize_to_content()
            self.bar_hide_timer = QTimer(self, singleShot=True, interval=650)
            self.bar_hide_timer.timeout.connect(self.hide_bar_if_left)
            self.quick_bar.installEventFilter(self)
            self.renderer.ready.connect(self.quick_bar.sync_outfit)
            from tt_weather import WeatherService
            from tt_weather_ui import WeatherSettings
            self.weather = WeatherService(self.manager,self)
            self.weather.busy = self.weather_busy
            self.weather.reminder.connect(self.weather_notice)
            self.weather_settings = WeatherSettings(self.weather,self.chat)
            self.chat.add_service_page(self.weather_settings, '天气')
            self.install_tarot()

        def install_tarot(self):
            import sqlite3
            from tt_tarot_companion import TarotCompanion
            self.tarot = None
            self.tarot_error = ""
            try:
                self.tarot = TarotCompanion(self)
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                self.tarot_error = str(error)
            if self.tarot:
                self.chat.settings_page.layout().addWidget(
                    self.tarot.create_settings(self.chat.settings_page)
                )

        def open_tarot(self):
            if self.tarot:
                self.tarot.open()
            else:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "每日塔罗", "塔罗资源或数据库无法加载。\n" + self.tarot_error)

        def open_weather_bubble(self):
            from tt_weather_ui import WeatherBubble
            if not hasattr(self,'weather_bubble'):
                self.weather_bubble = WeatherBubble(self,self.weather,
                                                    lambda:self.open_room(self.chat.tabs.indexOf(self.weather_settings)),
                                                    self.weather_brief)
                self.window_manager.track(self.weather_bubble,"weather")
            self.window_manager.show_panel(self.weather_bubble)
            if not self.weather.data:
                self.weather.refresh()
            elif self.weather_bubble.fresh:
                from tt_voice_profiles import with_reference
                chat = self.chat
                settings = chat.pro_settings
                if (settings['speech_enabled'] and not settings['muted'] and settings['volume']
                        and not chat.busy() and not chat.pro_voice.active):
                    chat.pro_warm_reply_voice(with_reference(chat.db,settings))

        def weather_busy(self):
            chat = self.chat
            return (not self.isVisible() or chat.busy() or chat.isActiveWindow()
                    or bool(chat.input.toPlainText().strip()) or chat.pro_voice.active)

        def weather_notice(self, icon, text, speak, speech_parts=None):
            self.quick_bar.set_weather(icon,text)
            self.window_manager.show_overlay(self.quick_bar)
            if speak:
                return self.speak_weather(speech_parts or (text,))
            return None

        def weather_brief(self):
            import time
            from tt_weather import forecasts, label, number, summary
            from tt_weather_ui import hint
            if (not self.weather.cfg['enabled'] or not self.weather.data or self.weather.error
                    or time.time()-self.weather.updated>self.weather.cfg['interval']*60+180):
                return (), '请先刷新天气预报，再试一次。'
            data, cfg = self.weather.data, self.weather.cfg
            current, _, _ = forecasts(data)
            icon, condition = label(number(current,'weather_code'))
            temperature = number(current,'temperature_2m')
            conditions = []
            if temperature is not None:
                conditions.append(f'现在约{temperature:g}度')
            if condition != '天气状况未知':
                conditions.append(condition)
            text = '，'.join(conditions)+'。' if conditions else ''
            reminder = hint(data,cfg)
            if reminder:
                icon, important = reminder
            else:
                _, important = summary(data,cfg)
            return self.weather_notice(icon,text+important,True,(text,important))

        def speak_weather(self,texts):
            from pathlib import Path
            from tt_voice_profiles import with_reference
            chat = self.chat
            voice = chat.pro_voice
            settings = with_reference(chat.db,chat.pro_settings)
            if chat.busy() or voice.active:
                return (), 'TT 正在回复或朗读，请稍后再播报。'
            if not settings['speech_enabled']:
                return (), '请在 TT 语音设置中打开语音总开关。'
            if settings['muted'] or not settings['volume']:
                return (), '请取消 TT 应用内静音并调高语音音量。'
            reference = settings['voice_reference']
            if not reference or not Path(reference).is_file():
                return (), '请在 TT 语音设置中选择可用的本地参考音色。'
            settings['voice'] = True
            files = []
            for text in filter(None,texts):
                path = voice.play(text,settings,kind='weather')
                if not path or not voice.player.has_audio((path,)):
                    if voice.active:
                        voice.stop()
                    return (), voice.status if voice.status.startswith('失败') else '本地语音未能开始播报。'
                files.append(path)
            return tuple(files), ''

        def open_music_bubble(self):
            from tt_music_bubble import MusicBubble
            if not hasattr(self, "music_bubble"):
                self.music_bubble = MusicBubble(self)
                self.chat.dashboard.bind_music(self.music_bubble)
                self.window_manager.track(self.music_bubble,"music")
            self.window_manager.show_panel(self.music_bubble)

        def shutdown(self):
            try:
                if getattr(self, "tarot", None):
                    self.tarot.close()
                self.chat.window_layout.unbind()
                if hasattr(self,'weather'):
                    self.weather.close()
                if hasattr(self, 'music_services'):
                    self.music_services.close()
            finally:
                super().shutdown()

        def cleaned(self, ack):
            try:
                if getattr(self, "tarot", None):
                    self.tarot.close()
                self.chat.window_layout.unbind()
                self.weather.close()
            finally:
                super().cleaned(ack)

        def select_expression(self, name):
            super().select_expression(name)
            if hasattr(self, 'chat') and name in self.manager.config[self.manager.selected]['expressions']:
                self.chat.dashboard.expression_changed(name)
        def resize_quick_bar(self):
            self.quick_bar.resize_to_content()
            self.place_bar()

        def hide_bar_if_left(self):
            if self.underMouse() or self.quick_bar.underMouse():
                return
            self.quick_actions.hide()
            self.resize_quick_bar()

        def show_quick_actions(self):
            self.bar_hide_timer.stop()
            self.quick_actions.show()
            self.resize_quick_bar()
            self.window_manager.show_overlay(self.quick_bar)

        def enterEvent(self, event):
            super().enterEvent(event)
            if hasattr(self, "quick_bar"):
                self.show_quick_actions()

        def leaveEvent(self, event):
            super().leaveEvent(event)
            if hasattr(self, "bar_hide_timer"):
                self.bar_hide_timer.start()

        def eventFilter(self, watched, event):
            if watched is getattr(self, "quick_bar", None):
                if event.type() == QEvent.Type.Enter:
                    self.show_quick_actions()
                elif event.type() == QEvent.Type.Leave:
                    self.bar_hide_timer.start()
            return super().eventFilter(watched, event)

        def open_music_settings(self):
            self.open_room(self.chat.tabs.indexOf(self.music_page))

        def open_room(self, tab):
            self.chat.tabs.setCurrentIndex(tab)
            if self.chat.isMinimized():
                self.chat.showNormal()
            else:
                self.chat.show()
            self.chat.raise_()
            self.chat.activateWindow()
            if tab == 0:
                self.chat.input.setFocus()

        def place_bar(self):
            if not hasattr(self, "quick_bar"):
                return
            self.quick_bar.follow_pet()

        def showEvent(self, event):
            super().showEvent(event)
            if hasattr(self, "quick_bar"):
                self.quick_actions.setVisible(self.underMouse())
                self.place_bar()

        def hideEvent(self, event):
            super().hideEvent(event)
            if hasattr(self, "quick_bar"):
                self.bar_hide_timer.stop()
                self.quick_actions.hide()

        def closeEvent(self, event):
            super().closeEvent(event)
            if event.isAccepted():
                self.quick_bar.close()

    base.ChatWindow = RoomWindow
    base.ChatPet = DockPet
