#!/usr/bin/env python3
"""Validate downloaded wheels by embedded metadata, version, and SHA-256."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from email.parser import BytesParser
import hashlib
from pathlib import Path
import re
import sys
import zipfile


CANONICAL_NAME_SEPARATOR = re.compile(r"[-_.]+")
SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


class ValidationError(RuntimeError):
    """A wheel set does not match the pinned expectations."""


@dataclass(frozen=True)
class Expectation:
    display_name: str
    canonical_name: str
    version: str
    sha256: str


@dataclass(frozen=True)
class ValidatedWheel:
    canonical_name: str
    path: Path


def canonicalize_name(name: str) -> str:
    return CANONICAL_NAME_SEPARATOR.sub("-", name).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def wheel_identity(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_files = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                raise ValidationError(
                    f"{path}: expected exactly one .dist-info/METADATA entry, "
                    f"found {len(metadata_files)}"
                )
            metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"{path}: cannot read wheel metadata: {exc}") from exc

    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise ValidationError(f"{path}: wheel metadata lacks Name or Version")
    return name.strip(), version.strip()


def parse_expectations(values: list[list[str]]) -> list[Expectation]:
    expectations: list[Expectation] = []
    seen: set[str] = set()
    for name, version, digest in values:
        canonical_name = canonicalize_name(name)
        if not canonical_name or not version:
            raise ValidationError("expected distribution name and version cannot be empty")
        if not SHA256_PATTERN.fullmatch(digest):
            raise ValidationError(f"invalid SHA-256 for {name}: {digest}")
        if canonical_name in seen:
            raise ValidationError(f"duplicate expected distribution: {name}")
        seen.add(canonical_name)
        expectations.append(
            Expectation(name, canonical_name, version, digest.lower())
        )
    return expectations


def validate_wheels(
    directory: Path, expectations: list[Expectation]
) -> list[ValidatedWheel]:
    if not directory.is_dir():
        raise ValidationError(f"wheel directory does not exist: {directory}")
    expected_by_name = {item.canonical_name: item for item in expectations}
    expected_digests = {item.sha256 for item in expectations}
    found: dict[str, ValidatedWheel] = {}

    wheels = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.casefold() == ".whl"
    )
    for wheel in wheels:
        actual_digest = sha256_file(wheel)
        if actual_digest not in expected_digests:
            raise ValidationError(
                f"SHA-256 does not match any expected wheel: {wheel}"
            )
        name, version = wheel_identity(wheel)
        canonical_name = canonicalize_name(name)
        expectation = expected_by_name.get(canonical_name)
        if expectation is None:
            raise ValidationError(
                f"unexpected wheel distribution {name} in {wheel}"
            )
        if canonical_name in found:
            raise ValidationError(
                f"multiple wheels found for {expectation.display_name}"
            )
        if version != expectation.version:
            raise ValidationError(
                f"{expectation.display_name}: expected version {expectation.version}, "
                f"found {version} in {wheel}"
            )
        if actual_digest != expectation.sha256:
            raise ValidationError(
                f"{expectation.display_name}: SHA-256 mismatch for {wheel}"
            )
        if "\t" in str(wheel) or "\n" in str(wheel):
            raise ValidationError(f"unsupported wheel path: {wheel}")
        found[canonical_name] = ValidatedWheel(canonical_name, wheel.resolve())

    missing = [
        item.display_name for item in expectations if item.canonical_name not in found
    ]
    if missing:
        raise ValidationError(f"expected wheels were not found: {', '.join(missing)}")
    return [found[item.canonical_name] for item in expectations]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--expect",
        action="append",
        nargs=3,
        metavar=("NAME", "VERSION", "SHA256"),
        required=True,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        expectations = parse_expectations(args.expect)
        validated = validate_wheels(args.directory, expectations)
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for item in validated:
        print(f"{item.canonical_name}\t{item.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
