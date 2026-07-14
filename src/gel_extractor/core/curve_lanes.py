"""Prototype: curve-tracing lane detection (2026-07-14).

## Why this exists

`core.lanes.detect_lanes` treats every lane as a fixed-width vertical
rectangle spanning the image's full height, found by summing pixel darkness
down each column *over the whole image* and thresholding. Real gels violate
that model in two confirmed ways (see AGENTS.md Known Limitations / this
module's originating task):

1. "Gel smiling" -- edge lanes migrate faster/slower than center lanes, so a
   lane's true x-position drifts as you go down the image. A fixed vertical
   rectangle is wrong by construction on any gel with real curvature.
2. Bleed-over -- heavily-loaded wells smear sideways into neighboring lanes
   near the top, so two "lanes" aren't always separable rectangles there.

Lane over/under-segmentation from this rectangle mismatch was found to be
the dominant source of wrong purity numbers on real images this session
(one image topped out at 18% purity against a confirmed 91% ground truth).

## What this module is (and isn't)

This is a **minimal prototype**, not a production replacement for
`core.lanes`. It is not wired into `purity.analysis` or the CLI. It exists to
answer one question with a picture: does tracing curved lane paths across
horizontal strips visibly track real gel curvature better than a straight
rectangle? See `scripts/curve_trace_demo.py` for the rendered comparison and
the written verdict (repeated in this module's tests and in the PR/commit
history) on whether it does.

## Approach

1. Divide the image height into `num_strips` horizontal bands. Within each
   strip, run the *existing* `core.lanes.detect_lanes` column-projection
   technique on just that strip's rows -- same algorithm, smaller vertical
   slice (`detect_lanes_per_strip`).
2. Track each strip-local lane candidate into the next strip by nearest
   centroid position, since a real lane's centroid should drift smoothly and
   slowly, not jump (`track_lanes_across_strips`).
3. Turn a track into a per-row x-position function via linear interpolation
   between consecutive strips' centroids (`TracedLane.x_at_row`), then sum
   pixel intensity along that curved path per row instead of a fixed column
   range (`extract_curved_profile`) -- producing a profile compatible with
   the existing `core.bands.detect_bands`/`correct_baseline` (unchanged,
   reused as-is).

## Explicit, deliberate limitation: no split/merge handling

Real gels (especially over-segmented ones, per the task that motivated this)
can show a different *number* of lane candidates from one strip to the next
-- a lane that fragments within a strip, or two candidates that merge as
they converge/diverge. This prototype does **not** attempt to reconcile
that. Matching is a simple greedy nearest-centroid assignment within a
maximum-jump distance:

- A track with no plausible candidate in the next strip is marked `broken`
  for that strip and its *last known centroid* is carried forward unchanged
  (so `x_at_row` stays defined and doesn't jump to zero) -- it can "pick
  back up" in a later strip if a candidate reappears near where the track
  left off, since matching always looks at the track's last real (or
  carried-forward) position, not a prediction.
- A strip candidate with no plausible track to attach to starts a *new*
  track from that strip onward. Tracks are never merged and never split.

This means: a lane that fragments into two candidates in one strip and
recombines in the next will, under this prototype, either look like one
track that briefly "loses" the other fragment (if the two fragments are
both close to the track's last centroid, greedy nearest-neighbor picks one
arbitrarily) or spawn a short-lived spurious extra track. Both are known,
accepted rough edges for a prototype whose job is to demonstrate the curve-
tracing concept, not to be a correct multi-object tracker. A real
implementation would need proper track birth/death/merge/split logic (e.g.
a Hungarian-assignment or Kalman-filter based multi-object tracker) before
this could replace `core.lanes` in production.

## Findings from real-image testing (2026-07-14) -- honest, mixed result

Tested via `scripts/curve_trace_demo.py` against 3 real images (rendered
comparisons saved to `curve_trace_output/`, committed alongside this
module):

- `8.6.25 Protein Purity.tif` (a relatively well-behaved gel): traced curves
  look *good* -- they visibly converge into each comb tooth at the top the
  way the real wells do, track smoothly down through a real mid-gel darkened
  region, and subjectively track true lane centerlines at least as well as
  the straight rectangles, arguably better near the top where lanes visibly
  aren't vertical.
- `251017_ProteinPurity_FusionProtein.tif` (a dilution series, 1x-128x):
  mostly good and smooth, but 2 of ~13 real lanes show a visible track
  jumping to an adjacent lane's candidate partway down (an "X" or "V" shape
  in the rendered image) -- this is *exactly* the documented no-split/merge
  limitation showing up on a real image, not a hypothetical.
- `R-236_PDEV1452_91pct_53.53519kDa.png` (the worst known over-segmentation
  case): still messy. Curved tracking produces roughly 2x as many tracks as
  real lanes (e.g. 27 traced tracks vs. 14 straight-rectangle lanes even
  after tuning down from this module's original defaults), with visible
  duplicate/crossing tracks per real lane. Per-strip independent detection
  turned out to be *noisier* than whole-image column projection (less
  height to average over), so greedy no-merge tracking amplifies rather
  than resolves this image's over-segmentation problem.

Net verdict: curve tracing is a real, visible improvement on a well-behaved
gel, but does **not** clearly solve the over-segmentation problem that
motivated this prototype in the first place, on the specific image where
that problem was worst. The likely reason (see findings above) is that
per-strip lane *detection from scratch* is fragile on a narrow vertical
slice; the strip-tracking idea (nearest-centroid across strips) itself
looked reasonable and worth keeping.

Recommended next step if this is picked up again: don't re-detect lane
candidates independently in each strip. Instead, detect lanes once on the
whole image (the existing, more-robust `core.lanes.detect_lanes`) to fix
the lane *count* and rough starting positions, then only trace each
already-identified lane's local curvature strip-by-strip within a narrow
search window around its whole-image position. That constrains the
per-strip search enough to avoid spawning spurious tracks from noise,
while still capturing real curvature -- likely a better combination than
either fully-independent per-strip detection (this prototype) or a single
rigid rectangle (the current production approach).

## Redesign: anchored tracing (2026-07-14, follow-up session)

Implemented the "recommended next step" above as `trace_lane_from_anchor`
(single lane) / `trace_lanes_from_detected` (whole image, the entry point
meant for real use). This deliberately does **not** replace the
per-strip-detect-then-track machinery above (`detect_lanes_per_strip`,
`track_lanes_across_strips`, `trace_lanes`) -- that code and its tests are
left as-is, documenting the rejected approach and why. The new functions
are additive.

The key structural change: lane *identity and count* now come from one call
to `core.lanes.detect_lanes` on the whole image -- the same call
`purity.analysis.analyze_image` already makes and already has its own
validated lane-fragmentation fixes (comb-fringe/bottom-edge adaptive
cropping, min-width/min-gap merging). Per strip, each already-identified
lane's centroid is recomputed as a weighted center of mass *only within a
narrow window* around that lane's whole-image `x_start`/`x_end` (`margin`
below), not by re-running lane detection from scratch on the strip. Since
no new lanes can appear and no lane can vanish (the window always exists,
even if its content is faint), there is no split/merge ambiguity left to
introduce -- only "where did this lane's center drift to."

Validated 2026-07-14 against the same real images used above:

- `R-236_PDEV1452_91pct_53.53519kDa.png` (the hard over-segmentation case):
  `detect_lanes` finds 14 lanes; anchored tracing produces exactly 14
  tracks (one per anchor, by construction) -- fixes the fragment-count
  problem the per-strip-redetection prototype made worse (27 vs. ~14).
  This is close to a tautology (the anchor step *is* `detect_lanes`), but
  it's also exactly the point: count/identity ambiguity is eliminated by
  design rather than resolved after the fact.
- `8.6.25 Protein Purity.tif` (the well-behaved gel): the earlier real win
  -- traced curves visibly converging into comb teeth -- is preserved,
  since the windowed per-strip centroid still moves with real curvature;
  it just can't drift far enough to jump to a neighboring lane's anchor
  (bounded by `margin`).

`margin` (the anchor window's one-sided padding beyond the lane's own
detected width) defaults to 25% of the lane's width (`DEFAULT_ANCHOR_MARGIN_FRACTION`)
-- narrow enough to reject a neighboring lane's signal from pulling the
centroid across, wide enough to still capture real smiling drift within a
lane's own strip-to-strip movement. Not rigorously tuned (one image, one
value tried in the time available); see
`scripts/curve_trace_anchor_compare.py` (gitignored, left on disk in this
worktree) for the real-image comparison run.

### Stretch goal: does the curved boundary change the purity number?

Also compared (`scripts/curve_trace_purity_compare.py`, gitignored):
per-lane purity % using the anchored curved trace as the lane boundary,
against the existing straight-rectangle `purity.analysis.analyze_image`
result, on the same real images -- reusing `analyze_image`/`analyze_lane`
unchanged, swapping only the sample lane's intensity profile
(`extract_curved_profile` instead of a fixed column-range sum). Honest
result: on both `R-236_PDEV1452...png` and `8.6.25 Protein Purity.tif`,
curved-trace purity % matched straight-rectangle purity % almost exactly
(identical on most lanes, off by 1 percentage point on a few) -- the
anchored window is narrow enough, and MW calibration/matching happens far
enough down the lane (below the top-of-gel curvature this redesign
actually captures), that the boundary shape barely moves the final number
on these two images. In particular this does **not** touch R-236's
separately-documented, unresolved MW-migration discrepancy (see AGENTS.md
Known Limitations) -- that gap between measured and the 91% confirmed
ground truth is a calibration issue, not a lane-shape issue, and this
redesign was never expected to fix it.
"""

