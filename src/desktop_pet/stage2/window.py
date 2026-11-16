import math
import time
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QMenu, QWidget
from .assets import OUTFITS, POSES

class DesktopPet(QWidget):
    def __init__(self, sprites):
        super().__init__()
        self.sprites = sprites
        self.outfit_index = 0
        self.pose_index = 0
        self.drag_offset = None
        self.started = time.perf_counter()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(290, 290)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.update)
        self.timer.start(16)

    def paintEvent(self, event):
        sprite = self.sprites[self.outfit_index][self.pose_index]
        seconds = time.perf_counter() - self.started
        breath = math.sin(math.tau * seconds / 4.2)
        sway = math.sin(math.tau * seconds / 7.5)
        width = sprite.width()
        height = sprite.height() * (1.0 + 0.006 * breath)
        left = (self.width() - width) / 2 + 0.7 * sway
        top = self.height() - height - 5

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(
            QRectF(left, top, width, height), sprite, QRectF(sprite.rect())
        )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = None

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        outfits = menu.addMenu("服装")
        poses = menu.addMenu("生活姿势")

        outfit_actions = [outfits.addAction(name) for name in OUTFITS]
        pose_actions = [poses.addAction(name) for name in POSES]
        for action in (*outfit_actions, *pose_actions):
            action.setCheckable(True)
        outfit_actions[self.outfit_index].setChecked(True)
        pose_actions[self.pose_index].setChecked(True)

        menu.addSeparator()
        quit_action = menu.addAction("退出桌宠")
        selected = menu.exec(event.globalPos())

        if selected == quit_action:
            self.close()
        elif selected in outfit_actions:
            self.outfit_index = outfit_actions.index(selected)
            self.update()
        elif selected in pose_actions:
            self.pose_index = pose_actions.index(selected)
            self.update()
