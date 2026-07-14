"""Debug visualization: render detected lanes/bands onto a copy of the input image.

See AGENTS.md "Implementation Status" (the `--debug` flag) for why this
exists -- built to replace ad hoc manual cropping/viewing during
lane-detection debugging with a systematic, reusable output.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from gel_extractor.purity.analysis import AnalysisDebugInfo, LaneResult

LADDER_COLOR = (60, 120, 255)
SAMPLE_LANE_COLOR = (255, 190, 0)
TARGET_BAND_COLOR = (40, 200, 40)
OTHER_BAND_COLOR = (230, 50, 50)
CROP_BOUND_COLOR = (180, 0, 220)
LABEL_TEXT_COLOR = (255, 255, 255)
LABEL_BG_COLOR = (0, 0, 0)
CURVE_TRACE_COLOR = (255, 140, 0)  # this branch only -- see core.curve_lanes; kept distinct from red/green band colors


def render_debug_image(
    image: np.ndarray,
    results: list[LaneResult],
    debug_info: AnalysisDebugInfo,
) -> Image.Image:
    """Draw lane and band boxes on a copy of the raw input image.

    `image` is the raw grayscale array from `core.image_io.load_image`
    (*not* the polarity-normalized `to_signal` array) -- rendering the raw
    image keeps the output looking like a normal gel photo (dark bands on a
    light background) rather than `to_signal`'s inverted convention. Band/
    lane positions are the same in either array since inversion doesn't
    shift positions, only which values count as "signal."

    Lane boxes span the image's full height. A magenta line marks each
    lane's `top_bound` (where its own comb/well fringe was adaptively
    detected to end -- this varies lane to lane) and the shared
    `bottom_bound` (where the bottom cassette/tape-edge artifact was
    detected to begin -- the same for every lane, see `core.lanes`). Band
    boxes are offset by that lane's own `top_bound`, since a `Band`'s
    `start`/`end` are indices into the post-crop profile, not the full
    image.

    Color key: blue = ladder lane, amber = sample lane, magenta = adaptive
    crop boundary, green = band counted as the target/matched signal, red =
    other/contaminant band, **orange = this lane's traced curve** (this
    branch only -- see `core.curve_lanes`; the straight amber/blue box is
    still drawn alongside it for comparison, since band intensity was
    actually summed along the curve, not the straight box). A sample lane
    with no matched band ("not-found") shows all its bands in red.
    """
    normalized = image.astype(np.float64) - image.min()
    peak = normalized.max()
    if peak > 0:
        normalized = normalized / peak
    canvas = Image.fromarray((normalized * 255).astype("uint8")).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    height = image.shape[0]
    results_by_lane = {r.lane: r for r in results}

    for lane_info in debug_info.lanes:
        lane_color = LADDER_COLOR if lane_info.is_ladder else SAMPLE_LANE_COLOR
        x1 = max(lane_info.x_end - 1, lane_info.x_start)
        draw.rectangle([lane_info.x_start, 0, x1, height - 1], outline=lane_color, width=2)
        draw.line([(lane_info.x_start, lane_info.top_bound), (x1, lane_info.top_bound)], fill=CROP_BOUND_COLOR, width=1)
        draw.line(
            [(lane_info.x_start, lane_info.bottom_bound), (x1, lane_info.bottom_bound)],
            fill=CROP_BOUND_COLOR,
            width=1,
        )
        _draw_traced_curve(draw, lane_info.track, lane_info.top_bound, lane_info.bottom_bound)

        target_band_ids = {id(b) for b in lane_info.target_bands}
        for band in lane_info.bands:
            band_color = TARGET_BAND_COLOR if id(band) in target_band_ids else OTHER_BAND_COLOR
            y0 = lane_info.top_bound + band.start
            y1 = lane_info.top_bound + band.end
            draw.rectangle([lane_info.x_start, y0, x1, y1], outline=band_color, width=2)

        label = _lane_label(lane_info, results_by_lane)
        if label:
            text_y = max(0, lane_info.top_bound - 14)
            draw.rectangle([lane_info.x_start, text_y, lane_info.x_end - 1, text_y + 12], fill=LABEL_BG_COLOR)
            draw.text((lane_info.x_start + 2, text_y), label, fill=LABEL_TEXT_COLOR)

    _draw_ladder_calibration(draw, debug_info)

    return canvas


def _draw_traced_curve(draw: ImageDraw.ImageDraw, track, top_bound: int, bottom_bound: int) -> None:
    """Draw one lane's traced curve (this branch only, see `core.curve_lanes`).

    Sampled every few rows rather than every row -- plenty smooth for a
    visual check, and much cheaper than one `line()` call per row.
    """
    step = max(1, (bottom_bound - top_bound) // 100)
    points = [(track.x_at_row(row), row) for row in range(top_bound, bottom_bound, step)]
    if len(points) >= 2:
        draw.line(points, fill=CURVE_TRACE_COLOR, width=2)


def _lane_label(lane_info, results_by_lane: dict[int, LaneResult]) -> str:
    if lane_info.is_ladder:
        return "ladder"
    result = results_by_lane.get(lane_info.lane)
    if result is None:
        return ""
    if result.confidence == "not-found":
        return f"L{lane_info.lane}: not-found"
    purity = f"{result.purity_percent}%" if result.purity_percent is not None else "n/a"
    mw = f"{result.matched_band_mw:.1f}kDa" if result.matched_band_mw is not None else "n/a"
    flag = " low-sig" if result.low_signal else ""
    return f"L{lane_info.lane}: {purity} ({mw}){flag}"


def _draw_ladder_calibration(draw: ImageDraw.ImageDraw, debug_info: AnalysisDebugInfo) -> None:
    """Annotate the ladder lane with its calibrated MW at each fitted band position."""
    calibration = debug_info.ladder_calibration
    if calibration is None:
        return
    ladder_lane = next((lane for lane in debug_info.lanes if lane.is_ladder), None)
    if ladder_lane is None:
        return
    for position, mw in zip(calibration.positions, calibration.mws):
        y = ladder_lane.top_bound + int(position)
        draw.line([(ladder_lane.x_start, y), (ladder_lane.x_end - 1, y)], fill=LADDER_COLOR, width=1)
        draw.text((ladder_lane.x_end + 4, y - 6), f"{mw:.0f}", fill=(0, 0, 0))


def save_debug_image(
    image: np.ndarray,
    results: list[LaneResult],
    debug_info: AnalysisDebugInfo,
    path: str | Path,
) -> None:
    render_debug_image(image, results, debug_info).save(str(path))
