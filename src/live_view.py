"""Live View / average-mode acquisition using the Gage CompuScope API.

Architecture
------------
All PyGage / driver calls run in a **child process** started with the ``spawn``
context. Dear PyGui (and its OpenGL threads) stay in the parent.

Why: on Linux the Gage driver delivers hardware events via a POSIX signal
handler (``HWEventHandler``) that is not async-signal-safe. Continuous
single-shot capture in the same process as DPG races with that handler and
either SIGSEGVs in ``CWinEventHandle::signal`` or leaves the SSM stuck so
``GetStatus`` never returns READY. Isolating the driver in its own process
matches the reliability of headless capture and keeps the UI alive even if
the driver process is restarted after a fault.

High-rate averaging (2 MSa, 40+ Hz)
-----------------------------------
The child keeps numpy arrays (never Python lists), aligns a 64k-sample
window rather than the full record, and publishes min/max-downsampled
plots. Each new ``StartCapture`` is armed *before* the previous record is
aligned so processing overlaps the board's next sample window — the same
overlap CsTestQt / LabVIEW use to hide host work inside acquisition time.

The comb-aligned FFT is **not** on this path. Full traces are written to
shared memory; a background thread in the UI process transforms them on
its own clock so a 2–4 MSa rFFT cannot stall the next shot.

Relay-click pauses
------------------
Commit, Abort, and on-board calibration all toggle the Razor analog
front-end relays. The capture loop therefore:

* waits through ``ACQ_STATUS_BUSY_CALIB`` without Abort/Force/Commit
* uses a 2 s hardware trigger timeout while averaging (long vs Δf_rep,
  short vs a WAIT_TRIGGER that hides calibration on Linux)
* heartbeats while waiting so the UI does not recycle the child (which
  would re-Commit and wipe the running average)
* prefers a C shim (``libgage_acq.so``) that polls at 10 ms like CsTestQt
  instead of 100 µs GetStatus from Python
"""

from __future__ import annotations

import atexit
import math
import multiprocessing as mp
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict

import numpy as np

from src.average_shm import SharedAverageBuffer
from src.averaging import (
    AverageResult,
    InterferogramAverager,
    downsample_minmax,
)
from src.config import MAX_LIVE_SAMPLES
from src.gage_shim import (
    ACQ_STATUS_BUSY_CALIB,
    ACQ_STATUS_READY,
    CALIB_TIMEOUT_S,
    RESULT_READY,
    RESULT_STOPPED,
    WAIT_SLICE_S,
    GageAcq,
    board_is_calibrating,
    can_start_capture,
    status_name,
)
from src.spectrum import compute_rf_spectrum
from src.trace_shm import SharedTraceBuffer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAGE_API_DIR = PROJECT_ROOT / "gage_api"
if str(GAGE_API_DIR) not in sys.path:
    sys.path.insert(0, str(GAGE_API_DIR))

DEFAULT_INI = Path(__file__).resolve().parent / "Acquire.ini"


class TriggerSettings(TypedDict, total=False):
    """UI-facing trigger options applied on Live View configure/Commit."""

    source: str  # "Channel 1" | "External"
    edge: str  # "Rising" | "Falling"
    level: int  # percent of full scale (Gage Level; UI uses 0…100)
    ext_coupling: str  # "AC" | "DC"
    ext_range_mv: int  # ExtRange, mV peak-to-peak
    ext_impedance: str  # "50 Ohms" | "High Z"


DEFAULT_TRIGGER_SETTINGS: TriggerSettings = {
    "source": "Channel 1",
    "edge": "Rising",
    "level": 0,
    "ext_coupling": "DC",
    "ext_range_mv": 2000,
    "ext_impedance": "High Z",
}


def normalize_trigger_settings(
    trigger: Optional[Dict[str, Any]] = None,
) -> TriggerSettings:
    """Sanitize trigger options into a stable dict for configure/IPC."""
    t = trigger if isinstance(trigger, dict) else {}
    source = str(t.get("source", DEFAULT_TRIGGER_SETTINGS["source"]))
    if source not in ("Channel 1", "External"):
        source = DEFAULT_TRIGGER_SETTINGS["source"]
    edge = str(t.get("edge", DEFAULT_TRIGGER_SETTINGS["edge"]))
    if edge not in ("Rising", "Falling"):
        edge = DEFAULT_TRIGGER_SETTINGS["edge"]
    try:
        level = int(t.get("level", DEFAULT_TRIGGER_SETTINGS["level"]))
    except (TypeError, ValueError):
        level = DEFAULT_TRIGGER_SETTINGS["level"]
    # Gage Level is −100…+100; clamp to that full range.
    level = max(-100, min(100, level))
    ext_coupling = str(
        t.get("ext_coupling", DEFAULT_TRIGGER_SETTINGS["ext_coupling"])
    )
    if ext_coupling not in ("AC", "DC"):
        ext_coupling = DEFAULT_TRIGGER_SETTINGS["ext_coupling"]
    try:
        ext_range_mv = int(
            t.get("ext_range_mv", DEFAULT_TRIGGER_SETTINGS["ext_range_mv"])
        )
    except (TypeError, ValueError):
        ext_range_mv = DEFAULT_TRIGGER_SETTINGS["ext_range_mv"]
    if ext_range_mv < 1:
        ext_range_mv = DEFAULT_TRIGGER_SETTINGS["ext_range_mv"]
    ext_impedance = str(
        t.get("ext_impedance", DEFAULT_TRIGGER_SETTINGS["ext_impedance"])
    )
    if ext_impedance not in ("50 Ohms", "High Z"):
        ext_impedance = DEFAULT_TRIGGER_SETTINGS["ext_impedance"]
    return {
        "source": source,
        "edge": edge,
        "level": level,
        "ext_coupling": ext_coupling,
        "ext_range_mv": ext_range_mv,
        "ext_impedance": ext_impedance,
    }


def trigger_settings_key(trigger: TriggerSettings) -> Tuple:
    """Hashable key for config-equality checks."""
    return (
        trigger["source"],
        trigger["edge"],
        trigger["level"],
        trigger["ext_coupling"],
        trigger["ext_range_mv"],
        trigger["ext_impedance"],
    )


def _format_trigger_summary(trigger: TriggerSettings, trig_source: int) -> str:
    if trigger["source"] == "External" or trig_source == -1:
        src = "EXT"
    else:
        src = f"CH{trig_source}"
    edge = "rising" if trigger["edge"] == "Rising" else "falling"
    return f"{src} {edge}@{trigger['level']}%"

# Acquisition trigger timeout (100 ns units). 100 ms = 1_000_000 * 100 ns.
# Monitor: short timeout so Live View still refreshes on a quiet input.
# Average: a few seconds — long vs Δf_rep (~22 ms at 45.84 Hz) so real
# interferograms always win, short vs a stuck WAIT_TRIGGER. On Linux,
# CsGetStatus while the SSM is in WAIT_TRIGGER never reports BUSY_CALIB,
# so an infinite timeout hangs the run the next time the analog front-end
# calibrates (relay click, typically after a few minutes).
LIVE_TRIGGER_TIMEOUT = 1_000_000
AVERAGE_TRIGGER_TIMEOUT = 20_000_000  # 2 s

# If the hardware timeout did not produce READY, Force once, then give up
# this shot. ACTION_FORCE does not click relays; Abort/Commit do. Forced
# records are rejected by the correlator (low r).
CAPTURE_WAIT_TIMEOUT_S = 2.0
AVERAGE_WAIT_TIMEOUT_S = 3.0
STATUS_POLL_INTERVAL_S = 0.01
# Fallback minimum time between capture attempts when no max rate is configured.
WORKER_MIN_FRAME_INTERVAL_S = 0.0
# Commit runs on-board calibration (relay sequence). CsTestQt ForceCalib
# sleeps 10 s; wait a full minute before declaring the board stuck.
POST_COMMIT_READY_TIMEOUT_S = 60.0
HEARTBEAT_INTERVAL_S = 0.5


def max_capture_rate_to_interval_s(max_hz: int) -> float:
    """Convert a max capture rate (Hz) to a minimum inter-capture interval."""
    try:
        hz = int(max_hz)
    except (TypeError, ValueError):
        return WORKER_MIN_FRAME_INTERVAL_S
    if hz < 1:
        return WORKER_MIN_FRAME_INTERVAL_S
    return 1.0 / float(hz)

# How many consecutive child failures before surfacing an error to the UI.
# A successful capture after a restart clears the counter (see capture()).
MAX_CHILD_RESTARTS = 8

# Periodic AbortCapture was used to clear driver state, but on CSE1642 it
# toggles the on-board input relay every cycle (~few seconds) and is audible.
# Prefer recovering only when the child actually faults (SIGSEGV / stall).
# Set to 0 to disable periodic Abort entirely.
SOFT_RESET_EVERY_N_FRAMES = 0

# CSE1642 reports CAPS_DEPTH_INCREMENT=32; use that as the default alignment
# so UI values commit cleanly before the child queries the real cap.
# Exported as LIVE_DEPTH_INCREMENT for the UI spinner step (must match).
_DEFAULT_DEPTH_INCREMENT = 32
LIVE_DEPTH_INCREMENT = _DEFAULT_DEPTH_INCREMENT

# Dear PyGui / ImPlot cannot draw 2 MSa traces at 40 Hz. Min/max-downsample
# to this many points for the time plot; stride-downsample spectra similarly.
PLOT_MAX_POINTS = 8192
# How often the child publishes a plot/spectrum snapshot (capture continues).
PLOT_PUBLISH_INTERVAL_S = 0.05
# FFT runs on a side thread / in the UI process. Keep this slower than
# the capture loop — a 2–4 MSa comb-aligned rFFT can take hundreds of ms.
SPECTRUM_PUBLISH_INTERVAL_S = 0.50

# Channel -> (x samples, y volts) as numpy arrays (lists accepted on input).
ChannelData = Dict[int, Tuple[np.ndarray, np.ndarray]]
# Full-resolution RF spectrum from the child (no optical mapping, no stride).
# {"n": time-domain length, "sample_rate": Hz, "mag": float32 rFFT magnitude}
SpectrumData = Dict[str, Any]


def _ensure_project_path() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def downsample_channel_data(
    data: ChannelData, max_points: int = PLOT_MAX_POINTS
) -> ChannelData:
    """Min/max-downsample every channel for plotting / IPC."""
    out: ChannelData = {}
    for ch, (x, y) in data.items():
        out[int(ch)] = downsample_minmax(x, y, max_points)
    return out


