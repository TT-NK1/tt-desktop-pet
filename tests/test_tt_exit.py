"""Exercise the actual pet close handler with an unresponsive network task."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QWidget

import desktop_pet_chat as base
from tt_media_share import MediaShare


class PetFixture(base.ChatPet):
    def __init__(self, chat, folder):
        QWidget.__init__(self)
        self.chat = chat
        self.cleanup_ack = self.closing = self.verify = False
        self.timer = QTimer(self)
        self.tray = Mock()
        self.autonomous = Mock()
        self.manager = Mock()
        self.renderer = Mock(runtime=folder)
        self.renderer.close.side_effect = lambda: QTimer.singleShot(20, self, lambda: self.cleaned(True))
        self.report = {'checks': {}}
        self.exit_action = QAction('退出', self)
        self.exit_action.triggered.connect(self.close)


def exercise_exit_without_waiting():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    request_started, media_started, release = Event(), Event(), Event()

    def slow_reply(_worker):
        request_started.set()
        release.wait(30)

    def slow_media(*_args):
        media_started.set()
        release.wait(30)

    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'LOCALAPPDATA': folder}), \
            patch.object(base.auth, 'load_credentials', return_value=None), \
            patch.object(base.RequestWorker, 'run', new=slow_reply), \
            patch('tt_media_share.find_topic', new=slow_media):
        chat = base.ChatWindow()
        app.processEvents()
        chat.identity = 'test'
        chat.models.addItem('Test', 'test-model')
        chat.consent.setChecked(True)
        chat.chat_messages = object()
        media = MediaShare(chat, base)
        media.sources = ('bilibili',)
        app.aboutToQuit.connect(media.shutdown)
        assert media.try_proactive()
        assert chat.send_prompt('测试退出时保存消息')
        assert request_started.wait(3) and media_started.wait(3)
        worker, turn, topic = chat.worker, chat.pending_id, media.job
        pet = PetFixture(chat, Path(folder))
        QTimer.singleShot(0, pet, pet.exit_action.trigger)
        assert app.exec() == 0
        assert pet.cleanup_ack and pet.closing
        assert worker.cancelled.is_set() and topic.cancelled.is_set()
        assert chat.db.execute('SELECT status FROM turns WHERE id=?', (turn,)).fetchone() == ('stopped',)
        chat.db.close()
        print('退出已完成，聊天和媒体的阻塞任务仍未返回。', flush=True)


class QuitTests(unittest.TestCase):
    def test_quit_while_background_requests_are_blocked(self):
        result = subprocess.run([sys.executable, '-B', __file__, '--child'],
                                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'PYTHONIOENCODING': 'utf-8'},
                                capture_output=True, encoding='utf-8', timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn('阻塞任务仍未返回', result.stdout)


if __name__ == '__main__':
    if '--child' in sys.argv:
        exercise_exit_without_waiting()
    else:
        unittest.main()
