"""Phase-aligned interferogram averaging via normalized cross-correlation.

Each accepted capture is cross-correlated against the running average. The lag
at the peak of the correlation vector is used to circularly shift the new
trace so its phase matches the average before it is folded in. Traces whose
peak correlation falls below a threshold are rejected (e.g. empty triggers or
glitches), so the final stack SNR improves roughly as sqrt(N).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# ChannelData-compatible: channel -> (x samples, y volts)
ChannelData = Dict[int, Tuple[List[float], List[float]]]


def circular_cross_correlation(
    signal: np.ndarray, reference: np.ndarray
) -> np.ndarray:
    """Return the normalized circular cross-correlation vector.

    ``result[k]`` is the Pearson-like coefficient when *signal* is circularly
    shifted by *k* samples relative to *reference* (zero-mean, full-vector
    norms). Length equals ``len(signal)``; values are in approximately
    ``[-1, 1]``.
    """
    sig = np.asarray(signal, dtype=np.float64).ravel()
    ref = np.asarray(reference, dtype=np.float64).ravel()
    if sig.size != ref.size:
        raise ValueError(
            f"signal and reference length mismatch: {sig.size} vs {ref.size}"
        )
    n = sig.size
    if n == 0:
        return np.zeros(0, dtype=np.float64)

    sig_zm = sig - sig.mean()
    ref_zm = ref - ref.mean()
    denom = float(np.linalg.norm(sig_zm) * np.linalg.norm(ref_zm))
    if denom < 1e-15:
        return np.zeros(n, dtype=np.float64)

    # corr[k] = sum_i sig_zm[i] * ref_zm[(i - k) mod n]
    corr = np.fft.ifft(np.fft.fft(sig_zm) * np.conj(np.fft.fft(ref_zm))).real
    return corr / denom


def best_alignment_lag(corr: np.ndarray) -> Tuple[int, float]:
    """Return ``(lag, peak)`` for a circular correlation vector.

    *lag* is the shift such that ``np.roll(signal, -lag)`` aligns with the
    reference used to form *corr*. Peak is ``corr[lag]`` (wrapped index).
    """
    if corr.size == 0:
        return 0, 0.0
    peak_idx = int(np.argmax(corr))
    peak = float(corr[peak_idx])
    n = corr.size
    # Prefer the smallest absolute lag when the peak is near the wrap boundary.
    lag = peak_idx if peak_idx <= n // 2 else peak_idx - n
    return lag, peak


def align_trace(signal: np.ndarray, lag: int) -> np.ndarray:
    """Circularly shift *signal* by *-lag* samples to match the reference."""
    sig = np.asarray(signal, dtype=np.float64).ravel()
    if lag == 0 or sig.size == 0:
        return sig.copy()
    return np.roll(sig, -int(lag))


@dataclass
class AverageResult:
    """Snapshot of averaging progress."""

    accepted: int = 0
    rejected: int = 0
    target: int = 0
    last_peak_corr: float = 0.0
    last_lag: int = 0
    complete: bool = False
    # channel -> averaged y (only when accepted > 0)
    averages: Dict[int, np.ndarray] = field(default_factory=dict)
    x: Optional[np.ndarray] = None


class InterferogramAverager:
    """Accumulate phase-aligned interferograms until *target* accepts."""

    def __init__(
        self,
        target: int,
        threshold: float,
        reference_channel: Optional[int] = None,
    ):
        self.target = max(1, int(target))
        self.threshold = float(np.clip(threshold, 0.0, 1.0))
        self.reference_channel = reference_channel
        self.accepted = 0
        self.rejected = 0
        self.last_peak_corr = 0.0
        self.last_lag = 0
        self._sum: Dict[int, np.ndarray] = {}
        self._x: Optional[np.ndarray] = None
        self._length: Optional[int] = None
        self._channels: Tuple[int, ...] = ()

    @property
    def complete(self) -> bool:
        return self.accepted >= self.target

    def reset(self) -> None:
        self.accepted = 0
        self.rejected = 0
        self.last_peak_corr = 0.0
        self.last_lag = 0
        self._sum.clear()
        self._x = None
        self._length = None
        self._channels = ()

    def averages(self) -> Dict[int, np.ndarray]:
        if self.accepted < 1:
            return {}
        n = float(self.accepted)
        return {ch: (buf / n) for ch, buf in self._sum.items()}

    def to_channel_data(self) -> ChannelData:
        """Current averages in Live View ``ChannelData`` form."""
        if self._x is None or self.accepted < 1:
            return {}
        x_list = self._x.tolist()
        out: ChannelData = {}
        for ch, avg in self.averages().items():
            out[ch] = (x_list, avg.tolist())
        return out

    def snapshot(self) -> AverageResult:
        return AverageResult(
            accepted=self.accepted,
            rejected=self.rejected,
            target=self.target,
            last_peak_corr=self.last_peak_corr,
            last_lag=self.last_lag,
            complete=self.complete,
            averages=self.averages(),
            x=None if self._x is None else self._x.copy(),
        )

    def process_frame(self, channel_data: ChannelData) -> AverageResult:
        """Fold one multi-channel capture into the average if it correlates.

        The first accepted frame seeds the stack (no threshold check). Later
        frames are aligned to the running average using the peak lag of the
        circular cross-correlation on the reference channel; the same lag is
        applied to every channel in the frame so relative timing is preserved.
        """
        if self.complete:
            return self.snapshot()

        if not channel_data:
            return self.snapshot()

        channels = tuple(sorted(int(ch) for ch in channel_data.keys()))
        if not channels:
            return self.snapshot()

        # Pick reference channel for lag / threshold (stable across run).
        if self.reference_channel is not None and self.reference_channel in channel_data:
            ref_ch = int(self.reference_channel)
        elif self._channels:
            ref_ch = self._channels[0] if self._channels[0] in channel_data else channels[0]
        else:
            ref_ch = channels[0]

        x_ref, y_ref = channel_data[ref_ch]
        y_ref_arr = np.asarray(y_ref, dtype=np.float64).ravel()
        x_arr = np.asarray(x_ref, dtype=np.float64).ravel()
        n = y_ref_arr.size
        if n < 2:
            self.rejected += 1
            return self.snapshot()

        # Seed with the first frame.
        if self.accepted == 0:
            self._length = n
            self._x = x_arr.copy()
            self._channels = channels
            self.reference_channel = ref_ch
            for ch in channels:
                _x, y = channel_data[ch]
                y_arr = np.asarray(y, dtype=np.float64).ravel()
                if y_arr.size != n:
                    # Pad/truncate to reference length so a partial frame can seed.
                    if y_arr.size < n:
                        y_arr = np.pad(y_arr, (0, n - y_arr.size))
                    else:
                        y_arr = y_arr[:n]
                self._sum[ch] = y_arr.copy()
            self.accepted = 1
            self.last_peak_corr = 1.0
            self.last_lag = 0
            return self.snapshot()

        # Length must match the stack.
        if self._length is not None and n != self._length:
            self.rejected += 1
            return self.snapshot()

        avg_ref = self._sum[ref_ch] / float(self.accepted)
        corr = circular_cross_correlation(y_ref_arr, avg_ref)
        lag, peak = best_alignment_lag(corr)
        self.last_peak_corr = peak
        self.last_lag = lag

        if peak < self.threshold:
            self.rejected += 1
            return self.snapshot()

        # Accept: align every channel with the same lag, then accumulate.
        for ch in channels:
            if ch not in channel_data:
                continue
            _x, y = channel_data[ch]
            y_arr = np.asarray(y, dtype=np.float64).ravel()
            if y_arr.size != n:
                if y_arr.size < n:
                    y_arr = np.pad(y_arr, (0, n - y_arr.size))
                else:
                    y_arr = y_arr[:n]
            aligned = align_trace(y_arr, lag)
            if ch in self._sum:
                self._sum[ch] += aligned
            else:
                # New channel mid-run: seed with current average length zeros
                # then add (rare; usually channel set is fixed at start).
                self._sum[ch] = aligned * float(self.accepted) + aligned
        self.accepted += 1
        return self.snapshot()


def save_averaged_interferograms(
    path: str | Path,
    result: AverageResult,
    *,
    sample_rate: int = 0,
    threshold: float = 0.0,
    metadata: Optional[dict] = None,
) -> Path:
    """Write averaged traces to *path* (``.npz`` if no suffix)."""
    out = Path(path)
    if not str(out):
        raise ValueError("save path is empty")
    if out.suffix == "":
        out = out.with_suffix(".npz")
    out.parent.mkdir(parents=True, exist_ok=True)

    if result.x is None or not result.averages:
        raise ValueError("no averaged data to save")

    payload = {
        "x": np.asarray(result.x, dtype=np.float64),
        "accepted": np.int64(result.accepted),
        "rejected": np.int64(result.rejected),
        "target": np.int64(result.target),
        "threshold": np.float64(threshold),
        "sample_rate": np.int64(sample_rate),
    }
    for ch, y in sorted(result.averages.items()):
        payload[f"ch{int(ch)}"] = np.asarray(y, dtype=np.float64)
    if metadata:
        for key, value in metadata.items():
            if key in payload:
                continue
            try:
                payload[str(key)] = np.asarray(value)
            except Exception:
                payload[str(key)] = np.asarray(str(value))
    np.savez_compressed(out, **payload)
    return out
