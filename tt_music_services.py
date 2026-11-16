"""Single active music source, with independent Windows client bindings."""
from base64 import b64encode
from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import time

from PySide6.QtCore import QObject, QProcess, QTemporaryDir, QTimer, Signal

from tt_netease_now_playing import LOCAL_SOURCE as NETEASE_LOCAL_SOURCE, NetEaseNowPlaying


@dataclass(frozen=True)
class Platform:
    name: str
    mark: str
    color: str
    source_names: tuple[str, ...]
    website: str


PLATFORMS = {
    'netease': Platform('网易云音乐', '云', '#bc3345', ('cloudmusic', 'neteasecloudmusic'), 'https://music.163.com/#/download'),
    'apple': Platform('Apple Music', '♪', '#b23758', ('applemusic', 'applemusicwin'), 'https://support.apple.com/zh-cn/guide/music-windows/mus19e7bc658/windows'),
}
WAITING_CLIENTS = ('apple', 'netease')


@dataclass(frozen=True)
class MusicState:
    source: str = ''
    title: str = ''
    artist: str = ''
    album: str = ''
    artwork: str = ''
    status: str = 'Stopped'
    position: int = 0
    duration: int = 0
    capabilities: frozenset = frozenset()
    online: bool = False
    artwork_url: str = ''


def matches_platform(platform, source):
    # AUMIDs use publisher/product segments or an executable filename.
    # Do not infer a platform from a song title, browser tab or partial match.
    identity = source.replace('\\', '/').rsplit('/', 1)[-1].casefold()
    segments = re.split(r'[.!_\-]', identity)
    return any(name in segments for name in platform.source_names)


class WindowsSMTCProvider(QObject):
    """Asynchronous WinRT calls through Windows PowerShell."""
    sessions_changed = Signal()
    timeline_received = Signal(object)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.readyReadStandardError.connect(self.drain_error)
        self.process.finished.connect(self.exited)
        self.process.errorOccurred.connect(self.process_error)
        self.watchdog = QTimer(self, singleShot=True, interval=7500)
        self.watchdog.timeout.connect(self.timed_out)
        self.buffer = bytearray()
        self.callback = None
        self.closing = False
        self.events = QProcess(self)
        self.events.readyReadStandardOutput.connect(self.read_events)
        self.events.readyReadStandardError.connect(self.events.readAllStandardError)
        self.event_ready = False
        self.last_event = ''
        self.events_seen = {}
        self.timeline_sequence = 0
        self.timeline_request = None
        self.timeline_directory = QTemporaryDir()
        self.events.finished.connect(self.events_exited)

    def start_events(self):
        path = Path(__file__).resolve().with_name('tt_smtc_events.exe')
        if sys.platform == 'win32' and path.is_file():
            self.events.start(str(path), [self.timeline_directory.filePath('timeline')])

    def read_events(self):
        while self.events.canReadLine():
            notice = bytes(self.events.readLine()).strip()
            if notice.startswith(b'timeline\t'):
                self.read_timeline(notice)
                continue
            if notice == b'ready':
                self.event_ready = True
            if notice in (b'ready', b'sessions', b'media', b'playback'):
                self.last_event = notice.decode('ascii')
                self.events_seen[self.last_event] = self.events_seen.get(self.last_event, 0) + 1
                self.sessions_changed.emit()

    def events_exited(self, *_):
        self.event_ready = False
        self.timeline_request = None

    def request_timeline(self, source):
        now = time.monotonic()
        if not self.event_ready or not self.timeline_directory.isValid() or (
                self.timeline_request and now-self.timeline_request[2]<2):
            return
        self.timeline_sequence += 1
        self.timeline_request = (self.timeline_sequence,source,now)
        try:
            Path(self.timeline_directory.filePath('timeline')).write_bytes(
                str(self.timeline_sequence).encode('ascii')+b'\t'+b64encode(source.encode('utf8')))
        except OSError:
            self.timeline_request = None

    def read_timeline(self, notice):
        fields = notice.split(b'\t')
        pending = self.timeline_request
        if not pending or fields[1]!=str(pending[0]).encode('ascii'):
            return
        self.timeline_request = None
        if len(fields)==8:
            _, source, sent = pending
            _, _, status, position, duration, rate, revision, raw = fields
            now = time.monotonic()
            if now-sent<1:
                try:
                    sample = (source,status.decode('ascii'),int(position),int(duration),
                              float(rate),int(revision),int(raw),(now+sent)/2)
                except ValueError:
                    return
                self.timeline_received.emit(sample)

    def request(self, action, callback, **parameters):
        if self.closing or self.callback is not None or self.process.state()!=QProcess.ProcessState.NotRunning:
            return False
        if sys.platform != 'win32':
            QTimer.singleShot(0, self, lambda: callback(False, 'Windows SMTC 仅在 Windows 上可用。'))
            return True
        self.callback = callback
        self.watchdog.start()
        self.buffer.clear()
        shell = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = Path(__file__).resolve().with_name('tt_smtc.ps1')
        self.process.start(str(shell), ['-MTA', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'RemoteSigned', '-File', str(script)])
        if self.callback is not None:
            self.process.write((json.dumps({'action': action, **parameters}, ensure_ascii=True)+'\n').encode('ascii'))
            self.process.closeWriteChannel()
        return True

    def read(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.buffer)>3_000_000:
            self.timed_out()
            return

    def drain_error(self):
        self.process.readAllStandardError()

    def exited(self, *_):
        self.read()
        try:
            reply = json.loads(self.buffer)
            ok = bool(reply['ok'])
            value = reply.get('value') if ok else reply.get('error', 'Windows 媒体会话不可用。')
        except (ValueError, KeyError, TypeError):
            self.finish(False, 'Windows 媒体会话服务未返回有效响应。')
        else:
            self.finish(ok, value)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.finish(False, '无法启动 Windows PowerShell 媒体会话服务。')

    def timed_out(self):
        self.finish(False, 'Windows 媒体会话检测超时，请重试。')
        self.process.kill()

    def finish(self, ok, value):
        self.watchdog.stop()
        callback, self.callback = self.callback, None
        if callback and not self.closing:
            callback(ok, value)

    def close(self):
        self.closing = True
        self.watchdog.stop()
        self.callback = None
        for process in (self.process, self.events):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(700)


