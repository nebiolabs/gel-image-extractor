"""Prototype: ridge/vesselness-filter based curved lane geometry (2026-07-16).

## Why this exists

`core.lanes.detect_lanes` (the production path) treats every lane as a
straight vertical rectangle spanning the image's full height. Real gels
"smile" -- edge lanes migrate faster/slower than center lanes, so a lane's
true x-position drifts down the image -- which a fixed rectangle can't
represent. A prior prototype (branch `curve-tracing-lane-detection`,
`core/curve_lanes.py`) attacked this by independently re-detecting lane
candidates in horizontal strips and tracking them strip-to-strip; per
AGENTS.md Implementation Status, that approach's own findings were mixed --
independent per-strip detection turned out to be *noisier* than one
whole-image column projection, and its no-split/merge tracker amplified
that noise into *more* fragments on the project's hardest case (`PDEV1452`:
~27 traced tracks vs. ~14 real lanes), specifically recommending: **don't
re-detect lane candidates independently per strip -- fix lane count/identity
once on the whole image first, then only trace each already-identified
lane's local curvature.**

This module takes that advice literally, using a different geometric tool:
a ridge/vesselness filter (`skimage.filters.meijering`), originally built to
trace blood vessels/neurites in medical images -- structurally the same kind
of object as a gel lane (an elongated, roughly-parallel streak against a
different-intensity background). Applying it to the *whole* image at once
(rather than per-strip) means the filter itself gets the full vertical
extent of real lane structure to work with, rather than a noisy few-row
slice -- directly addressing the "less height to average over" problem the
prior prototype ran into.

## Pipeline

1. `detect_lanes` (core.lanes, UNCHANGED, reused) runs ONCE on the whole
   image's column-sum profile to fix lane count and each lane's straight
   x-range. This module never re-detects lane candidates; it only re-derives
   an already-identified lane's curved centerline within its own column
   range.
2. The whole image is background-flattened (large-radius Gaussian estimate
   subtracted -- real gel photos aren't on a white background, same
   principle as `core.bands.correct_baseline`, just applied in 2D) and run
   through `meijering` ONCE for the whole image, producing a whole-image
   ridge-response map. `black_ridges=False` since a real lane is a *brighter*
   streak in the (inverted, see `image_io.to_signal`) signal image, not a
   darker one.
3. For each already-identified lane, its centerline is traced row-by-row as
   the ridge-response-weighted centroid of that row, restricted to a hard
   column window bounded by the MIDPOINT to its nearest neighboring lane on
   either side -- never the raw lane rectangle, which would still let the
   trace wander into a close neighbor's own ridge response. This directly
   implements the documented "a search window can reach into a neighboring
   lane's own signal" bug (AGENTS.md task instructions / prior real bugs in
   windowed geometry approaches). The resulting per-row path is smoothed
   along the row axis (small Gaussian) since real curvature changes
   gradually, not row-to-row.
4. The per-lane 1D intensity profile is summed from the ORIGINAL (`signal`,
   not ridge-filtered) image in a window around that traced centerline --
   the ridge filter is purely a geometry/position tool, never a substitute
   for the real densitometry signal that band detection needs.
5. Vertical cropping (comb/well fringe at top, cassette/tape-edge artifact
   at bottom) and the ladder-frame `position_offset` re-basing reuse
   `core.lanes.detect_comb_fringe_end` / `detect_bottom_edge_artifact_start`
   completely unchanged, applied to each lane's *straight* rectangle exactly
   as `purity.analysis` already does for the rectangle approach -- curvature
   is only assumed to matter *within* the resolving region, not for locating
   where that region starts/ends.
6. Once a profile exists, `purity.analysis._analyze_lane_detailed` is
   imported and called directly -- band detection, ladder calibration, and
   the purity-percent formula are NOT reimplemented here, so any purity
   difference against the straight-rectangle approach is attributable only
   to this geometry difference (see this task's own instructions).

## Honest scope

This is a quick go/no-go prototype, not a production replacement. It does
not handle lane count over/under-segmentation at all (that's `detect_lanes`'
job, unchanged); it only asks whether tracing curvature *within* an already-
correctly-identified lane, via a ridge filter, produces a materially
different (better or worse) purity reading than summing a straight
rectangle. See `scripts/compare_ridge_vs_straight.py` for the real-image
comparison.

## Findings from real-image testing (2026-07-16) -- honest, mixed result

Ran via `scripts/compare_ridge_vs_straight.py` against all 17 real images
this project has target-MW/ladder info for (same list as `scripts/
generate_debug_images.py`), comparing every sample lane's purity % against
the straight-rectangle pipeline on the identical image/target-MW/ladder
arguments:

- **A real bug was caught and fixed by this validation, not just
  hypothesized**: the outermost lane on either side has no neighbor to
  bound its outer search window, and the initial implementation left that
  side unbounded out to the image edge. On `8.6.25 Protein Purity.tif`, the
  ladder lane's traced centerline near its top was measurably dragged 150+
  px away from its own straight rectangle (confirmed by printing raw
  `centers` values, not just eyeballing a render) -- the gel slab's own
  physical edge is itself a strong, literal elongated boundary that
  `meijering` responds to just as readily as a real lane, the same root
  cause as Attempt 1 of the rectangle over-segmentation problem (see
  AGENTS.md). Fixed by bounding the outer edge to one reference lane width
  beyond the lane's own rectangle, same as the inter-lane midpoint bound.
- **Where it's plausible-looking**: on well-behaved images (e.g. the same
  `8.6.25 Protein Purity.tif` / HpyCH4IV image the prior curve-tracing
  prototype also called out), traced centerlines visibly converge into each
  comb tooth's real apex at the top, similar to that prior prototype's own
  finding.
- **Real, material per-lane purity divergence, not just noise**: across the
  17 images, per-image max |purity-point difference| against the straight
  baseline ranged from single digits to as high as 95 points on individual
  lanes, and 11-20 lane-level confidence-tier flips (mw-matched vs.
  heuristic vs. not-found) depending on the row-smoothing parameter -- see
  next finding.
- **A real, unresolved tuning tension, not swept away by "smooth it more"**:
  the raw per-row ridge-weighted centroid is visibly noisy/wobbly
  (non-physical left-right zigzag within a single lane, especially in
  faint/blank lanes) before smoothing. Increasing the row-smoothing
  Gaussian's sigma produces visually smoother, more plausible-looking single
  curves per lane (confirmed by rendered overlays) -- but empirically did
  NOT uniformly improve agreement with the straight-rectangle baseline
  across all 17 images: it markedly tightened agreement on some images
  (e.g. the confirmed-98.5%-purity `PDEV1580` image went from a 17-point max
  diff down to 2-3 points) while making others meaningfully worse (e.g.
  `R-244_PDEV1526` grew from a 6-point to a 95-point max single-lane
  divergence) as the smoothed curve shifts which detected band ends up
  closest to a given calibrated MW. This is a genuine finding, not a
  parameter that was left untuned by omission: "smoother is better" doesn't
  hold uniformly on this real image set, so whatever value ships here is a
  documented compromise (`DEFAULT_ROW_SMOOTHING_FRACTION` /
  `DEFAULT_MIN_ROW_SMOOTHING_SIGMA`), not a validated optimum.
- **Verdict**: feasible to implement and run end-to-end (no crashes across
  all 17 real images, ~3-4s/image), and it does trace visually plausible
  curvature on well-behaved gels. But it does not obviously improve on the
  straight-rectangle baseline's actual purity numbers on this image set, and
  introduces its own real fragility (noise sensitivity, a smoothing-parameter
  tradeoff that cuts both ways) that the straight approach doesn't have.
  Same overall shape of result as the prior strip-tracking curve-tracing
  prototype: mixed, not a clear win, not adopted here either -- a different
  geometric tool hit a similar practical ceiling, which itself is useful
  signal for any future attempt at this problem.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d
from skimage.filters import meijering

from gel_extractor.core.image_io import load_image, to_signal
from gel_extractor.core.ladder import (
    LadderCalibration,
    LadderCalibrationError,
    UnknownLadderError,
    calibrate_ladder,
    get_ladder_bands,
)
from gel_extractor.core.lanes import Lane, detect_bottom_edge_artifact_start, detect_comb_fringe_end, detect_lanes
from gel_extractor.purity.analysis import (
    DEFAULT_LOW_SIGNAL_FRACTION,
    DEFAULT_MW_TOLERANCE_PERCENT,
    LadderNotCalibratedError,
    LaneResult,
    _analyze_lane_detailed,
)

# Background-flattening radius, expressed as a multiple of this image's own
# detected reference lane width (median of `detect_lanes`' runs) rather than
# a fixed pixel count -- so it scales with image resolution instead of
# silently breaking on a differently-scanned/resized image. Must be large
# relative to a lane's own width, or the "background" estimate would just
# smooth away real lane structure instead of the slow gel-wide shading it's
# meant to capture.
DEFAULT_BACKGROUND_SIGMA_FACTOR = 4.0
DEFAULT_MIN_BACKGROUND_SIGMA = 20.0

# Ridge-filter scales, also expressed as fractions of the reference lane
# width -- a real lane's ridge is roughly as wide as the lane itself, so the
# filter needs scales spanning a meaningful fraction of that width to
# respond to it (too small only catches sub-band texture; too large starts
# blurring across whole lanes).
DEFAULT_RIDGE_SIGMA_FRACTIONS = (0.06, 0.15, 0.3)
DEFAULT_MIN_RIDGE_SIGMA = 1.0

# How much of a lane's own straight width to sum across, centered on the
# traced centerline, when building its intensity profile.
DEFAULT_PROFILE_HALF_WIDTH_FRACTION = 0.4

# Row-axis smoothing for the traced centerline -- expressed as a fraction of
# the cropped (resolving-region) height, since real gel curvature changes
# gradually over hundreds of rows, not row-to-row; this suppresses
# ridge-response noise jitter without flattening real curvature trends.
DEFAULT_ROW_SMOOTHING_FRACTION = 0.06
DEFAULT_MIN_ROW_SMOOTHING_SIGMA = 8.0


@dataclass(frozen=True)
class TracedLane:
    """One lane's traced geometry, for debug visualization and inspection."""

    lane_index: int  # matches Lane.index
    is_ladder: bool
    x_start: int
    x_end: int
    left_bound: int  # hard column bound -- midpoint to the left neighbor (or 0)
    right_bound: int  # hard column bound -- midpoint to the right neighbor (or image width)
    top_bound: int  # row where this lane's comb/well fringe ends (own straight rectangle)
    bottom_bound: int  # row where the shared bottom edge artifact begins
    centers: np.ndarray  # per-row traced center-column, one entry per row in [top_bound, bottom_bound)


