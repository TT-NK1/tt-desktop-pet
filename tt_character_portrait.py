"""Natural Idle portraits for the TT conversation header."""
import json
import logging

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


LOG = logging.getLogger(__name__)


class CharacterPortraitManager:
    """Resolve the desktop's outfit ID and cache each decoded PNG."""

    def __init__(self, directory):
        self.directory = directory
        self.images = {}
        try:
            mapping = json.loads((directory / 'index.json').read_text(encoding='utf-8'))
            if not isinstance(mapping, dict):
                raise ValueError('Expected an outfit-to-PNG mapping')
            self.mapping = mapping
        except (OSError, ValueError) as error:
            LOG.warning('Could not load TT portrait mapping: %s', error)
            self.mapping = {}

    def load(self, outfit):
        filename = self.mapping.get(outfit)
        if (not isinstance(filename, str) or not filename.endswith('.png')
                or any(mark in filename for mark in ('/', '\\', ':'))):
            return None
        if filename not in self.images:
            image = QImage(str(self.directory / filename))
            if image.isNull() or not image.hasAlphaChannel():
                return None
            self.images[filename] = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return self.images[filename]


class CharacterPortrait(QWidget):
    def __init__(self, directory, parent):
        super().__init__(parent)
        self.setFixedSize(196, 162)
        self.portraits = CharacterPortraitManager(directory)
        self.image = None
        self.outgoing = None
        self.fade = QVariantAnimation(self)
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.setDuration(260)
        self.fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.fade.valueChanged.connect(self.update)
        self.fade.finished.connect(self.finish_fade)

        self.notice = QLabel('当前服装缺少待机立绘', self)
        self.notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice.setStyleSheet('background: #fbf4ff; border-radius: 5px; padding: 2px;')
        self.notice.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.notice, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_outfit(self, outfit):
        image = self.portraits.load(outfit)
        if image is None:
            self.notice.setText('当前服装缺少待机立绘' if self.image is None else '待机立绘缺失 · 显示上一套')
            self.layout().setAlignment(self.notice, Qt.AlignmentFlag.AlignCenter if self.image is None
                                       else Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            self.notice.setToolTip(str(outfit))
            self.notice.show()
            LOG.warning('Missing Natural Idle portrait for outfit %s', outfit)
            return

        self.notice.hide()
        if image is self.image:
            return
        previous = self.current_frame() if self.image is not None and self.isVisible() else None
        self.fade.stop()
        self.image = image
        self.outgoing = previous
        if previous is not None:
            self.fade.start()
        self.update()

    def finish_fade(self):
        self.outgoing = None
        self.update()

    def hideEvent(self, event):
        self.fade.stop()
        self.finish_fade()
        super().hideEvent(event)

    def paintEvent(self, event):
        if self.image is not None:
            with QPainter(self) as painter:
                painter.drawImage(0, 0, self.current_frame())

    def current_frame(self):
        ratio = self.devicePixelRatioF()
        canvas = QImage(QSize(round(self.width() * ratio), round(self.height() * ratio)),
                        QImage.Format.Format_ARGB32_Premultiplied)
        canvas.setDevicePixelRatio(ratio)
        canvas.fill(Qt.GlobalColor.transparent)
        bounds = QRectF(self.rect())
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(0, 0, size.width(), size.height())
        target.moveCenter(bounds.center())
        progress = self.fade.currentValue() if self.outgoing is not None else 1.0
        with QPainter(canvas) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setOpacity(progress)
            painter.drawImage(target, self.image)
        if self.outgoing is not None:
            # Weight both premultiplied images before adding, keeping overlap opaque.
            previous = QImage(canvas.size(), canvas.format())
            previous.setDevicePixelRatio(ratio)
            previous.fill(Qt.GlobalColor.transparent)
            with QPainter(previous) as painter:
                painter.setOpacity(1.0 - progress)
                painter.drawImage(0, 0, self.outgoing)
                painter.setOpacity(1.0)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                painter.drawImage(0, 0, canvas)
            return previous
        return canvas
