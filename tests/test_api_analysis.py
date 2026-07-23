"""Unit tests for neband.api.analysis -- the pure (no-Flask) request logic
ported from scripts/hitl_ui_server.py's analyze() route. Uses the same
synthetic_gel fixture as the rest of the suite (see tests/conftest.py).
"""

import numpy as np
import pytest

from neband.api.analysis import AnalyzeError, run_analyze, run_lane_detect
from neband.core.image_io import to_signal

LANE_WIDTH = 60
GAP_WIDTH = 20


def _lane_bounds(index: int) -> tuple[int, int]:
    x_start = GAP_WIDTH + index * (LANE_WIDTH + GAP_WIDTH)
    return x_start, x_start + LANE_WIDTH


def _two_lane_signal(synthetic_gel, band_row: float = 100.0, darkness: float = 0.8):
    image = synthetic_gel(
        height=300,
        lane_width=LANE_WIDTH,
        gap_width=GAP_WIDTH,
        band_specs=[[(band_row, darkness)], [(band_row, darkness)]],
    )
    return to_signal(image)


def _lane_dict(index: int, is_ladder: bool = False) -> dict:
    x_start, x_end = _lane_bounds(index)
    return {"client_id": index, "x_start": x_start, "x_end": x_end, "is_ladder": is_ladder}


def test_run_lane_detect_finds_both_lanes_and_defaults_first_to_ladder(synthetic_gel):
    signal = _two_lane_signal(synthetic_gel)
    result = run_lane_detect(signal)

    assert result.width == signal.shape[1]
    assert result.height == signal.shape[0]
    assert len(result.lanes) == 2
    assert result.lanes[0]["is_ladder"] is True
    assert result.lanes[1]["is_ladder"] is False
    # Detected bounds should be roughly close to the synthetic construction's
    # own -- detect_lanes's own thresholding doesn't produce pixel-exact
    # edges, and that's not this test's concern (detect_lanes is already
    # tested elsewhere); this just checks run_lane_detect wires it up sanely.
    expected_0 = _lane_bounds(0)
    assert abs(result.lanes[0]["x_start"] - expected_0[0]) < 10
    assert abs(result.lanes[0]["x_end"] - expected_0[1]) < 10


def test_run_analyze_single_band_lanes_report_full_purity(synthetic_gel):
    signal = _two_lane_signal(synthetic_gel, band_row=100.0)
    lanes = [_lane_dict(0), _lane_dict(1)]
    reference = {"client_id": 0, "row": 100}

    results = run_analyze(
        signal=signal,
        lanes=lanes,
        reference=reference,
        excluded_from_series=set(),
        deleted_ranges_by_lane={},
        known_mws=None,
    )

    assert set(results) == {0, 1}
    for cid in (0, 1):
        assert results[cid]["miss_reason"] is None
        assert results[cid]["purity_percent"] == 100
        assert results[cid]["band_y_start"] is not None


def test_run_analyze_raises_when_click_is_nowhere_near_a_band(synthetic_gel):
    signal = _two_lane_signal(synthetic_gel, band_row=100.0)
    lanes = [_lane_dict(0), _lane_dict(1)]
    reference = {"client_id": 0, "row": 280}  # far from the band at row 100

    with pytest.raises(AnalyzeError):
        run_analyze(
            signal=signal,
            lanes=lanes,
            reference=reference,
            excluded_from_series=set(),
            deleted_ranges_by_lane={},
            known_mws=None,
        )


def test_run_analyze_excluded_lane_is_omitted_from_results(synthetic_gel):
    signal = _two_lane_signal(synthetic_gel, band_row=100.0)
    lanes = [_lane_dict(0), _lane_dict(1)]
    reference = {"client_id": 0, "row": 100}

    results = run_analyze(
        signal=signal,
        lanes=lanes,
        reference=reference,
        excluded_from_series={1},
        deleted_ranges_by_lane={},
        known_mws=None,
    )

    assert set(results) == {0}


def test_deleted_target_band_falls_back_to_unmatched_not_a_different_candidate(synthetic_gel):
    signal = _two_lane_signal(synthetic_gel, band_row=100.0)
    lanes = [_lane_dict(0), _lane_dict(1)]
    reference = {"client_id": 0, "row": 100}

    baseline = run_analyze(
        signal=signal,
        lanes=lanes,
        reference=reference,
        excluded_from_series=set(),
        deleted_ranges_by_lane={},
        known_mws=None,
    )
    target_range = (baseline[0]["band_y_start"], baseline[0]["band_y_end"])

    results = run_analyze(
        signal=signal,
        lanes=lanes,
        reference=reference,
        excluded_from_series=set(),
        deleted_ranges_by_lane={0: {target_range}},
        known_mws=None,
    )

    assert results[0]["purity_percent"] is None
    assert results[0]["band_y_start"] is None
    assert results[0]["miss_reason"] == "target band manually deleted"
    # The deleted band must not still appear in the debug overlay list.
    assert all(b["y_start"] != target_range[0] for b in results[0]["all_bands"])


def test_run_analyze_populates_matched_mw_when_ladder_calibrates(synthetic_gel):
    height = 300
    top_margin = int(height * 0.05)
    slope, intercept = -0.01, 2.3
    known_mws = [100.0, 50.0, 25.0]

    def post_crop_pos(mw):
        return (intercept - np.log10(mw)) / -slope

    ladder_bands = [(post_crop_pos(mw) + top_margin, 0.7) for mw in known_mws]
    target_pos = post_crop_pos(25.0) + top_margin

    image = synthetic_gel(
        height=height,
        lane_width=LANE_WIDTH,
        gap_width=GAP_WIDTH,
        band_specs=[ladder_bands, [(target_pos, 0.6)]],
    )
    signal = to_signal(image)

    lanes = [_lane_dict(0, is_ladder=True), _lane_dict(1)]
    reference = {"client_id": 1, "row": int(target_pos)}

    results = run_analyze(
        signal=signal,
        lanes=lanes,
        reference=reference,
        excluded_from_series=set(),
        deleted_ranges_by_lane={},
        known_mws=known_mws,
    )

    assert results[1]["miss_reason"] is None
    assert results[1]["matched_mw"] is not None
    assert results[1]["matched_mw"] == pytest.approx(25.0, rel=0.2)
