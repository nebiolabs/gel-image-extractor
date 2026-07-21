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
    # "mw-strict" specifically: under the default "largest" band_selection,
    # a calibrated-but-out-of-tolerance band reports "mw-mismatch" (with a
    # real purity_percent), not "not-found" -- see
    # test_analyze_lane_largest_mode_flags_mismatch below for that case.
    calibration = _flat_calibration()
    profile = _profile_with_bands(300, [(250, 5.0)])  # no band near target mw

    result = analyze_lane(
        profile, lane_index=1, target_mw=50.0, calibration=calibration, allow_heuristic=False, band_selection="mw-strict"
    )

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


def test_analyze_lane_no_bands_detected_is_not_found_even_with_heuristic():
    # A lane with literally nothing detectable (e.g. a blank/degenerate
    # sample, or a spurious lane detection) must report "not-found", not a
    # fabricated 0% -- see AGENTS.md Implementation Status, 2026-07-14.
    profile = np.zeros(300)

    result = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=None, allow_heuristic=True)

    assert result.confidence == "not-found"
    assert result.purity_percent is None


def test_analyze_lane_sums_doublet_within_tolerance():
    # Doublet-summing (multiple bands within tolerance counted together) is
    # "mw-strict"-only behavior -- the default "largest" mode selects a
    # single band regardless of MW, by design (2026-07-17 decision, see
    # AGENTS.md), so this doublet's two near-equal bands would NOT both
    # count without band_selection="mw-strict" here.
    calibration = _flat_calibration()
    target_mw = 50.0
    target_pos = _position_for_mw(calibration, target_mw)
    # Two close bands both within tolerance of target_mw (a doublet), plus
    # an out-of-tolerance contaminant.
    profile = _profile_with_bands(300, [(target_pos - 5, 4.0), (target_pos + 5, 4.0), (250, 2.0)])

    result = analyze_lane(
        profile, lane_index=1, target_mw=target_mw, calibration=calibration, tolerance_percent=17.5,
        band_selection="mw-strict",
    )

    assert result.confidence == "mw-matched"
    assert result.purity_percent > 50  # both doublet bands counted as target


