"""Purity workflow: target-band identification and purity % computation."""

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from gel_extractor.core.bands import Band, correct_baseline, detect_bands
from gel_extractor.core.image_io import load_image, to_signal
from gel_extractor.core.ladder import (
    LADDER_MIN_SNR,
    LadderCalibration,
    LadderCalibrationError,
    UnknownLadderError,
    calibrate_ladder,
    get_ladder_bands,
)
from gel_extractor.core.lanes import Lane, detect_bottom_edge_artifact_start, detect_comb_fringe_end, detect_lanes

# Placeholder -- see AGENTS.md Design Decisions ("±15-20% of expected MW,
# deliberately approximate, to be tuned empirically"). Moved from the
# midpoint (17.5%) to the top of that originally-discussed range after real
# testing (2026-07-13): on a real gel, the true target band sat ~19% off the
# calibrated MW (itself imperfect -- see core.ladder's window-search notes),
# just outside 17.5% -- at 20%, 8 of 10 real sample lanes matched
# consistently on the same MW (34.6-34.9 kDa) instead of falling back to
# "not-found" or matching a coincidental smaller band instead of the real
# dominant one. Still coarse; expect further tuning with more real images.
DEFAULT_MW_TOLERANCE_PERCENT = 20.0

# Which band counts as "the target" in a lane -- see AGENTS.md Implementation
# Status (2026-07-17 empirical test) and Open Questions for the full history.
# "largest" (the default): the biggest detected band always wins, regardless
# of MW -- empirically closer to confirmed ground-truth purity on this
# project's real images than MW-based selection, on 2 of 4 registered
# methods, a wash on the other 2. Ladder calibration still runs when
# possible, purely to VERIFY the selected band's MW against --target-mw and
# flag a mismatch (confidence="mw-mismatch") -- it never gates selection in
# this mode. "mw-strict" (the original, still-available behavior): only a
# band within --mw-tolerance of --target-mw counts as the target at all;
# falls back to "largest" only when --allow-heuristic is passed and no band
# matches. Kept as an explicit opt-in, not deleted, since it's the only
# mode with an a-priori external check on band identity.
DEFAULT_BAND_SELECTION = "largest"
BAND_SELECTIONS = ("largest", "mw-strict")

# Cross-lane crop-artifact corroboration -- see AGENTS.md Known Limitations
# (2026-07-22 entry) for the confirmed bug this exists to catch:
# `detect_comb_fringe_end` can leave a broad, roughly uniform-intensity
# leftover glued to a lane's own crop boundary that's wide/bright enough to
# win band_selection="largest" on area alone, or simply inflate a lane's
# total_area (and thus dilute purity_percent) even when it isn't selected.
# A single lane's width/position alone isn't a safe enough signal to act
# on -- calibrated against all 15 real ground-truth images, a per-lane
# "this band is unusually wide" rule misfires on legitimately wide real
# bands elsewhere in the gel (confirmed real regressions, including nearly
# erasing a lane that otherwise matched confirmed purity almost exactly).
# Requiring the SAME suspect band (by absolute row range) to recur across
# most sample lanes of the SAME image is far more specific: a real band's
# position is lane-specific, but a shared physical crop-boundary artifact
# recurs at essentially the same row in every lane. Only lanes
# contributing to a corroborated cluster get anything excluded.
ARTIFACT_CORROBORATION_ROW_SLACK = 10
MIN_ARTIFACT_CORROBORATION_LANES = 3

# Placeholder -- see AGENTS.md Known Limitations ("dilution-detectability
# limit," confirmed real by the user 2026-07-14): at high dilution, faint
# contaminant bands drop below the detection floor before the target band
# does, inflating apparent purity. Rather than silently reporting that
# inflated number at face value, a lane whose total detected signal is under
# this fraction of the most-concentrated lane in the same image is flagged
# `low_signal` -- not a fix for the underlying limit-of-detection effect
# (there isn't one), just an honest confidence signal so a low-signal lane's
# purity % isn't read with the same weight as a well-loaded one. Not yet
# empirically tuned against real dilution series.
DEFAULT_LOW_SIGNAL_FRACTION = 0.2


