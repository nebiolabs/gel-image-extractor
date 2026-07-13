"""Baseline correction and band/peak detection on 1D intensity profiles."""

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import find_peaks

DEFAULT_BASELINE_WINDOW = 51
DEFAULT_MIN_PROMINENCE_FRACTION = 0.05
DEFAULT_MIN_WIDTH = 2


@dataclass(frozen=True)
class Band:
    """A detected band along a 1D profile."""

    start: int
    end: int  # exclusive
    center: float
    area: float


BaselineStrategy = Callable[[np.ndarray], np.ndarray]


def rolling_minimum_baseline(profile: np.ndarray, window: int = DEFAULT_BASELINE_WINDOW) -> np.ndarray:
    """Estimate baseline as a smoothed rolling minimum of the profile.

    Simple, deterministic densitometry baseline: assumes background signal
    varies slowly and never exceeds the local minimum. Placeholder default
    per AGENTS.md Design Decisions -- expected to be revisited once tested
    against real gels (e.g. asymmetric least squares is a common
    alternative), which is why baseline correction is pluggable via the
    `strategy` argument on `correct_baseline` rather than hardcoded here.
    """
    baseline = minimum_filter1d(profile, size=window, mode="nearest")
    return uniform_filter1d(baseline, size=window, mode="nearest")


DEFAULT_BASELINE_STRATEGY: BaselineStrategy = rolling_minimum_baseline


def correct_baseline(profile: np.ndarray, strategy: BaselineStrategy = DEFAULT_BASELINE_STRATEGY) -> np.ndarray:
    """Subtract an estimated baseline from a 1D intensity profile."""
    baseline = strategy(profile)
    return np.clip(profile - baseline, 0, None)


def detect_bands(
    profile: np.ndarray,
    min_prominence_fraction: float = DEFAULT_MIN_PROMINENCE_FRACTION,
    min_width: int = DEFAULT_MIN_WIDTH,
) -> list[Band]:
    """Find bands (peaks) in a baseline-corrected 1D intensity profile.

    Each band's extent comes from scipy's peak-width estimate at half
    prominence; area is the trapezoidal integral of the profile over that
    extent.
    """
    if profile.size == 0 or profile.max() <= 0:
        return []

    prominence = profile.max() * min_prominence_fraction
    peaks, properties = find_peaks(profile, prominence=prominence, width=min_width)

    bands = []
    for peak, left, right in zip(peaks, properties["left_ips"], properties["right_ips"]):
        start = int(np.floor(left))
        end = min(int(np.ceil(right)) + 1, len(profile))
        area = float(np.trapezoid(profile[start:end]))
        bands.append(Band(start=start, end=end, center=float(peak), area=area))
    return bands
