"""One local IndexTTS worker and an ordered, memory-backed Qt playback queue."""
from collections import OrderedDict, deque
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QBuffer, QIODevice, QObject, QProcess, QProcessEnvironment, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from tt_indextts_resources import ROOT, FixedAudioCache, ResourceManager
from tt_natural_speech import STRENGTH, automatic_reaction, pause_after, style_for
from tt_speech_segments import ChineseTextSegmenter, PauseController
from tt_speech_wave import wav_edges

PREFIX = b"INDEXTTS_REPLY "
MAX_CLIP_BYTES = 8 * 1024 * 1024
RECENT_AUDIO_LIMIT = 12 * 1024 * 1024
RECENT_CLIP_LIMIT = 24


@dataclass(slots=True)
class Clip:
    owner: Path
    text: str
    reference: str
    options: dict
    volume: float
    pause: int
    expression: str | None
    remember: bool = False
    regenerate: bool = False


@dataclass(slots=True)
class Job:
    clip: Clip
    identifier: str
    cache_key: str | None
    packet: bytes
    recent_key: tuple | None = None


class IndexPlayback(QObject):
    def __init__(self,parent,cache,status,completed,on_playing=None):
        super().__init__(parent)
        self.cache,self.status,self.completed = cache,status,completed
        self.pending = deque()
        self.process = None
        self.worker_ready = False
        self.worker_handle = None
        self.retiring = False
        self.reference = ''
        self.free_vram = None
        self.idle_since = None
        self.output = bytearray()
        self.frame = None
        self.job = None
        self.cancelled = False
        self.buffered = None
        self.current = None
        self.current_wav = None
        self.device = None
        self.current_pause = 0
        self.current_tail = 0
        self.boundary = None
        self.current_expression = None
        self.on_playing = on_playing
        self.resources = ResourceManager()
        self.fixed = None
        self.recent_audio = OrderedDict()
        self.recent_bytes = 0
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.playbackStateChanged.connect(self.playback_state)
        self.player.errorOccurred.connect(self.playback_error)
        self.gap = QTimer(self,singleShot=True)
        self.gap.timeout.connect(self.advance)
        self.timeout = QTimer(self,singleShot=True)
        self.timeout.timeout.connect(lambda:self.fail("IndexTTS 生成超时，可重新点击朗读"))
        self.idle = QTimer(self,interval=20_000)
        self.idle.timeout.connect(self.check_resources)

    @property
    def active(self):
        return bool(self.pending or self.buffered or self.current or self.gap.isActive()
                    or (self.job is not None and not self.cancelled))

    def play(self,text,settings):
        self.idle_since = None
        owner = self.cache/(uuid4().hex+".wav")
        style = style_for(text,settings.get("speech_style","auto"))
        segmenter = ChineseTextSegmenter()
        scene = settings.get("speech_scene", "chat")
        text = segmenter.normalize(text, scene)
        scale = settings.get("developer_pause_scale", 1)
        options = dict(style=style,rate=settings.get("rate",0),
                       emotion_strength=settings.get("emotion_strength",STRENGTH),
                       fast_speech=bool(settings.get("fast_speech")))
        if scale != 1:
            options["developer_pause_scale"] = scale
        if settings.get("developer_trace"):
            options["developer_trace"] = True
        for part in segmenter.queue_blocks(text, responsive=scene in ('chat', 'reply')):
            part_options = options
            if settings.get("automatic_chat_delivery"):
                part_style, strength = automatic_reaction(part, style, options["emotion_strength"])
                part_options = dict(options, style=part_style, emotion_strength=strength)
            self.pending.append(Clip(owner, part, settings.get("voice_reference", ""), part_options,
                                     settings["volume"]/100, round(pause_after(part)*scale),
                                     settings.get("avatar_mode"), scene in ('chat', 'reply'),
                                     bool(settings.get('regenerate_speech'))))
        self.advance()
        return owner

    def has_audio(self,paths):
        return (self.current in paths
                or bool(self.buffered and self.buffered[0].owner in paths)
                or bool(self.job and not self.cancelled and self.job.clip.owner in paths)
                or any(clip.owner in paths for clip in self.pending))

    def warmup(self,reference):
        if reference and Path(reference).is_file():
            self.reference = reference
            if not self.active and self.process is None and self.resources.allow_warmup():
                self.start_worker()

    def check_resources(self):
        if self.process is None or self.active or self.job or self.retiring:
            return
        elapsed = time.monotonic()-self.idle_since if self.idle_since else 0
        if self.resources.retire(elapsed,self.free_vram):
            self.retiring = True
            self.process.closeWriteChannel()
        elif self.worker_ready:
            self.write(self.process,{"resources":True})

    def mark_idle(self):
        if self.idle_since is None:
            self.idle_since = time.monotonic()

    def start_audio(self,clip,wav):
        self.current,self.current_wav = clip.owner,wav
        self.current_pause,self.current_expression = clip.pause,clip.expression
        _, _, self.current_tail = wav_edges(wav)
        self.audio.setVolume(clip.volume)
        self.device = QBuffer(self)
        self.device.setData(wav)
        self.device.open(QIODevice.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self.device,QUrl("memory:/speech.wav"))
        if self.current:
            self.player.play()

    def advance(self):
        if self.current is None and self.buffered and not self.gap.isActive():
            clip,wav = self.buffered
            wav,head,_ = wav_edges(wav, trim=bool(self.boundary or self.pending))
            hold = 0
            if self.boundary is not None:
                ended,target,tail = self.boundary
                hold = PauseController.additional(target,head,tail,(time.monotonic()-ended)*1000)
            if hold:
                self.gap.start(hold)
            else:
                self.buffered = self.boundary = None
                self.start_audio(clip,wav)
        if self.job or self.buffered or self.retiring:
            return
        if not self.pending:
            if self.current is None and not self.gap.isActive():
                self.boundary = None
                self.mark_idle()
                self.status("语音待机")
                self.completed()
            return
        clip = self.pending.popleft()
        if not clip.reference or not Path(clip.reference).is_file():
            self.finish("失败：请选择有效的 IndexTTS 参考音色后重试")
            return
        if self.fixed is None or self.fixed.directory!=self.cache/"fixed":
            self.fixed = FixedAudioCache(self.cache/"fixed")
        try:
            recent_key = (FixedAudioCache.file_state(Path(clip.reference)), clip.text,
                          tuple(sorted(clip.options.items()))) if clip.remember else None
        except OSError:
            recent_key = None
        key = None if clip.regenerate else self.fixed.key(clip.reference,clip.text,clip.options)
        cached = None if clip.regenerate else self.recent_audio.get(recent_key)
        if cached:
            self.recent_audio.move_to_end(recent_key)
        elif key:
            cached = self.fixed.read(key)
        if cached is not None:
            self.buffered = clip,cached
            self.advance()
            return
        identifier = uuid4().hex
        packet = dict(id=identifier,text=clip.text,reference=clip.reference,**clip.options)
        self.job = Job(clip,identifier,key,self.encode(packet),recent_key)
        self.cancelled = False
        if self.current is None:
            self.status("生成中" if self.worker_ready else "语音加载中：IndexTTS 2.0")
        self.timeout.start(180_000)
        if self.process is None:
            self.start_worker()
        elif self.process.state()==QProcess.ProcessState.Running:
            self.send_request(self.process)

    @staticmethod
    def encode(packet):
        return (json.dumps(packet)+"\n").encode("ascii")

    def write(self,process,packet):
        return process.write(self.encode(packet))

    def start_worker(self):
        python = ROOT/".venv"/"Scripts"/"python.exe"
        if not python.is_file():
            if self.job:
                self.finish("失败：IndexTTS 2.0 本地环境不可用，修复环境后可重试")
            return
        self.cache.mkdir(parents=True,exist_ok=True)
        self.output.clear()
        self.frame = None
        self.worker_ready = False
        self.free_vram = None
        process = QProcess(self)
        self.process = process
        environment = QProcessEnvironment.systemEnvironment()
        for key,value in {
            "NLTK_DATA":ROOT/"nltk_data","HF_HOME":ROOT/"hf-cache",
            "HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","HF_HUB_DISABLE_TELEMETRY":"1",
            "NUMBA_CACHE_DIR":ROOT/"numba-cache","MPLCONFIGDIR":ROOT/"matplotlib-cache",
        }.items():
            environment.insert(key,str(value))
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(ROOT))
        log = self.cache / "worker.log"
        try:
            if log.stat().st_size > 512 * 1024:
                log.replace(self.cache / "worker.previous.log")
        except OSError:
            pass
        process.setStandardErrorFile(str(log), QIODevice.OpenModeFlag.Append)
        process.started.connect(lambda:self.worker_started(process))
        process.readyReadStandardOutput.connect(lambda:self.read_response(process))
        process.errorOccurred.connect(lambda error:self.worker_error(process,error))
        process.finished.connect(lambda *_:self.worker_exited(process))
        process.start(str(python),["-I","-B","-u","-X","utf8",
                                  str(Path(__file__).with_name("tt_indextts_worker.py")),str(ROOT),"--memory-pipe"])
        if not self.idle.isActive():
            self.idle.start()

    def worker_started(self,process):
        if process is not self.process or self.retiring:
            return
        if self.job:
            if self.cancelled:
                self.job = None
                self.timeout.stop()
                self.advance()
            else:
                self.send_request(process)
        elif self.reference:
            self.write(process,{"warmup":True,"reference":self.reference})

    def send_request(self,process):
        if process.write(self.job.packet)<0:
            self.fail("IndexTTS 后台连接中断，可重新点击朗读")

    def read_response(self,process):
        if process is not self.process or self.retiring:
            return
        self.output.extend(bytes(process.readAllStandardOutput()))
        try:
            while self.output:
                if self.frame is None:
                    end = self.output.find(b"\n")
                    if end<0:
                        if len(self.output)>4096:
                            raise ValueError("Invalid frame header")
                        return
                    line = bytes(self.output[:end])
                    del self.output[:end+1]
                    if not line.startswith(PREFIX):
                        continue
                    self.frame = json.loads(line[len(PREFIX):])
                length = self.frame["bytes"]
                if not isinstance(length,int) or not 0<=length<=MAX_CLIP_BYTES:
                    raise ValueError("Invalid frame length")
                if len(self.output)<length:
                    return
                response,data = self.frame,bytes(self.output[:length])
                self.frame = None
                del self.output[:length]
                self.receive(response,data)
        except (ValueError,KeyError,TypeError):
            self.fail("IndexTTS 返回的音频无效，可重新点击朗读")

    def receive(self,response,data):
        if "worker_pid" in response:
            if self.worker_handle is None and hasattr(ctypes,"windll"):
                open_process = ctypes.windll.kernel32.OpenProcess
                open_process.argtypes = (wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
                open_process.restype = wintypes.HANDLE
                self.worker_handle = open_process(0x0001,False,response["worker_pid"])
            return
        if "free_mib" in response:
            self.free_vram = response["free_mib"]
            self.worker_ready = True
        if response.get("model_ready"):
            if self.job and not self.cancelled:
                self.timeout.start(180_000)
                if self.current is None:
                    self.status("生成中")
            return
        if response.get("warmup"):
            if not self.active:
                self.mark_idle()
                if not response.get("ok"):
                    self.retiring = True
                    self.process.closeWriteChannel()
            return
        job = self.job
        if not job or job.identifier!=response.get("id"):
            return
        self.timeout.stop()
        self.job = None
        if self.cancelled or response.get("cancelled"):
            self.advance()
            return
        if not response.get("ok"):
            reason = ("IndexTTS 正在被本机网页服务使用，请停止网页服务后重试"
                      if response.get("busy") else response.get("reason","IndexTTS 合成失败，可重新点击朗读"))
            self.finish("失败："+reason)
            if not self.worker_ready:
                self.retiring = True
                self.process.closeWriteChannel()
            return
        if len(data)<44 or data[:4]!=b"RIFF" or data[8:12]!=b"WAVE":
            self.fail("IndexTTS 返回的音频无效，可重新点击朗读")
            return
        self.fixed.put(job.cache_key,data)
        self.remember_audio(job.recent_key, data)
        self.buffered = job.clip,data
        self.advance()

    def remember_audio(self, key, data):
        if key is None:
            return
        self.recent_bytes -= len(self.recent_audio.pop(key, b''))
        self.recent_audio[key] = data
        self.recent_bytes += len(data)
        while self.recent_bytes > RECENT_AUDIO_LIMIT or len(self.recent_audio) > RECENT_CLIP_LIMIT:
            _, older = self.recent_audio.popitem(last=False)
            self.recent_bytes -= len(older)

    def worker_error(self,process,error):
        if error==QProcess.ProcessError.FailedToStart:
            self.worker_exited(process)

    def worker_exited(self,process):
        if process is not self.process:
            return
        expected = self.retiring
        self.process = None
        self.idle.stop()
        self.retiring = False
        self.worker_ready = False
        self.output.clear()
        self.frame = None
        if self.worker_handle:
            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self.worker_handle))
            self.worker_handle = None
        process.deleteLater()
        if expected:
            if self.pending:
                self.advance()
        elif self.job:
            self.finish("失败：IndexTTS 后台退出，请查看语音日志后重试")
        else:
            self.mark_idle()

    def playback_state(self,state):
        if self.current and state==self.player.PlaybackState.PlayingState:
            if self.on_playing is not None and self.current_expression is not None:
                self.on_playing(self.current_expression)
            self.status("播放中")

    def media_status(self,state):
        if self.current and state==self.player.MediaStatus.EndOfMedia:
            self.boundary = time.monotonic(),self.current_pause,self.current_tail
            self.release_current()
            if self.job:
                self.status("生成中")
            self.advance()

    def playback_error(self,*_):
        if self.current:
            self.stop()
            self.status("失败：无法通过默认音频设备播放，检查输出设备后可重试")

    def release_current(self):
        self.current = self.current_wav = self.current_expression = None
        self.player.stop()
        self.player.setSource(QUrl())
        if self.device:
            self.device.close()
            self.device.deleteLater()
            self.device = None

    def finish(self,status):
        self.timeout.stop()
        self.job = None
        self.stop()
        self.status(status)
        self.completed()

    def fail(self,message):
        if self.process:
            self.retiring = True
            self.kill_worker()
        self.finish("失败："+message)

    def kill_worker(self):
        if self.worker_handle:
            ctypes.windll.kernel32.TerminateProcess(wintypes.HANDLE(self.worker_handle),1)
        elif self.process:
            self.process.kill()

    def cancel_job(self):
        if self.job and not self.cancelled:
            self.cancelled = True
            if self.process and self.process.state()==QProcess.ProcessState.Running:
                self.write(self.process,{"cancel":self.job.identifier})

    def stop_owner(self, owner):
        """Stop one utterance while leaving other producers' clips in the queue."""
        if owner is None or not self.has_audio((owner,)):
            return False
        self.pending = deque(clip for clip in self.pending if clip.owner != owner)
        if self.job and self.job.clip.owner == owner:
            self.cancel_job()
        if self.buffered and self.buffered[0].owner == owner:
            self.buffered = None
            self.gap.stop()
        if self.current == owner:
            self.boundary = None
            self.release_current()
        self.advance()
        return True

    def stop(self):
        self.gap.stop()
        self.boundary = None
        self.pending.clear()
        self.cancel_job()
        self.buffered = None
        self.release_current()
        self.mark_idle()

    def shutdown(self):
        self.stop()
        self.recent_audio.clear()
        self.recent_bytes = 0
        self.timeout.stop()
        self.idle.stop()
        process = self.process
        if process is not None:
            self.retiring = True
            process.closeWriteChannel()
            if not process.waitForFinished(500):
                self.kill_worker()
                process.waitForFinished(1000)
        self.job = None
