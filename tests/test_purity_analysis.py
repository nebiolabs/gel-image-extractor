import numpy as np
import pytest
from skimage.io import imsave

from gel_extractor.core.ladder import LadderCalibration
from gel_extractor.purity.analysis import LadderNotCalibratedError, analyze_image, analyze_lane


def _flat_calibration() -> LadderCalibration:
    return LadderCalibration(
        positions=np.array([0.0, 100.0]), mws=np.array([100.0, 10.0]), slope=-0.01, intercept=2.0, r_squared=1.0
    )


def _profile_with_bands(height: int, bands: list[tuple[float, float]]) -> np.ndarray:
    x = np.arange(height)
    profile = np.zeros(height)
    for center, amplitude in bands:
        profile += amplitude * np.exp(-((x - center) ** 2) / (2 * 3.0**2))
    return profile


def _position_for_mw(calibration: LadderCalibration, mw: float) -> float:
    return (calibration.intercept - np.log10(mw)) / -calibration.slope


def test_analyze_lane_mw_matched():
    calibration = _flat_calibration()
    target_mw = 50.0
    target_pos = _position_for_mw(calibration, target_mw)
    profile = _profile_with_bands(300, [(target_pos, 5.0), (250, 2.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=target_mw, calibration=calibration, tolerance_percent=17.5)

    assert result.confidence == "mw-matched"
    assert result.purity_percent is not None
    assert 0 < result.purity_percent < 100
    assert result.matched_band_mw is not None


def test_analyze_lane_not_found_without_heuristic():
    calibration = _flat_calibration()
    profile = _profile_with_bands(300, [(250, 5.0)])  # no band near target mw

    result = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=calibration, allow_heuristic=False)

    assert result.confidence == "not-found"
    assert result.purity_percent is None


def test_analyze_lane_heuristic_fallback():
    profile = _profile_with_bands(300, [(50, 5.0), (150, 2.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=None, allow_heuristic=True)

    assert result.confidence == "heuristic"
    assert result.purity_percent is not None


def test_analyze_lane_no_calibration_without_heuristic_is_not_found():
    profile = _profile_with_bands(300, [(50, 5.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=None, allow_heuristic=False)

    assert result.confidence == "not-found"
    assert result.purity_percent is None


def test_analyze_lane_sums_doublet_within_tolerance():
    calibration = _flat_calibration()
    target_mw = 50.0
    target_pos = _position_for_mw(calibration, target_mw)
    # Two close bands both within tolerance of target_mw (a doublet), plus
    # an out-of-tolerance contaminant.
    profile = _profile_with_bands(300, [(target_pos - 5, 4.0), (target_pos + 5, 4.0), (250, 2.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=target_mw, calibration=calibration, tolerance_percent=17.5)

    assert result.confidence == "mw-matched"
    assert result.purity_percent > 50  # both doublet bands counted as target


def test_analyze_image_raises_without_ladder_info(tmp_path, synthetic_gel):
    image = synthetic_gel(band_specs=[[(50, 0.8)], [(100, 0.5)]])
    path = tmp_path / "gel.png"
    imsave(str(path), (image * 255).astype("uint8"))

    with pytest.raises(LadderNotCalibratedError):
        analyze_image(str(path), target_mw=50.0)


def test_analyze_image_end_to_end_with_ladder_bands(tmp_path, synthetic_gel):
    height = 300
    top_margin = int(height * 0.05)  # matches Lane.crop's default
    slope, intercept = -0.01, 2.3
    known_mws = [100.0, 50.0, 25.0, 12.5, 6.25]

    def post_crop_pos(mw: float) -> float:
        return (intercept - np.log10(mw)) / -slope

    ladder_bands = [(post_crop_pos(mw) + top_margin, 0.7) for mw in known_mws]

    target_mw = 25.0
    target_pos = post_crop_pos(target_mw) + top_margin
    contaminant_pos = 280  # far outside tolerance for any of the known MWs

    image = synthetic_gel(
        height=height,
        band_specs=[ladder_bands, [(target_pos, 0.6), (contaminant_pos, 0.3)]],
    )
    path = tmp_path / "gel.png"
    imsave(str(path), (image * 255).astype("uint8"))

    results, ladder_lane_index, debug_info = analyze_image(
        str(path),
        target_mw=target_mw,
        ladder_bands=known_mws,
        tolerance_percent=17.5,
    )

    assert ladder_lane_index == 0
    assert len(results) == 1
    result = results[0]
    assert result.confidence == "mw-matched"
    assert result.purity_percent is not None
    # target band (0.6 darkness) should dominate over the fainter contaminant (0.3)
    assert result.purity_percent > 50

    # debug_info should carry the raw detections behind the above result.
    assert len(debug_info.lanes) == 2
    ladder_debug, sample_debug = debug_info.lanes
    assert ladder_debug.is_ladder is True
    assert len(ladder_debug.bands) == len(known_mws)
    assert debug_info.ladder_calibration is not None
    assert sample_debug.is_ladder is False
    assert len(sample_debug.bands) == 2  # target + contaminant
    assert len(sample_debug.target_bands) == 1  # only the target band matched