@dataclass(frozen=True)
class RidgeAnalysisDebugInfo:
    """Traced-geometry detail for `scripts/compare_ridge_vs_straight.py` overlays."""

    traces: list[TracedLane]
    reference_lane_width: float


def _lane_neighbor_bounds(lanes: list[Lane], width: int, reference_width: float) -> list[tuple[int, int]]:
    """Hard column bounds for each lane: the midpoint to its nearest neighbor.

    Implements the documented "a search window can reach into a neighboring
    lane's own signal, producing a false read instead of a correct
    not-found" bug: both centerline tracing and profile sampling for a lane
    are restricted to at most the midpoint between it and its nearest
    neighbor on each side, so they can never see into a close neighbor's own
    content regardless of how wide the lane's own rectangle or profile
    window is.

    The OUTERMOST lane on either side has no neighbor on its outer side, so
    that bound can't be a midpoint -- found by real-image validation
    (2026-07-16, `8.6.25 Protein Purity.tif`'s ladder lane) to matter in
    practice, not just in theory: leaving it unbounded out to the image edge
    let the centerline-tracing step's weighted centroid get dragged 150+ px
    away from the lane's own straight rectangle by the gel slab's own
    physical edge (itself a strong, literal elongated boundary that a ridge
    filter responds to just as readily as a real lane -- the same root cause
    Attempt 1 of the rectangle-lane over-segmentation problem hit, see
    AGENTS.md Implementation Status). Fixed the same way as the inter-lane
    case: bound the outer side too, to at most one reference lane width
    beyond the lane's own straight rectangle.
    """
    ordered = sorted(lanes, key=lambda lane: lane.x_start)
    bounds_by_order: list[tuple[int, int]] = []
    for i, lane in enumerate(ordered):
        if i > 0:
            left_bound = (ordered[i - 1].x_end + lane.x_start) // 2
        else:
            left_bound = max(0, int(lane.x_start - reference_width))
        if i < len(ordered) - 1:
            right_bound = (lane.x_end + ordered[i + 1].x_start) // 2
        else:
            right_bound = min(width, int(lane.x_end + reference_width))
        bounds_by_order.append((left_bound, right_bound))
    # Re-express in the original (already left-to-right, per detect_lanes) order.
    order_index = {id(lane): i for i, lane in enumerate(ordered)}
    return [bounds_by_order[order_index[id(lane)]] for lane in lanes]


