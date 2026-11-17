"""Run IndexTTS in its own Python interpreter using private stdio pipes."""
import contextlib
import ctypes
import inspect
import io
import json
import os
import shutil
from queue import Queue
import subprocess
import sys
from threading import Event, Thread
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import soundfile as sf
from tt_indextts_resources import MIB, ResourceManager
from tt_natural_audio import compact_audio, join_audio
from tt_natural_speech import delivery_plan
from tt_speech_segments import NATIVE_TOKEN_LIMIT

FFMPEG = Path(os.environ.get("TT_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg")


def load_model(root, cache_directory=None, *, fp16=True, cuda_kernel=False, deepspeed=False):
    import torch
    from indextts import infer_v2
    from indextts.infer_v2 import IndexTTS2
    from tt_indextts_reference_cache import FEATURES, ReferenceCache

    if not torch.cuda.is_available():
        raise RuntimeError("IndexTTS 2.0 需要可用的 CUDA 显卡")
    torch.set_float32_matmul_precision("high")

    class DesktopIndexTTS(IndexTTS2):
        reference_token = None
        pending_cache_path = None

        def activate_reference(self, reference):
            stat = reference.stat()
            token = (reference, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if token == self.reference_token:
                return
            path = self.reference_cache.path_for(reference)
            tensors = self.reference_cache.read(path)
            for name in FEATURES:
                setattr(self, name, tensors[name].to(self.device) if tensors is not None else None)
            self.cache_spk_audio_prompt = self.cache_emo_audio_prompt = str(reference) if tensors is not None else None
            self.pending_cache_path = path if tensors is None else None
            self.reference_token = token
            print(f"IndexTTS reference cache: {'hit' if tensors is not None else 'miss'}", file=sys.stderr)

        def _reference_prepared(self):
            if self.semantic_model is not None:
                self.semantic_model = None
                self.campplus_model = None
                torch.cuda.empty_cache()
            if self.pending_cache_path is not None:
                self.reference_cache.write(self.pending_cache_path, self)
                self.pending_cache_path = None

    weights = root / "checkpoints"
    accepted = inspect.signature(IndexTTS2.__init__).parameters
    use_cache = {"eager_reference_encoder", "desktop_low_memory"}.issubset(accepted)
    optional = dict(use_fp16=fp16, use_cuda_kernel=cuda_kernel, use_deepspeed=deepspeed,
                    use_qwen_emo=False, use_accel=False, use_torch_compile=False)
    if use_cache:
        optional.update(eager_reference_encoder=False, desktop_low_memory=True)
    model_type = DesktopIndexTTS if use_cache else IndexTTS2
    model = model_type(cfg_path=str(weights / "config.yaml"), model_dir=str(weights),
                       **{name: value for name, value in optional.items() if name in accepted})
    model.desktop_reference_cache = use_cache
    if use_cache:
        model.reference_cache = ReferenceCache(cache_directory or root / "desktop-pet" / "reference-cache",
                                               model, torch, Path(infer_v2.__file__))
    return torch, model


class Cancelled(RuntimeError):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled


def cancellation_criteria(cancel):
    from transformers import StoppingCriteria, StoppingCriteriaList

    class CheckCancellation(StoppingCriteria):
        def __call__(self, input_ids, scores, **kwargs):
            check_cancel(cancel)
            return False
    return StoppingCriteriaList([CheckCancellation()])


def change_pace(pcm, rate, duration):
    result = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-nostdin",
         "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
         "-af", f"atempo={1/duration:.8f}", "-f", "s16le", "pipe:1"],
        input=pcm.tobytes(), stdout=subprocess.PIPE, check=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return np.frombuffer(result.stdout, dtype="<i2")


def render(model, text, options, cancel):
    reference, phrase = text
    options = dict(options)
    duration = options.pop("duration_factor")
    compact = options.pop("compact_followup", False)
    rate, pcm = model.infer(spk_audio_prompt=str(reference),text=phrase,output_path=None,
                            stopping_criteria=cancellation_criteria(cancel),**options)
    check_cancel(cancel)
    pcm = pcm.reshape(-1).astype("<i2",copy=False)
    if duration!=1:
        pcm = change_pace(pcm, rate, duration)
    audio = pcm.astype("float32")/32768
    return (compact_audio(audio,rate,FFMPEG) if compact else audio),rate


def synthesize(torch, model, request, cancel):
    reference = Path(request["reference"])
    if not reference.is_file():
        raise FileNotFoundError("参考音频缺失")
    chunks, pauses = delivery_plan(
        request["text"], request["style"], request["rate"], request["emotion_strength"],
        token_count=lambda phrase: len(model.tokenizer.tokenize(phrase)),
        token_limit=min(NATIVE_TOKEN_LIMIT, int(model.cfg.gpt.max_text_tokens)),
    )
    pause_scale = request.get("developer_pause_scale", 1)
    if pause_scale != 1:
        pauses = [round(ms * pause_scale) for ms in pauses]
    if not chunks:
        raise ValueError("没有可朗读的文字")
    with torch.inference_mode():
        if getattr(model, "desktop_reference_cache", False):
            model.activate_reference(reference)
        rendered = []
        for text,options in chunks:
            check_cancel(cancel)
            if request.get("fast_speech"):
                options["num_beams"] = 1
            audio,rate = render(model,(reference,text),options,cancel)
            rendered.append(audio)
            if request.get("developer_trace"):
                tokens = model.tokenizer.tokenize(text)
                record = {"text": text, "characters": len(text), "tokens": len(tokens),
                          "model_pieces": len(model.tokenizer.split_segments(tokens)),
                          "seconds": round(audio.size / rate, 3)}
                print("TT_SEGMENT_DEBUG " + json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)
        if len(rendered)>1:
            measurements = [] if request.get("developer_trace") else None
            audio = join_audio(rendered,[t for t,_ in chunks],rate,pauses,
                               tight_joins={i for i,(_,o) in enumerate(chunks) if i and o.get("compact_followup")},
                               pause_metrics=measurements)
            if measurements is not None:
                print("TT_SEGMENT_PAUSES " + json.dumps(measurements), file=sys.stderr, flush=True)
        if not audio.size or not np.isfinite(audio).all():
            raise ValueError("生成的音频为空或无效")
        with io.BytesIO() as data:
            sf.write(data,audio,rate,format="WAV",subtype="PCM_16")
            return data.getvalue()


def request_lines():
    if os.name!="nt":
        yield from sys.stdin
        return
    # Qt passes a Windows pipe. Read outside the CRT stream so the input thread
    # cannot hold the stdin lock while a native dependency initializes stdio.
    import msvcrt
    from ctypes import wintypes
    read_file = ctypes.windll.kernel32.ReadFile
    read_file.argtypes = (wintypes.HANDLE,wintypes.LPVOID,wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID)
    pipe = msvcrt.get_osfhandle(sys.stdin.fileno())
    chunk = ctypes.create_string_buffer(8192)
    received = wintypes.DWORD()
    pending = b""
    while read_file(pipe,chunk,len(chunk),ctypes.byref(received),None) and received.value:
        lines = (pending+chunk.raw[:received.value]).split(b"\n")
        pending = lines.pop()
        yield from lines
    if pending:
        yield pending


def read_requests(inbox, waiting):
    for line in request_lines():
        try:
            request = json.loads(line)
            if "cancel" in request:
                signal = waiting.get(request["cancel"])
                if signal:
                    signal.set()
            else:
                signal = Event()
                if "id" in request:
                    waiting[request["id"]] = signal
                inbox.put((request,signal))
        except (ValueError, TypeError):
            continue
    inbox.put(None)


def send(response, data=b""):
    response["bytes"] = len(data)
    stream = sys.stdout.buffer
    stream.write(("INDEXTTS_REPLY "+json.dumps(response)+"\n").encode("ascii"))
    if data:
        stream.write(data)
    stream.flush()


def main(root, memory_pipe, qwen_runtime=None):
    sys.path.insert(0, str(root / "official-source"))
    sys.path.insert(0, str(root))
    from indextts_gpu_slot import GpuSlotBusy, claim_gpu

    resources = ResourceManager()
    inbox,waiting = Queue(),{}
    if memory_pipe:
        send({"worker_pid":os.getpid()})
    Thread(target=read_requests,args=(inbox,waiting),daemon=True).start()
    model, budget, slot = None,None,None
    while (item := inbox.get()) is not None:
        request,cancel = item
        ident = request.get("id")
        warmup = request.get("warmup",False)
        data = b""
        response = {"id":ident,"warmup":warmup}
        try:
            check_cancel(cancel)
            if request.get("resources"):
                if model is not None:
                    response["free_mib"] = int(torch.cuda.mem_get_info()[0]/MIB)
                send(dict(response,ok=True,resources=True))
                continue
            current = resources.inference_budget()
            if budget!=current and os.name=="nt":
                priority = 0x4000 if current[1] else 0x0020
                ctypes.windll.kernel32.SetPriorityClass(ctypes.c_void_p(-1),priority)
            if model is None:
                started = time.monotonic()
                slot = claim_gpu(root)
                print("TT loading local voice model",file=sys.stderr,flush=True)
                try:
                    with contextlib.redirect_stdout(sys.stderr):
                        import torch
                        torch.set_num_threads(current[0])
                        if qwen_runtime is None:
                            torch, model = load_model(root)
                        else:
                            from tt_qwen_voice import ForeignVoice
                            model = ForeignVoice(qwen_runtime)
                except Exception:
                    slot.close()
                    slot = None
                    raise
                print(f"TT voice ready in {time.monotonic() - started:.2f}s", file=sys.stderr, flush=True)
                if memory_pipe:
                    send(dict(model_ready=True, free_mib=int(torch.cuda.mem_get_info()[0] / MIB)))
            if budget!=current:
                torch.set_num_threads(current[0])
                budget = current
            check_cancel(cancel)
            if warmup:
                reference = request.get("reference")
                if (qwen_runtime is None and reference and Path(reference).is_file()
                        and getattr(model, "desktop_reference_cache", False)):
                    with torch.inference_mode():
                        model.activate_reference(Path(reference))
            else:
                with open(os.devnull,"w",encoding="utf-8") as quiet, contextlib.redirect_stdout(quiet):
                    if qwen_runtime is None:
                        data = synthesize(torch,model,request,cancel)
                    else:
                        from tt_qwen_voice import to_wave
                        with torch.inference_mode():
                            audio, sample_rate = model.render(request, cancellation_criteria(cancel))
                        if rate_adjustment := max(-100, min(100, int(request.get("rate", 0)))):
                            pcm = np.clip(audio.reshape(-1) * 32768, -32768, 32767).astype("<i2")
                            audio = change_pace(pcm, sample_rate, 1 / (1 + rate_adjustment / 200))
                        data = to_wave(audio, sample_rate)
                check_cancel(cancel)
                if not memory_pipe:
                    with Path(request["output"]).open("xb") as output:
                        output.write(data)
                    data = b""
            response.update(ok=True,free_mib=int(torch.cuda.mem_get_info()[0]/MIB))
        except Cancelled:
            response.update(ok=False,cancelled=True)
            data = b""
        except GpuSlotBusy:
            response.update(ok=False,busy=True)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            reason = ("参考音频或本地推理文件缺失" if isinstance(error,FileNotFoundError) else
                      "调整语速时音频处理失败" if isinstance(error,subprocess.SubprocessError) else
                      "显存不足，请释放显卡占用后再试" if "out of memory" in str(error).lower() else
                      f"合成失败（{type(error).__name__}），可查看语音日志")
            response.update(ok=False,reason=reason,out_of_memory="out of memory" in str(error).lower())
            data = b""
        finally:
            if ident:
                waiting.pop(ident,None)
        if memory_pipe or not warmup:
            send(response,data)


if __name__ == "__main__":
    arguments = sys.argv[2:]
    qwen = Path(arguments[arguments.index("--qwen") + 1]).resolve() if "--qwen" in arguments else None
    main(Path(sys.argv[1]).resolve(), "--memory-pipe" in arguments, qwen)
