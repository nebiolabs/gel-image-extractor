import numpy as np

from gel_extractor.core.curve_lanes import (
    extract_curved_profile,
    trace_lane_from_anchor,
    trace_lanes,
    trace_lanes_from_detected,
    track_lanes_across_strips,
)
from gel_extractor.core.lanes import Lane, detect_lanes


def _synthetic_smiling_gel(height=300, width=200, curvature=15.0):
    """3 lanes on a synthetic gel; edge lanes drift (smile), center stays put."""
    img = np.zeros((height, width))
    lane_centers_top = [40, 100, 160]
    xs = np.arange(width)
    for row in range(height):
        frac = row / height
        smile = curvature * np.sin(frac * np.pi)
        for i, cx in enumerate(lane_centers_top):
            drift = smile * (1.0 if i != 1 else 0.2)
            x = cx + drift
            img[row] += 5 * np.exp(-0.5 * ((xs - x) / 4) ** 2)
    return img


def test_trace_lanes_follows_curvature_more_than_center_lane():
    img = _synthetic_smiling_gel()

    tracks = trace_lanes(img, num_strips=12)

    assert len(tracks) == 3
    drifts = []
    for track in tracks:
        centroids = [s.centroid for s in track.strips]
        drifts.append(max(centroids) - min(centroids))

    # Edge lanes (tracks 0 and 2) were built with 5x the drift of the
    # center lane (track 1) -- the traced curve should reflect that.
    edge_drifts = [drifts[0], drifts[2]]
    center_drift = drifts[1]
    assert all(d > center_drift * 2 for d in edge_drifts)


def test_trace_lanes_x_at_row_tracks_the_synthetic_curve():
    img = _synthetic_smiling_gel(height=300, width=200, curvature=15.0)
    tracks = trace_lanes(img, num_strips=15)

    # Track 0 starts near x=40 and should curve away from a straight line by
    # roughly `curvature` at mid-height (where sin(frac*pi) peaks).
    track0 = min(tracks, key=lambda t: t.x_at_row(0))
    x_top = track0.x_at_row(2)
    x_mid = track0.x_at_row(150)
    assert x_mid - x_top > 8  # real curvature, not a flat/straight trace


def test_track_lanes_carries_broken_lane_forward_without_split_merge_handling():
    # Middle strip has no signal at all for one lane (e.g. faint/blank
    # region) -- the tracker should carry the lane's last centroid forward
    # marked `broken`, not drop the track or crash.
    from gel_extractor.core.curve_lanes import StripLane

    strips = [
        [StripLane(strip_index=0, row_center=10, x_start=10, x_end=20, centroid=15.0)],
        [],  # nothing detected in this strip
        [StripLane(strip_index=2, row_center=30, x_start=12, x_end=22, centroid=17.0)],
    ]

    tracks = track_lanes_across_strips(strips, image_width=100, row_centers=[10, 20, 30])

    assert len(tracks) == 1
    track = tracks[0]
    assert len(track.strips) == 3
    assert track.strips[1].broken is True
    assert track.strips[1].centroid == 15.0  # carried forward unchanged
    assert track.strips[2].broken is False  # picked back up


def test_track_lanes_does_not_merge_two_close_candidates_into_one_track():
    # Two lanes are far apart in strip 0 but both plausible matches appear
    # in strip 1 (simulating fragmentation) -- explicitly out of scope, but
    # the tracker should still produce a sane result (no crash, no silent
    # loss of all tracks) per the documented greedy/no-merge behavior.
    from gel_extractor.core.curve_lanes import StripLane

    strips = [
        [
            StripLane(strip_index=0, row_center=10, x_start=10, x_end=20, centroid=15.0),
            StripLane(strip_index=0, row_center=10, x_start=80, x_end=90, centroid=85.0),
        ],
        [
            StripLane(strip_index=1, row_center=20, x_start=10, x_end=20, centroid=16.0),
            StripLane(strip_index=1, row_center=20, x_start=80, x_end=90, centroid=84.0),
        ],
    ]

    tracks = track_lanes_across_strips(strips, image_width=200, row_centers=[10, 20])

    assert len(tracks) == 2
    centroids_by_track = sorted(t.strips[-1].centroid for t in tracks)
    assert centroids_by_track == [16.0, 84.0]


def test_trace_lanes_from_detected_preserves_detect_lanes_count():
    # The whole point of the anchored redesign: lane count/identity comes
    # from one whole-image detect_lanes call, so anchored tracing can never
    # produce more or fewer tracks than that -- no split/merge ambiguity to
    # spawn spurious extras (unlike the earlier per-strip-redetection
    # prototype, see module docstring "Findings").
    img = _synthetic_smiling_gel()

    lanes = detect_lanes(img)
    detected_lanes, tracks = trace_lanes_from_detected(img, num_strips=12)

    assert len(tracks) == len(lanes) == len(detected_lanes) == 3


def test_trace_lane_from_anchor_follows_curvature_more_than_center_lane():
    # Same real property the earlier prototype's test checked (edge lanes
    # drift more than the center lane) but now via the anchored entry point,
    # confirming curvature tracking survived the redesign.
    img = _synthetic_smiling_gel()
    lanes = detect_lanes(img)
    assert len(lanes) == 3

    tracks = [trace_lane_from_anchor(img, lane, num_strips=12) for lane in lanes]
    drifts = []
    for track in tracks:
        centroids = [s.centroid for s in track.strips]
        drifts.append(max(centroids) - min(centroids))

    edge_drifts = [drifts[0], drifts[2]]
    center_drift = drifts[1]
    assert all(d > center_drift * 2 for d in edge_drifts)


def test_trace_lane_from_anchor_keeps_centroid_within_window_bounds():
    # Two lanes are far apart, but one has a much brighter signal than the
    # other. The anchor window must stop that brighter signal from pulling
    # a different lane's traced centroid across -- containment is what
    # eliminates the over-segmentation the earlier prototype introduced.
    height, width = 100, 200
    img = np.zeros((height, width))
    lane = Lane(index=0, x_start=40, x_end=60)  # width 20
    img[:, 40:60] = 1.0  # this lane's own (faint) signal
    img[:, 90:110] = 100.0  # a much brighter, unrelated lane far outside the window

    margin_fraction = 0.25
    track = trace_lane_from_anchor(img, lane, num_strips=5, margin_fraction=margin_fraction)

    margin = int(round((lane.x_end - lane.x_start) * margin_fraction))
    for strip in track.strips:
        assert lane.x_start - margin <= strip.centroid <= lane.x_end + margin


def test_extract_curved_profile_matches_straight_sum_on_a_straight_lane():
    # Sanity check: on a perfectly straight (non-curving) lane, the curved
    # extraction should closely match a plain fixed-column sum.
    height, width = 100, 50
    img = np.zeros((height, width))
    img[:, 20:30] = 1.0
    img[40:50, 20:30] = 5.0  # a "band"

    tracks = trace_lanes(img, num_strips=10)
    assert len(tracks) == 1

    curved_profile = extract_curved_profile(img, tracks[0], height=height)
    straight_profile = img[:, 20:30].sum(axis=1)

    np.testing.assert_allclose(curved_profile, straight_profile, atol=1e-6)
