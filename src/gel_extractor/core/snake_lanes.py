"""Deformable-contour ("snake") lane tracing -- an alternative geometry to
straight rectangular lanes for gel "smiling" (edge lanes migrating
faster/slower than center lanes, curving bands across the image).

Core idea: seed one open active contour ("snake") per already-detected lane
(seed x-position/count from `core.lanes.detect_lanes` -- lane identity is not
re-derived here) as a straight vertical line from the lane's own detected
position down through the full resolving-gel height, anchor its top endpoint
(the well position -- migration/curvature is, by definition, zero there), and
let `skimage.segmentation.active_contour` deform the rest of the line along
the image's *own* local intensity signal so it settles onto the lane's real
migration path -- including any real curvature -- without assuming any fixed
lane width, pitch, or rectangular geometry.

This is deliberately a different bias/variance tradeoff than the per-row
local-centroid tracer already explored on `curve-tracing-lane-detection`
(see AGENTS.md's curve-tracing v1/v2/v3 entries): a snake's whole shape is
optimized jointly against internal elasticity/rigidity terms, so it can't
jump erratically row to row the way an unconstrained local search can --
smoothness is built into the model itself, not bolted on after the fact --
but by the same token it also can't "notice" a genuinely sharp real kink the
way a per-row search could.

Per AGENTS.md's caught-bug warning (a windowed search reaching into a
*neighboring* lane's own signal, producing a false centroid instead of a
correct not-found, when lanes sit close together): the snake's external-
energy image is *hard-masked* to each lane's own bounded window -- at most
the midpoint to its nearest detected neighbor -- before being handed to
`active_contour`, not merely biased toward staying inside it. A neighboring
lane's signal is invisible to the snake by construction. The converged
snake's column position is additionally clamped back into that same window
afterward, since `active_contour` itself has no awareness of the bound (only
the masked-to-zero input image discourages drifting past it).

Reuse, not reimplementation: seed positions/count come from
`core.lanes.detect_lanes`; each lane's adaptive top crop comes from
`core.lanes.detect_comb_fringe_end` (identical to the straight-rectangle
pipeline in `purity.analysis`); the shared bottom crop
(`detect_bottom_edge_artifact_start`) is expected to be computed once by the
caller (an image-wide, cross-lane computation, not this module's concern).
This module only produces a per-lane 1D intensity profile via a curved path
instead of a straight column range -- band detection, ladder calibration,
and the purity-percentage formula are all `purity.analysis`'s, untouched.
"""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter
from skimage.segmentation import active_contour

from gel_extractor.core.lanes import Lane, detect_comb_fringe_end

# Number of control points along the snake's length. Coarser than the image's
# own row count on purpose -- `active_contour` optimizes all points jointly,
# so more points means more degrees of freedom for noise to wiggle into; the
# converged snake is resampled back to one column-position-per-row afterward
# (see `extract_snake_profile`), so this doesn't limit the profile's
# resolution, only the snake's own shape complexity.
DEFAULT_NUM_POINTS = 30

# Snake shape parameters (skimage.segmentation.active_contour). Kept fairly
# rigid (low alpha, moderate-high beta) deliberately: a lane's real migration
# path is a gentle, single-direction drift (smiling), not a wiggly line, and
# the anchored-tracing prototype on `curve-tracing-lane-detection` found a
# similarly-motivated caught bug from a search being *too* locally reactive
# to noise. w_line is positive (attract toward high `signal` values --
# `core.image_io.to_signal` already normalizes so higher = more stain, not
# raw pixel brightness) and w_edge is left at 0 -- lane tracing wants to
# follow the ridge of the lane's own stain density, not snap to hard edges.
DEFAULT_ALPHA = 0.02
DEFAULT_BETA = 8.0
DEFAULT_W_LINE = 1.0
DEFAULT_W_EDGE = 0.0
DEFAULT_GAMMA = 0.1
DEFAULT_MAX_PX_MOVE = 1.5
DEFAULT_MAX_NUM_ITER = 300

# The external-energy image is Gaussian-smoothed before being handed to
# active_contour -- a raw per-pixel signal is noisy enough that its gradient
# field is mostly noise; smoothing gives the snake a real basin to descend
# into rather than getting stuck against pixel-level jitter immediately.
DEFAULT_GAUSSIAN_SIGMA = 4.0

