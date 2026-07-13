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


def test_lane_crop_excludes_top_margin():
    from gel_extractor.core.lanes import Lane

    signal = np.arange(100 * 20).reshape(100, 20).astype(float)
    lane = Lane(index=0, x_start=0, x_end=20)

    cropped = lane.crop(signal, top_margin_fraction=0.1)

    assert cropped.shape[0] == 90
    assert np.array_equal(cropped, signal[10:, :])
