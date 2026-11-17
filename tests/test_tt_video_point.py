"""Cover the timestamped video attachment flow without requiring a browser login."""

import json
import os
from base64 import b64encode
from threading import Event
import unittest
from unittest.mock import Mock

import httpx

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QWidget

from tt_chat_images import ChatImage
from tt_share_content import video_card
from tt_share_player import WatchDialog
from tt_video_preview import PreviewFrame, bilibili_preview


VIDEO = 'https://www.bilibili.com/video/BV1orsc68Ej4/'
PART_2 = VIDEO + '?p=2'


class Room(QWidget):
    def __init__(self):
        super().__init__()
        self.identity, self.session = 'test', 'video'
        self.consent = QCheckBox()
        self.consent.setChecked(True)
        self.models = QComboBox()
        self.models.addItem('Test', 'test-model')
        self.send_prompt = Mock()

    def busy(self):
        return False


class Player(QWidget):
    def url(self):
        return self.page_url

    def setUrl(self, url):
        self.page_url = url

    def stop(self):
        pass

    def runJavaScript(self, script, callback):
        self.script, self.callback = script, callback


class TestWatch(WatchDialog):
    __test__ = False

    def make_player(self):
        self.page = self.view = Player(self)
        return self.view


class VideoPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.room = Room()
        self.watch = TestWatch(self.room, video_card(VIDEO, '小狗'))
        self.pix = QImage(96, 64, QImage.Format.Format_RGB32)
        self.pix.fill(QColor('#8775a2'))
        self.jpeg = bytes(ChatImage.encode(self.pix, 'JPEG'))

    def tearDown(self):
        self.watch.close()
        self.room.close()

    def test_embed_current_frame_by_default(self):
        self.assertTrue(self.watch.include_frame.isChecked())
        self.watch.discuss()
        self.assertIn('if (true)', self.watch.page.script)
        point = {'url': VIDEO, 'time': 13.5, 'frame': 'data:image/jpeg;base64,' + b64encode(self.jpeg).decode()}
        self.watch.page.callback(json.dumps(point))
        prompt = self.room.send_prompt.call_args.args[0]
        self.assertIn('播放器在 00:13 的画面', prompt)
        images = self.room.send_prompt.call_args.kwargs['images']
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].input_part()['type'], 'input_image')

    def test_send_timestamp_only_when_selected(self):
        self.watch.include_frame.setChecked(False)
        self.watch.discuss()
        self.assertIn('if (false)', self.watch.page.script)
        self.watch.page.callback({'url': VIDEO, 'time': 13})
        self.assertEqual(self.room.send_prompt.call_args.kwargs['images'], ())

    def test_stale_callback_cannot_send_other_part(self):
        self.watch.discuss()
        callback = self.watch.page.callback
        self.watch.set_item(video_card(PART_2, '下一集'))
        callback({'url': VIDEO, 'time': 13})
        self.room.send_prompt.assert_not_called()
        self.watch.discuss()
        self.watch.page.callback({'url': VIDEO, 'time': 13})
        self.room.send_prompt.assert_not_called()
        self.assertIn('已切换', self.watch.status.text())

    def test_preview_uses_its_own_timestamp(self):
        job = Mock(seconds=13.0)
        self.watch.preview_job = job
        self.watch.preview_ready(job, PreviewFrame(15, self.pix))
        self.room.send_prompt.assert_called_once()
        self.assertIn('00:13 附近', self.room.send_prompt.call_args.args[0])
        self.assertIn('预览帧（00:15）', self.room.send_prompt.call_args.args[0])
        self.assertEqual(len(self.room.send_prompt.call_args.kwargs['images']), 1)

    def test_missing_frame_does_not_silently_send_text(self):
        job = Mock(seconds=13.0)
        self.watch.preview_job = job
        self.watch.preview_ready(job, None)
        self.room.send_prompt.assert_not_called()
        self.assertIn('暂时取不到', self.watch.status.text())

    def test_public_sprite_uses_cid_and_nearest_tile(self):
        sheet = QImage(96, 64, QImage.Format.Format_RGB32)
        sheet.fill(QColor('#245d80'))
        for x in range(48, 96):
            for y in range(64):
                sheet.setPixelColor(x, y, QColor('#b96921'))
        jpeg = bytes(ChatImage.encode(sheet, 'JPEG'))
        requests = []

        def handler(request):
            requests.append(request.url)
            if request.url.path.endswith('/view'):
                return httpx.Response(200, json={'code': 0, 'data': {'pages': [{'page': 1}, {'page': 2, 'cid': 42}]}})
            if request.url.path.endswith('/videoshot'):
                return httpx.Response(200, json={'code': 0, 'data': {
                    'image': ['//bimp.hdslb.com/video.jpg'], 'index': [10, 15],
                    'img_x_len': 2, 'img_y_len': 1, 'img_x_size': 48, 'img_y_size': 64,
                }})
            return httpx.Response(200, content=jpeg)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            frame = bilibili_preview(client, PART_2, 13, Event())
            self.assertIsNone(bilibili_preview(client, PART_2, 200, Event()))
        self.assertEqual(requests[1].params.get('cid'), '42')
        self.assertEqual(frame.seconds, 15)
        self.assertEqual((frame.image.width(), frame.image.height()), (48, 64))
        self.assertGreater(frame.image.pixelColor(12, 12).red(), 150)


if __name__ == '__main__':
    unittest.main()
