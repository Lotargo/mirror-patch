import os
import json
import struct
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
from patcher.agy import aicode


class NativePatchTests(unittest.TestCase):
    def test_grpc_patch_targets_existing_read_only_host_and_native_epilogue(self):
        fixture = Path(__file__).resolve().parents[1] / "test_cache/1.5.9__agy_cli_windows_x64.zip extracted/antigravity.exe"
        if not fixture.is_file():
            self.skipTest("Optional audited Windows CLI fixture")
        data = aicode.patch_native(fixture.read_bytes())
        pe = struct.unpack_from("<I", data, 0x3c)[0]
        opt = pe + 24
        base = struct.unpack_from("<Q", data, opt + 24)[0]
        count = struct.unpack_from("<H", data, pe + 6)[0]
        opt_size = struct.unpack_from("<H", data, pe + 20)[0]
        sections = [struct.unpack_from("<IIII", data, opt + opt_size + i * 40 + 8) for i in range(count)]
        def offset(va):
            return next(at + va - base - rva for _, rva, size, at in sections
                        if base + rva <= va < base + rva + size)
        at, va = 0x2742C9F, 0x14274369F
        self.assertEqual(data[at:at+3], b"\x48\x8d\x05")
        host = va + 7 + struct.unpack_from("<i", data, at + 3)[0]
        size = struct.unpack_from("<I", data, at + 8)[0]
        self.assertEqual(data[offset(host):offset(host)+size], b"aicode.googleapis.com:443")
        epilogue = va + 17 + struct.unpack_from("<i", data, at + 13)[0]
        self.assertEqual(data[offset(epilogue):offset(epilogue)+6], bytes.fromhex("4883c4605dc3"))

    def test_unknown_build_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "pristine official"):
            aicode.patch_native(b"MZ not the pinned upstream")

    def test_official_fixture_preserves_size_and_only_changes_reviewed_windows(self):
        fixture = Path(__file__).resolve().parents[1] / "test_cache/manager-gate-1.5.9-2026-11-15/agy.pristine-test.exe"
        if not fixture.exists():
            self.skipTest("Official binary fixture is intentionally not committed")
        original = fixture.read_bytes()
        result = aicode.patch_native(original)
        self.assertEqual(len(result), len(original))
        self.assertEqual(aicode.digest(result), "8c033def5d690d24bd8d2552517a7086965858e374d88091071466e263d09cec")
        restored = bytearray(result)
        for at, before, after in aicode.PATCHES:
            replacement = bytes.fromhex(after)
            self.assertEqual(result[at:at + len(replacement)], replacement)
            restored[at:at + len(replacement)] = bytes.fromhex(before)
        self.assertEqual(restored, original)
        with self.assertRaises(ValueError):
            aicode.patch_native(result)