# How far outside the lane's own detected column range the snake's search
# window may extend, as a fraction of that lane's own width -- a real
# curving lane can legitimately drift outside the width `detect_lanes`
# originally measured (that measurement collapses the whole image height
# into one number, see AGENTS.md's root-cause note on lane over-
# segmentation). Still hard-clamped to the midpoint to the nearest
# neighboring lane (see module docstring) -- this only controls how much
# margin is *requested* before that clamp is applied, never overrides it.
DEFAULT_WINDOW_MARGIN_FRACTION = 0.75

# Width of the column window summed at each row to build the final profile
# from the converged snake path, as a fraction of the lane's own detected
# width -- deliberately derived from this image's own already-detected lane
# width at runtime, not any fixed/comb-derived pitch (see AGENTS.md: the gel
# is a flexible medium no longer locked to the comb once run).
DEFAULT_SAMPLE_HALF_WIDTH_FRACTION = 0.5


@dataclass(frozen=True)
class SnakeTrace:
    """A converged snake and the profile extracted along it, for one lane."""

    lane_index: int
    top_bound: int
    snake: np.ndarray  # (K, 2) array of (row, col), in full-image coordinates
    window_left: int
    window_right: int
    profile: np.ndarray  # one intensity value per row, top_bound..bottom_bound


def lane_window_bounds(
    lanes: list[Lane],
    index: int,
    image_width: int,
    margin_fraction: float = DEFAULT_WINDOW_MARGIN_FRACTION,
) -> tuple[int, int]:
    """This lane's allowed column window: its own span plus a margin, hard-
    clamped to the midpoint to its nearest detected neighbor (or the image
    edge, at the ends). See module docstring for why the clamp is a hard
    mask rather than a soft preference -- this is the fix for the
    neighboring-lane-bleed bug the sibling curve-tracing branch caught.
    """
    lane = lanes[index]
    width = lane.x_end - lane.x_start
    margin = width * margin_fraction
    left = lane.x_start - margin
    right = lane.x_end + margin

    if index > 0:
        left_mid = (lanes[index - 1].x_end + lane.x_start) / 2.0
        left = max(left, left_mid)
    else:
        left = max(left, 0)

    if index < len(lanes) - 1:
        right_mid = (lane.x_end + lanes[index + 1].x_start) / 2.0
        right = min(right, right_mid)
    else:
        right = min(right, image_width)

    return int(round(left)), int(round(right))


def trace_lane_snake(
    signal: np.ndarray,
    lanes: list[Lane],
    index: int,
    top_bound: int,
    bottom_bound: int,
    num_points: int = DEFAULT_NUM_POINTS,
    window_margin_fraction: float = DEFAULT_WINDOW_MARGIN_FRACTION,
    gaussian_sigma: float = DEFAULT_GAUSSIAN_SIGMA,
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    w_line: float = DEFAULT_W_LINE,
    w_edge: float = DEFAULT_W_EDGE,
    gamma: float = DEFAULT_GAMMA,
    max_px_move: float = DEFAULT_MAX_PX_MOVE,
    max_num_iter: int = DEFAULT_MAX_NUM_ITER,
) -> tuple[np.ndarray, int, int]:
    """Deform a straight vertical line down one lane onto its real path.

    Returns `(snake, window_left, window_right)` -- the converged (K, 2)
    array of (row, col) points in full-image coordinates, and the hard
    column bound the snake was masked to (for callers building a debug
    visualization or re-deriving the sample width). The top endpoint is
    anchored at this lane's own detected centroid column and pinned via
    `boundary_condition="fixed-free"` (fixed at the well, free at the
    bottom) -- the well position is the one point on a curving lane that
    isn't in question, since migration-driven drift is zero there by
    definition.
    """
    lane = lanes[index]
    height, width = signal.shape
    left, right = lane_window_bounds(lanes, index, width, window_margin_fraction)
    anchor_x = float(np.clip((lane.x_start + lane.x_end) / 2.0, left, right - 1))

    if bottom_bound <= top_bound + 1 or right <= left:
        # Degenerate crop (near-zero height or a fully neighbor-squeezed
        # window) -- nothing meaningful to trace. Fall back to a straight
        # line at the lane's own centroid, i.e. identical behavior to the
        # plain rectangular approach for this lane.
        rows = np.linspace(top_bound, max(top_bound, bottom_bound - 1), num_points)
        snake = np.column_stack([rows, np.full(num_points, anchor_x)])
        return snake, left, right

    window_image = np.zeros_like(signal)
    window_image[top_bound:bottom_bound, left:right] = signal[top_bound:bottom_bound, left:right]
    smoothed = gaussian_filter(window_image, sigma=gaussian_sigma)

    rows = np.linspace(top_bound, bottom_bound - 1, num_points)
    init_snake = np.column_stack([rows, np.full(num_points, anchor_x)])

    snake = active_contour(
        smoothed,
        init_snake,
        alpha=alpha,
        beta=beta,
        w_line=w_line,
        w_edge=w_edge,
        gamma=gamma,
        max_px_move=max_px_move,
        max_num_iter=max_num_iter,
        boundary_condition="fixed-free",
    )
    # active_contour has no awareness of the neighbor-midpoint bound itself
    # (only the masked-to-zero input image discourages drifting past it) --
    # clamp explicitly so a downstream profile extraction can never sample
    # a neighboring lane's own signal, matching the fix already validated on
    # the sibling curve-tracing branch for its own (differently-shaped)
    # version of this same risk.
    snake[:, 1] = np.clip(snake[:, 1], left, right - 1)
    return snake, left, right


