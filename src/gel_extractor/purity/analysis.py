"""Purity workflow: target-band identification and purity % computation."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from gel_extractor.core.bands import Band, correct_baseline, detect_bands
from gel_extractor.core.image_io import load_image, to_signal
from gel_extractor.core.ladder import (
    LadderCalibration,
    LadderCalibrationError,
    UnknownLadderError,
    calibrate_ladder,
    get_ladder_bands,
)
from gel_extractor.core.lanes import detect_lanes

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


@dataclass(frozen=True)
class LaneResult:
    """Purity result for a single sample lane."""

    lane: int
    purity_percent: float | None
    confidence: str  # "mw-matched" | "heuristic" | "not-found"
    target_mw_expected: float
    matched_band_mw: float | None


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
) -> tuple[list[LaneResult], int]:
    """Run the full purity pipeline on a gel image.

    Returns `(results, ladder_lane_index)` -- the ladder lane is excluded
    from `results`. `ladder_lane_index` and `lane_index` (if given) are
    0-based and 1-based respectively, matching the CLI's `--ladder-lane` and
    `--lane` flags.
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

    calibration: LadderCalibration | None = None
    if known_mws is not None:
        ladder_profile = lanes[ladder_idx].crop(signal).sum(axis=1)
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

    results = [
        analyze_lane(
            lane.crop(signal).sum(axis=1),
            lane_index=idx,
            target_mw=target_mw,
            calibration=calibration,
            tolerance_percent=tolerance_percent,
            allow_heuristic=allow_heuristic,
        )
        for idx, lane in selected
    ]
    return results, ladder_idx


def analyze_lane(
    lane_profile: np.ndarray,
    lane_index: int,
    target_mw: float,
    calibration: LadderCalibration | None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
) -> LaneResult:
    """Compute a purity result for one sample lane's intensity profile.

    Tries MW-based target identification first (if `calibration` is given).
    If that fails to find a matching band -- or no calibration is available
    at all -- falls back to a largest-band heuristic only when
    `allow_heuristic` is set; otherwise reports "not-found" rather than
    silently guessing.
    """
    corrected = correct_baseline(lane_profile)
    bands = detect_bands(corrected)
    total_area = sum(b.area for b in bands)

    if calibration is not None:
        matched_bands, matched_mw = _match_target_band(bands, calibration, target_mw, tolerance_percent)
        if matched_bands:
            target_area = sum(b.area for b in matched_bands)
            return LaneResult(
                lane=lane_index,
                purity_percent=_safe_percent(target_area, total_area),
                confidence="mw-matched",
                target_mw_expected=target_mw,
                matched_band_mw=matched_mw,
            )

    if not allow_heuristic:
        return LaneResult(
            lane=lane_index,
            purity_percent=None,
            confidence="not-found",
            target_mw_expected=target_mw,
            matched_band_mw=None,
        )

    target_bands = _largest_band(bands)
    target_area = sum(b.area for b in target_bands)
    return LaneResult(
        lane=lane_index,
        purity_percent=_safe_percent(target_area, total_area),
        confidence="heuristic",
        target_mw_expected=target_mw,
        matched_band_mw=None,
    )


def _match_target_band(
    bands: list[Band],
    calibration: LadderCalibration,
    target_mw: float,
    tolerance_percent: float,
) -> tuple[list[Band], float | None]:
    """Sum all bands within tolerance of target_mw (e.g. doublets), not just the nearest one."""
    tolerance = target_mw * (tolerance_percent / 100.0)
    matches = [(band, calibration.mw_at(band.center)) for band in bands]
    matches = [(band, mw) for band, mw in matches if abs(mw - target_mw) <= tolerance]
    if not matches:
        return [], None
    closest_mw = min((mw for _, mw in matches), key=lambda mw: abs(mw - target_mw))
    return [band for band, _ in matches], closest_mw


def _largest_band(bands: list[Band]) -> list[Band]:
    if not bands:
        return []
    return [max(bands, key=lambda b: b.area)]


def _safe_percent(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def _resolve_known_mws(ladder: str | None, ladder_bands: list[float] | None) -> list[float] | None:
    if ladder_bands is not None:
        return ladder_bands
    if ladder is not None:
        try:
            return get_ladder_bands(ladder)
        except UnknownLadderError:
            return None
    return None