from dataclasses import dataclass, field

import numpy as np

from gel_extractor.core.lanes import Lane, detect_lanes

# Number of horizontal strips to slice the image into for per-strip lane
# detection. Originally guessed at 16 (more strips = finer curvature
# resolution); empirical testing against real images (see "Findings" in
# this module's docstring, 2026-07-14) found *fewer*, wider strips actually
# produced less spurious track fragmentation -- each strip's column
# projection is noisier than the whole-image version it's derived from
# (less height to average over), so more/narrower strips means more noisy,
# independent redetections and more chances to spawn a spurious extra
# track. Still not a rigorously tuned value, just the better of a small
# number tried (8, 16, 24 strips).
DEFAULT_NUM_STRIPS = 8

# Maximum allowed centroid jump between adjacent strips, as a fraction of
# image width, before a strip-to-strip match is rejected as implausible.
# Real gel smiling drifts a lane by a small fraction of the image width over
# its *entire* height; per-strip, the drift should be much smaller than
# that. Originally set loose (0.06) on the theory that accepting an
# occasional bad match beats fragmenting tracks -- empirical testing found
# the opposite: a tighter value (0.02) rejected more implausible matches
# and modestly reduced spurious track counts on real images without
# visibly breaking legitimate curvature tracking (see docstring
# "Findings").
DEFAULT_MAX_JUMP_FRACTION = 0.02

