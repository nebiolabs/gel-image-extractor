from gel_extractor.core.band_propagation import (
    absolute_row,
    exclude_deleted_bands,
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


def test_find_nearest_band_snaps_to_nearest_when_ambiguity_check_disabled():
    # Same setup as the ambiguous case above, but require_unambiguous=False
    # (the direct-human-click path) should snap to the nearer one instead of
    # refusing.
    nearer = _band(center=52.0)
    bands = [_band(center=48.0), nearer]

    result = find_nearest_band(
        bands, top_bound=0, target_absolute_row=53.0, tolerance=20.0, require_unambiguous=False
    )

    assert result is nearer


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


def test_propagate_target_band_snaps_to_nearest_even_when_ambiguous():
    # Series-lane propagation now matches the reference click's behavior:
    # two close candidates in a lane no longer hold the whole lane back --
    # the nearer one wins, same as require_unambiguous=False directly.
    nearer = _band(center=52.0)
    lanes = {2: (0, [_band(center=48.0), nearer])}

    result = propagate_target_band(reference_absolute_row=53.0, lanes=lanes, series_lanes=[2], tolerance=20.0)

    assert result[2] is nearer


def test_propagate_target_band_ignores_lanes_outside_series():
    lanes = {
        1: (0, [_band(center=50.0)]),
        2: (0, [_band(center=999.0)]),  # not in series_lanes -- must be ignored
    }

    result = propagate_target_band(reference_absolute_row=50.0, lanes=lanes, series_lanes=[1], tolerance=10.0)

    assert list(result.keys()) == [1]


def test_exclude_deleted_bands_is_a_no_op_when_nothing_deleted():
    bands = [_band(center=50.0), _band(center=200.0)]

    result = exclude_deleted_bands(bands, top_bound=100, deleted_ranges=set())

    assert result == bands


def test_exclude_deleted_bands_removes_only_the_matching_range():
    target = _band(center=50.0)
    contaminant = _band(center=200.0)
    bands = [target, contaminant]
    deleted_ranges = {(100 + contaminant.start, 100 + contaminant.end)}

    result = exclude_deleted_bands(bands, top_bound=100, deleted_ranges=deleted_ranges)

    assert result == [target]


def test_exclude_deleted_bands_can_remove_the_matched_band_itself():
    # Deleting the currently-matched target band, not just a contaminant --
    # the HITL server relies on this to fall back to "unmatched" rather than
    # silently promoting a different candidate.
    target = _band(center=50.0)
    deleted_ranges = {(100 + target.start, 100 + target.end)}

    result = exclude_deleted_bands([target], top_bound=100, deleted_ranges=deleted_ranges)

    assert result == []
