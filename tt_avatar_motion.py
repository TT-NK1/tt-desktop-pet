"""Local, expression-specific motion for TT's existing portrait art."""

from dataclasses import dataclass
import math

from PySide6.QtGui import QImage


# Coordinates are in the 190 x 156 portrait crop, excluding its three-pixel frame.
# Each expression has its own landmarks because the original image crops differ.
LANDMARKS = {
    "idle": ((79, 73), (103, 58), (101, 84)),
    "thinking": ((80, 73), (107, 56), (104, 85)),
    "happy": ((78, 73), (102, 56), (102, 83)),
    "sad": ((79, 77), (102, 65), (101, 89)),
    "comfort": ((80, 74), (105, 60), (106, 83)),
    "angry": ((80, 74), (104, 62), (105, 87)),
    "tsundere": ((78, 75), (102, 60), (100, 87)),
    "shy": ((77, 74), (103, 62), (101, 84)),
}


@dataclass(frozen=True)
class Pose:
    gaze_x: float = 0
    gaze_y: float = 0
    eyelid: float = 0
    brow: float = 0
    cheek: float = 0
    mouth: float = 0
    fringe: float = 0


def ease(value):
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


def pulse(t, start, close, hold, release):
    return ease((t - start) / close) * ease((start + close + hold + release - t) / release)


def idle_sway(elapsed):
    phase = math.tau * elapsed / 5.8
    return .62 * math.sin(phase), .33 * math.sin(phase * .83), .52 * math.sin(phase)


def pose_at(mode, elapsed):
    if mode == "thinking":
        cycle, t = divmod(elapsed, 4.7)
        direction = 1 if int(cycle) % 2 == 0 else -1
        sweep = ease((t - 0.10) / 1.70)
        angle = -math.pi / 2 + direction * math.tau * sweep
        radius = pulse(t, 0, 0.33, 1.48, 0.54)
        blink = pulse(t, 2.38, 0.09, 0.02, 0.14)
        return Pose(-0.54 + 1.92 * radius * math.cos(angle),
                    0.26 + 1.65 * radius * math.sin(angle), 0.80 * blink)
    if mode == "happy":
        t = elapsed % 4.6
        smile = pulse(t, 0.08, 0.32, 0.48, 0.44)
        return Pose(eyelid=0.69 * smile, mouth=-0.24 * smile, cheek=0.20 * smile)
    if mode == "sad":
        t = elapsed % 4.8
        blink = pulse(t, 0.32, 0.10, 0.045, 0.14) + pulse(t, 0.73, 0.09, 0.025, 0.18)
        flutter = pulse(t, 0.45, 0.06, 0.33, 0.13) * (1 + math.sin(42 * t))
        return Pose(eyelid=min(1, 0.91 * blink + 0.09 * flutter))
    if mode == "comfort":
        t = elapsed % 5.1
        soften = pulse(t, 0.1, 0.62, 0.65, 0.65)
        blink = pulse(t, 2.55, 0.18, 0.07, 0.22)
        return Pose(eyelid=0.25 * soften + 0.74 * blink, mouth=-0.15 * soften, cheek=0.12 * soften)
    if mode == "angry":
        t = elapsed % 4.5
        frown = pulse(t, 0.08, 0.38, 0.87, 0.50)
        return Pose(eyelid=0.19 * frown, brow=1.65 * frown,
                    cheek=1.55 * frown, mouth=0.74 * frown)
    if mode == "tsundere":
        t = elapsed % 4.5
        glance = pulse(t, 0.32, 0.31, 0.18, 0.40)
        pout = pulse(t, 0.25, 0.36, 1.00, 0.48)
        blink = pulse(t, 2.12, .10, .02, .15)
        return Pose(0.42 * glance, 0.23 * glance, 0.75 * blink + 0.12 * glance,
                    cheek=0.50 * pout, mouth=0.70 * pout)
    if mode == "shy":
        t = elapsed % 4.9
        look_down = pulse(t, .06, .52, .78, .64)
        blink = pulse(t, 1.7, .11, .04, .20)
        return Pose(-.32 * look_down, .92 * look_down, .17 * look_down + .76 * blink,
                    cheek=.18 * look_down, mouth=-.19 * look_down)
    t = elapsed % 10.6
    blink = (pulse(t, 1.10, 0.09, 0.02, 0.15) + pulse(t, 4.66, 0.10, 0.03, 0.15)
             + pulse(t, 8.07, 0.08, 0.02, 0.13) + pulse(t, 8.42, 0.10, 0.02, 0.16))
    glance = pulse(t, 2.36, 0.38, 0.52, 0.46)
    smile = pulse(t, 5.52, 0.62, 0.44, 0.65)
    fringe = 0.58 * pulse(t, 6.45, 0.46, 0.06, 0.85)
    return Pose(0.72 * glance, -0.45 * glance, 0.84 * blink + 0.10 * smile,
                cheek=0.18 * smile, mouth=-0.26 * smile, fringe=fringe)