@dataclass(frozen=True)
class LaneResult:
    """Purity result for a single sample lane."""

    lane: int
    purity_percent: int | None
    confidence: str  # "mw-matched" | "mw-mismatch" | "largest-unverified" | "heuristic" | "not-found"
    target_mw_expected: float | None
    matched_band_mw: float | None
    # True when this lane's total detected signal is faint relative to the
    # most-concentrated lane in the same image -- a likely high-dilution
    # lane where the dilution-detectability limit above may be inflating
    # purity_percent. Only ever set by analyze_image (a whole-image, cross-
    # lane comparison); analyze_lane() alone has no other lane to compare
    # against and always leaves this False.
    low_signal: bool = False


@dataclass(frozen=True)
class Centerline:
    """A per-row x-position curve, for drawing an alternative method's traced
    lane path in `--debug` output (see `purity.debug_viz`).

    Every alternative lane-geometry method (`purity.methods`) shapes its own
    native curve representation (a full-image-row array, a crop-relative-row
    array, a raw scattered-point path, an `x_at_row`-method object, ...) into
    this one common type -- keeping `debug_viz` free of any dependency on the
    methods themselves. `rows`/`xs` need not cover every row (e.g. a method
    might only trace within one lane's own vertical crop); `x_at_row`
    interpolates for anything in between.
    """

    rows: np.ndarray
    xs: np.ndarray

    def x_at_row(self, row: float) -> float:
        return float(np.interp(row, self.rows, self.xs))


@dataclass(frozen=True)
class LaneDebugInfo:
    """Raw per-lane detection detail, for the `--debug` visualization output.

    Not part of `LaneResult` -- that stays a clean, stable result object for
    table/CSV/JSON output (see AGENTS.md "modular, swappable architecture").
    This carries the underlying `Band` objects a debug renderer needs but
    that end-user output formats never should.
    """

    lane: int  # matches LaneResult.lane for sample lanes; 0 (unused) for the ladder
    x_start: int
    x_end: int
    top_bound: int  # row where this lane's comb/well fringe ends (adaptive, per-lane)
    bottom_bound: int  # row where the shared bottom edge artifact begins (same for every lane)
    is_ladder: bool
    bands: list[Band]
    target_bands: list[Band]  # subset of `bands` counted as the target/matched signal
    # Alternative-method geometry, both optional and both None for the
    # default straight-rectangle method (nothing to add to the plain box):
    centerline: "Centerline | None" = None  # a traced curve, drawn as an overlay line
    annotation: str | None = None  # short text for geometry that isn't a curve (e.g. a per-lane row shift)


@dataclass(frozen=True)
class AnalysisDebugInfo:
    """Full per-lane detection detail for one analyzed image, for `--debug`."""

    lanes: list[LaneDebugInfo]
    ladder_calibration: LadderCalibration | None


class LadderNotCalibratedError(RuntimeError):
    """Raised when the ladder can't be calibrated and --allow-heuristic wasn't given."""


def _default_crop_lane(signal: np.ndarray, lane: Lane, bottom_bound: int) -> tuple[np.ndarray, int, "Centerline | None"]:
    """The straight-rectangle geometry: column-range crop, summed to a profile.

    This is the default `crop_lane` -- see `analyze_image`'s `crop_lane`
    parameter. Returns `(profile, top_bound, centerline)`; `centerline` is
    always `None` here since a straight rectangle has no curve to draw.
    """
    cropped, top_bound = _adaptive_crop(signal, lane, bottom_bound)
    return cropped.sum(axis=1), top_bound, None


