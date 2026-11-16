"""Interpolate a player timeline in monotonic time between real samples."""


class PlaybackClock:
    def __init__(self):
        self.identity = None
        self.status = ''
        self.position = 0.0
        self.duration = 0
        self.at = 0.0
        self.observed_at = 0.0
        self.rate = 1.0
        self.correction = 0.0
        self.revision = None

    def value(self, now):
        if self.status!='Playing':
            return round(self.position)
        limit = self.observed_at+6
        elapsed = max(0,min(now,limit)-self.at)
        return round(max(0,min(self.duration,self.position+elapsed*self.rate*1000
                                +self.correction*min(elapsed/2,1))))

    def update(self, identity, status, position, duration, at, *, rate=1.0, revision=None, reset=False):
        position = min(max(0,position),duration)
        changed = self.identity!=identity or self.status!=status or reset
        shown = self.value(at)
        self.position = position if changed or status!='Playing' else min(shown,duration)
        self.correction = 0 if changed or status!='Playing' else max(-800*rate,min(1500*rate,position-self.position))
        self.identity, self.status, self.duration = identity,status,duration
        self.at, self.observed_at, self.rate, self.revision = at,at,rate,revision
