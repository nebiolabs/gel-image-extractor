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

# Adaptive vertical-bound detection (2026-07-14) -- see AGENTS.md
# Implementation Status. Two distinct real artifacts, confirmed on multiple
# real images, that a fixed top-margin-only crop doesn't handle:
#
# 1. "Comb fringe" -- the plastic comb that forms the loading wells leaves a
#    scalloped/toothed top edge with real staining smear right at the tooth
#    boundaries. Confirmed to vary meaningfully *lane to lane* on the same
#    image (10.9%-21.9% of image height in one real gel), so this is
#    detected per-lane, using that lane's own row-to-row variability: a comb
#    tooth's converging diagonal edges create real side-to-side contrast
#    *within* a single lane's narrow column range, while real resolved bands
#    (below the comb) are close to uniform across that same narrow width.
# 2. "Bottom edge artifact" -- a dark horizontal line/edge near the very
#    bottom of the image (~88-90% down in every real image checked),
#    confirmed to appear at essentially the same row across every lane of a
#    given image, ladder included -- consistent with a physical cassette/tape
#    edge, not gel content. Detected once per image using the combined
#    column range across all detected lanes (not per-lane): averaging over
#    every lane makes this shared, full-width feature stand out far more
#    clearly than any single lane's own noisier profile.
#
# Both were found to matter in practice, not just in theory: this artifact
# was the direct cause of a real ladder-miscalibration bug (see
# implementation_learnings memory) -- its outsized area displaced genuine,
# fainter ladder rungs from the "keep the k most prominent bands" step.
#
# All constants below are empirically-derived starting points from a small
# number of real images (2 images, few lanes each) -- expected to need
# further tuning as more real images are checked, same as other tunable
# constants in this project.
DEFAULT_COMB_STD_MULTIPLIER = 5.0
# IQR-style (percentile-spread-based), not a ratio-to-median: a plain ratio
# breaks down whenever a lane is mostly blank (median near zero, so *any*
# real signal looks "infinitely" elevated by comparison) -- found via a
# synthetic test with a near-blank lane. Spread-based thresholds stay
# meaningful regardless of the baseline level.
DEFAULT_EDGE_IQR_MULTIPLIER = 0.15
# Bounds how far into the image either search is allowed to look, since both
# artifacts are only ever expected near their respective edge. Without this,
# a real target band shared across every lane of a dilution series (which,
# being the same protein, migrates to about the same row in every lane) can
# look just like the bottom edge artifact to a "shared across all lanes"
# detector -- restricting the search window keeps real mid-gel content out
# of consideration entirely, rather than trying to distinguish them by shape.
DEFAULT_EDGE_SEARCH_FRACTION = 0.3
DEFAULT_MIN_TOP_MARGIN_FRACTION = 0.02
DEFAULT_MIN_BOTTOM_MARGIN_FRACTION = 0.02


@dataclass(frozen=True)
class Lane:
    """A detected vertical lane, in left-to-right column-index order."""

    index: int
    x_start: int
    x_end: int  # exclusive


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


def detect_comb_fringe_end(
    lane_columns: np.ndarray,
    std_multiplier: float = DEFAULT_COMB_STD_MULTIPLIER,
    search_fraction: float = DEFAULT_EDGE_SEARCH_FRACTION,
    min_margin_fraction: float = DEFAULT_MIN_TOP_MARGIN_FRACTION,
) -> int:
    """Find where this lane's comb/well fringe ends, adaptively.

    `lane_columns` is one lane's full-height column slice (rows x lane
    width), *before* any cropping. A comb tooth's converging diagonal edges
    create real row-to-row-varying, side-to-side contrast within the lane's
    own narrow width; a real resolved band below the comb is close to
    uniform across that same width (it's one horizontal stripe, not two
    edges converging to a point). So: compute each row's standard deviation
    across the lane's width, use the 20th percentile as a robust "normal"
    baseline (robust to the comb itself, and to a real band elsewhere, both
    minority regions), and find the first contiguous run -- within the first
    `search_fraction` of the height, where the comb is expected -- whose std
    exceeds `std_multiplier` times that baseline. The end of that run is the
    crop boundary. Falls back to `min_margin_fraction` of the height if
    nothing looks anomalous.
    """
    height = lane_columns.shape[0]
    min_margin = int(height * min_margin_fraction)
    if height == 0:
        return 0
    row_std = lane_columns.std(axis=1)
    baseline = np.percentile(row_std, 20)
    threshold = baseline * std_multiplier
    anomalous = row_std > threshold if threshold > 0 else np.zeros(height, dtype=bool)
    search_limit = int(height * search_fraction)
    anomalous[search_limit:] = False
    return _leading_artifact_boundary(anomalous, min_margin)


def detect_bottom_edge_artifact_start(
    all_lanes_columns: np.ndarray,
    iqr_multiplier: float = DEFAULT_EDGE_IQR_MULTIPLIER,
    search_fraction: float = DEFAULT_EDGE_SEARCH_FRACTION,
    min_margin_fraction: float = DEFAULT_MIN_BOTTOM_MARGIN_FRACTION,
) -> int:
    """Find where the bottom cassette/tape-edge artifact begins, adaptively.

    `all_lanes_columns` is the full-height column range spanning *every*
    detected lane combined (not one lane) -- this artifact is consistent
    across the whole gel width, so averaging over every lane makes it stand
    out far more clearly than any single lane's own noisier profile would.
    Uses the spread between the 10th/90th percentile row-mean as a "normal
    variation" reference (not a ratio to the median -- that breaks down when
    a lane is mostly blank, since almost anything looks "elevated" compared
    to a near-zero baseline) and flags rows more than `iqr_multiplier` times
    that spread beyond either percentile as anomalous -- both directions,
    since the artifact shows up as a rise then a fall toward background past
    the physical gel edge, and it's not assumed which comes first. Only
    considers the last `search_fraction` of the height: a real target band
    shared across every lane of a dilution series can otherwise look just
    like this artifact to a same-row-across-all-lanes detector, so real
    mid-gel content is kept out of consideration entirely rather than
    trying to distinguish it by shape. Falls back to `min_margin_fraction`
    from the bottom of the height if nothing looks anomalous.
    """
    height = all_lanes_columns.shape[0]
    min_margin = int(height * min_margin_fraction)
    if height == 0:
        return 0
    row_mean = all_lanes_columns.mean(axis=1)
    lo, hi = np.percentile(row_mean, [10, 90])
    spread = hi - lo
    if spread <= 0:
        anomalous = np.zeros(height, dtype=bool)
    else:
        anomalous = (row_mean > hi + iqr_multiplier * spread) | (row_mean < lo - iqr_multiplier * spread)
    search_start = height - int(height * search_fraction)
    anomalous[:search_start] = False
    return _trailing_artifact_boundary(anomalous, min_margin)


def _leading_artifact_boundary(anomalous: np.ndarray, min_margin: int) -> int:
    """End of the first contiguous anomalous run, or `min_margin` if none."""
    runs = _contiguous_runs(anomalous)
    if not runs:
        return min_margin
    _, first_end = runs[0]
    return max(min_margin, first_end)


def _trailing_artifact_boundary(anomalous: np.ndarray, min_margin: int) -> int:
    """Start of the last contiguous anomalous run, or `len - min_margin` if none."""
    height = len(anomalous)
    runs = _contiguous_runs(anomalous)
    if not runs:
        return height - min_margin
    last_start, _ = runs[-1]
    return min(height - min_margin, last_start)


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
