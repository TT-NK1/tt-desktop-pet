import random
import unittest
from datetime import datetime

from tt_proactive import DEFAULTS, Scheduler, in_window
from tt_proactive_voice import Voice


class ProactiveTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 8, 10).timestamp()
        self.settings = dict(DEFAULTS, enabled=True)
        self.state = {}
        self.saved = []
        self.scheduler = Scheduler(self.settings, self.state, lambda: self.saved.append(True), self.now, random.Random(1))
        self.scheduler.tick(self.now)

    def advance(self, target, busy=False):
        while self.scheduler.last_tick + 30 < target:
            self.scheduler.tick(self.scheduler.last_tick + 30, busy)
        return self.scheduler.tick(target, busy)

    def test_default_off_and_minimum_interval(self):
        self.assertFalse(DEFAULTS['enabled'])
        self.assertFalse(DEFAULTS['voice'])
        self.assertGreaterEqual(self.state['due'] - self.now, 7200)
        self.assertFalse(self.advance(self.now + 7199))

    def test_delivery_cap_and_nonresponse_backoff(self):
        self.settings['interval'] = 1
        self.settings['limit'] = 2
        self.scheduler.defer(self.now)
        self.advance(self.state['due'])
        self.assertEqual(self.state['count'], 1)
        self.assertGreaterEqual(self.state['due'] - self.state['last'], 120)
        self.advance(self.state['due'])
        self.assertEqual(self.state['count'], 2)
        self.assertFalse(self.advance(self.state['due']))

    def test_dnd_and_quiet_and_disabled_cancel(self):
        for key in ('dnd', 'quiet', 'disabled'):
            with self.subTest(key=key):
                self.settings.update(enabled=True, dnd=False)
                self.state.pop('quiet_day', None)
                if key == 'dnd':
                    self.settings['dnd'] = True
                elif key == 'quiet':
                    self.state['quiet_day'] = '2026-11-08'
                else:
                    self.settings['enabled'] = False
                self.state['due'] = self.now
                self.assertFalse(self.scheduler.tick(self.now))

    def test_sleep_and_restart_never_catch_up(self):
        self.state.update(due=self.now, count=2, unanswered=2)
        self.assertFalse(self.scheduler.tick(self.now + 3600))
        self.assertGreater(self.state['due'], self.now + 3600)
        restarted = Scheduler(self.settings, self.state, lambda: None, self.now + 7200)
        self.assertFalse(restarted.tick(self.now + 7200))
        self.assertEqual(self.state['count'], 2)

    def test_midnight_and_clock_backwards_defer(self):
        tomorrow = datetime(2026, 11, 9, 10).timestamp()
        self.state['count'] = 3
        self.assertFalse(self.scheduler.tick(tomorrow))
        self.assertEqual(self.state['count'], 0)
        self.assertFalse(self.scheduler.tick(self.now))

    def test_busy_and_reply_defer(self):
        self.state['due'] = self.now
        self.assertFalse(self.scheduler.tick(self.now, busy=True))
        self.state['unanswered'] = 3
        self.scheduler.activity(self.now)
        self.assertEqual(self.state['unanswered'], 0)
        self.assertGreater(self.state['due'], self.now)

    def test_time_windows(self):
        self.assertTrue(in_window(30, 1320, 540))
        self.assertFalse(in_window(600, 1320, 540))
        self.assertFalse(self.scheduler.allowed(datetime(2026, 11, 8, 23).timestamp()))

    def test_phrases_do_not_repeat(self):
        phrases = [self.scheduler.message() for _ in range(30)]
        self.assertTrue(all(a != b for a, b in zip(phrases, phrases[1:])))

    def test_snooze_and_rolling_cap(self):
        self.state['snooze'] = self.now + 600
        self.assertFalse(self.scheduler.allowed(self.now))
        self.state.update(snooze=0, sent=[self.now] * 3, due=self.now)
        self.assertFalse(self.scheduler.tick(self.now))

    def test_voice_failure_is_contained(self):
        class Broken:
            def play(self, text, settings):
                raise RuntimeError('no audio')
            def stop(self):
                pass
        voice = Voice.__new__(Voice)
        voice.player, voice.listeners, voice.seen = Broken(), [], []
        voice.play('你好。', dict(DEFAULTS, voice=True, speech_enabled=True))
        self.assertIn('失败', voice.status)
        voice.stop()

    def test_muted_voice_never_speaks(self):
        class Player:
            def stop(self):
                pass
            def play(self, text, settings):
                raise AssertionError('must not speak')
        voice = Voice.__new__(Voice)
        voice.player, voice.listeners = Player(), []
        voice.play('你好。', dict(DEFAULTS, voice=True, muted=True))
        self.assertEqual(voice.status, '已停止')


if __name__ == '__main__':
    unittest.main()