def analyze_image(
    path: Path | str,
    target_mw: float | None,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    band_selection: str = DEFAULT_BAND_SELECTION,
    crop_lane=None,
) -> tuple[list[LaneResult], int, AnalysisDebugInfo]:
    """Run the full purity pipeline on a gel image.

    Returns `(results, ladder_lane_index, debug_info)` -- the ladder lane is
    excluded from `results`. `ladder_lane_index` and `lane_index` (if given)
    are 0-based and 1-based respectively, matching the CLI's `--ladder-lane`
    and `--lane` flags. `debug_info` carries the raw lane/band detections
    behind the results, for the `--debug` visualization output -- see
    `purity.debug_viz`.

    `target_mw` may be `None` only with `band_selection="largest"` (the
    default) -- added 2026-07-20 for batches spanning many different
    proteins with no per-image expected MW available. The largest band is
    still selected and, when the ladder calibrates, its real measured MW is
    still reported (`matched_band_mw`) -- there's just nothing to verify it
    against, so `confidence` becomes `"largest-unverified"` instead of
    `"mw-matched"`/`"mw-mismatch"`. `band_selection="mw-strict"` needs
    `target_mw` to select a band at all, so `None` there raises `ValueError`.

    `band_selection` (`"largest"` default, or `"mw-strict"`) decides which
    band counts as the target -- see the constant's own comment above for
    the full rationale. `crop_lane` is the one pluggable seam an alternative
    lane-geometry method (see `purity.methods`) needs: a callable
    `(signal, lane, bottom_bound) -> (profile, top_bound, centerline)`,
    defaulting to `_default_crop_lane` (today's straight-rectangle
    behavior, unchanged). Everything else in this function -- ladder
    calibration, band selection, low_signal flagging, debug-info assembly --
    is already geometry-agnostic and shared by every method, straight or
    curved, so there's exactly one control-flow copy to maintain.
    """
    image = load_image(path)
    signal = to_signal(image)
    return _analyze_signal(
        signal,
        path,
        target_mw,
        ladder=ladder,
        ladder_bands=ladder_bands,
        ladder_lane_index=ladder_lane_index,
        lane_index=lane_index,
        tolerance_percent=tolerance_percent,
        allow_heuristic=allow_heuristic,
        band_selection=band_selection,
        crop_lane=crop_lane,
    )


