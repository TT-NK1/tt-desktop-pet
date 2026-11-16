"""Widgets for streamed replies and paged local conversation history."""

from datetime import datetime
from math import ceil
import re

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontMetrics, QTextCursor, QTextDocument, QTextFormat, QTextOption
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)
from tt_theme import LIGHT
from tt_media_share import MediaCard


PARAGRAPH_BREAK = re.compile(r'\n[ \t]*\n+')
STRUCTURED_REPLY = re.compile(
    r'(?m)^\s*(?:#{1,6}\s|>|[-*+]\s|\d+[.)、]\s*|\|)'
    r'|\x60|https?://|!?\[[^\n\]]+\]\(|\[\d+\]'
)


def reply_parts(text):
    parts = PARAGRAPH_BREAK.split(text.strip(), maxsplit=3)
    if (2 <= len(parts) <= 3 and len(text) <= 260
            and all('\n' not in part and len(part) <= 120 for part in parts)
            and not STRUCTURED_REPLY.search(text)):
        return parts
    return [text]


class MessageText(QTextBrowser):
    def __init__(self, assistant, theme=LIGHT):
        super().__init__()
        self.assistant = assistant
        self.theme = theme
        self.setObjectName('messageText')
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self.open_link)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(1)
        self.document().setDefaultFont(QFont('Microsoft YaHei UI', 11))
        self.document().documentLayout().documentSizeChanged.connect(self.adjust_height)

    @staticmethod
    def open_link(url):
        if url.scheme() in ('https', 'http') and url.host():
            edge = QUrl('microsoft-edge:' + url.toString(QUrl.ComponentFormattingOption.FullyEncoded))
            if not QDesktopServices.openUrl(edge):
                QDesktopServices.openUrl(url)

    def loadResource(self, _type, _name):
        return None

    def wheelEvent(self, event):
        event.ignore()

    def update_text(self, text, streaming):
        if self.assistant and not streaming:
            document = self.document()
            features = QTextDocument.MarkdownFeature
            document.setMarkdown(text, features.MarkdownDialectGitHub | features.MarkdownNoHTML)
            self.apply_document_colors()
        else:
            self.setPlainText(text)
        self.adjust_height()

    def apply_document_colors(self):
        document = self.document()
        prop = QTextFormat.Property
        block = document.begin()
        while block.isValid():
            fmt = block.blockFormat()
            if fmt.hasProperty(prop.BlockCodeLanguage) or fmt.hasProperty(prop.BlockCodeFence):
                fmt.setBackground(QColor(self.theme.code))
                fmt.setLeftMargin(8)
                fmt.setRightMargin(8)
                QTextCursor(block).setBlockFormat(fmt)
            block = block.next()

    def adjust_height(self, *_):
        height = ceil(self.document().size().height()) + 2
        if self.height() != height or self.minimumHeight() != height:
            self.setFixedHeight(height)


