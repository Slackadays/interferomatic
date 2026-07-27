"""Live View acquisition for MONITOR mode using the Gage CompuScope API.

Follows the same configure → commit → capture → transfer pattern as
gage_api/GageAcquire.py, but loops for a continuous scope-style display
and converts raw samples to volts for plotting.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

GAGE_API_DIR = Path(__file__).resolve().parent.parent / "gage_api"
if str(GAGE_API_DIR) not in sys.path:
    sys.path.insert(0, str(GAGE_API_DIR))

# Default INI lives next to this package (src/Acquire.ini).
DEFAULT_INI = Path(__file__).resolve().parent / "Acquire.ini"

# Trigger timeout for free-running live view (100 ns units). 1 ms.
LIVE_TRIGGER_TIMEOUT = 10_000

# How long to wait for ACQ_STATUS_READY before giving up (seconds).
CAPTURE_WAIT_TIMEOUT_S = 2.0

ChannelData = Dict[int, Tuple[List[float], List[float]]]  # ch -> (x samples, y volts)


def _mode_for_channels(enabled: Sequence[int]) -> int:
    """Pick the smallest Gage acquisition mode that covers *enabled* channels."""
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
    """Channel indices the card will acquire in this mode.

    On a single board the active set is the first N channels for the mode
    (Single→1, Dual→1–2, Quad→1–4). Multi-board systems fall back to the
    Gage SDK CalculateChannelIndexIncrement stride used in the samples.
    """
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
    """Convert raw ADC counts to volts (same formula as GageSupport.SaveVoltageFile)."""
    samples = np.asarray(buffer, dtype=np.float64)
    scale = chan["InputRange"] / 2000.0
    offset = chan["DcOffset"] / 1000.0
    sample_offset = float(acq["SampleOffset"])
    sample_res = float(acq["SampleResolution"])
    if sample_res == 0:
        sample_res = 1.0
    return ((sample_offset - samples) / sample_res) * scale + offset


class LiveViewEngine:
    """Owns a Gage system handle and performs one-shot captures for Live View."""

    def __init__(self, ini_path: Optional[Path] = None):
        self.ini_path = Path(ini_path) if ini_path else DEFAULT_INI
        self.handle: Optional[int] = None
        self.system_info: Optional[dict] = None
        self.app_config: Optional[dict] = None
        self._configured_rate: Optional[int] = None
        self._configured_channels: Optional[Tuple[int, ...]] = None
        self._active_channels: List[int] = [1]
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def open(self) -> None:
        """Initialize the Gage driver and open the first system."""
        import PyGage

        status = PyGage.Initialize()
        if status < 0:
            raise RuntimeError(PyGage.GetErrorString(status))

        handle = PyGage.GetSystem(0, 0, 0, 0)
        if handle < 0:
            raise RuntimeError(PyGage.GetErrorString(handle))

        system_info = PyGage.GetSystemInfo(handle)
        if not isinstance(system_info, dict):
            PyGage.FreeSystem(handle)
            raise RuntimeError(PyGage.GetErrorString(system_info))

        import GageSupport as gs

        app, _ = gs.LoadApplicationConfiguration(str(self.ini_path))

        self.handle = handle
        self.system_info = system_info
        self.app_config = app
        self._available = True
        print(
            f"Gage Live View ready: {system_info.get('BoardName', 'unknown')} "
            f"({system_info.get('ChannelCount', '?')} ch)"
        )

    def close(self) -> None:
        """Release the Gage system if open."""
        if self.handle is None:
            return
        try:
            import PyGage

            try:
                PyGage.AbortCapture(self.handle)
            except Exception:
                pass
            PyGage.FreeSystem(self.handle)
        finally:
            self.handle = None
            self.system_info = None
            self._available = False
            self._configured_rate = None
            self._configured_channels = None

    def configure(self, sample_rate: int, enabled_channels: Sequence[int]) -> None:
        """Load INI defaults, apply UI sample rate / channel mode, and Commit."""
        if self.handle is None or self.system_info is None:
            raise RuntimeError("Gage system is not open")

        import PyGage
        import GageSupport as gs
        import GageConstants as gc

        enabled = sorted({int(c) for c in enabled_channels if 1 <= int(c) <= 4})
        if not enabled:
            enabled = [1]

        key = (sample_rate, tuple(enabled))
        if key == (self._configured_rate, self._configured_channels):
            return

        ini = str(self.ini_path)
        acq, sts = gs.LoadAcquisitionConfiguration(self.handle, ini)
        if not isinstance(acq, dict):
            raise RuntimeError(PyGage.GetErrorString(acq))

        mode = _mode_for_channels(enabled)
        acq["Mode"] = mode
        acq["SampleRate"] = int(sample_rate)
        # Free-run: auto-trigger after a short timeout so Live View keeps updating.
        acq["TriggerTimeout"] = LIVE_TRIGGER_TIMEOUT
        acq["SegmentCount"] = 1

        status = PyGage.SetAcquisitionConfig(self.handle, acq)
        if status < 0:
            raise RuntimeError(PyGage.GetErrorString(status))

        channel_count = int(self.system_info["ChannelCount"])
        board_count = int(self.system_info["BoardCount"])
        active = _active_channel_indices(mode, channel_count, board_count)

        for ch in active:
            chan, _ = gs.LoadChannelConfiguration(self.handle, ch, ini)
            if isinstance(chan, dict) and chan:
                status = PyGage.SetChannelConfig(self.handle, ch, chan)
                if status < 0:
                    raise RuntimeError(PyGage.GetErrorString(status))

        # One trigger engine is enough for basic Live View.
        trig, _ = gs.LoadTriggerConfiguration(self.handle, 1, ini)
        if isinstance(trig, dict) and trig:
            status = PyGage.SetTriggerConfig(self.handle, 1, trig)
            if status < 0:
                raise RuntimeError(PyGage.GetErrorString(status))

        status = PyGage.Commit(self.handle)
        if status < 0:
            raise RuntimeError(PyGage.GetErrorString(status))

        # Only transfer channels that are both active on the card and requested in the UI.
        self._active_channels = [ch for ch in active if ch in enabled]
        if not self._active_channels:
            self._active_channels = active[:1] or [1]

        self._configured_rate = sample_rate
        self._configured_channels = tuple(enabled)
        print(
            f"Live View configured: rate={sample_rate} S/s, mode={mode}, "
            f"channels={self._active_channels}"
        )

    def capture(self, enabled_channels: Sequence[int]) -> ChannelData:
        """Run one capture and return sample-index / volt arrays for enabled channels."""
        if self.handle is None or self.app_config is None:
            raise RuntimeError("Gage system is not open")

        import PyGage
        import GageConstants as gc

        enabled = {int(c) for c in enabled_channels if 1 <= int(c) <= 4}
        channels = [ch for ch in self._active_channels if ch in enabled]
        if not channels:
            return {}

        status = PyGage.StartCapture(self.handle)
        if status < 0:
            raise RuntimeError(PyGage.GetErrorString(status))

        deadline = time.monotonic() + CAPTURE_WAIT_TIMEOUT_S
        status = PyGage.GetStatus(self.handle)
        while status != gc.ACQ_STATUS_READY:
            if time.monotonic() > deadline:
                # Force the trigger so Live View does not hang on a quiet input.
                force = PyGage.ForceCapture(self.handle)
                if force < 0:
                    PyGage.AbortCapture(self.handle)
                    raise RuntimeError(
                        f"Capture timed out ({PyGage.GetErrorString(force)})"
                    )
                deadline = time.monotonic() + CAPTURE_WAIT_TIMEOUT_S
            status = PyGage.GetStatus(self.handle)
            if status < 0:
                raise RuntimeError(PyGage.GetErrorString(status))

        acq = PyGage.GetAcquisitionConfig(self.handle)
        if not isinstance(acq, dict):
            raise RuntimeError(PyGage.GetErrorString(acq))

        start = int(self.app_config.get("StartPosition", 0))
        length = int(self.app_config.get("TransferLength", 2040))

        # Validate transfer window (same checks as GageAcquire.save_data_to_file).
        min_start = acq["TriggerDelay"] + acq["Depth"] - acq["SegmentSize"]
        if start < min_start:
            start = int(min_start)
        max_length = acq["TriggerDelay"] + acq["Depth"] - min_start
        if length > max_length:
            length = int(max_length)

        result: ChannelData = {}
        for ch in channels:
            # +64 padding in case the driver adjusts the transfer length (Gage samples).
            transferred = PyGage.TransferData(
                self.handle, ch, gc.TxMODE_DEFAULT, 1, start, length + 64
            )
            if isinstance(transferred, int):
                raise RuntimeError(
                    f"Transfer channel {ch}: {PyGage.GetErrorString(transferred)}"
                )

            buf, actual_start, actual_length = transferred
            chan = PyGage.GetChannelConfig(self.handle, ch)
            if not isinstance(chan, dict):
                raise RuntimeError(PyGage.GetErrorString(chan))

            volts = raw_to_volts(buf, acq, chan)
            # Prefer the requested length when the driver returned extra padding.
            n = min(int(actual_length), length, len(volts))
            y = volts[:n].tolist()
            x = list(range(int(actual_start), int(actual_start) + n))
            result[ch] = (x, y)

        return result


class SimulatedLiveViewEngine:
    """Synthetic waveforms so Live View can be exercised without a Gage card."""

    def __init__(self):
        self._available = True
        self._t0 = time.monotonic()
        self._configured_rate = 200_000_000
        self._active_channels = [1]

    @property
    def available(self) -> bool:
        return self._available

    def open(self) -> None:
        self._available = True
        print("Gage-less Live View: using simulated waveforms")

    def close(self) -> None:
        self._available = False

    def configure(self, sample_rate: int, enabled_channels: Sequence[int]) -> None:
        enabled = sorted({int(c) for c in enabled_channels if 1 <= int(c) <= 4})
        self._active_channels = enabled or [1]
        self._configured_rate = int(sample_rate)

    def capture(self, enabled_channels: Sequence[int]) -> ChannelData:
        enabled = {int(c) for c in enabled_channels if 1 <= int(c) <= 4}
        channels = [ch for ch in self._active_channels if ch in enabled]
        n = 2040
        t = time.monotonic() - self._t0
        x = list(range(n))
        result: ChannelData = {}
        for ch in channels:
            freq = 3.0 + ch  # distinct tone per channel
            phase = t * (1.0 + 0.2 * ch)
            amp = 0.4 + 0.1 * ch
            y = [
                amp * math.sin(2 * math.pi * freq * (i / n) + phase)
                + 0.05 * math.sin(2 * math.pi * 40 * (i / n) + phase * 0.3)
                for i in range(n)
            ]
            result[ch] = (x, y)
        return result


def gage_extension_available() -> bool:
    """True when the built PyGage extension (not just the source folder) is importable."""
    try:
        import PyGage
        return all(
            hasattr(PyGage, name)
            for name in ("Initialize", "GetSystem", "TransferData", "Commit")
        )
    except ImportError:
        return False


def create_live_view_engine(has_gage: bool) -> object:
    """Return a real Gage engine when the card is available, else a simulator."""
    if has_gage and gage_extension_available():
        return LiveViewEngine()
    return SimulatedLiveViewEngine()
