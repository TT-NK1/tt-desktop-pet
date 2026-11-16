"""Schedule occasional outfit-specific actions once a fresh idle interval has elapsed."""
from dataclasses import dataclass
from random import Random

from .state_machine import State


@dataclass(frozen=True)
class Behavior:
    weight: float
    lead: tuple[float, float] = (1.8, 3.8)


# Lead time precedes the existing gesture. Completion comes from the renderer.
WAIT = (60.0, 180.0)
POOLS = {
    'street': {'look_around': Behavior(1.2), 'reading': Behavior(1.0, (5.0, 9.0)),
               'listening_music': Behavior(1.05, (3.0, 5.0))},
    'gothic': {'look_around': Behavior(.85), 'afternoon_tea': Behavior(1.0), 'embroidery': Behavior(1.1)},
    'maid': {'look_around': Behavior(.85), 'serve_tea': Behavior(.95), 'tidy_clean': Behavior(1.12)},
    'bunny_hoodie': {'look_around': Behavior(.95), 'hug_plush': Behavior(1.08), 'stretching': Behavior(1.0)},
    'sailor': {'look_around': Behavior(.90), 'use_phone': Behavior(1.12, (2.5, 4.5)),
               'take_photo': Behavior(1.0)},
}


class AutonomousIdleManager:
    def __init__(self, states, *, rng=None, wait=WAIT):
        self.states = states
        self.rng = rng or Random()
        self.wait = wait
        self.enabled = True
        self.outfit = None
        self.pool = {}
        self.history = {}
        self.seen = {}
        self.due = None
        self.current = None
        self.failed = set()

    def set_outfit(self, outfit, motions):
        self.reset()
        self.outfit = outfit
        self.failed.clear()
        self.pool = {State(name): behavior for name, behavior in POOLS.get(outfit, {}).items() if name in motions}

    def reset(self):
        self.states.release('auto')
        self.current = None
        self.due = None

    def finish(self, state):
        if self.current == state:
            self.reset()

    def unavailable(self, state):
        self.failed.add(state)
        self.finish(state)

    def tick(self, now, *, idle, blocked, music, unavailable):
        if not self.enabled or blocked:
            self.due = None
            return None
        if self.current is not None:
            return None
        if not idle or not self.pool:
            self.due = None
            return None
        if self.due is None:
            self.due = now + self.rng.uniform(*self.wait)
        if now < self.due:
            return None

        self.due = None
        eligible = set(self.pool) - self.failed - unavailable
        if not music:
            eligible.discard(State.LISTENING_MUSIC)
        if not eligible:
            return None
        last = self.history.get(self.outfit)
        choices = eligible - {last} if len(eligible) > 1 else eligible
        seen = self.seen.setdefault(self.outfit, set())
        if eligible <= seen:
            seen.clear()
        fresh = choices - seen
        candidates = [state for state in self.pool if state in (fresh or choices)]
        weights = [self.pool[state].weight * (1.45 if self.outfit == 'street'
                   and last == State.LOOK_AROUND and state == State.READING else 1) for state in candidates]
        state = self.rng.choices(candidates, weights, k=1)[0]
        seen.add(state)
        self.history[self.outfit] = state
        self.current = state
        self.states.request('auto', state, now)
        lead = self.rng.uniform(*self.pool[state].lead)
        return state, round(lead, 3)