def _analyze_signal(
    signal: np.ndarray,
    path_for_errors,
    target_mw: float | None,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    band_selection: str = DEFAULT_BAND_SELECTION,
    crop_lane=None,
) -> tuple[list[LaneResult], int, AnalysisDebugInfo]:
    """Same pipeline as `analyze_image`, but starting from an already-loaded
    `signal` array rather than a path -- lets a `purity.methods` adapter that
    already needs `signal` for its own geometry computation (e.g. tracing a
    curve) avoid loading/decoding the image a second time. `path_for_errors`
    is only used to format error messages the same way `analyze_image` does.
    """
    if band_selection not in BAND_SELECTIONS:
        raise ValueError(f"Unknown band_selection {band_selection!r} -- expected one of {BAND_SELECTIONS}")
    crop_lane = crop_lane or _default_crop_lane
    lanes = detect_lanes(signal)
    if not lanes:
        raise ValueError(f"No lanes detected in {path_for_errors!r}")

    ladder_idx = ladder_lane_index if ladder_lane_index is not None else 0
    if not (0 <= ladder_idx < len(lanes)):
        raise ValueError(f"--ladder-lane is out of range: got index {ladder_idx}, have {len(lanes)} lane(s)")

    known_mws = _resolve_known_mws(ladder, ladder_bands)

    # Adaptive vertical bounds (see core.lanes, 2026-07-14): the bottom
    # cassette/tape-edge artifact is consistent across the whole gel width,
    # so it's detected once using every lane combined; the comb/well fringe
    # at the top varies lane to lane, so it's detected per lane below.
    all_lanes_mask = np.zeros(signal.shape[1], dtype=bool)
    for lane in lanes:
        all_lanes_mask[lane.x_start : lane.x_end] = True
    bottom_bound = detect_bottom_edge_artifact_start(signal[:, all_lanes_mask])

    ladder_lane = lanes[ladder_idx]
    ladder_profile, ladder_top_bound, ladder_centerline = crop_lane(signal, ladder_lane, bottom_bound)

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

    sample_lanes = [lane for i, lane in enumerate(lanes) if i != ladder_idx]

    if lane_index is not None:
        if not (1 <= lane_index <= len(sample_lanes)):
            raise ValueError(f"--lane is out of range: got {lane_index}, have {len(sample_lanes)} sample lane(s)")
        selected = [(lane_index, sample_lanes[lane_index - 1])]
    else:
        selected = list(enumerate(sample_lanes, start=1))

    # Two passes, not one: cross-lane crop-artifact corroboration (see
    # `_corroborated_crop_artifact_bands`) needs every lane's bands/top_bound
    # up front to decide what recurs across the image, before any lane's
    # final result can be computed. The first pass crops and detects bands
    # for every lane; `_analyze_lane_detailed` below recomputes bands from
    # the same profile a second time (cheap -- a 1D baseline-correct plus
    # peak-find, not another crop) rather than threading precomputed bands
    # through its contract, since that contract is also used directly by
    # `analyze_lane`'s single-lane callers with no cross-lane context at all.
    lane_crops: dict[int, tuple[Lane, np.ndarray, int, "Centerline | None"]] = {}
    lane_bands_for_corroboration: dict[int, tuple[int, list[Band]]] = {}
    for idx, lane in selected:
        profile, top_bound, centerline = crop_lane(signal, lane, bottom_bound)
        lane_crops[idx] = (lane, profile, top_bound, centerline)
        lane_bands_for_corroboration[idx] = (top_bound, detect_bands(correct_baseline(profile)))
    crop_artifact_bands = _corroborated_crop_artifact_bands(lane_bands_for_corroboration)

    results: list[LaneResult] = []
    lane_total_areas: list[float] = []
    lane_debug_info: list[LaneDebugInfo] = []
    for idx, (lane, profile, top_bound, centerline) in lane_crops.items():
        # Each lane's band positions are relative to *its own* adaptive
        # top_bound, but `calibration` was fit against the ladder lane's own
        # top_bound -- if the two differ (comb depth varies lane to lane,
        # see core.lanes), "position 0" in each isn't the same physical row.
        # Re-express this lane's positions in the ladder's frame before
        # calibrating, or MW comes out silently wrong by however much the
        # two crops differ (confirmed as a real bug on a real image, not
        # theoretical -- see AGENTS.md Implementation Status, 2026-07-14).
        position_offset = top_bound - ladder_top_bound
        excluded = [crop_artifact_bands[idx]] if idx in crop_artifact_bands else None
        result, bands, target_bands, total_area = _analyze_lane_detailed(
            profile,
            lane_index=idx,
            target_mw=target_mw,
            calibration=calibration,
            tolerance_percent=tolerance_percent,
            allow_heuristic=allow_heuristic,
            position_offset=position_offset,
            band_selection=band_selection,
            exclude_bands=excluded,
        )
        results.append(result)
        lane_total_areas.append(total_area)
        lane_debug_info.append(
            LaneDebugInfo(
                lane=idx,
                x_start=lane.x_start,
                x_end=lane.x_end,
                top_bound=top_bound,
                bottom_bound=bottom_bound,
                is_ladder=False,
                bands=bands,
                target_bands=target_bands,
                centerline=centerline,
            )
        )

    # Recomputed with the ladder-specific noise floor (see core.ladder) so the
    # debug image shows exactly the bands calibration actually used, not a
    # different set from the general-purpose default.
    ladder_bands_detected = detect_bands(correct_baseline(ladder_profile), min_snr=LADDER_MIN_SNR)
    debug_info = AnalysisDebugInfo(
        lanes=[
            LaneDebugInfo(
                lane=0,
                x_start=ladder_lane.x_start,
                x_end=ladder_lane.x_end,
                top_bound=ladder_top_bound,
                bottom_bound=bottom_bound,
                is_ladder=True,
                bands=ladder_bands_detected,
                target_bands=[],
                centerline=ladder_centerline,
            ),
            *lane_debug_info,
        ],
        ladder_calibration=calibration,
    )

    # Flag lanes whose total detected signal is faint relative to the most-
    # concentrated lane in this image -- likely high-dilution lanes where the
    # dilution-detectability limit (see DEFAULT_LOW_SIGNAL_FRACTION) may be
    # inflating purity_percent. A single --lane run has nothing else in the
    # series to compare against, so max_area is just that one lane's own
    # area and nothing gets flagged -- this is a whole-series comparison by
    # design, not a per-lane property.
    max_area = max(lane_total_areas, default=0.0)
    if max_area > 0:
        results = [
            replace(result, low_signal=True)
            if result.purity_percent is not None and area < max_area * DEFAULT_LOW_SIGNAL_FRACTION
            else result
            for result, area in zip(results, lane_total_areas)
        ]

    return results, ladder_idx, debug_info


