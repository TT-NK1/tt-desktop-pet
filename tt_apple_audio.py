"""Read and set a supported music client's Windows audio session volume."""
import ctypes
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
import time

from PySide6.QtCore import QObject, Signal


def apple_process(pid, process_class):
    """Validate a live process against the official Store package family."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.GetPackageFamilyName.argtypes = (wintypes.HANDLE,ctypes.POINTER(wintypes.UINT),wintypes.LPWSTR)
    handle = kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        return False
    try:
        length = wintypes.UINT(0)
        if kernel.GetPackageFamilyName(handle,ctypes.byref(length),None)!=122:
            return False
        name = ctypes.create_unicode_buffer(length.value)
        return (kernel.GetPackageFamilyName(handle,ctypes.byref(length),name)==0
                and name.value.casefold()=='appleinc.applemusicwin_nzyj5cx40ttqa'
                and process_class(pid).name().casefold() in ('applemusic.exe','amplibraryagent.exe'))
    finally:
        kernel.CloseHandle(handle)


def netease_process(pid, process_class):
    return Path(process_class(pid).exe()).name.casefold() == 'cloudmusic.exe'


class WindowsAppVolume(QObject):
    changed = Signal(object)
    observed = Signal(object)
    failed = Signal(str)
    session_event = Signal()

    def __init__(self, parent=None, *, label='Apple Music', matches_process=apple_process, allow_inactive=False):
        super().__init__(parent)
        self.label = label
        self.matches_process = matches_process
        self.allow_inactive = allow_inactive
        self.commands = Queue()
        self.worker = None
        self.value = None
        self.enabled = False
        self.events = 0
        self.observed.connect(self.apply)
        self.session_event.connect(self.count_event)

    def count_event(self):
        self.events += 1

    def start(self):
        if self.enabled:
            return
        self.enabled = True
        if self.worker is None:
            self.worker = Thread(target=self.run,name=f'TT {self.label} Core Audio',daemon=True)
            self.worker.start()
        else:
            self.commands.put(('resume',0))

    def apply(self, value):
        if self.value!=value:
            self.value = value
            self.changed.emit(value)

    def set(self, value):
        if self.enabled and self.value is not None:
            self.commands.put(('set',max(0,min(100,value))))

    def close(self):
        if self.worker:
            self.commands.put(('close',0))

    @staticmethod
    def publish(signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:
            pass  # A queued COM callback can arrive while Qt deletes the bubble.

    def stop(self):
        if self.enabled:
            self.enabled = False
            self.value = None
            self.commands.put(('idle',0))

    def run(self):
        try:
            # The bundled dependencies are imported on this worker. Qt widgets
            # only receive scalars, so COM pointers never cross into the UI.
            dependencies = str(Path(__file__).with_name('tt_audio_dependencies'))
            if Path(dependencies).is_dir() and dependencies not in sys.path:
                sys.path.append(dependencies)
            from ctypes import COMError
            import comtypes
            import psutil
            from pycaw.callbacks import AudioSessionEvents
            from pycaw.constants import DEVICE_STATE, EDataFlow
            from pycaw.pycaw import AudioUtilities, IAudioSessionControl2, IAudioSessionEvents, ISimpleAudioVolume
        except (ImportError,OSError):
            self.publish(self.failed, f'{self.label}应用音量组件不可用。')
            return

        queue = self.commands
        failures = (COMError,ctypes.ArgumentError,OSError,psutil.Error)

        class SessionEvents(AudioSessionEvents):
            _com_interfaces_ = (IAudioSessionEvents,)

            def on_simple_volume_changed(self, *_):
                queue.put(('changed',0))
                self.owner.publish(self.owner.session_event)

        def scan():
            active, inactive = {}, {}
            for device in AudioUtilities.GetAllDevices(EDataFlow.eRender.value,DEVICE_STATE.ACTIVE.value):
                try:
                    enumerator = device.AudioSessionManager.GetSessionEnumerator()
                    for index in range(enumerator.GetCount()):
                        try:
                            ctl = enumerator.GetSession(index).QueryInterface(IAudioSessionControl2)
                            state, pid = ctl.GetState(), ctl.GetProcessId()
                            if state in (0,1) and pid and self.matches_process(pid,psutil.Process):
                                entry = (ctl,ctl.QueryInterface(ISimpleAudioVolume),pid)
                                (active if state==1 else inactive)[ctl.GetSessionInstanceIdentifier()] = entry
                        except failures:
                            continue
                except failures:
                    continue
            retained = {key:inactive[key] for key in watched if key in inactive}
            unique_inactive = inactive if self.allow_inactive and len(inactive)==1 else {}
            return active or retained or unique_inactive

        watched = {}
        listener = SessionEvents()
        listener.owner = self
        callback = listener.QueryInterface(IAudioSessionEvents)
        next_scan = 0.0
        enabled = True
        while True:
            try:
                action,value = queue.get(timeout=max(0,next_scan-time.monotonic()) if enabled else None)
            except Empty:
                action,value = 'scan',None
            if action=='close':
                break
            if action=='idle':
                enabled = False
                available = {}
                for ctl,_,_ in watched.values():
                    try:
                        ctl.UnregisterAudioSessionNotification(callback)
                    except failures:
                        pass
                watched = available
                self.publish(self.observed, None)
            elif action=='resume':
                enabled = True
                next_scan = 0.0
            if not enabled:
                continue
            try:
                if time.monotonic()>=next_scan:
                    available = scan()
                    for key in watched.keys()-available.keys():
                        try:
                            watched[key][0].UnregisterAudioSessionNotification(callback)
                        except failures:
                            pass
                    for key in available.keys()-watched.keys():
                        try:
                            available[key][0].RegisterAudioSessionNotification(callback)
                        except failures:
                            pass
                    watched = {key:watched.get(key,entry) for key,entry in available.items()}
                    next_scan = time.monotonic()+1
                if action=='set':
                    if not watched:
                        self.publish(self.failed, f'没有检测到可控制的{self.label}应用音频会话。')
                    for _,volume,pid in watched.values():
                        if self.matches_process(pid,psutil.Process):
                            # Keep the previous scalar when muting. An external unmute
                            # restores that scalar, and a positive slider value unmutes.
                            if value:
                                volume.SetMasterVolume(value/100,None)
                            volume.SetMute(value==0,None)
                if watched:
                    volumes = [entry[1] for entry in watched.values()]
                    gains = [round(volume.GetMasterVolume()*100) for volume in volumes if not volume.GetMute()]
                    self.publish(self.observed, max(gains,default=0))
                else:
                    self.publish(self.observed, None)
            except failures:
                self.publish(self.observed, None)
                if action=='set':
                    self.publish(self.failed, f'{self.label}应用音量未能写入，请确认客户端正在播放。')
                next_scan = time.monotonic()+1
        for ctl,_,_ in watched.values():
            try:
                ctl.UnregisterAudioSessionNotification(callback)
            except failures:
                pass


class AppleAudioVolume(WindowsAppVolume):
    """Windows audio session bound to the Apple Music Store package."""
