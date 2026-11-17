"""Regression coverage for direct media requests in TT conversations."""

import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QImage
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMimeData, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

import desktop_pet_search as launcher
from tt_chat_images import ChatImage
from tt_edge_research import routing_input
from tt_share_content import SharedMedia, find_requested_media, image_card, link_card, music_card, share_request, video_card


class ChatMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        launcher.configure_companion()

    def test_read_original_request_bypasses_chat_and_reads_only_once(self):
        original = '私はあなたが好きです。'
        with self.latency_window() as window, \
                patch.object(launcher.base.RequestWorker.__mro__[1], 'run') as chat, \
                patch.object(window.pro_voice.player, 'play') as play:
            window.pro_settings.update(speech_enabled=True, reply_voice=False, muted=False)
            window.send_prompt(original + ' 帮我朗读出来')
            row = self.wait_for_turn(window)
            chat.assert_not_called()
            self.assertEqual(row.content, original)
            play.assert_called_once()
            self.assertEqual(play.call_args.args[0], original)
            play.reset_mock()
            window.retry_turn(row.turn)
            retry = self.wait_for_turn(window)
            self.assertEqual(retry.content, original)
            chat.assert_not_called()
            play.assert_called_once()
            self.assertEqual(play.call_args.args[0], original)

    def test_mixed_japanese_and_chinese_switch_models_at_sentence_boundaries(self):
        from tt_indextts_playback import IndexPlayback
        from tt_multilingual import speech_sections
        from tt_speech_segments import ChineseTextSegmenter

        first = 'わたしは あなたが すきです Watashi wa anata ga suki desu'
        second = '意思是「我喜欢你」……这句我有认真听进去哦。'
        spoken_chinese = '意思是：我喜欢你。这句我有认真听进去哦。'
        segmenter = ChineseTextSegmenter()
        self.assertEqual(list(speech_sections('私はあなたが好きです。' + second, segmenter)),
                         [('ja', '私はあなたが好きです。'), ('zh', second)])
        pure = '早上好。今天还有三件事要做，先喝点水。'
        self.assertEqual(list(speech_sections(pure, segmenter)), [('zh', pure)])
        self.assertEqual(list(speech_sections('125%', segmenter)), [('zh', '125%')])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model').mkdir()
            (root / 'model' / 'model.safetensors').touch()
            reference = root / 'reference.wav'
            reference.touch()
            player = IndexPlayback(None, root, lambda _: None, lambda: None)
            with patch('tt_indextts_playback.QWEN_RUNTIME', root), patch.object(player, 'start_worker') as start:
                player.play(first + '\n' + second, dict(volume=70, voice_reference=str(reference)))
                start.assert_called_once_with('qwen')
                self.assertEqual(player.job.clip.text.strip(), first)
                self.assertEqual(player.pending[0].text.strip(), spoken_chinese)
                self.assertNotIn('language', player.pending[0].options)
                player.job = None
                player.advance()
                self.assertEqual(start.call_args.args[0], 'index')
                self.assertEqual(player.job.clip.text.strip(), spoken_chinese)
                player.job = None
            player.shutdown()

    def test_recognized_text_is_read_once_in_the_selected_voice(self):
        original = '君の知らない物語。これは日本語の文章です。'
        request_instructions = []

        def reply(worker):
            request_instructions.append(worker.payload['instructions'])
            worker.publish('delta', original + '\n\n')
            worker.publish('completed', original)

        with self.latency_window() as window, \
                patch.object(launcher.base.RequestWorker.__mro__[1], 'run', new=reply), \
                patch.object(window.pro_voice.player, 'play') as play:
            window.pro_settings.update(speech_enabled=True, reply_voice=True, muted=False, voice_name='TT 测试')
            with window.db:
                window.db.execute('INSERT INTO tt_voice_profiles VALUES (?,?,?)',
                                  ('TT 测试', 'tt-reference.wav', '参考稿'))
            window.recognition.describe('识别图片中的文字。')
            row = self.wait_for_turn(window)
            self.assertIn(original, row.content)
            self.assertIn('保留原文语言', request_instructions[0])
            play.assert_called_once()
            self.assertEqual(play.call_args.args[0], original)
            self.assertEqual(play.call_args.args[1]['voice_reference'], 'tt-reference.wav')
            self.assertEqual(play.call_args.args[1]['voice_reference_text'], '参考稿')

    def test_speech_backends_route_only_four_languages(self):
        from tt_indextts_playback import IndexPlayback

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'model').mkdir()
            (root / 'model' / 'model.safetensors').touch()
            reference = root / 'reference.wav'
            reference.touch()
            statuses = []
            player = IndexPlayback(None, root, statuses.append, lambda: None)
            settings = dict(volume=70, voice_reference=str(reference), voice_reference_text='参考稿')
            with patch('tt_indextts_playback.QWEN_RUNTIME', root), patch.object(player, 'start_worker') as start:
                for text, language, backend in (
                    ('请朗读这段文字。', None, 'index'),
                    ('Please read this sentence out loud.', None, 'index'),
                    ('おはようございます。今日もいい天気ですね。', 'ja', 'qwen'),
                    ('안녕하세요. 오늘도 함께해요.', 'ko', 'qwen'),
                ):
                    player.play(text, settings)
                    self.assertEqual(start.call_args.args[0], backend)
                    self.assertEqual(player.job.clip.options.get('language'), language)
                    if language:
                        self.assertEqual(player.job.clip.options['reference_text'], '参考稿')
                    player.job = None
                    player.stop()
                start.reset_mock()
                player.play('Bonjour, le train arrive à la gare.', settings)
                start.assert_not_called()
                self.assertTrue(statuses[-1].startswith('失败'))
            player.shutdown()

    def test_live_reply_finishes_only_once(self):
        from tt_live_reply import LiveReply

        live = LiveReply('test', {})
        self.assertEqual(live.finish('最后一句。'), '最后一句。')
        self.assertEqual(live.finish('最后一句。'), '')

    def test_common_requests_and_privacy(self):
        for prompt, kind, query, platform in (
            ('你发一首歌我听听呗', 'music', '', ''),
            ('给我发周杰伦的《晴天》听听', 'music', '周杰伦 晴天', ''),
            ('用网易云发一首歌', 'music', '', 'netease'),
            ('发我一首歌，网易云的', 'music', '', 'netease'),
            ('用 Apple Music 发一首歌', 'music', '', 'apple'),
            ('我想听米津玄师的《Lemon》', 'music', '米津玄师 Lemon', ''),
            ('我想听日文歌', 'music', '', ''),
            ('给我发个网易云音乐的宣传视频', 'video', '网易云音乐的宣传', ''),
            ('给我视频链接', 'video', '', ''),
            ('黑魂攻略的视频链接发我', 'video', '黑魂攻略', ''),
            ('给我发个关于黑魂的 B 站视频链接', 'video', '黑魂', 'bilibili'),
            ('给我发一个 YouTube 的猫咪视频链接', 'video', '猫咪', 'youtube'),
            ('给我发一张猫咪的照片', 'image', '猫咪', ''),
            ('给我发张小猫的图', 'image', '小猫', ''),
        ):
            with self.subTest(prompt=prompt):
                request = share_request(prompt)
                self.assertEqual((request.kind, request.query, request.platform), (kind, query, platform))
        for prompt in ('你喜欢听什么歌？', '帮我分析这张图片', '来分析下我拍的照片',
                       '我想听你说话', '我想听这首歌怎么样', '我想听日文歌的历史',
                       '流行歌是什么？', '有没有流行歌的历史资料', '不要联网，换一首流行歌',
                       '不要联网，给我发一张猫咪照片', '发一张我上传的照片', '发张我刚拍的照片',
                       '给我发一张密码：123 的图片', '帮我看看这个视频 https://youtu.be/abcdefghijk'):
            with self.subTest(prompt=prompt):
                self.assertIsNone(share_request(prompt))

    def test_music_source_selection(self):
        song = music_card('https://music.163.com/song?id=66842', '十年', '陈奕迅')
        with patch('tt_share_content.choose_netease_music', return_value=song) as netease, \
                patch('tt_share_content.choose_music', return_value=None) as apple:
            cancel = threading.Event()
            named = share_request('发陈奕迅的《十年》听听')
            self.assertEqual(find_requested_media(named, cancel, preferred_music='netease'), song)
            netease.assert_called_once()
            apple.assert_not_called()
            netease.reset_mock()
            self.assertEqual(find_requested_media(named, cancel, preferred_music='apple'), song)
            apple.assert_called_once()
            netease.assert_called_once()
            netease.reset_mock(return_value=True)
            apple.reset_mock()
            explicit = share_request('用网易云发陈奕迅的《十年》听听')
            netease.return_value = None
            self.assertIsNone(find_requested_media(explicit, cancel, preferred_music='apple'))
            apple.assert_not_called()
            self.assertIn('music.163.com', explicit.search_url('apple'))

    def test_bare_song_request_uses_the_conversation_topic(self):
        ask = '那你直接发我一首呗'
        for prior, answer, query in (
            ('你喜欢 supercell 吗？', '你最喜欢他们哪首？', 'supercell'),
            ('你喜欢 supercell 吗？', '我喜欢《君の知らない物語》。你最喜欢他们哪首？',
             'supercell 君の知らない物語'),
            (ask, '🎧 supercell《君の知らない物語》\n就这首，陪你听听。', 'supercell 君の知らない物語'),
            ('你认识周杰伦吗？', '当然呀，他的歌很好听。', '周杰伦'),
        ):
            with self.subTest(prior=prior, answer=answer):
                request = share_request(ask, history=[(prior, answer, ())])
                self.assertEqual((request.kind, request.query), ('music', query))
        genre = share_request(ask, history=[('有好听的日文歌吗', '你平时听哪首？', ())])
        self.assertEqual(genre.music_style, 'japanese')
        self.assertIsNone(share_request(ask, history=[('聊聊古诗', '你最喜欢哪首诗？', ())]))
        self.assertIsNone(share_request(ask, history=[('给我推荐一本书', '可以看《小王子》。', ())]))

    def test_contextual_song_request_replies_with_a_card(self):
        song = music_card('https://music.apple.com/tw/song/test/1537428425', '君の知らない物語', 'supercell')

        def chat_reply(worker):
            worker.publish('completed', '我喜欢《君の知らない物語》。你最喜欢他们哪首？')

        with self.latency_window() as window, \
                patch.object(launcher.base.RequestWorker.__mro__[1], 'run', new=chat_reply), \
                patch('tt_media_share.fetch_card_previews', side_effect=lambda cards, *_: cards), \
                patch('tt_plus_search.find_requested_media', return_value=song) as find:
            window.send_prompt('你喜欢 supercell 吗？')
            self.wait_for_turn(window)
            window.restore_session(window.session, '恢复对话')
            window.send_prompt('那你直接发我一首呗')
            row = self.wait_for_turn(window)
            self.assertEqual(find.call_args.args[0].query, 'supercell 君の知らない物語')
            self.assertEqual(window.media.card_for(row.turn), song)
            self.assertTrue(row.body.parentWidget().isHidden())

    def test_japanese_song_tries_music_platforms_before_video(self):
        request = share_request('我想听日文歌')
        self.assertEqual(request.music_style, 'japanese')
        bili = video_card('https://www.bilibili.com/video/BV1KW411w7LX/', '米津玄師 Lemon 官方 MV')
        youtube = video_card('https://www.youtube.com/watch?v=abcdefghijk', '米津玄師 Lemon 官方 MV')
        for available, expected in (
            ('netease', ['Apple Music', '网易云音乐']),
            ('bilibili', ['Apple Music', '网易云音乐', 'bilibili']),
            ('youtube', ['Apple Music', '网易云音乐', 'bilibili', 'youtube']),
        ):
            with self.subTest(available=available):
                attempted = []

                def catalog(_client, item, _cancelled):
                    attempted.append(item.source)
                    if item.source == '网易云音乐' and available == 'netease':
                        return 'Lemon', '米津玄師', 'Lemon', ''
                    return ()

                def search_video(_client, source, query, _seen, _cancelled, **_options):
                    attempted.append(source)
                    self.assertIn('Lemon', query)
                    return {'bilibili': bili, 'youtube': youtube}[source] if source == available else None

                with patch('tt_share_content.music_metadata', side_effect=catalog), \
                        patch('tt_share_content.search_bilibili', return_value=None), \
                        patch('tt_share_content.choose_video', side_effect=search_video):
                    item = find_requested_media(request, threading.Event(), preferred_music='apple')
                self.assertEqual(attempted, expected)
                self.assertEqual(item.source, '网易云音乐' if available == 'netease' else available)

    def test_japanese_music_request_and_followup_show_cards(self):
        def catalog(_client, item, _cancelled):
            return item.title, item.detail, '测试专辑', ''

        with self.latency_window() as window, \
                patch('tt_media_share.fetch_card_previews', side_effect=lambda cards, *_: cards), \
                patch('tt_share_content.music_metadata', side_effect=catalog):
            for prompt, expected in (('我想听日文歌', 'Lemon'), ('换一首', '打上花火')):
                window.send_prompt(prompt)
                row = self.wait_for_turn(window)
                item = window.media.card_for(row.turn)
                self.assertEqual(item.title, expected)
                self.assertEqual(row.media_card.kind, 'music')
                self.assertTrue(row.body.parentWidget().isHidden())

    def test_pop_requests_choose_vocal_tracks_on_both_platforms(self):
        for prompt in ('为什么总是鼓点歌，没有流行歌吗', '换一首流行歌', '给我发一首流行歌曲',
                       '我想听有人唱的', '发一首带人声的歌'):
            with self.subTest(prompt=prompt):
                request = share_request(prompt)
                self.assertEqual((request.kind, request.query, request.music_style), ('music', '', 'pop'))
        cancel = threading.Event()
        request = share_request('为什么总是鼓点歌，没有流行歌吗')
        apple = find_requested_media(request, cancel, preferred_music='apple')
        self.assertEqual((apple.title, apple.detail, apple.source), ('晴天', '周杰伦', 'Apple Music'))
        album_link = 'https://music.apple.com/tw/album/sunny-day/535824731?i=535824738'
        netease = find_requested_media(share_request('用网易云换一首流行歌'), cancel,
                                       preferred_music='apple', recent_music=(album_link,))
        self.assertEqual((netease.title, netease.detail, netease.source), ('十年', '陈奕迅', '网易云音乐'))
        self.assertEqual(share_request('给我发首纯音乐').music_style, 'instrumental')

    def test_short_replacements_use_the_recent_music_card(self):
        url = 'https://music.163.com/song?id=186016'
        music = ('发一首歌', f'\n【分享的网易云音乐内容】晴天\n{url}', ())
        for prompt in ('换一种', '换一个吧', '换一首', '再来一首', '换首别的歌'):
            with self.subTest(prompt=prompt):
                request = share_request(prompt, history=[music])
                self.assertEqual((request.kind, request.platform), ('music', 'netease'))
                self.assertIsNone(share_request(prompt))
        retry = share_request('换一个', history=[music, ('换一种', '我发不了音乐卡片。', ())])
        self.assertEqual(retry.platform, 'netease')
        override = share_request('用 Apple Music 换一个', history=[music])
        self.assertEqual(override.platform, 'apple')
        for prompt, history in (
            ('换一种', [music, ('聊聊别的', '你想聊什么？', ())]),
            ('换一种', [('给我视频', '\n【分享的B 站内容】小猫\nhttps://www.bilibili.com/video/BV1orsc68Ej4/', ())]),
            ('换一种', [('这首歌怎么样？', f'可以点这里听 {url}', ())]),
            ('别联网，换一种', [music]),
        ):
            self.assertIsNone(share_request(prompt, history=history))

    def test_pop_followup_is_a_card_only_reply_and_restores_from_history(self):
        with self.latency_window() as window, patch('tt_media_share.fetch_card_previews', side_effect=lambda cards, *_: cards):
            titles = []
            for prompt in ('为什么总是鼓点歌，没有流行歌吗', '换一种', '换一个'):
                window.send_prompt(prompt)
                row = self.wait_for_turn(window)
                item = window.media.card_for(row.turn)
                titles.append(item.title)
                self.assertEqual(row.media_card.kind, 'music')
                self.assertTrue(row.body.parentWidget().isHidden())
                saved = window.db.execute('SELECT answer,status FROM turns WHERE id=?', (row.turn,)).fetchone()
                self.assertEqual(saved, ('', 'completed'))
                window.restore_session(window.session, '还原聊天')
                restored = window.chat_messages.rows[-1]
                self.assertEqual(restored.media_card.kind, 'music')
                self.assertTrue(restored.body.parentWidget().isHidden())
            self.assertEqual(titles, ['晴天', '十年', '遇见'])

    def wait_for_turn(self, window):
        deadline = time.monotonic() + 6
        while (window.busy() or window.pending_id is not None) and time.monotonic() < deadline:
            self.app.processEvents()
            threading.Event().wait(0.01)
        self.app.processEvents()
        self.assertFalse(window.busy())
        self.assertIsNone(window.pending_id)
        return window.chat_messages.rows[-1]

    def test_ctrl_v_screenshot_uses_existing_image_attachment_flow(self):
        image = QImage(2250, 100, QImage.Format.Format_RGB32)
        image.fill(0xFF743FC4)
        clipboard = self.app.clipboard()
        payloads = []

        def capture(worker):
            payloads.append(worker.chat_payload())
            worker.publish('completed', '看到了。')

        try:
            with self.latency_window() as window, patch.object(launcher.base.RequestWorker, 'run', new=capture):
                clipboard.setImage(image)
                QTest.keyClick(window.input, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(window.input.toPlainText(), '')
                self.assertEqual(len(window.attachments), 1)
                self.assertEqual(window.prepare_images()[0].image.width(), 2048)
                self.assertTrue(window.send_button.isEnabled())
                self.assertFalse(payloads)
                window.input.setPlainText('这首歌不错')
                window.send_button.click()
                self.wait_for_turn(window)
                parts = payloads[0]['input'][-1]['content']
                self.assertEqual(parts[-1]['type'], 'input_image')
                self.assertTrue(parts[-1]['image_url'].startswith('data:image/png;base64,'))
                self.assertEqual(window.attachments, [])
                for _ in range(2):
                    sent = next(row for row in window.chat_messages.rows if not row.assistant)
                    self.assertEqual(sent.body.toPlainText(), '这首歌不错')
                    self.assertEqual(len(sent.thumbnails), 1)
                    self.assertFalse(sent.thumbnails[0][0].pixmap().isNull())
                    window.chat_messages.reset()
        finally:
            clipboard.clear()

    def test_followup_previews_stay_with_corresponding_message(self):
        started = threading.Event()
        first = QImage(80, 60, QImage.Format.Format_RGB32)
        second = QImage(80, 60, QImage.Format.Format_RGB32)
        first.fill(0xFFCC2200)
        second.fill(0xFF0033CC)

        def reply(worker):
            if not started.is_set():
                started.set()
                worker.cancelled.wait(3)
            else:
                worker.publish('completed', '收到两张图片。')

        with self.latency_window() as window, patch.object(launcher.base.RequestWorker, 'run', new=reply):
            window.send_prompt('第一张', images=(ChatImage(first),))
            self.assertTrue(started.wait(2))
            window.send_prompt('第二张', images=(ChatImage(second),))
            self.wait_for_turn(window)
            for _ in range(2):
                rows = [row for row in window.chat_messages.rows if not row.assistant]
                self.assertEqual([row.body.toPlainText() for row in rows], ['第一张', '第二张'])
                self.assertEqual([len(row.thumbnails) for row in rows], [1, 1])
                colors = [row.thumbnails[0][1].toImage().pixelColor(0, 0) for row in rows]
                self.assertGreater(colors[0].red(), colors[0].blue())
                self.assertGreater(colors[1].blue(), colors[1].red())
                window.chat_messages.reset()

    def test_video_and_music_cover_cards_are_kept_on_both_sides(self):
        image = QImage(120, 68, QImage.Format.Format_RGB32)
        image.fill(0xFF743FC4)
        cover = bytes(ChatImage.encode(image, 'JPEG'))
        video = replace(link_card('https://www.bilibili.com/video/BV1orsc68Ej4/'),
                        title='小狗第一次收到它的小惊喜', author='酒酿小猫鱼丸', duration=94, image=cover)
        tracks = [
            music_card('https://music.163.com/song?id=66842', '十年', '陈奕迅'),
            music_card('https://music.apple.com/cn/song/test/42', '示例歌曲', '示例歌手'),
        ]

        def enrich(cards, *_args):
            return tuple(video if card.kind == 'video' else replace(card, album='示例专辑', image=cover)
                         for card in cards)

        def finish(worker):
            worker.publish('completed', '一起看看。')

        with self.latency_window() as window, patch('tt_media_share.fetch_card_previews', side_effect=enrich), \
                patch.object(launcher.base.RequestWorker.__mro__[1], 'run', new=finish):
            window.send_prompt('一起看\n' + video.url)
            self.wait_for_turn(window)
            for track in tracks:
                window.send_prompt(track.url)
                self.wait_for_turn(window)
            for item in [video, *tracks]:
                with patch('tt_plus_search.find_requested_media', return_value=replace(item, image=b'')):
                    query = '给我发个小猫的视频' if item.kind == 'video' else '给我发一首歌'
                    window.send_prompt(query)
                    self.wait_for_turn(window)
            deadline = time.monotonic() + 3
            while window.media.card_jobs and time.monotonic() < deadline:
                self.app.processEvents()
                threading.Event().wait(.01)
            self.assertFalse(window.media.card_jobs)
            window.chat_messages.reset()
            media_rows = [row for row in window.chat_messages.rows if row.media_card]
            self.assertEqual([row.assistant for row in media_rows], [False, False, False, True, True, True])
            self.assertEqual(media_rows[0].body.toPlainText(), '一起看')
            self.assertNotIn(video.url, media_rows[3].body.toPlainText())
            for row in media_rows:
                self.assertFalse(row.media_card.cover.isNull())
            with patch.object(window.media, 'watch_video') as watch:
                media_rows[0].media_card.video_cover.click()
                watch.assert_called_once_with(video.url, video.title, video.detail)

    def test_clipboard_file_and_windows_png_follow_attachment_limit(self):
        image = QImage(80, 60, QImage.Format.Format_RGB32)
        image.fill(0xFF743FC4)
        clipboard = self.app.clipboard()
        try:
            with self.latency_window() as window, tempfile.TemporaryDirectory() as folder:
                clipboard.setText('保留普通文本粘贴')
                window.input.paste()
                self.assertEqual(window.input.toPlainText(), '保留普通文本粘贴')

                file = Path(folder) / '截图.png'
                self.assertTrue(image.save(str(file)))
                files = QMimeData()
                files.setUrls([QUrl.fromLocalFile(str(file))])
                clipboard.setMimeData(files)
                window.input.paste()
                self.assertEqual(window.attachments[0]['path'], file)

                pixels = QMimeData()
                pixels.setData('application/x-qt-windows-mime;value="PNG"', ChatImage.encode(image, 'PNG'))
                clipboard.setMimeData(pixels)
                for _ in range(3):
                    window.input.paste()
                self.assertEqual(len(window.attachments), 3)
                self.assertEqual(window.status.text(), '一次最多添加 3 个附件。')
                self.assertEqual(window.input.toPlainText(), '保留普通文本粘贴')
        finally:
            clipboard.clear()

    @contextmanager
    def latency_window(self):
        base = launcher.base
        with tempfile.TemporaryDirectory() as settings, patch.dict(os.environ, {'LOCALAPPDATA': settings}), \
                patch.object(base.auth, 'load_credentials', return_value=None):
            window = base.ChatWindow()
            try:
                self.app.processEvents()
                window.pro_timer.stop()
                window.long_memory.timer.stop()
                window.models.addItem('Test', 'test-model')
                window.identity = 'latency-test'
                window.consent.setChecked(True)
                window.web_enabled.setChecked(True)
                yield window
            finally:
                window.close()
                window.begin_shutdown()
                window.pro_shutdown()
                window.db.close()

    def test_ping_and_duplicate_do_not_restart_media_search(self):
        started, release = threading.Event(), threading.Event()
        video = video_card('https://www.bilibili.com/video/BV1orsc68Ej4/', '小猫测试')

        def search(request, cancelled, **kwargs):
            started.set()
            release.wait(3)
            self.assertFalse(cancelled.is_set())
            return video

        with self.latency_window() as window, patch('tt_plus_search.find_requested_media', side_effect=search) as lookup:
            try:
                window.send_prompt('给我发小猫视频')
                self.assertTrue(started.wait(2))
                self.app.processEvents()
                self.assertIn('正在查找视频', window.status.text())
                turn = window.pending_id
                worker = window.worker
                window.send_prompt('你还在吗')
                window.send_prompt('给我发小猫视频')
                self.assertIs(window.worker, worker)
                self.assertEqual(window.pending_id, turn)
                self.assertEqual(window.db.execute('SELECT count(*) FROM tt_turn_inputs WHERE turn_id=?', (turn,)).fetchone(), (1,))
            finally:
                release.set()
            self.assertEqual(window.media.card_for(self.wait_for_turn(window).turn), video)
            lookup.assert_called_once()

    def test_newest_followup_can_request_video_without_model(self):
        self.assertEqual(routing_input('你还在吗'), '')
        self.assertTrue(routing_input('广州塔几点关门？'))
        first = threading.Event()
        video = video_card('https://www.bilibili.com/video/BV1orsc68Ej4/', '小猫测试')

        def wait_for_cancellation(worker):
            self.assertFalse(worker.routing_prompt)
            first.set()
            worker.cancelled.wait(3)

        with self.latency_window() as window, \
                patch.object(launcher.base.RequestWorker.__mro__[1], 'run', new=wait_for_cancellation), \
                patch('tt_plus_search.find_requested_media', return_value=video) as lookup:
            window.send_prompt('你还在吗')
            self.assertTrue(first.wait(2))
            window.send_prompt('给我发个小猫的视频')
            self.assertEqual(window.media.card_for(self.wait_for_turn(window).turn), video)
            lookup.assert_called_once()

    def test_worker_exit_without_reply_finishes_only_once(self):
        attempts = []

        def exit_without_signals(worker):
            attempts.append(worker.mode)

        with self.latency_window() as window, patch.object(launcher.base.RequestWorker, 'run', new=exit_without_signals):
            window.send_prompt('你还在吗')
            self.wait_for_turn(window)
            self.assertEqual(attempts, ['chat'])
            self.assertIn('未收到完整回复', window.status.text())

    def test_first_reply_timeout_releases_pending_turn(self):
        started = threading.Event()

        def wait_for_cancellation(worker):
            started.set()
            worker.cancelled.wait(3)

        with self.latency_window() as window, patch.object(launcher.base.RequestWorker, 'run', new=wait_for_cancellation):
            window.send_prompt('你还在吗')
            self.assertTrue(started.wait(2))
            window.first_reply_timer.timeout.emit()
            self.wait_for_turn(window)
            self.assertIn('超过 35 秒', window.status.text())

    def test_cards_show_in_live_reply_and_saved_history(self):
        graphic = QImage(8, 8, QImage.Format.Format_RGB32)
        graphic.fill(0xFF743FC4)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(graphic.save(buffer, 'PNG'))
        image = image_card(bytes(data), '猫咪')

        requests = (
            ('你发一首歌我听听呗', music_card('https://music.apple.com/cn/song/test/42', '测试歌曲', '测试歌手')),
            ('给我发个黑魂攻略视频链接', video_card('https://www.bilibili.com/video/BV1KW411w7LX/', '黑魂攻略')),
            ('给我发张猫咪照片', SharedMedia('image', '必应图片', image.title, '', 'https://example.com/cat', image.image)),
        )
        base = launcher.base
        with tempfile.TemporaryDirectory() as settings, patch.dict(os.environ, {'LOCALAPPDATA': settings}), \
                patch.object(base.auth, 'load_credentials', return_value=None):
            window = base.ChatWindow()
            try:
                window.pro_timer.stop()
                window.models.addItem('Test', 'test-model')
                window.identity = 'test'
                window.consent.setChecked(True)
                window.web_enabled.setChecked(True)

                for prompt, item in requests:
                    with self.subTest(prompt=prompt), patch('tt_plus_search.find_requested_media', return_value=item) as find:
                        window.input.setPlainText(prompt)
                        window.send()
                        row = self.wait_for_turn(window)
                        find.assert_called_once()
                        self.assertEqual(window.media.card_for(row.turn), item)
                        self.assertIsNotNone(row.media_card)
                        self.assertIn(item.title, window.session_context(window.session)[-1][1])
                        self.assertIn(item.title, window.history[-1][1])
                        if item.kind in ('music', 'video'):
                            buttons = row.media_card.findChildren(QPushButton)
                            self.assertIn('打开歌曲' if item.kind == 'music' else '和 TT 一起看',
                                          [button.text() for button in buttons])
                        if item.kind == 'image':
                            self.assertIn(item.url, row.content)

                with patch('tt_plus_search.find_requested_media', return_value=None):
                    window.send_prompt('发我一张月亮的图片')
                    row = self.wait_for_turn(window)
                    self.assertIn('打开搜索结果', row.content)
                    self.assertIsNone(row.media_card)

                window.web_enabled.setChecked(False)
                with patch('tt_plus_search.find_requested_media') as find:
                    window.send_prompt('你发一首歌我听听呗')
                    row = self.wait_for_turn(window)
                    find.assert_not_called()
                    self.assertIn('启用自动联网查', row.content)

                window.chat_messages.reset()
                self.assertEqual(sum(row.media_card is not None for row in window.chat_messages.rows), 3)
            finally:
                window.pro_shutdown()
                window.long_memory.timer.stop()
                window.close()
                window.db.close()


if __name__ == '__main__':
    unittest.main()
