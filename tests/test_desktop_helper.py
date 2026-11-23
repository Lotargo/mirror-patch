"""Repair contracts use temporary files; installed applications are never changed."""

import hashlib
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

    def test_verified_auto_update_is_healed_with_recovery_and_sidecar_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agy.exe"
            backup, native, manifest = helper.aicode.paths(target)
            original, patched, launcher = b"original", b"native patched", b"launcher"
            upgraded = b"official auto update"
            sidecar = target.with_name(helper.AUTO_UPDATE_SIDECAR)
            record = {
                "schemaVersion": 1, "version": "1.5.11", "size": len(upgraded),
                "mtimeMs": 1, "ino": 1, "installedAt": 1,
                "sha512": hashlib.sha512(upgraded).hexdigest(),
                "releaseBaseUrl": f"https://{helper.AUTO_UPDATE_HOST}/manifests/windows_amd64.json",
            }
            target.write_bytes(upgraded)
            sidecar.write_text(json.dumps(record))
            backup.write_bytes(original)
            native.write_bytes(patched)
            manifest.write_text(json.dumps({
                "profile": "hub", "patch_version": 4,
                "previous_sha256": helper.aicode.digest(original),
                "native_sha256": helper.aicode.digest(patched),
                "launcher_sha256": helper.aicode.digest(launcher),
                "runtime_sha256": "old",
            }))

            def build(output, *args, **kwargs):
                output.write_bytes(launcher)

            with (
                patch.object(helper.aicode, "SOURCE_SHA256", helper.aicode.digest(original)),
                patch.object(helper.aicode, "PREBUILT_NATIVE_HASHES",
                             {"hub": (helper.aicode.digest(patched),)}),
                patch.object(helper.aicode, "runtime_fingerprint", return_value="new"),
                patch.object(helper.aicode, "build_launcher", side_effect=build),
            ):
                self.assertEqual(helper.native_info(target, "hub"), ("modified", True))
                self.assertEqual(helper.auto_update_info(target)["version"], "1.5.11")
                result = helper.repair_native(target, "hub")
                self.assertEqual(result["state"], "installed")
            self.assertEqual(target.read_bytes(), launcher)
            recovered = json.loads(sidecar.read_text())
            self.assertEqual(recovered["version"], "1.5.11")
            self.assertEqual(recovered["size"], len(launcher))
            self.assertEqual(recovered["sha512"], hashlib.sha512(launcher).hexdigest())
            metadata = json.loads(manifest.read_text())
            self.assertEqual(metadata["auto_update_replaced_version"], "1.5.11")
            self.assertTrue(metadata["sidecar_synced"])
            backups = [p for p in Path(directory).glob("*.before-heal-*")]
            self.assertEqual(len(backups), 2)
            self.assertIn(upgraded, [p.read_bytes() for p in backups])

    def test_unverified_auto_update_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agy.exe"
            backup, native, manifest = helper.aicode.paths(target)
            upgraded = b"forged update"
            sidecar = target.with_name(helper.AUTO_UPDATE_SIDECAR)
            target.write_bytes(upgraded)
            sidecar.write_text(json.dumps({
                "schemaVersion": 1, "version": "1.5.11", "size": len(upgraded),
                "sha512": hashlib.sha512(upgraded).hexdigest(),
                "releaseBaseUrl": "https://evil.example/release.json",
            }))
            backup.write_bytes(b"original")
            native.write_bytes(b"native patched")
            manifest.write_text(json.dumps({
                "profile": "hub", "patch_version": 4,
                "previous_sha256": helper.aicode.digest(b"original"),
                "native_sha256": helper.aicode.digest(b"native patched"),
                "launcher_sha256": helper.aicode.digest(b"launcher"),
                "runtime_sha256": "old",
            }))
            self.assertIsNone(helper.auto_update_info(target))
            self.assertEqual(helper.native_info(target, "hub"), ("modified", False))
            with self.assertRaises(ValueError):
                helper.repair_native(target, "hub")
            self.assertEqual(target.read_bytes(), upgraded)

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