def analyze_lane(
    lane_profile: np.ndarray,
    lane_index: int,
    target_mw: float | None,
    calibration: LadderCalibration | None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    position_offset: float = 0.0,
    band_selection: str = DEFAULT_BAND_SELECTION,
) -> LaneResult:
    """Compute a purity result for one sample lane's intensity profile.

    `band_selection` (`"largest"` default, or `"mw-strict"`) decides which
    band counts as the target -- see `DEFAULT_BAND_SELECTION`'s comment for
    the full rationale. `position_offset` re-expresses this lane's band
    positions in the ladder lane's own coordinate frame before calibrating
    -- see `analyze_image` for why that matters; 0.0 (the default) assumes
    both lanes share a frame, true whenever they were cropped identically.
    """
    result, _bands, _target_bands, _total_area = _analyze_lane_detailed(
        lane_profile,
        lane_index=lane_index,
        target_mw=target_mw,
        calibration=calibration,
        tolerance_percent=tolerance_percent,
        allow_heuristic=allow_heuristic,
        position_offset=position_offset,
        band_selection=band_selection,
    )
    return result


def _analyze_lane_detailed(
    lane_profile: np.ndarray,
    lane_index: int,
    target_mw: float | None,
    calibration: LadderCalibration | None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    position_offset: float = 0.0,
    band_selection: str = DEFAULT_BAND_SELECTION,
    exclude_bands: list[Band] | None = None,
) -> tuple[LaneResult, list[Band], list[Band], float]:
    """Same as `analyze_lane`, but also returns the raw bands, the subset
    counted as the target, and this lane's total detected band area --
    the raw bands/target subset are for `--debug` visualization (see
    `LaneDebugInfo`), the total area is for analyze_image's cross-lane
    `low_signal` comparison (see `LaneResult`). `analyze_lane` stays the
    stable public entry point returning just the result; this is where the
    actual work happens.

    `exclude_bands`, when given, drops any detected band matching one in
    that list (by value -- `Band` is a frozen dataclass) before anything
    else runs, so it affects both `total_area` (the purity% denominator)
    and which band is eligible to be selected as target. Only
    `analyze_image` passes this, for a cross-lane-corroborated crop
    artifact (see `_corroborated_crop_artifact_bands`); `analyze_lane`'s
    single-lane callers have no cross-lane context to corroborate against,
    so they always leave it `None`.

    Two independent branches below, deliberately not interleaved: `"mw-strict"`
    is byte-for-byte the original selection logic (a band only counts as
    target if its calibrated MW is within tolerance; falls back to the
    largest band only when `allow_heuristic` and nothing matched). `"largest"`
    (the default) always selects the largest band regardless of MW, and uses
    calibration -- when available -- only to VERIFY that selection and flag
    a mismatch, never to gate it. See `DEFAULT_BAND_SELECTION`'s module-level
    comment and AGENTS.md's 2026-07-17 entries for why.
    """
    if band_selection not in BAND_SELECTIONS:
        raise ValueError(f"Unknown band_selection {band_selection!r} -- expected one of {BAND_SELECTIONS}")
    if target_mw is None and band_selection == "mw-strict":
        raise ValueError("target_mw is required when band_selection='mw-strict' (only 'largest' allows None)")

    corrected = correct_baseline(lane_profile)
    bands = detect_bands(corrected)
    if exclude_bands:
        bands = [b for b in bands if b not in exclude_bands]
    total_area = sum(b.area for b in bands)

    if not bands:
        # No detected signal at all -- report "not-found" rather than a
        # fabricated 0% (which would otherwise fall out of _safe_percent(0, 0)
        # and misleadingly read as "confidently measured, all contaminant").
        # A lane with nothing detectable hasn't been measured at all -- could
        # be a genuinely blank/degenerate sample, or a spurious lane detection
        # (see AGENTS.md Known Limitations, lane over-segmentation).
        return (
            LaneResult(
                lane=lane_index,
                purity_percent=None,
                confidence="not-found",
                target_mw_expected=target_mw,
                matched_band_mw=None,
            ),
            bands,
            [],
            total_area,
        )

    if band_selection == "mw-strict":
        if calibration is not None:
            matched_bands, matched_mw = _match_target_band(
                bands, calibration, target_mw, tolerance_percent, position_offset
            )
            if matched_bands:
                target_area = sum(b.area for b in matched_bands)
                return (
                    LaneResult(
                        lane=lane_index,
                        purity_percent=_safe_percent(target_area, total_area),
                        confidence="mw-matched",
                        target_mw_expected=target_mw,
                        matched_band_mw=matched_mw,
                    ),
                    bands,
                    matched_bands,
                    total_area,
                )

        if not allow_heuristic:
            return (
                LaneResult(
                    lane=lane_index,
                    purity_percent=None,
                    confidence="not-found",
                    target_mw_expected=target_mw,
                    matched_band_mw=None,
                ),
                bands,
                [],
                total_area,
            )

        target_bands = _largest_band(bands)
        target_area = sum(b.area for b in target_bands)
        return (
            LaneResult(
                lane=lane_index,
                purity_percent=_safe_percent(target_area, total_area),
                confidence="heuristic",
                target_mw_expected=target_mw,
                matched_band_mw=None,
            ),
            bands,
            target_bands,
            total_area,
        )

    # band_selection == "largest" (the default): the largest band always
    # wins regardless of MW. Calibration, when available, only verifies that
    # choice against target_mw and flags a mismatch -- it never gates
    # selection or `purity_percent`, which is why the empirical accuracy
    # numbers already measured for this mode (see AGENTS.md, 2026-07-17)
    # hold regardless of whether the ladder happens to calibrate.
    target_bands = _largest_band(bands)
    target_area = sum(b.area for b in target_bands)
    purity_percent = _safe_percent(target_area, total_area)

    if calibration is not None:
        matched_mw = calibration.mw_at(target_bands[0].center + position_offset)
        if target_mw is None:
            # No expected MW to verify against (see analyze_image's
            # docstring, 2026-07-20) -- report the real calibrated MW without
            # pretending to know whether it's right.
            confidence = "largest-unverified"
        else:
            confidence = "mw-matched" if _mw_within_tolerance(matched_mw, target_mw, tolerance_percent) else "mw-mismatch"
        return (
            LaneResult(
                lane=lane_index,
                purity_percent=purity_percent,
                confidence=confidence,
                target_mw_expected=target_mw,
                matched_band_mw=matched_mw,
            ),
            bands,
            target_bands,
            total_area,
        )

    if not allow_heuristic:
        # No calibration at all -- e.g. the ladder never calibrated -- and
        # the caller hasn't opted into an unverifiable guess. Same gate
        # "mw-strict" uses in its own uncalibrated case; largest-band
        # selection doesn't change how conservative this refusal is.
        return (
            LaneResult(
                lane=lane_index,
                purity_percent=None,
                confidence="not-found",
                target_mw_expected=target_mw,
                matched_band_mw=None,
            ),
            bands,
            [],
            total_area,
        )

    return (
        LaneResult(
            lane=lane_index,
            purity_percent=purity_percent,
            confidence="heuristic",
            target_mw_expected=target_mw,
            matched_band_mw=None,
        ),
        bands,
        target_bands,
        total_area,
    )