@unittest.skipUnless(os.name == "nt", "Windows-only installer")
class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "agy.exe"
        self.target.write_bytes(b"previous installed agy")
        self.source = Path(self.tmp.name) / "official.exe"
        self.source.write_bytes(b"official fixture")
        self.native = b"patched native fixture"
        self.addCleanup(patch.stopall)
        patch.object(aicode, "patch_native", return_value=self.native).start()
        self.build = patch.object(aicode, "build_launcher", side_effect=lambda output, _: output.write_bytes(b"launcher fixture")).start()

    def install(self):
        return aicode.install(self.target, self.source)

    def test_install_is_idempotent_and_restore_preserves_historical_backup(self):
        historical = self.target.with_suffix(".exe.agybak")
        historical.write_bytes(b"historical pristine")
        self.assertEqual(self.install()["state"], "installed")
        from patcher.agy.patcher import get_status, do_patch_agy
        self.assertEqual(get_status(str(self.target))[0], "patched")
        with patch("patcher.agy.patcher._mapped", side_effect=AssertionError("must not apply legacy gates")):
            do_patch_agy(str(self.target))
        self.assertEqual(self.install()["state"], "installed")
        self.assertEqual(self.build.call_count, 1)
        self.assertEqual(aicode.restore(self.target)["state"], "restored")
        self.assertEqual(self.target.read_bytes(), b"previous installed agy")
        self.assertEqual(historical.read_bytes(), b"historical pristine")
        self.assertEqual(aicode.status(self.target)["state"], "not-installed")

    def test_tampered_companion_blocks_launch_status_and_restore(self):
        self.install()
        _, native, _ = aicode.paths(self.target)
        native.write_bytes(b"tampered")
        self.assertEqual(aicode.status(self.target)["state"], "modified")
        from patcher.agy.patcher import get_status
        self.assertEqual(get_status(str(self.target))[0], "unknown")
        with self.assertRaises(ValueError):
            aicode.restore(self.target)
        with self.assertRaises(ValueError):
            self.install()

    def test_failed_install_restores_previous_executable(self):
        real_write = aicode.atomic_write

        def fail_target(path, data):
            if path == self.target:
                raise PermissionError("locked target")
            real_write(path, data)

        with patch.object(aicode, "atomic_write", side_effect=fail_target):
            with self.assertRaises(PermissionError):
                self.install()
        self.assertEqual(self.target.read_bytes(), b"previous installed agy")
        self.assertTrue(all(not p.exists() for p in aicode.paths(self.target)))

    def test_orphan_backup_is_never_overwritten(self):
        backup, _, _ = aicode.paths(self.target)
        backup.write_bytes(b"valuable original")
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(backup.read_bytes(), b"valuable original")

    def test_upgrade_keeps_original_backup_and_updates_native(self):
        self.install()
        backup, native, manifest_path = aicode.paths(self.target)
        old = json.loads(manifest_path.read_text())
        old["patch_version"] = 1
        old.pop("runtime_sha256", None)
        manifest_path.write_text(json.dumps(old))
        with patch.object(aicode, "patch_native", return_value=b"quota-enabled native"):
            self.assertEqual(self.install()["state"], "installed")
        self.assertEqual(native.read_bytes(), b"quota-enabled native")
        self.assertEqual(backup.read_bytes(), b"previous installed agy")
        self.assertEqual(aicode.restore(self.target)["state"], "restored")
        self.assertEqual(self.target.read_bytes(), b"previous installed agy")

    def test_upgrade_rolls_back_when_launcher_replacement_fails(self):
        self.install()
        backup, native, manifest_path = aicode.paths(self.target)
        old = json.loads(manifest_path.read_text())
        old["patch_version"] = 1
        manifest_path.write_text(json.dumps(old))
        real_write = aicode.atomic_write
        def fail_launcher(path, data):
            if path == self.target:
                raise PermissionError("active launcher")
            real_write(path, data)
        with patch.object(aicode, "patch_native", return_value=b"new native"):
            with patch.object(aicode, "atomic_write", side_effect=fail_launcher):
                with self.assertRaises(PermissionError):
                    self.install()
        self.assertEqual(native.read_bytes(), self.native)
        self.assertEqual(aicode.status(self.target)["state"], "installed")
        self.assertEqual(backup.read_bytes(), b"previous installed agy")

    def test_v2_and_v3_upgrades_preserve_original_backup(self):
        self.install()
        backup, native, manifest_path = aicode.paths(self.target)
        for version in (2, 3):
            with self.subTest(version=version):
                manifest = json.loads(manifest_path.read_text())
                manifest["patch_version"] = version
                manifest_path.write_text(json.dumps(manifest))
                updated = f"grpc-separated native from v{version}".encode()
                with patch.object(aicode, "patch_native", return_value=updated):
                    self.assertEqual(self.install()["patch_version"], aicode.PATCH_VERSION)
                self.assertEqual(native.read_bytes(), updated)
                self.assertEqual(backup.read_bytes(), b"previous installed agy")

    def test_profile_change_is_not_an_implicit_upgrade(self):
        self.install()
        with self.assertRaises(ValueError):
            aicode.install(self.target, self.source, profile="hub")


if __name__ == "__main__":
    unittest.main()
