import math
import random


PRESETS = {
    "normal": {},
    "smile": {"ParamMouthForm": 0.35},
    "shy": {"ParamMouthForm": 0.15, "ParamCheek": 0.3},
    "blush": {"ParamCheek": 0.45},
    "sleepy": {"ParamEyeLOpen": 0.45, "ParamEyeROpen": 0.45},
    "puzzled": {"ParamBrowLY": 0.25, "ParamBrowRY": -0.1},
    "annoyed": {"ParamBrowLY": -0.25, "ParamBrowRY": -0.25, "ParamMouthForm": -0.2},
    "surprised": {"ParamBrowLY": 0.3, "ParamBrowRY": 0.3},
}


class ExpressionController:
    """Higher priority wins per parameter; actual model ranges bound all output."""

    def __init__(self, ranges, *, rng=None):
        self.ranges = ranges
        self.layers = {}
        self.values = {}
        self.rng = rng or random.Random()
        self.next_blink = self.rng.uniform(2.5, 6)

    def set(self, name, now, duration=1.2, priority=1):
        if name not in PRESETS:
            raise ValueError("Unknown expression")
        self.layers[name] = (now + duration, priority)

    def sample(self, now, dt, overrides=None):
        self.layers = {name: item for name, item in self.layers.items() if now < item[0]}
        targets = {key: spec[2] for key, spec in self.ranges.items()}
        for name in sorted(self.layers, key=lambda item: self.layers[item][1]):
            targets.update({key: value for key, value in PRESETS[name].items() if key in targets})
        if now >= self.next_blink + 0.2:
            self.next_blink = now + self.rng.uniform(2.5, 6)
        if self.next_blink <= now < self.next_blink + 0.2:
            blink = abs((now - self.next_blink) / 0.1 - 1)
            for key in ("ParamEyeLOpen", "ParamEyeROpen"):
                if key in targets:
                    targets[key] *= blink
        targets.update({key: value for key, value in (overrides or {}).items() if key in targets})
        weight = 1 - math.exp(-max(0, min(dt, 0.1)) * 18)
        for key, target in targets.items():
            low, high, default = self.ranges[key]
            target = max(low, min(high, target))
            self.values[key] = self.values.get(key, default) + (target - self.values.get(key, default)) * weight
        return dict(self.values)
