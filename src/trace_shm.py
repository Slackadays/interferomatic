"""Lock-free double-buffered traces shared by the Gage child and the UI.

The capture loop writes a snapshot of the latest full-resolution waveforms
here and immediately goes back to the digitizer. The UI and a background
FFT thread read copies on their own clocks, so a 2 MSa transform cannot
stall acquisition.
"""

from __future__ import annotations

import struct
from typing import Dict, Optional, Tuple

import numpy as np

MAX_CHANNELS = 4
# seq u64, n i32, nch i32, x0 f64, channel ids i32[4]
_HEADER_FMT = "<Qiid4i"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

ChannelData = Dict[int, Tuple[np.ndarray, np.ndarray]]


class SharedTraceBuffer:
    """Two slots of float32 channel data plus a published sequence number."""

    def __init__(
        self,
        max_samples: int,
        *,
        name: Optional[str] = None,
        create: bool = False,
    ):
        from multiprocessing import shared_memory

        self.max_samples = max(1, int(max_samples))
        self._y_bytes = MAX_CHANNELS * self.max_samples * 4
        self._slot_bytes = _HEADER_SIZE + self._y_bytes
        # [pub_seq u64][slot0][slot1]
        self._size = 8 + 2 * self._slot_bytes
        self._owns = bool(create)
        if create:
            self._shm = shared_memory.SharedMemory(create=True, size=self._size)
            self._shm.buf[:8] = b"\x00" * 8
        else:
            if not name:
                raise ValueError("SharedTraceBuffer attach requires a name")
            self._shm = shared_memory.SharedMemory(name=name)
        self.name = self._shm.name
        self._last_read_seq = -1

    def _slot_offset(self, slot: int) -> int:
        return 8 + int(slot) * self._slot_bytes

    def write(self, data: ChannelData) -> int:
        """Publish *data* into the back slot. Returns the new sequence number."""
        if not data:
            return int(self.seq)
        items = []
        x0 = 0.0
        n = 0
        for ch in sorted(int(c) for c in data.keys()):
            if ch < 1 or ch > MAX_CHANNELS:
                continue
            x, y = data[ch]
            y_arr = np.ascontiguousarray(np.asarray(y, dtype=np.float32).ravel())
            if y_arr.size < 1:
                continue
            if n == 0:
                n = int(min(y_arr.size, self.max_samples))
                x_arr = np.asarray(x).ravel()
                x0 = float(x_arr[0]) if x_arr.size else 0.0
            items.append((ch, y_arr[:n]))
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
            dtype=np.float32,
            buffer=self._shm.buf,
            offset=y_off,
        )
        for i, (ch, y_arr) in enumerate(items):
            ch_ids[i] = int(ch)
            y_buf[i, :n] = y_arr
        header = struct.pack(
            _HEADER_FMT, pub + 1, n, len(items), x0, *ch_ids
        )
        self._shm.buf[off : off + _HEADER_SIZE] = header
        struct.pack_into("<Q", self._shm.buf, 0, pub + 1)
        return pub + 1

    @property
    def seq(self) -> int:
        return int(struct.unpack_from("<Q", self._shm.buf, 0)[0])

    def read(self) -> Optional[ChannelData]:
        """Copy the latest published snapshot, or None if nothing is written."""
        out: ChannelData = {}
        for _ in range(4):
            pub = self.seq
            if pub <= 0:
                return None
            slot = pub & 1
            off = self._slot_offset(slot)
            header = bytes(self._shm.buf[off : off + _HEADER_SIZE])
            _seq, n, nch, x0, c0, c1, c2, c3 = struct.unpack(_HEADER_FMT, header)
            if n < 1 or n > self.max_samples or nch < 1:
                return None
            y_off = off + _HEADER_SIZE
            y_buf = np.ndarray(
                (MAX_CHANNELS, self.max_samples),
                dtype=np.float32,
                buffer=self._shm.buf,
                offset=y_off,
            )
            channels = [c for c in (c0, c1, c2, c3) if c > 0][:nch]
            x = np.arange(n, dtype=np.float64) + float(x0)
            out: ChannelData = {}
            for i, ch in enumerate(channels):
                out[int(ch)] = (x, np.array(y_buf[i, :n], dtype=np.float32, copy=True))
            if self.seq == pub:
                self._last_read_seq = pub
                return out
        return out if out else None

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
