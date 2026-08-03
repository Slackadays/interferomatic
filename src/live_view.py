"""Live View acquisition for MONITOR mode using the Gage CompuScope API.

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
"""

from __future__ import annotations

import atexit
import math
import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict

import numpy as np

GAGE_API_DIR = Path(__file__).resolve().parent.parent / "gage_api"
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

# Acquisition trigger timeout (100 ns units). Used as a safety net when the
# channel edge never arrives (quiet input) so Live View still refreshes.
# 100 ms = 1_000_000 * 100 ns.
LIVE_TRIGGER_TIMEOUT = 1_000_000

CAPTURE_WAIT_TIMEOUT_S = 2.0
STATUS_POLL_INTERVAL_S = 0.0001
# ~15 Hz display update; slower than the old 33 Hz loop to reduce driver stress.
WORKER_MIN_FRAME_INTERVAL_S = 0.065
POST_COMMIT_READY_TIMEOUT_S = 30.0

# How many consecutive child failures before surfacing an error to the UI.
MAX_CHILD_RESTARTS = 5

# Soft-reset (Abort) the board every N successful frames to clear driver state.
SOFT_RESET_EVERY_N_FRAMES = 50

# CSE1642 reports CAPS_DEPTH_INCREMENT=32; use that as the default alignment
# so UI values commit cleanly before the child queries the real cap.
# Exported as LIVE_DEPTH_INCREMENT for the UI spinner step (must match).
_DEFAULT_DEPTH_INCREMENT = 32
LIVE_DEPTH_INCREMENT = _DEFAULT_DEPTH_INCREMENT

ChannelData = Dict[int, Tuple[List[float], List[float]]]


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
    samples = np.asarray(buffer, dtype=np.float64)
    scale = chan["InputRange"] / 2000.0
    offset = chan["DcOffset"] / 1000.0
    sample_offset = float(acq["SampleOffset"])
    sample_res = float(acq["SampleResolution"])
    if sample_res == 0:
        sample_res = 1.0
    return ((sample_offset - samples) / sample_res) * scale + offset


def _child_abort(handle) -> None:
    import PyGage

    try:
        PyGage.AbortCapture(handle)
    except Exception:
        pass


def _child_wait_ready(handle, timeout_s: float, stop_event: mp.synchronize.Event) -> None:
    import PyGage
    import GageConstants as gc

    deadline = time.monotonic() + timeout_s
    forced = False
    while not stop_event.is_set():
        status = PyGage.GetStatus(handle)
        if status < 0:
            _child_abort(handle)
            raise RuntimeError(PyGage.GetErrorString(status))
        if status == gc.ACQ_STATUS_READY:
            return
        now = time.monotonic()
        if now > deadline:
            if not forced:
                force = PyGage.ForceCapture(handle)
                forced = True
                if force < 0:
                    _child_abort(handle)
                    raise RuntimeError(
                        f"Capture timed out ({PyGage.GetErrorString(force)})"
                    )
                deadline = now + timeout_s
                time.sleep(STATUS_POLL_INTERVAL_S)
                continue
            _child_abort(handle)
            raise RuntimeError("Capture timed out waiting for ACQ_STATUS_READY")
        time.sleep(STATUS_POLL_INTERVAL_S)
    _child_abort(handle)
    raise RuntimeError("stopped")


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
) -> List[int]:
    import PyGage
    import GageSupport as gs
    import GageConstants as gc

    trigger_settings = normalize_trigger_settings(trigger)

    _child_abort(handle)

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
    acq["TriggerTimeout"] = LIVE_TRIGGER_TIMEOUT
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

    deadline = time.monotonic() + POST_COMMIT_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        st = PyGage.GetStatus(handle)
        if st < 0:
            raise RuntimeError(PyGage.GetErrorString(st))
        if st == gc.ACQ_STATUS_READY:
            break
        time.sleep(0.01)
    else:
        raise RuntimeError("Timed out waiting for board READY after Commit")

    # Transfer window relative to trigger: [-pre, +post).
    app_config["StartPosition"] = -pre
    app_config["TransferLength"] = total
    app_config["PreTriggerSamples"] = pre
    app_config["PostTriggerSamples"] = post
    app_config["TriggerSource"] = int(trig_source)
    app_config["TriggerSettings"] = dict(trigger_settings)

    active_out = [ch for ch in active if ch in enabled] or active[:1] or [1]
    return active_out