class ClientMusicProvider:
    """One official client and its currently bound platform session."""
    def __init__(self, platform, bridge, source=''):
        self.platform = platform
        self.bridge = bridge
        self.state = MusicState(source=source)

    def apply(self, value):
        current = self.state if self.state.source==value['source'] else MusicState()
        title = value.get('title',current.title)
        artist = value.get('artist',current.artist)
        album = value.get('album',current.album)
        existing_art = current.artwork if (title,artist,album)==(current.title,current.artist,current.album) else ''
        self.state = MusicState(source=value['source'], online=True, title=value.get('title', current.title),
            artist=artist, album=album,
            artwork=value.get('art', existing_art), artwork_url=value.get('artwork_url', ''), status=value['status'],
            position=value['position'], duration=value['duration'],
            capabilities=frozenset(k for k,v in value['capabilities'].items() if v))

    def disconnect(self):
        self.state = MusicState()

    def get_current_track(self):
        return self.state

    def get_playback_state(self):
        return self.state.status

    def get_capabilities(self):
        return self.state.capabilities if self.state.online else frozenset()

    def control(self, action, callback, **parameters):
        if action not in self.get_capabilities():
            callback(False, '当前客户端会话没有开放这个操作。')
            return True
        return self.bridge.request(action, callback, source=self.state.source, **parameters)


class MusicStateManager(QObject):
    changed = Signal()
    feedback = Signal(str)
    seek_result = Signal(str, int, bool)


