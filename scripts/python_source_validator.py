#!/usr/bin/env python3
"""Read-only syntax validation for UTF-8 Python sources with optional BOM."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys


class SourceValidationError(RuntimeError):
    """A source directory or Python file cannot be validated."""


def validate_source(path: Path) -> None:
    """Parse a UTF-8 source without changing its bytes.

    utf-8-sig consumes an initial UTF-8 BOM when present and otherwise behaves
    like ordinary UTF-8.
    """

    try:
        source = path.read_text(encoding="utf-8-sig")
        ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise SourceValidationError(f"{path}: {exc}") from exc


def validate_directory(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise SourceValidationError(f"source directory does not exist: {directory}")
    paths = sorted(directory.glob("*.py"))
    if not paths:
        raise SourceValidationError(f"no Python sources found in: {directory}")
    for path in paths:
        validate_source(path)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        paths = validate_directory(args.directory)
    except SourceValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Python source syntax: OK ({len(paths)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
