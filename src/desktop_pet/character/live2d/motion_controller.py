from .state_machine import State


class MotionController:
    def __init__(self, states, motions):
        self.states = states
        self.motions = motions

    def select(self, state, now):
        state = State(state)
        if state == State.IDLE:
            self.states.release("user")
            return
        if state.value not in self.motions:
            raise ValueError("当前模型没有这个动作")
        self.states.request("user", state, now)

    def tick(self, now):
        return self.states.current(now)

    def finished(self, state):
        for source in ("auto", "user"):
            request = self.states.requests.get(source)
            if request and request.state == state:
                self.states.release(source)
