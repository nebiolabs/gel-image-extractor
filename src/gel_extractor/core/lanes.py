"""Lane auto-detection via column-intensity projection."""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d

from gel_extractor.core.bands import correct_baseline, rolling_minimum_baseline

# Tunable knobs, centralized per the project's "no scattered magic numbers"
# architecture requirement. Expected to need empirical tuning against real
# gels -- see AGENTS.md Design Decisions.
#
# threshold_fraction is deliberately small: real gel photos have their own
# non-white "gel rectangle" background that dominates the raw column-sum
# profile, so lane peaks (after baseline correction) can be a small fraction
# of the tallest lane's peak (e.g. a faint dilution lane next to a dense
# ladder lane) -- found empirically against real example gels (2026-07-13).
DEFAULT_THRESHOLD_FRACTION = 0.03
DEFAULT_SMOOTHING_SIGMA = 3.0
DEFAULT_BASELINE_WINDOW = 51
DEFAULT_MIN_LANE_WIDTH = 10
DEFAULT_MIN_GAP_WIDTH = 4
DEFAULT_TOP_MARGIN_FRACTION = 0.05


@dataclass(frozen=True)
class Lane:
    """A detected vertical lane, in left-to-right column-index order."""

    index: int
    x_start: int
    x_end: int  # exclusive

    def crop(self, signal: np.ndarray, top_margin_fraction: float = DEFAULT_TOP_MARGIN_FRACTION) -> np.ndarray:
        """Return this lane's pixels, skipping a top margin to exclude the loading well.

        Placeholder for now: excludes a fixed fraction of the top rows but
        doesn't yet detect the dye front, so the bottom bound is just the
        image edge. Needs visual validation against real gels -- see
        AGENTS.md Design Decisions.
        """
        top = int(signal.shape[0] * top_margin_fraction)
        return signal[top:, self.x_start : self.x_end]


def detect_lanes(
    signal: np.ndarray,
    threshold_fraction: float = DEFAULT_THRESHOLD_FRACTION,
    smoothing_sigma: float = DEFAULT_SMOOTHING_SIGMA,
    baseline_window: int = DEFAULT_BASELINE_WINDOW,
    min_lane_width: int = DEFAULT_MIN_LANE_WIDTH,
    min_gap_width: int = DEFAULT_MIN_GAP_WIDTH,
) -> list[Lane]:
    """Detect vertical lanes in a gel's signal array via column-sum projection.

    Sums signal down each column, smooths the resulting profile, and
    baseline-corrects it (the same rolling-minimum approach used for band
    detection, just applied along the column axis) to remove the gel
    rectangle's own background level before thresholding -- real gel photos
    aren't on a pure white background, so lane peaks sit on top of a
    slowly-varying baseline rather than near zero. Contiguous runs above
    `threshold_fraction` of the corrected profile's peak are treated as
    lanes; runs separated by a gap narrower than `min_gap_width` are merged
    (a real lane can have a faint dip mid-lane without reaching background
    level); runs narrower than `min_lane_width` are discarded as noise.
    """
    column_profile = signal.sum(axis=0)
    smoothed = gaussian_filter1d(column_profile, sigma=smoothing_sigma)
    corrected = correct_baseline(smoothed, strategy=lambda p: rolling_minimum_baseline(p, window=baseline_window))

    threshold = corrected.max() * threshold_fraction
    above = corrected > threshold

    runs = _contiguous_runs(above)
    runs = _merge_close_runs(runs, min_gap_width)
    runs = [r for r in runs if (r[1] - r[0]) >= min_lane_width]

    return [Lane(index=i, x_start=start, x_end=end) for i, (start, end) in enumerate(runs)]


def _contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return (start, end) index pairs for each contiguous True run in `mask`."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return list(zip(edges[0::2], edges[1::2]))


def _merge_close_runs(runs: list[tuple[int, int]], min_gap_width: int) -> list[tuple[int, int]]:
    if not runs:
        return runs
    merged = [runs[0]]
    for start, end in runs[1:]:
        prev_start, prev_end = merged[-1]
        if start - prev_end < min_gap_width:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged
