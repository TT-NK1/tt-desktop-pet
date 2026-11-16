import math
from dataclasses import dataclass
from pathlib import Path
import re

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget
from tt_voice_prosody import CELEBRATION, COMFORT, DISTRESS, TEASING
from tt_natural_speech import STYLES, style_for
from tt_avatar_motion import PortraitMotion, idle_sway, pose_at, pulse


PORTRAIT = Path(__file__).resolve().parent / "assets/character/Character_Portraits/casual_idle.png"
EXPRESSIONS = Path(__file__).resolve().parent / "assets" / "private" / "tt_chat_portraits_v1"
MODES = ("idle", "thinking", "happy", "sad", "comfort", "angry", "tsundere", "shy")
ANGRY = re.compile(r"^(?:我(?:真的|有点)?生气了|真气人|气死我了|这也太过分了|太过分了|"
                   r"你给我听好|给我听好了|你真把我气到了|我(?:可|真)?得(?:好好说说你|说你两句)|"
                   r"我(?:可|真)?要(?:好好说说你|说你两句))")
SAD = re.compile(r"^(?:唉[，。 ]*|嗯[，。 ]*)?(?:真可惜|太可惜了|有点遗憾|有点失落|我也有点难过|我有点难过)")
HAPPY_INPUT = re.compile(r"我(?:好|很|太)开心|好消息|我成功了|我赢了|终于(?:通过|成功|搞定|做到了)")
HAPPY_REPLY = re.compile(r"^(?:替你开心|我也开心|(?:真)?为你高兴|真不错|真好呀|好棒|哈哈|开心[呀啦])")
EMPATHY = re.compile(r"难怪你|我陪你|你可以先休息|不好受|难熬|换我也|抱一下|辛苦了")
SHY_REPLY = re.compile(r"^(?:我(?:都|有点|有些)?(?:不好意思|害羞了|脸红了)|被你夸得|你这样夸我|别(?:这样|这么)夸)")
PLAYFUL_REPLY = re.compile(r"^(?:才没有|才没想你|谁(?:说|想)你了|少得意|被你发现了)")
COMPLIMENT = re.compile(
    r"(?:你|TT)(?:的声音|说话|这样|也)?(?:还)?(?:真的很|真的好|真|很|好|太|特别|真的)"
    r"(?:可爱|温柔|贴心|好听|漂亮|好看|厉害|懂我)|"
    r"(?:好喜欢你|喜欢(?:跟|和)你聊|送(?:你|给你)(?:一[份个]|的小)?礼物|想夸夸你)"
)
GOOD_NEWS = re.compile(
    r"(?<![没未不别想])(?:通过了(?:面试|考试|考核)?|(?:面试|考试|考核)(?:通过了|过了)|"
    r"拿到(?:了)?(?:录取|offer|奖|第一|冠军|驾照)|被录取了|考上了|上岸了|毕业了|赢了比赛|"
    r"今天.{0,10}(?:获奖了|转正了|发工资了)|终于(?:修好|搞定|做成|过了))",
    re.IGNORECASE,
)
DISAPPOINTMENT = re.compile(r"比赛输了|差一点就赢了|错过了(?:比赛|机会|最后一场)|好可惜|遗憾的是|"
                            r"(?<!还)没通过(?:面试|考试)|面试没过|(?<!还)没有被录取")
UPSET = re.compile(
    r"(?:我|今天|刚刚|最近|这几天|心里).{0,14}(?:受不了|崩溃|特别难受|累坏了|受委屈|失恋了|被放鸽子)|"
    r"没人理解我|什么都不想做|哭了一整晚|我快撑不住了|我的.{0,8}去世了"
)
UNFAIR = re.compile(r"(?:他|她|对方|有人|他们).{0,12}(?:故意欺负|故意伤害|抢走我的|倒打一耙)|"
                   r"太欺负人了|被人欺负了|这也太不公平了|还倒打一耙")
