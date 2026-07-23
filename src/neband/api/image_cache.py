"""Bounded, content-addressed cache for decoded image signals.

Directly implements GH issue #1's Fix #1: the disposable prototype's
`get_signal` cache (`scripts/hitl_ui_server.py`) is a bare in-process dict
keyed by image *name*, with no eviction. That's a real correctness bug
once this is a shared service -- two callers' images sharing a name would
silently serve each other's cached signal, and memory grows without
bound.

Fixed here by keying on a SHA-256 hash of the raw uploaded bytes (an
identity the cache derives itself, not something a caller supplies or can
get wrong/collide on) and bounding the cache by both entry count and total
byte size, evicting least-recently-used entries once either limit is
exceeded.

Not shared across processes: each `gunicorn` worker (see GH issue #1's
Fix #2 on concurrency) gets its own instance and its own memory. The same
image gets decoded once per worker that happens to handle it, not once
globally -- a deliberate simplification. That costs some redundant
decoding under multi-worker load, but each worker's cache stays correct
and bounded independently; a cross-process shared cache (e.g. Redis) is
not warranted unless real usage shows the redundant-decode cost matters.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict

import numpy as np

DEFAULT_MAX_ENTRIES = 64
DEFAULT_MAX_BYTES = 1_000_000_000  # 1 GB of decoded float64 signal arrays


def content_id(image_bytes: bytes) -> str:
    """Stable identity for `image_bytes`, independent of any caller-supplied name."""
    return hashlib.sha256(image_bytes).hexdigest()


class ImageCache:
    """LRU cache of decoded image signals, bounded by count and total bytes.

    Thread-safe (a single `gunicorn` worker can still be multi-threaded),
    not process-safe -- see the module docstring.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._store: OrderedDict[str, np.ndarray] = OrderedDict()
        self._total_bytes = 0
        self._lock = threading.Lock()

    def get(self, image_id: str) -> np.ndarray | None:
        with self._lock:
            signal = self._store.get(image_id)
            if signal is not None:
                self._store.move_to_end(image_id)
            return signal

    def put(self, image_id: str, signal: np.ndarray) -> None:
        with self._lock:
            if image_id in self._store:
                self._store.move_to_end(image_id)
                return
            self._store[image_id] = signal
            self._total_bytes += signal.nbytes
            self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while self._store and (
            len(self._store) > self._max_entries or self._total_bytes > self._max_bytes
        ):
            _, evicted = self._store.popitem(last=False)
            self._total_bytes -= evicted.nbytes

    def __len__(self) -> int:
        return len(self._store)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes
