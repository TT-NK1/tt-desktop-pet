"""Local resource policy and bounded cache for the one IndexTTS 2.0 worker."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

ROOT = Path(os.environ.get("TT_INDEXTTS_ROOT", Path.home() / "IndexTTS-2.0"))
RUNTIME = ROOT / "desktop-pet"
CONFIG = RUNTIME / "voice-runtime.json"
FIXED_CACHE = RUNTIME / "cache" / "indextts" / "fixed"
MODES = ("low", "balanced", "idle")
MIB = 2**20
FIXED = frozenset(("欢迎回来", "我在这里哦", "今天辛苦啦", "记得带伞", "正在思考",
                   "有事随时叫我哦", "出门记得带伞哦"))
FILENAME = re.compile(r"[a-f0-9]{64}\.wav\Z")


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
        (name, ctypes.c_uint64) for name in ("physical", "available", "page", "page_available",
            "virtual", "virtual_available", "extended")]


class LastInput(ctypes.Structure):
    _fields_ = [("size", wintypes.UINT), ("tick", wintypes.DWORD)]


def atomic_write(path, data):
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def mode():
    try:
        value = json.loads(CONFIG.read_text(encoding="utf-8")).get("mode")
        return value if value in MODES else "balanced"
    except (OSError, ValueError, AttributeError):
        return "balanced"


class ResourceManager:
    def __init__(self):
        self.times = None

    @staticmethod
    def memory_headroom():
        if not hasattr(ctypes, "windll"):
            return float("inf")
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            return float("inf")
        return min(memory.available, memory.page_available) / MIB

    def sample(self):
        if not hasattr(ctypes, "windll"):
            return float("inf"), 0., 0.
        kernel = ctypes.windll.kernel32
        available = self.memory_headroom()
        info = LastInput(ctypes.sizeof(LastInput), 0)
        idle = ((kernel.GetTickCount()-info.tick) & 0xffffffff)/1000 if (
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))) else 0.
        times = [ctypes.c_uint64() for _ in range(3)]
        load = 0.
        if kernel.GetSystemTimes(*(ctypes.byref(t) for t in times)):
            counters = tuple(t.value for t in times)
            if self.times:
                total = counters[1]+counters[2]-self.times[1]-self.times[2]
                if total > 0:
                    load = 1-(counters[0]-self.times[0])/total
            self.times = counters
        return available, idle, load

    def allow_warmup(self):
        available, idle, load = self.sample()
        selected = mode()
        return available >= 3072 and load < .80 and (
            selected == "balanced" or selected == "idle" and idle >= 90)

    def inference_budget(self):
        available, idle, load = self.sample()
        selected = mode()
        if selected == "low" or available < 3072 or load >= .80:
            return 2, True
        if selected == "idle" and idle >= 90 and load < .45:
            return 4, False
        return 3, True

    def retire(self, idle_seconds, free_vram=None):
        available, _, _ = self.sample()
        selected = mode()
        limit = 60 if selected == 'low' else 120
        if selected == 'balanced' and available >= 4096 and free_vram is not None and free_vram >= 1400:
            limit = 300
        return (idle_seconds>=limit
                or (idle_seconds>=10 and available<2560)
                or (idle_seconds>=20 and free_vram is not None and free_vram<900))


class FixedAudioCache:
    def __init__(self, directory):
        self.directory = directory
        self.identity = None
        self.signature = None

    @staticmethod
    def file_state(path):
        stat = path.stat()
        return str(path),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns

    def key(self, reference, text, options):
        if text.strip().rstrip("。！？!?") not in FIXED:
            return None
        weights = ROOT/"checkpoints"
        sources = (weights/"config.yaml",weights/"gpt.pth",weights/"s2mel.pth",
                   ROOT/"official-source/indextts/infer_v2.py",
                   ROOT/"official-source/indextts/gpt/model_v2.py",
                   weights/"hf_cache/bigvgan/config.json",
                   Path(__file__).with_name("tt_indextts_worker.py"),
                   Path(__file__).with_name("tt_natural_speech.py"),
                   Path(__file__).with_name("tt_natural_audio.py"),
                   Path(__file__).with_name("tt_speech_segments.py"),
                   Path(__file__).with_name("tt_speech_wave.py"))
        try:
            identity = tuple(self.file_state(path) for path in (*sources,Path(reference)))
            if identity != self.identity:
                digest = hashlib.sha256(repr(("IndexTTS 2.0",identity)).encode())
                with Path(reference).open("rb") as handle:
                    digest.update(hashlib.file_digest(handle,"sha256").digest())
                self.identity,self.signature = identity,digest
            digest = self.signature.copy()
            digest.update(json.dumps((text,options),ensure_ascii=False,sort_keys=True).encode())
            return digest.hexdigest()
        except OSError:
            return None

    def read(self, key):
        if not key:
            return None
        path = self.directory/(key+".wav")
        try:
            stat = path.stat()
            if not 44<stat.st_size<=MIB or time.time()-stat.st_mtime>45*86400:
                return None
            data = path.read_bytes()
            if data[:4]==b"RIFF" and data[8:12]==b"WAVE":
                os.utime(path,None)
                return data
        except OSError:
            pass
        return None

    def put(self, key, data):
        if key and len(data)<=MIB:
            try:
                atomic_write(self.directory/(key+".wav"),data)
                files = sorted(((p.stat().st_mtime,p) for p in self.directory.glob("*.wav")
                                if FILENAME.fullmatch(p.name)),reverse=True)
                for index,(updated,path) in enumerate(files):
                    if index>=32 or time.time()-updated>45*86400:
                        path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__=="__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TT 本地 IndexTTS 2.0 资源与固定短句缓存")
    parser.add_argument("action",nargs="?",choices=(*MODES,"status","clear","clear-reference"))
    action = parser.parse_args().action
    if action is None:
        print("当前语音资源模式",mode())
        print("1 低占用  2 平衡  3 空闲加速  4 清理固定短句  5 清理参考特征  0 退出")
        action = {"1":"low","2":"balanced","3":"idle","4":"clear","5":"clear-reference"}.get(input("请选择 ").strip())
    if action in ("clear","clear-reference"):
        directory,ext = (FIXED_CACHE,".wav") if action=="clear" else (RUNTIME/"reference-cache",".safetensors")
        paths = [p for p in directory.glob("*"+ext)
                 if re.fullmatch(r"[a-f0-9]{64}",p.stem)]
        for path in paths:
            path.unlink(missing_ok=True)
        print(f"已清理 {len(paths)} 个缓存文件。")
    elif action=="status":
        print("当前语音资源模式",mode())
    elif action in MODES:
        atomic_write(CONFIG,json.dumps({"mode":action}).encode())
        print("已保存语音资源模式",action)
