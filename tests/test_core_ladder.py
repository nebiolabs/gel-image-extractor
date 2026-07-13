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


def test_calibrate_ladder_raises_on_band_count_mismatch():
    profile = _synthetic_ladder_profile([50, 150], height=250)
    with pytest.raises(LadderCalibrationError):
        calibrate_ladder(profile, [250, 100, 50])


def test_get_ladder_bands_raises_for_unknown_name():
    with pytest.raises(UnknownLadderError):
        get_ladder_bands("P7719")
