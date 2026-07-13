"""argparse wiring for the `purity` subcommand."""

import argparse
import sys

from gel_extractor.purity.analysis import DEFAULT_MW_TOLERANCE_PERCENT, LadderNotCalibratedError, analyze_image
from gel_extractor.purity.output import format_csv, format_json, format_table, write_output


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("purity", help="Quantify protein purity from an SDS-PAGE gel image.")
    purity_subparsers = parser.add_subparsers(dest="purity_command", required=True)

    analyze_parser = purity_subparsers.add_parser(
        "analyze",
        help="Analyze a gel image and report purity %% per sample lane.",
        description=(
            "Auto-detects lanes in a gel image, calibrates the ladder lane "
            "against known band sizes, identifies the target protein band by "
            "molecular weight, and reports purity % (target band area / "
            "total lane area) for each sample lane."
        ),
    )
    analyze_parser.add_argument("image", help="Path to the gel image file (TIFF, PNG, or JPG).")
    analyze_parser.add_argument(
        "--target-mw",
        type=float,
        required=True,
        metavar="KDA",
        help="Expected molecular weight of the target protein, in kDa.",
    )

    ladder_group = analyze_parser.add_mutually_exclusive_group()
    ladder_group.add_argument(
        "--ladder",
        metavar="NAME",
        help=(
            "Name of a known ladder to calibrate against (currently: "
            "P7719). Use --ladder-bands instead for any other ladder. "
            "Mutually exclusive with --ladder-bands."
        ),
    )
    ladder_group.add_argument(
        "--ladder-bands",
        metavar="KDA1,KDA2,...",
        help=(
            "Comma-separated known band sizes (kDa) for the ladder actually "
            "used, largest first, e.g. '250,150,100,75,50,37,25,20,15,10'. "
            "Required for MW-based matching until --ladder recognizes your ladder."
        ),
    )
    analyze_parser.add_argument(
        "--ladder-lane",
        type=int,
        metavar="N",
        help="1-based index of the detected lane that is the ladder (default: leftmost detected lane).",
    )
    analyze_parser.add_argument(
        "--lane",
        type=int,
        metavar="N",
        help="Only analyze this 1-based sample lane (default: analyze all sample lanes).",
    )
    analyze_parser.add_argument(
        "--mw-tolerance",
        type=float,
        default=DEFAULT_MW_TOLERANCE_PERCENT,
        metavar="PERCENT",
        help=(
            f"How close (as %% of target MW) a detected band must be to count as "
            f"the target (default: {DEFAULT_MW_TOLERANCE_PERCENT}%%). Placeholder "
            "value -- expected to be tuned as more real gels are tested."
        ),
    )
    analyze_parser.add_argument(
        "--allow-heuristic",
        action="store_true",
        help=(
            "If the ladder can't be calibrated, or no band matches --target-mw "
            "within tolerance, fall back to treating the single largest band as "
            "the target instead of refusing to produce a result. Results from "
            "this fallback are marked 'heuristic' (lower-confidence) in the "
            "output, never presented as equivalent to an MW-matched result."
        ),
    )
    analyze_parser.add_argument(
        "--csv",
        nargs="?",
        const="-",
        default=None,
        metavar="PATH",
        help=(
            "Also emit CSV output. With no PATH, prints CSV to stdout instead of "
            "the human-readable table; with a PATH, writes a CSV file there and "
            "still prints the table."
        ),
    )
    analyze_parser.add_argument(
        "--json",
        nargs="?",
        const="-",
        default=None,
        metavar="PATH",
        help=(
            "Also emit JSON output. With no PATH, prints JSON to stdout instead "
            "of the human-readable table; with a PATH, writes a JSON file there "
            "and still prints the table."
        ),
    )
    analyze_parser.set_defaults(func=_run_analyze)


def _run_analyze(args: argparse.Namespace) -> int:
    if args.csv == "-" and args.json == "-":
        print("error: --csv and --json cannot both be written to stdout at once", file=sys.stderr)
        return 2

    ladder_bands = [float(v) for v in args.ladder_bands.split(",")] if args.ladder_bands else None

    try:
        results, ladder_lane_index = analyze_image(
            args.image,
            target_mw=args.target_mw,
            ladder=args.ladder,
            ladder_bands=ladder_bands,
            ladder_lane_index=(args.ladder_lane - 1) if args.ladder_lane else None,
            lane_index=args.lane,
            tolerance_percent=args.mw_tolerance,
            allow_heuristic=args.allow_heuristic,
        )
    except (LadderNotCalibratedError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    suppress_table = args.csv == "-" or args.json == "-"
    if not suppress_table:
        print(format_table(results, ladder_lane_index))

    if args.csv is not None:
        write_output(format_csv(results), None if args.csv == "-" else args.csv)
    if args.json is not None:
        write_output(format_json(results, ladder_lane_index), None if args.json == "-" else args.json)

    return 0
