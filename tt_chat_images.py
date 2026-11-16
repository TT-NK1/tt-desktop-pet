"""Bounded image attachments for the Responses chat request."""

import base64
import re
from threading import Lock

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter


MAX_ATTACHMENTS = 3
IMAGE_NOTE = re.compile(r"\n\n【图片附件 · [1-3] 张】$")


def image_note(count):
    return f"\n\n【图片附件 · {count} 张】"


def read_image(path):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isEmpty() or size.width() * size.height() > 24_000_000:
        raise ValueError("图片无法读取，或超过 2400 万像素。")
    if max(size.width(), size.height()) > 2048:
        reader.setScaledSize(size.scaled(QSize(2048, 2048), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError("图片文件损坏，或当前格式无法读取。")
    return image


class ChatImage:
    def __init__(self, image):
        self.image = image
        self.url = None
        self.lock = Lock()

    @staticmethod
    def encode(image, format_, quality=-1):
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, format_, quality):
            raise ValueError("图片处理失败，请重新添加图片。")
        buffer.close()
        return data

    def input_part(self):
        with self.lock:
            if self.url is None:
                data = self.encode(self.image, "PNG")
                mime = "png"
                if len(data) > 3 * 1024 * 1024:
                    background = QImage(self.image.size(), QImage.Format.Format_RGB32)
                    background.fill(Qt.GlobalColor.white)
                    painter = QPainter(background)
                    painter.drawImage(0, 0, self.image)
                    painter.end()
                    data = self.encode(background, "JPEG", 88)
                    mime = "jpeg"
                if len(data) > 3 * 1024 * 1024:
                    raise ValueError("图片压缩后仍过大，请裁剪后重试。")
                self.url = f"data:image/{mime};base64," + base64.b64encode(data).decode("ascii")
                self.image = None
        return {"type": "input_image", "image_url": self.url, "detail": "high"}
