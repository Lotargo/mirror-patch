"""Repair contracts use temporary files; installed applications are never changed."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from patcher import desktop_helper as helper


class DesktopHelperTests(unittest.TestCase):
    def test_native_loss_known_source_repaired_unknown_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agy.exe"
            backup, native, manifest = helper.aicode.paths(target)
            original, patched, launcher = b"original", b"native patched", b"launcher"
            target.write_bytes(original)
            backup.write_bytes(original)
            native.write_bytes(patched)
            metadata = {
                "profile": "cli",
                "patch_version": 4,
                "previous_sha256": helper.aicode.digest(original),
                "native_sha256": helper.aicode.digest(patched),
                "launcher_sha256": helper.aicode.digest(launcher),
                "runtime_sha256": "old",
            }
            manifest.write_text(json.dumps(metadata))

            def build(output, *args, **kwargs):
                output.write_bytes(launcher)

            with (
                patch.object(
                    helper.aicode, "SOURCE_SHA256", helper.aicode.digest(original)
                ),
                patch.object(
                    helper.aicode,
                    "PREBUILT_NATIVE_HASHES",
                    {"cli": [helper.aicode.digest(patched)]},
                ),
                patch.object(helper.aicode, "runtime_fingerprint", return_value="new"),
                patch.object(helper.aicode, "build_launcher", side_effect=build),
            ):
                self.assertEqual(helper.native_info(target, "cli"), ("modified", True))
                helper.repair_native(target, "cli")
                self.assertEqual(target.read_bytes(), launcher)
                self.assertEqual(backup.read_bytes(), original)
                self.assertEqual(helper.native_info(target, "cli"), ("patched", True))
                target.write_bytes(b"unknown upgrade")
                self.assertEqual(helper.native_info(target, "cli"), ("modified", False))
                with self.assertRaises(ValueError):
                    helper.repair_native(target, "cli")
                self.assertEqual(target.read_bytes(), b"unknown upgrade")

    def test_transaction_rolls_back_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a", Path(directory) / "b"
            first.write_bytes(b"old")
            atomic = helper.aicode.atomic_write

            def write(path, data):
                if path == second:
                    raise PermissionError("locked fixture")
                atomic(path, data)

            with patch.object(helper.aicode, "atomic_write", side_effect=write):
                with self.assertRaises(PermissionError):
                    helper.transaction([(first, b"new"), (second, b"new")])
            self.assertEqual(first.read_bytes(), b"old")
            self.assertFalse(second.exists())

    def test_shared_dependency_healed_once_and_failure_reported(self):
        component = {
            "id": "ide_hub",
            "path": __file__,
            "installed": True,
            "status": "modified",
            "can_heal": True,
        }
        report = {
            "protocol": 1,
            "modules": [
                {
                    "id": mid,
                    "installed": True,
                    "components": [component],
                    "status": "modified",
                }
                for mid in ["ide", "vscode", "cli"]
            ],
        }
        with (
            patch.object(helper, "inspect", return_value=report),
            patch.object(
                helper, "apply", side_effect=PermissionError("fixture")
            ) as apply,
        ):
            result = helper._heal("all")
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(len(result["actions"]), 1)
        self.assertTrue(all(m["status"] == "error" for m in result["modules"]))

    def test_modern_extension_and_shared_profile_are_not_false_failures(self):
        raw = {
            "components": [
                {
                    "id": "ide",
                    "installed": False,
                    "required": False,
                    "status": "missing",
                },
                {
                    "id": "vscode_ext",
                    "path": __file__,
                    "installed": True,
                    "required": True,
                    "mode": "hub",
                    "status": "not_applicable",
                },
                {
                    "id": "ide_hub",
                    "path": __file__,
                    "installed": True,
                    "required": True,
                    "mode": "hub",
                    "status": "patched",
                },
            ]
        }
        with (
            patch.object(helper.diagnostics, "get_components_status", return_value=raw),
            patch.object(helper, "native_info", return_value=("patched", True)),
        ):
            result = helper.inspect()
        vscode = next(m for m in result["modules"] if m["id"] == "vscode")
        self.assertEqual(vscode["status"], "patched")
        self.assertIn("общий Hub", vscode["components"][0]["detail"])
        self.assertEqual(
            helper.native_profile({"id": "agy", "shared_with": "ide_hub"}), "hub"
        )


if __name__ == "__main__":
    unittest.main()
