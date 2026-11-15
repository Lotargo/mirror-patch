import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
from patcher.agy import aicode


class NativePatchTests(unittest.TestCase):
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
        self.assertEqual(aicode.digest(result), "f4aa5af8dec3ce520d801411f6a52e9f3dcfe9014d1285c98715519623cef7f9")
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


if __name__ == "__main__":
    unittest.main()