def _reference_lane_width(lanes: list[Lane]) -> float:
    widths = [lane.x_end - lane.x_start for lane in lanes]
    return float(np.median(widths)) if widths else 1.0


def compute_ridge_response(signal: np.ndarray, reference_width: float) -> np.ndarray:
    """Background-flatten `signal` and run a whole-image ridge/vesselness filter.

    `reference_width` (this image's own median detected lane width, from
    `detect_lanes` -- never a fixed pixel constant or the physical comb
    pitch) scales both the background-flattening radius and the filter's
    own scales, so the same code works across differently-scaled images.
    """
    background_sigma = max(reference_width * DEFAULT_BACKGROUND_SIGMA_FACTOR, DEFAULT_MIN_BACKGROUND_SIGMA)
    background = gaussian_filter(signal, sigma=background_sigma)
    flattened = np.clip(signal - background, 0, None)

    sigmas = [max(reference_width * f, DEFAULT_MIN_RIDGE_SIGMA) for f in DEFAULT_RIDGE_SIGMA_FRACTIONS]
    # Lanes are BRIGHTER streaks in `signal` (see image_io.to_signal: higher
    # = more signal), so this looks for white, not black, ridges.
    return meijering(flattened, sigmas=sigmas, black_ridges=False)


def trace_centerline(
    ridge_response: np.ndarray,
    lane: Lane,
    left_bound: int,
    right_bound: int,
    top: int,
    bottom: int,
    smoothing_sigma: float,
) -> np.ndarray:
    """Trace one lane's per-row centerline as the ridge-response-weighted centroid.

    Restricted to `[left_bound, right_bound)` -- see `_lane_neighbor_bounds`.
    A row with no ridge response at all in that window (e.g. a blank
    stretch) falls back to the previous row's traced position, or this
    lane's own straight-rectangle midpoint for the very first row -- never
    an arbitrary column. The raw per-row centroid is then smoothed along the
    row axis, since real gel curvature changes gradually rather than
    row-to-row.
    """
    n_rows = bottom - top
    if n_rows <= 0:
        return np.empty(0, dtype=np.float64)

    cols = np.arange(left_bound, right_bound, dtype=np.float64)
    fallback = (lane.x_start + lane.x_end) / 2.0
    centers = np.empty(n_rows, dtype=np.float64)

    window = ridge_response[top:bottom, left_bound:right_bound]
    row_totals = window.sum(axis=1)
    for i in range(n_rows):
        total = row_totals[i]
        if total > 0:
            centers[i] = (cols * window[i]).sum() / total
        else:
            centers[i] = centers[i - 1] if i > 0 else fallback

    centers = gaussian_filter1d(centers, sigma=smoothing_sigma, mode="nearest")
    return np.clip(centers, left_bound, max(left_bound, right_bound - 1))