def test_analyze_lane_largest_mode_flags_mismatch():
    # Same scenario as test_analyze_lane_not_found_without_heuristic (a
    # calibrated ladder, but the lane's only band sits far from target_mw)
    # -- under the default "largest" band_selection, this is a real result
    # (largest band selected regardless of MW) flagged as a mismatch, not a
    # refusal.
    calibration = _flat_calibration()
    profile = _profile_with_bands(300, [(250, 5.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=calibration, allow_heuristic=False)

    assert result.confidence == "mw-mismatch"
    assert result.purity_percent is not None
    assert result.matched_band_mw is not None
    assert result.target_mw_expected == 50.0


def test_analyze_lane_largest_mode_matches_when_biggest_band_is_correct():
    # When the biggest band IS also the MW-correct one, "largest" and
    # "mw-strict" must agree exactly -- a regression-safety check that the
    # redesign didn't change behavior on the case both modes were always
    # meant to handle the same way.
    calibration = _flat_calibration()
    target_mw = 50.0
    target_pos = _position_for_mw(calibration, target_mw)
    profile = _profile_with_bands(300, [(target_pos, 5.0), (250, 2.0)])

    largest = analyze_lane(profile, lane_index=1, target_mw=target_mw, calibration=calibration, tolerance_percent=17.5)
    mw_strict = analyze_lane(
        profile, lane_index=1, target_mw=target_mw, calibration=calibration, tolerance_percent=17.5,
        band_selection="mw-strict",
    )

    assert largest.confidence == mw_strict.confidence == "mw-matched"
    assert largest.purity_percent == mw_strict.purity_percent
    assert largest.matched_band_mw == mw_strict.matched_band_mw


def test_analyze_lane_largest_mode_no_calibration_without_heuristic_is_not_found():
    profile = _profile_with_bands(300, [(50, 5.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=None, allow_heuristic=False)

    assert result.confidence == "not-found"
    assert result.purity_percent is None


def test_analyze_lane_largest_mode_no_calibration_with_heuristic_is_heuristic():
    profile = _profile_with_bands(300, [(50, 5.0), (150, 2.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=None, allow_heuristic=True)

    assert result.confidence == "heuristic"
    assert result.purity_percent is not None
    assert result.matched_band_mw is None


def test_analyze_lane_largest_mode_mismatch_ignores_allow_heuristic():
    # The one genuinely new interaction: once calibration succeeds, a
    # mismatch is reported identically regardless of --allow-heuristic,
    # since real (if disagreeing) information exists -- unlike the
    # zero-calibration case above, which IS gated by it.
    calibration = _flat_calibration()
    profile = _profile_with_bands(300, [(250, 5.0)])

    not_allowed = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=calibration, allow_heuristic=False)
    allowed = analyze_lane(profile, lane_index=1, target_mw=50.0, calibration=calibration, allow_heuristic=True)

    assert not_allowed.confidence == allowed.confidence == "mw-mismatch"
    assert not_allowed.purity_percent == allowed.purity_percent


def test_analyze_lane_largest_mode_no_target_mw_with_calibration_is_unverified():
    # 2026-07-20: batches spanning many proteins with no per-image expected
    # MW available -- the largest band is still selected and its real
    # calibrated MW still reported, just flagged as unverified rather than
    # compared against a target that doesn't exist.
    calibration = _flat_calibration()
    profile = _profile_with_bands(300, [(50, 5.0), (150, 2.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=None, calibration=calibration)

    assert result.confidence == "largest-unverified"
    assert result.purity_percent is not None
    assert result.matched_band_mw is not None
    assert result.target_mw_expected is None


def test_analyze_lane_largest_mode_no_target_mw_no_calibration_is_heuristic():
    # No target_mw and no calibration is the same "nothing to verify against
    # at all" case the heuristic fallback already handles -- target_mw being
    # absent doesn't change that path.
    profile = _profile_with_bands(300, [(50, 5.0), (150, 2.0)])

    result = analyze_lane(profile, lane_index=1, target_mw=None, calibration=None, allow_heuristic=True)

    assert result.confidence == "heuristic"
    assert result.purity_percent is not None


def test_analyze_lane_mw_strict_requires_target_mw():
    # "mw-strict" needs target_mw to select a band at all -- None must fail
    # loudly, not silently produce a nonsense result.
    calibration = _flat_calibration()
    profile = _profile_with_bands(300, [(50, 5.0)])

    with pytest.raises(ValueError, match="target_mw is required"):
        analyze_lane(profile, lane_index=1, target_mw=None, calibration=calibration, band_selection="mw-strict")


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


def test_analyze_image_flags_faint_lane_as_low_signal(tmp_path, synthetic_gel):
    # A dilution series: one well-loaded lane, one much fainter one (as if
    # highly diluted). The faint lane's purity reading may be inflated by
    # the dilution-detectability limit (see AGENTS.md Known Limitations) --
    # it should come back flagged low_signal, the strong lane should not.
    height = 300
    top_margin = int(height * 0.05)
    slope, intercept = -0.01, 2.3
    known_mws = [100.0, 50.0, 25.0, 12.5, 6.25]

    def post_crop_pos(mw: float) -> float:
        return (intercept - np.log10(mw)) / -slope

    ladder_bands = [(post_crop_pos(mw) + top_margin, 0.7) for mw in known_mws]
    target_mw = 25.0
    target_pos = post_crop_pos(target_mw) + top_margin

    strong_lane = [(target_pos, 0.6), (280, 0.3)]
    faint_lane = [(target_pos, 0.12)]

    image = synthetic_gel(height=height, band_specs=[ladder_bands, strong_lane, faint_lane])
    path = tmp_path / "gel.png"
    imsave(str(path), (image * 255).astype("uint8"))

    results, _ladder_lane_index, _debug_info = analyze_image(
        str(path), target_mw=target_mw, ladder_bands=known_mws, tolerance_percent=17.5
    )

    assert len(results) == 2
    strong_result, faint_result = results
    assert strong_result.low_signal is False
    assert faint_result.low_signal is True