TEASE_INPUT = re.compile(r"你又嘴硬了|你就嘴硬吧|逗你(?:玩)?呢|你是不是在嘴硬")
QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|\x60[^\x60]*\x60")
HYPOTHETICAL = re.compile(r"如果|假如|假设|举例|如何|为什么|怎么(?:才能|判断)|是不是会|能不能举|"
                          r"分析.{0,18}的原因")
NEGATED_NEWS = re.compile(r"没(?:有|能)?(?:成功|通过|拿到|考上)|还没|不一定|万一")
MOOD_WORDS = {
    "不开心": "sad", "不高兴": "sad", "难过": "sad", "伤心": "sad", "低落": "sad", "悲伤": "sad",
    "开心": "happy", "高兴": "happy", "快乐": "happy", "兴奋": "excited", "笑着": "happy",
    "温柔": "calm", "安慰": "calm", "平静": "calm", "常态": "chat", "普通": "chat",
    "生气": "angry", "气鼓鼓": "angry", "恼火": "angry",
    "害羞": "shy", "腼腆": "shy", "不好意思": "shy", "傲娇": "tsundere",
    "委屈": "hurt", "惊讶": "surprise", "吃惊": "surprise", "犹豫": "hesitant", "撒娇": "affectionate",
}
MOOD = r'[「『“"]?(?P<mood>' + "|".join(MOOD_WORDS) + r')[」』”"]?'
REQUEST_START = (r"^(?:(?:TT|我想听你|我想让你|我想让她|我希望你|我要你|我让你|我让她|"
                 r"想听你|让你|让她|你|她|请|麻烦|能不能|可不可以|可以|试着|现在|这次|再|就|"
                 r"也|要|稍微|更)[，,\s]*)*")
SCOLD = r"(?:骂骂?|训训?|凶凶?|数落)我(?!了|过|吗|嘛)"
SPEAK = rf"(?:和我|跟我|对我)?(?:说话|说|回答|回复|聊聊天|聊天|聊|讲|打个招呼|{SCOLD})"
SCOLD_REQUEST = re.compile(
    REQUEST_START + rf"(?:来)?(?:(?:狠狠|大声|严厉|认真)地?)?{SCOLD}"
    r"(?:一下|几句|两句|一句|一顿|几声)?(?:吧|呗|呀|好吗)?$"
)
DEGREE = r"(?:一点|点儿|一些|点)"
STYLE_REQUESTS = (
    re.compile(REQUEST_START + rf"(?:用|以|带着)?(?:一种|点|有点)?{MOOD}{DEGREE}?(?:的)?(?:语气|表情|样子|方式)?(?:地)?[，, ]*{SPEAK}"),
    re.compile(REQUEST_START + rf"{SPEAK}(?:的时候|时)?(?:要|可以)?{MOOD}{DEGREE}"),
    re.compile(REQUEST_START + rf"(?:给我|来|做|摆出|露出|切换到|表现得)(?:一个|一下|点)?{MOOD}(?:一点|点儿|的)?(?:表情|样子)?(?:[吧吗嘛呢呀哦]|$)"),
    re.compile(REQUEST_START + rf"(?:用|换成|切到|切换成){MOOD}(?:的)?(?:语气|表情)[吧吗嘛呢呀哦]?$"),
    re.compile(REQUEST_START + rf"{MOOD}{DEGREE}[吧吗嘛呢呀哦]?$"),
)


def requested_style(prompt):
    for sentence in re.split(r"[。！？!?；;\n]", prompt[:600]):
        sentence = sentence.strip().replace("TT，", "TT").replace("你，", "你")
        if re.fullmatch(r"(?:TT)?(?:请|能不能|可以)?安慰(?:我|一下|我一下)[吧吗嘛]?", sentence):
            return "calm"
        for pattern in STYLE_REQUESTS:
            match = pattern.match(sentence)
            if match:
                return MOOD_WORDS[match["mood"]]
        if SCOLD_REQUEST.fullmatch(sentence):
            return "angry"
    return None


