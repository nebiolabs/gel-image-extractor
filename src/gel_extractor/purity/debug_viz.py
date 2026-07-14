"""Debug visualization: render detected lanes/bands onto a copy of the input image.

See AGENTS.md "Implementation Status" (the `--debug` flag) for why this
exists -- built to replace ad hoc manual cropping/viewing during
lane-detection debugging with a systematic, reusable output.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from gel_extractor.core.lanes import DEFAULT_TOP_MARGIN_FRACTION
from gel_extractor.purity.analysis import AnalysisDebugInfo, LaneResult

LADDER_COLOR = (60, 120, 255)
SAMPLE_LANE_COLOR = (255, 190, 0)
TARGET_BAND_COLOR = (40, 200, 40)
OTHER_BAND_COLOR = (230, 50, 50)
LABEL_TEXT_COLOR = (255, 255, 255)
LABEL_BG_COLOR = (0, 0, 0)


def render_debug_image(
    image: np.ndarray,
    results: list[LaneResult],
    debug_info: AnalysisDebugInfo,
    top_margin_fraction: float = DEFAULT_TOP_MARGIN_FRACTION,
) -> Image.Image:
    """Draw lane and band boxes on a copy of the raw input image.

    `image` is the raw grayscale array from `core.image_io.load_image`
    (*not* the polarity-normalized `to_signal` array) -- rendering the raw
    image keeps the output looking like a normal gel photo (dark bands on a
    light background) rather than `to_signal`'s inverted convention. Band/
    lane positions are the same in either array since inversion doesn't
    shift positions, only which values count as "signal."

    Lane boxes span the image's full height. Band boxes are offset down by
    `top_margin_fraction` (must match whatever `Lane.crop` used), since a
    `Band`'s `start`/`end` are indices into the post-crop profile, not the
    full image.

    Color key: blue = ladder lane, amber = sample lane, green = band counted
    as the target/matched signal, red = other/contaminant band. A sample
    lane with no matched band ("not-found") shows all its bands in red.
    """
    normalized = image.astype(np.float64) - image.min()
    peak = normalized.max()
    if peak > 0:
        normalized = normalized / peak
    canvas = Image.fromarray((normalized * 255).astype("uint8")).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    height = image.shape[0]
    top_offset = int(height * top_margin_fraction)
    results_by_lane = {r.lane: r for r in results}

    for lane_info in debug_info.lanes:
        lane_color = LADDER_COLOR if lane_info.is_ladder else SAMPLE_LANE_COLOR
        draw.rectangle(
            [lane_info.x_start, 0, max(lane_info.x_end - 1, lane_info.x_start), height - 1],
            outline=lane_color,
            width=2,
        )

        target_band_ids = {id(b) for b in lane_info.target_bands}
        for band in lane_info.bands:
            band_color = TARGET_BAND_COLOR if id(band) in target_band_ids else OTHER_BAND_COLOR
            y0 = top_offset + band.start
            y1 = top_offset + band.end
            draw.rectangle(
                [lane_info.x_start, y0, max(lane_info.x_end - 1, lane_info.x_start), y1],
                outline=band_color,
                width=2,
            )

        label = _lane_label(lane_info, results_by_lane)
        if label:
            text_y = max(0, top_offset - 14)
            draw.rectangle([lane_info.x_start, text_y, lane_info.x_end - 1, text_y + 12], fill=LABEL_BG_COLOR)
            draw.text((lane_info.x_start + 2, text_y), label, fill=LABEL_TEXT_COLOR)

    _draw_ladder_calibration(draw, debug_info, top_offset)

    return canvas


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
    return f"L{lane_info.lane}: {purity} ({mw})"


def _draw_ladder_calibration(draw: ImageDraw.ImageDraw, debug_info: AnalysisDebugInfo, top_offset: int) -> None:
    """Annotate the ladder lane with its calibrated MW at each fitted band position."""
    calibration = debug_info.ladder_calibration
    if calibration is None:
        return
    ladder_lane = next((lane for lane in debug_info.lanes if lane.is_ladder), None)
    if ladder_lane is None:
        return
    for position, mw in zip(calibration.positions, calibration.mws):
        y = top_offset + int(position)
        draw.line([(ladder_lane.x_start, y), (ladder_lane.x_end - 1, y)], fill=LADDER_COLOR, width=1)
        draw.text((ladder_lane.x_end + 4, y - 6), f"{mw:.0f}", fill=(0, 0, 0))


def save_debug_image(
    image: np.ndarray,
    results: list[LaneResult],
    debug_info: AnalysisDebugInfo,
    path: str | Path,
    top_margin_fraction: float = DEFAULT_TOP_MARGIN_FRACTION,
) -> None:
    render_debug_image(image, results, debug_info, top_margin_fraction).save(str(path))
