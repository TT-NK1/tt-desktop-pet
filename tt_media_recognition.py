"""Local attachment recognition for the chat editor."""

import base64
from collections import deque
import json
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer
from PySide6.QtGui import QImage

from tt_chat_images import ChatImage
from tt_multilingual import ASR_PYTHON, FFMPEG, RUNTIME, VIDEO_SUFFIXES


class MediaRecognition(QObject):
    def __init__(self, room):
        super().__init__(room)
        self.room = room
        self.read_turn = None
        self.starting_read = False
        self.process = None
        self.jobs = deque()
        self.recognition_limit = QTimer(self, singleShot=True, interval=210_000)
        self.recognition_limit.timeout.connect(self.timed_out)

    @property
    def busy(self):
        return bool(self.process or self.jobs)

    def add(self, entry):
        self.jobs.append(entry)
        self.start_next()

    def start_next(self):
        if self.process:
            return
        while self.jobs:
            entry = self.jobs.popleft()
            if any(item is entry for item in self.room.attachments):
                break
        else:
            return
        kind = 'video' if entry['path'].suffix.lower() in VIDEO_SUFFIXES else 'audio'
        process = QProcess(self)
        self.process = process
        process.finished.connect(lambda code, _: self.finished(process, entry, code))
        process.errorOccurred.connect(lambda error: self.finished(process, entry, 1)
                                      if error == QProcess.ProcessError.FailedToStart else None)
        process.start(str(ASR_PYTHON), ['-I', '-B', str(Path(__file__).with_name('tt_media_recognition_worker.py')),
                      str(RUNTIME), str(FFMPEG), kind, str(entry['path'])])
        self.recognition_limit.start()
        self.room.status.setText('正在本机识别音视频…')
        self.room.update_buttons()

    def finished(self, process, entry, exit_code):
        if process is not self.process:
            return
        self.recognition_limit.stop()
        self.process = None
        try:
            result = json.loads(bytes(process.readAllStandardOutput())) if exit_code == 0 else {}
        except ValueError:
            result = {}
        process.deleteLater()
        error = result.get('error') or ('本机识别失败，请重试。' if not result else '')
        if any(item is entry for item in self.room.attachments):
            if error:
                self.room.attachments.remove(entry)
                self.room.status.setText(error)
            else:
                photos = []
                for encoded in result.get('frames', []):
                    image = QImage.fromData(base64.b64decode(encoded))
                    if not image.isNull():
                        photos.append(ChatImage(image))
                entry['frames'] = photos
                entry['text'] = (f"本机{result['kind']}（{result.get('language') or '语言未标注'}）\n{result['text']}"
                                 if result.get('text') else '本机未识别到清楚的人声或字幕。')
                entry['processing'] = False
                self.room.status.setText('识别资料已加入附件，点击发送让 TT 解读。')
            self.room.render_attachments()
        self.start_next()
        self.room.update_buttons()

    def timed_out(self):
        if self.process:
            self.process.kill()

    def describe(self, prompt, images=()):
        if self.busy or self.room.busy():
            self.room.status.setText('请等当前识别或回复完成，再识别这条内容。')
            return
        self.starting_read = True
        try:
            if self.room.send_prompt(prompt, images=images, clear_input=False):
                self.read_turn = (self.room.session, self.room.pending_id)
        finally:
            self.starting_read = False

    def reading_requested(self, turn):
        return self.starting_read or self.read_turn == (self.room.session, turn)

    def take_read(self, turn):
        requested = self.read_turn == (self.room.session, turn)
        self.read_turn = None
        return requested

    def cancel(self):
        self.read_turn = None
        self.jobs.clear()
        self.recognition_limit.stop()
        if self.process is not None:
            process, self.process = self.process, None
            process.finished.connect(process.deleteLater)
            process.kill()
        self.room.update_buttons()