STYLE_EXPRESSIONS = {
    "chat": "idle", "happy": "happy", "excited": "happy", "shy": "shy",
    "hesitant": "thinking", "angry": "angry", "hurt": "sad", "sad": "sad",
    "surprise": "happy", "calm": "comfort", "affectionate": "shy", "tsundere": "tsundere",
}
EXPRESSION_STYLES = {
    "idle": "chat", "happy": "happy", "sad": "sad", "comfort": "calm",
    "angry": "angry", "tsundere": "tsundere", "shy": "shy",
}
FRUSTRATED_INPUT = re.compile(r"我(?:现在|真的|好|很|快|有点){0,3}(?:要?气死|生气|恼火|烦死)|气得我|气死我了")
UNEASY_INPUT = re.compile(r"我(?:现在|真的)?(?:有点|好|很|特别)(?:紧张|害怕|担心|慌|害羞|难过|伤心|委屈|失落|不开心)|我心里没底")
SURPRISED_INPUT = re.compile(r"^(?:天哪|天呐|真的假的|没想到|什么情况)[，,。！？!? ]|我没想到")
PLAYFUL_INPUT = re.compile(r"^(?:哈哈[哈]*|嘿嘿|笑死了)(?:[，,。！？!? ]|$)")


@dataclass(frozen=True)
class ReplyDelivery:
    style: str
    expression: str


DECLARED_DELIVERY = {
    'neutral': ReplyDelivery('chat', 'idle'), 'smile': ReplyDelivery('chat', 'smile'),
    'happy': ReplyDelivery('happy', 'happy'), 'comfort': ReplyDelivery('calm', 'comfort'),
    'sad': ReplyDelivery('sad', 'sad'), 'puzzled': ReplyDelivery('hesitant', 'puzzled'),
    'surprised': ReplyDelivery('surprise', 'surprised'), 'shy': ReplyDelivery('shy', 'shy'),
    'focused': ReplyDelivery('chat', 'focused'), 'annoyed': ReplyDelivery('angry', 'angry'),
    'sleepy': ReplyDelivery('calm', 'sleepy'),
}


def requested_emotion(prompt):
    return STYLE_EXPRESSIONS.get(requested_style(prompt))


def reply_delivery(prompt, answer, selected="auto", *, declared=None):
    """Resolve the pair once, preserving explicit user requests and the existing artwork."""
    style = requested_style(prompt)
    if style is None and (delivery := DECLARED_DELIVERY.get(declared)):
        return ReplyDelivery(selected, delivery.expression) if selected in STYLES else delivery
    if style is None and selected in STYLES:
        style = selected
    if style is None:
        expression = inferred_expression(prompt, answer)
        clean = QUOTED.sub("", re.sub(r"```.*?(?:```|$)|https?://\S+", "", answer[:600], flags=re.DOTALL))
        spoken = style_for(clean)
        if spoken == "hesitant" and re.match(r"^嗯[，,。]", clean):
            # An ordinary acknowledgement should not override the user's mood.
            spoken = style_for(clean[2:].lstrip())
        if expression in ("comfort", "shy", "angry", "tsundere"):
            style = EXPRESSION_STYLES[expression]
        elif spoken != "chat":
            style = spoken
        else:
            style = EXPRESSION_STYLES[expression]
            if style == "chat":
                personal = personal_context(prompt)
                if SURPRISED_INPUT.search(personal):
                    style = "surprise"
                elif PLAYFUL_INPUT.search(personal):
                    style = "happy"
    return ReplyDelivery(style, STYLE_EXPRESSIONS[style])


def reply_emotion(prompt, answer):
    return reply_delivery(prompt, answer).expression


def personal_context(prompt):
    prompt = QUOTED.sub("", prompt[:600])
    return "。".join(part for part in re.split(r"[。！？!?；;\n]", prompt)
                    if not HYPOTHETICAL.search(part))


