"""Command-line entry point: `s2s validate room.json [...]`."""

from __future__ import annotations

import argparse
import sys

from .validate import validate_file


def _cmd_validate(args: argparse.Namespace) -> int:
    failed = 0
    for path in args.files:
        issues = validate_file(path)
        if issues:
            failed += 1
            print(f"FAIL {path}")
            for issue in issues:
                print(f"  - {issue}")
        elif not args.quiet:
            print(f"ok   {path}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s2s", description="Scan-to-Schema tools")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="check room-core documents")
    v.add_argument("files", nargs="+")
    v.add_argument("-q", "--quiet", action="store_true", help="only report failures")
    v.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