def _child_transfer(handle, app_config: dict, channels: Sequence[int]) -> ChannelData:
    import PyGage
    import GageConstants as gc

    acq = PyGage.GetAcquisitionConfig(handle)
    if not isinstance(acq, dict):
        raise RuntimeError(PyGage.GetErrorString(acq))

    start = int(app_config.get("StartPosition", 0))
    length = int(app_config.get("TransferLength", 2040))
    min_start = acq["TriggerDelay"] + acq["Depth"] - acq["SegmentSize"]
    if start < min_start:
        start = int(min_start)
    max_length = acq["TriggerDelay"] + acq["Depth"] - min_start
    if length > max_length:
        length = int(max_length)

    result: ChannelData = {}
    for ch in channels:
        transferred = PyGage.TransferData(
            handle, ch, gc.TxMODE_DEFAULT, 1, start, length + 64
        )
        if isinstance(transferred, int):
            raise RuntimeError(
                f"Transfer channel {ch}: {PyGage.GetErrorString(transferred)}"
            )
        buf, actual_start, actual_length = transferred
        chan = PyGage.GetChannelConfig(handle, ch)
        if not isinstance(chan, dict):
            raise RuntimeError(PyGage.GetErrorString(chan))
        volts = raw_to_volts(buf, acq, chan)
        n = min(int(actual_length), length, len(volts))
        # Prefer trigger-relative sample indices for the plot (0 = trigger).
        x0 = int(actual_start) if actual_start is not None else start
        result[ch] = (
            list(range(x0, x0 + n)),
            volts[:n].tolist(),
        )
    return result


def _child_capture_one(handle, app_config: dict, channels: Sequence[int],
                       stop_event: mp.synchronize.Event) -> Optional[ChannelData]:
    import PyGage
    import GageConstants as gc

    status = PyGage.GetStatus(handle)
    if status < 0:
        raise RuntimeError(PyGage.GetErrorString(status))
    if status == gc.ACQ_STATUS_BUSY_CALIB:
        return None
    if status != gc.ACQ_STATUS_READY:
        _child_abort(handle)
        time.sleep(0.001)

    status = PyGage.StartCapture(handle)
    if status < 0:
        raise RuntimeError(PyGage.GetErrorString(status))

    _child_wait_ready(handle, CAPTURE_WAIT_TIMEOUT_S, stop_event)
    if stop_event.is_set():
        return None
    return _child_transfer(handle, app_config, channels)