def extract_curved_profile(
    signal: np.ndarray,
    centers: np.ndarray,
    top: int,
    half_width: float,
    left_bound: int,
    right_bound: int,
) -> np.ndarray:
    """Sum the ORIGINAL (unfiltered) signal in a window around a traced centerline.

    Produces a profile compatible with `core.bands.detect_bands` /
    `correct_baseline` (via `purity.analysis._analyze_lane_detailed`,
    unchanged) -- one intensity value per row, exactly like the straight-
    rectangle approach's `cropped.sum(axis=1)`, just following the curved
    path instead of a fixed column range. The sampling window is always
    clipped to `[left_bound, right_bound)`, so it can never reach into a
    neighboring lane's own columns even if `half_width` would otherwise
    extend past them.
    """
    profile = np.empty(len(centers), dtype=np.float64)
    for i, center in enumerate(centers):
        row = top + i
        lo = max(left_bound, int(round(center - half_width)))
        hi = min(right_bound, int(round(center + half_width)) + 1)
        profile[i] = signal[row, lo:hi].sum() if hi > lo else 0.0
    return profile


def _resolve_known_mws(ladder: str | None, ladder_bands: list[float] | None) -> list[float] | None:
    if ladder_bands is not None:
        return ladder_bands
    if ladder is not None:
        try:
            return get_ladder_bands(ladder)
        except UnknownLadderError:
            return None
    return None


