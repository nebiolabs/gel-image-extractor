"""Unit tests for neband.api.image_cache -- GH issue #1's Fix #1
(cache isolation + bounded eviction) in isolation from any HTTP/Flask layer.
"""

import numpy as np
import pytest

from neband.api.image_cache import ImageCache, content_id


def _arr(nbytes: int) -> np.ndarray:
    """A float64 array of roughly `nbytes` bytes (8 bytes/element)."""
    n = max(1, nbytes // 8)
    return np.zeros(n, dtype=np.float64)


def test_content_id_is_stable_for_identical_bytes():
    assert content_id(b"same bytes") == content_id(b"same bytes")


def test_content_id_differs_for_different_bytes():
    assert content_id(b"image one") != content_id(b"image two")


def test_put_then_get_returns_the_same_array():
    cache = ImageCache()
    signal = _arr(100)
    cache.put("abc", signal)
    assert cache.get("abc") is signal


def test_get_miss_returns_none():
    cache = ImageCache()
    assert cache.get("nonexistent") is None


def test_different_content_ids_never_collide():
    """Two distinct images cached under distinct ids never shadow each other --
    the actual correctness bug this cache design fixes vs. a filename-keyed one.
    """
    cache = ImageCache()
    signal_a = _arr(100)
    signal_b = _arr(100)
    cache.put("image-a", signal_a)
    cache.put("image-b", signal_b)
    assert cache.get("image-a") is signal_a
    assert cache.get("image-b") is signal_b


def test_eviction_by_entry_count():
    cache = ImageCache(max_entries=2, max_bytes=10**9)
    cache.put("first", _arr(8))
    cache.put("second", _arr(8))
    cache.put("third", _arr(8))
    assert len(cache) == 2
    assert cache.get("first") is None  # least-recently-used, evicted
    assert cache.get("second") is not None
    assert cache.get("third") is not None


def test_get_refreshes_recency_so_it_survives_eviction():
    cache = ImageCache(max_entries=2, max_bytes=10**9)
    cache.put("first", _arr(8))
    cache.put("second", _arr(8))
    cache.get("first")  # touch "first" -- "second" is now the LRU entry
    cache.put("third", _arr(8))
    assert cache.get("first") is not None
    assert cache.get("second") is None
    assert cache.get("third") is not None


def test_eviction_by_total_bytes():
    cache = ImageCache(max_entries=100, max_bytes=20)
    cache.put("a", _arr(16))
    cache.put("b", _arr(16))
    assert cache.total_bytes <= 20
    assert len(cache) == 1
    assert cache.get("b") is not None
    assert cache.get("a") is None


def test_putting_an_existing_id_again_is_a_no_op_not_a_duplicate():
    cache = ImageCache()
    first = _arr(8)
    second = _arr(8)
    cache.put("dup", first)
    cache.put("dup", second)  # same id, e.g. a repeat upload of the same image
    assert len(cache) == 1
    assert cache.get("dup") is first  # first write wins, not silently replaced


@pytest.mark.parametrize("bad_value", [0, -1])
def test_rejects_non_positive_limits(bad_value):
    with pytest.raises(ValueError):
        ImageCache(max_entries=bad_value)
    with pytest.raises(ValueError):
        ImageCache(max_bytes=bad_value)
