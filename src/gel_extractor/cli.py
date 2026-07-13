"""Top-level CLI entry point (`gelx`)."""

import argparse
import sys

from gel_extractor.purity import cli as purity_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gelx",
        description="Extract quantitative and categorical data from gel electrophoresis images.",
    )
    subparsers = parser.add_subparsers(dest="workflow", required=True)
    purity_cli.add_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