def analyze_image_ridge(
    path: Path | str,
    target_mw: float,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
) -> tuple[list[LaneResult], int, RidgeAnalysisDebugInfo]:
    """Ridge-traced counterpart to `purity.analysis.analyze_image`.

    Same signature and return shape (results, ladder_lane_index, debug_info)
    as the straight-rectangle pipeline, so a comparison script can call both
    against the same image/arguments -- only `debug_info`'s type differs
    (`RidgeAnalysisDebugInfo` instead of `AnalysisDebugInfo`), since the
    underlying detail (traced centerlines) is specific to this approach.
    Lane count/identity, vertical cropping, ladder calibration, band
    detection, and the purity formula are all identical to the
    straight-rectangle pipeline -- see this module's docstring for exactly
    which pieces are reused unchanged. Only how each lane's 1D profile is
    built differs.
    """
    image = load_image(path)
    signal = to_signal(image)
    lanes = detect_lanes(signal)
    if not lanes:
        raise ValueError(f"No lanes detected in {path!r}")

    ladder_idx = ladder_lane_index if ladder_lane_index is not None else 0
    if not (0 <= ladder_idx < len(lanes)):
        raise ValueError(f"--ladder-lane is out of range: got index {ladder_idx}, have {len(lanes)} lane(s)")

    known_mws = _resolve_known_mws(ladder, ladder_bands)

    width = signal.shape[1]
    reference_width = _reference_lane_width(lanes)
    neighbor_bounds = _lane_neighbor_bounds(lanes, width, reference_width)
    ridge_response = compute_ridge_response(signal, reference_width)

    all_lanes_mask = np.zeros(width, dtype=bool)
    for lane in lanes:
        all_lanes_mask[lane.x_start : lane.x_end] = True
    bottom_bound = detect_bottom_edge_artifact_start(signal[:, all_lanes_mask])

    def trace_and_extract(lane: Lane, left_bound: int, right_bound: int) -> tuple[np.ndarray, np.ndarray, int]:
        lane_columns = signal[:, lane.x_start : lane.x_end]
        top_bound = detect_comb_fringe_end(lane_columns)
        n_rows = max(bottom_bound - top_bound, 0)
        row_smoothing_sigma = max(n_rows * DEFAULT_ROW_SMOOTHING_FRACTION, DEFAULT_MIN_ROW_SMOOTHING_SIGMA)
        centers = trace_centerline(ridge_response, lane, left_bound, right_bound, top_bound, bottom_bound, row_smoothing_sigma)
        half_width = (lane.x_end - lane.x_start) * DEFAULT_PROFILE_HALF_WIDTH_FRACTION
        profile = extract_curved_profile(signal, centers, top_bound, half_width, left_bound, right_bound)
        return profile, centers, top_bound

    ladder_lane = lanes[ladder_idx]
    ladder_left, ladder_right = neighbor_bounds[ladder_idx]
    ladder_profile, ladder_centers, ladder_top_bound = trace_and_extract(ladder_lane, ladder_left, ladder_right)

    calibration: LadderCalibration | None = None
    if known_mws is not None:
        try:
            calibration = calibrate_ladder(ladder_profile, known_mws)
        except LadderCalibrationError:
            calibration = None

    if calibration is None and not allow_heuristic:
        raise LadderNotCalibratedError(
            "Could not calibrate the ladder lane against known band sizes. "
            "Pass --ladder-bands with the correct sizes, or --allow-heuristic "
            "to fall back to a largest-band heuristic instead."
        )

    sample_entries = [(i, lane) for i, lane in enumerate(lanes) if i != ladder_idx]

    if lane_index is not None:
        if not (1 <= lane_index <= len(sample_entries)):
            raise ValueError(f"--lane is out of range: got {lane_index}, have {len(sample_entries)} sample lane(s)")
        selected = [(lane_index, sample_entries[lane_index - 1])]
    else:
        selected = list(enumerate(sample_entries, start=1))

    results: list[LaneResult] = []
    lane_total_areas: list[float] = []
    traces = [
        TracedLane(
            lane_index=ladder_idx,
            is_ladder=True,
            x_start=int(ladder_lane.x_start),
            x_end=int(ladder_lane.x_end),
            left_bound=ladder_left,
            right_bound=ladder_right,
            top_bound=ladder_top_bound,
            bottom_bound=bottom_bound,
            centers=ladder_centers,
        )
    ]
    for display_idx, (global_idx, lane) in selected:
        left_bound, right_bound = neighbor_bounds[global_idx]
        profile, centers, top_bound = trace_and_extract(lane, left_bound, right_bound)
        # Each lane's band positions are relative to its OWN adaptive
        # top_bound, but `calibration` was fit against the ladder lane's own
        # top_bound -- re-express in the ladder's frame before calibrating.
        # Same fix as purity.analysis.analyze_image, for the same reason.
        position_offset = top_bound - ladder_top_bound
        result, _bands, _target_bands, total_area = _analyze_lane_detailed(
            profile,
            lane_index=display_idx,
            target_mw=target_mw,
            calibration=calibration,
            tolerance_percent=tolerance_percent,
            allow_heuristic=allow_heuristic,
            position_offset=position_offset,
        )
        results.append(result)
        lane_total_areas.append(total_area)
        traces.append(
            TracedLane(
                lane_index=global_idx,
                is_ladder=False,
                x_start=int(lane.x_start),
                x_end=int(lane.x_end),
                left_bound=left_bound,
                right_bound=right_bound,
                top_bound=top_bound,
                bottom_bound=bottom_bound,
                centers=centers,
            )
        )

    max_area = max(lane_total_areas, default=0.0)
    if max_area > 0:
        from dataclasses import replace

        results = [
            replace(result, low_signal=True)
            if result.purity_percent is not None and area < max_area * DEFAULT_LOW_SIGNAL_FRACTION
            else result
            for result, area in zip(results, lane_total_areas)
        ]

    debug_info = RidgeAnalysisDebugInfo(traces=traces, reference_lane_width=reference_width)
    return results, ladder_idx, debug_info
