"""Parent-owned checkpoint of the running interferogram average.

The Gage child can SIGSEGV or be recycled after a stuck WAIT_TRIGGER. The
float64 sums live in this buffer (owned by the UI process) so a new child
can restore the stack instead of starting from zero.
"""

from __future__ import annotations

import struct
from typing import Dict, Optional

import numpy as np

MAX_CHANNELS = 4
# seq u64, accepted i64, rejected i64, target i64, threshold f64,
# last_peak_corr f64, last_lag i32, ref_ch i32, align_center i32,
# align_window i32, n i32, nch i32, x0 f64, ch ids i32[4], eta f64
_HEADER_FMT = "<Qqqqdd6id4id"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


class SharedAverageBuffer:
    """Double-buffered float64 channel sums plus progress counters."""

    def __init__(
        self,
        max_samples: int,
        *,
        name: Optional[str] = None,
        create: bool = False,
    ):
        from multiprocessing import shared_memory

        self._owns = bool(create)
        if create:
            self.max_samples = max(1, int(max_samples))
            self._y_bytes = MAX_CHANNELS * self.max_samples * 8
            self._slot_bytes = _HEADER_SIZE + self._y_bytes
            self._size = 8 + 2 * self._slot_bytes
            self._shm = shared_memory.SharedMemory(create=True, size=self._size)
            self._shm.buf[:8] = b"\x00" * 8
        else:
            if not name:
                raise ValueError("SharedAverageBuffer attach requires a name")
            self._shm = shared_memory.SharedMemory(name=name)
            self._size = int(self._shm.size)
            usable = max(0, (self._size - 8) // 2 - _HEADER_SIZE)
            self.max_samples = max(1, usable // (MAX_CHANNELS * 8))
            self._y_bytes = MAX_CHANNELS * self.max_samples * 8
            self._slot_bytes = _HEADER_SIZE + self._y_bytes
        self.name = self._shm.name

    def _slot_offset(self, slot: int) -> int:
        return 8 + int(slot) * self._slot_bytes

    @property
    def seq(self) -> int:
        return int(struct.unpack_from("<Q", self._shm.buf, 0)[0])

    def clear(self) -> None:
        struct.pack_into("<Q", self._shm.buf, 0, 0)

    def write(
        self,
        *,
        accepted: int,
        rejected: int,
        target: int,
        threshold: float,
        last_peak_corr: float,
        last_lag: int,
        reference_channel: Optional[int],
        align_center: Optional[int],
        align_window: int,
        x0: float,
        sums: Dict[int, np.ndarray],
        eta_seconds: Optional[float],
    ) -> int:
        if accepted < 1 or not sums:
            return int(self.seq)
        items = []
        n = 0
        for ch in sorted(int(c) for c in sums.keys()):
            if ch < 1 or ch > MAX_CHANNELS:
                continue
            y = np.ascontiguousarray(np.asarray(sums[ch], dtype=np.float64).ravel())
            if y.size < 1:
                continue
            if n == 0:
                n = int(min(y.size, self.max_samples))
            items.append((ch, y[:n]))
            if len(items) >= MAX_CHANNELS:
                break
        if not items or n < 1:
            return int(self.seq)

        pub = self.seq
        slot = (pub + 1) & 1
        off = self._slot_offset(slot)
        ch_ids = [0, 0, 0, 0]
        y_off = off + _HEADER_SIZE
        y_buf = np.ndarray(
            (MAX_CHANNELS, self.max_samples),
            dtype=np.float64,
            buffer=self._shm.buf,
            offset=y_off,
        )
        for i, (ch, y_arr) in enumerate(items):
            ch_ids[i] = int(ch)
            y_buf[i, :n] = y_arr
        eta = float(eta_seconds) if eta_seconds is not None else -1.0
        header = struct.pack(
            _HEADER_FMT,
            pub + 1,
            int(accepted),
            int(rejected),
            int(target),
            float(threshold),
            float(last_peak_corr),
            int(last_lag),
            int(reference_channel or 0),
            int(align_center if align_center is not None else -1),
            int(align_window),
            n,
            len(items),
            float(x0),
            *ch_ids,
            eta,
        )
        self._shm.buf[off : off + _HEADER_SIZE] = header
        struct.pack_into("<Q", self._shm.buf, 0, pub + 1)
        return pub + 1

    def read(self) -> Optional[dict]:
        for _ in range(4):
            pub = self.seq
            if pub <= 0:
                return None
            slot = pub & 1
            off = self._slot_offset(slot)
            header = bytes(self._shm.buf[off : off + _HEADER_SIZE])
            (
                _seq,
                accepted,
                rejected,
                target,
                threshold,
                last_peak_corr,
                last_lag,
                ref_ch,
                align_center,
                align_window,
                n,
                nch,
                x0,
                c0,
                c1,
                c2,
                c3,
                eta,
            ) = struct.unpack(_HEADER_FMT, header)
            if n < 1 or n > self.max_samples or nch < 1 or accepted < 1:
                return None
            y_off = off + _HEADER_SIZE
            y_buf = np.ndarray(
                (MAX_CHANNELS, self.max_samples),
                dtype=np.float64,
                buffer=self._shm.buf,
                offset=y_off,
            )
            channels = [c for c in (c0, c1, c2, c3) if c > 0][:nch]
            sums: Dict[int, np.ndarray] = {}
            for i, ch in enumerate(channels):
                sums[int(ch)] = np.array(y_buf[i, :n], dtype=np.float64, copy=True)
            if self.seq != pub:
                continue
            return {
                "accepted": int(accepted),
                "rejected": int(rejected),
                "target": int(target),
                "threshold": float(threshold),
                "last_peak_corr": float(last_peak_corr),
                "last_lag": int(last_lag),
                "reference_channel": int(ref_ch) if ref_ch > 0 else None,
                "align_center": int(align_center) if align_center >= 0 else None,
                "align_window": int(align_window),
                "x0": float(x0),
                "n": int(n),
                "sums": sums,
                "eta_seconds": None if eta < 0 else float(eta),
            }
        return None

    def close(self, unlink: Optional[bool] = None) -> None:
        do_unlink = self._owns if unlink is None else bool(unlink)
        try:
            self._shm.close()
        except Exception:
            pass
        if do_unlink:
            try:
                self._shm.unlink()
            except Exception:
                pass
