"""Live2D presentation for TT's desktop character and conversation header."""
import math
from pathlib import Path
import time

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QWidget

from src.desktop_pet.character.live2d.desktop_window import DesktopWindow, clamp_position
from tt_chat_portrait import ChatPortraitViewport
from tt_window_manager import WindowManager


PROJECT = Path(__file__).resolve().parent
VISIBLE_ALPHA = bytes(int(alpha >= 16) for alpha in range(256))


def visible_bounds(image):
    """Measure the silhouette and the center of its upper edge in widget coordinates."""
    image = image.convertToFormat(QImage.Format.Format_Alpha8)
    pixels = bytes(image.constBits()).translate(VISIBLE_ALPHA)
    width, height, stride = image.width(), image.height(), image.bytesPerLine()
    rows = []
    for y in range(height):
        start = y * stride
        left = pixels.find(b'\x01', start, start + width)
        if left != -1:
            rows.append((y, left - start, pixels.rfind(b'\x01', left, start + width) - start + 1))
    if not rows:
        return QRect(), None

    top, bottom = rows[0][0], rows[-1][0] + 1
    left = min(row[1] for row in rows)
    right = max(row[2] for row in rows)
    crown = [row for row in rows if row[0] < top + (bottom - top) * .12]
    dpr = image.devicePixelRatio()
    bounds = QRectF(left / dpr, top / dpr, (right - left) / dpr, (bottom - top) / dpr)
    crown_x = (min(row[1] for row in crown) + max(row[2] for row in crown)) / (2 * dpr)
    return bounds.toAlignedRect(), crown_x

# Existing TT reply moods drive only parameters supported by the current rig.
MOODS = {
    'thinking': (-.20, .26, .94),
    'search': (.26, .08, .96),
    'happy': (.03, .12, .96),
    'sad': (-.18, -.31, .65),
    'comfort': (.06, -.06, .86),
    'angry': (.14, -.05, .76),
    'tsundere': (-.30, .05, .81),
    'shy': (-.25, -.22, .79),
}
PORTRAIT_EXPRESSIONS = {
    'thinking': 'focused', 'focused': 'focused', 'smile': 'smile', 'happy': 'happy',
    'sad': 'aggrieved', 'comfort': 'smile', 'puzzled': 'puzzled', 'surprised': 'surprised',
    'angry': 'annoyed', 'tsundere': 'annoyed', 'shy': 'shy', 'sleepy': 'sleepy',
}


class TTLive2DAvatar(ChatPortraitViewport):
    """Mirror the authoritative desktop model and supply shared chat/voice intent."""
    def __init__(self, room):
        super().__init__(room)
        self.room = room
        self.mode = 'idle'
        self.resting = True
        self.mode_since = time.monotonic()

    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self.mode_since = time.monotonic()
            self.sync_portrait_expression()

    def set_resting(self, resting):
        if self.resting != resting:
            self.resting = resting
            self.sync_portrait_expression()

    def resume(self):
        super().resume()
        self.sync_portrait_expression()

    def sync_portrait_expression(self):
        if not self.view or not self.state_manager or not self.presentation_visible():
            return
        import json
        manager = self.state_manager.pet.manager
        expressions = manager.config[manager.selected].get('expressions', {})
        expression = None if self.resting else expressions.get(PORTRAIT_EXPRESSIONS.get(self.mode))
        self.view.page().runJavaScript(
            'window.sdkProbe && sdkProbe.setPortraitExpression && sdkProbe.setPortraitExpression('
            + json.dumps(expression) + ')')

    def model_parameters(self, now):
        values = {}
        if not self.resting:
            mood = MOODS.get(self.mode)
            if mood:
                x, y, openness = mood
                blend = min(1.0, max(0.0, (now - self.mode_since) * 4.0))
                values.update(ParamEyeBallX=x * blend, ParamEyeBallY=y * blend)
                # Keep occasional full blinks during longer expressions.
                phase = (now - self.mode_since) % 4.1
                blink = 1 - max(0.0, 1 - abs(phase - 3.7) / .13)
                values.update(ParamEyeLOpen=openness * blink, ParamEyeROpen=openness * blink)

        # Observe the existing player's position for a visual talking rhythm.
        # Audio synthesis, volume, timing and playback ownership remain in IndexTTS.
        voice = getattr(self.room, 'pro_voice', None)
        if voice is not None:
            player = voice.player.player
            if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                seconds = player.position() / 1000
                syllable = max(0.0, math.sin(math.tau * 4.0 * seconds)) ** 1.5
                cadence = .72 + .28 * math.sin(math.tau * 1.23 * seconds)
                values['ParamMouthOpenY'] = .07 + .62 * syllable * cadence
        return values


