"""Unit tests for phase-aligned averaging and downsample helpers."""

from __future__ import annotations

import numpy as np

from src.averaging import (
    InterferogramAverager,
    add_aligned_inplace,
    alignment_window_slice,
    best_alignment_lag,
    circular_cross_correlation,
    downsample_minmax,
    downsample_stride,
    pow2_window_length,
    slice_xy_for_view,
)


def _burst(n: int, center: int, width: float = 80.0, carrier: float = 0.12) -> np.ndarray:
    i = np.arange(n, dtype=np.float64)
    u = (i - center) / width
    return np.exp(-0.5 * u * u) * np.cos(2.0 * np.pi * carrier * (i - center))


def test_window_is_power_of_two():
    assert pow2_window_length(2_000_000, 65536) == 65536
    assert pow2_window_length(1000, 65536) == 512
    start, stop = alignment_window_slice(2_000_000, 1_000_000, 65536)
    assert stop - start == 65536
    assert start <= 1_000_000 < stop


def test_circular_correlation_recovers_lag():
    n = 4096
    ref = _burst(n, n // 2)
    lag_true = 37
    sig = np.roll(ref, lag_true)
    corr = circular_cross_correlation(sig, ref)
    lag, peak = best_alignment_lag(corr)
    assert lag == lag_true
    assert peak > 0.99


def test_process_frame_aligns_and_rejects():
    n = 100_000
    center = 40_000
    ref = _burst(n, center)
    x = np.arange(n, dtype=np.float64)
    avg = InterferogramAverager(target=5, threshold=0.5, reference_channel=1)

    seed = avg.process_frame({1: (x, ref)})
    assert seed.accepted == 1
    assert not seed.complete

    shifted = np.roll(ref, 19)
    ok = avg.process_frame({1: (x, shifted)})
    assert ok.accepted == 2
    assert ok.last_lag == 19
    assert ok.last_peak_corr > 0.9

    noise = 0.01 * np.random.default_rng(0).standard_normal(n)
    bad = avg.process_frame({1: (x, noise)})
    assert bad.rejected == 1
    assert bad.accepted == 2

    # Stacked average should match the seed shape after alignment.
    out = avg.averages()[1]
    corr = circular_cross_correlation(out, ref)
    lag, peak = best_alignment_lag(corr)
    assert lag == 0
    assert peak > 0.99


def test_add_aligned_matches_roll():
    acc = np.zeros(16, dtype=np.float64)
    y = np.arange(16, dtype=np.float64)
    add_aligned_inplace(acc, y, 3)
    np.testing.assert_allclose(acc, np.roll(y, -3))


def test_downsample_preserves_peak():
    n = 100_000
    x = np.arange(n, dtype=np.float64)
    y = np.zeros(n, dtype=np.float64)
    y[50_000] = 12.0
    xd, yd = downsample_minmax(x, y, max_points=200)
    assert yd.size <= 200
    assert yd.size >= 2
    assert float(yd.max()) == 12.0
    xs, ys = downsample_stride(x, y, max_points=200)
    assert xs.size <= 200


def test_slice_xy_keeps_every_sample_when_zoomed():
    n = 200_000
    x = np.arange(n, dtype=np.float64) - n / 4.0
    y = np.sin(x / 50.0)
    xs, ys = slice_xy_for_view(x, y, -50.0, 50.0, max_points=10_000)
    # 2% pad around the window, but every remaining sample is kept.
    assert 101 <= xs.size <= 121
    assert xs[0] <= -50.0 and xs[-1] >= 50.0
    assert np.all(np.diff(xs) == 1.0)
    assert ys.size == xs.size


def test_checkpoint_roundtrip_keeps_stack():
    n = 4096
    ref = _burst(n, n // 2)
    x = np.arange(n, dtype=np.float64)
    avg = InterferogramAverager(target=50, threshold=0.5, reference_channel=1)
    avg.process_frame({1: (x, ref)})
    avg.process_frame({1: (x, np.roll(ref, 11))})
    assert avg.accepted == 2
    state = avg.checkpoint_dict()
    assert state is not None
    fresh = InterferogramAverager(target=50, threshold=0.5, reference_channel=1)
    assert fresh.load_checkpoint(state)
    assert fresh.accepted == 2
    np.testing.assert_allclose(fresh.averages()[1], avg.averages()[1])
    # New aligned frames still fold into the restored sum.
    ok = fresh.process_frame({1: (x, np.roll(ref, -4))})
    assert ok.accepted == 3
    assert ok.last_peak_corr > 0.9


def test_snapshot_lite_omits_arrays():
    n = 1024
    y = _burst(n, n // 2)
    avg = InterferogramAverager(target=2, threshold=0.1)
    avg.process_frame({1: (np.arange(n), y)})
    lite = avg.snapshot()
    assert lite.averages == {}
    assert lite.x is None
    full = avg.snapshot(include_arrays=True)
    assert 1 in full.averages
    assert full.x is not None
    assert full.x.size == n