def inferred_expression(prompt, answer):
    answer = re.sub(r"```.*?(?:```|$)|https?://\S+", "", answer[:600], flags=re.DOTALL)
    clauses = [part.lstrip(" \n\t*#>") for part in re.split(r"[。！？!?；;，,\n]", QUOTED.sub("", answer))]

    def said(pattern):
        return any(pattern.search(part) for part in clauses)

    context = personal_context(prompt)
    distress = bool(DISTRESS.search(context) or UPSET.search(context) or FRUSTRATED_INPUT.search(context)
                    or UNEASY_INPUT.search(context))
    if said(COMFORT) or (distress and said(EMPATHY)):
        return "comfort"
    if said(ANGRY):
        return "angry"
    if said(SHY_REPLY):
        return "shy"
    if said(TEASING) or said(PLAYFUL_REPLY):
        return "tsundere"
    if said(SAD):
        return "sad"
    if said(CELEBRATION) or said(HAPPY_REPLY):
        return "happy"
    if distress:
        return "comfort"
    if COMPLIMENT.search(context):
        return "shy"
    if UNFAIR.search(context):
        return "comfort"
    if TEASE_INPUT.search(context):
        return "tsundere"
    if not NEGATED_NEWS.search(context) and (GOOD_NEWS.search(context) or HAPPY_INPUT.search(context)):
        return "happy"
    if DISAPPOINTMENT.search(context):
        return "sad"
    return "idle"


def portrait_raster(pixmap, aspect):
    width, height = pixmap.width(), pixmap.height()
    source_width = min(width, height * aspect)
    source_height = source_width / aspect
    source = QRectF((width - source_width) / 2, (height - source_height) / 2,
                    source_width, source_height)
    raster = QImage(380, 312, QImage.Format.Format_RGB32)
    painter = QPainter(raster)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(QRectF(0, 0, 380, 312), pixmap, source)
    painter.end()
    return raster


def laugh_patch(pixmap, aspect):
    raster = portrait_raster(pixmap, aspect).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(raster)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    center = QPointF(204, 166)
    mask = QRadialGradient(center, 27)
    mask.setColorAt(0.0, Qt.GlobalColor.white)
    mask.setColorAt(0.52, Qt.GlobalColor.white)
    mask.setColorAt(1.0, Qt.GlobalColor.transparent)
    painter.fillRect(raster.rect(), mask)
    painter.end()
    return raster


def draw_mood_marks(painter, frame, mode, elapsed):
    if mode in ("shy", "tsundere"):
        warmth = pulse(elapsed % 5.0, 0, .48, 1.6, .84)
        strength = (34 if mode == "shy" else 49) + round(warmth * 22)
        for x, y in ((83, 79), (109, 71)):
            center = frame.topLeft() + QPointF(x, y)
            tint = QRadialGradient(center, 9)
            tint.setColorAt(0, QColor(237, 95, 124, strength))
            tint.setColorAt(1, QColor(237, 95, 124, 0))
            painter.fillRect(QRectF(center.x() - 9, center.y() - 9, 18, 18), tint)
    elif mode == "angry":
        emphasis = pulse(elapsed % 4.5, 0.18, .20, .95, .48)
        if not emphasis:
            return
        painter.save()
        painter.translate(frame.topLeft() + QPointF(129, 29))
        painter.rotate(-11)
        painter.scale(.85 + .18 * emphasis, .85 + .18 * emphasis)
        ink = QPen(QColor(235, 105, 128, round(235 * emphasis)), 1.7)
        ink.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(ink)
        for _ in range(4):
            mark = QPainterPath(QPointF(-2.1, -6.5))
            mark.cubicTo(-2.2, -3.0, -3.0, -2.2, -6.5, -2.1)
            painter.drawPath(mark)
            painter.rotate(90)
        painter.restore()


def draw_idle_sway(painter, frame, portrait, elapsed):
    x, y, angle = idle_sway(elapsed)
    anchor = frame.topLeft() + QPointF(frame.width() * .52, frame.height() * .88)
    painter.save()
    painter.fillRect(frame, Qt.GlobalColor.black)
    painter.translate(anchor + QPointF(x, y))
    painter.rotate(angle)
    target = frame.translated(-anchor.x(), -anchor.y()).adjusted(-1, -1, 1, 1)
    painter.drawImage(target, portrait)
    painter.restore()


