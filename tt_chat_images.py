"""Bounded image attachments for the Responses chat request."""

import base64
import json
from pathlib import Path
import re
from threading import Lock

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QPlainTextEdit


MAX_ATTACHMENTS = 3
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_EDGE = 2048
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
CLIPBOARD_IMAGE_FORMATS = (
    'image/png', 'image/jpeg', 'image/webp', 'image/bmp', 'image/gif',
    'application/x-qt-windows-mime;value="PNG"',
    'application/x-qt-windows-mime;value="JFIF"',
)
PREVIEW_SIZE = QSize(600, 450)
IMAGE_NOTE = re.compile(r"\n\n【图片附件 · [1-3] 张】$")


def image_note(count):
    return f"\n\n【图片附件 · {count} 张】"


def encode_previews(images):
    return json.dumps([base64.b64encode(image.encode(image.preview, "JPEG", 82)).decode("ascii")
                       for image in images]) if images else None


def decode_previews(value):
    if not isinstance(value, str) or len(value) > MAX_ATTACHMENTS * 500_000:
        return ()
    try:
        entries = json.loads(value)
    except (ValueError, RecursionError):
        return ()
    if not isinstance(entries, list):
        return ()
    previews = []
    for entry in entries[:MAX_ATTACHMENTS]:
        if not isinstance(entry, str) or len(entry) > 500_000:
            continue
        try:
            data = QByteArray(base64.b64decode(entry, validate=True))
        except ValueError:
            continue
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b'jpeg')
        size = reader.size()
        if not size.isEmpty() and size.width() <= PREVIEW_SIZE.width() and size.height() <= PREVIEW_SIZE.height():
            preview = reader.read()
            if not preview.isNull():
                previews.append(preview)
    return tuple(previews)


def read_image(path):
    return read_from(QImageReader(str(path)))


def read_from(reader):
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isEmpty() or size.width() * size.height() > MAX_IMAGE_PIXELS:
        raise ValueError("图片无法读取，或超过 2400 万像素。")
    if max(size.width(), size.height()) > MAX_IMAGE_EDGE:
        reader.setScaledSize(size.scaled(QSize(MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError("图片文件损坏，或当前格式无法读取。")
    return image


def clipboard_image_paths(source):
    return [path for url in source.urls()
            if url.isLocalFile() and (path := Path(url.toLocalFile())).suffix.lower() in IMAGE_SUFFIXES]


def has_clipboard_image(source):
    return source.hasImage() or any(source.hasFormat(fmt) for fmt in CLIPBOARD_IMAGE_FORMATS)


def read_clipboard_image(source):
    if source.hasImage():
        image = source.imageData()
        if isinstance(image, QPixmap):
            image = image.toImage()
        if isinstance(image, QImage) and not image.isNull():
            if image.width() * image.height() > MAX_IMAGE_PIXELS:
                raise ValueError("剪贴板图片超过 2400 万像素，请缩小截图范围后重试。")
            if max(image.width(), image.height()) > MAX_IMAGE_EDGE:
                image = image.scaled(MAX_IMAGE_EDGE, MAX_IMAGE_EDGE, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            return image
    for fmt in CLIPBOARD_IMAGE_FORMATS:
        if source.hasFormat(fmt):
            data = source.data(fmt)
            if data.isEmpty():
                continue
            if data.size() > 32 * 1024 * 1024:
                raise ValueError("剪贴板图片过大，请缩小截图范围后重试。")
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            return read_from(QImageReader(buffer))
    raise ValueError("无法读取剪贴板里的图片，请重新截图。")


class ChatInput(QPlainTextEdit):
    paste_attachment = None

    def canInsertFromMimeData(self, source):
        return self.accepts_attachment(source) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if self.accepts_attachment(source):
            self.paste_attachment(source)
        else:
            super().insertFromMimeData(source)

    def accepts_attachment(self, source):
        return (self.paste_attachment is not None and self.isEnabled() and not self.isReadOnly()
                and source is not None and (has_clipboard_image(source) or bool(clipboard_image_paths(source))))


class ChatImage:
    def __init__(self, image):
        self.image = image
        self.preview = image.scaled(PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
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
