from dataclasses import replace

import numpy as np
from skimage.io import imsave

from gel_extractor.purity.analysis import Centerline, analyze_image
from gel_extractor.purity.debug_viz import MATURITY_BANNER_COLOR, render_debug_image


def _write_synthetic_gel_with_contaminant(tmp_path, synthetic_gel):
    height = 300
    top_margin = int(height * 0.05)
    slope, intercept = -0.01, 2.3
    known_mws = [100.0, 50.0, 25.0, 12.5, 6.25]

    def post_crop_pos(mw):
        return (intercept - np.log10(mw)) / -slope

    ladder_bands = [(post_crop_pos(mw) + top_margin, 0.7) for mw in known_mws]
    target_pos = post_crop_pos(25.0) + top_margin
    contaminant_pos = 280

    image = synthetic_gel(
        height=height,
        band_specs=[ladder_bands, [(target_pos, 0.6), (contaminant_pos, 0.3)]],
    )
    path = tmp_path / "gel.png"
    imsave(str(path), (image * 255).astype("uint8"))
    return path, image


def test_render_debug_image_matches_input_dimensions(tmp_path, synthetic_gel):
    path, raw_image = _write_synthetic_gel_with_contaminant(tmp_path, synthetic_gel)
    results, ladder_lane_index, debug_info = analyze_image(
        str(path), target_mw=25.0, ladder_bands=[100.0, 50.0, 25.0, 12.5, 6.25], tolerance_percent=17.5
    )

    canvas = render_debug_image(raw_image, results, debug_info)

    assert canvas.size == (raw_image.shape[1], raw_image.shape[0])
    assert canvas.mode == "RGB"


def test_render_debug_image_draws_distinct_colors_for_target_and_contaminant(tmp_path, synthetic_gel):
    path, raw_image = _write_synthetic_gel_with_contaminant(tmp_path, synthetic_gel)
    results, ladder_lane_index, debug_info = analyze_image(
        str(path), target_mw=25.0, ladder_bands=[100.0, 50.0, 25.0, 12.5, 6.25], tolerance_percent=17.5
    )

    canvas = render_debug_image(raw_image, results, debug_info)
    pixels = np.array(canvas)

    sample_lane_debug = next(lane for lane in debug_info.lanes if not lane.is_ladder)
    target_band = sample_lane_debug.target_bands[0]
    other_band = next(b for b in sample_lane_debug.bands if b not in sample_lane_debug.target_bands)

    top_offset = sample_lane_debug.top_bound
    mid_x = (sample_lane_debug.x_start + sample_lane_debug.x_end) // 2

    target_row_pixel = tuple(pixels[top_offset + target_band.start, mid_x])
    other_row_pixel = tuple(pixels[top_offset + other_band.start, mid_x])

    assert target_row_pixel != other_row_pixel


def test_render_debug_image_handles_not_found_lane(tmp_path, synthetic_gel):
    height = 300
    top_margin = int(height * 0.05)
    slope, intercept = -0.01, 2.3
    known_mws = [100.0, 50.0, 25.0]

    def post_crop_pos(mw):
        return (intercept - np.log10(mw)) / -slope

    ladder_bands = [(post_crop_pos(mw) + top_margin, 0.7) for mw in known_mws]
    off_target_pos = 280  # far from any known MW -> not-found

    image = synthetic_gel(height=height, band_specs=[ladder_bands, [(off_target_pos, 0.6)]])
    path = tmp_path / "gel.png"
    imsave(str(path), (image * 255).astype("uint8"))

    results, ladder_lane_index, debug_info = analyze_image(
        str(path), target_mw=25.0, ladder_bands=known_mws, tolerance_percent=17.5
    )

    assert results[0].confidence == "not-found"
    # Should render without error even when nothing matched.
    canvas = render_debug_image(image, results, debug_info)
    assert canvas.size == (image.shape[1], image.shape[0])


def test_render_debug_image_draws_method_banner_colored_by_maturity(tmp_path, synthetic_gel):
    path, raw_image = _write_synthetic_gel_with_contaminant(tmp_path, synthetic_gel)
    results, ladder_lane_index, debug_info = analyze_image(
        str(path), target_mw=25.0, ladder_bands=[100.0, 50.0, 25.0, 12.5, 6.25], tolerance_percent=17.5
    )

    canvas = render_debug_image(raw_image, results, debug_info, method="viterbi", maturity="promising")
    pixels = np.array(canvas)

    # Banner spans the top 17px (see _draw_method_banner) filled with that
    # maturity's registered color -- check a pixel well inside the band,
    # away from the text glyphs drawn on top of it.
    assert tuple(pixels[8, canvas.width - 5]) == MATURITY_BANNER_COLOR["promising"]


def test_render_debug_image_without_method_draws_no_banner(tmp_path, synthetic_gel):
    path, raw_image = _write_synthetic_gel_with_contaminant(tmp_path, synthetic_gel)
    results, ladder_lane_index, debug_info = analyze_image(
        str(path), target_mw=25.0, ladder_bands=[100.0, 50.0, 25.0, 12.5, 6.25], tolerance_percent=17.5
    )

    canvas = render_debug_image(raw_image, results, debug_info)
    pixels = np.array(canvas)

    # No method given (today's plain rectangle call pattern, unchanged) ->
    # no banner fill; the top-right corner pixel stays whatever the
    # underlying image/lane-box rendering produced, never a maturity color.
    assert tuple(pixels[8, canvas.width - 5]) not in MATURITY_BANNER_COLOR.values()


def test_render_debug_image_draws_centerline_without_error(tmp_path, synthetic_gel):
    path, raw_image = _write_synthetic_gel_with_contaminant(tmp_path, synthetic_gel)
    results, ladder_lane_index, debug_info = analyze_image(
        str(path), target_mw=25.0, ladder_bands=[100.0, 50.0, 25.0, 12.5, 6.25], tolerance_percent=17.5
    )

    sample_lane_debug = next(lane for lane in debug_info.lanes if not lane.is_ladder)
    rows = np.arange(sample_lane_debug.top_bound, sample_lane_debug.bottom_bound)
    mid_x = (sample_lane_debug.x_start + sample_lane_debug.x_end) / 2.0
    centerline = Centerline(rows=rows, xs=np.full(rows.shape, mid_x))
    lanes_with_curve = [
        replace(lane, centerline=centerline) if lane is sample_lane_debug else lane for lane in debug_info.lanes
    ]
    debug_info_with_curve = replace(debug_info, lanes=lanes_with_curve)

    canvas = render_debug_image(raw_image, results, debug_info_with_curve)

    assert canvas.size == (raw_image.shape[1], raw_image.shape[0])


def test_render_debug_image_draws_annotation_without_error(tmp_path, synthetic_gel):
    path, raw_image = _write_synthetic_gel_with_contaminant(tmp_path, synthetic_gel)
    results, ladder_lane_index, debug_info = analyze_image(
        str(path), target_mw=25.0, ladder_bands=[100.0, 50.0, 25.0, 12.5, 6.25], tolerance_percent=17.5
    )

    sample_lane_debug = next(lane for lane in debug_info.lanes if not lane.is_ladder)
    lanes_with_annotation = [
        replace(lane, annotation="shifted +3px") if lane is sample_lane_debug else lane for lane in debug_info.lanes
    ]
    debug_info_with_annotation = replace(debug_info, lanes=lanes_with_annotation)

    canvas = render_debug_image(raw_image, results, debug_info_with_annotation)

    assert canvas.size == (raw_image.shape[1], raw_image.shape[0])
