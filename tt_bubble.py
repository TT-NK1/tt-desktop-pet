"""随角色动作定位的思绪与功能气泡。"""

import math
import random
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QTransform
from PySide6.QtWidgets import QWidget, QVBoxLayout

from tt_bubble_art import outline, paint_glass, paint_motif, palette_for


THOUGHTS = (
    ("今天也陪着你呀 (｡･ω･｡)ﾉ♡", "偷偷给你加一点好运 ✨", "发会儿呆，也很不错呢～"),
    ("一二一，一起散散步 ♪", "走两步，灵感就来啦！", "哒哒哒，快乐巡逻中～"),
    ("嗨！看到你啦 (´▽`)ﾉ", "给你一个元气满满的招呼 ✨", "这里有一只挥手的 TT～"),
    ("认真敲敲敲 (ง •̀_•́)ง", "你专心忙，我陪着你～", "灵感加载中… ☕"),
    ("伸个懒腰，充充电～", "一起活动一下肩膀吧 ♪", "呼——舒展开啦 (´▽`)"),
    ("喵～今天也被可爱击中了 🐾", "小猫的心事，是小鱼干吗？", "摸摸猫，烦恼跑跑～"),
    ("捧一杯暖暖的幸福 ☕", "喝口水，慢慢来呀～", "杯子暖暖，心情软软 ♡"),
    ("小声一点，正在梦游 zZ", "晚安，梦里也有小星星 ✨", "电量恢复中 (－ω－) zZ"),
)

def bubble_placement(area, pet, width, height, crown_x=None, *, side_inset=0, scale=1., face_y=None):
    """Return the window origin and the point on the pet that the tail faces."""
    gap = 7 * scale if crown_x is not None else 0
    crown_x = pet.center().x() if crown_x is None else crown_x
    head_y = pet.top() + min(pet.height() * 0.26, 56 * scale) if face_y is None else face_y
    # In the 125% reference the center of the bubble sits slightly below the eyes.
    side_y = head_y - height / 2 + (18 * scale if face_y is not None else 0)
    above = (crown_x - width / 2, pet.top() - height - gap, crown_x, pet.top(), False)
    beside = (
        (pet.right(), side_y, pet.right(), head_y, True),
        (pet.left() - width, side_y, pet.left(), head_y, True),
    )
    below = (pet.center().x() - width / 2, pet.bottom(), pet.center().x(), pet.bottom(), False)
    candidates = (above, *beside, below)
    max_x = max(area.left(), area.right() + 1 - width)
    max_y = max(area.top(), area.bottom() + 1 - height)
    for x, y, target_x, target_y, beside in candidates:
        fits = area.left() <= x <= max_x if beside else area.top() <= y <= max_y
        if fits:
            break
    else:
        # If all four sides are too narrow, favor the one with least overlap.
        def overlap(candidate):
            x, y = candidate[:2]
            bounds = QRectF(max(area.left(), min(x, max_x)),
                            max(area.top(), min(y, max_y)), width, height)
            intersection = bounds.intersected(pet)
            return intersection.width() * intersection.height()

        x, y, target_x, target_y, beside = min(candidates, key=overlap)
    if beside:
        x += side_inset if x < pet.left() else -side_inset
    origin = QPoint(round(max(area.left(), min(x, max_x))),
                    round(max(area.top(), min(y, max_y))))
    return origin, QPointF(target_x - origin.x(), target_y - origin.y())


def tail_direction(outline, body, target):
    center = body.center()
    delta = target - center
    length = (delta.x() ** 2 + delta.y() ** 2) ** 0.5
    direction = delta / length if length else QPointF(0, 1)
    inside, outside = 0.0, max(body.width(), body.height())
    for _ in range(14):
        radius = (inside + outside) / 2
        if outline.contains(center + direction * radius):
            inside = radius
        else:
            outside = radius
    return center + direction * inside, direction


