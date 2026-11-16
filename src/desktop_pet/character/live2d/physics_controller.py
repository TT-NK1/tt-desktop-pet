import math


class PhysicsController:
    """Pointer inertia only. Hair/garment physics must come from Cubism physics3."""

    def __init__(self):
        self.x = self.y = 0.0

    def sample(self, dx, dy, dt):
        weight = 1 - math.exp(-max(0, min(dt, 0.1)) * 10)
        self.x += (max(-1, min(1, dx)) - self.x) * weight
        self.y += (max(-1, min(1, dy)) - self.y) * weight
        return {"ParamAngleZ": -8 * self.x, "ParamBodyAngleX": 4 * self.x}
