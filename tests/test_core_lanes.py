import numpy as np

from gel_extractor.core.lanes import detect_lanes


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
