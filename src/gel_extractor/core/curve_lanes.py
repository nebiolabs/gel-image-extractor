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
"""

from dataclasses import dataclass, field

import numpy as np

from gel_extractor.core.lanes import detect_lanes

# Number of horizontal strips to slice the image into for per-strip lane
# detection. A prototype-stage guess, not empirically tuned: enough strips
# to resolve real curvature (gel smiling is a gradual effect over the whole
# height) without each strip being so short that column-projection noise
# dominates over real signal. See this module's docstring for context.
DEFAULT_NUM_STRIPS = 16

# Maximum allowed centroid jump between adjacent strips, as a fraction of
# image width, before a strip-to-strip match is rejected as implausible.
# Real gel smiling drifts a lane by a small fraction of the image width over
# its *entire* height; per-strip, the drift should be much smaller than
# that. Set generously loose for a prototype -- better to occasionally
# accept a bad match than to fragment tracks unnecessarily, since split/merge
# handling is explicitly out of scope (see docstring).
DEFAULT_MAX_JUMP_FRACTION = 0.06


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
    """Convenience entry point: strip-detect then track, in one call."""
    strips = detect_lanes_per_strip(signal, num_strips=num_strips, **detect_lanes_kwargs)
    row_centers = [row_center for _, _, row_center in _strip_boundaries(signal.shape[0], num_strips)]
    return track_lanes_across_strips(
        strips,
        image_width=signal.shape[1],
        max_jump_fraction=max_jump_fraction,
        row_centers=row_centers,
    )
