"""Pure request-handling logic for the API -- no Flask, no HTTP.

Ported from `scripts/hitl_ui_server.py`'s `analyze()` route (see GH issue
#1). Every band-selection/propagation decision is still delegated entirely
to the already-tested `neband.core` modules; nothing here is new
algorithm logic, only request shaping.

Two deliberate differences from the prototype server, both scoped by GH
issue #1:
- `known_mws` (the ladder's known-MW list) is a per-call parameter, not a
  server-startup global -- the prototype served a fixed set of images with
  one ladder for the whole process lifetime; a real API serves arbitrary
  images from arbitrary callers, so the ladder has to travel with each
  request.
- No correction-record JSON is written here. That file (
  `data/hitl_correction_records/*.json`) is specific to this repo's own
  offline evaluation workflow (`scripts/evaluate_human_assisted_propagation.py`),
  not a general capability of the tool -- persisting a submitted review is
  the embedding application's job (e.g. `ebase`'s own `GelImage` model, per
  the issue's Architecture section), not this package's.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from neband.core.band_propagation import (
    absolute_row,
    exclude_deleted_bands,
    find_nearest_band,
    propagate_target_band,
    row_tolerance,
)
from neband.core.bands import Band, correct_baseline, detect_bands
from neband.core.ladder import LadderCalibrationError, calibrate_ladder
from neband.core.lanes import Lane, detect_bottom_edge_artifact_start, detect_lanes
from neband.purity.analysis import _default_crop_lane


class AnalyzeError(Exception):
    """A request-level error with a caller-facing message (maps to HTTP 400)."""


@dataclass(frozen=True)
class LaneDetectResult:
    width: int
    height: int
    lanes: list[dict]


def run_lane_detect(signal: np.ndarray) -> LaneDetectResult:
    """Auto-detect initial lane boxes for a freshly-uploaded image.

    Mirrors the prototype's `GET /` auto-detect step: the leftmost detected
    lane defaults to the ladder, matching `detect_lanes`'s left-to-right
    ordering convention.
    """
    lanes = detect_lanes(signal)
    return LaneDetectResult(
        width=int(signal.shape[1]),
        height=int(signal.shape[0]),
        lanes=[
            {
                "index": int(lane.index),
                "x_start": int(lane.x_start),
                "x_end": int(lane.x_end),
                "is_ladder": i == 0,
            }
            for i, lane in enumerate(lanes)
        ],
    )


def render_lane(signal: np.ndarray, lane: Lane, bottom_bound: int) -> tuple[int, list[Band], float]:
    """(top_bound, bands, total_area) for one lane -- the same crop+detect
    pipeline the CLI's `analyze_image` uses, reused directly via `_default_crop_lane`.
    """
    profile, top_bound, _ = _default_crop_lane(signal, lane, bottom_bound)
    corrected = correct_baseline(profile)
    bands = detect_bands(corrected)
    total_area = sum(b.area for b in bands)
    return int(top_bound), bands, total_area


def classify_miss(bands: list[Band], top_bound: int, target_row: float, tolerance: float) -> str:
    """Why `find_nearest_band` returned None, for display only -- mirrors its
    ranking logic without changing that already-tested function's contract.
    """
    if not bands:
        return "no bands detected in this lane"
    ranked = sorted(bands, key=lambda b: abs(absolute_row(top_bound, b) - target_row))
    nearest_distance = abs(absolute_row(top_bound, ranked[0]) - target_row)
    if nearest_distance > tolerance:
        return "no band within tolerance"
    second_distance = abs(absolute_row(top_bound, ranked[1]) - target_row) if len(ranked) > 1 else None
    if second_distance is not None and (second_distance - nearest_distance) < (tolerance / 2):
        return "ambiguous -- two candidates too close to call"
    return "unknown"  # shouldn't happen if find_nearest_band actually returned None


def run_analyze(
    signal: np.ndarray,
    lanes: list[dict],
    reference: dict,
    excluded_from_series: set[int],
    deleted_ranges_by_lane: dict[int, set[tuple[int, int]]],
    known_mws: list[float] | None,
) -> dict:
    """The reviewed-lane analysis, given one human reference click.

    `lanes`: [{client_id, x_start, x_end, is_ladder}, ...]
    `reference`: {client_id, row} -- the human's raw click, in image
    coordinates, on the reference lane.
    `deleted_ranges_by_lane`: {client_id: {(y_start, y_end), ...}} -- bands
    manually excluded post-analyze (the "Delete band" interaction).
    `known_mws`: resolved ladder MW list, or None to skip MW calibration
    entirely.

    Raises `AnalyzeError` (caller-facing message) if the reference click
    doesn't land near a real band -- the one case that isn't a normal
    per-lane result.
    """
    all_lanes_mask = np.zeros(signal.shape[1], dtype=bool)
    for lane in lanes:
        all_lanes_mask[lane["x_start"] : lane["x_end"]] = True
    bottom_bound = int(detect_bottom_edge_artifact_start(signal[:, all_lanes_mask]))

    rendered: dict[int, tuple[int, list[Band], float]] = {}
    for lane in lanes:
        lane_obj = Lane(index=lane["client_id"], x_start=lane["x_start"], x_end=lane["x_end"])
        rendered[lane["client_id"]] = render_lane(signal, lane_obj, bottom_bound)

    ladder_lanes = [lane for lane in lanes if lane["is_ladder"]]
    calibration = None
    ladder_top_bound = None
    if ladder_lanes and known_mws is not None:
        ladder_id = ladder_lanes[0]["client_id"]
        ladder_top_bound, _, _ = rendered[ladder_id]
        # calibrate_ladder takes the ladder lane's cropped profile directly --
        # recompute via _default_crop_lane once more here since render_lane
        # only kept top_bound/bands/total_area, not the raw profile itself.
        ladder_lane_obj = Lane(index=ladder_id, x_start=ladder_lanes[0]["x_start"], x_end=ladder_lanes[0]["x_end"])
        ladder_profile, _, _ = _default_crop_lane(signal, ladder_lane_obj, bottom_bound)
        try:
            calibration = calibrate_ladder(correct_baseline(ladder_profile), known_mws)
        except LadderCalibrationError:
            calibration = None

    reference_id = reference["client_id"]
    ref_top_bound, ref_bands, _ = rendered[reference_id]
    ref_tolerance = row_tolerance(bottom_bound - ref_top_bound)
    # require_unambiguous=False -- this is a direct human click, not a
    # row-position guess, so a nearby second candidate shouldn't reject it;
    # the caller's UI gives immediate visual feedback (band overlay +
    # "Delete band") to catch a bad snap. propagate_target_band's own
    # per-lane matching below keeps the default ambiguity check.
    reference_band = find_nearest_band(
        ref_bands, ref_top_bound, reference["row"], ref_tolerance, require_unambiguous=False
    )
    if reference_band is None:
        reason = classify_miss(ref_bands, ref_top_bound, reference["row"], ref_tolerance)
        raise AnalyzeError(f"No detected band near your click ({reason}). Try clicking more precisely.")

    reference_absolute_row = absolute_row(ref_top_bound, reference_band)

    series_ids = [
        lane["client_id"]
        for lane in lanes
        if lane["client_id"] != reference_id and not lane["is_ladder"] and lane["client_id"] not in excluded_from_series
    ]
    lanes_for_propagation = {cid: (top_bound, bands) for cid, (top_bound, bands, _) in rendered.items()}
    propagated = propagate_target_band(reference_absolute_row, lanes_for_propagation, series_ids, ref_tolerance)

    def mw_of(top_bound: int, band: Band | None) -> float | None:
        if calibration is None or band is None or ladder_top_bound is None:
            return None
        return round(calibration.mw_at(absolute_row(top_bound, band) - ladder_top_bound), 2)

    results = {}
    for cid in [reference_id, *series_ids]:
        top_bound, bands, _ = rendered[cid]
        band = reference_band if cid == reference_id else propagated.get(cid)
        deleted_ranges = deleted_ranges_by_lane.get(cid, set())

        # A manually-deleted match falls back to "unmatched," never to a
        # different candidate -- re-running find_nearest_band against a
        # shrunken band list could silently promote a lesser candidate,
        # which the UI design deliberately avoids (see AGENTS.md).
        manually_deleted_target = band is not None and (top_bound + band.start, top_bound + band.end) in deleted_ranges
        if manually_deleted_target:
            band = None

        visible_bands = exclude_deleted_bands(bands, top_bound, deleted_ranges)
        total_area = sum(b.area for b in visible_bands)
        all_bands = [
            {
                "y_start": top_bound + b.start,
                "y_end": top_bound + b.end,
                "is_target": b is band,
            }
            for b in visible_bands
        ]
        if band is not None:
            purity = round(100 * band.area / total_area) if total_area > 0 else None
            results[cid] = {
                "purity_percent": purity,
                "matched_mw": mw_of(top_bound, band),
                "band_y_start": top_bound + band.start,
                "band_y_end": top_bound + band.end,
                "miss_reason": None,
                "all_bands": all_bands,
            }
        else:
            miss_reason = (
                "target band manually deleted"
                if manually_deleted_target
                else classify_miss(bands, top_bound, reference_absolute_row, ref_tolerance)
            )
            results[cid] = {
                "purity_percent": None,
                "matched_mw": None,
                "band_y_start": None,
                "band_y_end": None,
                "miss_reason": miss_reason,
                "all_bands": all_bands,
            }

    return results
