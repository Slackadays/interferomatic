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