class Region:
    """Precompute a feathered ellipse in portrait pixels for inverse sampling."""

    def __init__(self, center, radii, angle, width, height, scale):
        self.scale = scale
        cx, cy = center
        rx, ry = radii
        self.cos, self.sin = math.cos(angle), math.sin(angle)
        extent = math.ceil(max(rx, ry) * scale)
        self.points = []
        for y in range(max(1, round(cy * scale) - extent), min(height - 1, round(cy * scale) + extent + 1)):
            for x in range(max(1, round(cx * scale) - extent), min(width - 1, round(cx * scale) + extent + 1)):
                dx, dy = x / scale - cx, y / scale - cy
                u, v = dx * self.cos + dy * self.sin, -dx * self.sin + dy * self.cos
                horizontal = (u / rx) ** 2
                radius = horizontal + (v / ry) ** 2
                if radius < 1:
                    self.points.append((x, y, u, v, (1 - radius) ** 2,
                                        ry * math.sqrt(1 - horizontal), 1 - horizontal))

    def warp(self, source, target, stride, move_x=0, move_y=0, stretch_x=0, stretch_y=0,
             lid=0, rotation=0):
        cos, sin, scale = self.cos, self.sin, self.scale
        turn_cos, turn_sin = math.cos(rotation) - 1, math.sin(rotation)
        for x, y, u, v, weight, vertical, inner in self.points:
            dx = (u * (stretch_x + turn_cos) + v * turn_sin - move_x) * weight * scale
            dy = (v * (stretch_y + turn_cos) - u * turn_sin - move_y) * weight
            if lid:
                opening = vertical * 0.47
                squint = opening * (1 - 0.88 * lid * inner)
                distance = abs(v)
                if distance <= squint:
                    original = distance * opening / squint
                else:
                    original = opening + (distance - squint) * (vertical - opening) / (vertical - squint)
                dy += math.copysign(original - distance, v)
            dy *= scale
            sx, sy = x + dx * cos - dy * sin, y + dx * sin + dy * cos
            ix, iy = int(sx), int(sy)
            fx, fy = sx - ix, sy - iy
            a = iy * stride + ix * 4
            b = a + stride
            offset = y * stride + x * 4
            for channel in range(3):
                upper = source[a + channel] * (1 - fx) + source[a + channel + 4] * fx
                lower = source[b + channel] * (1 - fx) + source[b + channel + 4] * fx
                target[offset + channel] = round(upper * (1 - fy) + lower * fy)


class PortraitMotion:
    """Animate eyes, brows, lips and cheeks while keeping the portrait anchored."""

    def __init__(self, mode, base):
        self.base = base.convertToFormat(QImage.Format.Format_RGB32)
        width, height = self.base.width(), self.base.height()
        scale = width / 190
        left, right, mouth = LANDMARKS[mode]
        angle = math.atan2(right[1] - left[1], right[0] - left[0])

        def region(center, radii):
            return Region(center, radii, angle, width, height, scale)

        self.lids = [region(left, (10, 7)), region(right, (12, 8))]
        self.irises = [region(left, (7, 5.5)), region(right, (8, 6))]
        self.brows = [region((left[0] - 4, left[1] - 5), (10, 4)),
                      region((right[0] - 4, right[1] - 6), (12, 5))]
        self.cheeks = [region((mouth[0] - 11, mouth[1] - 5), (10, 9)),
                       region((mouth[0] + 10, mouth[1] - 7), (10, 10))]
        self.mouth = region(mouth, (10, 7))
        self.fringe = Region((61, 43), (13, 19), 0, width, height, scale) if mode == "idle" else None
        self.last_pose = Pose()
        self.last_frame = self.base

    def frame(self, pose):
        # Quantize small changes to avoid recalculating identical resting frames.
        pose = Pose(*(round(value, 2) for value in (
            pose.gaze_x, pose.gaze_y, pose.eyelid, pose.brow, pose.cheek, pose.mouth, pose.fringe)))
        if pose == self.last_pose:
            return self.last_frame
        output = self.base.copy()
        operations = []
        if pose.fringe and self.fringe:
            operations.append((self.fringe, (pose.fringe, -0.12 * pose.fringe, 0, 0)))
        if pose.cheek:
            operations.extend((area, (direction * pose.cheek, 0.25 * pose.cheek, 0, 0))
                              for area, direction in zip(self.cheeks, (-1, 1)))
        if pose.brow:
            operations.extend((area, (direction * pose.brow * 0.54, pose.brow, 0, 0))
                              for area, direction in zip(self.brows, (1, -1)))
        if pose.gaze_x or pose.gaze_y:
            operations.extend((area, (pose.gaze_x, pose.gaze_y, 0, 0)) for area in self.irises)
        if pose.eyelid:
            operations.extend((area, (0, 0, 0, 0, pose.eyelid)) for area in self.lids)
        if pose.mouth:
            operations.append((self.mouth, (0, 0.45 * pose.mouth, pose.mouth, -0.48 * pose.mouth)))
        stride = output.bytesPerLine()
        for area, parameters in operations:
            original = bytes(output.constBits())
            area.warp(original, output.bits(), stride, *parameters)
        self.last_pose, self.last_frame = pose, output
        return output
