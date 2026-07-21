"""Registry of alternative lane-geometry methods for the purity workflow.

Each prototype module (`core.viterbi_lanes`, and more added phase by phase --
see the multi-method-lane-detection implementation plan) lives unmodified,
exactly as validated on its own branch during the 2026-07-16 Workflow
exploration (see AGENTS.md Implementation Status). The adapter functions
below are the only new code that calls into them: each one calls that
prototype's own real entry point (the same call pattern already proven in
that prototype's own scripts/compare_*.py) inside a try/except covering its
documented failure modes, normalizing everything into one common
`MethodOutcome` shape. This keeps the prototype modules themselves at zero
regression risk, and gives `purity.cli`/`purity.debug_viz`/`purity.output`
exactly one shape to render regardless of which method actually ran.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from gel_extractor.core.image_io import load_image, to_signal
from gel_extractor.core.lanes import detect_comb_fringe_end, detect_lanes
from gel_extractor.core.ridge_lanes import (
    DEFAULT_MIN_ROW_SMOOTHING_SIGMA as RIDGE_MIN_ROW_SMOOTHING_SIGMA,
    DEFAULT_PROFILE_HALF_WIDTH_FRACTION as RIDGE_PROFILE_HALF_WIDTH_FRACTION,
    DEFAULT_ROW_SMOOTHING_FRACTION as RIDGE_ROW_SMOOTHING_FRACTION,
    _lane_neighbor_bounds as _ridge_lane_neighbor_bounds,
    _reference_lane_width as _ridge_reference_lane_width,
    compute_ridge_response,
    extract_curved_profile as ridge_extract_curved_profile,
    trace_centerline,
)
from gel_extractor.core.snake_lanes import trace_and_extract_profile
from gel_extractor.core.viterbi_lanes import extract_curved_profile, trace_lanes_from_detected
from gel_extractor.purity.analysis import (
    AnalysisDebugInfo,
    Centerline,
    DEFAULT_BAND_SELECTION,
    DEFAULT_MW_TOLERANCE_PERCENT,
    LadderNotCalibratedError,
    LaneResult,
    _analyze_signal,
    _default_crop_lane,
    analyze_image,
)


@dataclass(frozen=True)
class MethodOutcome:
    """Normalized result of running one lane-geometry method against one image.

    Either `results`/`ladder_lane_index`/`debug_info` are set (success) or
    `error` is set (failure) -- never a mix, and never a raised exception
    escaping an adapter. `lane_numbering_caveat`, when set, means this
    method's lane numbering/count isn't guaranteed to line up with the
    default rectangle method's -- a combined multi-method view must not
    imply "lane 3" is the same physical sample across methods when this is
    set (see the implementation plan's "Finding 3").
    """

    method: str
    maturity: str
    results: list[LaneResult] | None = None
    ladder_lane_index: int | None = None
    debug_info: AnalysisDebugInfo | None = None
    error: str | None = None
    lane_numbering_caveat: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class MethodInfo:
    """One entry in `METHOD_REGISTRY` -- everything needed to run a method
    and to render its confidence/maturity very clearly in every output
    surface (table, CSV/JSON, debug image), per the explicit product
    requirement behind this whole module.
    """

    key: str
    label: str
    maturity: str  # "stable" | "promising" | "experimental" | "research_preview" | "gated"
    caveats: list[str]
    adapter: Callable[..., MethodOutcome]


def _run_rectangle(
    path: Path | str,
    target_mw: float | None,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    band_selection: str = DEFAULT_BAND_SELECTION,
) -> MethodOutcome:
    """Today's default, unchanged pipeline -- `analyze_image` with no
    `crop_lane` override, so straight-rectangle behavior is exactly what it
    always was. The trivial adapter: every other method's adapter follows
    this same rescue-block shape.
    """
    info = METHOD_REGISTRY["rectangle"]
    try:
        results, ladder_idx, debug_info = analyze_image(
            path,
            target_mw,
            ladder=ladder,
            ladder_bands=ladder_bands,
            ladder_lane_index=ladder_lane_index,
            lane_index=lane_index,
            tolerance_percent=tolerance_percent,
            allow_heuristic=allow_heuristic,
            band_selection=band_selection,
        )
    except (LadderNotCalibratedError, ValueError) as exc:
        return MethodOutcome(method=info.key, maturity=info.maturity, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- defense in depth, see module docstring
        return MethodOutcome(method=info.key, maturity=info.maturity, error=f"{type(exc).__name__}: {exc}")
    return MethodOutcome(
        method=info.key, maturity=info.maturity, results=results, ladder_lane_index=ladder_idx, debug_info=debug_info
    )


def _run_viterbi(
    path: Path | str,
    target_mw: float | None,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    band_selection: str = DEFAULT_BAND_SELECTION,
) -> MethodOutcome:
    """Globally-optimal (DP/Viterbi) curved lane tracing -- see
    `core.viterbi_lanes` for the algorithm itself. Reuses `detect_lanes`'s
    lane count/identity unchanged (via `trace_lanes_from_detected`); only
    the per-lane profile-extraction geometry differs from `rectangle`.
    """
    info = METHOD_REGISTRY["viterbi"]
    try:
        image = load_image(path)
        signal = to_signal(image)
        lanes, paths = trace_lanes_from_detected(signal)
        paths_by_index = {p.lane_index: p for p in paths}

        def crop_lane(signal: np.ndarray, lane, bottom_bound: int):
            path_obj = paths_by_index[lane.index]
            top_bound = detect_comb_fringe_end(signal[:, lane.x_start : lane.x_end])
            full_profile = extract_curved_profile(signal, path_obj)
            profile = full_profile[top_bound:bottom_bound]
            centerline = Centerline(
                rows=np.arange(top_bound, bottom_bound),
                xs=path_obj.centers[top_bound:bottom_bound],
            )
            return profile, top_bound, centerline

        results, ladder_idx, debug_info = _analyze_signal(
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
    except (LadderNotCalibratedError, ValueError) as exc:
        return MethodOutcome(method=info.key, maturity=info.maturity, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- viterbi_lanes itself never raises, but guard anyway
        return MethodOutcome(method=info.key, maturity=info.maturity, error=f"{type(exc).__name__}: {exc}")
    return MethodOutcome(
        method=info.key, maturity=info.maturity, results=results, ladder_lane_index=ladder_idx, debug_info=debug_info
    )


def _run_ridge(
    path: Path | str,
    target_mw: float | None,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    band_selection: str = DEFAULT_BAND_SELECTION,
) -> MethodOutcome:
    """Ridge/vesselness-filter (`skimage.filters.meijering`) curved lane
    tracing -- see `core.ridge_lanes` for the algorithm. Its own
    `analyze_image_ridge` discards each lane's `Band` list (only keeps
    `LaneResult`s), so this adapter uses its lower-level primitives
    (`compute_ridge_response`/`trace_centerline`) as a `crop_lane` instead of
    calling that function directly -- the same reason `viterbi` isn't called
    via any standalone wrapper, and needed here for real band boxes in
    `--debug` output, not just a curve line.
    """
    info = METHOD_REGISTRY["ridge"]
    try:
        image = load_image(path)
        signal = to_signal(image)
        lanes = detect_lanes(signal)
        if not lanes:
            return MethodOutcome(method=info.key, maturity=info.maturity, error=f"No lanes detected in {path!r}")

        width = signal.shape[1]
        reference_width = _ridge_reference_lane_width(lanes)
        neighbor_bounds = _ridge_lane_neighbor_bounds(lanes, width, reference_width)
        bounds_by_index = {lane.index: bounds for lane, bounds in zip(lanes, neighbor_bounds)}
        ridge_response = compute_ridge_response(signal, reference_width)

        def crop_lane(signal: np.ndarray, lane, bottom_bound: int):
            left_bound, right_bound = bounds_by_index[lane.index]
            top_bound = detect_comb_fringe_end(signal[:, lane.x_start : lane.x_end])
            n_rows = max(bottom_bound - top_bound, 0)
            row_smoothing_sigma = max(n_rows * RIDGE_ROW_SMOOTHING_FRACTION, RIDGE_MIN_ROW_SMOOTHING_SIGMA)
            centers = trace_centerline(
                ridge_response, lane, left_bound, right_bound, top_bound, bottom_bound, row_smoothing_sigma
            )
            half_width = (lane.x_end - lane.x_start) * RIDGE_PROFILE_HALF_WIDTH_FRACTION
            profile = ridge_extract_curved_profile(signal, centers, top_bound, half_width, left_bound, right_bound)
            centerline = Centerline(rows=np.arange(top_bound, bottom_bound), xs=centers)
            return profile, top_bound, centerline

        results, ladder_idx, debug_info = _analyze_signal(
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
    except (LadderNotCalibratedError, ValueError) as exc:
        return MethodOutcome(method=info.key, maturity=info.maturity, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- defense in depth, see module docstring
        return MethodOutcome(method=info.key, maturity=info.maturity, error=f"{type(exc).__name__}: {exc}")
    return MethodOutcome(
        method=info.key, maturity=info.maturity, results=results, ladder_lane_index=ladder_idx, debug_info=debug_info
    )


def _run_snake(
    path: Path | str,
    target_mw: float | None,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
    band_selection: str = DEFAULT_BAND_SELECTION,
) -> MethodOutcome:
    """Deformable active-contour ("snake") lane tracing -- see
    `core.snake_lanes`. Unlike the other per-lane methods, a single lane's
    snake can fail to converge without the whole method being unusable --
    `crop_lane` catches that *per lane* and falls back to the plain
    rectangle crop for just that one lane, rather than failing the whole
    image (per the implementation plan's Phase B note).
    """
    info = METHOD_REGISTRY["snake"]
    try:
        image = load_image(path)
        signal = to_signal(image)
        lanes = detect_lanes(signal)
        if not lanes:
            return MethodOutcome(method=info.key, maturity=info.maturity, error=f"No lanes detected in {path!r}")
        position_by_index = {lane.index: position for position, lane in enumerate(lanes)}

        def crop_lane(signal: np.ndarray, lane, bottom_bound: int):
            position = position_by_index[lane.index]
            try:
                profile, top_bound, snake = trace_and_extract_profile(signal, lanes, position, bottom_bound)
            except Exception:  # noqa: BLE001 -- per-lane rescue, see docstring above
                return _default_crop_lane(signal, lane, bottom_bound)
            rows = np.arange(top_bound, bottom_bound)
            order = np.argsort(snake[:, 0])
            xs = np.interp(rows, snake[order, 0], snake[order, 1])
            centerline = Centerline(rows=rows, xs=xs)
            return profile, top_bound, centerline

        results, ladder_idx, debug_info = _analyze_signal(
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
    except (LadderNotCalibratedError, ValueError) as exc:
        return MethodOutcome(method=info.key, maturity=info.maturity, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- defense in depth, see module docstring
        return MethodOutcome(method=info.key, maturity=info.maturity, error=f"{type(exc).__name__}: {exc}")
    return MethodOutcome(
        method=info.key, maturity=info.maturity, results=results, ladder_lane_index=ladder_idx, debug_info=debug_info
    )


METHOD_REGISTRY: dict[str, MethodInfo] = {
    "rectangle": MethodInfo(
        key="rectangle",
        label="Straight rectangle (default)",
        maturity="stable",
        caveats=[],
        adapter=_run_rectangle,
    ),
    "viterbi": MethodInfo(
        key="viterbi",
        label="Globally-optimal curved tracing (DP/Viterbi)",
        maturity="promising",
        caveats=[
            "Only reshapes each already-identified lane's profile -- does not fix lane "
            "over/under-segmentation (detect_lanes's lane count is reused unchanged).",
            "A few lanes have shown large, hard-to-explain purity swings vs. rectangle on "
            "real images with no per-lane ground truth to say which is more correct.",
        ],
        adapter=_run_viterbi,
    ),
    "ridge": MethodInfo(
        key="ridge",
        label="Ridge/vesselness-filter curved tracing (Frangi-style)",
        maturity="experimental",
        caveats=[
            "Only reshapes each already-identified lane's profile -- does not fix lane "
            "over/under-segmentation (detect_lanes's lane count is reused unchanged).",
            "Real, unresolved tuning tension: more row-smoothing sometimes tightens "
            "agreement with the rectangle baseline and sometimes worsens it sharply on "
            "the same real image set -- not swept away by a single 'smoother is better' fix.",
            "Per-lane purity has diverged from rectangle by as much as ~95 points on a "
            "single real lane in prior validation -- treat any large delta with suspicion, "
            "not automatically as an improvement.",
        ],
        adapter=_run_ridge,
    ),
    "snake": MethodInfo(
        key="snake",
        label="Deformable active-contour tracing (snake)",
        maturity="experimental",
        caveats=[
            "Only reshapes each already-identified lane's profile -- does not fix lane "
            "over/under-segmentation (detect_lanes's lane count is reused unchanged).",
            "Deliberately rigid (low alpha, moderate-high beta) -- can't represent a "
            "genuinely sharp real kink in a lane's path, only gentle drift.",
            "A single lane's snake failing to converge falls back to that one lane's plain "
            "rectangle crop rather than failing the whole image -- check --debug if a "
            "particular lane looks like a straight line when others show real curvature.",
        ],
        adapter=_run_snake,
    ),
}


def run_method(key: str, path: Path | str, target_mw: float | None, **kwargs) -> MethodOutcome:
    """Run one registered method by key. Unknown keys are a caller bug
    (typically a CLI argparse `choices=` mismatch), not a runtime data
    problem -- raises `KeyError` rather than returning a `MethodOutcome`,
    so it's never silently swallowed.
    """
    if key not in METHOD_REGISTRY:
        raise KeyError(f"Unknown method {key!r} -- registered methods: {sorted(METHOD_REGISTRY)}")
    return METHOD_REGISTRY[key].adapter(path, target_mw, **kwargs)


def run_all_methods(path: Path | str, target_mw: float | None, **kwargs) -> list[MethodOutcome]:
    """Run every registered method against the same image, in registration
    order. Each method's own adapter already catches its documented failure
    modes -- a method that fails still returns a `MethodOutcome` (with
    `error` set), never raises, so one bad method can't abort this loop.
    """
    return [info.adapter(path, target_mw, **kwargs) for info in METHOD_REGISTRY.values()]
