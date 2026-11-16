from dataclasses import dataclass
from enum import StrEnum


class State(StrEnum):
    IDLE = "idle"
    LOOK_AROUND = "look_around"
    READING = "reading"
    LISTENING_MUSIC = "listening_music"
    PETTING_CAT = "petting_cat"
    STRETCHING = "stretching"
    AFTERNOON_TEA = "afternoon_tea"
    EMBROIDERY = "embroidery"
    SERVE_TEA = "serve_tea"
    TIDY_CLEAN = "tidy_clean"
    HUG_PLUSH = "hug_plush"
    USE_PHONE = "use_phone"
    TAKE_PHOTO = "take_photo"
    TALKING = "talking"
    MOUSE_HOVER = "mouse_hover"
    MOUSE_CLICK = "mouse_click"
    DRAGGING = "dragging"


PRIORITY = {"auto": 1, "user": 2, "voice": 3, "click": 4, "drag": 5}


@dataclass(frozen=True)
class Request:
    state: State
    expires: float | None


class StateMachine:
    """Each source owns one request; temporary overrides preserve lower requests."""

    def __init__(self):
        self.requests = {}

    def request(self, source, state, now, duration=None):
        if source not in PRIORITY:
            raise ValueError("Unknown state source")
        if duration is not None and duration <= 0:
            raise ValueError("Duration must be positive")
        self.requests[source] = Request(State(state), None if duration is None else now + duration)

    def release(self, source):
        self.requests.pop(source, None)

    def current(self, now):
        self.requests = {
            source: item for source, item in self.requests.items()
            if item.expires is None or now < item.expires
        }
        if not self.requests:
            return State.IDLE
        source = max(self.requests, key=PRIORITY.__getitem__)
        return self.requests[source].state
