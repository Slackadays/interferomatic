"""CsTestQt-style Gage wait / start / transfer, with an optional C backend.

The Razor analog front-end clicks relays on ACTION_COMMIT, ACTION_ABORT, and
during on-board calibration (ACQ_STATUS_BUSY_CALIB). This module:

* waits through calibration without Abort/Force/Commit
* polls GetStatus at 10 ms (CsTestQt), not 100 µs
* uses libgage_acq.so (CsDo / CsTransfer / event-fd poll) when built, else
  falls back to PyGage

Never import this from the Dear PyGui process — the driver installs a POSIX
signal handler that is not async-signal-safe.
"""

from __future__ import annotations

import ctypes
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

# Mirror CsDefines.h / GageConstants.py
ACQ_STATUS_READY = 0
ACQ_STATUS_WAIT_TRIGGER = 1
ACQ_STATUS_TRIGGERED = 2
ACQ_STATUS_BUSY_TX = 3
ACQ_STATUS_BUSY_CALIB = 4

CS_TIMEOUT_DISABLE = -1
CS_MODE_POWER_ON = 0x80
CS_INVALID_HANDLE = -6

GAGE_ACQ_READY = 0
GAGE_ACQ_STOPPED = 1
GAGE_ACQ_TIMEOUT = 2

RESULT_READY = "ready"
RESULT_STOPPED = "stopped"
RESULT_TIMEOUT = "timeout"

# CsTestQt WaitForReady / WaitForTrigger sleep 10 ms between GetStatus calls.
STATUS_POLL_S = 0.01
# Front-end calibration (relay sequence) can take many seconds. CsTestQt
# ForceCalib sleeps 10 s; give Commit/calib a full minute.
CALIB_TIMEOUT_S = 60.0
# After ACTION_ABORT, CsTestQt sleeps 1 s for the analog path to settle.
ABORT_SETTLE_S = 1.0
# Slice length so the child can drain halt/stop commands during a wait.
WAIT_SLICE_S = 0.05

TRANSIENT_STATUSES = frozenset(
    {
        ACQ_STATUS_WAIT_TRIGGER,
        ACQ_STATUS_TRIGGERED,
        ACQ_STATUS_BUSY_TX,
        ACQ_STATUS_BUSY_CALIB,
    }
)

_LIB_NAME = "libgage_acq.so"
_LIB_PATH = Path(__file__).resolve().parent / _LIB_NAME


def status_name(status: int) -> str:
    names = {
        ACQ_STATUS_READY: "ready",
        ACQ_STATUS_WAIT_TRIGGER: "wait_trigger",
        ACQ_STATUS_TRIGGERED: "triggered",
        ACQ_STATUS_BUSY_TX: "busy_tx",
        ACQ_STATUS_BUSY_CALIB: "calibrating",
    }
    if status < 0:
        return f"error({status})"
    return names.get(int(status), f"status({status})")


def board_is_calibrating(status: int) -> bool:
    return int(status) == ACQ_STATUS_BUSY_CALIB


def board_is_acquiring(status: int) -> bool:
    return int(status) in (
        ACQ_STATUS_WAIT_TRIGGER,
        ACQ_STATUS_TRIGGERED,
        ACQ_STATUS_BUSY_TX,
    )


def can_start_capture(status: int) -> bool:
    """True only when StartCapture will not Abort an in-flight shot or calib."""
    return int(status) == ACQ_STATUS_READY