class MessageRow(QFrame):
    part_shown = Signal()

    def __init__(self, turn, created, assistant, body, room, *, streaming=False, can_retry=False,
                 split=False, paced=False):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.assistant = assistant
        self.can_retry = can_retry
        self.room = room
        self.turn = turn
        self.content = body
        self.buttons = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.column = QWidget()
        self.column.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.stack = column = QVBoxLayout(self.column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        clock = datetime.fromtimestamp(created).strftime('%H:%M')
        self.sender = QLabel(f'TT   {clock}' if assistant else f'{clock}   你')
        self.sender.setObjectName('messageSender')
        self.sender.setAlignment(Qt.AlignmentFlag.AlignLeft if assistant else Qt.AlignmentFlag.AlignRight)
        column.addWidget(self.sender)
        self.bodies = []
        self.body = self.add_bubble()
        self.media_card = None
        self.remaining = []
        self.reveal_timer = None
        if assistant:
            self.action_bar = QWidget()
            self.action_bar.setObjectName('messageActions')
            self.action_bar.setProperty('active', False)
            actions = QHBoxLayout(self.action_bar)
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(2)
            callbacks = [('复制', lambda: room.copy_reply(self.content)),
                         ('朗读', lambda: room.read_reply(turn, self.content))]
            if can_retry:
                callbacks.append(('重新生成', lambda: room.retry_turn(turn)))
            for label, callback in callbacks:
                button = QPushButton(label)
                button.setMinimumWidth(42)
                button.clicked.connect(callback)
                if label == '朗读':
                    button.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
                    rerender = QAction('重新合成语音', button)
                    rerender.triggered.connect(lambda: room.read_reply(turn, self.content, regenerate=True))
                    button.addAction(rerender)
                actions.addWidget(button)
                self.buttons.append(button)
            if can_retry:
                self.up = QPushButton('赞')
                self.down = QPushButton('踩')
                for button, kind in ((self.up, 'liked'), (self.down, 'disliked')):
                    button.setCheckable(True)
                    button.clicked.connect(lambda _, vote=kind: self.vote(vote))
                    actions.addWidget(button)
                    self.buttons.append(button)
            actions.addStretch()
            column.addWidget(self.action_bar)
            if can_retry:
                self.show_vote(room.vote_for(turn))
            for widget in [self.body, self.body.viewport(), self.action_bar, *self.buttons]:
                widget.installEventFilter(self)
        if assistant:
            layout.addWidget(self.column)
            layout.addStretch(1)
        else:
            layout.addStretch(1)
            layout.addWidget(self.column)
        self.set_body(body, streaming, split=split, paced=paced)
        if assistant and not streaming:
            self.load_media_card()

    def load_media_card(self):
        media = getattr(self.room, 'media', None)
        if self.media_card is None and media and (item := media.card_for(self.turn)):
            self.media_card = MediaCard(item, media)
            self.stack.insertWidget(self.stack.indexOf(self.action_bar), self.media_card)

    def add_bubble(self):
        bubble = QFrame()
        bubble.setObjectName('ttMessage' if self.assistant else 'userMessage')
        inner = QVBoxLayout(bubble)
        inner.setContentsMargins(12, 8, 12, 8)
        theme = self.room.theme.palette if hasattr(self.room, 'theme') else LIGHT
        body = MessageText(self.assistant, theme)
        inner.addWidget(body)
        self.stack.insertWidget(1 + len(self.bodies), bubble)
        self.bodies.append(body)
        return body

    def set_body(self, text, streaming=False, *, split=False, paced=False):
        self.content = text
        first, *rest = reply_parts(text) if split else [text]
        self.body.update_text(first, streaming)
        if self.assistant:
            self.action_bar.setVisible(not streaming)
        if not streaming and rest:
            self.remaining = rest
            if paced:
                self.reveal_timer = QTimer(self)
                self.reveal_timer.setSingleShot(True)
                self.reveal_timer.timeout.connect(self.reveal_next)
                self.reveal_timer.start(440)
            else:
                self.finish_parts()

    def reveal_next(self):
        self.add_bubble().update_text(self.remaining.pop(0), False)
        self.part_shown.emit()
        if self.reveal_timer and self.remaining:
            self.reveal_timer.start(580)

    def finish_parts(self):
        if self.reveal_timer:
            self.reveal_timer.stop()
        while self.remaining:
            self.add_bubble().update_text(self.remaining.pop(0), False)
        self.part_shown.emit()

    def vote(self, kind):
        self.show_vote(self.room.vote_reply(self.turn, kind))

    def show_vote(self, kind):
        self.up.setChecked(kind == 'liked')
        self.down.setChecked(kind == 'disliked')

    def set_width(self, available):
        preferred = min(710, max(238, round(available * (0.82 if self.assistant else 0.7))))
        if not self.assistant:
            ideal = QFontMetrics(self.body.document().defaultFont()).horizontalAdvance(self.content[:110]) + 40
            preferred = min(preferred, max(132, ideal))
        self.column.setFixedWidth(min(available, preferred))

    def highlight_actions(self):
        active = self.underMouse() or any(button.hasFocus() for button in self.buttons)
        if self.assistant and self.action_bar.property('active') != active:
            self.action_bar.setProperty('active', active)
            for button in self.buttons:
                button.style().unpolish(button)
                button.style().polish(button)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.highlight_actions()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        QTimer.singleShot(0, self, self.highlight_actions)

    def eventFilter(self, _watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            QTimer.singleShot(0, self, self.highlight_actions)
        return False


class ChatMessages(QStackedWidget):
    PAGE = 32

    def __init__(self, room):
        super().__init__()
        self.room = room
        self.first_turn = None
        self.last_day = None
        self.rows = []
        self.active_row = None
        self.delivery_row = None
        self.follow = True
        self.moving = False
        self.follow_timer = QTimer(self, singleShot=True, interval=0)
        self.follow_timer.timeout.connect(self.scroll_bottom)
        self.older_anchor = None
        self.older_timer = QTimer(self, singleShot=True, interval=35)
        self.older_timer.timeout.connect(self.restore_older_anchor)
        self.empty = QWidget()
        self.empty.setObjectName('emptyChat')
        empty = QVBoxLayout(self.empty)
        empty.setContentsMargins(23, 12, 23, 12)
        empty.setSpacing(10)
        empty.addStretch()
        title = QLabel('今天想聊些什么？')
        title.setObjectName('emptyTitle')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(title)
        for label, prompt in [('陪我聊聊天', '陪我聊聊天吧。'), ('整理今天的计划', '帮我整理今天的计划。'),
                              ('陪我学习', '陪我学习吧，先问问我今天要学什么。')]:
            chip = QPushButton(label)
            chip.setObjectName('chatSuggestion')
            chip.clicked.connect(lambda _, value=prompt: self.room.fill_suggestion(value))
            empty.addWidget(chip, 0, Qt.AlignmentFlag.AlignHCenter)
        empty.addStretch()
        self.addWidget(self.empty)

        history = QWidget()
        flow = QVBoxLayout(history)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setSpacing(4)
        self.scroll = QScrollArea()
        self.scroll.setObjectName('chatHistory')
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)
        self.page = QWidget()
        self.page.setObjectName('messagePage')
        self.flow = QVBoxLayout(self.page)
        self.flow.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.flow.setContentsMargins(15, 10, 15, 10)
        self.flow.setSpacing(10)
        self.older = QPushButton('加载更早消息')
        self.older.clicked.connect(self.load_older)
        self.flow.addWidget(self.older, 0, Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setWidget(self.page)
        flow.addWidget(self.scroll, 1)
        self.latest = QPushButton('↓  返回最新消息')
        self.latest.clicked.connect(self.to_latest)
        flow.addWidget(self.latest, 0, Qt.AlignmentFlag.AlignRight)
        self.latest.hide()
        bar = self.scroll.verticalScrollBar()
        bar.valueChanged.connect(self.scroll_changed)
        bar.rangeChanged.connect(self.scroll_range_changed)
        bar.actionTriggered.connect(self.clear_older_anchor)
        self.addWidget(history)

    def records(self, before=None):
        query = 'SELECT id,created,prompt,answer,status FROM turns WHERE account=? AND session=?'
        args = [self.room.identity, self.room.session]
        if before is not None:
            query += ' AND id<?'
            args.append(before)
        return self.room.db.execute(query+' ORDER BY id DESC LIMIT ?', (*args, self.PAGE + 1)).fetchall()

    def minimumSizeHint(self):
        return QSize(250, 115)

    def apply_theme(self, palette):
        for row in self.rows:
            for body in row.bodies:
                if body.theme != palette:
                    body.theme = palette
                    body.apply_document_colors()

    def reset(self):
        self.clear_older_anchor()
        if self.delivery_row and self.delivery_row.reveal_timer:
            self.delivery_row.reveal_timer.stop()
        self.delivery_row = None
        while self.flow.count() > 1:
            widget = self.flow.takeAt(1).widget()
            widget.hide()
            widget.deleteLater()
        self.active_row = None
        self.rows.clear()
        self.last_day = self.first_turn = None
        records = self.records()
        self.older.setVisible(len(records) > self.PAGE)
        self.setCurrentIndex(int(bool(records)))
        for record in reversed(records[:self.PAGE]):
            self.add_record(record)
        self.to_latest()

    def add_record(self, record, index=None, *, paced=False):
        turn, created, prompt, answer, status = record
        self.first_turn = turn if self.first_turn is None else min(turn, self.first_turn)
        self.setCurrentIndex(1)
        index = self.flow.count() if index is None else index
        day = datetime.fromtimestamp(created).date()
        if day != self.last_day:
            date = QLabel('今天' if day == datetime.now().date() else day.strftime('%Y 年 %m 月 %d 日'))
            date.setProperty('messageDay', day.isoformat())
            date.setObjectName('messageDate')
            self.flow.insertWidget(index, date, 0, Qt.AlignmentFlag.AlignHCenter)
            self.last_day = day
            index += 1
        if prompt:
            inputs = self.room.db.execute('SELECT created,prompt FROM tt_turn_inputs WHERE turn_id=? ORDER BY rowid',
                                          (turn,)).fetchall()
            for sent_at, text in inputs or [(created, prompt)]:
                self.add_row(index, MessageRow(turn, sent_at, False, text, self.room))
                index += 1
        pending = status == 'pending' and turn == self.room.pending_id
        if (pending or paced) and self.delivery_row:
            self.delivery_row.finish_parts()
            self.delivery_row = None
        if answer or prompt or pending:
            missing = 'TT 正在思考…' if pending else '已停止生成。' if status == 'stopped' else '这条回复未完成。'
            row = MessageRow(turn, created, True, answer or missing, self.room,
                             streaming=pending, can_retry=bool(prompt),
                             split=status in ('completed', 'proactive'), paced=paced)
            row.part_shown.connect(self.maybe_follow)
            self.add_row(index, row)
            index += 1
            if pending:
                self.active_row = row
            if paced and row.remaining:
                self.delivery_row = row
        self.maybe_follow()
        return index

    def add_followup(self, turn, created, prompt):
        reply = self.active_row
        if reply is None:
            return
        self.add_row(self.flow.indexOf(reply), MessageRow(turn, created, False, prompt, self.room))
        self.rows.insert(self.rows.index(reply), self.rows.pop())
        self.set_reply('TT 正在重新思考…', True)

    def add_row(self, index, row):
        self.rows.append(row)
        row.set_width(self.message_width())
        self.flow.insertWidget(index, row)

    def load_older(self):
        records = self.records(self.first_turn)
        if not records:
            self.older.hide()
            return
        bar = self.scroll.verticalScrollBar()
        # Keep the first visible message in place while the prepended rows wrap.
        anchor = min((row for row in self.rows if row.geometry().bottom() >= bar.value()),
                     key=lambda row: row.y(), default=None)
        if anchor is not None:
            self.older_anchor = anchor, anchor.y() - bar.value()
        end_day, self.last_day = self.last_day, None
        index = 1
        self.follow = False
        for record in reversed(records[:self.PAGE]):
            index = self.add_record(record, index)
        first_visible = self.flow.itemAt(index)
        if first_visible and first_visible.widget().property('messageDay') == self.last_day.isoformat():
            duplicate = self.flow.takeAt(index).widget()
            duplicate.hide()
            duplicate.deleteLater()
        self.last_day = end_day
        self.older.setVisible(len(records) > self.PAGE)
        self.flow.activate()
        if self.older_anchor is not None:
            self.older_timer.start()

    def clear_older_anchor(self):
        self.older_anchor = None
        self.older_timer.stop()

    def restore_older_anchor(self):
        if self.older_anchor is not None:
            row, offset = self.older_anchor
            self.older_anchor = None
            self.scroll.verticalScrollBar().setValue(row.y() - offset)

    def message_width(self):
        return max(240, self.scroll.viewport().width() - 30)

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            for row in self.rows:
                row.set_width(self.message_width())
            self.maybe_follow()
        return super().eventFilter(watched, event)

    def scroll_changed(self, value):
        if not self.moving:
            self.follow = self.scroll.verticalScrollBar().maximum() - value <= 28
            self.latest.setVisible(not self.follow)

    def scroll_range_changed(self, minimum, maximum):
        if self.older_anchor is not None:
            self.older_timer.start()
            return
        if maximum == minimum:
            self.follow = True
            self.latest.hide()
        self.maybe_follow()

    def to_latest(self):
        self.clear_older_anchor()
        self.follow = True
        self.latest.hide()
        self.maybe_follow()

    def maybe_follow(self):
        if self.follow and not self.follow_timer.isActive():
            self.follow_timer.start()

    def scroll_bottom(self):
        if self.follow:
            self.moving = True
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            self.moving = False

    def set_reply(self, text, streaming, *, paced=False):
        if self.active_row is not None:
            self.active_row.set_body(text, streaming, split=streaming or paced, paced=paced)
            if not streaming:
                self.active_row.load_media_card()
                if self.active_row.remaining:
                    self.delivery_row = self.active_row
                self.active_row = None
            self.maybe_follow()