def _lite_average_dict(result: AverageResult) -> dict:
    return {
        "accepted": int(result.accepted),
        "rejected": int(result.rejected),
        "target": int(result.target),
        "last_peak_corr": float(result.last_peak_corr),
        "last_lag": int(result.last_lag),
        "complete": bool(result.complete),
        "eta_seconds": result.eta_seconds,
    }


def _average_from_lite(payload: dict) -> AverageResult:
    return AverageResult(
        accepted=int(payload.get("accepted", 0)),
        rejected=int(payload.get("rejected", 0)),
        target=int(payload.get("target", 0)),
        last_peak_corr=float(payload.get("last_peak_corr", 0.0)),
        last_lag=int(payload.get("last_lag", 0)),
        complete=bool(payload.get("complete", False)),
        eta_seconds=payload.get("eta_seconds"),
    )


def _compute_spectrum_payload(
    y: np.ndarray,
    sample_rate: float,
    spectrum: Optional[dict],
) -> Optional[SpectrumData]:
    """FFT *y* at full length. Optical mapping happens in the UI process.

    Stride-downsampling here would throw away the fine spectral bins that
    a long interferogram is captured to provide.
    """
    if not spectrum or y is None or np.asarray(y).size < 4:
        return None
    try:
        d_frep = 0.0
        try:
            d_frep = float(spectrum.get("d_frep_hz", 0.0) or 0.0)
        except (TypeError, ValueError):
            d_frep = 0.0
        apodization = spectrum.get("apodization")
        try:
            zpd_index = spectrum.get("zpd_index")
            zpd_index = int(zpd_index) if zpd_index is not None else None
        except (TypeError, ValueError):
            zpd_index = None
        _frf, mag, n = compute_rf_spectrum(
            y,
            float(sample_rate),
            d_frep_hz=d_frep,
            apodization=apodization or "Boxcar",
            zpd_index=zpd_index,
        )
    except Exception:
        return None
    if mag.size < 2:
        return None
    return {
        "n": int(n),
        "sample_rate": float(sample_rate),
        "d_frep_hz": float(d_frep),
        "mag": np.ascontiguousarray(mag, dtype=np.float32),
    }


