"""Cross-lane band matching by absolute row position.

Prototyped 2026-07-21 for the human-in-the-loop band-selection effort (see
AGENTS.md and `data/purity_solution_space_reassessment.md`): a human marks
the target band in one reference lane of a dilution series; this module
finds the corresponding band in every other lane of that same series by
row position, on the premise that the same protein migrates to about the
same row across lanes of the same gel. Reuses the exact absolute-row
convention `purity/analysis.py` (`position_offset`) and `purity/debug_viz.py`
(`top_bound + band.start`) already use -- a lane's own adaptive `top_bound`
(see `core.lanes.detect_comb_fringe_end`) plus a `Band.center`, which is
relative to that lane's own post-crop profile, gives one shared absolute
image-row frame across every lane, straight-rectangle geometry only.

Standalone experimental module, not wired into `purity/methods.py`'s
`METHOD_REGISTRY` -- consistent with how every other alternative
band-selection/geometry idea in this project was prototyped in isolation
before earning CLI integration.
"""

from neband.core.bands import Band

# Never a fixed pixel constant -- this project's real images range from
# phone photos to scanner TIFFs, so a tolerance tuned to one image's
# resolution silently misbehaves on another (matches how every other
# tunable threshold in this codebase is a fraction/percentile of the
# image's own statistics, e.g. core.lanes's DEFAULT_FRAGMENT_NARROW_FRACTION,
# never an absolute). Placeholder fraction -- expected to be tuned once
# real evaluation data exists.
DEFAULT_ROW_TOLERANCE_FRACTION = 0.05


def row_tolerance(resolving_height: float, fraction: float = DEFAULT_ROW_TOLERANCE_FRACTION) -> float:
    """Derive a row-matching tolerance from a lane's own resolving-gel
    height (`bottom_bound - top_bound`) -- see the module-level comment on
    `DEFAULT_ROW_TOLERANCE_FRACTION` for why this isn't a fixed constant.
    """
    return resolving_height * fraction


def absolute_row(top_bound: int, band: Band) -> float:
    """A band's position in a shared, whole-image row frame.

    `Band.center` is relative to that lane's own post-crop profile (see
    `core.bands.detect_bands`) -- adding the lane's own `top_bound`
    (already an absolute image row, since lanes are only ever cropped in
    columns before `top_bound` is computed) re-expresses it in a frame
    every lane shares, the same convention `purity/analysis.py`'s
    `position_offset` and `purity/debug_viz.py`'s rendering already use.
    Don't reimplement this conversion a third time elsewhere.
    """
    return top_bound + band.center


def find_nearest_band(
    bands: list[Band],
    top_bound: int,
    target_absolute_row: float,
    tolerance: float,
    require_unambiguous: bool = True,
) -> Band | None:
    """Find whichever of `bands` (all from one lane, with that lane's own
    `top_bound`) best matches `target_absolute_row`, or `None` on an honest
    miss. Serves two roles: snapping a human's raw click to the nearest
    actually-detected band in that same lane, and matching a reference row
    against a *different* lane's bands during propagation -- both are
    "which of this lane's bands is closest to a target row," just with a
    different source for the target row, and different stakes.

    Two ways to come back `None` rather than a forced guess, since a
    confidently-wrong pick is worse than admitting uncertainty:
    - nothing falls within `tolerance` at all, or
    - (only when `require_unambiguous`) the two nearest candidates are
      within half of `tolerance` of each other (ambiguous -- e.g. a real
      contaminant band sitting almost as close to the target row as the
      true target band itself).

    `require_unambiguous=False` is for a direct human click only (see
    `scripts/hitl_ui_server.py`'s reference-click match): the click already
    *is* the human's judgment, not a guess to be second-guessed, and the
    HITL UI shows the snapped result immediately (band overlay, plus the
    "Delete band" correction) for the human to catch and fix a bad snap --
    protections `propagate_target_band`'s row-guess-based matching across
    *other* lanes doesn't have, which is why its calls keep the default
    `True` and this parameter never reaches it.
    """
    if not bands:
        return None

    ranked = sorted(bands, key=lambda band: abs(absolute_row(top_bound, band) - target_absolute_row))
    nearest = ranked[0]
    nearest_distance = abs(absolute_row(top_bound, nearest) - target_absolute_row)
    if nearest_distance > tolerance:
        return None

    if require_unambiguous and len(ranked) > 1:
        second_distance = abs(absolute_row(top_bound, ranked[1]) - target_absolute_row)
        if (second_distance - nearest_distance) < (tolerance / 2):
            return None

    return nearest


def exclude_deleted_bands(
    bands: list[Band],
    top_bound: int,
    deleted_ranges: set[tuple[int, int]],
) -> list[Band]:
    """`bands` minus any whose absolute (y_start, y_end) range is in
    `deleted_ranges` -- the human-in-the-loop review UI's per-band "delete"
    action (see `scripts/hitl_ui_server.py`). Identity is by absolute pixel
    range rather than a separate id: those are the exact values already sent
    to and echoed back from the client, and band detection is deterministic
    for an unchanged lane crop, so no id scheme is needed.
    """
    return [
        band
        for band in bands
        if (top_bound + band.start, top_bound + band.end) not in deleted_ranges
    ]


def propagate_target_band(
    reference_absolute_row: float,
    lanes: dict[int, tuple[int, list[Band]]],
    series_lanes: list[int],
    tolerance: float,
) -> dict[int, Band | None]:
    """Match `reference_absolute_row` against every lane in `series_lanes`.

    `lanes` maps a lane index to that lane's own `(top_bound, bands)` --
    may contain lanes outside `series_lanes` (e.g. the reference lane
    itself, or a non-dilution-series lane like an embedded purity-standard
    ladder, see AGENTS.md Data Inventory), which are simply ignored.
    `series_lanes` is deliberately explicit rather than "every other
    lane in the image" -- real gels in this project's own data mix a
    dilution series with lanes that aren't part of it at all.

    Returns a dict covering every lane in `series_lanes`; a lane with
    nothing within tolerance at all maps to `None` rather than a forced
    guess -- a genuinely absent target. Ambiguity between two close
    candidates does NOT hold a lane back (`require_unambiguous=False`,
    same as the reference click -- see `find_nearest_band`'s docstring):
    every lane's result is shown via the HITL UI's band overlay and
    correctable with its "Delete band" action, the same safety net that
    justified relaxing the reference click, so there's no longer a real
    distinction between "a human clicked this" and "this was propagated"
    for ambiguity-tolerance purposes.
    """
    result: dict[int, Band | None] = {}
    for lane_index in series_lanes:
        top_bound, bands = lanes[lane_index]
        result[lane_index] = find_nearest_band(
            bands, top_bound, reference_absolute_row, tolerance, require_unambiguous=False
        )
    return result
