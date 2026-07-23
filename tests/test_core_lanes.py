import pytest
import numpy as np

from gel_extractor.core.lanes import Lane, apply_lane_corrections, detect_lanes


def test_detect_lanes_finds_expected_number_and_bounds():
    signal = np.zeros((50, 100))
    signal[:, 10:30] = 1.0
    signal[:, 50:70] = 1.0

    lanes = detect_lanes(signal, smoothing_sigma=1.0)

    assert len(lanes) == 2
    assert lanes[0].x_start < 15 and lanes[0].x_end > 25
    assert lanes[1].x_start < 55 and lanes[1].x_end > 65


def test_detect_lanes_merges_small_gap():
    signal = np.zeros((50, 100))
    signal[:, 10:29] = 1.0
    signal[:, 31:50] = 1.0  # 2px gap, narrower than min_gap_width below

    lanes = detect_lanes(signal, smoothing_sigma=0.5, min_gap_width=4)

    assert len(lanes) == 1


def test_detect_lanes_discards_narrow_noise():
    signal = np.zeros((50, 100))
    signal[:, 10:30] = 1.0
    signal[:, 60:62] = 1.0  # narrow spike, should be discarded as noise

    lanes = detect_lanes(signal, smoothing_sigma=0.5, min_lane_width=10)

    assert len(lanes) == 1
    assert lanes[0].x_start < 15


def test_detect_lanes_merges_narrow_fragments_of_one_faded_lane():
    # Several real, well-resolved lanes (width ~40, most of a typical real
    # gel's detected runs -- needed so the width-percentile reference
    # reflects "normal lane width," not get dragged down by the very
    # slivers being tested) plus a cluster of narrow slivers (width ~7-8
    # each) packed into about one lane-width's worth of space -- the
    # fragmentation pattern from a real lane fading toward background (see
    # AGENTS.md Implementation Status, 2026-07-14). Gaps between the slivers
    # (15-20px, comfortably wider than min_gap_width after smoothing) mean
    # mechanism A alone can't bridge them -- only the fragment-merge step
    # (mechanism B) should.
    signal = np.zeros((50, 400))
    signal[:, 10:50] = 1.0
    signal[:, 200:240] = 1.0
    signal[:, 350:390] = 1.0
    signal[:, 100:107] = 0.3
    signal[:, 122:130] = 0.3
    signal[:, 145:152] = 0.3

    lanes = detect_lanes(signal, smoothing_sigma=0.5, threshold_fraction=0.1)

    assert len(lanes) == 4
    fragment_lane = lanes[1]
    assert fragment_lane.x_start <= 100 and fragment_lane.x_end >= 152


def test_detect_lanes_does_not_merge_two_normal_width_lanes():
    # Regression guard (2026-07-14): an earlier version of the fragment-merge
    # fix merged two genuinely separate, normal-width lanes together just
    # because they sat close together and the combined width still looked
    # "reasonable" -- caught on a real image where this merged the ladder
    # lane into the first sample lane. Two lanes of comparable width to each
    # other must stay separate even with a modest gap between them (still
    # comfortably wider than min_gap_width after smoothing, so mechanism A
    # doesn't confound this -- it's mechanism B being tested here).
    signal = np.zeros((50, 300))
    signal[:, 10:50] = 1.0  # width 40
    signal[:, 70:110] = 1.0  # width 40, 20px gap from the first

    lanes = detect_lanes(signal, smoothing_sigma=0.5)

    assert len(lanes) == 2