class MusicServiceManager(MusicStateManager):
    def __init__(self, settings, parent=None, bridge=None):
        super().__init__(parent)
        self.settings = settings
        self.bridge = bridge or WindowsSMTCProvider(self)
        saved = settings.get_setting('music_platform_bindings', {})
        if not isinstance(saved, dict):
            saved = {}
        self.providers = {key: ClientMusicProvider(platform, self.bridge, str(saved.get(key, '')))
                          for key, platform in PLATFORMS.items()}
        self.netease_local = NetEaseNowPlaying()
        self.bindings = {key: provider.state.source for key, provider in self.providers.items() if provider.state.source}
        if any(key not in PLATFORMS for key in saved):
            settings.save_setting('music_platform_bindings', self.bindings.copy())
        self.inventory = {}
        self.rows = {key:{} for key in PLATFORMS}
        self.last_errors = {}
        self.control_results = {}
        self.sampled_at = ''
        self.inventory_time = float('-inf')
        self.current_session = ''
        self.sources = []
        self.followup = False
        from tt_apple_music import AppleMusicProvider
        self.apple = AppleMusicProvider(self)
        self.apple.changed.connect(self.changed)
        self.apple.feedback.connect(self.feedback)
        chosen = settings.get_setting('music_platform_active')
        if chosen is not None and chosen not in (*PLATFORMS, 'musickit', 'local'):
            chosen = 'local'
            settings.save_setting('music_platform_active', chosen)
        self.auto_select_apple = chosen is None
        self.resume = chosen if chosen in self.bindings and chosen not in WAITING_CLIENTS else ''
        self.active = (chosen if chosen in WAITING_CLIENTS else
                       'musickit' if chosen == 'musickit' and self.apple.user and self.apple.developer_ready else 'local')
        self.apple.set_active(self.active == 'musickit')
        self.pause_previous = bool(settings.get_setting('music_platform_pause_previous', True))
        self.pending = False
        self.polling = False
        self.deferred_request = None
        self.pause_local = None
        self.bubble_source = None
        self.timer = QTimer(self, interval=5000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        if isinstance(self.bridge, WindowsSMTCProvider):
            self.bridge.sessions_changed.connect(self.session_notice)
            self.bridge.start_events()
            QTimer.singleShot(0, self, self.poll)

    def state(self, platform=None):
        if (platform or self.active)=='musickit':
            return self.apple.state
        provider = self.providers.get(platform or self.active)
        return provider.state if provider else MusicState()

    def persist(self):
        self.settings.save_setting('music_platform_bindings', self.bindings.copy())
        self.settings.save_setting('music_platform_active', self.resume or self.active)

    def set_pause_previous(self, value):
        self.pause_previous = bool(value)
        self.settings.save_setting('music_platform_pause_previous', self.pause_previous)

    def disconnect(self, key):
        if self.polling or self.pending:
            self.deferred_request = lambda: self.disconnect(key)
            return
        self.bindings.pop(key, None)
        if self.resume == key:
            self.resume = ''
        if self.active == key:
            self.active = 'local'
            self.auto_select_apple = False
        self.persist()
        self.changed.emit()
        self.feedback.emit(f'已解除 TT 对{PLATFORMS[key].name}会话的绑定。')

    def attach(self, key, *, activate=False, test=False):
        def result():
            if activate and key in WAITING_CLIENTS:
                self.switch_to(key)
                return
            if not self.state(key).online:
                self.last_errors[key] = self.session_status(key)
                self.feedback.emit(PLATFORMS[key].name+' · '+self.last_errors[key])
                self.changed.emit()
                return
            if activate:
                self.switch_to(key)
            elif test:
                self.feedback.emit(PLATFORMS[key].name+' · '+self.session_status(key)+'，详细能力见诊断。')
            elif self.state(key).source != NETEASE_LOCAL_SOURCE:
                self.bindings[key] = self.state(key).source
                self.persist()
                self.changed.emit()
        self.poll(force_inventory=True, artwork=(key,), completed=result)

    def launch_client(self, key):
        def launch():
            record = self.inventory.get(key,{})
            target = record.get('launch','')
            if not target:
                self.feedback.emit(PLATFORMS[key].name+(' 已检测到安装，请从开始菜单手动打开。' if record.get('installed')
                                                       else ' 未检测到可启动的官方客户端。'))
                return
            if '!' in target and not Path(target).is_absolute():
                executable, args = str(Path(os.environ['SystemRoot'])/'explorer.exe'), ['shell:AppsFolder\\'+target]
            else:
                executable, args = target, []
            started, _ = QProcess.startDetached(executable,args)
            self.feedback.emit('已请求打开'+PLATFORMS[key].name+'。请播放歌曲，TT 会自动检测会话。' if started else '无法启动官方客户端。')
            if started:
                QTimer.singleShot(2400,self,lambda: self.poll(force_inventory=True))
        self.poll(force_inventory=True,completed=launch)

    def switch_to(self, key):
        if key not in (*PLATFORMS, 'musickit', 'local'):
            return
        if key=='local' and self.active=='local':
            self.auto_select_apple = False
            self.resume = ''
            self.persist()
            self.changed.emit()
            return
        if self.polling:
            self.deferred_request = lambda: self.switch_to(key)
            return
        if self.pending:
            self.feedback.emit('媒体服务正在执行另一个操作，请重试。')
            return
        if key=='musickit' and not self.apple.authorized:
            self.feedback.emit('请打开 MusicKit 完成 Apple 授权，再设置为当前平台。')
            return
        if (key in self.providers and key not in WAITING_CLIENTS
                and (not self.state(key).online or not self.state(key).capabilities)):
            self.feedback.emit('该来源尚未发布可控制的会话，请在官方客户端播放后重试。')
            return
        self.resume = ''
        self.auto_select_apple = False
        if self.active == key:
            if key in self.providers and self.state(key).online and self.state(key).source != NETEASE_LOCAL_SOURCE:
                self.bindings[key] = self.state(key).source
            self.persist()
            self.changed.emit()
            return
        old = self.active
        self.active = key
        if key in self.providers and self.state(key).online and self.state(key).source != NETEASE_LOCAL_SOURCE:
            self.bindings[key] = self.state(key).source
        self.apple.set_active(key=='musickit')
        self.persist()
        self.changed.emit()
        self.feedback.emit('已切换到' + (PLATFORMS[key].name if key in PLATFORMS else 'Apple Music' if key=='musickit' else '本地音频'))
        if self.pause_previous and old == 'local' and self.pause_local:
            self.pause_local()
        elif self.pause_previous and old in self.providers and self.state(old).status == 'Playing':
            self.pause_provider(self.providers[old])
        elif self.pause_previous and old=='musickit' and self.apple.state.status=='Playing':
            self.pause_provider(self.apple)

    def pause_provider(self, provider):
        self.pending = True
        self.changed.emit()
        def paused(ok, error):
            self.pending = False
            if not ok:
                self.feedback.emit('已切换，原平台未暂停。'+str(error))
            self.changed.emit()
            self.flush_deferred()
        if not provider.control('pause', paused):
            paused(False, '媒体服务忙。')

    def apple_command(self, action, **params):
        def result(ok, value):
            if ok and action=='disconnect' and self.active=='musickit':
                self.switch_to('local')
            self.feedback.emit(('Apple Music 验证成功。' if action=='test' else '已清除 TT 的 Apple 授权。') if ok else str(value))
            self.changed.emit()
        accepted = (self.apple.disconnect(result) if action=='disconnect'
                    else self.apple.control(action,result,**params))
        if not accepted:
            result(False,'MusicKit 正在执行其他操作，请重试。')

    def session_notice(self):
        self.followup = True
        self.poll()

    def session_status(self, key):
        state = self.state(key)
        row = self.rows[key]
        if row.get('error'):
            return row['error']
        if state.online and state.source == NETEASE_LOCAL_SOURCE:
            return '已读取网易云本地播放状态 · 客户端未开放播放控制'
        if state.online:
            return {'Playing':'正在播放','Paused':'已暂停','Opened':'已打开，等待播放','Stopped':'已停止'}.get(state.status,'媒体会话可用')
        if key=='apple' and self.active=='apple':
            return '媒体会话不可用 · 等待 Apple Music 客户端'
        app = self.inventory.get(key)
        if key=='netease' and app and app['running']:
            return '网易云已打开 · 等待 Windows 媒体会话。请播放歌曲；若客户端有「开启 SMTC」，请打开后重新检测。'
        if app and app['running']:
            return '客户端运行中 · 未发布媒体会话（请在客户端播放后再检测）'
        if app and app['installed']:
            return '已检测到安装 · 客户端未运行'
        return '未在 Windows 登记中检测到客户端' if app is not None else '正在检查 Windows 客户端'

    def diagnostics(self, key):
        app, row, state = self.inventory.get(key,{}), self.rows[key], self.state(key)
        def yes(value):
            return '是' if value else '否'
        metadata = '已读取' if row.get('metadata') else '未读取'
        if row.get('metadata') and not state.title:
            metadata = '已读取，客户端未发布曲名'
        cover = '已读取封面地址' if state.online and state.artwork_url else yes(bool(state.artwork and state.online))
        return '\n'.join((
            f'Provider ID：{key}', f'检测时间：{self.sampled_at or "尚未检测"}',
            f'安装登记：{yes(app["installed"]) if "installed" in app else "未知"}',
            f'客户端运行：{yes(app["running"]) if "running" in app else "未知"}',
            f'AppUserModelId：{", ".join(row.get("sources", ())) or "—"}',
            f'会话存在：{yes(bool(row.get("sources")))}', f'会话状态：{self.session_status(key)}',
            f'Windows 当前会话：{self.current_session or "—"}',
            f'Windows 媒体会话数量：{len(self.sources)}',
            f'Windows 全部会话：{", ".join(self.sources) or "—"}',
            f'Windows 事件监听：{"已就绪" if getattr(self.bridge,"event_ready",False) else "定时检测"}',
            f'最近事件：{getattr(self.bridge,"last_event","") or "—"}',
            f'曲目信息：{metadata}；封面：{cover}',
            f'曲名可读：{yes(bool(state.title and state.online))}；歌手可读：{yes(bool(state.artist and state.online))}',
            f'曲目：{state.title or "—"} / {state.artist or "—"} / {state.album or "—"}',
            f'进度：{state.position} / {state.duration} ms；读到时间线：{yes(state.online and state.duration>0)}',
            '会话声明的控制能力：'+(', '.join(sorted(state.capabilities)) or '无'),
            f'当前音乐来源 ID：{self.active}',
            f'音乐气泡来源 ID：{self.bubble_source or "尚未初始化"}；已绑定到此会话：{yes(self.bubble_source==key and state.online)}',
            '实际控制测试：'+self.control_results.get(key,'尚未发送控制命令'),
            '最后错误：'+(row.get('error') or row.get('metadata_error') or self.last_errors.get(key) or '—'),
            '账号授权：由官方客户端管理，Windows 会话不提供账号状态。',
        ))

    def apply_snapshot(self, value):
        if value.get('inventory') is not None:
            self.inventory = value['inventory']
            self.inventory_time = time.monotonic()
        self.current_session = value.get('current','')
        entries = value.get('sessions',[])
        self.sources = [entry['source'] for entry in entries]
        for key, provider in self.providers.items():
            candidates = [entry for entry in entries if matches_platform(provider.platform, entry['source'])]
            desired = self.bindings.get(key)
            exact = [entry for entry in candidates if entry['source']==desired]
            selected = exact[0] if len(exact)==1 else candidates[0] if len(candidates)==1 else {}
            row = {'sources':[entry['source'] for entry in candidates]}
            media = selected.get('media')
            if media:
                provider.apply(media)
                self.last_errors.pop(key,None)
                row.update(metadata=bool(media.get('metadata') or media.get('title')), metadata_error=media.get('metadataError',''))
            else:
                provider.state = replace(provider.state, online=False, capabilities=frozenset())
                if candidates:
                    row['error'] = selected.get('error') or '检测到多个会话，无法安全选择控制目标'
            self.rows[key] = row
        if self.auto_select_apple and self.active=='local':
            apple = self.state('apple')
            if apple.online and apple.title and apple.capabilities and apple.status=='Playing':
                self.switch_to('apple')
        if self.active in self.providers and self.active not in WAITING_CLIENTS and not self.state().online:
            self.resume = self.active
            self.active = 'local'
            self.feedback.emit('原音乐会话已断开，已切回本地音频。')
        if self.resume and self.state(self.resume).online and self.state(self.resume).capabilities:
            self.active = self.resume
            self.resume = ''
            self.bindings[self.active] = self.state().source
            self.persist()
        elif self.active in WAITING_CLIENTS and self.active not in self.bindings and self.state().online:
            self.bindings[self.active] = self.state().source
            self.persist()
        window = value.get('netease_window')
        if not self.state('netease').online and (window or self.active == 'netease'):
            reading = self.netease_local.read(window or {})
            if reading:
                self.providers['netease'].apply(reading)
                self.rows['netease'] = {'sources': [], 'metadata': True}

    def poll(self, *, force_inventory=False, artwork=(), completed=None):
        if self.pending or self.polling:
            if completed:
                self.deferred_request = lambda: self.poll(force_inventory=force_inventory, artwork=artwork, completed=completed)
            return
        self.followup = False
        self.polling = True
        known = {p.state.source:[p.state.title,p.state.artist,p.state.album]
                 for p in self.providers.values() if p.state.online and p.state.artwork}
        target_art = list(set(artwork) | {self.active, self.resume} | ({'apple'} if self.auto_select_apple else set()))

        def fetched(ok, value):
            self.polling = False
            self.sampled_at = datetime.now().astimezone().isoformat(timespec='seconds')
            if ok:
                self.apply_snapshot(value)
            else:
                for key, row in self.rows.items():
                    row['error'] = str(value)
                    self.providers[key].state = replace(self.state(key),online=False,capabilities=frozenset())
                if self.active in self.providers and self.active not in WAITING_CLIENTS:
                    self.resume, self.active = self.active, 'local'
                if completed:
                    self.feedback.emit(str(value))
            self.changed.emit()
            if completed:
                completed()
            self.flush_deferred()

        if not self.bridge.request('snapshot', fetched, identities={key:p.source_names for key,p in PLATFORMS.items()},
                labels={key:p.name for key,p in PLATFORMS.items()}, artwork=target_art, known=known,
                netease_window=self.active == 'netease' or 'netease' in artwork,
                netease_pid=self.netease_local.pid or 0,
                inventory=force_inventory or (time.monotonic()-self.inventory_time)>12):
            fetched(False,'Windows 会话检测暂不可用，请重试。')

    def flush_deferred(self):
        if not self.pending and not self.polling:
            queued, self.deferred_request = self.deferred_request, None
            if queued:
                queued()
            elif self.followup:
                self.poll()

    def test_control(self, key):
        if self.polling or self.pending:
            self.deferred_request = lambda: self.test_control(key)
            return
        state = self.state(key)
        action = 'pause' if state.status=='Playing' else 'play'
        if action not in state.capabilities and 'toggle' in state.capabilities:
            action = 'toggle'
        if not state.online or not state.title or action not in state.capabilities:
            self.control_results[key] = '未执行：需要已发布曲目及对应的播放或暂停能力。'
            self.feedback.emit(self.control_results[key])
            return
        self.send_control(key, action)

    def control(self, action, **parameters):
        active = self.active
        if action=='seek' and (self.polling or self.pending) and active in self.providers:
            state = self.state()
            target = (active,state.source,state.title,state.artist,state.duration)
            def seek_when_ready():
                current = self.state()
                if target==(self.active,current.source,current.title,current.artist,current.duration) and current.online:
                    self.control(action, **parameters)
                else:
                    self.seek_result.emit(active,parameters['position'],False)
                    self.feedback.emit('播放来源或曲目已改变，已取消跳转。')
            self.deferred_request = seek_when_ready
            return
        if self.polling:
            self.deferred_request = lambda: self.control(action, **parameters) if self.active==active else None
            return
        if self.active=='musickit':
            def result(ok, value):
                if action=='seek':
                    self.seek_result.emit(active,parameters['position'],ok)
                self.report_control(ok,value)
            if action not in self.apple.state.capabilities:
                result(False,'MusicKit 当前播放器未开放此操作。')
            elif not self.apple.control(action, result, **parameters):
                result(False,'MusicKit 正在执行其他操作，请重试。')
            return
        if self.active not in self.providers:
            return
        if self.pending:
            return
        self.send_control(self.active, action, **parameters)

    def send_control(self, key, action, **parameters):
        def done(ok, value):
            self.pending = False
            result = ('客户端已接受跳转命令，等待实际位置确认' if key=='apple' and action=='seek'
                      else '客户端已接受命令') if ok else str(value)
            self.control_results[key] = f'{action} · {result}'
            if not ok:
                self.last_errors[key] = str(value)
            if action=='seek':
                self.seek_result.emit(key,parameters['position'],ok)
            self.feedback.emit(PLATFORMS[key].name+' · '+self.control_results[key])
            self.changed.emit()
            self.poll(artwork=(key,))
        self.pending = True
        self.changed.emit()
        if not self.providers[key].control(action, done, **parameters):
            done(False,'媒体服务忙，请稍后重试。')

    def close(self):
        self.timer.stop()
        self.deferred_request = None
        self.bridge.close()
        self.apple.close()

    def report_control(self, ok, value):
        if not ok:
            self.feedback.emit(str(value))
