"""Desktop pet controls for local IndexTTS speech."""
import re
from collections import deque
from PySide6.QtCore import QTimer
from tt_game_activity import GameActivity
from tt_speech_segments import ENTITIES


GAME_PAUSED_STATUS = '游戏中，TT 语音已暂停'


_FACE_LETTERS = frozenset("ωΩДд▽▼口益皿艸人ワヮㅂㅅㅜㅠᵕᴗᴖᴥᵔ๑ಠಥ눈ツシゝつづòóÒÓèéっッʕʔงوoOTQqUwuvVxX")
_FACE_MARKS = frozenset("_^＿＾ω▽▼Дд╥〒≧≦•●◕◉‿﹏︿乂ಥಠ눈˘´｀￣＞＜>‹›❛°")
_FACE_BODY = r"(?:\([^()（）\n]{1,40}\)|（[^()（）\n]{1,40}）|\[[^\[\]\n]{1,40}\]|【[^【】\n]{1,40}】|［[^［］\n]{1,40}］)"
_FACE_ARMS = r"[ヽヾ╰╯╮╭ノﾉ٩۶୧୨ᕕᕗ乁┌┐└┘づつงو✧]"
_BRACKETED_FACE = re.compile(rf"{_FACE_ARMS}*(?P<body>{_FACE_BODY}){_FACE_ARMS}*(?:o(?![A-Za-z0-9_]))?")
_BARE_FACE = re.compile(
    r"(?<![A-Za-z0-9_])(?:T[_-]T|Q[_-]Q|Q[AaWw]Q|>_<|;_;|\^_\^|ಠ_ಠ|ಥ_ಥ|눈_눈|╥﹏╥|"
    r"-\.-|[oO][._][oO]|[:;=8][-~']?[)DPp(/]|[xX]D|[UuOo][Ww][UuOo])(?![A-Za-z0-9_])"
)


def strip_kaomoji(text):
    def remove_bracketed(match):
        inner = match["body"][1:-1].strip()
        if _BARE_FACE.fullmatch(inner):
            return ""
        if (len(inner) >= 2 and any(ch in _FACE_MARKS for ch in inner)
                and all(ch in _FACE_LETTERS or not ch.isalnum() for ch in inner)):
            return ""
        return match[0]

    return _BARE_FACE.sub("", _BRACKETED_FACE.sub(remove_bracketed, text))


def spoken_text(text, *, keep_breaks=False):
    text = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)', '。代码略。', text)
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    entities = []

    def protect(match):
        entities.append(match[0])
        return f"\ue000{len(entities)-1}\ue001"

    text = ENTITIES.sub(protect, text)
    text = strip_kaomoji(text)
    text = re.sub(r'(?m)^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+|\d+\.\s+)', '', text)
    text = re.sub(r'[*_~#|♡♥☆★^<>\\{}\[\]]', '', text)
    text = re.sub(r'[^\w\s，。！？；：、,.!?;:（）()「」『』“”‘’《》—…%％℃°+\-\"\u3400-\u9fff\ue000\ue001]', '', text)
    text = re.sub(r'\ue000(\d+)\ue001', lambda match: entities[int(match[1])], text)
    spaces = r'[^\S\n]+' if keep_breaks else r'\s+'
    if not any(ch.isalnum() for ch in text):
        return ''
    cleaned = re.sub(spaces, ' ', text)
    return cleaned.lstrip().rstrip(" \t") if keep_breaks else cleaned.strip()


class Voice:
    def __init__(self, parent):
        from tt_indextts_playback import IndexPlayback
        from tt_voice_profiles import voice_directory

        self.status = '语音待机'
        self.scene = None
        self.reply_turn = None
        self.listeners = []
        self.seen = deque(maxlen=128)
        self.player = IndexPlayback(parent, voice_directory().parent / 'cache' / 'indextts',
                                    self._status, lambda: None,
                                    on_playing=getattr(parent, 'avatar_speaking', None))
        self.game_activity = GameActivity()
        self.game_paused = False
        self.game_timer = QTimer(parent, interval=1000)
        self.game_timer.timeout.connect(self.check_game)
        self.game_settings = getattr(parent, 'pro_settings', {})
        self.apply_game_preference()

    @property
    def active(self):
        return self.player.active

    def _status(self, status):
        if getattr(self, 'game_paused', False):
            status = GAME_PAUSED_STATUS
        self.status = status
        for callback in self.listeners:
            callback(status)

    def stop(self):
        self.reply_turn = None
        self.player.stop()
        self._status('已停止')

    def shutdown(self):
        if hasattr(self, 'game_timer'):
            self.game_timer.stop()
            self.game_activity.clear()
        self.player.shutdown()

    def apply_game_preference(self):
        if self.game_activity.supported and self.game_settings.get('pause_voice_in_games', True):
            self.game_timer.start()
        else:
            self.game_timer.stop()
            self.game_activity.clear()
        self.check_game()

    def check_game(self):
        paused = self.game_timer.isActive() and self.game_activity.active()
        if paused != self.game_paused:
            self.game_paused = paused
            if paused:
                self.reply_turn = None
            self.player.set_game_paused(paused)
            self._status('语音待机')
        return paused

    def warmup(self, reference):
        if not self.check_game():
            self.player.warmup(reference)

    def play(self, text, settings, kind='proactive', message_id=None, *, reply_turn=None):
        if settings.get('muted') or not settings.get('speech_enabled', True):
            self.stop()
            return
        if kind not in ('preview', 'replay') and not settings.get('reply_voice' if kind == 'reply' else 'voice'):
            return
        if message_id is not None and message_id in self.seen:
            return
        if message_id is not None:
            self.seen.append(message_id)
        return self._queue(text, settings, scene='reply' if kind == 'replay' else kind,
                           reply_turn=reply_turn)

    def stream_piece(self, text, settings, *, reply_turn=None):
        if settings.get('speech_enabled') and settings.get('reply_voice') and not settings.get('muted'):
            self._queue(text, settings, report_empty=False, reply_turn=reply_turn)

    def _queue(self, text, settings, *, report_empty=True, scene="chat", reply_turn=None):
        if hasattr(self, 'game_timer') and self.check_game():
            return
        text = spoken_text(text, keep_breaks=True)
        if not text:
            if report_empty:
                self._status('没有可朗读的文字')
            return
        try:
            self.scene = scene
            self.reply_turn = reply_turn
            return self.player.play(text, dict(settings, speech_scene=scene))
        except (OSError, RuntimeError):
            self.player.stop()
            self._status('失败：无法启动 IndexTTS 语音或访问语音缓存')