def _live_view_child_main(
    cmd_q: mp.Queue,
    frame_q: mp.Queue,
    event_q: mp.Queue,
    stop_event: mp.synchronize.Event,
    ini_path: str,
) -> None:
    """Child process entry: exclusive owner of the Gage system handle."""
    # Ensure gage_api is importable in the spawned process.
    if str(GAGE_API_DIR) not in sys.path:
        sys.path.insert(0, str(GAGE_API_DIR))

    import PyGage
    import GageSupport as gs

    handle = None
    system_info = None
    app_config = None
    active_channels: List[int] = [1]
    capturing = False
    channels: List[int] = [1]
    frames_since_reset = 0
    # Last successful configure:
    # (rate, enabled, range_mv, pre, post, trigger_settings_dict).
    last_config: Optional[Tuple[int, List[int], int, int, int, dict]] = None

    def emit(kind: str, payload=None) -> None:
        try:
            event_q.put_nowait((kind, payload))
        except Exception:
            pass

    def open_board() -> None:
        nonlocal handle, system_info, app_config
        if handle is not None:
            try:
                _child_abort(handle)
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
            },
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
                    # (op, rate, enabled, range_mv, pre, post[, trigger_dict])
                    sample_rate = cmd[1]
                    enabled = cmd[2]
                    range_mv = cmd[3]
                    pre_samples = cmd[4]
                    post_samples = cmd[5]
                    trigger_in = cmd[6] if len(cmd) > 6 else None
                    try:
                        capturing = False
                        trig_settings = normalize_trigger_settings(trigger_in)
                        last_config = (
                            int(sample_rate),
                            list(enabled),
                            int(range_mv),
                            int(pre_samples),
                            int(post_samples),
                            dict(trig_settings),
                        )
                        active_channels = _child_configure(
                            handle,
                            system_info,
                            ini_path,
                            last_config[0],
                            last_config[1],
                            last_config[2],
                            last_config[3],
                            last_config[4],
                            app_config,
                            trigger=last_config[5],
                        )
                        channels = list(active_channels)
                        frames_since_reset = 0
                        emit(
                            "configured",
                            {
                                "rate": sample_rate,
                                "channels": active_channels,
                                "range_mv": range_mv,
                                "pre": last_config[3],
                                "post": last_config[4],
                                "trigger_source": app_config.get(
                                    "TriggerSource",
                                    active_channels[0] if active_channels else 1,
                                ),
                                "trigger": dict(trig_settings),
                            },
                        )
                    except Exception as e:
                        emit("error", str(e))
                elif op == "start":
                    _, enabled = cmd
                    enabled_set = {int(c) for c in enabled}
                    channels = [c for c in active_channels if c in enabled_set] or list(
                        active_channels[:1]
                    )
                    capturing = True
                elif op == "halt":
                    capturing = False
                    _child_abort(handle)
                elif op == "channels":
                    _, enabled = cmd
                    enabled_set = {int(c) for c in enabled}
                    channels = [c for c in active_channels if c in enabled_set] or list(
                        active_channels[:1]
                    )

            if not capturing or stop_event.is_set():
                continue

            t0 = time.monotonic()
            try:
                frame = _child_capture_one(handle, app_config, channels, stop_event)
            except Exception as e:
                if stop_event.is_set() or "stopped" in str(e).lower():
                    break
                # Recover by re-opening the board and re-applying config once.
                try:
                    open_board()
                    if last_config is not None:
                        active_channels = _child_configure(
                            handle,
                            system_info,
                            ini_path,
                            last_config[0],
                            last_config[1],
                            last_config[2],
                            last_config[3],
                            last_config[4],
                            app_config,
                            trigger=last_config[5] if len(last_config) > 5 else None,
                        )
                        channels = [c for c in active_channels if c in set(channels)] or list(
                            active_channels[:1]
                        )
                    frames_since_reset = 0
                    continue
                except Exception as e2:
                    emit("error", f"{e} (recover failed: {e2})")
                    capturing = False
                    continue

            if frame:
                frames_since_reset += 1
                if frames_since_reset >= SOFT_RESET_EVERY_N_FRAMES:
                    _child_abort(handle)
                    time.sleep(0.002)
                    frames_since_reset = 0
                # Keep only the newest frame in the parent.
                try:
                    while True:
                        frame_q.get_nowait()
                except Exception:
                    pass
                try:
                    frame_q.put_nowait(frame)
                except Exception:
                    pass

            elapsed = time.monotonic() - t0
            delay = WORKER_MIN_FRAME_INTERVAL_S - elapsed
            if delay > 0 and not stop_event.is_set():
                time.sleep(delay)

    except Exception as e:
        emit("error", str(e))
    finally:
        if handle is not None:
            try:
                _child_abort(handle)
            except Exception:
                pass
            try:
                PyGage.FreeSystem(handle)
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
        self._configured_trigger: Optional[Tuple] = None
        self._running = False
        self._child_restarts = 0
        self._board_name = "Gage"
        # (rate, channels, range_mv, pre, post, trigger_key, trigger_dict)
        self._pending_config: Optional[
            Tuple[int, Tuple[int, ...], int, int, int, Tuple, dict]
        ] = None
        self._last_error: Optional[str] = None
        self._config_ack = False
        self._last_sent_channels: Optional[Tuple[int, ...]] = None
        self._atexit_registered = False

    @property
    def available(self) -> bool:
        return self._available

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
        time.sleep(0.3)
        self._cmd_q = self._ctx.Queue()
        self._frame_q = self._ctx.Queue(maxsize=2)
        self._event_q = self._ctx.Queue()
        self._stop_event = self._ctx.Event()
        self._available = False
        self._last_error = None
        self._proc = self._ctx.Process(
            target=_live_view_child_main,
            args=(
                self._cmd_q,
                self._frame_q,
                self._event_q,
                self._stop_event,
                str(self.ini_path),
            ),
            name="LiveViewGageChild",
            daemon=True,
        )
        self._proc.start()

    def _stop_child(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
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
        self._cmd_q = None
        self._frame_q = None
        self._event_q = None
        self._stop_event = None
        self._available = False
        self._running = False
        # Brief pause so CsRm releases the board after process death.
        time.sleep(0.2)

    def close(self) -> None:
        self._stop_child()
        self._configured_rate = None
        self._configured_channels = None
        self._configured_input_range = None
        self._configured_pre = None
        self._configured_post = None
        self._configured_trigger = None

    def stop(self) -> None:
        """Halt capture but keep the child (and board handle) alive."""
        self._running = False
        if self._cmd_q is not None:
            try:
                self._cmd_q.put_nowait(("halt",))
            except Exception:
                pass
        self._drain_frames()

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
                if isinstance(payload, dict):
                    self._board_name = str(payload.get("BoardName") or "Gage")
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
                    print(
                        f"Live View configured: rate={payload.get('rate')} S/s, "
                        f"range=±{(payload.get('range_mv') or 0) / 2:g} mV, "
                        f"channels={payload.get('channels')}{window}{trig}"
                    )
            elif kind == "error":
                self._last_error = str(payload)
            elif kind == "exited":
                self._available = False

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
            rate, enabled, range_mv, pre, post, _trig_key, trig_dict = self._pending_config
            self._send(
                ("configure", rate, list(enabled), range_mv, pre, post, trig_dict)
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
            self._configured_trigger = _trig_key

        if self._running and self._configured_channels is not None:
            self._send(("start", list(self._configured_channels)))

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

        key = (int(sample_rate), enabled, range_mv, pre, post, trig_key, dict(trig_settings))
        self._pending_config = key
        if (
            int(sample_rate) == self._configured_rate
            and enabled == self._configured_channels
            and range_mv == self._configured_input_range
            and pre == self._configured_pre
            and post == self._configured_post
            and trig_key == self._configured_trigger
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
                dict(trig_settings),
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
        self._configured_trigger = trig_key
        self._child_restarts = 0  # healthy configure resets restart budget

    def start(self, enabled_channels: Sequence[int]) -> None:
        enabled = [int(c) for c in enabled_channels if 1 <= int(c) <= 4] or [1]
        self._ensure_child()
        self._running = True
        self._send(("start", enabled))

    def capture(self, enabled_channels: Sequence[int]) -> Optional[ChannelData]:
        """Return the latest frame from the child, or None if none is ready."""
        enabled = [int(c) for c in enabled_channels if 1 <= int(c) <= 4]
        if not enabled:
            self.stop()
            return {}

        self._drain_events()
        if self._last_error:
            err = self._last_error
            self._last_error = None
            self._running = False
            raise RuntimeError(err)

        if self._proc is not None and not self._proc.is_alive():
            # Child crashed (e.g. driver SIGSEGV) — restart and continue.
            code = self._proc.exitcode
            print(f"Gage Live View child exited (code={code}); recovering...")
            was_running = self._running
            self._ensure_child()
            if was_running:
                self.start(enabled)
            return None

        enabled_key = tuple(enabled)
        if self._running:
            # Keep channel selection in sync (only when it changes).
            if enabled_key != self._last_sent_channels:
                try:
                    self._send(("channels", enabled))
                    self._last_sent_channels = enabled_key
                except Exception:
                    pass
        else:
            self.start(enabled)
            self._last_sent_channels = enabled_key

        latest: Optional[ChannelData] = None
        if self._frame_q is not None:
            while True:
                try:
                    latest = self._frame_q.get_nowait()
                except Exception:
                    break
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

    @property
    def available(self) -> bool:
        return self._available

    def open(self) -> None:
        self._available = True
        print("Gage-less Live View: using simulated waveforms")

    def close(self) -> None:
        self._available = False

    def stop(self) -> None:
        pass

    def start(self, enabled_channels: Sequence[int]) -> None:
        pass

    def configure(
        self,
        sample_rate: int,
        enabled_channels: Sequence[int],
        input_range: int = 2000,
        pre_trigger_samples: int = 5000,
        post_trigger_samples: int = 15000,
        trigger: Optional[Dict[str, Any]] = None,
    ) -> None:
        enabled = sorted({int(c) for c in enabled_channels if 1 <= int(c) <= 4})
        self._active_channels = enabled or [1]
        self._configured_rate = int(sample_rate)
        self._configured_input_range = max(1, int(input_range))
        self._pre, self._post = normalize_live_window(
            pre_trigger_samples, post_trigger_samples
        )
        self._trigger = normalize_trigger_settings(trigger)
        trig_src = (
            -1 if self._trigger["source"] == "External" else 1
        )
        print(
            f"Live View configured: rate={sample_rate} S/s, "
            f"range=±{self._configured_input_range / 2:g} mV, "
            f"channels={self._active_channels}, "
            f"window={self._pre}+{self._post} samples, "
            f"trigger={_format_trigger_summary(self._trigger, trig_src)} "
            f"(simulated)"
        )

    def capture(self, enabled_channels: Sequence[int]) -> Optional[ChannelData]:
        enabled = {int(c) for c in enabled_channels if 1 <= int(c) <= 4}
        channels = [ch for ch in self._active_channels if ch in enabled]
        pre, post = self._pre, self._post
        n = pre + post
        t = time.monotonic() - self._t0
        half_scale_v = (self._configured_input_range / 1000.0) / 2.0
        # Trigger at sample index 0; pre-trigger is negative indices.
        x = list(range(-pre, post))
        result: ChannelData = {}
        for ch in channels:
            freq = 3.0 + ch
            phase = t * (1.0 + 0.2 * ch)
            amp = half_scale_v * (0.55 + 0.08 * ch)
            y = [
                amp * math.sin(2 * math.pi * freq * (i / max(n, 1)) + phase)
                + 0.05
                * half_scale_v
                * math.sin(2 * math.pi * 40 * (i / max(n, 1)) + phase * 0.3)
                for i in range(n)
            ]
            result[ch] = (x, y)
        return result


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
