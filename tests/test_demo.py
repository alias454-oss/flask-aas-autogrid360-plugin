# app/plugins/autogrid360/tests/test_demo.py
"""Standalone deterministic demo-backup generator contract tests."""

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PLUGIN_ROOT / "scripts" / "demo_listing_generator.py"


class AutoGrid360DemoBundleTests(unittest.TestCase):
    def _run_demo(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DEMO_SCRIPT), *args],
            cwd=PLUGIN_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_multi_seller_demo_bundle_has_expected_lifecycle_mix(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "demo-site.zip"
            result = self._run_demo(
                "--num",
                "100",
                "--seller",
                "seller-a",
                "--seller",
                "seller-b",
                "--seed",
                "454",
                "--anchor-date",
                "2026-08-14",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with zipfile.ZipFile(output, "r") as archive:
                manifest = json.loads(archive.read("manifest.json"))

            self.assertEqual(manifest["format"], "autogrid360-inventory")
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(manifest["scope"], "site")
            self.assertEqual(
                [seller["username"] for seller in manifest["sellers"]],
                ["seller-a", "seller-b"],
            )
            self.assertEqual(len(manifest["listings"]), 100)
            self.assertEqual(
                Counter(row["source"]["status"] for row in manifest["listings"]),
                Counter(
                    {
                        "active": 70,
                        "sale_pending": 10,
                        "sold": 10,
                        "pending": 4,
                        "draft": 2,
                        "expired": 2,
                        "removed": 2,
                    }
                ),
            )
            self.assertEqual(
                {row["seller_username"] for row in manifest["listings"]},
                {"seller-a", "seller-b"},
            )

    def test_demo_cycle_has_multiple_models_per_make(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "demo-models.zip"
            result = self._run_demo(
                "--num",
                "18",
                "--seller",
                "seller-a",
                "--seed",
                "454",
                "--anchor-date",
                "2026-08-14",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with zipfile.ZipFile(output, "r") as archive:
                manifest = json.loads(archive.read("manifest.json"))

            models_by_make: dict[str, set[str]] = {}
            for row in manifest["listings"]:
                vehicle = row["vehicle"]
                models_by_make.setdefault(vehicle["make_key"], set()).add(
                    vehicle["model"]["key"]
                )

            self.assertEqual(set(models_by_make), {
                "chevrolet",
                "ford",
                "honda",
                "jeep",
                "subaru",
                "toyota",
            })
            self.assertTrue(
                all(len(models) == 3 for models in models_by_make.values())
            )

    def test_fixed_seed_and_anchor_produce_byte_stable_image_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "demo-a.zip"
            second = Path(temporary) / "demo-b.zip"
            common = (
                "--num",
                "2",
                "--seller",
                "seller-a",
                "--images",
                "1",
                "--seed",
                "454",
                "--anchor-date",
                "2026-08-14",
            )

            first_result = self._run_demo(*common, str(first))
            second_result = self._run_demo(*common, str(second))

            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first, "r") as archive:
                manifest = json.loads(archive.read("manifest.json"))
                image_names = sorted(
                    name for name in archive.namelist() if name.startswith("images/")
                )
                self.assertEqual(manifest["scope"], "seller")
                self.assertNotIn("seller_username", manifest["listings"][0])
                self.assertEqual(len(image_names), 2)
                self.assertTrue(archive.read(image_names[0]).startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