class TTAvatar(QWidget):
    """Display expression art inside the original chat portrait frame."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(196, 162)
        self.mode = "idle"
        self.resting = True
        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.portrait = QPixmap(str(PORTRAIT))
        self.expressions = {mode: QPixmap(str(EXPRESSIONS / f"{mode}.png")) for mode in MODES}
        self.open_smile = self.expressions["happy"]
        closed = QPixmap(str(EXPRESSIONS / "happy_closed.png"))
        self.has_closed_smile = not closed.isNull()
        if self.has_closed_smile:
            self.expressions["happy"] = closed
        self.laugh_layer = None
        self.motions = {}

    def showEvent(self, event):
        super().showEvent(event)
        self.clock.restart()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def set_mode(self, mode):
        mode = "thinking" if mode == "search" else mode
        if self.mode != mode:
            self.mode = mode
            self.clock.restart()
            self.update()

    def set_resting(self, resting):
        if self.resting != resting:
            self.resting = resting
            if resting:
                self.clock.restart()
            self.update()

    def tick(self):
        idle = self.resting and self.mode == "idle"
        interval = 16 if idle else 33
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        frame = QRectF(3, 3, self.width() - 6, self.height() - 6)
        clip = QPainterPath()
        clip.addRoundedRect(frame, 20, 20)
        painter.setClipPath(clip)
        glow = QRadialGradient(frame.center(), 115)
        glow.setColorAt(0, QColor("#fff1f8"))
        glow.setColorAt(1, QColor("#d6bde9"))
        painter.fillRect(frame, glow)
        expression = self.expressions.get(self.mode)
        if expression is not None and not expression.isNull():
            elapsed = self.clock.elapsed() / 1000
            aspect = frame.width() / frame.height()
            motion = self.motions.get(self.mode)
            if motion is None:
                motion = self.motions[self.mode] = PortraitMotion(self.mode, portrait_raster(expression, aspect))
            portrait = motion.frame(pose_at(self.mode, elapsed))
            if self.mode == "idle" and self.resting:
                draw_idle_sway(painter, frame, portrait, elapsed)
            else:
                painter.drawImage(frame, portrait)
            if self.mode == "happy" and self.has_closed_smile and not self.open_smile.isNull():
                openness = pulse(elapsed, 0.22, .18, .12, .23)
                if openness:
                    if self.laugh_layer is None:
                        self.laugh_layer = laugh_patch(self.open_smile, aspect)
                    painter.setOpacity(openness)
                    painter.drawImage(frame, self.laugh_layer)
                    painter.setOpacity(1)
            draw_mood_marks(painter, frame, self.mode, elapsed)
            return
        if self.portrait.isNull():
            painter.setPen(QColor("#5b3b75"))
            painter.drawText(frame, Qt.AlignmentFlag.AlignCenter, "TT · 立绘未找到")
            return

        seconds = self.clock.elapsed() / 1000
        breath = math.sin(seconds * 1.9)
        if self.mode == "thinking":
            angle, lean = -3.0 + breath * .45, -4.0
        elif self.mode == "search":
            angle, lean = math.sin(seconds * 2.9) * 2.5, 4.0
        else:
            angle, lean = breath * .7, 0.0

        width, height = self.portrait.width(), self.portrait.height()
        crop_height = height * .49
        crop_width = crop_height * frame.width() / frame.height()
        crop = QRectF(width * .455 - crop_width / 2, 0, crop_width, crop_height)
        center = frame.center()
        painter.translate(center + QPointF(lean, breath * 1.6))
        painter.rotate(angle)
        painter.scale(1.10, 1.10)
        painter.drawPixmap(
            QRectF(-frame.width() / 2, -frame.height() / 2, frame.width(), frame.height()),
            self.portrait, crop,
        )