def extract_snake_profile(
    signal: np.ndarray,
    snake: np.ndarray,
    top_bound: int,
    bottom_bound: int,
    sample_half_width: float,
    window_left: int,
    window_right: int,
) -> np.ndarray:
    """Sum `signal` in a window around the converged snake at every row.

    The curved-path analogue of a straight lane's `column_range.sum(axis=1)`:
    interpolates the snake's (sparse control-point) column position to one
    value per row, then sums a `sample_half_width`-wide window centered on
    that row's traced column. The sampling window is itself clamped to
    `[window_left, window_right)` -- the same neighbor-bounded window the
    snake was traced within -- so widening the sample window can't leak into
    a neighboring lane's signal either, even if `sample_half_width` is
    generous.
    """
    rows = np.arange(top_bound, bottom_bound)
    if len(rows) == 0:
        return np.zeros(0)

    order = np.argsort(snake[:, 0])
    col_at_row = np.interp(rows, snake[order, 0], snake[order, 1])

    profile = np.empty(len(rows), dtype=float)
    for i, col in enumerate(col_at_row):
        lo = max(window_left, int(round(col - sample_half_width)))
        hi = min(window_right, int(round(col + sample_half_width)) + 1)
        profile[i] = signal[rows[i], lo:hi].sum() if hi > lo else 0.0
    return profile


def trace_and_extract_profile(
    signal: np.ndarray,
    lanes: list[Lane],
    index: int,
    bottom_bound: int,
    sample_half_width_fraction: float = DEFAULT_SAMPLE_HALF_WIDTH_FRACTION,
    **snake_kwargs,
) -> tuple[np.ndarray, int, np.ndarray]:
    """Full per-lane pipeline: adaptive top crop -> snake trace -> profile.

    Returns `(profile, top_bound, snake)`. `top_bound` mirrors
    `purity.analysis._adaptive_crop`'s return value exactly (same
    `detect_comb_fringe_end` call, same lane, same semantics) so a caller can
    compute `position_offset` (this lane's `top_bound` minus the ladder
    lane's own `top_bound`) the same way the real pipeline does, before MW
    calibration -- see AGENTS.md on why skipping this silently produces
    wrong MWs for every sample lane.
    """
    lane = lanes[index]
    lane_columns = signal[:, lane.x_start : lane.x_end]
    top_bound = detect_comb_fringe_end(lane_columns)

    width = lane.x_end - lane.x_start
    sample_half_width = max(1.0, width * sample_half_width_fraction / 2.0)

    snake, window_left, window_right = trace_lane_snake(
        signal, lanes, index, top_bound, bottom_bound, **snake_kwargs
    )
    profile = extract_snake_profile(
        signal, snake, top_bound, bottom_bound, sample_half_width, window_left, window_right
    )
    return profile, top_bound, snake
