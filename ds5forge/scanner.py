"""Find-a-value scanner (like Cheat Engine's "unknown initial value" scan, read-only).

Used to locate values the community hasn't published yet - e.g. Birth by Sleep's
HP: start the scan, take a hit, tell the scanner "it went down", repeat a few
times, and only the real HP address survives.
"""

from __future__ import annotations

import numpy as np

from .memory import Memory, MemoryError_

DTYPES = {1: np.dtype("<u1"), 2: np.dtype("<u2"), 4: np.dtype("<u4")}
CHUNK = 16 * 1024 * 1024
FEW = 4000  # below this many candidates, read them one by one instead of whole regions


class Scanner:
    def __init__(self, mem: Memory, size: int = 4, module_only: bool = True,
                 min_value: int = 1, max_value: int = 99999):
        self.mem = mem
        self.size = size
        self.dtype = DTYPES[size]
        self.module_only = module_only
        self.min_value = min_value
        self.max_value = max_value
        self._regions: list[tuple[int, int]] = []
        self._idx: list[np.ndarray] = []
        self._vals: list[np.ndarray] = []
        self.steps = 0

    # ------------------------------------------------------------------ reads
    def _read_region(self, start: int, length: int) -> np.ndarray | None:
        parts = []
        pos = start
        end = start + length
        while pos < end:
            n = min(CHUNK, end - pos)
            try:
                parts.append(self.mem.read(pos, n))
            except (MemoryError_, OSError):
                parts.append(bytes(n))  # unreadable page: treat as zeros
            pos += n
        raw = b"".join(parts)
        usable = len(raw) - len(raw) % self.size
        return np.frombuffer(raw[:usable], dtype=self.dtype)

    def _current(self, r: int) -> np.ndarray:
        start, length = self._regions[r]
        idx = self._idx[r]
        if len(idx) == 0:
            return np.zeros(0, dtype=self.dtype)
        if self.count() <= FEW:
            out = np.zeros(len(idx), dtype=self.dtype)
            for i, el in enumerate(idx):
                try:
                    out[i] = np.frombuffer(self.mem.read(start + int(el) * self.size, self.size), self.dtype)[0]
                except (MemoryError_, OSError):
                    out[i] = 0
            return out
        arr = self._read_region(start, length)
        return arr[idx]

    # ------------------------------------------------------------------- API
    def start(self) -> int:
        """Snapshot every plausible value (min_value..max_value). Returns candidate count."""
        self._regions = [(s, n) for s, n in self.mem.regions(self.module_only) if n >= self.size]
        self._idx, self._vals = [], []
        for start, length in self._regions:
            arr = self._read_region(start, length)
            keep = np.nonzero((arr >= self.min_value) & (arr <= self.max_value))[0].astype(np.uint32)
            self._idx.append(keep)
            self._vals.append(arr[keep].copy())
        self.steps = 0
        return self.count()

    def step(self, change: str, value: int | None = None) -> int:
        """Keep candidates whose value 'decreased', 'increased', 'unchanged', 'changed' or 'equals'."""
        for r in range(len(self._regions)):
            if len(self._idx[r]) == 0:
                continue
            old = self._vals[r].astype(np.int64)
            new = self._current(r)
            n64 = new.astype(np.int64)
            if change == "decreased":
                keep = n64 < old
            elif change == "increased":
                keep = n64 > old
            elif change == "unchanged":
                keep = n64 == old
            elif change == "changed":
                keep = n64 != old
            elif change == "equals":
                keep = n64 == int(value)
            else:
                raise ValueError(change)
            self._idx[r] = self._idx[r][keep]
            self._vals[r] = new[keep].copy()
        self.steps += 1
        return self.count()

    def count(self) -> int:
        return int(sum(len(i) for i in self._idx))

    def candidates(self, limit: int = 50) -> list[tuple[int, int]]:
        """(offset from module base, last value) for up to ``limit`` candidates."""
        out = []
        for r, (start, _) in enumerate(self._regions):
            for el, val in zip(self._idx[r][:limit - len(out)], self._vals[r][:limit - len(out)]):
                out.append((start + int(el) * self.size - self.mem.base, int(val)))
            if len(out) >= limit:
                break
        return out

    def read_offset(self, offset: int) -> int | None:
        try:
            raw = self.mem.read(self.mem.base + offset, self.size)
        except (MemoryError_, OSError):
            return None
        return int(np.frombuffer(raw, self.dtype)[0])
