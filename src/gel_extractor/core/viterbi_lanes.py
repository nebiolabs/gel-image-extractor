"""Prototype: globally-optimal (Viterbi/DP) curved-lane tracing (2026-07-16).

## Why this exists

`core.lanes.detect_lanes` treats every lane as a fixed-width vertical
rectangle. The `curve-tracing-lane-detection` branch (`core/curve_lanes.py`,
not touched by this module) already explored one alternative -- a *greedy*
per-strip/per-row centroid walk, anchored to `detect_lanes`'s whole-image
lane count/positions to avoid re-detecting lane candidates from scratch (see
that module's docstring, "Redesign: anchored tracing"). That greedy walk
picks the best position at each strip using only the *previous* strip's
choice, then (if a strip's own window looks blank) just freezes in place.

This module tries a different algorithm on the same anchoring idea: instead
of a greedy row-by-row (or strip-by-strip) walk, solve for the single
per-lane path that is *globally* optimal across the whole lane's height, via
dynamic programming (a Viterbi-style shortest-path decode). Each row's DP
cost trades off (a) a local reward for high column-window intensity at a
candidate x-position, against (b) a penalty proportional to horizontal
displacement from the *chosen* position in the row above. Unlike a greedy
walk, a low-signal/noisy row's chosen position is influenced by rows on
*both* sides (through the DP's backward pass), not just whatever the walk
had already committed to going in -- so an ambiguous row is pulled toward
wherever the path needs to be to stay cheap overall, rather than frozen at
its predecessor's value or picking up noise on its own.

## What this module is (and isn't)

A minimal, standalone prototype -- not wired into `purity.analysis` or the
CLI. Reuses `core.lanes.detect_lanes` for lane count/seed positions
unchanged (per this prototyping task's brief: only the per-lane profile-
generation step is being tested here), and is meant to be compared against
both the straight-rectangle baseline and the existing greedy curve-tracing
prototype via `scripts/compare_viterbi_lanes.py`.

## Bounding the search window (a previously-found, real bug)

A prior windowed/search-based prototype (`core.curve_lanes`) found a real
bug via a synthetic test: an unbounded (or too-wide) per-row/per-strip
search window can drift into a *neighboring* lane's own signal when two
lanes sit close together, producing a false, confidently-wrong centroid
instead of a correct "no real content here." This module reuses that
project's fix: each lane's DP search space is hard-bounded to (at most) the
midpoint between it and its nearest neighbor on each side
(`trace_lanes_from_detected` computes these bounds the same way
`core.curve_lanes.trace_lanes_from_detected` does), never to the neighbor's
own column range.

## Reward function

Per row, the reward at candidate x is a sliding column-window sum of that
row's own signal (window width = the lane's own already-detected width from
`detect_lanes`, so the reward is directly comparable to the whole-image
column-sum `detect_lanes` itself uses, just computed one row at a time
instead of summed over the full height). No baseline correction is applied
to this local, single-row, narrow-window reward: baseline correction (see
`core.bands.correct_baseline`) matters for `detect_lanes`'s whole-image
*absolute* column-sum profile, which gets thresholded against zero to find
lane boundaries in the first place -- a slowly-varying background level
there can otherwise look like one giant lane spanning the whole image (see
AGENTS.md). Here, the search space is already bounded to one known lane's
neighborhood and only *relative* differences between nearby candidate
x-positions in the same row matter (which one wins the DP step), so a
roughly-constant background offset across that narrow window cancels out
and does not need explicit correction.

A light vertical (row-axis) Gaussian smoothing pass is applied to the whole
bounded window before extracting rewards, to reduce single-row pixel noise
without discarding row resolution (contrast with the greedy prototype's
"strip" idea, which traded away row resolution for the same
noise-reduction effect -- see that module's docstring on why *fewer, wider*
strips helped there).

## DP formulation

For a lane with a bounded candidate-column range of width `W` and image
height `H`:

    DP[0, x]  = -reward(0, x)
    DP[r, x]  = -reward(r, x) + min_{x'} ( DP[r-1, x'] + lambda * |x - x'| )

solved by backtracking from `argmin_x DP[H-1, x]`. Implemented directly via
vectorized numpy broadcasting over the `W x W` displacement-penalty matrix
per row (`W` is bounded by the inter-lane spacing, at most a few hundred
pixels on the real images checked -- see module-level validation notes
below -- so this is fast enough for a prototype without the more intricate
O(W) "slope trick" sweep an L1 penalty admits).

## Real-image validation (2026-07-16) -- honest, mixed result

Run via `scripts/compare_viterbi_lanes.py` against all 6 `pptx_tet3_gels`
images (the richest ground truth available -- confirmed purity % + MW per
image) plus a visual check on 2 of them (rendered debug overlays, not
committed -- gitignored `data/`). `detect_lanes`'s lane count/seed positions
are shared, unchanged, between the rectangle baseline and this prototype in
every comparison, so any difference is attributable to the profile-
generation geometry, not incidental reimplementation differences.

- **A real bug found and fixed during this validation, not just a tuning
  miss**: an early version only bounded the DP *search* window to the
  neighbor midpoint (see "Bounding the search window" above) but extracted
  the final profile using a plain `lane_width`-wide window centered on the
  traced center -- which can still reach past that same bound into a
  neighboring lane's own columns when the traced center sits near the
  bound's edge. Confirmed as a real, not theoretical, regression: on
  `R-236_PID1502_PDEV1580_98.5pct...png` (a well-behaved image where the
  rectangle baseline already gets close to the confirmed 98.5% on several
  lanes), this bug alone dropped a 91% rectangle match to 1% under the
  curved profile. Fixed by carrying the same neighbor-clamped bound
  (`TracedPath.bound_x0`/`bound_x1`) through to `extract_curved_profile` and
  clipping there too -- see that dataclass's docstring.
- **After the fix, `R-236_PID1502_PDEV1580` (good case)**: purity on the 4
  lanes the rectangle baseline already matched stayed close (91/98/98 ->
  90/97/94, off by 1-4 points, plus a new 18 on a lane rectangle called 27)
  -- consistent with the existing curve-tracing prototype's own "stretch
  goal" finding that curved geometry barely moves the number once the
  bleed-into-neighbor bug is out of the picture. More notably, **4 lanes the
  rectangle baseline reported `not-found` got real `mw-matched` results
  under the curved profile** (61%, 76%, 98%, 100%) -- a visual debug overlay
  (rendered, not committed) confirms the traced red path visibly follows
  the real, smiling curvature of this gel's bands through those lanes,
  where a fixed rectangle's column range apparently missed enough of the
  true band to fail MW-matching. This is the single clearest positive
  signal for the DP approach found in this session, though it isn't
  independently confirmed per-lane (only the whole-image purity % is
  ground-truthed) so it should be read as suggestive, not proven.
- **`R-236_PDEV1452` (the project's standing hardest over-segmentation
  case, confirmed 91%)**: does **not** fix the motivating problem, and was
  not expected to (see module docstring's task framing -- this only swaps
  the per-lane profile, `detect_lanes`'s lane *count* is untouched and
  reused as-is). `detect_lanes` still finds 13 lanes on an image with ~9
  real ones under either geometry; both rectangle and DP-curved purity stay
  in the single-to-20s% range, nowhere near the confirmed 91% -- consistent
  with AGENTS.md's existing conclusion that this image's dominant problem
  is lane *identity*, not lane *shape*. A rendered debug overlay visually
  confirms two things at once: the DP path does trace real curvature
  reasonably well inside several already-wrong rectangles, **and** several
  rectangle pairs visibly split one real lane into two -- the over-
  segmentation is plainly visible independent of the curve-vs-rectangle
  question.
- **Other 4 `pptx_tet3_gels` images**: mixed, not a clean win. Several
  lanes track the rectangle result closely; a few swing by a large,
  hard-to-explain margin in either direction (e.g. one lane on
  `R-244_PDEV1526` went from a rectangle 3% to a curved 78%) with no
  per-lane ground truth available to say which is more correct -- flagged
  here as a real open question about this prototype's stability, not
  swept under the rug. `R-236_PDEV1495` stayed low (~2%) under both
  geometries, consistent with AGENTS.md's separately-tracked, unresolved
  R-236 MW-migration discrepancy (a calibration issue, not a lane-shape
  one).
- **Performance**: fast enough for interactive use as prototyped --
  roughly 0.1-0.3s to trace every lane on a full ~1000x1400px real image
  (vectorized-numpy DP, see "DP formulation" above), no separate tuning
  needed to make this tractable.

**Net verdict**: the core DP/global-optimization idea works as intended
(validated on synthetic data -- tracks a known curved path almost exactly,
including smoothly interpolating through a deliberately blank gap in the
signal, something a greedy carry-forward walk can't do as principled-ly) and
visibly follows real gel curvature on real images once the neighbor-bleed
extraction bug above was fixed. It is not a demonstrated fix for the
project's actual motivating hard case (lane over-segmentation on
`PDEV1452`) for the same reason the earlier greedy prototype wasn't: this
approach, like that one, only reshapes an already-fixed lane's *profile*
and deliberately does not touch `detect_lanes`'s lane *count*. Whether the
occasional large per-lane swings on the other 4 images are a real
improvement (recovering true signal a rigid rectangle clips) or a new
instability this prototype introduces is genuinely unresolved -- no
per-lane ground truth exists to distinguish the two, which is exactly the
kind of question this project's synthesis step should weigh against the
other prototyped approaches before deciding whether to invest further.
"""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d