# One-sided padding for the anchored-tracing window, as a fraction of the
# already-detected lane's own width -- see module docstring, "Redesign:
# anchored tracing". Each strip's centroid search is confined to
# `[lane.x_start - margin, lane.x_end + margin]`; too wide risks pulling in
# a neighboring lane's signal (defeating the point of anchoring), too
# narrow risks clipping real smiling drift at the image's top/bottom where
# curvature is largest. 25% is a single-image-tuned starting point (see
# docstring), not rigorously swept.
DEFAULT_ANCHOR_MARGIN_FRACTION = 0.25


@dataclass(frozen=True)
class StripLane:
    """One lane candidate detected within a single horizontal strip.

    `row_center` is this strip's vertical midpoint, in full-image row
    coordinates -- the anchor point used for interpolating a traced lane's
    curve between strips. `broken` is True when this entry is a carried-
    forward placeholder (no real detection matched in this strip), not an
    actual per-strip detection -- see module docstring.
    """

    strip_index: int
    row_center: float
    x_start: int
    x_end: int
    centroid: float
    broken: bool = False


@dataclass
class TracedLane:
    """A lane traced across strips via nearest-centroid tracking.

    `strips` is ordered by `strip_index` and holds one `StripLane` per strip
    from this track's first appearance onward (including carried-forward
    `broken` entries -- see module docstring). Curve position at an
    arbitrary row is linear interpolation between consecutive strips'
    `(row_center, centroid)` points, flat-extrapolated before the first /
    after the last strip this track covers.
    """

    track_id: int
    strips: list[StripLane] = field(default_factory=list)

    def x_at_row(self, row: float) -> float:
        """Interpolate this lane's traced x-centroid at an arbitrary row."""
        return _interp_at_row(row, self.strips, attr="centroid")

    def width_at_row(self, row: float) -> float:
        """Interpolate this lane's traced width (x_end - x_start) at a row."""
        widths_source = [(s.row_center, s.x_end - s.x_start) for s in self.strips]
        return _interp(row, widths_source)

    @property
    def row_span(self) -> tuple[float, float]:
        """(first, last) row_center this track covers -- for debug rendering."""
        return self.strips[0].row_center, self.strips[-1].row_center


