"""Tarot reading, history and settings widgets; artwork is loaded on demand."""
from functools import lru_cache
import math

from PySide6.QtCore import QRectF, QSize, Qt, QTime, QTimer, QVariantAnimation
from PySide6.QtGui import QIcon, QImageReader, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QTabWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from tt_tarot import THEMES
from tt_theme import DARK_UI, LIGHT


TAROT_STYLE = """
QDialog, QWidget#tarotPage, QScrollArea > QWidget { background: $background; color: $text; }
QWidget { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Inter"; font-size: 13px; }
QLabel { color: $text; }
QLabel#tarotHeading { font-size: 18px; font-weight: 650; }
QLabel#tarotName { font-size: 25px; font-weight: 700; }
QLabel#tarotSection { font-size: 16px; font-weight: 650; }
QLabel#tarotMeta { color: $muted; }
QLabel#tarotKeywords { color: $accent; font-size: 14px; font-weight: 650; }
QWidget#tarotPanel {
    background: $surface; border: 1px solid $border; border-radius: 14px;
}
QWidget#tarotCardPanel {
    background: $card; border: 1px solid $border; border-radius: 14px;
}
QPushButton, QComboBox, QLineEdit, QListWidget {
    border: 1px solid $border; border-radius: 8px; padding: 6px 10px;
}
QListWidget::item { padding: 5px; }
QListWidget::item:selected { color: $selected_text; background: $highlight; }
QTabBar::tab { background: $surface; color: $muted; padding: 7px 16px; border-top-left-radius: 7px; border-top-right-radius: 7px; }
QTabBar::tab:selected { background: $card; color: $text; }
QScrollArea, QTabWidget::pane { border: none; background: $background; }
QScrollBar:vertical { width: 9px; margin: 2px; }
QScrollBar::handle:vertical { min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
"""


def label(text="", name=""):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    return widget


def button(text, callback, layout):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


def panel(layout, name="tarotPanel"):
    widget = QWidget()
    widget.setObjectName(name)
    body = QVBoxLayout(widget)
    body.setContentsMargins(15, 12, 15, 12)
    body.setSpacing(10)
    layout.addWidget(widget)
    return widget, body


def paragraph(text=""):
    widget = label(text)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def read_pixmap(path, size=None):
    reader = QImageReader(str(path))
    if size is not None and reader.size().isValid():
        reader.setScaledSize(reader.size().scaled(size, Qt.AspectRatioMode.KeepAspectRatio))
    return QPixmap.fromImage(reader.read())


@lru_cache(maxsize=22)
def thumbnail(path):
    return QIcon(read_pixmap(path, QSize(52, 78)))


class CardButton(QPushButton):
    def __init__(self, parent):
        super().__init__(parent)
        self.face = QPixmap()
        self.revealed = False
        self.animation = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=430)
        self.animation.valueChanged.connect(self.update)
        self.setMinimumHeight(245)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        height = min(self.height() - 8, (self.width() - 8) * 1.5)
        transition = self.animation.state() == QVariantAnimation.State.Running
        frame = self.animation.currentValue() if transition else 0
        width = height * 2 / 3 * (max(.04, abs(math.cos(math.pi * frame))) if transition else 1)
        rect = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
        if (self.revealed or transition and frame >= .5) and not self.face.isNull():
            painter.drawPixmap(rect, self.face, QRectF(self.face.rect()))
        else:
            palette = self.palette()
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            painter.drawRoundedRect(rect, 12, 12)
            painter.setPen(palette.color(QPalette.ColorRole.HighlightedText))
            if not transition:
                text = "牌图缺失" if self.revealed else "✧\n\n揭晓今日牌"
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if self.hasFocus():
            painter.setPen(self.palette().color(QPalette.ColorRole.Accent))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 12, 12)