from gel_extractor.core.lanes import Lane, detect_lanes

# Per-pixel cost of horizontal displacement, in the same units as the
# reward (a column-window intensity sum on a [0, 1]-normalized signal, see
# `core.image_io.to_signal`). Not rigorously tuned -- a single starting
# value tried against a handful of real images (see module docstring);
# expected to need further tuning, same as every other placeholder constant
# in this project. Higher = a straighter, more rectangle-like path; lower =
# more willing to chase row-to-row noise.
DEFAULT_LAMBDA_SMOOTH = 0.6

# Vertical (row-axis) smoothing applied before reward extraction, to reduce
# single-row pixel noise -- see module docstring for why this is preferred
# here over the greedy prototype's "collapse into a few wide strips"
# approach (which discards row resolution the DP doesn't need to give up).
DEFAULT_VERTICAL_SMOOTH_SIGMA = 4.0

# One-sided padding for the anchored search window, as a fraction of the
# lane's own already-detected width -- same role and same default starting
# point as `core.curve_lanes.DEFAULT_ANCHOR_MARGIN_FRACTION`. Kept as a
# separate constant (not imported from that module) since this is a
# standalone prototype and the two approaches' margins are not assumed to
# need the same value.
DEFAULT_MARGIN_FRACTION = 0.25