def _interp_at_row(row: float, strips: list[StripLane], attr: str) -> float:
    points = [(s.row_center, getattr(s, attr)) for s in strips]
    return _interp(row, points)


def _interp(row: float, points: list[tuple[float, float]]) -> float:
    if not points:
        raise ValueError("cannot interpolate an empty track")
    if len(points) == 1:
        return points[0][1]
    rows = [p[0] for p in points]
    values = [p[1] for p in points]
    if row <= rows[0]:
        return values[0]
    if row >= rows[-1]:
        return values[-1]
    return float(np.interp(row, rows, values))


def _strip_boundaries(height: int, num_strips: int) -> list[tuple[int, int, float]]:
    """Return (row_start, row_end, row_center) for each of `num_strips` bands."""
    edges = np.linspace(0, height, num_strips + 1).astype(int)
    return [(int(edges[i]), int(edges[i + 1]), (edges[i] + edges[i + 1]) / 2.0) for i in range(num_strips)]


def detect_lanes_per_strip(
    signal: np.ndarray,
    num_strips: int = DEFAULT_NUM_STRIPS,
    **detect_lanes_kwargs,
) -> list[list[StripLane]]:
    """Run `core.lanes.detect_lanes`'s column-projection technique per horizontal strip.

    Returns one list of `StripLane` candidates per strip (in top-to-bottom
    strip order; a strip with no detected lanes gets an empty list).
    `detect_lanes_kwargs` are passed through to `detect_lanes` unchanged, so
    the same tunable thresholds apply per-strip as apply whole-image.
    """
    height = signal.shape[0]
    boundaries = _strip_boundaries(height, num_strips)
    strips: list[list[StripLane]] = []
    for i, (r0, r1, row_center) in enumerate(boundaries):
        if r1 <= r0:
            strips.append([])
            continue
        strip_signal = signal[r0:r1]
        lanes = detect_lanes(strip_signal, **detect_lanes_kwargs)
        strip_lanes = []
        for lane in lanes:
            column_profile = strip_signal[:, lane.x_start : lane.x_end].sum(axis=0)
            if column_profile.sum() > 0:
                xs = np.arange(lane.x_start, lane.x_end)
                centroid = float(np.average(xs, weights=column_profile))
            else:
                centroid = (lane.x_start + lane.x_end) / 2.0
            strip_lanes.append(
                StripLane(
                    strip_index=i,
                    row_center=row_center,
                    x_start=lane.x_start,
                    x_end=lane.x_end,
                    centroid=centroid,
                )
            )
        strips.append(strip_lanes)
    return strips