class ThemedTarotDialog(QDialog):
    def __init__(self, parent, theme_manager=None, flags=Qt.WindowType.Dialog):
        super().__init__(parent, flags)
        self.theme_manager = theme_manager
        if theme_manager:
            theme_manager.changed.connect(self.apply_theme)
        self.apply_theme()

    def apply_theme(self, *_):
        theme = self.theme_manager
        colors = theme.palette if theme else LIGHT
        self.setPalette(colors.qt(theme.widget_palette() if theme else self.palette()))
        self.setStyleSheet(colors.css(DARK_UI + TAROT_STYLE))
        if theme:
            theme.update_caption(self)

    def showEvent(self, event):
        super().showEvent(event)
        if self.theme_manager:
            self.theme_manager.update_caption(self)


class ZoomDialog(ThemedTarotDialog):
    def __init__(self, path, title, parent):
        super().__init__(parent, getattr(parent, "theme_manager", None))
        self.setWindowTitle(title)
        self.original = read_pixmap(path)
        self.picture = label("牌图缺失或无法读取" if self.original.isNull() else "")
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.picture)
        self.full = QCheckBox("原始像素")
        self.full.toggled.connect(self.fit)
        layout = QVBoxLayout(self)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.full)
        self.resize(570, 740)

    def fit(self, *_):
        if not self.original.isNull():
            pixmap = self.original if self.full.isChecked() else self.original.scaled(
                self.scroll.viewport().size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.picture.setPixmap(pixmap)
            self.picture.resize(pixmap.size())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class TarotDialog(ThemedTarotDialog):
    def __init__(self, service, parent):
        super().__init__(parent, service.theme_manager)
        self.service, self.manager = service, service.manager
        self.draw = None
        self.follow_today = True
        self.offset = 0
        self.loaded_image = None
        self.setWindowTitle("TT 的每日塔罗")
        self.setMinimumSize(330, 420)
        self.resize(560, 720)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        scroll = QScrollArea()
        self.reading_scroll = scroll
        scroll.setWidgetResizable(True)
        daily = QWidget()
        daily.setObjectName("tarotPage")
        scroll.setWidget(daily)
        reading_page = QWidget()
        reading_page.setObjectName("tarotPage")
        page_layout = QVBoxLayout(reading_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll, 1)
        self.tabs.addTab(reading_page, "牌面")
        reading = QVBoxLayout(daily)
        reading.setContentsMargins(16, 12, 16, 14)
        reading.setSpacing(12)
        self.header_panel, header = panel(reading)
        header.addWidget(label("TT 的每日塔罗", "tarotHeading"))
        self.meta = label("", "tarotMeta")
        header.addWidget(self.meta)
        self.card_panel, artwork = panel(reading, "tarotCardPanel")
        self.card = CardButton(self)
        self.card.clicked.connect(self.reveal_or_zoom)
        self.card.animation.finished.connect(self.finish_reveal)
        artwork.addWidget(self.card)
        self.summary_panel, summary = panel(reading)
        self.title = label("", "tarotName")
        self.keywords = label("", "tarotKeywords")
        self.detail = paragraph()
        self.advice = paragraph()
        self.tt_message = paragraph()
        for widget in (self.title, self.keywords, self.detail, self.advice, self.tt_message):
            summary.addWidget(widget)
        controls = QHBoxLayout()
        summary.addLayout(controls)
        self.theme = QComboBox()
        self.theme.setAccessibleName("解读主题")
        for value, name in THEMES.items():
            self.theme.addItem(name, value)
        self.theme.currentIndexChanged.connect(self.change_theme)
        controls.addWidget(self.theme)
        self.favorite_button = button("收藏", self.toggle_favorite, controls)
        controls.addStretch(1)
        self.favorite_hint = label("", "tarotMeta")
        self.favorite_hint.hide()
        self.favorite_timer = QTimer(self, singleShot=True, interval=2200)
        self.favorite_timer.timeout.connect(self.favorite_hint.hide)
        summary.addWidget(self.favorite_hint)
        summary_actions = QHBoxLayout()
        summary.addLayout(summary_actions)
        self.voice_button = button("朗读简析", self.read_voice, summary_actions)
        summary_actions.addStretch(1)

        self.detail_panel, detailed = panel(reading)
        detailed.addWidget(label("TT 的个性化解读", "tarotSection"))
        detail_actions = QHBoxLayout()
        detailed.addLayout(detail_actions)
        self.ai_button = button("生成解读", self.request_ai, detail_actions)
        self.detail_read_button = button("朗读解读", self.read_detail, detail_actions)
        detail_actions.addStretch(1)
        self.ai_text = paragraph()
        detailed.addWidget(self.ai_text)
        self.status = label("", "tarotMeta")
        detailed.addWidget(self.status)
        self.return_button = button("回到今天", self.show_today, header)
        reading.addWidget(label("塔罗仅用于娱乐与自我反思。", "tarotMeta"))
        reading.addStretch()

        self.action_panel = QWidget()
        playback = QHBoxLayout(self.action_panel)
        playback.setContentsMargins(16, 4, 16, 6)
        self.voice_status = label("", "tarotMeta")
        playback.addWidget(self.voice_status, 1)
        self.stop_button = button("停止朗读", self.stop_voice, playback)
        page_layout.addWidget(self.action_panel)

        history = QWidget()
        history.setObjectName("tarotPage")
        items = QVBoxLayout(history)
        filters = QHBoxLayout()
        self.date_filter = QLineEdit()
        self.date_filter.setPlaceholderText("日期 YYYY-MM-DD")
        self.date_filter.setMaxLength(10)
        self.card_filter = QComboBox()
        self.card_filter.addItem("全部牌名", "")
        for key, data in self.manager.cards.items():
            self.card_filter.addItem(data["name_cn"], key)
        self.date_filter.textChanged.connect(self.reset_history)
        self.card_filter.currentIndexChanged.connect(self.reset_history)
        filters.addWidget(self.date_filter)
        filters.addWidget(self.card_filter)
        items.addLayout(filters)
        self.history_list = QListWidget()
        self.history_list.setIconSize(QSize(52, 78))
        self.history_list.itemDoubleClicked.connect(self.history_item)
        items.addWidget(self.history_list)
        pages = QHBoxLayout()
        self.prev = button("上一页", lambda: self.page(-1), pages)
        self.next = button("下一页", lambda: self.page(1), pages)
        button("查看选中记录", lambda: self.history_item(self.history_list.currentItem()), pages)
        items.addLayout(pages)
        self.tabs.addTab(history, "历史")

        favorites = QWidget()
        favorites.setObjectName("tarotPage")
        saved = QVBoxLayout(favorites)
        self.favorites_list = QListWidget()
        self.favorites_list.setIconSize(QSize(52, 78))
        self.favorites_list.itemDoubleClicked.connect(self.favorite_preview)
        saved.addWidget(self.favorites_list)
        actions = QHBoxLayout()
        button("查看牌面", lambda: self.favorite_preview(self.favorites_list.currentItem()), actions)
        button("取消收藏", self.remove_favorite, actions)
        saved.addLayout(actions)
        self.tabs.addTab(favorites, "收藏")
        self.tabs.currentChanged.connect(self.refresh_tab)
        self.service.voice_changed.connect(self.update_voice_controls)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.card.setFixedHeight(max(245, min(475, int(self.height() * .47))))

    def show_today(self, *_):
        self.follow_today = True
        self.set_draw(self.manager.today())

    def set_draw(self, draw):
        self.card.animation.stop()
        self.draw = draw
        self.status.clear()
        self.voice_status.clear()
        self.favorite_hint.hide()
        self.tabs.setCurrentIndex(0)
        self.refresh()

    def refresh(self):
        draw = self.manager.find(self.draw.day) if self.draw else None
        self.draw = draw
        enabled = self.manager.setting("enabled")
        self.return_button.setVisible((not self.follow_today or not draw) and enabled)
        revealed = bool(draw and (draw.revealed or not self.follow_today))
        key = draw.card_id if draw and (revealed or self.card.animation.state() == QVariantAnimation.State.Running) else None
        if key != self.loaded_image:
            self.card.face = read_pixmap(self.manager.image_path(key), QSize(480, 720)) if key else QPixmap()
            self.loaded_image = key
        self.card.revealed = revealed
        self.card_panel.setVisible(bool(draw))
        self.card.setAccessibleName("放大卡牌" if revealed else "揭晓今日塔罗")
        self.card.update()
        for widget in (self.summary_panel, self.detail_panel, self.action_panel):
            widget.setVisible(revealed)
        if not draw:
            self.meta.setText("今天的牌还在准备。" if enabled else "每日塔罗已关闭。可在 TT 设置中重新开启。")
            return
        self.meta.setText(draw.day + ("　今日已抽取" if self.follow_today else "　历史记录")
                          + (" · 来看看今天的提醒吧。" if revealed else " · 点一下，把牌翻开吧。"))
        if not revealed:
            return
        data = self.manager.cards[draw.card_id]
        self.title.setText(f"{data['name_cn']} · {draw.orientation}")
        self.theme.blockSignals(True)
        self.theme.setCurrentIndex(self.theme.findData(draw.theme))
        self.theme.blockSignals(False)
        self.favorite_button.setText("取消收藏" if draw.card_id in self.manager.favorites() else "收藏")
        self.keywords.setText(" · ".join(data[draw.meaning_prefix + "_keywords"]))
        self.detail.setText(data[draw.meaning_prefix + "_meaning"])
        self.advice.setText(f"今日建议 · {data['daily_advice']}")
        self.tt_message.setText(f"TT 想对你说 · {data['tt_message']}")
        answer = self.manager.reading(draw.day, draw.theme)
        self.ai_text.setText(answer)
        self.ai_text.setVisible(bool(answer))
        self.detail_read_button.setVisible(bool(answer))
        self.ai_button.setVisible(not answer)
        self.ai_button.setEnabled(not self.service.ai_pending and not answer)
        generating = self.service.ai_pending and self.service.ai_pending[0] == (draw.day, draw.theme)
        self.ai_button.setText("生成中…" if generating else "生成解读")
        self.update_voice_controls()

    def update_voice_controls(self):
        mode = self.service.current_tts_mode
        self.stop_button.setEnabled(mode is not None)
        self.voice_button.setText("简析朗读中…" if mode == "summary" else "朗读简析")
        self.detail_read_button.setText("解读朗读中…" if mode == "detail" else "朗读解读")
        self.voice_button.setEnabled(mode != "summary")
        self.detail_read_button.setEnabled(mode != "detail")
        if mode is None and self.voice_status.text() == "已加入 TT 语音队列。":
            self.voice_status.clear()

    def reveal_or_zoom(self):
        if not self.draw or self.card.animation.state() == QVariantAnimation.State.Running:
            return
        if self.card.revealed:
            self.preview(self.draw.card_id, self.draw.orientation)
        else:
            self.loaded_image = self.draw.card_id
            self.card.face = read_pixmap(self.manager.image_path(self.draw.card_id), QSize(480, 720))
            self.card.animation.start()

    def finish_reveal(self):
        if self.draw:
            self.manager.reveal(self.draw.day)
            self.service.dismiss_reminder()
            self.refresh()

    def change_theme(self, *_):
        if self.draw and self.card.revealed:
            self.manager.select_theme(self.draw.day, self.theme.currentData())
            self.status.clear()
            self.refresh()

    def toggle_favorite(self):
        if self.draw:
            key = self.draw.card_id
            saved = key not in self.manager.favorites()
            self.manager.set_favorite(key, saved)
            self.refresh()
            self.favorite_hint.setText("已收藏" if saved else "已取消收藏")
            self.favorite_hint.show()
            self.favorite_timer.start()

    def request_ai(self):
        self.status.setText(self.service.request_ai(self.draw))
        self.refresh()

    def read_voice(self):
        self.voice_status.setText(self.service.speak(self.draw))

    def read_detail(self):
        self.voice_status.setText(self.service.speak(self.draw, mode="detail"))

    def stop_voice(self):
        self.voice_status.setText(self.service.stop_voice())

    def preview(self, key, suffix=""):
        ZoomDialog(self.manager.image_path(key), self.manager.cards[key]["name_cn"] + " " + suffix, self).exec()

    def history_item(self, item):
        if item:
            draw = self.manager.find(item.data(Qt.ItemDataRole.UserRole))
            if draw:
                self.follow_today = draw.day == self.manager.now().date().isoformat()
                self.set_draw(draw)

    def page(self, step):
        self.offset += step * 40
        self.refresh_tab(1)

    def reset_history(self, *_):
        self.offset = 0
        self.refresh_tab(1)

    def refresh_tab(self, index):
        if index == 1:
            rows = self.manager.history(self.date_filter.text().strip(), self.card_filter.currentData(),
                                        offset=self.offset, limit=41)
            self.history_list.clear()
            self.prev.setEnabled(self.offset > 0)
            self.next.setEnabled(len(rows) > 40)
            for draw in rows[:40]:
                data = self.manager.cards[draw.card_id]
                detail = self.manager.reading(draw.day, draw.theme) or self.manager.interpretation(draw, draw.theme)
                summary = " ".join(detail.split())[:35]
                hidden = not draw.revealed and draw.day == self.manager.now().date().isoformat()
                name = "待揭晓" if hidden else f"{data['name_cn']} · {draw.orientation}"
                body = "" if hidden else f"{THEMES[draw.theme]} · {summary}"
                item = QListWidgetItem(f"{draw.day}　{name}\n{body}")
                if not hidden:
                    item.setIcon(thumbnail(str(self.manager.image_path(draw.card_id))))
                item.setData(Qt.ItemDataRole.UserRole, draw.day)
                self.history_list.addItem(item)
        elif index == 2:
            self.favorites_list.clear()
            for key in sorted(self.manager.favorites() & self.manager.cards.keys()):
                item = QListWidgetItem(thumbnail(str(self.manager.image_path(key))), self.manager.cards[key]["name_cn"])
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.favorites_list.addItem(item)

    def favorite_preview(self, item):
        if item:
            self.preview(item.data(Qt.ItemDataRole.UserRole))

    def remove_favorite(self):
        item = self.favorites_list.currentItem()
        if item:
            self.manager.set_favorite(item.data(Qt.ItemDataRole.UserRole), False)
            self.refresh_tab(2)


class TarotSettings(QGroupBox):
    def __init__(self, service, parent):
        super().__init__("每日塔罗", parent)
        self.service = service
        manager = service.manager
        form = QFormLayout(self)
        for key, text in (("enabled", "开启每日塔罗"), ("reminders", "主动文字提醒"),
                          ("voice", "提醒时朗读")):
            control = QCheckBox(text)
            control.setChecked(manager.setting(key))
            control.toggled.connect(lambda value, name=key: self.update_setting(name, value))
            form.addRow(control)
        minute = manager.setting("minute")
        schedule = QTimeEdit(QTime(minute // 60, minute % 60))
        schedule.setDisplayFormat("HH:mm")
        schedule.timeChanged.connect(lambda time: self.update_setting("minute", 60 * time.hour() + time.minute()))
        form.addRow("提醒时间", schedule)
        theme = QComboBox()
        for key, text in THEMES.items():
            theme.addItem(text, key)
        theme.setCurrentIndex(theme.findData(manager.setting("theme")))
        theme.currentIndexChanged.connect(lambda: self.update_setting("theme", theme.currentData()))
        form.addRow("默认主题", theme)
        row = QHBoxLayout()
        button("查看历史", service.open_history, row)
        button("清除以前的记录", self.clear_history, row)
        form.addRow(row)

    def update_setting(self, key, value):
        self.service.manager.set_setting(key, value)
        self.service.settings_changed()

    def clear_history(self):
        if QMessageBox.question(self, "清除塔罗历史", "删除以往的塔罗记录和解读？今日牌与收藏会继续保留。") == QMessageBox.StandardButton.Yes:
            self.service.clear_history()
