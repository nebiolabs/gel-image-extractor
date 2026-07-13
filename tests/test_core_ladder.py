import numpy as np
import pytest

from gel_extractor.core.bands import correct_baseline, detect_bands
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
    # bands never resolved into detectable peaks, but the bottom 4 did. The
    # bottom-4 window should win the search since it's the true generating
    # alignment (near-perfect fit), not because of any fixed "assume top is
    # missing" rule -- see test_calibrate_ladder_finds_best_fitting_window
    # for a case where the correct window is neither the top nor the bottom.
    known_mws = [250, 180, 100, 50, 25, 10]
    slope, intercept = -0.01, 3.0
    detected_mws = [100, 50, 25, 10]  # the best-resolved, lowest-MW subset
    positions = [(np.log10(mw) - intercept) / slope for mw in detected_mws]
    profile = _synthetic_ladder_profile(positions, height=int(max(positions)) + 50)

    calibration = calibrate_ladder(profile, known_mws)

    assert len(calibration.positions) == 4
    for pos, mw in zip(positions, detected_mws):
        assert abs(calibration.mw_at(pos) - mw) / mw < 0.05


def test_calibrate_ladder_finds_best_fitting_window_not_just_top_or_bottom():
    # The detected bands truly correspond to a *middle* slice of the known
    # ladder (neither the highest-MW nor the lowest-MW subset) -- verifies
    # the search isn't hardcoded to assume missing bands are always at one
    # end (real image testing 2026-07-13 found a case where the opposite of
    # our original "assume top" rule fit meaningfully better).
    known_mws = [250, 180, 130, 95, 72, 55, 43, 34, 26, 17, 10]
    slope, intercept = -0.01, 3.0
    true_window = [95, 72, 55, 43]  # a middle slice, not top or bottom
    positions = [(np.log10(mw) - intercept) / slope for mw in true_window]
    profile = _synthetic_ladder_profile(positions, height=int(max(positions)) + 50)

    calibration = calibrate_ladder(profile, known_mws)

    assert list(calibration.mws) == true_window
    assert calibration.r_squared > 0.99


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
    # 4 clearly-separated, individually-detectable bands whose spacing
    # doesn't fit *any* contiguous 4-band window of the known P7719 sizes
    # well (best achievable R^2 across all windows is ~0.72, verified
    # numerically by brute-force search) -- signals no assumed correspondence
    # can be trusted, not just "the wrong window was picked."
    profile = _synthetic_ladder_profile([190.8, 812.7, 855.1, 889.4], height=950)
    with pytest.raises(LadderCalibrationError):
        calibrate_ladder(profile, [250, 180, 130, 95, 72, 55, 43, 34, 26, 17, 10])


def test_calibrate_ladder_uses_more_lenient_noise_floor_than_sample_lanes():
    # A faint-but-real ladder lane (prominence ~9x the estimated noise level)
    # -- found 2026-07-13 testing more real images: 3 of 4 images with a
    # visually-confirmed but low-contrast ladder lane failed to calibrate at
    # detect_bands' general default noise floor (10x), but succeeded at a
    # more lenient one (5x). Safe specifically for the ladder lane because
    # calibration has its own downstream guardrails (band count, R²) to
    # reject a bad fit -- confirmed here that the *same* profile would find
    # 0 bands under the stricter, general-purpose default used for sample
    # lanes (which have no such guardrail, so stay strict).
    known_mws = [100, 50, 25, 10]
    slope, intercept = -0.01, 2.5
    positions = [(np.log10(mw) - intercept) / slope for mw in known_mws]
    height = int(max(positions)) + 50
    x = np.arange(height)
    rng = np.random.default_rng(3)
    profile = np.abs(rng.normal(0, 0.08, size=height))
    for pos in positions:
        profile += 0.5 * np.exp(-((x - pos) ** 2) / (2 * 3.0**2))

    calibration = calibrate_ladder(profile, known_mws)
    assert len(calibration.positions) == 4
    for pos, mw in zip(positions, known_mws):
        assert abs(calibration.mw_at(pos) - mw) / mw < 0.1

    # Same profile, general-purpose (sample-lane) default: no bands survive.
    assert detect_bands(correct_baseline(profile)) == []


def test_get_ladder_bands_raises_for_unknown_name():
    with pytest.raises(UnknownLadderError):
        get_ladder_bands("not-a-real-ladder")


def test_get_ladder_bands_returns_p7719():
    bands = get_ladder_bands("P7719")
    assert bands == [250.0, 180.0, 130.0, 95.0, 72.0, 55.0, 43.0, 34.0, 26.0, 17.0, 10.0]
    assert bands == sorted(bands, reverse=True)