# Width of the per-row reward window, as a fraction of the lane's own
# already-detected width (`Lane.x_end - Lane.x_start`). Deliberately
# *narrower* than the full lane width: a synthetic-image check (see module
# validation notes) found that using the full anchor width as the reward
# window makes the reward almost insensitive to small shifts (a real band
# narrower than the full lane width stays entirely inside the window across
# a wide range of candidate centers, so nothing but the smoothness penalty
# has any gradient to follow -- the DP path collapses to a flat line at
# whatever position wins ties). A window closer to a single real band's own
# width acts as a matched filter and gives every candidate a real,
# distinguishable reward. 0.5 is a single-synthetic-example starting point,
# not tuned against real images' actual band widths.
DEFAULT_REWARD_WINDOW_FRACTION = 0.5


@dataclass(frozen=True)
class TracedPath:
    """One lane's globally-optimal traced path, one x-center per image row.

    `centers` has one entry per row of the *full* image (not just the
    lane's own vertical crop) -- callers slice to whatever vertical range
    they need (e.g. the adaptive comb-fringe/bottom-edge crop `purity.
    analysis` already computes) the same way they would slice a fixed
    `Lane.x_start:x_end` column range.

    `bound_x0`/`bound_x1` are the same neighbor-clamped search bounds passed
    to `trace_lane_viterbi` (see `_bounded_window`) -- carried alongside the
    path so `extract_curved_profile` can clip its extraction window to the
    same bound the DP search itself was clamped to. This matters: a naive
    extraction window of `lane_width` centered on a center near the edge of
    the search range can otherwise reach past that boundary into a
    neighboring lane's own column range even though the *search* was
    correctly bounded -- a variant of the exact neighbor-bleed bug this
    project has already hit once (see module docstring). Found via a real
    regression on a real image while validating this prototype, not
    theoretical -- see AGENTS.md-style validation notes in the module
    docstring.
    """

    lane_index: int
    centers: np.ndarray  # float, shape (height,)
    lane_width: int
    bound_x0: int
    bound_x1: int