class TTDesktopPet(DesktopWindow):
    """Keep TT's chat and quick-bar mixins on the official Live2D desktop host."""

    size_presets = (.75, 1.0, 1.25)
    window_type = Qt.WindowType.Tool
    separate_tray_menu = True

    def __init__(self, root):
        self.art_bounds = QRect()
        self.crown_x = None
        super().__init__(root, settings_database=root / 'runtime/live2d/tt-settings.sqlite3')
        saved = self.manager.get_setting('position', None)
        self.restore_origin = QPoint(*saved) if isinstance(saved, list) and len(saved) == 2 else None
        self.anchor_timer = QTimer(self,singleShot=True)
        self.anchor_timer.timeout.connect(self.read_art_anchor)
        self.anchor_retries = 0
        self.renderer.ready.connect(self.recheck_art_anchor)
        self.renderer.motion_finished.connect(self.recheck_art_anchor)
        self.window_manager = WindowManager(self,root/'logs/window_manager.log')

    def normalize_scale(self, scale):
        return min(self.size_presets, key=lambda preset: abs(preset - scale))

    def resize_pet(self, scale):
        scale = self.normalize_scale(scale)
        canvas = self.renderer.widget
        old_size = canvas.size()
        size = QSize(round(330 * scale), round(390 * scale))
        if size != old_size and not self.art_bounds.isEmpty():
            sx, sy = size.width() / old_size.width(), size.height() / old_size.height()
            art = self.art_bounds
            self.art_bounds = QRectF(art.x() * sx, art.y() * sy,
                                     art.width() * sx, art.height() * sy).toAlignedRect()
            self.crown_x *= sx
        canvas.setFixedSize(size)
        self.resize(size)
        self.scale = scale
        self.manager.save_setting('scale', scale)

    def change_size(self,value):
        old = self.pos()+self.bubble_anchor_rect().bottomRight().toPoint()
        self.resize_pet(value)
        origin = old-self.bubble_anchor_rect().bottomRight().toPoint()
        self.move_clamped(origin.x(),origin.y())
        self.recheck_art_anchor()

    def move_clamped(self,x,y):
        if self.art_bounds.isEmpty():
            super().move_clamped(x,y)
            return
        # Transparent canvas margins do not determine how close the art can get to an edge.
        bounds = self.interaction_bounds()
        offset = bounds.topLeft()
        point = clamp_position(int(x)+offset.x(),int(y)+offset.y(),bounds.width(),bounds.height(),
                               [screen.availableGeometry() for screen in QApplication.screens()])
        self.move(point-offset)

    def interaction_bounds(self):
        return self.art_bounds.adjusted(-11,-12,11,12).intersected(self.renderer.widget.rect())

    def interaction_rect(self):
        return (super().interaction_rect() if self.art_bounds.isEmpty()
                else self.interaction_bounds().translated(self.pos()))

    def recheck_art_anchor(self,_state=None):
        self.anchor_retries = 3
        self.anchor_timer.start(750)

    def showEvent(self,event):
        super().showEvent(event)
        if self.loaded:
            self.recheck_art_anchor()

    def read_art_anchor(self):
        if self.closing or not self.isVisible() or not self.loaded:
            return
        view = self.renderer.view
        settled = (view is not None and view.size() == self.renderer.widget.size()
                   and self.renderer.retiring_view is None)
        bounds,crown_x = visible_bounds(view.grab().toImage()) if settled else (QRect(),None)
        if bounds.isEmpty():
            if self.anchor_retries:
                self.anchor_retries -= 1
                self.anchor_timer.start(200)
            return
        first = self.art_bounds.isEmpty()
        self.art_bounds, self.crown_x = bounds, crown_x
        self.window_manager.schedule_placement()
        if first:
            origin = self.restore_origin if self.restore_origin is not None else self.pos()
            if self.drag_start is None:
                self.move_clamped(origin.x(),origin.y())
            self.restore_origin = None

    def autonomous_voice_active(self):
        voice = getattr(getattr(self, 'chat', None), 'pro_voice', None)
        return voice is not None and voice.player.player.isPlaying()

    def autonomous_music_playing(self):
        music = getattr(self, 'music_bubble', None)
        if music is None:
            return False
        services = getattr(music, 'services', None)
        if services is not None and services.active != 'local':
            state = services.state()
            return state.online and state.status == 'Playing'
        return music.player.isPlaying()

    def animation_visible(self):
        avatar = getattr(getattr(self, 'chat', None), 'avatar', None)
        return self.isVisible() or bool(avatar and avatar.presentation_visible())

    def tick(self):
        manager = getattr(self,'window_manager',None)
        if manager is not None:
            manager.observe_context(bool(self.renderer.snapshot.get('contextLost')))
        previous = self.current_motion
        super().tick()
        if self.current_motion != previous and not self.closing:
            self.recheck_art_anchor()

    def send_model_parameters(self, values, now):
        avatar = getattr(getattr(self, 'chat', None), 'avatar', None)
        if avatar is not None:
            for key, value in avatar.model_parameters(now).items():
                if key in ('ParamEyeLOpen', 'ParamEyeROpen') and now < self.blink_until:
                    continue
                if key in ('ParamEyeBallX', 'ParamEyeBallY') and key not in values:
                    continue
                values[key] = value
            mouth = avatar.speech_mouth()
            # Keep the existing voice player as the only owner of audio timing.
            import json
            self.renderer.command('(()=>{window.petSpeech=' + json.dumps(
                {'mouth': mouth} if mouth is not None else None) +
                '; window.petSpeech && (window.petSpeech.until=performance.now()+250);})()')
        super().send_model_parameters(values, now)

    def eventFilter(self, watched, event):
        if (not self.closing and event.type() == QEvent.Type.MouseButtonDblClick
                and event.button() == Qt.MouseButton.LeftButton
                and isinstance(watched, QWidget)
                and (watched is self.renderer.widget or self.renderer.widget.isAncestorOf(watched))
                and hasattr(self, 'open_chat')):
            self.release_pointer()
            self.open_chat()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def bubble_anchor_rect(self):
        if self.art_bounds.isEmpty():
            return QRectF(self.rect()).adjusted(self.width() * .12, self.height() * .25, -self.width() * .12, 0)
        return QRectF(self.art_bounds)

    def bubble_head_x(self):
        return self.crown_x

    def shutdown(self):
        avatar = getattr(getattr(self, 'chat', None), 'avatar', None)
        if avatar: avatar.shutdown()
        self.anchor_timer.stop()
        self.window_manager.stop()
        self.timer.stop()
        self.renderer.closing = True
        self.renderer.poll.stop()
        self.renderer.shutdown()
        if not self.cleanup_ack:
            self.manager.close()