def wait_until_ready(
    get_status: Callable[[], int],
    *,
    timeout_s: Optional[float],
    poll_s: float = STATUS_POLL_S,
    stop_check: Optional[Callable[[], bool]] = None,
    on_status: Optional[Callable[[int], None]] = None,
    calib_timeout_s: float = CALIB_TIMEOUT_S,
    pause_on_calib: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> str:
    """Wait for ACQ_STATUS_READY without Abort or Force.

    When *pause_on_calib* is true, BUSY_CALIB pauses the shot clock so
    relay-clicking calibration cannot look like a missed trigger.
    ``timeout_s is None`` waits forever (aside from *calib_timeout_s*).
    Returns ``ready``, ``stopped``, or ``timeout``.
    Negative GetStatus values raise RuntimeError with the raw code.
    """
    shot_deadline = None if timeout_s is None else now() + float(timeout_s)
    calib_started: Optional[float] = None
    poll = max(0.001, float(poll_s))

    while True:
        if stop_check is not None and stop_check():
            return RESULT_STOPPED
        status = int(get_status())
        if status < 0:
            raise RuntimeError(status)
        if on_status is not None:
            on_status(status)
        if status == ACQ_STATUS_READY:
            return RESULT_READY
        t = now()
        if status == ACQ_STATUS_BUSY_CALIB:
            if calib_started is None:
                calib_started = t
            elif t - calib_started >= float(calib_timeout_s):
                return RESULT_TIMEOUT
            if not pause_on_calib and shot_deadline is not None and t >= shot_deadline:
                return RESULT_TIMEOUT
        else:
            calib_started = None
            if shot_deadline is not None and t >= shot_deadline:
                return RESULT_TIMEOUT
        sleep(poll)


class GageAcq:
    """Start / wait / transfer / abort. C shim when present, else PyGage."""

    def __init__(self):
        self._lib = _load_c_lib()
        self._pygage = None
        self._xfer_bufs = {}
        if self._lib is None:
            import PyGage

            self._pygage = PyGage

    @property
    def using_c_shim(self) -> bool:
        return self._lib is not None

    def error_string(self, code: int) -> str:
        if self._lib is not None:
            buf = ctypes.create_string_buffer(256)
            self._lib.gage_acq_error_string(int(code), buf, 256)
            text = buf.value.decode("utf-8", errors="replace").strip()
            if text:
                return text
        if self._pygage is not None:
            try:
                return str(self._pygage.GetErrorString(int(code)))
            except Exception:
                pass
        return f"Gage error {int(code)}"

    def status(self, handle: int) -> int:
        if self._lib is not None:
            return int(self._lib.gage_acq_status(ctypes.c_uint32(handle)))
        return int(self._pygage.GetStatus(handle))

    def start(self, handle: int) -> int:
        if self._lib is not None:
            return int(self._lib.gage_acq_start(ctypes.c_uint32(handle)))
        return int(self._pygage.StartCapture(handle))

    def abort(self, handle: int) -> int:
        if self._lib is not None:
            return int(self._lib.gage_acq_abort(ctypes.c_uint32(handle)))
        return int(self._pygage.AbortCapture(handle))

    def force(self, handle: int) -> int:
        if self._lib is not None:
            return int(self._lib.gage_acq_force(ctypes.c_uint32(handle)))
        return int(self._pygage.ForceCapture(handle))

    def abort_and_settle(self, handle: int) -> None:
        try:
            self.abort(handle)
        except Exception:
            pass
        time.sleep(ABORT_SETTLE_S)

    def wait_ready(
        self,
        handle: int,
        timeout_s: Optional[float],
        stop_check: Optional[Callable[[], bool]] = None,
        on_status: Optional[Callable[[int], None]] = None,
        pause_on_calib: bool = True,
    ) -> str:
        if (
            self._lib is not None
            and stop_check is None
            and on_status is None
            and pause_on_calib
        ):
            timeout_ms = -1 if timeout_s is None else max(0, int(float(timeout_s) * 1000.0))
            out = ctypes.c_int(0)
            rc = int(
                self._lib.gage_acq_wait_ready(
                    ctypes.c_uint32(handle),
                    ctypes.c_int(timeout_ms),
                    None,
                    ctypes.byref(out),
                )
            )
            if rc < 0:
                raise RuntimeError(self.error_string(rc))
            if rc == GAGE_ACQ_READY:
                return RESULT_READY
            if rc == GAGE_ACQ_STOPPED:
                return RESULT_STOPPED
            return RESULT_TIMEOUT

        def get_status() -> int:
            st = self.status(handle)
            if st < 0:
                raise RuntimeError(self.error_string(st))
            return st

        return wait_until_ready(
            get_status,
            timeout_s=timeout_s,
            stop_check=stop_check,
            on_status=on_status,
            pause_on_calib=pause_on_calib,
        )

    def transfer(
        self,
        handle: int,
        channel: int,
        start: int,
        length: int,
        *,
        mode: int = 0,
        segment: int = 1,
    ) -> Tuple[object, int, int]:
        """DMA one channel into a reused int16 buffer.

        Returns ``(buffer, actual_start, actual_length)``. *buffer* is a numpy
        ndarray view of the first *actual_length* samples (or the PyGage
        array on the fallback path).
        """
        import numpy as np

        length = int(length)
        if self._lib is not None:
            buf = self._xfer_bufs.get(int(channel))
            if buf is None or buf.size < length:
                buf = np.empty(length, dtype=np.int16)
                self._xfer_bufs[int(channel)] = buf
            actual_start = ctypes.c_int64(0)
            actual_length = ctypes.c_int64(0)
            rc = int(
                self._lib.gage_acq_transfer(
                    ctypes.c_uint32(handle),
                    ctypes.c_uint16(channel),
                    ctypes.c_uint32(mode),
                    ctypes.c_uint32(segment),
                    ctypes.c_int64(start),
                    ctypes.c_int64(length),
                    buf.ctypes.data_as(ctypes.c_void_p),
                    ctypes.byref(actual_start),
                    ctypes.byref(actual_length),
                )
            )
            if rc < 0:
                raise RuntimeError(self.error_string(rc))
            n = int(actual_length.value)
            if n < 0:
                n = 0
            if n > length:
                n = length
            return buf[:n], int(actual_start.value), n

        transferred = self._pygage.TransferData(
            handle, int(channel), int(mode), int(segment), int(start), int(length)
        )
        if isinstance(transferred, int):
            raise RuntimeError(self.error_string(transferred))
        buf, actual_start, actual_length = transferred
        return buf, int(actual_start), int(actual_length)


def _load_c_lib():
    if not _LIB_PATH.is_file():
        return None
    try:
        lib = ctypes.CDLL(str(_LIB_PATH))
    except OSError:
        return None
    lib.gage_acq_status.argtypes = [ctypes.c_uint32]
    lib.gage_acq_status.restype = ctypes.c_int
    lib.gage_acq_start.argtypes = [ctypes.c_uint32]
    lib.gage_acq_start.restype = ctypes.c_int
    lib.gage_acq_abort.argtypes = [ctypes.c_uint32]
    lib.gage_acq_abort.restype = ctypes.c_int
    lib.gage_acq_force.argtypes = [ctypes.c_uint32]
    lib.gage_acq_force.restype = ctypes.c_int
    lib.gage_acq_wait_ready.argtypes = [
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.gage_acq_wait_ready.restype = ctypes.c_int
    lib.gage_acq_transfer.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint16,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int64),
        ctypes.POINTER(ctypes.c_int64),
    ]
    lib.gage_acq_transfer.restype = ctypes.c_int
    lib.gage_acq_error_string.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    lib.gage_acq_error_string.restype = ctypes.c_int
    return lib
