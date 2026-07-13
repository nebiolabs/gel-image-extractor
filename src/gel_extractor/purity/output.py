"""Table/CSV/JSON output formatters for purity results.

Each formatter reads from the same `LaneResult` objects -- adding a new
output format means writing one more formatter here, not touching the
analysis logic (see AGENTS.md Design Decisions: "modular, swappable
architecture").
"""

import csv
import io
import json
from pathlib import Path

from gel_extractor.purity.analysis import LaneResult

FIELDS = ["lane", "purity_percent", "confidence", "target_mw_expected", "matched_band_mw"]


def to_records(results: list[LaneResult]) -> list[dict]:
    return [
        {
            "lane": r.lane,
            "purity_percent": r.purity_percent,
            "confidence": r.confidence,
            "target_mw_expected": r.target_mw_expected,
            "matched_band_mw": r.matched_band_mw,
        }
        for r in results
    ]


def format_table(results: list[LaneResult], ladder_lane_index: int) -> str:
    total_lanes = len(results) + 1
    header = f"Ladder detected in lane {ladder_lane_index + 1} of {total_lanes} total lanes."

    col = {"lane": 4, "purity": 10, "confidence": 12, "mw": 12}
    title_row = (
        f"{'Lane':>{col['lane']}}  {'Purity %':>{col['purity']}}  "
        f"{'Confidence':<{col['confidence']}}  {'Matched MW':>{col['mw']}}"
    )
    rows = [title_row]
    for r in results:
        purity = f"{r.purity_percent:d}" if r.purity_percent is not None else "n/a"
        matched_mw = f"{r.matched_band_mw:.1f}" if r.matched_band_mw is not None else "n/a"
        rows.append(
            f"{r.lane:>{col['lane']}}  {purity:>{col['purity']}}  "
            f"{r.confidence:<{col['confidence']}}  {matched_mw:>{col['mw']}}"
        )
    return header + "\n" + "\n".join(rows)


def format_csv(results: list[LaneResult]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(to_records(results))
    return buf.getvalue()


def format_json(results: list[LaneResult], ladder_lane_index: int) -> str:
    payload = {"ladder_lane": ladder_lane_index + 1, "results": to_records(results)}
    return json.dumps(payload, indent=2)


def write_output(content: str, destination: str | None) -> None:
    """Print to stdout if destination is None/'-', else write to a file path."""
    if destination in (None, "-"):
        print(content)
    else:
        Path(destination).write_text(content)
