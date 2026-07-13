import numpy as np
import pytest

from gel_extractor.core.ladder import (
    LadderCalibrationError,
    UnknownLadderError,
    calibrate_ladder,
    get_ladder_bands,
)


def _synthetic_ladder_profile(positions: list[float], height: int) -> np.ndarray:
    x = np.arange(height)
    profile = np.zeros(height)
    for pos in positions:
        profile += 5.0 * np.exp(-((x - pos) ** 2) / (2 * 3.0**2))
    return profile


def test_calibrate_ladder_recovers_mw_at_known_positions():
    known_mws = [250, 100, 50, 25, 10]
    slope, intercept = -0.01, 3.0  # intercept > log10(max mw) keeps all positions positive
    # Construct positions exactly consistent with the fit model so the test
    # verifies calibrate_ladder recovers a known-true relationship, not just
    # "close enough" to an arbitrary one.
    positions = [(np.log10(mw) - intercept) / slope for mw in known_mws]
    profile = _synthetic_ladder_profile(positions, height=int(max(positions)) + 50)

    calibration = calibrate_ladder(profile, known_mws)

    for pos, mw in zip(positions, known_mws):
        estimated = calibration.mw_at(pos)
        assert abs(estimated - mw) / mw < 0.05


def test_calibrate_ladder_raises_when_too_few_bands_detected():
    # Only 2 bands detected -- below MIN_MATCHED_BANDS regardless of how many
    # known sizes are supplied, since a 2-point fit can't be sanity-checked.
    profile = _synthetic_ladder_profile([50, 150], height=250)
    with pytest.raises(LadderCalibrationError):
        calibrate_ladder(profile, [250, 100, 50])


def test_calibrate_ladder_works_with_fewer_bands_than_known():
    # Simulates real SDS-PAGE log-compression: the top 2 (highest-MW) known
    # bands never resolved into detectable peaks, but the bottom 4 did.
    known_mws = [250, 180, 100, 50, 25, 10]
    slope, intercept = -0.01, 3.0
    detected_mws = [100, 50, 25, 10]  # the best-resolved, lowest-MW subset
    positions = [(np.log10(mw) - intercept) / slope for mw in detected_mws]
    profile = _synthetic_ladder_profile(positions, height=int(max(positions)) + 50)

    calibration = calibrate_ladder(profile, known_mws)

    assert len(calibration.positions) == 4
    for pos, mw in zip(positions, detected_mws):
        assert abs(calibration.mw_at(pos) - mw) / mw < 0.05


def test_calibrate_ladder_keeps_most_prominent_bands_when_over_detected():
    known_mws = [100, 50, 25, 10]
    slope, intercept = -0.01, 2.5
    real_positions = [(np.log10(mw) - intercept) / slope for mw in known_mws]
    height = int(max(real_positions)) + 50

    x = np.arange(height)
    profile = np.zeros(height)
    for pos in real_positions:
        profile += 5.0 * np.exp(-((x - pos) ** 2) / (2 * 3.0**2))
    # Add small, low-amplitude noise "bands" that shouldn't be mistaken for
    # real ladder bands.
    for noise_pos in [30, 90, 150]:
        profile += 0.3 * np.exp(-((x - noise_pos) ** 2) / (2 * 2.0**2))

    calibration = calibrate_ladder(profile, known_mws)

    assert len(calibration.positions) == 4
    for pos, mw in zip(real_positions, known_mws):
        assert abs(calibration.mw_at(pos) - mw) / mw < 0.1


def test_calibrate_ladder_raises_on_poor_fit():
    # 4 clearly-separated, individually-detectable bands whose spacing is
    # inconsistent with a log-linear fit to the assumed bottom-4 known sizes
    # [150, 140, 130, 10] (R^2 ~0.64, verified numerically) -- signals the
    # assumed correspondence doesn't hold, not just "too few bands."
    profile = _synthetic_ladder_profile([50, 90, 130, 170], height=230)
    with pytest.raises(LadderCalibrationError):
        calibrate_ladder(profile, [250, 200, 150, 140, 130, 10], min_r_squared=0.9)


def test_get_ladder_bands_raises_for_unknown_name():
    with pytest.raises(UnknownLadderError):
        get_ladder_bands("not-a-real-ladder")


def test_get_ladder_bands_returns_p7719():
    bands = get_ladder_bands("P7719")
    assert bands == [250.0, 180.0, 130.0, 95.0, 72.0, 55.0, 43.0, 34.0, 26.0, 17.0, 10.0]
    assert bands == sorted(bands, reverse=True)
