"""Throughput check: averaging 2 MSa interferograms should exceed 40 Hz."""

from __future__ import annotations

import time

import numpy as np

from src.averaging import InterferogramAverager, downsample_minmax
from src.live_view import SimulatedLiveViewEngine


def _make_trace(n: int, lag: int = 0, rng=None) -> np.ndarray:
    i = np.arange(n, dtype=np.float32)
    center = n // 2 + int(lag)
    u = (i - center) / 120.0
    y = np.exp(-0.5 * u * u) * np.cos(0.11 * (i - center))
    if rng is not None:
        y = y + 0.02 * rng.standard_normal(n).astype(np.float32)
    return y


def bench_process_frame(n: int = 2_000_000, frames: int = 80) -> float:
    rng = np.random.default_rng(1)
    x = np.arange(-n // 4, n - n // 4, dtype=np.float64)
    seed = _make_trace(n, 0, rng)
    avg = InterferogramAverager(target=frames + 1, threshold=0.2, reference_channel=1)
    avg.process_frame({1: (x, seed)})
    traces = [_make_trace(n, int(rng.integers(-40, 41)), rng) for _ in range(frames)]
    t0 = time.perf_counter()
    for y in traces:
        avg.process_frame({1: (x, y)})
    elapsed = time.perf_counter() - t0
    hz = frames / elapsed
    print(
        f"process_frame {n} samples x {frames}: "
        f"{elapsed * 1000 / frames:.2f} ms/frame, {hz:.1f} Hz "
        f"(accepted={avg.accepted}, rejected={avg.rejected})"
    )
    assert avg.accepted == frames + 1, "alignment should accept the noisy bursts"
    return hz


def bench_simulator(n: int = 2_000_000, frames: int = 40) -> float:
    pre = n // 8
    post = n - pre
    engine = SimulatedLiveViewEngine()
    engine.configure(
        100_000_000,
        [1],
        input_range=400,
        pre_trigger_samples=pre,
        post_trigger_samples=post,
        max_capture_rate_hz=1000,
    )
    engine.start(
        [1],
        average={"target": frames, "threshold": 0.2, "reference_channel": 1},
        spectrum=None,
    )
    t0 = time.perf_counter()
    got = 0
    while got < frames:
        plot = engine.capture([1])
        status = engine.average_status()
        if status is not None:
            got = status.accepted
        del plot
    elapsed = time.perf_counter() - t0
    hz = frames / elapsed
    print(
        f"simulated capture+average {pre}+{post} samples x {frames}: "
        f"{elapsed * 1000 / frames:.2f} ms/frame, {hz:.1f} Hz"
    )
    return hz


def bench_downsample(n: int = 2_000_000) -> None:
    x = np.arange(n, dtype=np.float64)
    y = _make_trace(n)
    t0 = time.perf_counter()
    xd, yd = downsample_minmax(x, y, 8192)
    elapsed = time.perf_counter() - t0
    print(
        f"downsample_minmax {n} -> {yd.size}: {elapsed * 1000:.2f} ms"
    )


if __name__ == "__main__":
    bench_downsample()
    hz = bench_process_frame()
    hz_sim = bench_simulator()
    print(f"OK: averaging {hz:.1f} Hz, simulator {hz_sim:.1f} Hz (target ≥ 40)")
    if hz < 40:
        raise SystemExit(1)