def _bounded_window(lane: Lane, left_neighbor: Lane | None, right_neighbor: Lane | None, width: int, margin_fraction: float) -> tuple[int, int]:
    """Candidate x-range for one lane's DP search, clamped to neighbor midpoints.

    Mirrors `core.curve_lanes.trace_lanes_from_detected`'s bound computation
    -- see this module's docstring, "Bounding the search window", for why a
    hard clamp to the midpoint between neighbors (never into a neighbor's
    own column range) matters.
    """
    lane_width = lane.x_end - lane.x_start
    margin = max(1, int(round(lane_width * margin_fraction)))
    x0 = max(0, lane.x_start - margin)
    x1 = min(width, lane.x_end + margin)
    if left_neighbor is not None:
        x0 = max(x0, (left_neighbor.x_end + lane.x_start) // 2)
    if right_neighbor is not None:
        x1 = min(x1, (lane.x_end + right_neighbor.x_start) // 2)
    return x0, x1


def trace_lane_viterbi(
    signal: np.ndarray,
    lane: Lane,
    left_bound: int,
    right_bound: int,
    lambda_smooth: float = DEFAULT_LAMBDA_SMOOTH,
    vertical_smooth_sigma: float = DEFAULT_VERTICAL_SMOOTH_SIGMA,
    reward_window_fraction: float = DEFAULT_REWARD_WINDOW_FRACTION,
) -> TracedPath:
    """Trace one already-identified lane's globally-optimal curved path.

    `left_bound`/`right_bound` hard-clamp the candidate-column range (see
    `_bounded_window` / module docstring) -- this function does not compute
    them itself so a caller tracing every lane on an image only needs to
    detect lanes once and derive neighbor midpoints once, same division of
    responsibility as `core.curve_lanes.trace_lane_from_anchor` /
    `trace_lanes_from_detected`.

    Returns a `TracedPath` covering the *entire* image height; a genuinely
    blank/degenerate lane (no positive signal anywhere in its window) falls
    back to a flat path at the lane's own original centroid, matching
    `core.lanes.Lane`'s behavior for callers that don't want curvature.
    """
    height, image_width = signal.shape
    lane_width = lane.x_end - lane.x_start
    flat_center = (lane.x_start + lane.x_end) / 2.0

    if right_bound <= left_bound or height == 0:
        return TracedPath(
            lane_index=lane.index,
            centers=np.full(height, flat_center),
            lane_width=lane_width,
            bound_x0=left_bound,
            bound_x1=right_bound,
        )

    window = signal[:, left_bound:right_bound]
    smoothed = gaussian_filter1d(window, sigma=vertical_smooth_sigma, axis=0, mode="nearest")

    # Per-row sliding column-window sum, one row at a time -- see module
    # docstring, "Reward function". `uniform_filter1d` computes a mean over
    # `size=lane_width`; multiplying back by `lane_width` recovers the sum
    # so reward magnitude matches a real `Lane`'s own column-sum scale.
    win_width = right_bound - left_bound
    reward_width = max(1, int(round(lane_width * reward_window_fraction)))
    effective_width = max(1, min(reward_width, win_width))
    reward = uniform_filter1d(smoothed, size=effective_width, axis=1, mode="constant", cval=0.0) * effective_width

    if not np.any(reward > 0):
        return TracedPath(
            lane_index=lane.index,
            centers=np.full(height, flat_center),
            lane_width=lane_width,
            bound_x0=left_bound,
            bound_x1=right_bound,
        )

    xs = np.arange(left_bound, right_bound, dtype=np.float64)
    w = xs.size
    # Precompute the W x W displacement-penalty matrix once -- see module
    # docstring, "DP formulation".
    penalty = lambda_smooth * np.abs(xs[:, None] - xs[None, :])

    cost = np.empty((height, w), dtype=np.float64)
    backptr = np.empty((height, w), dtype=np.int64)
    cost[0] = -reward[0]
    backptr[0] = -1

    for r in range(1, height):
        prev = cost[r - 1]
        candidates = prev[None, :] + penalty  # rows=this-row x, cols=prev-row x'
        best_prev_idx = np.argmin(candidates, axis=1)
        best_prev_cost = candidates[np.arange(w), best_prev_idx]
        cost[r] = -reward[r] + best_prev_cost
        backptr[r] = best_prev_idx

    path_idx = np.empty(height, dtype=np.int64)
    path_idx[-1] = int(np.argmin(cost[-1]))
    for r in range(height - 2, -1, -1):
        path_idx[r] = backptr[r + 1, path_idx[r + 1]]

    centers = xs[path_idx]
    return TracedPath(
        lane_index=lane.index,
        centers=centers,
        lane_width=lane_width,
        bound_x0=left_bound,
        bound_x1=right_bound,
    )


def trace_lanes_from_detected(
    signal: np.ndarray,
    lambda_smooth: float = DEFAULT_LAMBDA_SMOOTH,
    vertical_smooth_sigma: float = DEFAULT_VERTICAL_SMOOTH_SIGMA,
    margin_fraction: float = DEFAULT_MARGIN_FRACTION,
    reward_window_fraction: float = DEFAULT_REWARD_WINDOW_FRACTION,
    **detect_lanes_kwargs,
) -> tuple[list[Lane], list[TracedPath]]:
    """Anchor lane count/identity via `detect_lanes`, then DP-trace each lane.

    Returns `(lanes, paths)` in the same order as `detect_lanes` alone would
    return `lanes` -- a caller can still tell which entry is the ladder,
    same contract as `core.curve_lanes.trace_lanes_from_detected`.
    """
    lanes = detect_lanes(signal, **detect_lanes_kwargs)
    width = signal.shape[1]
    paths = []
    for i, lane in enumerate(lanes):
        left_neighbor = lanes[i - 1] if i > 0 else None
        right_neighbor = lanes[i + 1] if i < len(lanes) - 1 else None
        left_bound, right_bound = _bounded_window(lane, left_neighbor, right_neighbor, width, margin_fraction)
        paths.append(
            trace_lane_viterbi(
                signal,
                lane,
                left_bound=left_bound,
                right_bound=right_bound,
                lambda_smooth=lambda_smooth,
                vertical_smooth_sigma=vertical_smooth_sigma,
                reward_window_fraction=reward_window_fraction,
            )
        )
    return lanes, paths


def extract_curved_profile(signal: np.ndarray, path: TracedPath) -> np.ndarray:
    """Sum signal along a traced path, one value per row.

    The curved-path analogue of `Lane`'s fixed `x_start:x_end` rectangle sum
    (`cropped.sum(axis=1)` in `purity.analysis`): at each row, sum the
    signal across a `path.lane_width`-wide window centered on that row's
    traced x-center instead of a column range fixed for the whole image
    height. Output is a 1D profile directly compatible with
    `core.bands.correct_baseline`/`detect_bands`, unchanged -- this is the
    only thing this prototype changes relative to the existing pipeline
    (see module docstring, "What this module is").
    """
    height, image_width = signal.shape
    half = path.lane_width / 2.0
    profile = np.zeros(height)
    for row in range(min(height, path.centers.size)):
        center = path.centers[row]
        # Clip to the same neighbor-clamped search bound the DP path itself
        # was confined to (`path.bound_x0`/`bound_x1`), not just the image
        # edges -- a naive `lane_width`-wide window centered on a traced
        # center near the edge of that bound can otherwise reach into a
        # neighboring lane's own column range even though the *search* was
        # correctly bounded. See `TracedPath`'s docstring: found as a real
        # regression on a real image while validating this prototype.
        x0 = max(0, path.bound_x0, int(round(center - half)))
        x1 = min(image_width, path.bound_x1, int(round(center + half)))
        if x1 > x0:
            profile[row] = float(signal[row, x0:x1].sum())
    return profile
