"""argparse wiring for the `purity` subcommand."""

import argparse
import json
import sys
from pathlib import Path

from neband.core.image_io import load_image
from neband.purity.analysis import BAND_SELECTIONS, DEFAULT_BAND_SELECTION, DEFAULT_MW_TOLERANCE_PERCENT
from neband.purity.debug_viz import save_debug_image
from neband.purity.methods import METHOD_REGISTRY, run_all_methods, run_method
from neband.purity.output import format_csv, format_table, to_payload, write_output


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
            "total lane area) for each sample lane. A lane whose total "
            "detected signal is faint relative to the most-concentrated "
            "lane in the image (e.g. a highly-diluted sample) is flagged "
            "'low signal' -- its purity % may be inflated by faint "
            "contaminant bands dropping below the detection floor before "
            "the target band does."
        ),
    )
    analyze_parser.add_argument("image", help="Path to the gel image file (TIFF, PNG, or JPG).")
    analyze_parser.add_argument(
        "--target-mw",
        type=float,
        default=None,
        metavar="KDA",
        help=(
            "Expected molecular weight of the target protein, in kDa. Required "
            "with --band-selection mw-strict (needed to select a band at all). "
            "Optional with the default --band-selection largest -- if omitted, "
            "the largest band is still reported with a real calibrated MW when "
            "the ladder calibrates, just flagged 'largest-unverified' instead of "
            "'mw-matched'/'mw-mismatch' since there's nothing to check it against."
        ),
    )

    ladder_group = analyze_parser.add_mutually_exclusive_group()
    ladder_group.add_argument(
        "--ladder",
        metavar="NAME",
        help=(
            "Name of a known ladder to calibrate against (currently: "
            "P7719, P7717). Use --ladder-bands instead for any other ladder. "
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
        "--band-selection",
        default=DEFAULT_BAND_SELECTION,
        choices=BAND_SELECTIONS,
        help=(
            f"Which band counts as the target (default: {DEFAULT_BAND_SELECTION}). "
            "'largest': the biggest detected band always wins, regardless of MW -- "
            "empirically closer to confirmed ground-truth purity than MW-matching on "
            "this project's real images (see AGENTS.md). The ladder is still "
            "calibrated when possible, purely to VERIFY the selected band against "
            "--target-mw and flag a mismatch ('mw-mismatch') -- it never gates "
            "selection in this mode. 'mw-strict': the original behavior -- only a "
            "band within --mw-tolerance of --target-mw counts as the target at all, "
            "falling back to the largest band only with --allow-heuristic."
        ),
    )
    analyze_parser.add_argument(
        "--mw-tolerance",
        type=float,
        default=DEFAULT_MW_TOLERANCE_PERCENT,
        metavar="PERCENT",
        help=(
            f"How close (as %% of target MW) counts as a match (default: "
            f"{DEFAULT_MW_TOLERANCE_PERCENT}%%). Meaning depends on --band-selection: "
            "with 'mw-strict' it's the selection filter (a band outside this range "
            "never counts as the target); with 'largest' it's only the threshold for "
            "flagging the already-selected band as 'mw-mismatch'. Placeholder value -- "
            "expected to be tuned as more real gels are tested."
        ),
    )
    analyze_parser.add_argument(
        "--allow-heuristic",
        action="store_true",
        help=(
            "If the ladder can't be calibrated at all (zero MW info available), fall "
            "back to reporting the largest band's purity anyway instead of refusing "
            "to produce a result. With --band-selection mw-strict, also applies when "
            "no band matches --target-mw within tolerance. Results from this fallback "
            "are marked 'heuristic' (lower-confidence) in the output, never presented "
            "as equivalent to an MW-verified result. Does NOT gate the 'largest' "
            "mode's mismatch flagging -- a calibrated-but-mismatched band is always "
            "reported (with the flag) regardless of this setting, since real "
            "information exists then, just disagreeing information."
        ),
    )
    analyze_parser.add_argument(
        "--method",
        default="rectangle",
        choices=[*sorted(METHOD_REGISTRY), "all"],
        help=(
            "Lane-geometry method to use (default: rectangle -- straight "
            "vertical lanes, the only method with production validation). "
            "Alternatives are experimental; every output clearly labels "
            "which method produced it and its confidence/maturity tier -- "
            "check that before trusting a number from anything but "
            "'rectangle'. 'all' runs every registered method and reports "
            "each one separately (one --debug image per method, if given)."
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
    analyze_parser.add_argument(
        "--debug",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=(
            "Also write an annotated debug image showing detected lane and "
            "band boxes (blue = ladder lane, amber = sample lane, green = "
            "band counted as the target/matched signal, gold/yellow = the "
            "selected band's calibrated MW doesn't match --target-mw "
            "('mw-mismatch', only possible with the default --band-selection "
            "largest), red = other/contaminant band, orange = a traced curve "
            "overlay for methods whose geometry isn't a straight rectangle). "
            "A colored banner across the top always names the method and "
            "its maturity tier. With no PATH, writes next to the input "
            "image as '<input-stem>_debug.png'; with --method all, one "
            "image per method instead, as '<input-stem>_debug_<method>.png'."
        ),
    )
    analyze_parser.set_defaults(func=_run_analyze)


def _run_analyze(args: argparse.Namespace) -> int:
    if args.csv == "-" and args.json == "-":
        print("error: --csv and --json cannot both be written to stdout at once", file=sys.stderr)
        return 2
    if args.target_mw is None and args.band_selection == "mw-strict":
        print("error: --target-mw is required with --band-selection mw-strict", file=sys.stderr)
        return 2

    ladder_bands = [float(v) for v in args.ladder_bands.split(",")] if args.ladder_bands else None
    method_kwargs = dict(
        ladder=args.ladder,
        ladder_bands=ladder_bands,
        ladder_lane_index=(args.ladder_lane - 1) if args.ladder_lane else None,
        lane_index=args.lane,
        tolerance_percent=args.mw_tolerance,
        allow_heuristic=args.allow_heuristic,
        band_selection=args.band_selection,
    )

    try:
        if args.method == "all":
            return _run_all_methods(args, method_kwargs)
        outcome = run_method(args.method, args.image, args.target_mw, **method_kwargs)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not outcome.ok:
        print(f"error: {outcome.error}", file=sys.stderr)
        return 1

    suppress_table = args.csv == "-" or args.json == "-"
    if not suppress_table:
        print(format_table(outcome.results, outcome.ladder_lane_index, outcome.method, outcome.maturity))

    if args.csv is not None:
        write_output(format_csv(outcome.results, outcome.method, outcome.maturity), None if args.csv == "-" else args.csv)
    if args.json is not None:
        payload = to_payload(outcome.results, outcome.ladder_lane_index, outcome.method, outcome.maturity)
        write_output(json.dumps(payload, indent=2), None if args.json == "-" else args.json)

    if args.debug is not None:
        debug_path = args.debug or str(Path(args.image).with_name(Path(args.image).stem + "_debug.png"))
        try:
            raw_image = load_image(args.image)
            save_debug_image(raw_image, outcome.results, outcome.debug_info, debug_path, outcome.method, outcome.maturity)
        except OSError as exc:
            print(f"error: could not write debug image to {debug_path!r}: {exc}", file=sys.stderr)
            return 1
        print(f"Debug image written to {debug_path}")

    return 0


def _run_all_methods(args: argparse.Namespace, method_kwargs: dict) -> int:
    """`--method all`: run every registered method, report each separately.

    Never lets one method's failure hide another's result -- each outcome
    is rendered as its own block (table) or its own list entry (JSON), and
    a failed method still gets one --debug attempt skipped with a clear
    note rather than aborting the run for the methods that did succeed.
    """
    outcomes = run_all_methods(args.image, args.target_mw, **method_kwargs)

    suppress_table = args.csv == "-" or args.json == "-"
    if not suppress_table:
        blocks = []
        for outcome in outcomes:
            header = f"=== method: {outcome.method} ({outcome.maturity}) ==="
            if outcome.lane_numbering_caveat:
                header += f"\n{outcome.lane_numbering_caveat}"
            if not outcome.ok:
                blocks.append(f"{header}\nFAILED: {outcome.error}")
            else:
                blocks.append(f"{header}\n{format_table(outcome.results, outcome.ladder_lane_index, outcome.method, outcome.maturity)}")
        print("\n\n".join(blocks))

    if args.csv is not None:
        rows = "".join(
            format_csv(outcome.results, outcome.method, outcome.maturity) for outcome in outcomes if outcome.ok
        )
        write_output(rows, None if args.csv == "-" else args.csv)

    if args.json is not None:
        payload = {
            "methods": [
                to_payload(outcome.results, outcome.ladder_lane_index, outcome.method, outcome.maturity)
                if outcome.ok
                else {"method": outcome.method, "maturity": outcome.maturity, "error": outcome.error}
                for outcome in outcomes
            ]
        }
        write_output(json.dumps(payload, indent=2), None if args.json == "-" else args.json)

    if args.debug is not None:
        try:
            raw_image = load_image(args.image)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        base_path = Path(args.debug) if args.debug else Path(args.image).with_name(Path(args.image).stem + "_debug.png")
        for outcome in outcomes:
            debug_path = base_path.with_name(f"{base_path.stem}_{outcome.method}{base_path.suffix}")
            if not outcome.ok:
                print(f"method {outcome.method}: FAILED - {outcome.error}", file=sys.stderr)
                continue
            try:
                save_debug_image(raw_image, outcome.results, outcome.debug_info, debug_path, outcome.method, outcome.maturity)
            except OSError as exc:
                print(f"error: could not write debug image to {debug_path!r}: {exc}", file=sys.stderr)
                continue
            print(f"Debug image written to {debug_path}")

    # Unix convention: exit non-zero only if EVERY method failed -- a
    # partial success (some methods worked, one crashed) is still a
    # successful run overall, matching the "one bad method never aborts
    # the others" design; report 1 only when there's genuinely nothing to
    # show for this run.
    return 1 if outcomes and not any(outcome.ok for outcome in outcomes) else 0