def track_lanes_across_strips(
    strips: list[list[StripLane]],
    image_width: int,
    max_jump_fraction: float = DEFAULT_MAX_JUMP_FRACTION,
    row_centers: list[float] | None = None,
) -> list[TracedLane]:
    """Trace lanes across strips via greedy nearest-centroid matching.

    Deliberately does not handle splits or merges -- see module docstring.
    A track with no plausible match in a strip carries its last centroid
    forward (marked `broken` for that strip) rather than terminating, so it
    can pick back up if a candidate reappears nearby in a later strip. A
    strip candidate with no plausible track starts a new track.

    `row_centers` (one per strip, in the same order as `strips`) is needed
    to place a carried-forward placeholder at the right row when a strip
    has no detections at all to borrow a `row_center` from; if omitted, the
    strip's index is used as a fallback row coordinate (fine for tests, but
    callers with real images should pass the real values -- see
    `trace_lanes`, which does this automatically).
    """
    max_jump = image_width * max_jump_fraction
    if row_centers is None:
        row_centers = [float(i) for i in range(len(strips))]

    tracks: list[TracedLane] = []
    next_id = 0

    first_populated = next((i for i, s in enumerate(strips) if s), None)
    if first_populated is None:
        return []

    for strip_lane in strips[first_populated]:
        tracks.append(TracedLane(track_id=next_id, strips=[strip_lane]))
        next_id += 1

    for i in range(first_populated + 1, len(strips)):
        candidates = strips[i]
        row_center = row_centers[i]

        if not candidates:
            # Whole strip came up empty (e.g. a faint/blank band of the
            # image) -- carry every active track's last centroid forward.
            for track in tracks:
                track.strips.append(_carry_forward(track.strips[-1], row_center=row_center))
            continue

        pairs = []
        for t_idx, track in enumerate(tracks):
            last_x = track.strips[-1].centroid
            for c_idx, cand in enumerate(candidates):
                dist = abs(cand.centroid - last_x)
                if dist <= max_jump:
                    pairs.append((dist, t_idx, c_idx))
        pairs.sort(key=lambda p: p[0])

        matched_tracks: set[int] = set()
        matched_candidates: set[int] = set()
        assignment: dict[int, int] = {}
        for _dist, t_idx, c_idx in pairs:
            if t_idx in matched_tracks or c_idx in matched_candidates:
                continue
            matched_tracks.add(t_idx)
            matched_candidates.add(c_idx)
            assignment[t_idx] = c_idx

        for t_idx, track in enumerate(tracks):
            if t_idx in assignment:
                track.strips.append(candidates[assignment[t_idx]])
            else:
                track.strips.append(_carry_forward(track.strips[-1], row_center=row_center))

        for c_idx, cand in enumerate(candidates):
            if c_idx not in matched_candidates:
                tracks.append(TracedLane(track_id=next_id, strips=[cand]))
                next_id += 1

    return tracks


def _carry_forward(last: StripLane, row_center: float) -> StripLane:
    """A placeholder continuation of `last` at a new strip, unchanged position."""
    return StripLane(
        strip_index=last.strip_index,
        row_center=row_center,
        x_start=last.x_start,
        x_end=last.x_end,
        centroid=last.centroid,
        broken=True,
    )


def extract_curved_profile(signal: np.ndarray, lane: TracedLane, height: int | None = None) -> np.ndarray:
    """Sum signal along a traced lane's curved path, one value per row.

    The curved-path analogue of `Lane`'s fixed `x_start:x_end` rectangle sum
    (`core.lanes` / `purity.analysis`'s `cropped.sum(axis=1)`): at each row,
    look up this lane's interpolated x-center and width (`TracedLane.x_at_row`
    / `width_at_row`) and sum the signal across that row's column window
    instead of a column range fixed for the whole image height. The result
    is a 1D profile directly compatible with `core.bands.correct_baseline`/
    `detect_bands`, unchanged.
    """
    if height is None:
        height = signal.shape[0]
    profile = np.zeros(height)
    for row in range(height):
        x_center = lane.x_at_row(row)
        half_width = lane.width_at_row(row) / 2.0
        x0 = max(0, int(round(x_center - half_width)))
        x1 = min(signal.shape[1], int(round(x_center + half_width)))
        if x1 > x0:
            profile[row] = float(signal[row, x0:x1].sum())
    return profile


def trace_lanes(
    signal: np.ndarray,
    num_strips: int = DEFAULT_NUM_STRIPS,
    max_jump_fraction: float = DEFAULT_MAX_JUMP_FRACTION,
    **detect_lanes_kwargs,
) -> list[TracedLane]:
    """Convenience entry point: strip-detect then track, in one call.

    Kept for the earlier per-strip-redetection prototype (see module
    docstring's "Findings" section for why it's not recommended for
    production use) -- `trace_lanes_from_detected` below is the intended
    real entry point going forward.
    """
    strips = detect_lanes_per_strip(signal, num_strips=num_strips, **detect_lanes_kwargs)
    row_centers = [row_center for _, _, row_center in _strip_boundaries(signal.shape[0], num_strips)]
    return track_lanes_across_strips(
        strips,
        image_width=signal.shape[1],
        max_jump_fraction=max_jump_fraction,
        row_centers=row_centers,
    )


