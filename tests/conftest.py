"""Shared pytest fixtures."""

import numpy as np
import pytest


def make_synthetic_gel(
    height: int = 300,
    lane_width: int = 60,
    gap_width: int = 20,
    band_specs: list[list[tuple[float, float]]] | None = None,
) -> np.ndarray:
    """Build a synthetic grayscale gel image: light background, dark bands.

    `band_specs` is a list (one per lane) of (row_center, darkness) pairs,
    darkness in [0, 1] where 1 means fully black at that band's peak.
    Returns a float64 array in [0, 1], background = 1.0 (white).
    """
    if band_specs is None:
        band_specs = [[(100, 0.8)]]

    n_lanes = len(band_specs)
    width = n_lanes * lane_width + (n_lanes + 1) * gap_width
    image = np.ones((height, width), dtype=np.float64)

    rows = np.arange(height)
    for lane_idx, bands in enumerate(band_specs):
        x_start = gap_width + lane_idx * (lane_width + gap_width)
        x_end = x_start + lane_width
        lane_signal = np.zeros(height)
        for row_center, darkness in bands:
            lane_signal += darkness * np.exp(-((rows - row_center) ** 2) / (2 * 3.0**2))
        image[:, x_start:x_end] -= np.clip(lane_signal, 0, 1)[:, None]

    return np.clip(image, 0, 1)


@pytest.fixture
def synthetic_gel():
    return make_synthetic_gel
