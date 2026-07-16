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
from gel_extractor.core.lanes import detect_comb_fringe_end
from gel_extractor.core.viterbi_lanes import extract_curved_profile, trace_lanes_from_detected
from gel_extractor.purity.analysis import (
    AnalysisDebugInfo,
    Centerline,
    DEFAULT_MW_TOLERANCE_PERCENT,
    LadderNotCalibratedError,
    LaneResult,
    _analyze_signal,
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
    target_mw: float,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
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
    target_mw: float,
    ladder: str | None = None,
    ladder_bands: list[float] | None = None,
    ladder_lane_index: int | None = None,
    lane_index: int | None = None,
    tolerance_percent: float = DEFAULT_MW_TOLERANCE_PERCENT,
    allow_heuristic: bool = False,
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
            crop_lane=crop_lane,
        )
    except (LadderNotCalibratedError, ValueError) as exc:
        return MethodOutcome(method=info.key, maturity=info.maturity, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- viterbi_lanes itself never raises, but guard anyway
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
}


def run_method(key: str, path: Path | str, target_mw: float, **kwargs) -> MethodOutcome:
    """Run one registered method by key. Unknown keys are a caller bug
    (typically a CLI argparse `choices=` mismatch), not a runtime data
    problem -- raises `KeyError` rather than returning a `MethodOutcome`,
    so it's never silently swallowed.
    """
    if key not in METHOD_REGISTRY:
        raise KeyError(f"Unknown method {key!r} -- registered methods: {sorted(METHOD_REGISTRY)}")
    return METHOD_REGISTRY[key].adapter(path, target_mw, **kwargs)


def run_all_methods(path: Path | str, target_mw: float, **kwargs) -> list[MethodOutcome]:
    """Run every registered method against the same image, in registration
    order. Each method's own adapter already catches its documented failure
    modes -- a method that fails still returns a `MethodOutcome` (with
    `error` set), never raises, so one bad method can't abort this loop.
    """
    return [info.adapter(path, target_mw, **kwargs) for info in METHOD_REGISTRY.values()]