def test_detect_comb_fringe_end_finds_elevated_variability_region():
    from gel_extractor.core.lanes import detect_comb_fringe_end

    rng = np.random.default_rng(0)
    height, width = 300, 40
    lane_columns = rng.normal(0, 0.01, size=(height, width))
    # Comb fringe: strong side-to-side contrast for the first 60 rows.
    lane_columns[:60, : width // 2] += 1.0

    top_bound = detect_comb_fringe_end(lane_columns)

    assert 55 <= top_bound <= 65


def test_detect_comb_fringe_end_falls_back_when_no_fringe_present():
    from gel_extractor.core.lanes import detect_comb_fringe_end

    lane_columns = np.full((300, 40), 0.5)  # perfectly flat -- no comb fringe anywhere

    top_bound = detect_comb_fringe_end(lane_columns, min_margin_fraction=0.02)

    assert top_bound == int(300 * 0.02)


def test_detect_bottom_edge_artifact_start_finds_anomalous_tail():
    from gel_extractor.core.lanes import detect_bottom_edge_artifact_start

    rng = np.random.default_rng(0)
    height, width = 300, 40
    columns = 0.5 + rng.normal(0, 0.01, size=(height, width))
    # Bottom edge artifact: a bright band near the very bottom.
    columns[270:280, :] = 0.9

    bottom_bound = detect_bottom_edge_artifact_start(columns)

    assert 260 <= bottom_bound <= 271


def test_detect_bottom_edge_artifact_start_falls_back_when_no_artifact_present():
    from gel_extractor.core.lanes import detect_bottom_edge_artifact_start

    columns = np.full((300, 40), 0.5)  # perfectly flat -- nothing anomalous anywhere

    bottom_bound = detect_bottom_edge_artifact_start(columns, min_margin_fraction=0.02)

    assert bottom_bound == 300 - int(300 * 0.02)


def test_apply_lane_corrections_no_op_passes_through_unchanged():
    lanes = [Lane(index=0, x_start=10, x_end=30), Lane(index=1, x_start=50, x_end=70)]

    result = apply_lane_corrections(lanes)

    assert result == lanes


def test_apply_lane_corrections_merges_group_into_one_lane():
    # A smear fragmented into 3 fake lanes -- the dominant real failure mode
    # this exists to fix (see AGENTS.md).
    lanes = [
        Lane(index=0, x_start=100, x_end=110),
        Lane(index=1, x_start=115, x_end=122),
        Lane(index=2, x_start=130, x_end=140),
    ]

    result = apply_lane_corrections(lanes, merge_groups=[[0, 1, 2]])

    assert len(result) == 1
    assert result[0].x_start == 100
    assert result[0].x_end == 140
    assert result[0].index == 0


def test_apply_lane_corrections_drops_lane():
    lanes = [
        Lane(index=0, x_start=10, x_end=30),
        Lane(index=1, x_start=50, x_end=70),  # e.g. a text/well-fringe artifact
        Lane(index=2, x_start=90, x_end=110),
    ]

    result = apply_lane_corrections(lanes, drop=[1])

    assert len(result) == 2
    assert [lane.x_start for lane in result] == [10, 90]
    assert [lane.index for lane in result] == [0, 1]  # re-indexed


def test_apply_lane_corrections_merge_and_drop_together_reindexed_by_position():
    lanes = [
        Lane(index=0, x_start=10, x_end=20),
        Lane(index=1, x_start=30, x_end=35),
        Lane(index=2, x_start=37, x_end=40),
        Lane(index=3, x_start=60, x_end=80),  # dropped
    ]

    result = apply_lane_corrections(lanes, merge_groups=[[1, 2]], drop=[3])

    assert len(result) == 2
    assert result[0].x_start == 10 and result[0].x_end == 20
    assert result[1].x_start == 30 and result[1].x_end == 40
    assert [lane.index for lane in result] == [0, 1]


def test_apply_lane_corrections_rejects_double_reference():
    lanes = [Lane(index=0, x_start=10, x_end=20), Lane(index=1, x_start=30, x_end=40)]

    with pytest.raises(ValueError, match="more than one correction"):
        apply_lane_corrections(lanes, merge_groups=[[0, 1]], drop=[1])


def test_apply_lane_corrections_rejects_unknown_index():
    lanes = [Lane(index=0, x_start=10, x_end=20)]

    with pytest.raises(ValueError, match="unknown lane index"):
        apply_lane_corrections(lanes, drop=[99])
