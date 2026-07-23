"""Baseline correction and band/peak detection on 1D intensity profiles."""

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import find_peaks

DEFAULT_BASELINE_WINDOW = 51
DEFAULT_MIN_PROMINENCE_FRACTION = 0.05
DEFAULT_MIN_WIDTH = 2
# A prominence floor purely relative to the profile's own max breaks down on
# faint/low-signal lanes: if the true signal is tiny, ordinary scan noise can
# exceed "5% of a tiny max" and gets counted as dozens of fake bands (found
# 2026-07-13 -- one real near-blank lane produced 98 "bands" this way). This
# adds an absolute noise-floor gate estimated from point-to-point
# variability (first-order differences), which stays low and stable for real
# signal (bands are smooth/wide, so adjacent-pixel jumps are small even at
# high amplitude) but correctly flags a lane as "mostly noise" when its
# tallest peak isn't meaningfully above that floor.
DEFAULT_MIN_SNR = 10.0


def estimate_noise_level(profile: np.ndarray) -> float:
    """Estimate background noise via the MAD of first-order differences.

    Robust to real peaks (which vary smoothly point-to-point regardless of
    height) unlike a plain MAD/std of the profile itself (which real peaks
    inflate directly).
    """
    if profile.size < 2:
        return 0.0
    diffs = np.diff(profile)
    return float(np.median(np.abs(diffs)) * 1.4826)


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
    min_snr: float = DEFAULT_MIN_SNR,
) -> list[Band]:
    """Find bands (peaks) in a baseline-corrected 1D intensity profile.

    A peak must clear both a relative floor (`min_prominence_fraction` of
    the profile's own max) and an absolute noise-floor gate
    (`min_snr` times the estimated background noise level) -- the relative
    floor alone isn't enough on a faint/low-signal profile, see
    `DEFAULT_MIN_SNR`. Each band's extent comes from scipy's peak-width
    estimate at half prominence; area is the trapezoidal integral of the
    profile over that extent.
    """
    if profile.size == 0 or profile.max() <= 0:
        return []

    noise_floor = estimate_noise_level(profile) * min_snr
    prominence = max(profile.max() * min_prominence_fraction, noise_floor)
    peaks, properties = find_peaks(profile, prominence=prominence, width=min_width)

    bands = []
    for peak, left, right in zip(peaks, properties["left_ips"], properties["right_ips"]):
        start = int(np.floor(left))
        end = min(int(np.ceil(right)) + 1, len(profile))
        area = float(np.trapezoid(profile[start:end]))
        bands.append(Band(start=start, end=end, center=float(peak), area=area))
    return bands
