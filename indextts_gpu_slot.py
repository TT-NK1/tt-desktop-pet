"""Prevent the desktop pet and local WebUI from loading duplicate GPU models."""
import errno
import msvcrt


class GpuSlotBusy(RuntimeError):
    pass


def claim_gpu(root):
    slot = (root / ".gpu-model.lock").open("a+b")
    try:
        slot.seek(0)
        msvcrt.locking(slot.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as error:
        slot.close()
        if error.errno == errno.EACCES:
            raise GpuSlotBusy("IndexTTS 2.0 is already running in the desktop pet or WebUI.") from None
        raise
    return slot
