#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from config_txt_inspector import (  # noqa: E402
    ConfigInspector,
    InspectionError,
    REQUIRED_SETTINGS,
)


class ConfigInspectorTests(unittest.TestCase):
    def inspect(self, text: str, includes: dict[str, str] | None = None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = root / "config.txt"
        config.write_text(text, encoding="utf-8")
        for name, content in (includes or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return ConfigInspector(config).inspect(), config

    def test_cm5_dwc2_is_ignored_and_zero2_setting_is_missing(self):
        result, _ = self.inspect("[cm5]\ndtoverlay=dwc2,dr_mode=host\n")
        self.assertIn(REQUIRED_SETTINGS[1], result.missing)
        self.assertEqual([item.setting for item in result.ignored], [REQUIRED_SETTINGS[1]])

    def test_all_section_with_all_settings(self):
        result, _ = self.inspect("[all]\n" + "\n".join(REQUIRED_SETTINGS) + "\n")
        self.assertEqual(result.missing, [])

    def test_pi0_section_applies_to_zero2w(self):
        result, _ = self.inspect("[pi0]\n" + "\n".join(REQUIRED_SETTINGS) + "\n")
        self.assertEqual(result.missing, [])

    def test_pi0w_and_pi02_sections_apply_to_zero2w(self):
        text = (
            f"[pi0w]\n{REQUIRED_SETTINGS[0]}\n{REQUIRED_SETTINGS[1]}\n"
            f"[pi02]\n{REQUIRED_SETTINGS[2]}\n"
        )
        result, _ = self.inspect(text)
        self.assertEqual(result.missing, [])

    def test_applicable_peripheral_mode_is_a_conflict(self):
        with self.assertRaisesRegex(InspectionError, "conflicting applicable setting"):
            self.inspect("[all]\ndtoverlay=dwc2,dr_mode=peripheral\n")

    def test_second_run_does_not_append_duplicates(self):
        result, config = self.inspect("[cm5]\ndtoverlay=dwc2,dr_mode=host\n[all]\n")
        block = (
            "# BEGIN ASTROEQ SOLAR AUTO GUIDER INSTALLER\n"
            "[all]\n"
            + "\n".join(result.missing)
            + "\n# END ASTROEQ SOLAR AUTO GUIDER INSTALLER\n"
        )
        config.write_text(config.read_text(encoding="utf-8") + block, encoding="utf-8")
        second = ConfigInspector(config).inspect()
        self.assertEqual(second.missing, [])
        final_text = config.read_text(encoding="utf-8")
        for setting in REQUIRED_SETTINGS:
            # The ignored CM5 dwc2 plus the applicable installer dwc2 is expected.
            expected = 2 if setting == REQUIRED_SETTINGS[1] else 1
            self.assertEqual(final_text.count(setting), expected)

    def test_active_include_is_expanded_in_place(self):
        result, _ = self.inspect(
            "[all]\ninclude extra.txt\n",
            {"extra.txt": "\n".join(REQUIRED_SETTINGS) + "\n"},
        )
        self.assertEqual(result.missing, [])
        self.assertEqual(len(result.included_files), 1)

    def test_include_in_cm5_inherits_nonmatching_filter(self):
        result, _ = self.inspect(
            "[cm5]\ninclude cm5.txt\n[all]\n",
            {"cm5.txt": "\n".join(REQUIRED_SETTINGS) + "\n"},
        )
        self.assertEqual(result.missing, list(REQUIRED_SETTINGS))
        self.assertEqual(len(result.included_files), 1)
        self.assertEqual(len(result.ignored), len(REQUIRED_SETTINGS))

    def test_include_can_reset_cm5_filter_to_all(self):
        result, _ = self.inspect(
            "[cm5]\ninclude common.txt\n",
            {"common.txt": "[all]\n" + "\n".join(REQUIRED_SETTINGS) + "\n"},
        )
        self.assertEqual(result.missing, [])

    def test_unknown_combined_filter_stops_for_managed_setting(self):
        with self.assertRaisesRegex(InspectionError, "cannot determine whether"):
            self.inspect("[EDID=ABC-Display]\n[pi0]\ndtparam=i2c_arm=on\n")

    def test_known_nonmatching_model_overrides_unknown_to_skip(self):
        result, _ = self.inspect(
            "[EDID=ABC-Display]\n[cm5]\ndtoverlay=dwc2,dr_mode=host\n[all]\n"
        )
        self.assertIn(REQUIRED_SETTINGS[1], result.missing)

    def test_recursive_include_stops(self):
        with self.assertRaisesRegex(InspectionError, "recursive include"):
            self.inspect("include loop.txt\n", {"loop.txt": "include config.txt\n"})


if __name__ == "__main__":
    unittest.main()
