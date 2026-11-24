#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from python_source_validator import (  # noqa: E402
    SourceValidationError,
    validate_source,
)


class PythonSourceValidatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_utf8_bom_source_is_parsed_without_rewriting(self):
        path = self.root / "with_bom.py"
        path.write_bytes(b"\xef\xbb\xbfvalue = 42\n")
        before = self.digest(path)
        validate_source(path)
        self.assertEqual(self.digest(path), before)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_utf8_without_bom_is_parsed_without_rewriting(self):
        path = self.root / "without_bom.py"
        path.write_bytes("message = '太陽'\n".encode("utf-8"))
        before = self.digest(path)
        validate_source(path)
        self.assertEqual(self.digest(path), before)
        self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_real_syntax_error_is_rejected(self):
        path = self.root / "invalid.py"
        path.write_bytes(b"def broken(:\n    pass\n")
        with self.assertRaisesRegex(SourceValidationError, "invalid.py"):
            validate_source(path)

    def test_python_interpreter_executes_utf8_bom_source(self):
        path = self.root / "executable_bom.py"
        path.write_bytes(b"\xef\xbb\xbfprint('BOM execution OK')\n")
        result = subprocess.run(
            [sys.executable, str(path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "BOM execution OK")


if __name__ == "__main__":
    unittest.main()
