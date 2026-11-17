"""Read replies aloud and deliver occasional local chat greetings."""
import json
import time

from PySide6.QtCore import QTime, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                               QLabel, QPushButton, QSpinBox, QTimeEdit)

from tt_proactive import DEFAULTS, Scheduler
from tt_proactive_voice import GAME_PAUSED_STATUS, Voice
from tt_voice_profiles import add_settings, prepare_db, with_reference
from tt_live_reply import LiveReply
from tt_natural_speech import STYLES
from tt_avatar import DECLARED_DELIVERY, reply_delivery, requested_style
from tt_media_share import MediaShare
from tt_share_content import music_reply
from tt_multilingual import RECOGNITION_READ_RULE, direct_read_text, recognition_read_text, speech_language


def install_voice(base):
    previous = base.ChatWindow
    if getattr(previous, '_proactive_installed', False):
        return

    class ProactiveWindow(previous):
        _proactive_installed = True

        def __init__(self):
            super().__init__()
            self.db.execute('CREATE TABLE IF NOT EXISTS tt_preferences (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self.pro_settings = dict(DEFAULTS)
            self.pro_state = {}
            data = {}
            try:
                row = self.db.execute("SELECT value FROM tt_preferences WHERE name='proactive_v1'").fetchone()
                data = json.loads(row[0]) if row else {}
                for key, default in DEFAULTS.items():
                    value = data.get('settings', {}).get(key, default)
                    if type(value) is type(default):
                        self.pro_settings[key] = value
                for key in ('start', 'end', 'dnd_start', 'dnd_end'):
                    self.pro_settings[key] = max(0, min(1439, self.pro_settings[key]))
                # Migrate the old proactive-only voice opt-in.
                if 'speech_enabled' not in data.get('settings', {}):
                    self.pro_settings['speech_enabled'] = self.pro_settings['voice']
                for key, low, high in (('interval', 1, 1440), ('limit', 1, 24), ('volume', 0, 100), ('rate', -100, 100), ('emotion_strength', 0, 100)):
                    self.pro_settings[key] = max(low, min(high, self.pro_settings[key]))
                if self.pro_settings['speech_style'] not in (*STYLES, 'auto'):
                    self.pro_settings['speech_style'] = 'auto'
                state = data.get('state', {})
                if isinstance(state, dict):
                    for key, value in state.items():
                        if key in ('last', 'snooze', 'count', 'unanswered') and type(value) in (int, float):
                            self.pro_state[key] = max(0, value)
                        elif key in ('day', 'quiet_day', 'phrase') and isinstance(value, str):
                            self.pro_state[key] = value
                        elif key == 'sent' and isinstance(value, list):
                            self.pro_state[key] = [x for x in value if type(x) in (int, float)]
            except (ValueError, TypeError, AttributeError):
                self.pro_settings = dict(DEFAULTS)
                self.pro_state = {}
            prepare_db(self.db)
            with self.db:
                self.db.execute('CREATE TABLE IF NOT EXISTS tt_reply_emotion ('
                                'turn_id INTEGER PRIMARY KEY, emotion TEXT NOT NULL)')
                self.db.execute('CREATE TRIGGER IF NOT EXISTS tt_remove_reply_emotion AFTER DELETE ON turns '
                                'BEGIN DELETE FROM tt_reply_emotion WHERE turn_id=OLD.id; END')
            self.pro_voice = Voice(self)
            self.pro_live = None
            self.pro_voice_warm_timer = QTimer(self)
            self.pro_voice_warm_timer.setSingleShot(True)
            self.pro_voice_warm_timer.setInterval(400)
            self.pro_voice_warm_timer.timeout.connect(self.pro_prepare_draft_voice)
            settings_layout = getattr(self, 'settings_page', self).layout()
            self.pro_delivering = False
            self.pro_speech_serial = 0
            self.pro_voice_status = QLabel(self.pro_voice.status)
            self.pro_voice_status.setWordWrap(True)
            self.pro_voice.listeners.append(self.pro_voice_status.setText)
            self.pro_voice.listeners.append(self.pro_voice_state)
            if hasattr(self, 'chat_messages'):
                self.connect_voice_controls()
            settings_layout.addWidget(self.pro_voice_status)
            self.pro_scheduler = Scheduler(self.pro_settings, self.pro_state, self.save_proactive, time.time())
            self.media = MediaShare(self, base)
            self.input.textChanged.connect(self.pro_activity)
            self.bubble_reply.connect(self.pro_reply)
            self.pro_button = QPushButton('主动聊天与语音设置')
            self.pro_button.clicked.connect(self.pro_dialog)
            settings_layout.addWidget(self.pro_button)
            media_button = QPushButton('主动聊天与媒体分享')
            media_button.clicked.connect(self.media.settings_dialog)
            settings_layout.addWidget(media_button)
            self.pro_timer = QTimer(self)
            self.pro_timer.setInterval(30000)
            self.pro_timer.timeout.connect(self.pro_tick)
            self.pro_timer.start()
            from PySide6.QtWidgets import QApplication
            QApplication.instance().aboutToQuit.connect(self.pro_shutdown)

        def save_proactive(self):
            self.db.execute('INSERT OR REPLACE INTO tt_preferences VALUES (?,?)',
                            ('proactive_v1', json.dumps(dict(settings=self.pro_settings, state=self.pro_state), ensure_ascii=False)))
            self.db.commit()

        def pro_shutdown(self):
            self.pro_timer.stop()
            self.pro_voice_warm_timer.stop()
            self.media.shutdown()
            self.pro_stop_speech()
            self.pro_voice.shutdown()

        def pro_stop_speech(self):
            if self.pro_live:
                self.pro_live.stop()
            self.pro_voice.stop()

        def pro_voice_state(self, status):
            if (status.startswith('失败') or status == GAME_PAUSED_STATUS) and self.pro_live:
                self.pro_live.stop()

        def pro_activity(self):
            self.pro_scheduler.activity(time.time())
            self.media.cancel()
            if getattr(self.pro_voice, 'scene', None) == 'proactive' and self.pro_voice.active:
                self.pro_stop_speech()
            if self.input.hasFocus():
                self.pro_voice_warm_timer.start()

        def showEvent(self, event):
            super().showEvent(event)
            if hasattr(self, 'tabs') and self.tabs.currentIndex() == 0:
                self.pro_prepare_reply_voice()

        def pro_prepare_draft_voice(self):
            if self.input.hasFocus() and self.input.toPlainText().strip():
                self.pro_prepare_reply_voice()

        def pro_prepare_reply_voice(self):
            settings = self.pro_settings
            if (settings['speech_enabled'] and settings['reply_voice'] and not settings['muted'] and settings['volume']
                    and self.pro_voice.player.process is None):
                self.pro_warm_reply_voice(with_reference(self.db, settings))

        def pro_warm_reply_voice(self, settings):
            try:
                self.pro_voice.warmup(settings.get('voice_reference'))
                if self.pro_voice.game_paused and self.pro_live:
                    self.pro_live.stop()
            except (OSError, RuntimeError):
                pass  # The real speech request reports any worker startup failure.

        def resolve_reply_delivery(self, prompt, text):
            live = self.pro_live
            turn = (self.session, getattr(self, 'pending_id', None))
            if (not self.pro_delivering and live and live.turn == turn
                    and live.delivery is not None):
                return live.delivery
            emotion = getattr(getattr(self, 'worker', None), 'reply_expression', None) if self.pending_id is not None else None
            return reply_delivery(prompt, text, self.pro_settings['speech_style'], declared=emotion)

        @staticmethod
        def speech_delivery_settings(settings, delivery, *, reply_prompt=None):
            automatic = (reply_prompt is not None and settings['speech_style'] == 'auto'
                         and requested_style(reply_prompt) is None)
            return dict(settings, speech_style=delivery.style, avatar_mode=delivery.expression,
                        automatic_chat_delivery=automatic)

        def pro_stream_piece(self, phrase, live, reply):
            phrase = music_reply(phrase, streaming=True)[0]
            if not phrase:
                return
            if live.delivery is None:
                live.delivery = self.resolve_reply_delivery(self.pending_prompt, reply)
            settings = self.speech_delivery_settings(live.settings, live.delivery, reply_prompt=self.pending_prompt)
            self.pro_voice.stream_piece(phrase, settings, reply_turn=live.turn)

        def save_result(self, answer, status):
            turn = self.pending_id
            declared = getattr(self.worker, 'reply_expression', None)
            result = super().save_result(answer, status)
            if turn is not None and status == 'completed' and declared in DECLARED_DELIVERY:
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO tt_reply_emotion VALUES (?,?)', (turn, declared))
            return result

        def replay_reply(self, turn, text, settings, *, regenerate=False):
            reply_turn = self.session, turn
            if not regenerate and self.pro_voice.reply_turn == reply_turn and self.pro_voice.active:
                self.status.setText('这条语音正在生成或播放。')
                return
            text = recognition_read_text(text)
            row = self.db.execute(
                'SELECT t.prompt, e.emotion FROM turns t LEFT JOIN tt_reply_emotion e ON e.turn_id=t.id '
                'WHERE t.id=? AND t.account=? AND t.session=?',
                (turn, self.identity, self.session),
            ).fetchone()
            prompt, declared = row or ('', None)
            delivery = reply_delivery(prompt, text, settings['speech_style'], declared=declared)
            speech = self.speech_delivery_settings(settings, delivery, reply_prompt=prompt or None)
            speech['regenerate_speech'] = regenerate
            self.pro_stop_speech()
            if prompt:
                # Match the same sentence boundaries used during automatic speech.
                complete = LiveReply(reply_turn, settings)
                parts = list(complete.feed(text))
                if tail := complete.finish(text):
                    parts.append(tail)
            else:
                parts = [text]
            for part in parts or [text]:
                self.pro_voice.play(part, speech, 'replay', reply_turn=reply_turn)

        def start_worker(self, worker):
            if getattr(worker, 'mode', None) == 'chat':
                self.media.cancel()
                self.pro_voice_warm_timer.stop()
                self.pro_stop_speech()
                self.pro_live = None
                settings = with_reference(self.db, self.pro_settings)
                recognition = getattr(self, 'recognition', None)
                reading_requested = recognition and recognition.reading_requested(self.pending_id)
                if reading_requested:
                    worker.payload['instructions'] += '\n' + RECOGNITION_READ_RULE
                if not reading_requested and not self.pending_images:
                    worker.read_aloud_text = direct_read_text(self.pending_request or self.pending_prompt)
                if (settings['speech_enabled']
                        and settings['reply_voice'] and not settings['muted']
                        and not reading_requested
                        and not getattr(worker, 'read_aloud_text', '')
                        and self.pending_id is not None):
                    self.pro_live = LiveReply((self.session, self.pending_id), settings)
                    if speech_language(self.pending_prompt) not in ('ja', 'ko'):
                        self.pro_warm_reply_voice(settings)
            return super().start_worker(worker)

        def add_delta(self, text):
            super().add_delta(text)
            live = self.pro_live
            if live and live.turn == (self.session, self.pending_id):
                for phrase in live.feed(text):
                    self.pro_stream_piece(phrase, live, self.pending_answer)

        def cancel_reply_worker(self):
            super().cancel_reply_worker()
            self.pro_stop_speech()

        def failed(self, message):
            if self.pro_live and self.pro_live.turn == (self.session, self.pending_id):
                self.pro_stop_speech()
            return super().failed(message)

        def new_chat(self):
            self.media.cancel()
            self.pro_stop_speech()
            self.pro_live = None
            return super().new_chat()

        def restore_session(self, session, title):
            self.media.cancel()
            return super().restore_session(session, title)

        def pro_reply(self, text):
            # Normal replies and proactive deliveries share the same cooldown.
            self.pro_scheduler.defer(time.time())
            if not self.pro_delivering:
                self.pro_speech_serial += 1
                # The completion signal is emitted before save_result clears this ID.
                turn_id = getattr(self, 'pending_id', None)
                recognition = getattr(self, 'recognition', None)
                requested_read = recognition and recognition.take_read(turn_id)
                explicit_read = bool(getattr(getattr(self, 'worker', None), 'read_aloud_text', ''))
                live = self.pro_live
                if live and live.turn == (self.session, turn_id) and not (requested_read or explicit_read):
                    tail = live.finish(text)
                    if tail:
                        self.pro_stream_piece(tail, live, text)
                    return
                if requested_read:
                    text = recognition_read_text(text)
                settings = with_reference(self.db, self.pro_settings)
                prompt = getattr(self, 'pending_prompt', '') if turn_id is not None else ''
                settings = self.speech_delivery_settings(
                    settings, self.resolve_reply_delivery(prompt, text), reply_prompt=prompt)
                self.pro_voice.play(text, settings, 'replay' if requested_read or explicit_read else 'reply',
                                    ('reply', self.session, 'turn', turn_id) if turn_id is not None
                                    else ('reply', self.session, 'event', self.pro_speech_serial),
                                    reply_turn=(self.session, turn_id) if turn_id is not None else None)

        def pro_busy(self):
            return (self.busy() or self.isActiveWindow() or self.media.watch is not None
                    or bool(self.input.toPlainText().strip()))

        def pro_tick(self):
            now = time.time()
            if self.pro_scheduler.tick(now, self.pro_busy()) and not self.media.try_proactive():
                self.pro_publish(now, self.pro_scheduler.message())

        def pro_publish(self, now, text, *, item=None, notify=True):
            with self.db:
                cursor = self.db.execute('INSERT INTO turns VALUES (NULL,?,?,?,?,?,?)',
                                (now, self.session, self.identity, '', text, 'proactive'))
                turn = cursor.lastrowid
                if item:
                    self.media.save_card(turn, item)
            context = self.media.context_text(text, item)
            self.history = (self.history + [('', context, ())])[-4:]
            if hasattr(self, 'chat_messages'):
                self.chat_messages.add_record((turn, now, '', text, 'proactive'), paced=True)
            else:
                self.view.appendPlainText('\nTT（主动）：\n' + text)
            if notify:
                self.pro_delivering = True
                try:
                    self.bubble_reply.emit(text)
                finally:
                    self.pro_delivering = False
                settings = with_reference(self.db, self.pro_settings)
                delivery = reply_delivery('', text, settings['speech_style'])
                self.pro_voice.play(text, self.speech_delivery_settings(settings, delivery),
                                    message_id=('proactive', now))
            return turn

        def pro_action(self, action):
            self.media.cancel()
            now = time.time()
            if action == 'quiet':
                self.pro_state['quiet_day'] = self.pro_scheduler.local(now).date().isoformat()
            elif action == 'snooze':
                self.pro_state['snooze'] = now + self.pro_settings['interval'] * 60
            self.pro_stop_speech()
            self.pro_scheduler.defer(now)

        def pro_dialog(self):
            dialog = QDialog(self)
            dialog.setWindowTitle('主动聊天与语音')
            form = QFormLayout(dialog)
            note = QLabel('主动问候由本机调度。可在「主动聊天与媒体分享」选择来源。\n'
                          '语音开启后会播放声音；仅使用本机语音引擎，不向外部服务发送文字。\n'
                          '遵循电脑当前时区；开始结束相同时表示全天。无法检测系统静音，请使用应用内静音。')
            note.setWordWrap(True)
            form.addRow(note)

            def changed(key, value):
                self.pro_settings[key] = value
                self.pro_stop_speech()
                if key == 'pause_voice_in_games':
                    self.pro_voice.apply_game_preference()
                if key in ('enabled', 'dnd', 'start', 'end', 'dnd_start', 'dnd_end'):
                    self.media.cancel()
                if key in ('speech_enabled', 'reply_voice', 'voice', 'muted', 'volume',
                           'rate', 'voice_name', 'speech_style',
                           'emotion_strength', 'fast_speech', 'pause_voice_in_games'):
                    self.save_proactive()
                else:
                    self.pro_scheduler.defer(time.time())

            for key, label in (('enabled', '允许 TT 主动聊天'), ('speech_enabled', '语音总开关'),
                               ('reply_voice', '普通回复朗读'), ('voice', '主动消息语音播报（会播放声音）'),
                               ('pause_voice_in_games', '游戏中暂停语音并释放显存'),
                               ('muted', '应用内静音'), ('dnd', '立即免打扰'),
                               ('fast_speech', '快速合成（语气可能略有变化）')):
                widget = QCheckBox(label)
                if key == 'pause_voice_in_games':
                    widget.setToolTip('识别游戏后停止合成和播放并卸载本地模型。切出游戏仍保持暂停，游戏退出后恢复。')
                widget.setChecked(self.pro_settings[key])
                widget.toggled.connect(lambda value, k=key: changed(k, value))
                form.addRow(widget)
            for key, label in (('start', '可打扰开始'), ('end', '可打扰结束'),
                               ('dnd_start', '免打扰开始'), ('dnd_end', '免打扰结束')):
                minute = self.pro_settings[key]
                widget = QTimeEdit(QTime(minute // 60, minute % 60))
                widget.setDisplayFormat('HH:mm')
                widget.timeChanged.connect(lambda value, k=key: changed(k, value.hour() * 60 + value.minute()))
                form.addRow(label, widget)
            for key, label, low, high in (('interval', '最小间隔（分钟）', 1, 1440), ('limit', '每日最多次数', 1, 24)):
                widget = QSpinBox()
                widget.setRange(low, high)
                widget.setValue(self.pro_settings[key])
                widget.valueChanged.connect(lambda value, k=key: changed(k, value))
                form.addRow(label, widget)
            volume = QSpinBox()
            volume.setObjectName('speech_volume')
            volume.setRange(0, 100)
            volume.setValue(self.pro_settings['volume'])
            volume.valueChanged.connect(lambda value: changed('volume', value))
            form.addRow('音量', volume)
            rate = QSpinBox()
            rate.setObjectName('speech_rate')
            rate.setRange(-100, 100)
            rate.setValue(self.pro_settings['rate'])
            rate.valueChanged.connect(lambda value: changed('rate', value))
            form.addRow('语速（0 为正常）', rate)
            add_settings(form, dialog, self.db, self.pro_settings, changed)
            style = QComboBox(dialog)
            style.setObjectName('speech_style')
            style.addItem('随语境调整', 'auto')
            for key, preset in STYLES.items():
                style.addItem(preset.label, key)
            style.setCurrentIndex(max(0, style.findData(self.pro_settings['speech_style'])))
            style.currentIndexChanged.connect(lambda _: changed('speech_style', style.currentData()))
            form.addRow('说话风格', style)
            emotion = QSpinBox(dialog)
            emotion.setObjectName('emotion_strength')
            emotion.setRange(0, 100)
            emotion.setSuffix('%')
            emotion.setValue(self.pro_settings['emotion_strength'])
            emotion.valueChanged.connect(lambda value: changed('emotion_strength', value))
            form.addRow('情绪强度（0 为关闭）', emotion)
            preview = QPushButton('试听（需开启语音总开关并取消静音）')
            def preview_voice():
                self.pro_stop_speech()
                if not self.pro_settings['speech_enabled'] or self.pro_settings['muted']:
                    self.pro_voice._status('试听未播放：请开启语音总开关并取消应用内静音')
                    return
                text = '你好，我是提提。想聊的时候叫我就好。'
                settings = with_reference(self.db, self.pro_settings)
                delivery = reply_delivery('', text, settings['speech_style'])
                self.pro_voice.play(text, self.speech_delivery_settings(settings, delivery), 'preview')
            preview.clicked.connect(preview_voice)
            form.addRow(preview)
            voice_status = QLabel(self.pro_voice.status)
            voice_status.setWordWrap(True)
            self.pro_voice.listeners.append(voice_status.setText)
            form.addRow(voice_status)
            stop = QPushButton('停止朗读')
            stop.clicked.connect(self.pro_stop_speech)
            form.addRow(stop)
            for action, label in (('snooze', '稍后提醒（延后一个最小间隔）'), ('quiet', '今天安静')):
                button = QPushButton(label)
                button.clicked.connect(lambda checked=False, a=action: self.pro_action(a))
                form.addRow(button)
            dialog.finished.connect(lambda _: self.pro_stop_speech())
            dialog.exec()
            self.pro_voice.listeners.remove(voice_status.setText)

    base.ChatWindow = ProactiveWindow
