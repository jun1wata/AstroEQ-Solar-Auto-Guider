#!/usr/bin/env python3
"""Inspect Raspberry Pi config.txt settings for a Pi Zero 2 W.

This parser is deliberately limited to the three settings managed by the
installer. Known model filters are evaluated, includes are expanded in place,
and an applicable conflict or an indeterminate conditional stops installation.
It never writes the configuration.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import sys


REQUIRED_SETTINGS = (
    "dtparam=i2c_arm=on",
    "dtoverlay=dwc2,dr_mode=host",
    "dtoverlay=rotary-encoder,pin_a=5,pin_b=6,relative_axis=1,steps-per-period=2",
)

APPLICABLE_MODEL_FILTERS = {"pi0", "pi0w", "pi02"}
NON_APPLICABLE_MODEL_FILTERS = {
    "pi1", "pi2", "pi3", "pi3+", "pi4", "pi5", "pi400", "pi500",
    "cm0", "cm1", "cm3", "cm3+", "cm4", "cm4s", "cm5",
}
KNOWN_MODEL_FILTERS = APPLICABLE_MODEL_FILTERS | NON_APPLICABLE_MODEL_FILTERS
MAX_INCLUDE_DEPTH = 10


class InspectionError(RuntimeError):
    """A configuration cannot be classified safely."""


@dataclass
class FilterState:
    model_filter: str = "all"
    model_applies: bool = True
    unknown_filters: list[str] = field(default_factory=list)

    @property
    def applicability(self) -> str:
        if not self.model_applies:
            return "skip"
        if self.unknown_filters:
            return "unknown"
        return "apply"

    @property
    def label(self) -> str:
        parts = [f"[{self.model_filter}]" if self.model_filter != "all" else "[all]"]
        parts.extend(f"[{item}]" for item in self.unknown_filters)
        return "+".join(parts)

    def set_section(self, section: str) -> None:
        lowered = section.casefold()
        if lowered == "all":
            self.model_filter = "all"
            self.model_applies = True
            self.unknown_filters.clear()
        elif lowered == "none":
            self.model_filter = "none"
            self.model_applies = False
            self.unknown_filters.clear()
        elif lowered in KNOWN_MODEL_FILTERS:
            # Model filters replace one another but retain filters of other types.
            self.model_filter = lowered
            self.model_applies = lowered in APPLICABLE_MODEL_FILTERS
        elif lowered not in self.unknown_filters:
            # EDID, GPIO, serial, board-type, expression, tryboot, and future
            # filter forms cannot be proven true for this installer invocation.
            self.unknown_filters.append(lowered)


@dataclass(frozen=True)
class Finding:
    setting: str
    path: Path
    line_number: int
    filters: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line_number} ({self.filters})"


@dataclass
class InspectionResult:
    present: dict[str, list[Finding]] = field(
        default_factory=lambda: {setting: [] for setting in REQUIRED_SETTINGS}
    )
    ignored: list[Finding] = field(default_factory=list)
    included_files: list[Path] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        return [setting for setting, findings in self.present.items() if not findings]


def normalise_setting(text: str) -> str:
    return "".join(text.split())


def managed_setting(line: str) -> str | None:
    if line.startswith("dtparam=i2c_arm="):
        return REQUIRED_SETTINGS[0]
    if line == "dtoverlay=dwc2" or line.startswith(("dtoverlay=dwc2,", "dtoverlay=dwc2=")):
        return REQUIRED_SETTINGS[1]
    if line == "dtoverlay=rotary-encoder" or line.startswith(
        ("dtoverlay=rotary-encoder,", "dtoverlay=rotary-encoder=")
    ):
        return REQUIRED_SETTINGS[2]
    return None


class ConfigInspector:
    def __init__(self, main_config: Path) -> None:
        self.main_config = main_config.resolve(strict=True)
        self.boot_root = self.main_config.parent
        self.state = FilterState()
        self.result = InspectionResult()
        self._include_stack: list[Path] = []

    def inspect(self) -> InspectionResult:
        self._parse_file(self.main_config, depth=0)
        for setting, findings in self.result.present.items():
            if len(findings) > 1:
                locations = ", ".join(item.location for item in findings)
                raise InspectionError(
                    f"duplicate applicable setting {setting}: {locations}"
                )
        return self.result

    def _resolve_include(self, value: str, source: Path, line_number: int) -> Path:
        if not value or any(char.isspace() for char in value):
            raise InspectionError(
                f"unsupported include syntax at {source}:{line_number}: include {value}"
            )
        candidate = Path(value)
        if candidate.is_absolute():
            raise InspectionError(
                f"absolute include is not allowed at {source}:{line_number}: {value}"
            )
        resolved = (self.boot_root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self.boot_root)
        except ValueError as exc:
            raise InspectionError(
                f"include escapes the boot configuration directory at "
                f"{source}:{line_number}: {value}"
            ) from exc
        if not resolved.is_file():
            raise InspectionError(
                f"included file is missing at {source}:{line_number}: {resolved}"
            )
        return resolved

    def _parse_file(self, path: Path, depth: int) -> None:
        if depth > MAX_INCLUDE_DEPTH:
            raise InspectionError(f"include depth exceeds {MAX_INCLUDE_DEPTH}: {path}")
        if path in self._include_stack:
            chain = " -> ".join(str(item) for item in (*self._include_stack, path))
            raise InspectionError(f"recursive include detected: {chain}")
        self._include_stack.append(path)
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            for line_number, raw_line in enumerate(lines, start=1):
                text = raw_line.split("#", 1)[0].strip()
                if not text:
                    continue
                if text.startswith("[") and text.endswith("]"):
                    section = text[1:-1].strip()
                    if not section:
                        raise InspectionError(f"empty conditional section at {path}:{line_number}")
                    self.state.set_section(section)
                    continue

                lowered = text.casefold()
                if lowered.startswith("include"):
                    parts = text.split(maxsplit=1)
                    if parts[0].casefold() != "include":
                        # A normal setting beginning with the same letters.
                        pass
                    else:
                        value = parts[1].strip() if len(parts) == 2 else ""
                        included = self._resolve_include(value, path, line_number)
                        self.result.included_files.append(included)
                        # Firmware inserts included content at this exact point.
                        # Parse it even in a currently false section: the file
                        # may contain [all] and change the following state.
                        self._parse_file(included, depth + 1)
                        continue

                setting_line = normalise_setting(text)
                target = managed_setting(setting_line)
                if target is None:
                    continue
                finding = Finding(target, path, line_number, self.state.label)
                applicability = self.state.applicability
                if applicability == "skip":
                    self.result.ignored.append(finding)
                    continue
                if applicability == "unknown":
                    raise InspectionError(
                        f"cannot determine whether {setting_line} applies at "
                        f"{finding.location}"
                    )
                if setting_line != target:
                    raise InspectionError(
                        f"conflicting applicable setting {setting_line} at {finding.location}; "
                        f"required value is {target}"
                    )
                self.result.present[target].append(finding)
        finally:
            self._include_stack.pop()


def emit_tsv(result: InspectionResult) -> None:
    for path in result.included_files:
        print(f"INCLUDE\t{path}")
    for finding in result.ignored:
        print(f"IGNORED\t{finding.setting}\t{finding.location}")
    for setting in REQUIRED_SETTINGS:
        findings = result.present[setting]
        if findings:
            print(f"PRESENT\t{setting}\t{findings[0].location}")
        else:
            print(f"MISSING\t{setting}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--model",
        choices=("pi02",),
        default="pi02",
        help="target model; pi02 is Raspberry Pi Zero 2 W",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = ConfigInspector(args.config).inspect()
    except (InspectionError, OSError, UnicodeError) as exc:
        print(f"ERROR\t{exc}", file=sys.stderr)
        return 2
    emit_tsv(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
