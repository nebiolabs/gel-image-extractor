import numpy as np

from gel_extractor.core.bands import correct_baseline, detect_bands


def test_correct_baseline_removes_slow_drift():
    x = np.arange(300)
    drift = 0.01 * x
    peak = 5.0 * np.exp(-((x - 150) ** 2) / (2 * 4.0**2))
    profile = drift + peak

    corrected = correct_baseline(profile)

    assert corrected[150] > 4.0  # peak preserved
    assert corrected[10] < 0.5  # baseline flattened near start
    assert corrected[290] < 1.5  # baseline flattened near end (allow some edge effect)


def test_detect_bands_finds_two_peaks():
    x = np.arange(200)
    peak1 = 5.0 * np.exp(-((x - 50) ** 2) / (2 * 4.0**2))
    peak2 = 3.0 * np.exp(-((x - 150) ** 2) / (2 * 4.0**2))
    profile = peak1 + peak2

    bands = detect_bands(profile)

    assert len(bands) == 2
    centers = sorted(b.center for b in bands)
    assert abs(centers[0] - 50) < 3
    assert abs(centers[1] - 150) < 3
    assert all(b.area > 0 for b in bands)


def test_detect_bands_empty_profile_returns_no_bands():
    profile = np.zeros(100)
    assert detect_bands(profile) == []


def test_detect_bands_taller_peak_has_larger_area():
    x = np.arange(200)
    small = 2.0 * np.exp(-((x - 50) ** 2) / (2 * 4.0**2))
    large = 6.0 * np.exp(-((x - 150) ** 2) / (2 * 4.0**2))
    profile = small + large

    bands = sorted(detect_bands(profile), key=lambda b: b.center)

    assert bands[1].area > bands[0].area
