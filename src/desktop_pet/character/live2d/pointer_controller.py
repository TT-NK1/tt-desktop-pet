from collections import deque
import math
import random

from .state_machine import State


class PointerController:
    def __init__(self, states, expressions, *, rng=None):
        self.states = states
        self.expressions = expressions
        self.rng = rng or random.Random()
        self.clicks = deque()
        self.last_click = -10
        self.x = self.y = 0.0

    def gaze(self, x, y, nearby, dt):
        weight = 1 - math.exp(-max(0, min(dt, 0.1)) * 6)
        tx, ty = (max(-1, min(1, x)), max(-1, min(1, y))) if nearby else (0, 0)
        self.x += (tx - self.x) * weight
        self.y += (ty - self.y) * weight
        return {"ParamEyeBallX": self.x * 0.65, "ParamEyeBallY": self.y * 0.65,
                "ParamAngleX": self.x * 12, "ParamAngleY": self.y * 8}

    def head_click(self, now):
        if now - self.last_click < 0.35:
            return False
        self.last_click = now
        self.clicks.append(now)
        while self.clicks and now - self.clicks[0] > 3:
            self.clicks.popleft()
        expression = "annoyed" if len(self.clicks) >= 4 else self.rng.choice(("smile", "smile", "shy"))
        self.expressions.set(expression, now, priority=5)
        self.expressions.next_blink = now
        self.states.request("click", State.MOUSE_CLICK, now, duration=1.2)
        return True

    def drag(self, active, now):
        if active:
            self.states.request("drag", State.DRAGGING, now)
        else:
            self.states.release("drag")