def _adaptive_crop(signal: np.ndarray, lane: Lane, bottom_bound: int) -> tuple[np.ndarray, int]:
    """Crop one lane to its real resolving-gel content.

    Excludes this lane's own comb/well fringe at the top (detected
    per-lane, since fringe depth varies lane to lane -- see
    `core.lanes.detect_comb_fringe_end`) and the shared bottom cassette/
    tape-edge artifact (`bottom_bound`, detected once for the whole image
    since it's consistent across every lane). Returns `(cropped, top_bound)`
    -- callers building `LaneDebugInfo` need `top_bound` to correctly place
    band boxes back onto the full image (see `purity.debug_viz`).
    """
    lane_columns = signal[:, lane.x_start : lane.x_end]
    top_bound = detect_comb_fringe_end(lane_columns)
    return lane_columns[top_bound:bottom_bound, :], top_bound


def _mw_within_tolerance(mw: float, target_mw: float, tolerance_percent: float) -> bool:
    """Is `mw` within `tolerance_percent` of `target_mw`?

    Shared by `_match_target_band` (a selection filter, in "mw-strict" mode)
    and `_analyze_lane_detailed`'s "largest" branch (a post-hoc verification
    check on an already-selected band) -- same math, two different jobs, so
    factored out rather than duplicated or called through
    `_match_target_band` itself (whose name/docstring signal multi-band
    selection, which the verification call site is not).
    """
    tolerance = target_mw * (tolerance_percent / 100.0)
    return abs(mw - target_mw) <= tolerance


