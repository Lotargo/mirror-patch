"""Native routing is an explicit verified choice, never an inferred entitlement."""

import tempfile
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from patcher import diagnostics, desktop_helper
from patcher.agy import official, aicode


class OfficialRoutingTests(unittest.TestCase):
    def updater_fixture(self, directory):
        target = Path(directory) / "agy.exe"
        backup, native, manifest = aicode.paths(target)
        target.write_bytes(b"launcher")
        backup.write_bytes(b"old original")
        native.write_bytes(b"patched companion")
        data = {"profile": "hub", "patch_version": 4,
                "launcher_sha256": aicode.digest(target.read_bytes()),
                "previous_sha256": aicode.digest(backup.read_bytes()),
                "native_sha256": aicode.digest(native.read_bytes()),
                "auto_update_replaced_version": "1.5.11"}
        manifest.write_text(json.dumps(data))
        stamp = "20261124072000000000"
        updated = target.with_name("agy.exe.before-heal-1.5.11-" + stamp)
        updated.write_bytes(b"MZ newer official recovery")
        record = {"schemaVersion": 1, "version": "1.5.11", "size": updated.stat().st_size,
                  "sha512": hashlib.sha512(updated.read_bytes()).hexdigest(),
                  "releaseBaseUrl": "https://" + official.AUTO_UPDATE_HOST + "/release.json"}
        meta = target.with_name("agy.version.json.before-heal-1.5.11-" + stamp)
        meta.write_text(json.dumps(record))
        target.with_name("agy.version.json").write_bytes(b"old live sidecar")
        return target, updated, meta

    def test_restore_uses_verified_official_update_and_preserves_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            target, updated, meta = self.updater_fixture(directory)
            result = aicode.restore(target)
            self.assertEqual(result["native_version"], "1.5.11")
            self.assertEqual(target.read_bytes(), updated.read_bytes())
            self.assertTrue(updated.is_file() and meta.is_file())
            self.assertTrue(official.inspect(target)["verified"])
            record = json.loads(target.with_name("agy.version.json").read_text())
            self.assertEqual(record["sha512"], hashlib.sha512(target.read_bytes()).hexdigest())
            self.assertEqual(record["size"], target.stat().st_size)

    def test_bad_update_pair_is_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            target, updated, _ = self.updater_fixture(directory)
            updated.write_bytes(b"changed update")
            with self.assertRaises(ValueError):
                aicode.restore(target)
            self.assertEqual(target.read_bytes(), b"launcher")
            self.assertEqual(aicode.status(target)["state"], "installed")

    def test_restore_rolls_back_if_sidecar_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as directory:
            target, _, _ = self.updater_fixture(directory)
            sidecar = target.with_name("agy.version.json")
            write = aicode.atomic_write
            def fail_sidecar(path, data):
                if path == sidecar:
                    raise PermissionError("locked sidecar")
                write(path, data)
            with patch.object(aicode, "atomic_write", side_effect=fail_sidecar):
                with self.assertRaises(PermissionError):
                    aicode.restore(target)
            self.assertEqual(target.read_bytes(), b"launcher")
            self.assertEqual(sidecar.read_bytes(), b"old live sidecar")
            self.assertEqual(aicode.status(target)["state"], "installed")

    def test_record_verifies_native_and_preserves_choice_after_file_change(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "language_server.exe"
            target.write_bytes(b"verified native fixture")
            self.assertIsNone(official.inspect(target))
            self.assertTrue(official.record(target, "manager")["verified"])
            self.assertEqual(desktop_helper.native_info(target, "manager"), ("not_required", False))
            with self.assertRaises(ValueError):
                desktop_helper.repair_native(target, "manager")
            target.write_bytes(b"updated native fixture")
            self.assertFalse(official.inspect(target)["verified"])
            self.assertEqual(desktop_helper.native_info(target, "manager"), ("modified", False))

    def test_diagnostics_and_force_heal_do_not_reinstall_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "language_server.exe"
            target.write_bytes(b"native fixture")
            official.record(target, "manager")
            component = diagnostics._component("manager", "Desktop")
            scanner = Mock(side_effect=AssertionError("legacy scan should not override explicit native routing"))
            diagnostics._inspect_binary(component, str(target), scanner, {})
            self.assertEqual(component["status"], "not_applicable")
            self.assertFalse(component["can_apply"])
            self.assertTrue(diagnostics.summarize_components([component])["all_patched"])
            with patch.object(diagnostics, "get_components_status", return_value={"components": [component]}), patch.object(desktop_helper, "apply", side_effect=AssertionError("native choice was overwritten")):
                result = desktop_helper._heal("desktop")
            self.assertEqual(result["actions"], [])
            self.assertEqual(next(m for m in result["modules"] if m["id"] == "desktop")["status"], "patched")
            self.assertEqual(target.read_bytes(), b"native fixture")

    def test_invalid_policy_does_not_allow_automatic_compatibility_install(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agy.exe"
            target.write_bytes(b"native fixture")
            official.marker_path(target).write_text('{"schema_version":999}')
            self.assertFalse(official.inspect(target)["verified"])
            self.assertEqual(desktop_helper.native_info(target, "hub"), ("modified", False))