def trace_lane_from_anchor(
    signal: np.ndarray,
    lane: Lane,
    num_strips: int = DEFAULT_NUM_STRIPS,
    margin_fraction: float = DEFAULT_ANCHOR_MARGIN_FRACTION,
) -> TracedLane:
    """Trace one already-identified lane's local curvature across strips.

    Unlike `trace_lanes`/`detect_lanes_per_strip`, this never re-runs lane
    *detection* -- `lane` (from `core.lanes.detect_lanes`, called once on
    the whole image) fixes this lane's identity, x-range, and width. Per
    strip, only the centroid (weighted center of mass of column intensity)
    is recomputed, and only within a narrow window
    `[lane.x_start - margin, lane.x_end + margin]` around the lane's own
    known position -- `margin = lane_width * margin_fraction`. See module
    docstring, "Redesign: anchored tracing", for why this avoids the
    over-segmentation the earlier per-strip-redetection prototype caused.

    A strip whose window has no signal at all (e.g. a genuinely blank
    strip) carries the last real centroid forward (`StripLane.broken =
    True`), mirroring `track_lanes_across_strips`'s carry-forward behavior,
    so `x_at_row` never jumps to a degenerate value.
    """
    height, width = signal.shape
    lane_width = lane.x_end - lane.x_start
    margin = max(1, int(round(lane_width * margin_fraction)))
    window_x0 = max(0, lane.x_start - margin)
    window_x1 = min(width, lane.x_end + margin)

    boundaries = _strip_boundaries(height, num_strips)
    last_centroid = (lane.x_start + lane.x_end) / 2.0
    strips: list[StripLane] = []
    for i, (r0, r1, row_center) in enumerate(boundaries):
        if r1 <= r0 or window_x1 <= window_x0:
            strips.append(
                StripLane(
                    strip_index=i,
                    row_center=row_center,
                    x_start=lane.x_start,
                    x_end=lane.x_end,
                    centroid=last_centroid,
                    broken=True,
                )
            )
            continue

        window = signal[r0:r1, window_x0:window_x1]
        column_profile = window.sum(axis=0)
        total = column_profile.sum()
        if total > 0:
            xs = np.arange(window_x0, window_x1)
            centroid = float(np.average(xs, weights=column_profile))
            last_centroid = centroid
            broken = False
        else:
            centroid = last_centroid
            broken = True

        strips.append(
            StripLane(
                strip_index=i,
                row_center=row_center,
                x_start=lane.x_start,
                x_end=lane.x_end,
                centroid=centroid,
                broken=broken,
            )
        )

    return TracedLane(track_id=lane.index, strips=strips)


def trace_lanes_from_detected(
    signal: np.ndarray,
    num_strips: int = DEFAULT_NUM_STRIPS,
    margin_fraction: float = DEFAULT_ANCHOR_MARGIN_FRACTION,
    **detect_lanes_kwargs,
) -> tuple[list[Lane], list[TracedLane]]:
    """The intended real entry point: anchor lane count/identity, then trace curvature.

    Calls `core.lanes.detect_lanes` once on the whole image (same call
    `purity.analysis.analyze_image` already makes) to fix the definitive
    lane count and x-ranges, then traces each lane's local curvature via
    `trace_lane_from_anchor`. Returns `(lanes, tracks)` -- `lanes` in the
    same order/count as `core.lanes.detect_lanes` would return alone (so a
    caller can still tell which lane is the ladder, etc.), `tracks` the
    parallel list of `TracedLane`s.
    """
    lanes = detect_lanes(signal, **detect_lanes_kwargs)
    tracks = [
        trace_lane_from_anchor(signal, lane, num_strips=num_strips, margin_fraction=margin_fraction)
        for lane in lanes
    ]
    return lanes, tracks
