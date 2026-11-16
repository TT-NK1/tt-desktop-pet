"""Chat layout and actions over the existing request worker and local database."""

import json
import time
from datetime import datetime
from pathlib import Path
from sqlite3 import Error as DatabaseError

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from tt_chat_images import ChatImage, IMAGE_NOTE, MAX_ATTACHMENTS, read_image
from tt_chat_messages import ChatMessages
from tt_character_dashboard import CharacterDashboard
from tt_plus_search import plain_words, without_links
from tt_theme import qt_file_dialog
from tt_voice_profiles import with_reference


TEXT_SUFFIXES = {'.txt', '.md', '.markdown', '.csv', '.json', '.log', '.xml', '.yaml', '.yml', '.tsv', '.srt', '.vtt'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}


def small_button(text, callback, layout, name='chatTool'):
    button = QPushButton(text)
    button.setObjectName(name)
    button.clicked.connect(callback)
    layout.addWidget(button)
    return button


class ModelLabel(QLabel):
    def set_model(self, name):
        self.setToolTip(name)
        self.update_text()

    def update_text(self):
        self.setText(self.fontMetrics().elidedText(
            self.toolTip() or '尚未选择模型', Qt.TextElideMode.ElideRight, self.contentsRect().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_text()


class ConversationUI:
    def build_conversation(self, page, header):
        self.attachments = []
        self.delta_timer = QTimer(self)
        self.delta_timer.setSingleShot(True)
        self.delta_timer.setInterval(65)
        self.delta_timer.timeout.connect(self.flush_chat_delta)
        self.setAcceptDrops(True)
        self.input.setAcceptDrops(False)
        self.input.viewport().setAcceptDrops(False)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 7, 12, 11)
        layout.setSpacing(8)
        tools = QHBoxLayout()
        self.model_label = ModelLabel()
        self.model_label.setObjectName('chatModel')
        self.model_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tools.addWidget(self.model_label, 1)
        self.history_button = small_button('历史会话', self.show_history, tools)
        self.new_button.setText('新建对话')
        self.new_button.setObjectName('chatTool')
        tools.addWidget(self.new_button)
        layout.addLayout(tools)
        self.dashboard = self.chat_header = CharacterDashboard(self)
        self.character_state = self.dashboard.state
        self.portrait_button = self.dashboard.toggle_button
        self.avatar_caption.setParent(self.dashboard)
        self.avatar_caption.hide()
        header.deleteLater()
        layout.addWidget(self.dashboard)

        self.chat_messages = ChatMessages(self)
        layout.addWidget(self.chat_messages, 1)
        self.view.hide()
        self.status.setObjectName('chatStatus')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)

        composer = QFrame()
        composer.setObjectName('composer')
        compose = QVBoxLayout(composer)
        compose.setContentsMargins(9, 6, 9, 6)
        compose.setSpacing(5)
        self.attachment_list = QWidget()
        self.attachment_layout = QVBoxLayout(self.attachment_list)
        self.attachment_layout.setContentsMargins(0, 0, 0, 0)
        self.attachment_layout.setSpacing(3)
        self.attachment_list.hide()
        compose.addWidget(self.attachment_list)
        self.input.setObjectName('chatInput')
        self.input.setPlaceholderText('写给 TT…')
        compose.addWidget(self.input)
        actions = QHBoxLayout()
        small_button('＋ 附件', self.pick_attachments, actions).setToolTip(
            '可同时选择文本和图片。发送前可预览图片，历史会话仅保存文字。')
        actions.addStretch()
        self.pause_voice = small_button('暂停朗读', self.toggle_voice, actions)
        self.stop_voice = small_button('停止朗读', self.stop_voice_now, actions)
        self.pause_voice.hide()
        self.stop_voice.hide()
        self.stop_button.setObjectName('chatTool')
        actions.addWidget(self.stop_button)
        self.send_button.setText('发送  ↵')
        self.send_button.setMinimumWidth(81)
        actions.addWidget(self.send_button)
        compose.addLayout(actions)
        layout.addWidget(composer)

        self.input.document().documentLayout().documentSizeChanged.connect(self.resize_editor)
        self.input.textChanged.connect(self.update_buttons)
        self.models.currentTextChanged.connect(self.update_model_label)
        self.chat_messages.reset()
        self.resize_editor()
        self.update_model_label()
        self.update_buttons()

    def update_model_label(self, *_):
        self.model_label.set_model(self.models.currentText())

    def resize_editor(self, *_):
        base = 60 if self.height() < 650 else 84 if self.height() >= 830 else 76
        lines = self.input.document().documentLayout().documentSize().height()
        height = max(base, min(124, round(lines*self.input.fontMetrics().lineSpacing()) + 14))
        if self.input.height() != height or self.input.minimumHeight() != height:
            self.input.setFixedHeight(height)

    def layout_portrait(self):
        self.dashboard.layout_for_room()
        self.resize_editor()

    def fill_suggestion(self, text):
        if self.input.toPlainText().strip():
            self.input.appendPlainText(text)
        else:
            self.input.setPlainText(text)
        self.input.setFocus()

    def update_buttons(self):
        super().update_buttons()
        if not hasattr(self, 'chat_messages'):
            return
        self.history_button.setEnabled(not self.busy())
        self.stop_button.setVisible(self.pending_id is not None)
        self.send_button.setEnabled(self.send_button.isEnabled()
                                    and (bool(self.attachments) or bool(self.input.toPlainText().strip())))

    def show_pending_input(self, created, prompt, followup):
        self.delta_timer.stop()
        if followup:
            self.chat_messages.add_followup(self.pending_id, created, prompt)
        else:
            self.chat_messages.add_record((self.pending_id, created, prompt, '', 'pending'))
        self.chat_messages.to_latest()
        self.character_state.setText('正在思考')
        self.layout_portrait()

    def queue_chat_delta(self):
        if not self.delta_timer.isActive():
            self.delta_timer.start()

    def receive_phase(self, message):
        if super().receive_phase(message) and not self.pending_answer:
            self.character_state.setText(message.rstrip('…'))

    def flush_chat_delta(self):
        answer = (plain_words(self.pending_answer, markdown=True) if self._reply_links
                  else without_links(self.pending_answer, markdown=True))
        if answer:
            self.chat_messages.set_reply(answer, True)
            self.character_state.setText('正在生成')

    def save_result(self, answer, status):
        if self.pending_id is not None:
            if status == 'completed' and (item := getattr(self.worker, 'media_item', None)):
                self.media.save_card(self.pending_id, item)
            self.delta_timer.stop()
            missing = '已停止生成。' if status == 'stopped' else '这条回复未完成，可以重新生成。'
            self.chat_messages.set_reply(answer or missing,
                                         False, paced=status == 'completed')
            self.character_state.setText('已回复' if status == 'completed' else '回复中断')
            if status == 'completed':
                self.dashboard.conversation_event('replied')
        result = super().save_result(answer, status)
        if status == 'completed':
            self.dashboard.load_conversation_info()
        return result

    def models_loaded(self, models, identity):
        if identity != self.identity:
            self.clear_image_draft()
        super().models_loaded(models, identity)
        self.chat_messages.reset()
        self.dashboard.load_conversation_info()
        self.layout_portrait()

    def new_chat(self):
        super().new_chat()
        self.clear_image_draft()
        self.chat_messages.reset()
        self.character_state.setText('待机')
        self.dashboard.conversation_event('new')
        self.layout_portrait()
        self.input.setFocus()

    def copy_reply(self, text):
        QApplication.clipboard().setText(text)
        self.status.setText('已复制回复')

    def read_reply(self, turn, text, *, regenerate=False):
        if self.busy():
            self.status.setText('回复生成完成后可以朗读。')
            return
        settings = with_reference(self.db, self.pro_settings)
        if not settings['speech_enabled'] or settings['muted'] or not settings.get('voice_reference'):
            self.status.setText('请在「聊天与语音设置」启用语音、解除静音并选择参考音色。')
            return
        self.replay_reply(turn, text, settings, regenerate=regenerate)
        self.refresh_voice_controls()

    def toggle_voice(self):
        media = self.pro_voice.player.player
        if media.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            media.pause()
        elif media.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            media.play()

    def stop_voice_now(self):
        self.pro_stop_speech()
        self.refresh_voice_controls()

    def refresh_voice_controls(self, *_):
        media = self.pro_voice.player.player
        playing = media.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        paused = media.playbackState() == QMediaPlayer.PlaybackState.PausedState
        self.pause_voice.setVisible(playing or paused)
        self.pause_voice.setText('继续朗读' if paused else '暂停朗读')
        self.stop_voice.setVisible(playing or paused or self.pro_voice.active)
        if playing or paused:
            self.character_state.setText('正在播放语音' if playing else '语音已暂停')
        elif self.character_state.text() in ('正在播放语音', '语音已暂停'):
            self.character_state.setText('已回复' if self.chat_messages.currentIndex() else '待机')

    def connect_voice_controls(self):
        self.pro_voice.player.player.playbackStateChanged.connect(self.refresh_voice_controls)
        self.pro_voice.listeners.append(self.voice_changed)

    def voice_changed(self, status):
        self.refresh_voice_controls()
        active_status = ('语音加载中', '生成中', '播放中')
        if status.startswith((*active_status, '失败', '没有可朗读')) and (not self.busy() or self.pending_id is None):
            self.status.setText(status)
        elif status in ('语音待机', '已停止') and self.status.text().startswith(active_status):
            self.status.setText('朗读结束。' if status == '语音待机' else '已停止朗读。')

    def completed(self, answer):
        super().completed(answer)
        self.voice_changed(self.pro_voice.status)

    def retry_turn(self, turn):
        if self.busy():
            self.status.setText('请等待当前回复结束。')
            return
        if not self.consent.isChecked() or not self.models.currentData():
            self.status.setText('请先在设置中授权并选择模型。')
            return
        item = self.db.execute('SELECT prompt FROM turns WHERE id=? AND account=? AND session=?',
                               (turn, self.identity, self.session)).fetchone()
        if item and item[0]:
            images = self.recent_images.get(turn, ())
            if IMAGE_NOTE.search(item[0]) and not images:
                self.status.setText('这轮图片已不在当前会话中，请重新添加图片再发送。')
                return
            self.send_prompt(IMAGE_NOTE.sub('', item[0]), context=self.session_context(self.session, before=turn),
                             clear_input=False, images=images)

    def session_context(self, session, before=None):
        query = ("SELECT id,prompt,answer,status FROM turns WHERE session=? AND account=? "
                 "AND ((status IN ('completed','stopped') AND prompt<>'') OR (status='proactive' AND answer<>''))")
        args = [session, self.identity]
        if before is not None:
            query += ' AND id<?'
            args.append(before)
        rows = self.db.execute(query + ' ORDER BY id DESC LIMIT 4', args).fetchall()
        context = []
        for turn, prompt, answer, status in reversed(rows):
            images = self.recent_images.get(turn, ())
            if status == 'stopped':
                answer = ''
            elif hasattr(self, 'media'):
                answer = self.media.context_text(answer, self.media.card_for(turn))
            context.append((IMAGE_NOTE.sub('', prompt) if images else prompt, answer, images))
        return context

    def vote_for(self, turn):
        row = self.db.execute('SELECT value FROM tt_preferences WHERE name=?', (f'chat_vote:{turn}',)).fetchone()
        if not row:
            return ''
        kind, number = json.loads(row[0])
        saved = self.db.execute('SELECT 1 FROM tt_speech_feedback WHERE id=? AND account=? AND kind=?',
                               (number, self.identity, kind)).fetchone()
        return kind if saved else ''

    def vote_reply(self, turn, kind):
        item = self.db.execute('SELECT prompt,answer FROM turns WHERE id=? AND account=? AND session=?',
                               (turn, self.identity, self.session)).fetchone()
        previous = self.vote_for(turn)
        if not item or not item[1]:
            return previous
        name = f'chat_vote:{turn}'
        row = self.db.execute('SELECT value FROM tt_preferences WHERE name=?', (name,)).fetchone()
        try:
            with self.db:
                if row:
                    self.db.execute('DELETE FROM tt_speech_feedback WHERE id=? AND account=?', (json.loads(row[0])[1], self.identity))
                    self.db.execute('DELETE FROM tt_preferences WHERE name=?', (name,))
                if previous == kind:
                    kind = ''
                if kind:
                    number = self.db.execute('INSERT INTO tt_speech_feedback (account,kind,question,answer,preferred,created) VALUES (?,?,?,?,?,?)',
                                             (self.identity, kind, *item, '', time.time())).lastrowid
                    self.db.execute('INSERT INTO tt_preferences VALUES (?,?)', (name, json.dumps([kind, number])))
        except DatabaseError:
            self.status.setText('反馈保存失败，请重试。')
            return previous
        self.status.setText('已保存反馈' if kind else '已取消反馈')
        return kind

    def session_key(self, session):
        return f'chat_title:{self.identity}:{session}'

    def show_history(self):
        if self.busy():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('历史会话')
        dialog.resize(570, 410)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        layout.addWidget(listing)
        empty = QLabel('此账户暂无历史会话。')
        layout.addWidget(empty)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)

        def refresh():
            listing.clear()
            rows = self.db.execute('''SELECT t.session, MAX(t.created), COUNT(*),
                (SELECT first.prompt FROM turns first WHERE first.account=t.account
                 AND first.session=t.session AND first.prompt<>'' ORDER BY first.id LIMIT 1)
                FROM turns t WHERE t.account=? GROUP BY t.session ORDER BY MAX(t.id) DESC''', (self.identity,))
            for session, date, count, prompt in rows:
                saved = self.db.execute('SELECT value FROM tt_preferences WHERE name=?', (self.session_key(session),)).fetchone()
                title = saved[0] if saved else (' '.join((prompt or '').split())[:36] or 'TT 的陪伴')
                stamp = datetime.fromtimestamp(date).strftime('%m 月 %d 日 %H:%M')
                item = QListWidgetItem(f'{title}\n{stamp}  ·  {count} 条记录')
                item.setData(Qt.ItemDataRole.UserRole, (session, title))
                listing.addItem(item)
            listing.setCurrentRow(0)
            empty.setVisible(not listing.count())
            for button in operations:
                button.setEnabled(bool(listing.count()))

        def selected():
            item = listing.currentItem()
            return item.data(Qt.ItemDataRole.UserRole) if item else (None, '')

        def restore():
            session, title = selected()
            if session:
                self.restore_session(session, title)
                dialog.accept()

        def rename():
            session, title = selected()
            text, accepted = QInputDialog.getText(dialog, '重命名会话', '会话名称', text=title)
            if session and accepted and text.strip():
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO tt_preferences VALUES (?,?)', (self.session_key(session), text.strip()[:60]))
                refresh()

        def remove():
            session, title = selected()
            if session and QMessageBox.question(dialog, '删除会话', f'删除「{title}」的本地消息记录？',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self.delete_session(session)
                refresh()

        operations = [small_button('恢复会话', restore, buttons), small_button('重命名', rename, buttons), small_button('删除', remove, buttons)]
        buttons.addStretch()
        small_button('关闭', dialog.close, buttons)
        listing.itemDoubleClicked.connect(restore)
        refresh()
        dialog.exec()

    def restore_session(self, session, title):
        self.stop_voice_now()
        self.avatar_idle()
        if self.session != session:
            self.recent_images.clear()
            self.clear_image_draft()
        self.session = session
        self.history = self.session_context(session)
        self.chat_messages.reset()
        self.dashboard.load_conversation_info()
        self.layout_portrait()
        self.status.setText(f'已打开「{title}」')

    def delete_session(self, session):
        with self.db:
            votes = self.db.execute("SELECT value FROM tt_preferences WHERE name IN (SELECT 'chat_vote:'||id FROM turns WHERE session=? AND account=?)", (session, self.identity)).fetchall()
            self.db.executemany('DELETE FROM tt_speech_feedback WHERE id=? AND account=?',
                                [(json.loads(value)[1], self.identity) for (value,) in votes])
            self.db.execute("DELETE FROM tt_preferences WHERE name IN (SELECT 'chat_vote:'||id FROM turns WHERE session=? AND account=?)", (session, self.identity))
            self.db.execute('DELETE FROM turns WHERE session=? AND account=?', (session, self.identity))
            self.db.execute('DELETE FROM tt_preferences WHERE name=?', (self.session_key(session),))
        if self.session == session:
            self.new_chat()

    def pick_attachments(self):
        extensions = ' '.join('*'+suffix for suffix in sorted(TEXT_SUFFIXES | IMAGE_SUFFIXES))
        paths, _ = QFileDialog.getOpenFileNames(self, '添加附件', '', f'文本和图片 ({extensions})',
                                              options=qt_file_dialog(self))
        self.add_attachments(paths)

    def dragEnterEvent(self, event):
        if self.input.isEnabled() and event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if self.input.isEnabled() and event.mimeData().hasUrls():
            self.add_attachments([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    def add_attachments(self, paths):
        if not self.input.isEnabled():
            return
        errors = []
        for filename in paths:
            if len(self.attachments) >= MAX_ATTACHMENTS:
                errors.append(f'一次最多添加 {MAX_ATTACHMENTS} 个附件。')
                break
            path = Path(filename)
            if any(item['path'] == path for item in self.attachments):
                continue
            entry = dict(path=path, name=path.name, text='', image=None, chat_image=None)
            try:
                if path.suffix.lower() in TEXT_SUFFIXES:
                    with path.open('rb') as source:
                        data = source.read(48001)
                    if len(data) > 48000:
                        raise ValueError('请先截取需要发送的文本。')
                    try:
                        entry['text'] = data.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        entry['text'] = data.decode('gb18030')
                    if not entry['text'].strip() or '\x00' in entry['text'] or len(entry['text']) > 5200:
                        raise ValueError('请选择非空文本，单个附件最多 5200 字符。')
                elif path.suffix.lower() in IMAGE_SUFFIXES and path.is_file():
                    image = read_image(path)
                    entry['chat_image'] = ChatImage(image)
                    entry['image'] = image.scaled(QSize(600, 450), Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation)
                else:
                    raise ValueError('请选择支持的文本文件或图片。')
            except (UnicodeError, OSError, ValueError) as error:
                errors.append(f'{path.name} · {error}' if isinstance(error, ValueError) and not isinstance(error, UnicodeError) else f'{path.name} 无法读取。')
                continue
            self.attachments.append(entry)
        self.render_attachments()
        if errors:
            self.status.setText(errors[0])

    def render_attachments(self):
        while self.attachment_layout.count():
            self.attachment_layout.takeAt(0).widget().deleteLater()
        for item in self.attachments:
            row = QFrame()
            row.setObjectName('attachmentRow')
            line = QHBoxLayout(row)
            line.setContentsMargins(6, 1, 2, 1)
            image = item['image']
            if image is not None:
                thumb = QLabel()
                thumb.setPixmap(QPixmap.fromImage(image).scaled(29, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                line.addWidget(thumb)
            name = QFontMetrics(self.font()).elidedText(item['name'], Qt.TextElideMode.ElideMiddle, 156)
            label = QLabel(name + (' · 图片待发送' if image is not None else f" · 待发送 {len(item['text'])} 字符"))
            label.setTextFormat(Qt.TextFormat.PlainText)
            line.addWidget(label, 1)
            small_button('预览', lambda _, value=item: self.preview_attachment(value), line)
            small_button('移除', lambda _, value=item: self.remove_attachment(value), line)
            self.attachment_layout.addWidget(row)
        self.attachment_list.setVisible(bool(self.attachments))
        self.update_buttons()

    def remove_attachment(self, item):
        self.attachments.remove(item)
        self.render_attachments()

    def clear_image_draft(self):
        if any(item['chat_image'] is not None for item in self.attachments):
            self.attachments[:] = [item for item in self.attachments if item['chat_image'] is None]
            self.render_attachments()

    def preview_attachment(self, item):
        from PySide6.QtWidgets import QPlainTextEdit, QScrollArea
        dialog = QDialog(self)
        dialog.setWindowTitle(item['name'])
        layout = QVBoxLayout(dialog)
        dialog.resize(530, 380)
        if item['image'] is not None:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setPixmap(QPixmap.fromImage(item['image']))
            scroll.setWidget(label)
            layout.addWidget(scroll)
        else:
            preview = QPlainTextEdit(item['text'])
            preview.setReadOnly(True)
            layout.addWidget(preview)
        dialog.exec()

    def prepare_prompt(self):
        prompt = self.input.toPlainText().strip()
        files = [item for item in self.attachments if item['text']]
        if not prompt and self.prepare_images():
            prompt = '请看看我发的图片，说说你看到了什么。'
        if not files:
            return prompt
        chunks = [prompt or '请帮我总结以下文本附件。']
        chunks.extend(f"【文本附件 · {item['name']}】\n{item['text']}\n【附件结束】" for item in files)
        return '\n\n'.join(chunks)

    def prepare_images(self):
        return tuple(item['chat_image'] for item in self.attachments if item['chat_image'] is not None)

    def send(self):
        sent = super().send()
        if sent:
            self.attachments.clear()
            self.render_attachments()
            self.input.setFocus()
        return sent
