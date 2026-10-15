"""Per-stage count samples and the rates derived from them (real elapsed time)."""
from __future__ import annotations

from collections import deque


class History:
    """Samples of a cumulative count. Rates are per minute; drops clamp to zero."""

    def __init__(self, bucket: int = 10, keep: int = 180):
        self.bucket = bucket
        self.keep = keep
        self._samples: deque[tuple[float, int]] = deque()

    def add(self, t: float, count: int | None) -> None:
        if count is None:
            return
        if self._samples and t <= self._samples[-1][0]:
            if t == self._samples[-1][0]:
                self._samples[-1] = (t, count)
            return
        self._samples.append((t, count))
        horizon = t - (self.keep + 2) * self.bucket
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()

    def seed(self, points) -> None:
        for t, count in sorted(points):
            self.add(t, count)

    def rate_per_min(self, now: float | None = None, window: float = 60.0) -> float | None:
        """Rate over the newest ``window`` seconds of samples (``now`` kept for callers)."""
        if len(self._samples) < 2:
            return None
        t1, c1 = self._samples[-1]
        t0, c0 = t1, c1
        for t, c in reversed(self._samples):
            if t < t1 - window:
                break
            t0, c0 = t, c
        if t1 - t0 <= 0:
            t0, c0 = self._samples[-2]
        return max(0.0, (c1 - c0) / (t1 - t0) * 60.0)

    def series(self) -> list[list[float]]:
        """``[bucket_start, rate_per_min]`` for each bucket after the first, newest last."""
        last_in_bucket: dict[int, tuple[float, int]] = {}
        for t, c in self._samples:
            last_in_bucket[int(t // self.bucket)] = (t, c)
        keys = sorted(last_in_bucket)
        out: list[list[float]] = []
        for prev, key in zip(keys, keys[1:]):
            (t0, c0), (t1, c1) = last_in_bucket[prev], last_in_bucket[key]
            rate = max(0.0, (c1 - c0) / (t1 - t0) * 60.0) if t1 > t0 else 0.0
            out.append([float(key * self.bucket), round(rate, 2)])
        return out[-self.keep:]
