"""Local-only proactive policy. No model, tools, history or file access."""
import random

from tt_natural_speech import STRENGTH
from datetime import datetime


DEFAULTS = dict(enabled=False, voice=False, speech_enabled=False, reply_voice=False,
                rate=0, muted=False, volume=50, voice_name='',
                speech_style='auto', emotion_strength=STRENGTH, fast_speech=False, pause_voice_in_games=True,
                start=540, end=1320, dnd=False, dnd_start=1320, dnd_end=540,
                interval=120, limit=3)
PHRASES = (
    '桌边好安静呀。\n\n你有空的话，我想听听今天有什么新鲜事。',
    '坐在这儿也挺舒服的，你按自己的节奏来呀。',
    '我有点想找你说说话。\n\n刚刚在忙什么呀？',
    '嘿，来给你一点打气。忙完再理我也行。',
    '想放空一会儿吗？\n\n我可以陪你安静坐坐。',
    '好久没说话啦。\n\n要不要聊一件今天的小事？',
)


def in_window(minute, start, end):
    if start == end:
        return True  # Explicitly displayed as all day in settings.
    return start <= minute < end if start < end else minute >= start or minute < end


class Scheduler:
    def __init__(self, settings, state, save, now, rng=None):
        self.settings = settings
        self.state = state
        self.save = save
        self.rng = rng or random.Random()
        self.last_tick = now
        self.zone = self.local(now).utcoffset()
        self.defer(now)  # Never deliver immediately on restart.

    @staticmethod
    def local(now):
        return datetime.fromtimestamp(now).astimezone()

    def defer(self, now):
        delay = self.settings['interval'] * 60
        delay *= 1 + min(self.state.get('unanswered', 0), 3)
        self.state['due'] = max(now, self.state.get('last', 0)) + delay * self.rng.uniform(1, 1.5)
        self.save()

    def allowed(self, now):
        local = self.local(now)
        minute = local.hour * 60 + local.minute
        s = self.settings
        return (s['enabled'] and not s['dnd']
                and self.state.get('quiet_day') != local.date().isoformat()
                and now >= self.state.get('snooze', 0)
                and in_window(minute, s['start'], s['end'])
                and not in_window(minute, s['dnd_start'], s['dnd_end']))

    def activity(self, now):
        self.state['unanswered'] = 0
        self.defer(now)

    def tick(self, now, busy=False):
        local = self.local(now)
        day = local.date().isoformat()
        discontinuity = now - self.last_tick > 180 or now < self.last_tick or self.zone != local.utcoffset()
        self.last_tick, self.zone = now, local.utcoffset()
        if self.state.get('day') != day:
            # A rolling 24h cap also prevents clock/timezone changes resetting the quota.
            self.state.update(day=day, count=0)
            discontinuity = True
        if discontinuity or busy:
            self.defer(now)
            return False
        recent = [t for t in self.state.get('sent', []) if now - t < 86400]
        if (not self.allowed(now) or self.state.get('count', 0) >= self.settings['limit']
                or len(recent) >= self.settings['limit'] or now < self.state['due']):
            return False
        self.state.update(last=now, count=self.state.get('count', 0) + 1,
                          unanswered=self.state.get('unanswered', 0) + 1, sent=recent + [now])
        self.defer(now)  # Commit quota before publishing; never retry a missed delivery.
        return True

    def message(self):
        quiet = self.state.get('unanswered', 0) > 1
        choices = [p for p in PHRASES if p != self.state.get('phrase')
                   and (not quiet or '\n\n' not in p)]
        text = self.rng.choice(choices)
        self.state['phrase'] = text
        self.save()
        return text