def _align_samples(n: int, multiple: int = _DEFAULT_DEPTH_INCREMENT) -> int:
    """Round *n* up to a multiple of *multiple* (minimum 0)."""
    n = max(0, int(n))
    multiple = max(1, int(multiple))
    if n == 0:
        return 0
    return ((n + multiple - 1) // multiple) * multiple


def normalize_live_window(
    pre_samples: int,
    post_samples: int,
    depth_increment: int = _DEFAULT_DEPTH_INCREMENT,
) -> Tuple[int, int]:
    """Sanitize and board-align the Live View pre/post-trigger sample counts.

    Both Depth and SegmentSize must be multiples of CAPS_DEPTH_INCREMENT
    (32 on CSE1642). Pre-trigger (= SegmentSize − Depth) inherits that
    constraint when both ends are aligned.
    """
    inc = max(1, int(depth_increment))
    pre = max(0, int(pre_samples))
    post = max(inc, int(post_samples))
    pre = _align_samples(pre, inc)
    post = _align_samples(post, inc)
    if post < inc:
        post = inc
    return pre, post


def _mode_for_channels(enabled: Sequence[int]) -> int:
    import GageConstants as gc

    # CSE1642 rejects CS_MODE_POWER_ON (CS_INVALID_ACQ_MODE). Analog-frontend
    # power-save pauses are handled by waiting through BUSY_CALIB instead.
    if not enabled:
        return gc.CS_MODE_SINGLE
    highest = max(enabled)
    if highest <= 1:
        return gc.CS_MODE_SINGLE
    if highest <= 2:
        return gc.CS_MODE_DUAL
    return gc.CS_MODE_QUAD


def _active_channel_indices(mode: int, channel_count: int, board_count: int) -> List[int]:
    import GageConstants as gc
    import GageSupport as gs

    masked = mode & gc.CS_MASKED_MODE
    if board_count <= 1:
        if masked >= gc.CS_MODE_QUAD:
            n = min(4, channel_count)
        elif masked >= gc.CS_MODE_DUAL:
            n = min(2, channel_count)
        else:
            n = min(1, channel_count)
        return list(range(1, n + 1))

    increment = gs.CalculateChannelIndexIncrement(mode, channel_count, board_count)
    return list(range(1, channel_count + 1, increment))


def raw_to_volts(buffer, acq: dict, chan: dict) -> np.ndarray:
    """Scale digitizer codes to volts as float32 (one copy from int16)."""
    samples = np.asarray(buffer, dtype=np.float32)
    scale = np.float32(chan["InputRange"] / 2000.0)
    offset = np.float32(chan["DcOffset"] / 1000.0)
    sample_offset = np.float32(acq["SampleOffset"])
    sample_res = float(acq["SampleResolution"]) or 1.0
    return ((sample_offset - samples) / np.float32(sample_res)) * scale + offset


def _child_abort(acq: GageAcq, handle) -> None:
    try:
        acq.abort(handle)
    except Exception:
        pass


class CaptureStopped(RuntimeError):
    """Raised when the user (or parent) asks the child to halt."""


def _stopped(stop_event, halt_event) -> bool:
    if stop_event is not None and stop_event.is_set():
        return True
    if halt_event is not None and halt_event.is_set():
        return True
    return False


def _pump_halt(cmd_q, halt_event, stop_event) -> None:
    """Apply halt/stop even while blocked in a wait; requeue other commands."""
    if cmd_q is None:
        return
    deferred = []
    while True:
        try:
            cmd = cmd_q.get_nowait()
        except Exception:
            break
        if not cmd:
            continue
        op = cmd[0]
        if op == "halt":
            if halt_event is not None:
                halt_event.set()
        elif op == "stop":
            if stop_event is not None:
                stop_event.set()
            if halt_event is not None:
                halt_event.set()
        else:
            deferred.append(cmd)
    for cmd in deferred:
        try:
            cmd_q.put_nowait(cmd)
        except Exception:
            pass


def _child_wait_ready(
    acq: GageAcq,
    handle,
    timeout_s: Optional[float],
    stop_event: mp.synchronize.Event,
    halt_event: Optional[threading.Event] = None,
    on_status: Optional[Any] = None,
    cmd_q=None,
    *,
    allow_force: bool = False,
) -> None:
    """Wait for READY. Calibration pauses the shot clock; no Abort here."""
    forced = False
    shot_t0 = time.monotonic()
    calib_t0: Optional[float] = None

    while True:
        _pump_halt(cmd_q, halt_event, stop_event)
        if _stopped(stop_event, halt_event):
            _child_abort(acq, handle)
            raise CaptureStopped("stopped")
        now = time.monotonic()
        st = acq.status(handle)
        if st < 0:
            raise RuntimeError(acq.error_string(st))
        if on_status is not None:
            on_status(st)
        if st == ACQ_STATUS_READY:
            return
        if st == ACQ_STATUS_BUSY_CALIB:
            if calib_t0 is None:
                calib_t0 = now
            elif now - calib_t0 >= CALIB_TIMEOUT_S:
                raise TimeoutError("Timed out waiting for analog calibration")
        else:
            calib_t0 = None
            if timeout_s is not None and now - shot_t0 >= timeout_s:
                if allow_force and not forced:
                    force = acq.force(handle)
                    if force < 0:
                        raise RuntimeError(acq.error_string(force))
                    forced = True
                    shot_t0 = time.monotonic()
                    continue
                raise TimeoutError("Capture timed out waiting for ACQ_STATUS_READY")
        # Short slice so halt/stop can be noticed; do not pause this slice
        # on calib (the outer loop already did).
        result = acq.wait_ready(
            handle,
            WAIT_SLICE_S,
            stop_check=lambda: _stopped(stop_event, halt_event),
            on_status=on_status,
            pause_on_calib=False,
        )
        if result == RESULT_READY:
            return
        if result == RESULT_STOPPED:
            _child_abort(acq, handle)
            raise CaptureStopped("stopped")


def _apply_trigger_config(
    handle,
    ini: str,
    trigger: TriggerSettings,
    enabled: List[int],
    active: List[int],
) -> int:
    """Map UI trigger settings onto Gage trigger engine 1. Returns Source used."""
    import PyGage
    import GageSupport as gs
    import GageConstants as gc

    if trigger["source"] == "External":
        trig_source = int(gc.CS_TRIG_SOURCE_EXT)
    else:
        # "Channel 1" (only internal source offered in the UI today).
        trig_source = 1
        if active and trig_source not in active:
            # In multi-channel modes the first active index is the safe fallback.
            trig_source = int(active[0])
        elif not active and enabled:
            trig_source = int(enabled[0])

    if trigger["edge"] == "Falling":
        condition = gc.CS_TRIG_COND_NEG_SLOPE
    else:
        condition = gc.CS_TRIG_COND_POS_SLOPE

    level = max(-100, min(100, int(trigger["level"])))

    if trigger["ext_coupling"] == "AC":
        ext_coupling = gc.CS_COUPLING_AC
    else:
        ext_coupling = gc.CS_COUPLING_DC

    if trigger["ext_impedance"] == "50 Ohms":
        ext_impedance = gc.CS_REAL_IMP_50_OHM
    else:
        ext_impedance = gc.CS_REAL_IMP_1M_OHM

    ext_range_mv = max(1, int(trigger["ext_range_mv"]))

    trig, _ = gs.LoadTriggerConfiguration(handle, 1, ini)
    if not isinstance(trig, dict) or not trig:
        trig = {}
    trig["Source"] = int(trig_source)
    trig["Condition"] = int(condition)
    # Level is percent of full-scale (−100…+100). 0 = mid-scale / 0 V for bipolar.
    trig["Level"] = int(level)
    # Always set external-trigger front-end fields; ignored when Source ≠ EXT.
    trig["ExtCoupling"] = int(ext_coupling)
    trig["ExtRange"] = int(ext_range_mv)
    trig["ExtImpedance"] = int(ext_impedance)

    status = PyGage.SetTriggerConfig(handle, 1, trig)
    if status < 0:
        raise RuntimeError(PyGage.GetErrorString(status))
    return int(trig_source)


def _child_configure(
    handle,
    system_info: dict,
    ini: str,
    sample_rate: int,
    enabled: List[int],
    range_mv: int,
    pre_samples: int,
    post_samples: int,
    app_config: dict,
    trigger: Optional[Dict[str, Any]] = None,
    trigger_timeout: Optional[int] = None,
    acq_backend: Optional[GageAcq] = None,
) -> List[int]:
    import PyGage
    import GageSupport as gs
    import GageConstants as gc

    trigger_settings = normalize_trigger_settings(trigger)
    if trigger_timeout is None:
        trigger_timeout = int(app_config.get("TriggerTimeout", LIVE_TRIGGER_TIMEOUT))
    app_config["TriggerTimeout"] = int(trigger_timeout)

    backend = acq_backend or GageAcq()
    # Only Abort if a shot is in flight. Abort on an idle board is what
    # clicks the Razor input relays between otherwise-identical Commits.
    try:
        st = backend.status(handle)
        if board_is_calibrating(st):
            backend.wait_ready(handle, CALIB_TIMEOUT_S)
            st = backend.status(handle)
        if not can_start_capture(st) and st >= 0:
            backend.abort_and_settle(handle)
            backend.wait_ready(handle, POST_COMMIT_READY_TIMEOUT_S)
    except Exception:
        pass

    # Align to the board's depth resolution (CSE1642 → 32).
    depth_inc = _DEFAULT_DEPTH_INCREMENT
    try:
        cap = PyGage.GetSystemCaps(handle, gc.CAPS_DEPTH_INCREMENT)
        if isinstance(cap, int) and cap > 0:
            depth_inc = cap
    except Exception:
        pass
    try:
        max_pre = PyGage.GetSystemCaps(handle, gc.CAPS_MAX_PRE_TRIGGER)
        if not (isinstance(max_pre, int) and max_pre > 0):
            max_pre = None
    except Exception:
        max_pre = None

    pre, post = normalize_live_window(pre_samples, post_samples, depth_inc)
    if max_pre is not None and pre > max_pre:
        pre = _align_samples(max_pre, depth_inc)
        if pre > max_pre:
            pre = max(0, (max_pre // depth_inc) * depth_inc)
    total = pre + post

    acq, _ = gs.LoadAcquisitionConfiguration(handle, ini)
    if not isinstance(acq, dict):
        raise RuntimeError(PyGage.GetErrorString(acq))

    mode = _mode_for_channels(enabled)
    acq["Mode"] = mode
    acq["SampleRate"] = int(sample_rate)
    trigger_timeout = app_config.get("TriggerTimeout", LIVE_TRIGGER_TIMEOUT)
    try:
        trigger_timeout = int(trigger_timeout)
    except (TypeError, ValueError):
        trigger_timeout = LIVE_TRIGGER_TIMEOUT
    acq["TriggerTimeout"] = trigger_timeout
    acq["SegmentCount"] = 1
    # Post-trigger depth; SegmentSize holds pre + post so pre-trigger is available.
    # Both must be multiples of CAPS_DEPTH_INCREMENT or Commit returns
    # CS_INVALID_SEGMENT_SIZE (-31).
    acq["Depth"] = post
    acq["SegmentSize"] = total
    acq["TriggerHoldoff"] = pre
    acq["TriggerDelay"] = 0

    status = PyGage.SetAcquisitionConfig(handle, acq)
    if status < 0:
        raise RuntimeError(PyGage.GetErrorString(status))

    channel_count = int(system_info["ChannelCount"])
    board_count = int(system_info["BoardCount"])
    active = _active_channel_indices(mode, channel_count, board_count)

    for ch in active:
        chan, _ = gs.LoadChannelConfiguration(handle, ch, ini)
        if isinstance(chan, dict) and chan:
            chan["InputRange"] = range_mv
            status = PyGage.SetChannelConfig(handle, ch, chan)
            if status < 0:
                raise RuntimeError(PyGage.GetErrorString(status))

    trig_source = _apply_trigger_config(
        handle, ini, trigger_settings, enabled, active
    )

    status = PyGage.Commit(handle)
    if status < 0:
        raise RuntimeError(PyGage.GetErrorString(status))

    # Commit often runs a full analog calibration (relays click for seconds).
    # Wait it out; Abort/Force here would click the relays again.
    acq = GageAcq()
    result = acq.wait_ready(handle, POST_COMMIT_READY_TIMEOUT_S)
    if result != RESULT_READY:
        raise RuntimeError("Timed out waiting for board READY after Commit")

    # Re-read the committed config once. Transfer uses this cache every frame
    # so we do not pay GetAcquisitionConfig / GetChannelConfig per interferogram.
    committed = PyGage.GetAcquisitionConfig(handle)
    if not isinstance(committed, dict):
        raise RuntimeError(PyGage.GetErrorString(committed))

    # Transfer window relative to trigger: [-pre, +post).
    app_config["StartPosition"] = -pre
    app_config["TransferLength"] = total
    app_config["PreTriggerSamples"] = pre
    app_config["PostTriggerSamples"] = post
    app_config["TriggerSource"] = int(trig_source)
    app_config["TriggerSettings"] = dict(trigger_settings)
    app_config["CachedAcq"] = committed
    app_config["CachedChan"] = {}
    for ch in active:
        chan = PyGage.GetChannelConfig(handle, ch)
        if isinstance(chan, dict):
            app_config["CachedChan"][int(ch)] = chan
    app_config["XAxis"] = np.arange(-pre, post, dtype=np.float64)

    active_out = [ch for ch in active if ch in enabled] or active[:1] or [1]
    return active_out


def _child_transfer(
    handle,
    app_config: dict,
    channels: Sequence[int],
    acq_backend: GageAcq,
    segment: int = 1,
) -> ChannelData:
    import PyGage
    import GageConstants as gc

    acq = app_config.get("CachedAcq")
    if not isinstance(acq, dict):
        acq = PyGage.GetAcquisitionConfig(handle)
        if not isinstance(acq, dict):
            raise RuntimeError(PyGage.GetErrorString(acq))
        app_config["CachedAcq"] = acq

    start = int(app_config.get("StartPosition", 0))
    length = int(app_config.get("TransferLength", 2040))
    min_start = acq["TriggerDelay"] + acq["Depth"] - acq["SegmentSize"]
    if start < min_start:
        start = int(min_start)
    max_length = acq["TriggerDelay"] + acq["Depth"] - min_start
    if length > max_length:
        length = int(max_length)

    chan_cache: dict = app_config.setdefault("CachedChan", {})
    x_axis = app_config.get("XAxis")
    result: ChannelData = {}
    for ch in channels:
        buf, actual_start, actual_length = acq_backend.transfer(
            handle,
            int(ch),
            start,
            length,
            mode=gc.TxMODE_DEFAULT,
            segment=int(segment),
        )
        chan = chan_cache.get(int(ch))
        if not isinstance(chan, dict):
            chan = PyGage.GetChannelConfig(handle, ch)
            if not isinstance(chan, dict):
                raise RuntimeError(PyGage.GetErrorString(chan))
            chan_cache[int(ch)] = chan
        volts = raw_to_volts(buf, acq, chan)
        n = min(int(actual_length), length, int(volts.size))
        volts = volts[:n]
        # Prefer trigger-relative sample indices for the plot (0 = trigger).
        if (
            isinstance(x_axis, np.ndarray)
            and x_axis.size >= n
            and actual_start == start
        ):
            x = x_axis[:n]
        else:
            x0 = int(actual_start) if actual_start is not None else start
            x = np.arange(x0, x0 + n, dtype=np.float64)
        result[int(ch)] = (x, volts)
    return result


def _child_arm_capture(
    acq: GageAcq,
    handle,
    stop_event: mp.synchronize.Event,
    halt_event: Optional[threading.Event] = None,
) -> bool:
    """Start a new acquisition. Returns False if the board is busy/calibrating.

    Never Aborts: Abort on CSE1642 toggles the input relays. If a previous
    shot or a calibration is still in flight, the caller waits it out.
    """
    if _stopped(stop_event, halt_event):
        return False
    status = acq.status(handle)
    if status < 0:
        raise RuntimeError(acq.error_string(status))
    if not can_start_capture(status):
        return False

    status = acq.start(handle)
    if status < 0:
        raise RuntimeError(acq.error_string(status))
    return True


def _child_finish_capture(
    acq: GageAcq,
    handle,
    app_config: dict,
    channels: Sequence[int],
    stop_event: mp.synchronize.Event,
    halt_event: Optional[threading.Event] = None,
    on_status: Optional[Any] = None,
    cmd_q=None,
    *,
    allow_force: bool = False,
    timeout_s: Optional[float] = CAPTURE_WAIT_TIMEOUT_S,
) -> Optional[ChannelData]:
    """Wait for the armed acquisition and DMA the record to host memory."""
    _child_wait_ready(
        acq,
        handle,
        timeout_s,
        stop_event,
        halt_event,
        on_status,
        cmd_q,
        allow_force=allow_force,
    )
    if _stopped(stop_event, halt_event):
        return None
    return _child_transfer(handle, app_config, channels, acq)


def _live_view_child_main(
    cmd_q: mp.Queue,
    frame_q: mp.Queue,
    event_q: mp.Queue,
    stop_event: mp.synchronize.Event,
    ini_path: str,
    shm_name: Optional[str] = None,
) -> None:
    """Child process entry: exclusive owner of the Gage system handle."""
    # Ensure gage_api / project packages are importable in the spawned process.
    _ensure_project_path()
    if str(GAGE_API_DIR) not in sys.path:
        sys.path.insert(0, str(GAGE_API_DIR))

    import PyGage
    import GageSupport as gs

    board = GageAcq()
    handle = None
    system_info = None
    app_config = None
    active_channels: List[int] = [1]
    capturing = False
    channels: List[int] = [1]
    frames_since_reset = 0
    min_frame_interval_s = WORKER_MIN_FRAME_INTERVAL_S
    # Last successful configure:
    # (rate, enabled, range_mv, pre, post, max_hz, trigger_settings, timeout).
    last_config: Optional[Tuple[int, List[int], int, int, int, int, dict, int]] = None
    wait_timeout_s: Optional[float] = CAPTURE_WAIT_TIMEOUT_S
    allow_force = True
    averager: Optional[InterferogramAverager] = None
    spectrum_params: Optional[dict] = None
    last_plot_t = 0.0
    sample_rate_hz = 0.0
    pending_frame: Optional[ChannelData] = None
    latest_monitor_y: Optional[np.ndarray] = None
    fft_stop = threading.Event()
    fft_thread: Optional[threading.Thread] = None
    halt_event = threading.Event()
    shm: Optional[SharedTraceBuffer] = None
    avg_shm: Optional[SharedAverageBuffer] = None
    last_avg_ckpt = 0.0
    last_avg_ckpt_accepted = -1
    if shm_name:
        try:
            shm = SharedTraceBuffer(MAX_LIVE_SAMPLES, name=shm_name, create=False)
        except Exception as exc:
            print(f"Gage child: shared traces unavailable ({exc})")
            shm = None

    def checkpoint_average(force: bool = False) -> None:
        nonlocal last_avg_ckpt, last_avg_ckpt_accepted
        if avg_shm is None or averager is None or averager.accepted < 1:
            return
        now_ckpt = time.monotonic()
        if not force and now_ckpt - last_avg_ckpt < 0.25:
            return
        state = averager.checkpoint_dict()
        if not state:
            return
        try:
            avg_shm.write(
                accepted=state["accepted"],
                rejected=state["rejected"],
                target=state["target"],
                threshold=state["threshold"],
                last_peak_corr=state["last_peak_corr"],
                last_lag=state["last_lag"],
                reference_channel=state["reference_channel"],
                align_center=state["align_center"],
                align_window=state["align_window"],
                x0=state["x0"],
                sums=state["sums"],
                eta_seconds=state["eta_seconds"],
            )
            last_avg_ckpt = now_ckpt
            last_avg_ckpt_accepted = int(state["accepted"])
        except Exception as exc:
            print(f"Gage child: average checkpoint failed ({exc})")

    def emit(kind: str, payload=None) -> None:
        try:
            event_q.put_nowait((kind, payload))
        except Exception:
            pass

    def snapshot_latest_y() -> Optional[np.ndarray]:
        """Copy the current stack (or last monitor frame). Always the newest."""
        if averager is not None and averager.accepted > 0:
            ch = averager.reference_channel
            buf = None
            if ch is not None:
                buf = averager._sum.get(int(ch))
            if buf is None and averager._sum:
                buf = next(iter(averager._sum.values()))
            if buf is None:
                return None
            raw = np.array(buf, dtype=np.float64, copy=True)
            n_acc = max(int(averager.accepted), 1)
            return (raw / float(n_acc)).astype(np.float32, copy=False)
        if latest_monitor_y is not None:
            return np.array(latest_monitor_y, dtype=np.float32, copy=True)
        return None

    def fft_loop() -> None:
        # HWEventHandler is not async-signal-safe. Keep Gage RT signals on
        # the capture thread so an rFFT cannot SIGSEGV the child mid-run.
        try:
            import signal as _signal
            lo = int(getattr(_signal, "SIGRTMIN", 34))
            hi = int(getattr(_signal, "SIGRTMAX", 64))
            _signal.pthread_sigmask(_signal.SIG_BLOCK, range(lo, hi + 1))
        except Exception:
            pass
        while not fft_stop.is_set() and not stop_event.is_set():
            if spectrum_params is None or sample_rate_hz <= 0:
                if fft_stop.wait(0.05):
                    break
                continue
            y = snapshot_latest_y()
            if y is None or y.size < 4:
                if fft_stop.wait(0.05):
                    break
                continue
            t0 = time.perf_counter()
            payload = _compute_spectrum_payload(y, sample_rate_hz, spectrum_params)
            if payload is not None:
                emit("spectrum", payload)
            # If this FFT was cheap, yield; if it was slow, immediately
            # snapshot whatever the averager holds now (never a backlog).
            elapsed = time.perf_counter() - t0
            if elapsed < 0.12:
                if fft_stop.wait(0.12 - elapsed):
                    break

    def start_fft_thread() -> None:
        nonlocal fft_thread
        stop_fft_thread()
        fft_stop.clear()
        fft_thread = threading.Thread(target=fft_loop, name="ChildSpectrumFFT", daemon=True)
        fft_thread.start()

    def stop_fft_thread() -> None:
        nonlocal fft_thread
        fft_stop.set()
        if fft_thread is not None:
            fft_thread.join(timeout=1.5)
        fft_thread = None

    last_hb = 0.0
    last_phase: Optional[str] = None

    def on_board_status(st: int) -> None:
        nonlocal last_hb, last_phase
        now_hb = time.monotonic()
        phase = status_name(st)
        if phase != last_phase:
            if st == ACQ_STATUS_BUSY_CALIB:
                print(
                    "Gage: analog calibration (relay sequence) — "
                    "waiting, not aborting"
                )
            last_phase = phase
            emit("heartbeat", {"status": phase})
            last_hb = now_hb
        elif now_hb - last_hb >= HEARTBEAT_INTERVAL_S:
            emit("heartbeat", {"status": phase})
            last_hb = now_hb

    def recover_soft(reason: str) -> bool:
        """Get back to READY without Commit/FreeSystem (no extra relay clicks)."""
        print(f"Gage: soft recover ({reason}) — wait READY, no re-Commit")
        if handle is None:
            return False
        try:
            st = board.status(handle)
            if st < 0:
                print(f"Gage: handle/status error {board.error_string(st)}")
                return False
            if st == ACQ_STATUS_READY:
                return True
            if board_is_calibrating(st) or not can_start_capture(st):
                result = board.wait_ready(
                    handle,
                    POST_COMMIT_READY_TIMEOUT_S,
                    stop_check=lambda: _stopped(stop_event, halt_event),
                    on_status=on_board_status,
                )
                if result == RESULT_READY:
                    return True
            board.abort_and_settle(handle)
            result = board.wait_ready(
                handle,
                POST_COMMIT_READY_TIMEOUT_S,
                stop_check=lambda: _stopped(stop_event, halt_event),
                on_status=on_board_status,
            )
            return result == RESULT_READY
        except Exception as exc:
            print(f"Gage: soft recover failed ({exc})")
            return False

    def open_board() -> None:
        nonlocal handle, system_info, app_config
        if handle is not None:
            try:
                board.abort_and_settle(handle)
            except Exception:
                pass
            try:
                PyGage.FreeSystem(handle)
            except Exception:
                pass
            handle = None
        status = PyGage.Initialize()
        if status < 0:
            raise RuntimeError(PyGage.GetErrorString(status))
        # Resource manager may need a moment after a previous process died.
        # CS_NO_AVAILABLE_SYSTEM (-21) usually means another app already holds
        # the exclusive system lock (e.g. CsTestQt / another Interferomatic).
        last_err = "No digitizer system found"
        last_code = None
        for attempt in range(10):
            handle = PyGage.GetSystem(0, 0, 0, 0)
            if handle >= 0:
                break
            last_code = int(handle)
            last_err = PyGage.GetErrorString(handle)
            time.sleep(0.2)
        else:
            hint = ""
            if last_code == -21:
                hint = (
                    " The digitizer is already locked by another process "
                    "(close CsTestQt / cstestqt, GageScope, or a leftover "
                    "Interferomatic capture child, then retry)."
                )
            elif last_code == -8:
                hint = (
                    " No CompuScope hardware was detected "
                    "(check the card, driver, and csrmd resource manager)."
                )
            raise RuntimeError(f"{last_err}{hint}")
        system_info = PyGage.GetSystemInfo(handle)
        if not isinstance(system_info, dict):
            raise RuntimeError(PyGage.GetErrorString(system_info))
        app_config, _ = gs.LoadApplicationConfiguration(ini_path)

    try:
        open_board()
        emit(
            "ready",
            {
                "BoardName": system_info.get("BoardName"),
                "ChannelCount": system_info.get("ChannelCount"),
                "c_shim": board.using_c_shim,
            },
        )
        print(
            "Gage capture backend: "
            + ("C shim (CsDo/CsTransfer)" if board.using_c_shim else "PyGage fallback")
        )

        while not stop_event.is_set():
            # Drain commands (non-blocking when capturing).
            try:
                if capturing:
                    cmd = cmd_q.get_nowait()
                else:
                    cmd = cmd_q.get(timeout=0.05)
            except Exception:
                cmd = None

            if cmd is not None:
                op = cmd[0]
                if op == "stop":
                    break
                if op == "configure":
                    # (op, rate, enabled, range_mv, pre, post, max_hz, trigger, timeout)
                    sample_rate = cmd[1]
                    enabled = cmd[2]
                    range_mv = cmd[3]
                    pre_samples = cmd[4]
                    post_samples = cmd[5]
                    max_hz_in = cmd[6] if len(cmd) > 6 else 0
                    trigger_in = cmd[7] if len(cmd) > 7 else None
                    timeout_in = cmd[8] if len(cmd) > 8 else LIVE_TRIGGER_TIMEOUT
                    try:
                        capturing = False
                        pending_frame = None
                        trig_settings = normalize_trigger_settings(trigger_in)
                        max_hz = max(0, int(max_hz_in))
                        min_frame_interval_s = max_capture_rate_to_interval_s(max_hz)
                        cfg_rate = int(sample_rate)
                        cfg_enabled = list(enabled)
                        cfg_range = int(range_mv)
                        cfg_pre = int(pre_samples)
                        cfg_post = int(post_samples)
                        try:
                            cfg_timeout = int(timeout_in)
                        except (TypeError, ValueError):
                            cfg_timeout = LIVE_TRIGGER_TIMEOUT
                        last_config = (
                            cfg_rate,
                            cfg_enabled,
                            cfg_range,
                            cfg_pre,
                            cfg_post,
                            max_hz,
                            dict(trig_settings),
                            cfg_timeout,
                        )
                        active_channels = _child_configure(
                            handle,
                            system_info,
                            ini_path,
                            cfg_rate,
                            cfg_enabled,
                            cfg_range,
                            cfg_pre,
                            cfg_post,
                            app_config,
                            trigger=last_config[6],
                            trigger_timeout=cfg_timeout,
                            acq_backend=board,
                        )
                        channels = list(active_channels)
                        frames_since_reset = 0
                        sample_rate_hz = float(cfg_rate)
                        emit(
                            "configured",
                            {
                                "rate": cfg_rate,
                                "channels": active_channels,
                                "range_mv": cfg_range,
                                "pre": cfg_pre,
                                "post": cfg_post,
                                "max_capture_rate_hz": max_hz,
                                "trigger_source": app_config.get(
                                    "TriggerSource",
                                    active_channels[0] if active_channels else 1,
                                ),
                                "trigger": dict(trig_settings),
                            },
                        )
                    except Exception as e:
                        emit("error", str(e))
                elif op == "spectrum":
                    spec_in = cmd[1] if len(cmd) > 1 else None
                    spectrum_params = (
                        dict(spec_in) if isinstance(spec_in, dict) else None
                    )
                elif op == "start":
                    # (op, enabled[, average_dict[, spectrum_dict]])
                    enabled = cmd[1]
                    avg_in = cmd[2] if len(cmd) > 2 else None
                    spec_in = cmd[3] if len(cmd) > 3 else None
                    enabled_set = {int(c) for c in enabled}
                    channels = [c for c in active_channels if c in enabled_set] or list(
                        active_channels[:1]
                    )
                    spectrum_params = dict(spec_in) if isinstance(spec_in, dict) else None
                    avg_shm_name = cmd[4] if len(cmd) > 4 else None
                    if avg_shm is None and avg_shm_name:
                        try:
                            avg_shm = SharedAverageBuffer(
                                MAX_LIVE_SAMPLES, name=str(avg_shm_name), create=False
                            )
                        except Exception as exc:
                            print(f"Gage child: average checkpoint unavailable ({exc})")
                            avg_shm = None
                    if isinstance(avg_in, dict) and avg_in:
                        ref_ch = avg_in.get("reference_channel")
                        try:
                            ref_ch = int(ref_ch) if ref_ch is not None else None
                        except (TypeError, ValueError):
                            ref_ch = None
                        averager = InterferogramAverager(
                            target=int(avg_in.get("target", 1)),
                            threshold=float(avg_in.get("threshold", 0.5)),
                            reference_channel=ref_ch,
                        )
                        restored = False
                        if avg_shm is not None:
                            try:
                                ckpt = avg_shm.read()
                            except Exception:
                                ckpt = None
                            if ckpt is not None:
                                restored = averager.load_checkpoint(ckpt)
                        if restored:
                            print(
                                f"Gage: restored average stack "
                                f"{averager.accepted}/{averager.target} "
                                f"({averager.rejected} rejected)"
                            )
                            emit("heartbeat", {"status": "restored"})
                            emit(
                                "average_result",
                                averager.snapshot(include_arrays=False),
                            )
                    else:
                        averager = None
                    # Finite wait + ForceCapture watchdog. Infinite wait hangs
                    # the first time the SSM stays in WAIT_TRIGGER through a
                    # front-end calibration (Linux GetStatus hides BUSY_CALIB).
                    if averager is not None:
                        wait_timeout_s = AVERAGE_WAIT_TIMEOUT_S
                    else:
                        wait_timeout_s = CAPTURE_WAIT_TIMEOUT_S
                    allow_force = True
                    last_plot_t = 0.0
                    halt_event.clear()
                    capturing = True
                    start_fft_thread()
                elif op == "halt":
                    capturing = False
                    halt_event.set()
                    stop_fft_thread()
                    if handle is not None:
                        board.abort_and_settle(handle)
                    if pending_frame is not None and averager is not None:
                        averager.process_frame(pending_frame)
                        pending_frame = None
                    checkpoint_average(force=True)
                elif op == "get_average":
                    if averager is not None:
                        emit(
                            "average_result",
                            averager.snapshot(include_arrays=True),
                        )
                    else:
                        emit("average_result", None)
                elif op == "channels":
                    _, enabled = cmd
                    enabled_set = {int(c) for c in enabled}
                    channels = [c for c in active_channels if c in enabled_set] or list(
                        active_channels[:1]
                    )

            if not capturing or stop_event.is_set():
                continue

            def consume_frame(frame: ChannelData) -> None:
                """Align/stack a finished record. FFT and plot live elsewhere."""
                nonlocal capturing, frames_since_reset, last_plot_t, latest_monitor_y
                frames_since_reset += 1
                if (
                    SOFT_RESET_EVERY_N_FRAMES > 0
                    and frames_since_reset >= SOFT_RESET_EVERY_N_FRAMES
                ):
                    board.abort_and_settle(handle)
                    frames_since_reset = 0

                avg_lite = None
                plot_data: Optional[ChannelData] = None
                now_pub = time.monotonic()
                publish = (
                    last_plot_t <= 0.0
                    or (now_pub - last_plot_t) >= PLOT_PUBLISH_INTERVAL_S
                )

                if averager is not None:
                    result = averager.process_frame(frame)
                    checkpoint_average(force=result.complete)
                    avg_lite = _lite_average_dict(result)
                    if result.complete:
                        capturing = False
                        publish = True
                        emit(
                            "average_complete",
                            averager.snapshot(include_arrays=True),
                        )
                    if publish and result.accepted > 0:
                        full = averager.to_channel_data()
                        if shm is not None and full:
                            try:
                                shm.write(full)
                            except Exception:
                                pass
                        elif full:
                            plot_data = downsample_channel_data(full)
                else:
                    for _ch, (_x, y) in frame.items():
                        latest_monitor_y = y
                        break
                    if publish:
                        if shm is not None:
                            try:
                                shm.write(frame)
                            except Exception:
                                pass
                        else:
                            plot_data = downsample_channel_data(frame)

                if publish or avg_lite is not None:
                    if publish:
                        last_plot_t = now_pub
                    payload = {
                        "kind": "frame",
                        "plot": plot_data or {},
                        "spectrum": None,
                        "average": avg_lite,
                    }
                    try:
                        while True:
                            frame_q.get_nowait()
                    except Exception:
                        pass
                    try:
                        frame_q.put_nowait(payload)
                    except Exception:
                        pass

            t0 = time.monotonic()
            try:
                # Arm the next shot first so alignment of the previous record
                # overlaps the board's sample window (LabVIEW / CsTestQt style).
                armed = _child_arm_capture(
                    board, handle, stop_event, halt_event
                )
                if not armed:
                    # Calibrating or previous shot still running — wait, do
                    # not Abort (Abort clicks the Razor relays).
                    try:
                        _child_wait_ready(
                            board,
                            handle,
                            WAIT_SLICE_S,
                            stop_event,
                            halt_event,
                            on_board_status,
                            cmd_q,
                            allow_force=False,
                        )
                    except TimeoutError:
                        pass
                    continue
                if pending_frame is not None:
                    consume_frame(pending_frame)
                    pending_frame = None
                if capturing and not stop_event.is_set():
                    try:
                        pending_frame = _child_finish_capture(
                            board,
                            handle,
                            app_config,
                            channels,
                            stop_event,
                            halt_event,
                            on_board_status,
                            cmd_q,
                            allow_force=allow_force,
                            timeout_s=wait_timeout_s,
                        )
                    except TimeoutError:
                        # Quiet input, missed trigger, or SSM stuck in
                        # WAIT_TRIGGER through a hidden calibration.
                        pending_frame = None
                        st = board.status(handle)
                        on_board_status(st)
                        if (
                            st >= 0
                            and not can_start_capture(st)
                            and not board_is_calibrating(st)
                        ):
                            print(
                                "Gage: acquisition watchdog — Abort to re-arm "
                                "(no re-Commit)"
                            )
                            board.abort_and_settle(handle)
                        continue
                else:
                    board.abort_and_settle(handle)
            except CaptureStopped:
                pending_frame = None
                capturing = False
                halt_event.set()
                stop_fft_thread()
                continue
            except Exception as e:
                pending_frame = None
                if stop_event.is_set() or "stopped" in str(e).lower():
                    capturing = False
                    continue
                # Soft recover first: wait through calib / drain in-flight
                # shot. Re-Commit (relay click) only if the handle is dead.
                if recover_soft(str(e)):
                    frames_since_reset = 0
                    continue
                try:
                    open_board()
                    if last_config is not None:
                        (
                            cfg_rate,
                            cfg_enabled,
                            cfg_range,
                            cfg_pre,
                            cfg_post,
                            cfg_max_hz,
                            cfg_trigger,
                            *rest,
                        ) = last_config
                        cfg_timeout = (
                            int(rest[0]) if rest else LIVE_TRIGGER_TIMEOUT
                        )
                        active_channels = _child_configure(
                            handle,
                            system_info,
                            ini_path,
                            cfg_rate,
                            cfg_enabled,
                            cfg_range,
                            cfg_pre,
                            cfg_post,
                            app_config,
                            trigger=cfg_trigger,
                            trigger_timeout=cfg_timeout,
                            acq_backend=board,
                        )
                        min_frame_interval_s = max_capture_rate_to_interval_s(
                            int(cfg_max_hz)
                        )
                        sample_rate_hz = float(cfg_rate)
                        channels = [
                            c for c in active_channels if c in set(channels)
                        ] or list(active_channels[:1])
                    frames_since_reset = 0
                    continue
                except Exception as e2:
                    emit("error", f"{e} (recover failed: {e2})")
                    capturing = False
                    continue

            elapsed = time.monotonic() - t0
            delay = min_frame_interval_s - elapsed
            if delay > 0 and not stop_event.is_set():
                time.sleep(delay)

    except Exception as e:
        emit("error", str(e))
    finally:
        if handle is not None:
            try:
                board.abort(handle)
            except Exception:
                pass
            try:
                PyGage.FreeSystem(handle)
            except Exception:
                pass
        stop_fft_thread()
        if shm is not None:
            try:
                shm.close(unlink=False)
            except Exception:
                pass
        if avg_shm is not None:
            try:
                checkpoint_average(force=True)
                avg_shm.close(unlink=False)
            except Exception:
                pass
        emit("exited", None)


class LiveViewEngine:
    """Parent-side Live View engine: spawns a Gage child process."""

    def __init__(self, ini_path: Optional[Path] = None):
        self.ini_path = Path(ini_path) if ini_path else DEFAULT_INI
        self._available = False
        self._ctx = mp.get_context("spawn")
        self._cmd_q: Optional[mp.Queue] = None
        self._frame_q: Optional[mp.Queue] = None
        self._event_q: Optional[mp.Queue] = None
        self._stop_event: Optional[mp.synchronize.Event] = None
        self._proc: Optional[mp.Process] = None

        self._configured_rate: Optional[int] = None
        self._configured_channels: Optional[Tuple[int, ...]] = None
        self._configured_input_range: Optional[int] = None
        self._configured_pre: Optional[int] = None
        self._configured_post: Optional[int] = None
        self._configured_max_hz: Optional[int] = None
        self._configured_trigger: Optional[Tuple] = None
        self._configured_trigger_timeout: Optional[int] = None
        self._running = False
        self._child_restarts = 0
        self._board_name = "Gage"
        self._last_heartbeat = 0.0
        self._board_phase = ""
        # (rate, channels, range_mv, pre, post, max_hz, trigger_key, trigger_dict, timeout)
        self._pending_config: Optional[
            Tuple[int, Tuple[int, ...], int, int, int, int, Tuple, dict, int]
        ] = None
        self._last_error: Optional[str] = None
        self._config_ack = False
        self._last_sent_channels: Optional[Tuple[int, ...]] = None
        self._atexit_registered = False
        self._average_status: Optional[AverageResult] = None
        self._average_full: Optional[AverageResult] = None
        self._last_spectrum: Optional[SpectrumData] = None
        self._average_request_pending = False
        self._start_average: Optional[dict] = None
        self._start_spectrum: Optional[dict] = None
        self._shm: Optional[SharedTraceBuffer] = None
        self._avg_shm: Optional[SharedAverageBuffer] = None
        self._spectrum_consumed: Optional[SpectrumData] = None
        self._traces_seq = -1

    @property
    def available(self) -> bool:
        return self._available

    @property
    def last_heartbeat(self) -> float:
        return self._last_heartbeat

    @property
    def board_phase(self) -> str:
        return self._board_phase

    def child_alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def open(self) -> None:
        """Start the Gage child process (does not begin capturing yet)."""
        if not self._atexit_registered:
            atexit.register(self._stop_child)
            self._atexit_registered = True
        self._start_child()
        # Wait briefly for the child to open the board.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            self._drain_events()
            if self._available:
                print(
                    f"Gage Live View ready: {self._board_name} "
                    f"(driver isolated in child process)"
                )
                return
            if self._last_error:
                raise RuntimeError(self._last_error)
            time.sleep(0.05)
        raise RuntimeError("Timed out waiting for Gage child process to open the board")

    def _start_child(self) -> None:
        self._stop_child()
        # Give the Gage resource manager time to release a just-killed holder.
        # Back off further when we have been crash-looping.
        cooldown = 0.5 + 0.4 * min(self._child_restarts, 6)
        time.sleep(cooldown)
        self._ensure_shm()
        self._cmd_q = self._ctx.Queue()
        self._frame_q = self._ctx.Queue(maxsize=2)
        self._event_q = self._ctx.Queue()
        self._stop_event = self._ctx.Event()
        self._available = False
        self._last_error = None
        shm_name = self._shm.name if self._shm is not None else None
        self._proc = self._ctx.Process(
            target=_live_view_child_main,
            args=(
                self._cmd_q,
                self._frame_q,
                self._event_q,
                self._stop_event,
                str(self.ini_path),
                shm_name,
            ),
            name="LiveViewGageChild",
            daemon=True,
        )
        self._proc.start()

    def _close_queue(self, q) -> None:
        """Best-effort close so multiprocessing semaphores do not leak."""
        if q is None:
            return
        try:
            q.close()
        except Exception:
            pass
        try:
            q.join_thread()
        except Exception:
            pass

    def _stop_child(self) -> None:
        if self._stop_event is not None:
            try:
                self._stop_event.set()
            except Exception:
                pass
        if self._cmd_q is not None:
            try:
                self._cmd_q.put_nowait(("stop",))
            except Exception:
                pass
        if self._proc is not None:
            self._proc.join(timeout=3.0)
            if self._proc.is_alive():
                self._proc.terminate()
                self._proc.join(timeout=2.0)
            if self._proc.is_alive():
                self._proc.kill()
                self._proc.join(timeout=1.0)
        self._proc = None
        # Close queues after the child is dead so the resource tracker can
        # reclaim semaphores (rapid SIGSEGV restarts were leaking them).
        self._close_queue(self._cmd_q)
        self._close_queue(self._frame_q)
        self._close_queue(self._event_q)
        self._cmd_q = None
        self._frame_q = None
        self._event_q = None
        self._stop_event = None
        self._available = False
        self._running = False
        # Pause so CsRm releases the board after process death. Longer after
        # fault recovery so the driver is less likely to GPF immediately.
        time.sleep(0.5)

    def close(self) -> None:
        self._stop_child()
        if self._shm is not None:
            try:
                self._shm.close(unlink=True)
            except Exception:
                pass
            self._shm = None
        if self._avg_shm is not None:
            try:
                self._avg_shm.close(unlink=True)
            except Exception:
                pass
            self._avg_shm = None
        self._configured_rate = None
        self._configured_channels = None
        self._configured_input_range = None
        self._configured_pre = None
        self._configured_post = None
        self._configured_max_hz = None
        self._configured_trigger = None
        self._configured_trigger_timeout = None

    def stop(self) -> None:
        """Halt capture but keep the child (and board handle) alive."""
        self._running = False
        if self._cmd_q is not None:
            try:
                self._cmd_q.put_nowait(("halt",))
            except Exception:
                pass
        self._drain_frames()
        # Drop any "start again" leftovers from a previous run.
        self._last_sent_channels = None

    def restart_capture(self, enabled_channels: Sequence[int]) -> None:
        """Recycle the Gage child and resume capture with the last config.

        Used when the worker stops producing frames without exiting (driver
        stuck) or after a hard fault path that left capture halted.
        """
        channels = [int(c) for c in enabled_channels if 1 <= int(c) <= 4] or [1]
        self.stop()
        self._stop_child()
        self._ensure_child()
        self.start(
            channels,
            average=self._start_average,
            spectrum=self._start_spectrum,
            resume_average=True,
        )

    def _drain_events(self) -> None:
        if self._event_q is None:
            return
        while True:
            try:
                kind, payload = self._event_q.get_nowait()
            except Exception:
                break
            if kind == "ready":
                self._available = True
                self._last_heartbeat = time.monotonic()
                if isinstance(payload, dict):
                    self._board_name = str(payload.get("BoardName") or "Gage")
                    if payload.get("c_shim"):
                        print("Gage child: using C acquisition shim")
            elif kind == "configured":
                self._config_ack = True
                if isinstance(payload, dict):
                    pre = payload.get("pre")
                    post = payload.get("post")
                    window = (
                        f", window={pre}+{post} samples"
                        if pre is not None and post is not None
                        else ""
                    )
                    trig_payload = payload.get("trigger")
                    if isinstance(trig_payload, dict):
                        trig_settings = normalize_trigger_settings(trig_payload)
                        trig_src = int(payload.get("trigger_source", 1) or 1)
                        trig = (
                            f", trigger={_format_trigger_summary(trig_settings, trig_src)}"
                        )
                    else:
                        trig_src = payload.get("trigger_source")
                        trig = f", trigger=CH{trig_src}" if trig_src is not None else ""
                    max_hz = payload.get("max_capture_rate_hz")
                    cap = f", max_capture={max_hz} Hz" if max_hz else ""
                    print(
                        f"Live View configured: rate={payload.get('rate')} S/s, "
                        f"range=±{(payload.get('range_mv') or 0) / 2:g} mV, "
                        f"channels={payload.get('channels')}{window}{cap}{trig}"
                    )
            elif kind == "error":
                self._last_error = str(payload)
            elif kind == "exited":
                self._available = False
            elif kind == "heartbeat":
                self._last_heartbeat = time.monotonic()
                if isinstance(payload, dict):
                    self._board_phase = str(payload.get("status") or "")
            elif kind == "spectrum":
                if isinstance(payload, dict) and payload.get("mag") is not None:
                    self._last_spectrum = payload
            elif kind in ("average_complete", "average_result"):
                if isinstance(payload, AverageResult):
                    self._average_full = payload
                    self._average_status = payload.lite()
                elif payload is None:
                    self._average_full = None
                self._average_request_pending = False

    def _drain_frames(self) -> None:
        if self._frame_q is None:
            return
        while True:
            try:
                self._frame_q.get_nowait()
            except Exception:
                break

    def _ensure_child(self) -> None:
        """Restart the child if it died; re-apply last config if needed."""
        self._drain_events()
        alive = self._proc is not None and self._proc.is_alive()
        if alive:
            return
        self._child_restarts += 1
        if self._child_restarts > MAX_CHILD_RESTARTS:
            raise RuntimeError(
                self._last_error
                or "Gage capture process exited repeatedly; check the driver/card"
            )
        print(
            f"Restarting Gage Live View child process "
            f"(attempt {self._child_restarts}/{MAX_CHILD_RESTARTS})..."
        )
        self._start_child()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            self._drain_events()
            if self._available:
                break
            if self._last_error:
                raise RuntimeError(self._last_error)
            time.sleep(0.05)
        else:
            raise RuntimeError("Gage child process failed to restart")

        if self._pending_config is not None:
            (
                rate,
                enabled,
                range_mv,
                pre,
                post,
                max_hz,
                _trig_key,
                trig_dict,
                trig_timeout,
            ) = self._pending_config
            self._send(
                (
                    "configure",
                    rate,
                    list(enabled),
                    range_mv,
                    pre,
                    post,
                    max_hz,
                    trig_dict,
                    trig_timeout,
                )
            )
            # Wait for configure ack / error briefly.
            deadline = time.monotonic() + POST_COMMIT_READY_TIMEOUT_S
            while time.monotonic() < deadline:
                self._drain_events()
                if self._last_error:
                    err = self._last_error
                    self._last_error = None
                    raise RuntimeError(err)
                # configured print is enough; don't block forever
                if self._configured_rate == rate:
                    break
                time.sleep(0.05)
            self._configured_rate = rate
            self._configured_channels = enabled
            self._configured_input_range = range_mv
            self._configured_pre = pre
            self._configured_post = post
            self._configured_max_hz = max_hz
            self._configured_trigger = _trig_key
            self._configured_trigger_timeout = trig_timeout

        if self._running and self._configured_channels is not None:
            self.start(
                list(self._configured_channels),
                average=self._start_average,
                spectrum=self._start_spectrum,
                resume_average=True,
            )

    def _send(self, cmd: tuple) -> None:
        if self._cmd_q is None:
            raise RuntimeError("Gage child process is not running")
        self._cmd_q.put(cmd)

    def configure(
        self,
        sample_rate: int,
        enabled_channels: Sequence[int],
        input_range: int = 2000,
        pre_trigger_samples: int = 5000,
        post_trigger_samples: int = 15000,
        trigger: Optional[Dict[str, Any]] = None,
        max_capture_rate_hz: int = 0,
        trigger_timeout: Optional[int] = None,
    ) -> None:
        if not self._available and self._proc is None:
            raise RuntimeError("Gage system is not open")

        enabled = tuple(sorted({int(c) for c in enabled_channels if 1 <= int(c) <= 4}))
        if not enabled:
            enabled = (1,)
        range_mv = int(input_range)
        if range_mv < 1:
            range_mv = 2000
        pre, post = normalize_live_window(pre_trigger_samples, post_trigger_samples)
        trig_settings = normalize_trigger_settings(trigger)
        trig_key = trigger_settings_key(trig_settings)
        max_hz = max(0, int(max_capture_rate_hz))
        if trigger_timeout is None:
            trig_timeout = LIVE_TRIGGER_TIMEOUT
        else:
            try:
                trig_timeout = int(trigger_timeout)
            except (TypeError, ValueError):
                trig_timeout = LIVE_TRIGGER_TIMEOUT

        key = (
            int(sample_rate),
            enabled,
            range_mv,
            pre,
            post,
            max_hz,
            trig_key,
            dict(trig_settings),
            trig_timeout,
        )
        self._pending_config = key
        if (
            int(sample_rate) == self._configured_rate
            and enabled == self._configured_channels
            and range_mv == self._configured_input_range
            and pre == self._configured_pre
            and post == self._configured_post
            and max_hz == self._configured_max_hz
            and trig_key == self._configured_trigger
            and trig_timeout == self._configured_trigger_timeout
        ):
            return

        self._ensure_child()
        self._last_error = None
        self._config_ack = False
        self._send(
            (
                "configure",
                int(sample_rate),
                list(enabled),
                range_mv,
                pre,
                post,
                max_hz,
                dict(trig_settings),
                trig_timeout,
            )
        )

        # Block until configured or error (Commit + calib can take seconds).
        deadline = time.monotonic() + POST_COMMIT_READY_TIMEOUT_S
        while time.monotonic() < deadline:
            self._drain_events()
            if self._last_error:
                err = self._last_error
                self._last_error = None
                raise RuntimeError(err)
            if self._config_ack:
                break
            if self._proc is not None and not self._proc.is_alive():
                raise RuntimeError(
                    self._last_error or "Gage child process exited during configure"
                )
            time.sleep(0.05)
        else:
            raise RuntimeError(
                "Timed out waiting for Live View configure in child process"
            )

        self._configured_rate = int(sample_rate)
        self._configured_channels = enabled
        self._configured_input_range = range_mv
        self._configured_pre = pre
        self._configured_post = post
        self._configured_max_hz = max_hz
        self._configured_trigger = trig_key
        self._configured_trigger_timeout = trig_timeout
        self._child_restarts = 0  # healthy configure resets restart budget

    def start(
        self,
        enabled_channels: Sequence[int],
        average: Optional[dict] = None,
        spectrum: Optional[dict] = None,
        resume_average: bool = False,
    ) -> None:
        enabled = [int(c) for c in enabled_channels if 1 <= int(c) <= 4] or [1]
        self._ensure_child()
        self._running = True
        self._start_average = dict(average) if average else None
        self._start_spectrum = dict(spectrum) if spectrum else None
        if self._start_average and not resume_average:
            self._clear_average_shm()
            self._average_status = None
            self._average_full = None
        avg_name = None
        if self._start_average:
            n = int(self._configured_pre or 0) + int(self._configured_post or 0)
            self._ensure_avg_shm(max(n, 1))
            avg_name = self._avg_shm.name if self._avg_shm is not None else None
        self._spectrum_consumed = None
        self._traces_seq = -1
        self._last_spectrum = None
        self._send(
            (
                "start",
                enabled,
                self._start_average,
                self._start_spectrum,
                avg_name,
            )
        )
        self._last_sent_channels = tuple(enabled)

    def update_spectrum(self, spectrum: Optional[dict] = None) -> None:
        """Replace FFT parameters on the running child (apodization, etc.)."""
        self._start_spectrum = dict(spectrum) if spectrum else None
        if self._cmd_q is None:
            return
        try:
            self._send(("spectrum", self._start_spectrum))
        except RuntimeError:
            pass

    def average_status(self) -> Optional[AverageResult]:
        """Latest lite averaging snapshot (no full-resolution arrays)."""
        return self._average_status

    def last_spectrum(self) -> Optional[SpectrumData]:
        """Latest spectrum from the background FFT worker (never the capture loop)."""
        return self._last_spectrum

    def take_spectrum(self) -> Optional[SpectrumData]:
        """Return a newly computed spectrum once; None if nothing new."""
        spec = self._last_spectrum
        if spec is None or spec is self._spectrum_consumed:
            return None
        self._spectrum_consumed = spec
        return spec

    def full_traces(self) -> Optional[ChannelData]:
        """Latest full-resolution traces from shared memory (for zoom)."""
        if self._shm is None:
            return None
        try:
            return self._shm.read()
        except Exception:
            return None

    def full_traces_if_newer(self) -> Optional[ChannelData]:
        """Copy SHM only when the child has published a newer snapshot."""
        if self._shm is None:
            return None
        try:
            seq = self._shm.seq
        except Exception:
            return None
        if seq <= 0 or seq == self._traces_seq:
            return None
        traces = self.full_traces()
        if traces:
            self._traces_seq = seq
        return traces

    def _ensure_shm(self) -> None:
        if self._shm is not None:
            return
        try:
            self._shm = SharedTraceBuffer(MAX_LIVE_SAMPLES, create=True)
        except Exception as exc:
            print(f"Shared traces unavailable ({exc}); zoom will use decimated plots")
            self._shm = None

    def _ensure_avg_shm(self, n_samples: int) -> None:
        n = max(1, int(n_samples), int(self._configured_pre or 0) + int(self._configured_post or 0))
        if self._avg_shm is not None:
            if self._avg_shm.max_samples >= n:
                return
            try:
                self._avg_shm.close(unlink=True)
            except Exception:
                pass
            self._avg_shm = None
        try:
            self._avg_shm = SharedAverageBuffer(n, create=True)
        except Exception as exc:
            print(f"Average checkpoint unavailable ({exc}); child restart will drop the stack")
            self._avg_shm = None

    def _clear_average_shm(self) -> None:
        if self._avg_shm is not None:
            try:
                self._avg_shm.clear()
            except Exception:
                pass

    def get_average_result(self, timeout_s: float = 1.0) -> Optional[AverageResult]:
        """Fetch the full-resolution average from the child (for save / final plot)."""
        if self._average_full is not None and self._average_full.averages:
            return self._average_full
        if self._cmd_q is None:
            return self._average_full
        self._average_request_pending = True
        try:
            self._send(("get_average",))
        except Exception:
            return self._average_full
        deadline = time.monotonic() + max(0.2, float(timeout_s))
        while time.monotonic() < deadline:
            self._drain_events()
            if not self._average_request_pending and self._average_full is not None:
                return self._average_full
            time.sleep(0.01)
        return self._average_full

    def _ingest_frame_payload(self, payload) -> Optional[ChannelData]:
        """Unpack a child snapshot. Returns plot ChannelData or None."""
        if payload is None:
            return None
        if isinstance(payload, dict) and payload.get("kind") == "frame":
            avg = payload.get("average")
            if isinstance(avg, dict):
                self._average_status = _average_from_lite(avg)
            spec = payload.get("spectrum")
            if spec is not None:
                self._last_spectrum = spec
            plot = payload.get("plot")
            if isinstance(plot, dict):
                return plot
            return {}
        if isinstance(payload, dict):
            # Legacy / unexpected ChannelData-shaped dict.
            return payload
        return None

    def capture(self, enabled_channels: Sequence[int]) -> Optional[ChannelData]:
        """Return the latest plot snapshot from the child, or None if none is ready."""
        enabled = [int(c) for c in enabled_channels if 1 <= int(c) <= 4]
        if not enabled:
            self.stop()
            return {}

        self._drain_events()
        if self._last_error:
            err = self._last_error
            self._last_error = None
            print(f"Gage child error: {err}")
            # Keep collecting: re-arm the child instead of tearing down the run.
            if self._running and self._proc is not None and self._proc.is_alive():
                try:
                    self.start(
                        enabled,
                        average=self._start_average,
                        spectrum=self._start_spectrum,
                        resume_average=True,
                    )
                except Exception:
                    pass
                return None
            self._running = False
            raise RuntimeError(err)

        if self._proc is not None and not self._proc.is_alive():
            # Child crashed (e.g. driver SIGSEGV) — restart and continue.
            code = self._proc.exitcode
            print(f"Gage Live View child exited (code={code}); recovering...")
            was_running = self._running
            self._ensure_child()
            if was_running:
                self.start(
                    enabled,
                    average=self._start_average,
                    spectrum=self._start_spectrum,
                    resume_average=True,
                )
            return None

        if not self._running:
            return None

        enabled_key = tuple(enabled)
        if enabled_key != self._last_sent_channels:
            try:
                self._send(("channels", enabled))
                self._last_sent_channels = enabled_key
            except Exception:
                pass

        latest: Optional[ChannelData] = None
        if self._frame_q is not None:
            while True:
                try:
                    raw = self._frame_q.get_nowait()
                except Exception:
                    break
                parsed = self._ingest_frame_payload(raw)
                if parsed is not None:
                    latest = parsed
        # A healthy frame means the child is stable again — allow future
        # recoveries without burning the consecutive-restart budget on a
        # multi-hour Average run (SIGSEGV every few hundred frames).
        if latest is not None and self._child_restarts:
            self._child_restarts = 0
        return latest


class SimulatedLiveViewEngine:
    """Synthetic waveforms so Live View can be exercised without a Gage card."""

    def __init__(self):
        self._available = True
        self._t0 = time.monotonic()
        self._configured_rate = 200_000_000
        self._configured_input_range = 2000
        self._active_channels = [1]
        self._pre = 5000
        self._post = 15000
        self._trigger: TriggerSettings = dict(DEFAULT_TRIGGER_SETTINGS)
        self._max_capture_rate_hz = 0
        self._min_frame_interval_s = 0.0
        self._last_capture_t = 0.0
        self._x: Optional[np.ndarray] = None
        self._templates: Dict[int, np.ndarray] = {}
        self._averager: Optional[InterferogramAverager] = None
        self._spectrum_params: Optional[dict] = None
        self._average_status: Optional[AverageResult] = None
        self._last_spectrum: Optional[SpectrumData] = None
        self._last_spectrum_t = 0.0
        self._last_plot_t = 0.0
        self._last_plot: Optional[ChannelData] = None
        self._full_traces: Optional[ChannelData] = None
        self._fft_stop: Optional[threading.Event] = None
        self._fft_thread: Optional[threading.Thread] = None
        self._fft_dirty = False
        self._spectrum_consumed: Optional[SpectrumData] = None
        self._traces_published = False
        self._running = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def last_heartbeat(self) -> float:
        return time.monotonic() if self._running else 0.0

    @property
    def board_phase(self) -> str:
        return "ready" if self._running else ""

    def child_alive(self) -> bool:
        return True

    def open(self) -> None:
        self._available = True
        print("Gage-less Live View: using simulated waveforms")

    def close(self) -> None:
        self._available = False

    def stop(self) -> None:
        self._last_capture_t = 0.0
        self._running = False
        if self._fft_stop is not None:
            self._fft_stop.set()

    def start(
        self,
        enabled_channels: Sequence[int],
        average: Optional[dict] = None,
        spectrum: Optional[dict] = None,
        resume_average: bool = False,
    ) -> None:
        self._last_capture_t = 0.0
        self._last_spectrum_t = 0.0
        self._last_plot_t = 0.0
        self._last_plot = None
        self._spectrum_params = dict(spectrum) if spectrum else None
        if isinstance(average, dict) and average:
            if not (resume_average and self._averager is not None):
                ref_ch = average.get("reference_channel")
                try:
                    ref_ch = int(ref_ch) if ref_ch is not None else None
                except (TypeError, ValueError):
                    ref_ch = None
                self._averager = InterferogramAverager(
                    target=int(average.get("target", 1)),
                    threshold=float(average.get("threshold", 0.5)),
                    reference_channel=ref_ch,
                )
                self._average_status = None
        else:
            self._averager = None
        self._fft_dirty = True
        self._spectrum_consumed = None
        self._last_spectrum = None
        self._running = True
        self._start_fft_worker()

    def update_spectrum(self, spectrum: Optional[dict] = None) -> None:
        """Replace FFT parameters used by the simulator FFT thread."""
        self._spectrum_params = dict(spectrum) if spectrum else None

    def restart_capture(self, enabled_channels: Sequence[int]) -> None:
        """No-op recycle for the simulator (resets rate-limit clock)."""
        avg = None
        if self._averager is not None:
            avg = {
                "target": self._averager.target,
                "threshold": self._averager.threshold,
                "reference_channel": self._averager.reference_channel,
            }
        self.stop()
        self.start(
            enabled_channels,
            average=avg,
            spectrum=self._spectrum_params,
            resume_average=True,
        )

    def configure(
        self,
        sample_rate: int,
        enabled_channels: Sequence[int],
        input_range: int = 2000,
        pre_trigger_samples: int = 5000,
        post_trigger_samples: int = 15000,
        trigger: Optional[Dict[str, Any]] = None,
        max_capture_rate_hz: int = 0,
        trigger_timeout: Optional[int] = None,
    ) -> None:
        del trigger_timeout
        enabled = sorted({int(c) for c in enabled_channels if 1 <= int(c) <= 4})
        self._active_channels = enabled or [1]
        self._configured_rate = int(sample_rate)
        self._configured_input_range = max(1, int(input_range))
        self._pre, self._post = normalize_live_window(
            pre_trigger_samples, post_trigger_samples
        )
        self._trigger = normalize_trigger_settings(trigger)
        self._max_capture_rate_hz = max(0, int(max_capture_rate_hz))
        self._min_frame_interval_s = max_capture_rate_to_interval_s(
            self._max_capture_rate_hz
        )
        self._rebuild_templates()
        trig_src = (
            -1 if self._trigger["source"] == "External" else 1
        )
        cap = (
            f", max_capture={self._max_capture_rate_hz} Hz"
            if self._max_capture_rate_hz
            else ""
        )
        print(
            f"Live View configured: rate={sample_rate} S/s, "
            f"range=±{self._configured_input_range / 2:g} mV, "
            f"channels={self._active_channels}, "
            f"window={self._pre}+{self._post} samples{cap}, "
            f"trigger={_format_trigger_summary(self._trigger, trig_src)} "
            f"(simulated)"
        )

    def _rebuild_templates(self) -> None:
        pre, post = self._pre, self._post
        n = pre + post
        self._x = np.arange(-pre, post, dtype=np.float64)
        half_scale_v = (self._configured_input_range / 1000.0) / 2.0
        i = np.arange(n, dtype=np.float32)
        center = float(pre)
        self._templates = {}
        for ch in (1, 2, 3, 4):
            amp = half_scale_v * (0.55 + 0.08 * ch)
            width = 25.0 + 3.0 * ch
            carrier = 0.08 + 0.01 * ch
            u = (i - center) / max(width, 1.0)
            envelope = np.exp(-0.5 * u * u)
            carrier_term = np.cos(2.0 * np.pi * carrier * (i - center))
            self._templates[ch] = np.ascontiguousarray(
                (amp * envelope * carrier_term).astype(np.float32)
            )

    def _make_frame(self, channels: Sequence[int], now: float) -> ChannelData:
        if self._x is None or not self._templates:
            self._rebuild_templates()
        assert self._x is not None
        t = now - self._t0
        lag_jitter = int(3.0 * math.sin(t * 2.7))
        result: ChannelData = {}
        for ch in channels:
            template = self._templates.get(int(ch))
            if template is None:
                continue
            if lag_jitter == 0:
                y = template
            else:
                y = np.roll(template, lag_jitter)
            result[int(ch)] = (self._x, y)
        return result

    def average_status(self) -> Optional[AverageResult]:
        return self._average_status

    def last_spectrum(self) -> Optional[SpectrumData]:
        return self._last_spectrum

    def take_spectrum(self) -> Optional[SpectrumData]:
        spec = self._last_spectrum
        if spec is None or spec is self._spectrum_consumed:
            return None
        self._spectrum_consumed = spec
        return spec

    def full_traces(self) -> Optional[ChannelData]:
        return self._full_traces

    def full_traces_if_newer(self) -> Optional[ChannelData]:
        if self._full_traces is None:
            return None
        # Hand the UI a new pointer only when capture published a fresh stack.
        if not self._traces_published:
            return None
        self._traces_published = False
        return self._full_traces

    def _start_fft_worker(self) -> None:
        if self._fft_thread is not None:
            if self._fft_stop is not None:
                self._fft_stop.set()
            if self._fft_thread.is_alive():
                self._fft_thread.join(timeout=1.5)
            self._fft_thread = None
        self._fft_stop = threading.Event()
        self._fft_thread = threading.Thread(
            target=self._fft_loop, name="SimSpectrumFFT", daemon=True
        )
        self._fft_thread.start()

    def _fft_loop(self) -> None:
        assert self._fft_stop is not None
        while not self._fft_stop.is_set():
            if not self._spectrum_params:
                if self._fft_stop.wait(0.05):
                    break
                continue
            y = None
            avg = self._averager
            if avg is not None and avg.accepted > 0:
                ch = avg.reference_channel
                buf = avg._sum.get(int(ch)) if ch is not None else None
                if buf is None and avg._sum:
                    buf = next(iter(avg._sum.values()))
                if buf is not None:
                    raw = np.array(buf, dtype=np.float64, copy=True)
                    y = (raw / float(max(avg.accepted, 1))).astype(np.float32)
            elif self._full_traces:
                for _x, yy in self._full_traces.values():
                    y = np.array(yy, dtype=np.float32, copy=True)
                    break
            if y is None or y.size < 4:
                if self._fft_stop.wait(0.05):
                    break
                continue
            t0 = time.perf_counter()
            payload = _compute_spectrum_payload(
                y, float(self._configured_rate), self._spectrum_params
            )
            if payload is not None:
                self._last_spectrum = payload
            elapsed = time.perf_counter() - t0
            if elapsed < 0.12:
                if self._fft_stop.wait(0.12 - elapsed):
                    break

    def get_average_result(self, timeout_s: float = 5.0) -> Optional[AverageResult]:
        del timeout_s
        if self._averager is None:
            return self._average_status
        return self._averager.snapshot(include_arrays=True)

    def capture(self, enabled_channels: Sequence[int]) -> Optional[ChannelData]:
        if not getattr(self, "_running", True):
            return None
        now = time.monotonic()
        if (
            self._min_frame_interval_s > 0
            and self._last_capture_t > 0
            and (now - self._last_capture_t) < self._min_frame_interval_s
        ):
            return None
        self._last_capture_t = now

        enabled = {int(c) for c in enabled_channels if 1 <= int(c) <= 4}
        channels = [ch for ch in self._active_channels if ch in enabled]
        full = self._make_frame(channels, now)

        if self._averager is not None:
            result = self._averager.process_frame(full)
            self._average_status = result
            publish = (
                result.complete
                or self._last_plot_t <= 0.0
                or (now - self._last_plot_t) >= PLOT_PUBLISH_INTERVAL_S
            )
            if result.accepted > 0:
                full_avg = self._averager.to_channel_data()
                self._full_traces = full_avg
                self._fft_dirty = True
                if publish:
                    self._traces_published = True
                    plot = downsample_channel_data(full_avg)
                    self._last_plot = plot
                    self._last_plot_t = now
                else:
                    plot = self._last_plot
            elif publish:
                plot = self._last_plot
            else:
                plot = self._last_plot
        else:
            publish = (
                self._last_plot_t <= 0.0
                or (now - self._last_plot_t) >= PLOT_PUBLISH_INTERVAL_S
            )
            if publish:
                self._full_traces = full
                self._fft_dirty = True
                self._traces_published = True
                plot = downsample_channel_data(full)
                self._last_plot = plot
                self._last_plot_t = now
            else:
                plot = self._last_plot

        return plot or {}


def gage_extension_available() -> bool:
    """True if the real PyGage extension can be imported in a *fresh* process.

    Intentionally uses a subprocess so this UI process never loads ``PyGage``
    (and its Linux signal handlers) into the Dear PyGui address space.
    """
    import subprocess

    probe = (
        "import PyGage; assert all(hasattr(PyGage, n) for n in "
        "('Initialize', 'GetSystem', 'TransferData', 'Commit'))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def create_live_view_engine(has_gage: bool) -> object:
    if has_gage and gage_extension_available():
        return LiveViewEngine()
    return SimulatedLiveViewEngine()
