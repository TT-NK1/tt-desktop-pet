"""Glass surfaces and small outfit motifs shared by TT's floating bubbles."""
from dataclasses import dataclass
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient


@dataclass(frozen=True)
class Palette:
    top: str
    bottom: str
    rim: str
    accent: str
    ink: str
    secondary: str
    motif: str
    dark: bool = False


OUTFITS = {
    'street': Palette('#524764', '#322b45', '#e9d5ff', '#d7b1f8', '#fff3ff', '#e3d6f0', 'headphones', True),
    'maid': Palette('#fff9f3', '#dfccea', '#fff9fd', '#9b64bc', '#35243e', '#594360', 'bow'),
    'bunny_hoodie': Palette('#e8dbf7', '#c5afe8', '#f7eaff', '#8257b2', '#302346', '#514265', 'bunny'),
    'sailor': Palette('#fae7f7', '#ded1f3', '#fff4ff', '#9a578e', '#37243e', '#63455f', 'blossom'),
    'gothic': Palette('#372b48', '#201b30', '#cdb5eb', '#d7a8e6', '#fff0ff', '#e1c9ec', 'pendant', True),
}


def tint(color, alpha):
    result = QColor(color)
    result.setAlpha(alpha)
    return result


def palette_for(pet):
    outfit = getattr(getattr(pet, 'manager', None), 'selected', 'street')
    return OUTFITS.get(outfit, OUTFITS['street'])


def round_pen(brush, width):
    return QPen(brush, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def outline(rect):
    def pt(x, y):
        return QPointF(rect.x() + x * rect.width(), rect.y() + y * rect.height())

    path = QPainterPath(pt(.26, .031))
    for x1, y1, x2, y2, x, y in (
        (.37, .037, .46, .060, .58, .030), (.67, .005, .76, .002, .86, .026),
        (.96, .049, .995, .146, .996, .285), (1.005, .45, .981, .57, .994, .70),
        (1.006, .875, .949, .977, .831, .982), (.70, .998, .618, .977, .50, .977),
        (.39, .977, .310, 1.008, .184, .981), (.054, .965, .006, .868, .008, .727),
        (.010, .601, .027, .496, .008, .342), (-.001, .145, .030, .054, .135, .032),
        (.174, .020, .214, .025, .26, .031),
    ):
        path.cubicTo(pt(x1, y1), pt(x2, y2), pt(x, y))
    path.closeSubpath()
    return path


def paint_glass(painter, rect, theme, tail=None):
    shape = outline(rect)
    if tail is not None:
        base, normal = tail
        tangent = QPointF(-normal.y(), normal.x())
        tip = base + normal * 12 + tangent * 2
        fin = QPainterPath(base - normal * 4 - tangent * 10)
        fin.cubicTo(base + normal * 4 - tangent * 5, tip - tangent * 3, tip)
        fin.cubicTo(tip + tangent * 3, base + tangent * 6, base - normal * 4 + tangent * 9)
        fin.closeSubpath()
        shape = shape.united(fin)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    for width, alpha in ((12, 7), (9, 13), (6, 25), (3.8, 36)):
        painter.setPen(round_pen(tint(theme.accent, alpha), width))
        painter.drawPath(shape.translated(0, 2))
    fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
    fill.setColorAt(0, tint(theme.top, 239))
    fill.setColorAt(.56, tint(theme.bottom, 222))
    fill.setColorAt(1, tint(theme.top, 233))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.fillPath(shape, fill)
    painter.save()
    painter.setClipPath(shape)
    for center, radius, alpha in ((rect.topLeft() + QPointF(32, 7), rect.width() * .56, 72),
                                  (rect.bottomRight() - QPointF(18, 0), rect.width() * .49, 112)):
        glow = QRadialGradient(center, radius)
        glow.setColorAt(0, tint(theme.rim, alpha))
        glow.setColorAt(1, tint(theme.rim, 0))
        painter.fillRect(rect.adjusted(-13, -13, 13, 13), glow)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(tint(theme.rim, 63), 5.6))
    painter.drawPath(shape)
    painter.restore()
    gloss = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for stop, opacity in ((0, 245), (.29, 180), (.52, 116), (.78, 192), (1, 250)):
        gloss.setColorAt(stop, tint(theme.rim, opacity))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(gloss, 1.45))
    painter.drawPath(shape)
    if theme.motif in ('bow', 'pendant'):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint(theme.rim, 162 if theme.dark else 225))
        for i in range(10):
            x = rect.left() + rect.width() * (.27 + .05 * i)
            y = rect.bottom() - rect.height() * (.030 - .010 * math.sin(i * .68))
            painter.drawEllipse(QPointF(x, y - 2), 2.4, 1.65)


def paint_motif(p, location, size, theme):
    p.save()
    p.translate(location)
    p.scale(size / 32, size / 32)
    fill = QLinearGradient(0, -15, 0, 15)
    fill.setColorAt(0, QColor(theme.rim))
    fill.setColorAt(1, QColor(theme.accent))
    p.setPen(round_pen(tint(theme.accent, 230), .95))
    p.setBrush(fill)
    glyph = QPainterPath()
    if theme.motif == 'headphones':
        p.setPen(round_pen(fill, 2.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(-10, -11, 20, 24), 0, 180 * 16)
        p.setBrush(fill)
        p.setPen(QPen(tint(theme.rim, 225), .9))
        for x in (-12, 6):
            p.drawRoundedRect(QRectF(x, -1, 6, 10), 2.8, 2.8)
    elif theme.motif == 'bow':
        for side in (-1, 1):
            glyph.moveTo(0, -1)
            glyph.cubicTo(side * 15, -13, side * 17, -6, side * 13, 3)
            glyph.quadTo(side * 10, 8, 0, 2)
            glyph.closeSubpath()
            glyph.moveTo(0, 1)
            glyph.lineTo(side * 11, 13)
            glyph.lineTo(side * 8, 8)
            glyph.lineTo(side * 5, 11)
            glyph.closeSubpath()
        p.drawPath(glyph)
        p.drawRoundedRect(QRectF(-3, -3, 6, 7), 1.8, 1.8)
    elif theme.motif == 'bunny':
        for x, angle in ((-5, -23), (5, 16)):
            p.save()
            p.translate(x, -6)
            p.rotate(angle)
            p.drawEllipse(QRectF(-3.8, -10, 7.6, 15))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tint('#fbabd9', 136))
            p.drawEllipse(QRectF(-1.5, -8, 3, 10))
            p.restore()
        p.drawEllipse(QRectF(-10, -3, 20, 15))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ink))
        for x in (-3.4, 3.4):
            p.drawEllipse(QPointF(x, 4), .9, 1.15)
    elif theme.motif == 'blossom':
        for i in range(5):
            p.save()
            p.rotate(i * 72)
            petal = QPainterPath(QPointF(0, 1))
            petal.cubicTo(-9, -3, -6, -12, -1, -11)
            petal.lineTo(0, -9)
            petal.lineTo(2, -11)
            petal.cubicTo(8, -10, 6, -2, 0, 1)
            p.drawPath(petal)
            p.restore()
        p.setBrush(QColor('#fff2d1'))
        p.drawEllipse(QPointF(0, 0), 2, 2)
    else:
        glyph.moveTo(0, -13)
        for x, y in ((4, -5), (12, 0), (4, 4), (0, 14), (-4, 4), (-12, 0), (-4, -5)):
            glyph.lineTo(x, y)
        glyph.closeSubpath()
        p.drawPath(glyph)
        p.setBrush(QColor('#fff3fd'))
        p.drawEllipse(QPointF(0, 0), 2, 2)
    p.restore()
