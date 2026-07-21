from gel_extractor.core.band_propagation import (
    absolute_row,
    find_nearest_band,
    propagate_target_band,
    row_tolerance,
)
from gel_extractor.core.bands import Band


def _band(center: float, area: float = 100.0) -> Band:
    return Band(start=int(center - 5), end=int(center + 5), center=center, area=area)


def test_absolute_row_adds_top_bound_to_band_center():
    band = _band(center=42.0)

    assert absolute_row(top_bound=100, band=band) == 142.0


def test_row_tolerance_scales_with_resolving_height():
    assert row_tolerance(resolving_height=200, fraction=0.05) == 10.0
    assert row_tolerance(resolving_height=1000, fraction=0.05) == 50.0


def test_find_nearest_band_returns_closest_within_tolerance():
    bands = [_band(center=50.0), _band(center=200.0)]

    result = find_nearest_band(bands, top_bound=0, target_absolute_row=55.0, tolerance=20.0)

    assert result is bands[0]


def test_find_nearest_band_none_when_nothing_within_tolerance():
    bands = [_band(center=50.0)]

    result = find_nearest_band(bands, top_bound=0, target_absolute_row=500.0, tolerance=20.0)

    assert result is None


def test_find_nearest_band_none_when_two_candidates_are_ambiguous():
    # Two bands nearly equidistant from the target row -- a confidently
    # wrong pick here (real contaminant vs. true target) is worse than an
    # honest miss.
    bands = [_band(center=48.0), _band(center=52.0)]

    result = find_nearest_band(bands, top_bound=0, target_absolute_row=50.0, tolerance=20.0)

    assert result is None


def test_find_nearest_band_not_ambiguous_when_second_candidate_much_farther():
    bands = [_band(center=51.0), _band(center=65.0)]

    result = find_nearest_band(bands, top_bound=0, target_absolute_row=50.0, tolerance=20.0)

    assert result is bands[0]


def test_find_nearest_band_none_on_empty_bands():
    assert find_nearest_band([], top_bound=0, target_absolute_row=50.0, tolerance=20.0) is None


def test_find_nearest_band_accounts_for_lane_top_bound_offset():
    # Same absolute target row, but this lane's own crop starts later --
    # a band at profile-relative center=10 in a lane with top_bound=100 is
    # at the same absolute row as center=50 in a lane with top_bound=60.
    bands = [_band(center=10.0)]

    result = find_nearest_band(bands, top_bound=100, target_absolute_row=110.0, tolerance=5.0)

    assert result is bands[0]


def test_propagate_target_band_matches_across_lanes_with_different_top_bounds():
    lanes = {
        1: (100, [_band(center=50.0)]),  # absolute row 150 -- the reference
        2: (110, [_band(center=42.0)]),  # absolute row 152 -- close match
        3: (90, [_band(center=200.0)]),  # absolute row 290 -- no match
    }

    result = propagate_target_band(
        reference_absolute_row=150.0, lanes=lanes, series_lanes=[2, 3], tolerance=10.0
    )

    assert result[2] is lanes[2][1][0]
    assert result[3] is None


def test_propagate_target_band_ignores_lanes_outside_series():
    lanes = {
        1: (0, [_band(center=50.0)]),
        2: (0, [_band(center=999.0)]),  # not in series_lanes -- must be ignored
    }

    result = propagate_target_band(reference_absolute_row=50.0, lanes=lanes, series_lanes=[1], tolerance=10.0)

    assert list(result.keys()) == [1]
