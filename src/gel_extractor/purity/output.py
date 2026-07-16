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

FIELDS = [
    "method",
    "maturity",
    "lane",
    "purity_percent",
    "confidence",
    "target_mw_expected",
    "matched_band_mw",
    "low_signal",
]


def to_records(results: list[LaneResult], method: str, maturity: str) -> list[dict]:
    """`method`/`maturity` are required, not optional -- every output row must
    say which lane-geometry method produced it and how much to trust it (see
    `purity.methods`), including today's single-method default, not just a
    future multi-method mode.
    """
    return [
        {
            "method": method,
            "maturity": maturity,
            "lane": r.lane,
            "purity_percent": r.purity_percent,
            "confidence": r.confidence,
            "target_mw_expected": r.target_mw_expected,
            "matched_band_mw": r.matched_band_mw,
            "low_signal": r.low_signal,
        }
        for r in results
    ]


def format_table(results: list[LaneResult], ladder_lane_index: int, method: str, maturity: str) -> str:
    total_lanes = len(results) + 1
    header = f"Method: {method} ({maturity}). Ladder detected in lane {ladder_lane_index + 1} of {total_lanes} total lanes."

    col = {"lane": 4, "purity": 10, "confidence": 12, "mw": 12, "flag": 11}
    title_row = (
        f"{'Lane':>{col['lane']}}  {'Purity %':>{col['purity']}}  "
        f"{'Confidence':<{col['confidence']}}  {'Matched MW':>{col['mw']}}  {'Flag':<{col['flag']}}"
    )
    rows = [title_row]
    for r in results:
        purity = f"{r.purity_percent:d}" if r.purity_percent is not None else "n/a"
        matched_mw = f"{r.matched_band_mw:.1f}" if r.matched_band_mw is not None else "n/a"
        flag = "low signal" if r.low_signal else ""
        rows.append(
            f"{r.lane:>{col['lane']}}  {purity:>{col['purity']}}  "
            f"{r.confidence:<{col['confidence']}}  {matched_mw:>{col['mw']}}  {flag:<{col['flag']}}"
        )
    return header + "\n" + "\n".join(rows)


def format_csv(results: list[LaneResult], method: str, maturity: str) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(to_records(results, method, maturity))
    return buf.getvalue()


def to_payload(results: list[LaneResult], ladder_lane_index: int, method: str, maturity: str) -> dict:
    """The dict a single method's JSON output serializes -- exposed
    separately from `format_json` so `--method all` (see `purity.cli`) can
    assemble several methods' payloads into one combined JSON document
    without round-tripping through a JSON string first.
    """
    return {
        "method": method,
        "maturity": maturity,
        "ladder_lane": ladder_lane_index + 1,
        "results": to_records(results, method, maturity),
    }


def format_json(results: list[LaneResult], ladder_lane_index: int, method: str, maturity: str) -> str:
    return json.dumps(to_payload(results, ladder_lane_index, method, maturity), indent=2)


def write_output(content: str, destination: str | None) -> None:
    """Print to stdout if destination is None/'-', else write to a file path."""
    if destination in (None, "-"):
        print(content)
    else:
        Path(destination).write_text(content)