class ThoughtBubble(QWidget):
    activated = Signal()

    def __init__(self, pet):
        super().__init__(pet, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.pet = pet
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.content = QVBoxLayout(self)
        self.settings_shortcut = None
        self.heading_rect = QRectF()
        self.message_rect = QRectF()
        self._tail_target = QPointF(105, 200)
        self._scale = 1.
        self.theme = None
        self.message = ""
        self.weather_icon = ''
        self._reply_until = 0
        self._next_thought = 0
        self._action = None
        self.sync_outfit()
        self.refresh_thought()
        if hasattr(pet,'window_manager'):
            self.follow_timer = QTimer(self,interval=500)
            self.follow_timer.timeout.connect(self.refresh_thought)
        else:
            self.follow_timer = QTimer(self,interval=80)
            self.follow_timer.timeout.connect(self.follow_pet)

    def set_message(self, text):
        self.clear_weather()
        self._reply_until = time.monotonic() + 30
        self._set_text(text)

    def set_weather(self, icon, text):
        self._reply_until = time.monotonic()+30
        self.weather_icon = icon
        self.message = text
        self.setToolTip(text)
        self.setAccessibleName('TT 的天气提醒：'+text)
        self.resize_to_content()
        self.follow_pet()

    def clear_weather(self):
        if self.weather_icon:
            self.weather_icon = ''
            self.resize_to_content()

    def _set_text(self, text):
        full = " ".join(str(text).split()) or "今天也陪着你。"
        self.message = full if len(full) <= 32 else full[:31] + "…"
        self.setToolTip(full)
        self.setAccessibleName("TT 的气泡：" + full)
        self.resize_to_content()
        self.update()

    def refresh_thought(self):
        now = time.monotonic()
        action = getattr(self.pet, "action_index", 0)
        if now < self._reply_until:
            return
        self.clear_weather()
        if action != self._action or now >= self._next_thought:
            choices = THOUGHTS[action % len(THOUGHTS)]
            self._set_text(random.choice([s for s in choices if s != self.message]))
            self._action = action
            self._next_thought = now + random.randint(14, 24)

    def sync_outfit(self, _state=None):
        theme = palette_for(self.pet)
        scale = getattr(self.pet, 'scale', 1.)
        if theme is self.theme and scale == self._scale:
            return
        self.theme = theme
        self._scale = scale
        hover = "255,255,255" if theme.dark else "79,43,106"
        font_size = max(8.5, self.font().pointSizeF() * scale)
        padding = round(6 * scale)
        radius = round(8 * scale)
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.ink}; border: 1px solid transparent; "
            f"padding: {padding}px; font-size: {font_size:g}pt; }}"
            f"QPushButton:hover {{ background: rgba({hover},40); border-radius: {radius}px; }}"
            f"QPushButton:focus {{ border-color: {theme.accent}; border-radius: {radius}px; }}")
        self.resize_to_content()
        self.update()

    def text_font(self, *, heading=False):
        font = QFont(self.font())
        size = max(8 / self._scale, 9) if heading else max(
            9 / self._scale, 9 if self.weather_icon else 10)
        font.setPointSizeF(size)
        font.setBold(True)
        return font

    def heading(self):
        return f'{self.weather_icon}  TT 的小心思' if self.weather_icon else 'TT 的小心思'

    def resize_to_content(self):
        scale = self._scale
        side = round(27 * scale)
        width = round((248 if self.weather_icon else 232) * scale)
        heading_metrics = QFontMetricsF(self.text_font(heading=True), self)
        message_metrics = QFontMetricsF(self.text_font(), self)
        if not self.weather_icon:
            default_width = message_metrics.horizontalAdvance(THOUGHTS[0][0]) + 49
            width = max(width, math.ceil(default_width * scale))
        shortcut = self.settings_shortcut
        if item := self.content.itemAt(0):
            actions = item.widget()
            actions.layout().setHorizontalSpacing(round(14 * scale))
            actions.layout().setVerticalSpacing(round(3 * scale))
            width = max(width, actions.sizeHint().width() + 2 * side)
            if shortcut is not None:
                shortcut.adjustSize()
                x = round(23 * scale)
                heading_width = heading_metrics.horizontalAdvance(self.heading()) * scale
                clearance = x + shortcut.width() + round(3 * scale)
                width = max(width, math.ceil(heading_width + 2 * clearance))
                shortcut.move(x, round(16 * scale))
                shortcut.setVisible(not actions.isHidden())

        logical_width = width / scale
        heading_height = max(20, heading_metrics.height() + 2)
        self.heading_rect = QRectF(30, 27, logical_width - 60, heading_height)
        message_width = logical_width - 48
        message_bounds = message_metrics.boundingRect(QRectF(0, 0, message_width, 0),
                                                      Qt.TextFlag.TextWordWrap, self.message)
        message_height = max(29, message_bounds.height() + 4)
        self.message_rect = QRectF(24, self.heading_rect.bottom() + 1, message_width, message_height)
        top = round((self.message_rect.bottom() + 5) * scale)
        bottom = round(25 * scale)
        self.content.setContentsMargins(side, top, side, bottom)
        self.setMinimumSize(width, top + bottom)
        self.resize(width, self.content.totalSizeHint().height())
        self.content.activate()

    def follow_pet(self):
        self.sync_outfit()
        self.refresh_thought()
        screen = self.pet.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        anchor = (self.pet.bubble_anchor_rect() if hasattr(self.pet, "bubble_anchor_rect")
                  else self.pet.rect())
        rect = QRectF(anchor).translated(self.pet.x(), self.pet.y())
        crown_x = None
        face_y = None
        if hasattr(self.pet, "bubble_head_x"):
            crown_x = self.pet.bubble_head_x()
            if crown_x is not None:
                crown_x += self.pet.x()
            if self._scale in (.75, 1.):
                # The eyes sit near y=139 on TT's 390 px Live2D canvas.
                # The full silhouette can extend above the head in some poses.
                face_y = self.pet.y() + self.pet.height() * (139 / 390)
        position, target = bubble_placement(
            area, rect, self.width(), self.height(), crown_x,
            side_inset=12 * self._scale, scale=self._scale, face_y=face_y)
        if position != self.pos():
            self.move(position)
        if target != self._tail_target:
            self._tail_target = target
            self.update()

    def bubble_body_rect(self):
        x, y = 20 * self._scale, 16 * self._scale
        return QRectF(x, y, self.width() - 2 * x, self.height() - 2 * y)

    def tail_rect(self):
        body = self.bubble_body_rect()
        base, direction = tail_direction(outline(body), body, self._tail_target)
        dot = base + direction * (10 * self._scale)
        return QRectF(dot.x() - 3, dot.y() - 3, 6, 6)

    def showEvent(self, event):
        super().showEvent(event)
        self.follow_pet()
        self.follow_timer.start()

    def hideEvent(self, event):
        self.follow_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self._scale
        painter.scale(scale, scale)
        body = QTransform.fromScale(1 / scale, 1 / scale).mapRect(self.bubble_body_rect())
        target = self._tail_target / scale
        paint_glass(painter, body, self.theme, tail_direction(outline(body), body, target))
        paint_motif(painter, QPointF(body.right() - 24, body.top() + 2), 20, self.theme)
        painter.setPen(QColor(self.theme.ink))
        painter.setFont(self.text_font(heading=True))
        painter.drawText(self.heading_rect, Qt.AlignmentFlag.AlignCenter, self.heading())
        painter.setFont(self.text_font())
        painter.drawText(self.message_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        painter.end()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)