def _match_target_band(
    bands: list[Band],
    calibration: LadderCalibration,
    target_mw: float,
    tolerance_percent: float,
    position_offset: float = 0.0,
) -> tuple[list[Band], float | None]:
    """Sum all bands within tolerance of target_mw (e.g. doublets), not just the nearest one.

    `position_offset` re-expresses each band's position in the ladder
    lane's own coordinate frame first -- see `analyze_image`.
    """
    matches = [(band, calibration.mw_at(band.center + position_offset)) for band in bands]
    matches = [(band, mw) for band, mw in matches if _mw_within_tolerance(mw, target_mw, tolerance_percent)]
    if not matches:
        return [], None
    closest_mw = min((mw for _, mw in matches), key=lambda mw: abs(mw - target_mw))
    return [band for band, _ in matches], closest_mw


def _largest_band(bands: list[Band]) -> list[Band]:
    if not bands:
        return []
    return [max(bands, key=lambda b: b.area)]


def _suspect_crop_artifact_band(bands: list[Band]) -> Band | None:
    """This lane's one candidate for a shared top-of-gel crop artifact: the
    band that is simultaneously the widest AND the closest to this lane's
    own crop boundary. Requires at least 2 bands -- a lone detected band has
    nothing to look anomalous relative to, so it's never flagged by this
    alone (see `_corroborated_crop_artifact_bands`, which additionally
    requires this same candidate to recur across other lanes before
    anything is actually excluded).
    """
    if len(bands) < 2:
        return None
    widest = max(bands, key=lambda b: b.end - b.start)
    closest_to_top = min(bands, key=lambda b: b.start)
    return widest if widest is closest_to_top else None


def _row_ranges_overlap(a: tuple[int, int], b: tuple[int, int], slack: int = ARTIFACT_CORROBORATION_ROW_SLACK) -> bool:
    return a[0] <= b[1] + slack and b[0] <= a[1] + slack


def _corroborated_crop_artifact_bands(lane_bands: dict[int, tuple[int, list[Band]]]) -> dict[int, Band]:
    """Cross-lane corroboration for `_suspect_crop_artifact_band` -- see the
    module-level comment above `ARTIFACT_CORROBORATION_ROW_SLACK` for why a
    single lane's signal alone isn't trusted. `lane_bands` maps each sample
    lane's index to `(top_bound, bands)`. Returns only the lanes whose
    suspect band ended up in a cluster big enough to corroborate -- every
    other lane's bands are left completely untouched, including lanes with
    no suspect band at all.
    """
    candidates: dict[int, tuple[tuple[int, int], Band]] = {}
    for lane_index, (top_bound, bands) in lane_bands.items():
        suspect = _suspect_crop_artifact_band(bands)
        if suspect is not None:
            candidates[lane_index] = ((top_bound + suspect.start, top_bound + suspect.end), suspect)

    clusters: list[dict] = []
    for lane_index, (abs_range, band) in candidates.items():
        cluster = next((c for c in clusters if _row_ranges_overlap(c["range"], abs_range)), None)
        if cluster is None:
            clusters.append({"lanes": {lane_index: band}, "range": abs_range})
            continue
        cluster["lanes"][lane_index] = band
        lo = min(cluster["range"][0], abs_range[0])
        hi = max(cluster["range"][1], abs_range[1])
        cluster["range"] = (lo, hi)

    threshold = max(MIN_ARTIFACT_CORROBORATION_LANES, len(lane_bands) // 2)
    excluded: dict[int, Band] = {}
    for cluster in clusters:
        if len(cluster["lanes"]) >= threshold:
            excluded.update(cluster["lanes"])
    return excluded


def _safe_percent(numerator: float, denominator: float) -> int:
    """Round to the nearest whole percent.

    Not 1 decimal place (the original default): given known calibration/
    detection imprecision (see AGENTS.md Known Limitations), a fractional
    percent implies more precision than the pipeline actually has.
    """
    if denominator <= 0:
        return 0
    return round(100.0 * numerator / denominator)


def _resolve_known_mws(ladder: str | None, ladder_bands: list[float] | None) -> list[float] | None:
    if ladder_bands is not None:
        return ladder_bands
    if ladder is not None:
        try:
            return get_ladder_bands(ladder)
        except UnknownLadderError:
            return None
    return None
