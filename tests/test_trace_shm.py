"""Shared-trace buffer used to keep FFT / zoom off the capture path."""

from __future__ import annotations

import numpy as np

from src.trace_shm import SharedTraceBuffer


def test_shared_trace_roundtrip():
    n = 10_000
    x = np.arange(n, dtype=np.float64) - 100.0
    y = np.sin(np.linspace(0, 6, n)).astype(np.float32)
    buf = SharedTraceBuffer(n, create=True)
    try:
        assert buf.read() is None
        seq = buf.write({1: (x, y)})
        assert seq == 1
        got = buf.read()
        assert got is not None
        assert 1 in got
        gx, gy = got[1]
        assert gx.size == n
        np.testing.assert_allclose(gx[0], -100.0)
        np.testing.assert_allclose(gy, y, atol=1e-6)
        # Second write uses the other slot.
        y2 = y + 1.0
        buf.write({1: (x, y2)})
        gx2, gy2 = buf.read()[1]
        np.testing.assert_allclose(gy2, y2, atol=1e-6)
    finally:
        buf.close(unlink=True)


def test_shared_average_roundtrip():
    from src.average_shm import SharedAverageBuffer
    from src.averaging import InterferogramAverager

    n = 8_192
    x = np.arange(n, dtype=np.float64) - 100.0
    y = np.sin(np.linspace(0, 8, n))
    avg = InterferogramAverager(target=10, threshold=0.2, reference_channel=1)
    avg.process_frame({1: (x, y)})
    avg.process_frame({1: (x, np.roll(y, 3))})
    buf = SharedAverageBuffer(n, create=True)
    try:
        assert buf.read() is None
        state = avg.checkpoint_dict()
        assert state is not None
        buf.write(
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
        got = buf.read()
        assert got is not None
        assert got["accepted"] == 2
        fresh = InterferogramAverager(target=10, threshold=0.2)
        assert fresh.load_checkpoint(got)
        np.testing.assert_allclose(fresh.averages()[1], avg.averages()[1])
        buf.clear()
        assert buf.read() is None
    finally:
        buf.close(unlink=True)
