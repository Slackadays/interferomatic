"""Phase-aligned interferogram averaging via normalized cross-correlation.

Each accepted capture is cross-correlated against the running average. The lag
at the peak of the correlation vector is used to circularly shift the new
trace so its phase matches the average before it is folded in. Traces whose
peak correlation falls below a threshold are rejected (e.g. empty triggers or
glitches), so the final stack SNR improves roughly as sqrt(N).

Hot path (2 MSa traces at 40+ Hz)
---------------------------------
A full-length 2 MSa circular FFT correlation is ~0.5 s on this machine and
cannot keep up with the digitizer. Triggered interferograms only need a small
residual alignment (trigger jitter), so we correlate a power-of-two window
around the centerburst (default 65 536 samples, ~2 ms) and apply that lag to
the whole trace. Accumulators stay float64; correlation uses float32 rFFT.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Sequence, Tuple, Union

import numpy as np

# Channel -> (x samples, y volts). Arrays preferred; sequences accepted.
ArrayLike = Union[np.ndarray, Sequence[float]]
ChannelData = Dict[int, Tuple[ArrayLike, ArrayLike]]

# Window for accept-rate / ETA estimates (number of accepted timestamps).
ETA_ACCEPT_WINDOW = 40
# Only refresh the displayed ETA after this many new accepts (keeps UI legible).
ETA_UPDATE_EVERY = 10

# Power-of-two alignment window. 65536 samples covers ±32k of trigger jitter
# and is ~250× faster than a 2 MSa circular FFT on this CPU.
DEFAULT_ALIGN_WINDOW = 65536
# Skip the FFT entirely when the user only needs a few samples of slack.
MIN_ALIGN_WINDOW = 16


def format_eta_seconds(seconds: Optional[float]) -> str:
    """Human-readable ETA, e.g. ``45s``, ``12m 03s``, ``1h 05m``."""
    if seconds is None or seconds < 0 or not np.isfinite(seconds):
        return "…"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def _as_1d(array: ArrayLike, dtype: np.dtype) -> np.ndarray:
    """Contiguous 1-D view/copy of *array* in *dtype*."""
    return np.ascontiguousarray(np.asarray(array, dtype=dtype).ravel())


def pow2_window_length(n: int, window: int) -> int:
    """Largest power of two that is ≤ min(n, window) and ≥ MIN_ALIGN_WINDOW."""
    if n <= 0:
        return 0
    w = min(int(window), int(n))
    if w < MIN_ALIGN_WINDOW:
        return max(w, 0)
    return 1 << (w.bit_length() - 1)


def alignment_window_slice(
    n: int, center: int, window: int = DEFAULT_ALIGN_WINDOW
) -> Tuple[int, int]:
    """Return ``[start, stop)`` for a power-of-two window around *center*."""
    w = pow2_window_length(n, window)
    if w <= 0:
        return 0, 0
    start = int(center) - w // 2
    start = max(0, min(start, n - w))
    return start, start + w


def circular_cross_correlation(
    signal: np.ndarray,
    reference: np.ndarray,
    *,
    dtype: np.dtype = np.float32,
) -> np.ndarray:
    """Return the normalized circular cross-correlation vector.

    ``result[k]`` is the Pearson-like coefficient when *signal* is circularly
    shifted by *k* samples relative to *reference* (zero-mean, full-vector
    norms). Length equals ``len(signal)``; values are in approximately
    ``[-1, 1]``.

    Uses a real FFT. Prefer a short window (see ``DEFAULT_ALIGN_WINDOW``)
    rather than the full capture when aligning triggered interferograms.
    """
    sig = _as_1d(signal, dtype)
    ref = _as_1d(reference, dtype)
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
    spec = np.fft.rfft(sig_zm) * np.conj(np.fft.rfft(ref_zm))
    corr = np.fft.irfft(spec, n=n).real
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
    sig = _as_1d(signal, np.float64)
    if lag == 0 or sig.size == 0:
        return sig.copy()
    return np.roll(sig, -int(lag))


def add_aligned_inplace(acc: np.ndarray, signal: np.ndarray, lag: int) -> None:
    """``acc += roll(signal, -lag)`` without allocating a rolled copy."""
    y = np.asarray(signal, dtype=acc.dtype).ravel()
    n = acc.size
    if y.size != n:
        raise ValueError(f"length mismatch: acc={n} signal={y.size}")
    if n == 0:
        return
    k = int(lag) % n
    if k == 0:
        acc += y
        return
    acc[:-k] += y[k:]
    acc[-k:] += y[:k]


def downsample_minmax(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int = 8192,
) -> Tuple[np.ndarray, np.ndarray]:
    """Min/max envelope downsample so plotted peaks survive.

    Each bin contributes its minimum and maximum (in sample order) so a
    2 MSa interferogram can be drawn as a few thousand points without
    hiding the centerburst.
    """
    x_arr = np.asarray(x).ravel()
    y_arr = np.asarray(y).ravel()
    n = min(x_arr.size, y_arr.size)
    if n <= 0:
        return (
            np.zeros(0, dtype=np.float64),
            np.zeros(0, dtype=np.float64),
        )
    x_arr = x_arr[:n]
    y_arr = y_arr[:n]
    if n <= int(max_points) or int(max_points) < 4:
        return (
            np.ascontiguousarray(x_arr, dtype=np.float64),
            np.ascontiguousarray(y_arr, dtype=np.float64),
        )
    n_bins = max(2, int(max_points) // 2)
    bin_len = n // n_bins
    usable = bin_len * n_bins
    y2 = np.reshape(y_arr[:usable], (n_bins, bin_len))
    x2 = np.reshape(x_arr[:usable], (n_bins, bin_len))
    i_min = np.argmin(y2, axis=1)
    i_max = np.argmax(y2, axis=1)
    first = np.minimum(i_min, i_max)
    second = np.maximum(i_min, i_max)
    rows = np.arange(n_bins)
    out_x = np.empty(n_bins * 2, dtype=np.float64)
    out_y = np.empty(n_bins * 2, dtype=np.float64)
    out_x[0::2] = x2[rows, first]
    out_y[0::2] = y2[rows, first]
    out_x[1::2] = x2[rows, second]
    out_y[1::2] = y2[rows, second]
    return out_x, out_y


def slice_xy_for_view(
    x: np.ndarray,
    y: np.ndarray,
    x_lo: float,
    x_hi: float,
    *,
    max_points: int = 32768,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return the ``[x_lo, x_hi]`` slice, min/max-decimated only if still huge.

    Zooming in drops *max_points* so every remaining sample/bin is drawn.
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()
    n = min(x_arr.size, y_arr.size)
    if n == 0:
        return x_arr, y_arr
    x_arr = x_arr[:n]
    y_arr = y_arr[:n]
    lo = min(float(x_lo), float(x_hi))
    hi = max(float(x_lo), float(x_hi))
    if hi <= lo:
        return x_arr[:0], y_arr[:0]
    pad = 0.02 * (hi - lo)
    # Fast path: x is monotonically increasing (time index or sorted λ).
    if n >= 2 and x_arr[0] <= x_arr[-1]:
        left = int(np.searchsorted(x_arr, lo - pad, side="left"))
        right = int(np.searchsorted(x_arr, hi + pad, side="right"))
        if right <= left:
            return x_arr[:0], y_arr[:0]
        xs = x_arr[left:right]
        ys = y_arr[left:right]
    else:
        mask = (x_arr >= lo - pad) & (x_arr <= hi + pad)
        if not np.any(mask):
            return x_arr[:0], y_arr[:0]
        xs = x_arr[mask]
        ys = y_arr[mask]
    if xs.size > int(max_points) and int(max_points) >= 4:
        return downsample_minmax(xs, ys, int(max_points))
    return (
        np.ascontiguousarray(xs, dtype=np.float64),
        np.ascontiguousarray(ys, dtype=np.float64),
    )


def downsample_stride(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int = 8192,
) -> Tuple[np.ndarray, np.ndarray]:
    """Stride-downsample a smooth curve (e.g. a spectrum) to *max_points*."""
    x_arr = np.ascontiguousarray(np.asarray(x, dtype=np.float64).ravel())
    y_arr = np.ascontiguousarray(np.asarray(y, dtype=np.float64).ravel())
    n = min(x_arr.size, y_arr.size)
    if n <= int(max_points) or int(max_points) < 2:
        return x_arr[:n], y_arr[:n]
    step = int(np.ceil(n / float(max_points)))
    return x_arr[::step], y_arr[::step]


@dataclass
class AverageResult:
    """Snapshot of averaging progress."""

    accepted: int = 0
    rejected: int = 0
    target: int = 0
    last_peak_corr: float = 0.0
    last_lag: int = 0
    complete: bool = False
    # Seconds remaining, or None if not enough accepts yet / complete.
    eta_seconds: Optional[float] = None
    # channel -> averaged y (only when accepted > 0 and include_arrays)
    averages: Dict[int, np.ndarray] = field(default_factory=dict)
    x: Optional[np.ndarray] = None

    def lite(self) -> "AverageResult":
        """Copy without the full-resolution arrays (safe to pickle often)."""
        return AverageResult(
            accepted=self.accepted,
            rejected=self.rejected,
            target=self.target,
            last_peak_corr=self.last_peak_corr,
            last_lag=self.last_lag,
            complete=self.complete,
            eta_seconds=self.eta_seconds,
        )


class InterferogramAverager:
    """Accumulate phase-aligned interferograms until *target* accepts."""

    def __init__(
        self,
        target: int,
        threshold: float,
        reference_channel: Optional[int] = None,
        align_window: int = DEFAULT_ALIGN_WINDOW,
    ):
        self.target = max(1, int(target))
        self.threshold = float(np.clip(threshold, 0.0, 1.0))
        self.reference_channel = reference_channel
        self.align_window = max(MIN_ALIGN_WINDOW, int(align_window))
        self.accepted = 0
        self.rejected = 0
        self.last_peak_corr = 0.0
        self.last_lag = 0
        self._sum: Dict[int, np.ndarray] = {}
        self._x: Optional[np.ndarray] = None
        self._length: Optional[int] = None
        self._channels: Tuple[int, ...] = ()
        # Sample index of the centerburst used to place the alignment window.
        self._align_center: Optional[int] = None
        # Monotonic timestamps of the last N accepted interferograms.
        self._accept_times: Deque[float] = deque(maxlen=ETA_ACCEPT_WINDOW)
        # Sticky ETA string value; only recomputed every ETA_UPDATE_EVERY accepts.
        self._displayed_eta_seconds: Optional[float] = None

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
        self._align_center = None
        self._accept_times.clear()
        self._displayed_eta_seconds = None

    def _record_accept(self) -> None:
        self._accept_times.append(time.monotonic())
        self._maybe_refresh_displayed_eta()

    def _compute_eta_seconds(self) -> Optional[float]:
        """Raw ETA from the last ≤ETA_ACCEPT_WINDOW accept timestamps."""
        if self.complete:
            return 0.0
        times = self._accept_times
        if len(times) < 2:
            return None
        elapsed = times[-1] - times[0]
        if elapsed <= 1e-9:
            return None
        n_intervals = len(times) - 1
        rate = n_intervals / elapsed  # accepts per second
        remaining = self.target - self.accepted
        if remaining <= 0:
            return 0.0
        return remaining / rate

    def _maybe_refresh_displayed_eta(self) -> None:
        """Update the sticky displayed ETA every ETA_UPDATE_EVERY accepts."""
        if self.complete:
            self._displayed_eta_seconds = 0.0
            return
        # Hold the previous value between milestones so the UI stays legible.
        if self.accepted % ETA_UPDATE_EVERY != 0:
            return
        eta = self._compute_eta_seconds()
        if eta is not None:
            self._displayed_eta_seconds = eta

    def eta_seconds(self) -> Optional[float]:
        """Displayed ETA (last 40 accepts, refreshed every 10 accepts).

        Uses the span of the most recent accept window:
        ``rate = (n_accepts - 1) / (t_last - t_first)``.
        First appears at 10 accepts; returns 0 when complete.
        """
        if self.complete:
            return 0.0
        return self._displayed_eta_seconds

    def averages(self) -> Dict[int, np.ndarray]:
        if self.accepted < 1:
            return {}
        n = float(self.accepted)
        return {ch: (buf / n) for ch, buf in self._sum.items()}

    def to_channel_data(self, max_points: Optional[int] = None) -> ChannelData:
        """Current averages in Live View ``ChannelData`` form.

        If *max_points* is set, traces are min/max-downsampled for plotting.
        """
        if self._x is None or self.accepted < 1:
            return {}
        out: ChannelData = {}
        n = float(self.accepted)
        for ch, buf in self._sum.items():
            if max_points is not None:
                x_ds, y_ds = downsample_minmax(self._x, buf, max_points)
                out[ch] = (x_ds, y_ds / n)
            else:
                out[ch] = (self._x, buf / n)
        return out

    def snapshot(self, include_arrays: bool = False) -> AverageResult:
        """Progress snapshot. Full arrays are omitted unless *include_arrays*."""
        return AverageResult(
            accepted=self.accepted,
            rejected=self.rejected,
            target=self.target,
            last_peak_corr=self.last_peak_corr,
            last_lag=self.last_lag,
            complete=self.complete,
            eta_seconds=self.eta_seconds(),
            averages=self.averages() if include_arrays else {},
            x=None
            if (not include_arrays or self._x is None)
            else self._x,
        )

    def _coerce_trace(
        self, y: ArrayLike, n: int, dtype: np.dtype
    ) -> np.ndarray:
        y_arr = _as_1d(y, dtype)
        if y_arr.size == n:
            return y_arr
        if y_arr.size < n:
            return np.pad(y_arr, (0, n - y_arr.size))
        return y_arr[:n]

    def process_frame(self, channel_data: ChannelData) -> AverageResult:
        """Fold one multi-channel capture into the average if it correlates.

        The first accepted frame seeds the stack (no threshold check). Later
        frames are aligned to the running average using the peak lag of the
        circular cross-correlation on the reference channel; the same lag is
        applied to every channel in the frame so relative timing is preserved.

        Correlation runs on a short window around the centerburst (see
        *align_window*), not the full record. NCC is scale-invariant, so the
        running *sum* is used as the reference (no per-frame divide).
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
        y_ref_arr = _as_1d(y_ref, np.float32)
        n = y_ref_arr.size
        if n < 2:
            self.rejected += 1
            return self.snapshot()

        # Seed with the first frame.
        if self.accepted == 0:
            x_arr = _as_1d(x_ref, np.float64)
            if x_arr.size != n:
                x_arr = np.arange(n, dtype=np.float64)
            self._length = n
            self._x = x_arr
            self._channels = channels
            self.reference_channel = ref_ch
            # Place the alignment window on the centerburst, not mid-record.
            self._align_center = int(np.argmax(np.abs(y_ref_arr)))
            for ch in channels:
                _x, y = channel_data[ch]
                y_arr = self._coerce_trace(y, n, np.float64)
                self._sum[ch] = np.array(y_arr, dtype=np.float64, copy=True)
            self.accepted = 1
            self.last_peak_corr = 1.0
            self.last_lag = 0
            self._record_accept()
            return self.snapshot()

        # Length must match the stack.
        if self._length is not None and n != self._length:
            self.rejected += 1
            return self.snapshot()

        center = self._align_center
        if center is None:
            center = n // 2
        start, stop = alignment_window_slice(n, center, self.align_window)
        # Correlate against the running sum: NCC is invariant to scale/offset.
        corr = circular_cross_correlation(
            y_ref_arr[start:stop],
            self._sum[ref_ch][start:stop],
        )
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
            y_arr = self._coerce_trace(y, n, np.float64)
            if ch in self._sum:
                add_aligned_inplace(self._sum[ch], y_arr, lag)
            else:
                # New channel mid-run: seed with current average length zeros
                # then add (rare; usually channel set is fixed at start).
                aligned = align_trace(y_arr, lag)
                self._sum[ch] = aligned * float(self.accepted) + aligned
        self.accepted += 1
        self._record_accept()
        return self.snapshot()
