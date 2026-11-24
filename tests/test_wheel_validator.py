#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from wheel_validator import (  # noqa: E402
    Expectation,
    ValidationError,
    canonicalize_name,
    validate_wheels,
)


class WheelValidatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def make_wheel(self, filename: str, name: str, version: str) -> tuple[Path, str]:
        path = self.root / filename
        dist_info = f"{canonicalize_name(name).replace('-', '_')}-{version}.dist-info"
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{dist_info}/METADATA", metadata)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return path, digest

    @staticmethod
    def expectation(name: str, version: str, digest: str) -> Expectation:
        return Expectation(name, canonicalize_name(name), version, digest)

    def test_dot_and_underscore_filenames_use_embedded_metadata(self):
        core, core_hash = self.make_wheel(
            "luma.core-2.4.2-py2.py3-none-any.whl", "luma.core", "2.4.2"
        )
        oled, oled_hash = self.make_wheel(
            "luma_oled-3.16.0-py3-none-any.whl", "luma.oled", "3.16.0"
        )
        result = validate_wheels(
            self.root,
            [
                self.expectation("luma.core", "2.4.2", core_hash),
                self.expectation("luma.oled", "3.16.0", oled_hash),
            ],
        )
        self.assertEqual([item.path for item in result], [core.resolve(), oled.resolve()])

    def test_alternate_filename_separators_do_not_change_identity(self):
        core, core_hash = self.make_wheel(
            "luma_core-2.4.2-py2.py3-none-any.whl", "luma.core", "2.4.2"
        )
        oled, oled_hash = self.make_wheel(
            "luma.oled-3.16.0-py3-none-any.whl", "luma.oled", "3.16.0"
        )
        result = validate_wheels(
            self.root,
            [
                self.expectation("luma.core", "2.4.2", core_hash),
                self.expectation("luma.oled", "3.16.0", oled_hash),
            ],
        )
        self.assertEqual([item.path for item in result], [core.resolve(), oled.resolve()])

    def test_sha256_mismatch_is_rejected(self):
        self.make_wheel("luma.core-2.4.2-any.whl", "luma.core", "2.4.2")
        with self.assertRaisesRegex(ValidationError, "SHA-256 does not match"):
            validate_wheels(
                self.root,
                [self.expectation("luma.core", "2.4.2", "0" * 64)],
            )

    def test_wrong_embedded_version_is_rejected(self):
        _, digest = self.make_wheel(
            "luma_core-2.4.2-any.whl", "luma.core", "9.9.9"
        )
        with self.assertRaisesRegex(ValidationError, "expected version 2.4.2"):
            validate_wheels(
                self.root,
                [self.expectation("luma.core", "2.4.2", digest)],
            )

    def test_filename_cannot_spoof_distribution_name(self):
        _, digest = self.make_wheel(
            "luma_core-2.4.2-any.whl", "different-package", "2.4.2"
        )
        with self.assertRaisesRegex(ValidationError, "unexpected wheel distribution"):
            validate_wheels(
                self.root,
                [self.expectation("luma.core", "2.4.2", digest)],
            )

    def test_missing_expected_wheel_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "expected wheels were not found"):
            validate_wheels(
                self.root,
                [self.expectation("luma.core", "2.4.2", "0" * 64)],
            )

    def test_duplicate_distribution_is_rejected(self):
        first, digest = self.make_wheel("first.whl", "luma.core", "2.4.2")
        shutil.copyfile(first, self.root / "second.whl")
        with self.assertRaisesRegex(ValidationError, "multiple wheels"):
            validate_wheels(
                self.root,
                [self.expectation("luma.core", "2.4.2", digest)],
            )


if __name__ == "__main__":
    unittest.main()
