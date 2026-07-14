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
    confidence: str  # "mw-matched" | "heuristic" | "not-found"
    target_mw_expected: float
    matched_band_mw: float | None
    # True when this lane's total detected signal is faint relative to the
    # most-concentrated lane in the same image -- a likely high-dilution
    # lane where the dilution-detectability limit above may be inflating
    # purity_percent. Only ever set by analyze_image (a whole-image, cross-
    # lane comparison); analyze_lane() alone has no other lane to compare
    # against and always leaves this False.
    low_signal: bool = False


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


@dataclass(frozen=True)
class AnalysisDebugInfo:
    """Full per-lane detection detail for one analyzed image, for `--debug`."""

    lanes: list[LaneDebugInfo]
    ladder_calibration: LadderCalibration | None


class LadderNotCalibratedError(RuntimeError):
    """Raised when the ladder can't be calibrated and --allow-heuristic wasn't given."""


def analyze_image(
    path: Path | str,
    target_mw: float,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
) -> tuple[list[LaneResult], int, AnalysisDebugInfo]:
    """Run the full purity pipeline on a gel image.

    Returns `(results, ladder_lane_index, debug_info)` -- the ladder lane is
    excluded from `results`. `ladder_lane_index` and `lane_index` (if given)
    are 0-based and 1-based respectively, matching the CLI's `--ladder-lane`
    and `--lane` flags. `debug_info` carries the raw lane/band detections
    behind the results, for the `--debug` visualization output -- see
    `purity.debug_viz`.
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

    # Adaptive vertical bounds (see core.lanes, 2026-07-14): the bottom
    # cassette/tape-edge artifact is consistent across the whole gel width,
    # so it's detected once using every lane combined; the comb/well fringe
    # at the top varies lane to lane, so it's detected per lane below.
    all_lanes_mask = np.zeros(signal.shape[1], dtype=bool)
    for lane in lanes:
        all_lanes_mask[lane.x_start : lane.x_end] = True
    bottom_bound = detect_bottom_edge_artifact_start(signal[:, all_lanes_mask])

    ladder_lane = lanes[ladder_idx]
    ladder_cropped, ladder_top_bound = _adaptive_crop(signal, ladder_lane, bottom_bound)
    ladder_profile = ladder_cropped.sum(axis=1)

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

    results: list[LaneResult] = []
    lane_total_areas: list[float] = []
    lane_debug_info: list[LaneDebugInfo] = []
    for idx, lane in selected:
        cropped, top_bound = _adaptive_crop(signal, lane, bottom_bound)
        # Each lane's band positions are relative to *its own* adaptive
        # top_bound, but `calibration` was fit against the ladder lane's own
        # top_bound -- if the two differ (comb depth varies lane to lane,
        # see core.lanes), "position 0" in each isn't the same physical row.
        # Re-express this lane's positions in the ladder's frame before
        # calibrating, or MW comes out silently wrong by however much the
        # two crops differ (confirmed as a real bug on a real image, not
        # theoretical -- see AGENTS.md Implementation Status, 2026-07-14).
        position_offset = top_bound - ladder_top_bound
        result, bands, target_bands, total_area = _analyze_lane_detailed(
            cropped.sum(axis=1),
            lane_index=idx,
            target_mw=target_mw,
            calibration=calibration,
            tolerance_percent=tolerance_percent,
            allow_heuristic=allow_heuristic,
            position_offset=position_offset,
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
    target_mw: float,
    calibration: LadderCalibration | None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    position_offset: float = 0.0,
) -> LaneResult:
    """Compute a purity result for one sample lane's intensity profile.

    Tries MW-based target identification first (if `calibration` is given).
    If that fails to find a matching band -- or no calibration is available
    at all -- falls back to a largest-band heuristic only when
    `allow_heuristic` is set; otherwise reports "not-found" rather than
    silently guessing. `position_offset` re-expresses this lane's band
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
    )
    return result


def _analyze_lane_detailed(
    lane_profile: np.ndarray,
    lane_index: int,
    target_mw: float,
    calibration: LadderCalibration | None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    position_offset: float = 0.0,
) -> tuple[LaneResult, list[Band], list[Band], float]:
    """Same as `analyze_lane`, but also returns the raw bands, the subset
    counted as the target, and this lane's total detected band area --
    the raw bands/target subset are for `--debug` visualization (see
    `LaneDebugInfo`), the total area is for analyze_image's cross-lane
    `low_signal` comparison (see `LaneResult`). `analyze_lane` stays the
    stable public entry point returning just the result; this is where the
    actual work happens.
    """
    corrected = correct_baseline(lane_profile)
    bands = detect_bands(corrected)
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

    if calibration is not None:
        matched_bands, matched_mw = _match_target_band(bands, calibration, target_mw, tolerance_percent, position_offset)
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
    tolerance = target_mw * (tolerance_percent / 100.0)
    matches = [(band, calibration.mw_at(band.center + position_offset)) for band in bands]
    matches = [(band, mw) for band, mw in matches if abs(mw - target_mw) <= tolerance]
    if not matches:
        return [], None
    closest_mw = min((mw for _, mw in matches), key=lambda mw: abs(mw - target_mw))
    return [band for band, _ in matches], closest_mw


def _largest_band(bands: list[Band]) -> list[Band]:
    if not bands:
        return []
    return [max(bands, key=lambda b: b.area)]


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
