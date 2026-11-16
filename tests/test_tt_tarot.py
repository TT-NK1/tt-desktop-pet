from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from tt_tarot import TarotManager
from tt_tarot_companion import TarotCompanion
from tt_tarot_ui import ZoomDialog
from tt_theme import DARK, LIGHT, ThemeManager
from tt_indextts_playback import IndexPlayback
import tt_tarot


DATA = Path(os.environ.get("TT_TAROT_DATA_ROOT", Path(__file__).resolve().parents[1]))
ART = Path(os.environ.get("TT_TAROT_ART_ROOT", DATA)) / "assets" / "tarot"
FIXTURE = Path(os.environ.get("TT_TAROT_TEST_DIR", Path.cwd()))
TODAY = datetime(2026, 9, 4, 23, 59)


class TarotTests(unittest.TestCase):
    def setUp(self):
        self.now = TODAY
        self.file = FIXTURE / f"tarot-test-{uuid4().hex}.sqlite3"
        self.db = sqlite3.connect(self.file)
        self.db.execute("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("INSERT INTO settings VALUES ('outfit','street')")
        self.db.commit()
        values = iter((17, 1, 6, 0, 0, 0))
        self.manager = TarotManager(DATA, self.db, now=lambda: self.now, randbelow=lambda n: next(values))
        self.manager.root = ART

    def tearDown(self):
        self.db.close()
        self.file.unlink(missing_ok=True)

    def test_reopen_outfit_theme_midnight_and_history(self):
        start = self.manager.today()
        self.assertEqual(start.orientation, "逆位")
        self.manager.reveal(start.day)
        self.manager.select_theme(start.day, "work")
        self.manager.save_reading(start.day, "work", "测试解读")
        self.manager.set_favorite(start.card_id, True)
        self.db.execute("UPDATE settings SET value='gothic' WHERE name='outfit'")
        self.db.commit()
        with closing(sqlite3.connect(self.file)) as reopened:
            again = TarotManager(DATA, reopened, now=lambda: self.now, randbelow=lambda _: self.fail("重复抽牌"))
            self.assertEqual(again.today().card_id, start.card_id)
            self.assertEqual(again.today().orientation, start.orientation)
            self.assertEqual(again.today().theme, "work")
            self.assertEqual(again.reading(start.day, "work"), "测试解读")
            self.assertEqual(again.favorites(), {start.card_id})
        script = """import sqlite3, sys
from datetime import datetime
sys.path.insert(0, sys.argv[1])
from tt_tarot import TarotManager
with sqlite3.connect(sys.argv[3]) as db:
    manager = TarotManager(sys.argv[2], db, now=lambda: datetime(2026, 9, 4, 23, 59))
    draw = manager.today()
    print(draw.card_id, int(draw.reversed))
"""
        process = subprocess.run(
            [sys.executable, "-c", script, str(Path(tt_tarot.__file__).parent), str(DATA), str(self.file)],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(process.stdout.strip(), f"{start.card_id} 1")
        self.now += timedelta(minutes=2)
        tomorrow = self.manager.today()
        self.assertNotEqual(tomorrow.day, start.day)
        self.assertEqual(tomorrow.orientation, "正位")
        self.assertEqual(self.manager.history(card_id=start.card_id)[0].day, start.day)
        self.manager.clear_history()
        self.assertIsNone(self.manager.find(start.day))
        self.assertEqual(self.manager.reading(start.day, "work"), "")
        self.assertEqual(self.manager.today(), tomorrow)
        self.assertEqual(self.manager.favorites(), {start.card_id})
        self.manager.set_favorite(start.card_id, False)
        self.assertFalse(self.manager.favorites())
        self.assertEqual(self.db.execute("SELECT value FROM settings WHERE name='outfit'").fetchone()[0], "gothic")

    def test_all_cards_both_orientations(self):
        values = iter(value for i in range(22) for direction in (0, 1) for value in (i, direction))
        self.manager.randbelow = lambda n: next(values)
        for i, item in enumerate(self.manager.cards.values()):
            for direction in (0, 1):
                draw = self.manager.today(TODAY + timedelta(days=2*i+direction))
                self.assertEqual((draw.card_id, draw.reversed), (item["id"], direction))
                self.assertTrue(self.manager.image_path(draw.card_id).is_file())
                self.assertIn(item[draw.meaning_prefix + "_meaning"], self.manager.interpretation(draw, "study"))
        self.assertEqual(len(self.manager.history(limit=50)), 44)

    def test_date_unique_across_connections(self):
        def access(number):
            connection = sqlite3.connect(self.file, timeout=10)
            try:
                return TarotManager(DATA, connection, now=lambda: self.now, randbelow=lambda n: number % n).today()
            finally:
                connection.close()
        with ThreadPoolExecutor(8) as workers:
            outcomes = list(workers.map(access, range(16)))
        self.assertEqual(len(set(outcomes)), 1)
        self.assertEqual(len(self.manager.history()), 1)

    def test_setting_and_reminder_snooze(self):
        self.manager.set_setting("enabled", False)
        self.assertIsNone(self.manager.today())
        self.manager.set_setting("enabled", True)
        draw = self.manager.today()
        self.manager.set_setting("reminders", False)
        self.assertFalse(self.manager.reminder_due(draw, self.now))
        self.assertEqual(self.manager.today(), draw)
        self.manager.set_setting("reminders", True)
        self.assertTrue(self.manager.reminder_due(draw, self.now))
        self.manager.remember_notification(draw.day, self.now.timestamp() + 30)
        self.assertFalse(self.manager.reminder_due(self.manager.find(draw.day), self.now))
        self.assertTrue(self.manager.reminder_due(self.manager.find(draw.day), self.now + timedelta(seconds=31)))
        self.manager.remember_notification(draw.day)
        self.assertFalse(self.manager.reminder_due(self.manager.find(draw.day), self.now + timedelta(seconds=31)))


class SimulatedWorker(QObject):
    completed = Signal(str)
    failed = Signal(str)
    fail = True
    calls = 0

    def __init__(self, mode, user, payload):
        super().__init__()
        self.payload = payload
        self.cancelled = threading.Event()

    def run(self):
        type(self).calls += 1
        assert self.payload["store"] is False
        if self.fail:
            self.failed.emit("离线测试")
        else:
            self.completed.emit("给自己留一点安静时间。")


class TarotUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        current = datetime(2026, 9, 4, 11)
        self.now = current
        self.db = sqlite3.connect(":memory:")
        choices = iter((17, 1, 4, 0))
        self.manager = TarotManager(DATA, self.db, now=lambda: self.now, randbelow=lambda n: next(choices))
        self.manager.root = ART
        self.pet = QWidget()
        self.room = QWidget()
        self.pet.chat = SimpleNamespace(busy=lambda: False, models=SimpleNamespace(currentData=lambda: "test"),
                                        consent=SimpleNamespace(isChecked=lambda: True), identity="fixture",
                                        theme=ThemeManager(self.room))
        self.pet.weather_busy = lambda: False
        self.pet.drag_start = None
        self.blocked = False
        self.service = TarotCompanion(self.pet, manager=self.manager, worker_factory=SimulatedWorker,
                                      busy=lambda: self.blocked)
        SimulatedWorker.fail = True
        SimulatedWorker.calls = 0

    def tearDown(self):
        self.service.close()
        self.pet.close()
        self.app.removeEventFilter(self.pet.chat.theme)
        self.room.close()
        self.db.close()

    def test_widget_flip_zoom_history_and_resize(self):
        self.service.open_history()
        self.assertEqual(self.manager.history(), [])
        self.service.open()
        window = self.service.window
        window.resize(330, 430)
        window.card.click()
        QTest.qWait(520)
        self.assertTrue(window.card.revealed)
        self.assertTrue(window.detail_read_button.isHidden())
        self.assertEqual(window.title.text(), "星星 · 逆位")
        self.assertEqual(window.card.face.width() * 3, window.card.face.height() * 2)
        zoom = ZoomDialog(self.manager.image_path(window.draw.card_id), "星星", window)
        self.assertEqual((zoom.original.width(), zoom.original.height()), (1200, 1800))
        zoom.close()
        window.favorite_button.click()
        window.tabs.setCurrentIndex(2)
        self.assertEqual(window.favorites_list.count(), 1)
        self.service.open_history()
        self.assertEqual(window.history_list.count(), 1)
        window.date_filter.setText("2026-09-01")
        self.assertEqual(window.history_list.count(), 0)
        window.date_filter.clear()
        window.history_item(window.history_list.item(0))
        self.assertEqual(window.draw.card_id, "17_The_Star")
        window.resize(620, 820)
        self.app.processEvents()
        if screenshot := os.environ.get("TT_TAROT_CAPTURE"):
            self.assertTrue(window.grab().save(screenshot))
        self.assertEqual(len(self.manager.history()), 1)

    def test_ai_failure_fallback_then_saved_reading_no_duplicate(self):
        self.service.open()
        window = self.service.window
        self.manager.reveal(window.draw.day)
        window.refresh()
        fallback = window.detail.text()
        window.ai_button.click()
        self.assertIn("正在处理", self.service.request_ai(window.draw))
        QTest.qWait(200)
        self.assertEqual(SimulatedWorker.calls, 1)
        self.assertEqual(window.detail.text(), fallback)
        self.assertIn("固定牌义", window.status.text())
        self.assertTrue(window.detail_read_button.isHidden())
        SimulatedWorker.fail = False
        window.ai_button.click()
        QTest.qWait(200)
        self.assertIn("给自己", window.ai_text.text())
        self.assertFalse(window.detail_read_button.isHidden())
        self.assertEqual(self.service.request_ai(window.draw), "")
        self.assertEqual(SimulatedWorker.calls, 2)

    def test_reminder_busy_optout_and_live_midnight(self):
        self.blocked = True
        self.service.tick()
        self.assertIsNone(self.service.reminder)
        self.blocked = False
        self.manager.set_setting("reminders", False)
        self.service.tick()
        self.assertIsNone(self.service.reminder)
        self.manager.set_setting("reminders", True)
        self.service.tick()
        self.assertIsNotNone(self.service.reminder)
        self.service.snooze()
        self.service.tick()
        self.assertIsNone(self.service.reminder)
        self.service.open()
        day = self.service.window.draw.day
        self.now += timedelta(days=1)
        self.service.tick()
        self.assertNotEqual(self.service.window.draw.day, day)
        self.assertEqual(len(self.manager.history()), 2)

    def test_theme_reacts_to_chat_for_card_zoom_and_reminder(self):
        self.service.open()
        window = self.service.window
        zoom = ZoomDialog(self.manager.image_path(window.draw.card_id), "星星", window)
        self.service.show_reminder(window.draw.day)
        theme = self.pet.chat.theme
        for mode, scheme, palette in (("dark", None, DARK), ("light", None, LIGHT),
                                      ("system", Qt.ColorScheme.Dark, DARK),
                                      ("system", Qt.ColorScheme.Light, LIGHT)):
            theme.set_mode(mode)
            if scheme is not None:
                theme.system_changed(scheme)
            for widget in (window, zoom, self.service.reminder):
                self.assertIn("background: " + palette.background, widget.styleSheet())
                for value in (palette.text, palette.accent, palette.input, palette.border,
                              palette.button, palette.hover):
                    self.assertIn(value, widget.styleSheet())
        zoom.close()

    def test_voice_payloads_and_switching_both_directions(self):
        with patch("tt_voice_profiles.with_reference", return_value={
            "speech_enabled": True, "muted": False, "volume": 35, "voice_reference": __file__
        }):
            calls = []
            active = set()
            def play(text, cfg, **kwargs):
                owner = f"tarot-{len(calls)}"
                calls.append(("play", text, owner))
                active.add(owner)
                voice.active = True
                return owner
            def stop(owner):
                calls.append(("stop", owner))
                active.remove(owner)
                voice.active = bool(active)
                return True
            player = SimpleNamespace(has_audio=lambda owners: bool(active.intersection(owners)), stop_owner=stop)
            voice = SimpleNamespace(active=False, player=player, status="", play=play)
            self.pet.chat.pro_voice = voice
            self.pet.chat.db = self.db
            self.pet.chat.pro_settings = {}
            self.service.open()
            window = self.service.window
            draw = window.draw
            self.manager.reveal(draw.day)
            window.refresh()
            self.assertIn("请先生成", self.service.speak(draw, mode="detail"))
            self.assertEqual(calls, [])
            window.voice_button.click()
            text = calls[0][1]
            data = self.manager.cards[draw.card_id]
            self.assertIn("星星，逆位", text)
            for word in (*data["reversed_keywords"], data["reversed_meaning"],
                         data["daily_advice"], data["tt_message"]):
                self.assertIn(word, text)
            self.assertNotIn("每日塔罗", text)
            self.assertIn("简析朗读中", window.voice_button.text())
            self.manager.save_reading(draw.day, draw.theme, "详细正文只出现在这里。")
            window.refresh()
            window.detail_read_button.click()
            self.assertEqual(calls[1], ("stop", calls[0][2]))
            self.assertTrue(calls[2][1].endswith("详细正文只出现在这里。"))
            self.assertNotIn("星星", calls[2][1])
            self.assertNotIn(data["daily_advice"], calls[2][1])
            self.assertIn("解读朗读中", window.detail_read_button.text())
            window.voice_button.click()
            self.assertEqual(calls[3], ("stop", calls[2][2]))
            self.assertEqual(calls[4][1], text)
            self.assertEqual(len(active), 1)
            self.assertIn("已停止", self.service.stop_voice())
            self.assertFalse(active)
            self.assertFalse(window.stop_button.isEnabled())

    def test_shared_player_stops_only_tarot_owner(self):
        from collections import deque
        from unittest.mock import Mock
        player = IndexPlayback(self.pet, FIXTURE, lambda *_: None, lambda: None)
        player.pending = deque(SimpleNamespace(owner=name) for name in ("tarot", "chat"))
        player.current = "tarot"
        player.job = SimpleNamespace(clip=SimpleNamespace(owner="tarot"))
        player.buffered = (SimpleNamespace(owner="chat"), b"wav")
        player.advance = Mock()
        player.release_current = Mock(side_effect=lambda: setattr(player, "current", None))
        self.assertTrue(player.stop_owner("tarot"))
        self.assertTrue(player.cancelled)
        self.assertEqual([clip.owner for clip in player.pending], ["chat"])
        self.assertEqual(player.buffered[0].owner, "chat")
        self.assertIsNone(player.current)
        player.release_current.assert_called_once_with()
        self.assertFalse(player.stop_owner("tarot"))


if __name__ == "__main__":
    unittest.main()
