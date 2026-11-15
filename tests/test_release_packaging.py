import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
from patcher.agy import aicode
from patcher.cli import show_about
from patcher.constants import DONATION_WALLET, PROJECT_REPO
from patcher.utils import console, update


class ReleasePackagingTests(unittest.TestCase):
    def test_bundled_launcher_needs_no_compiler_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "main.go").write_text("package main\n")
            prebuilt = runtime / "prebuilt"
            prebuilt.mkdir()
            native_hash = aicode.PREBUILT_NATIVE_HASHES["hub"][0]
            name = f"hub-{native_hash}.exe"
            data = b"MZ audited fixture"
            (prebuilt / name).write_bytes(data)
            with patch.object(aicode, "RUNTIME", runtime):
                manifest = {"runtime_sha256": aicode.runtime_fingerprint(), "assets": {name: aicode.digest(data)}}
                (prebuilt / "manifest.json").write_text(json.dumps(manifest))
                output = runtime / "output.exe"
                with patch.object(aicode.shutil, "which", side_effect=AssertionError("compiler must not be used")):
                    aicode.build_launcher(output, native_hash, profile="hub")
                self.assertEqual(output.read_bytes(), data)
                (prebuilt / name).write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    aicode.build_launcher(output, native_hash, profile="hub")

    def test_frozen_missing_launchers_fails_without_using_go(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(aicode, "RUNTIME", Path(directory)), patch.object(sys, "frozen", True, create=True):
                with self.assertRaisesRegex(ValueError, "missing"):
                    aicode.build_launcher(Path(directory) / "out.exe", aicode.PREBUILT_NATIVE_HASHES["cli"][0])

    def test_source_selection_preserves_existing_manager_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "language_server.exe"
            target.write_bytes(b"launcher")
            before = target.with_name(target.name + ".before-aicode")
            before.write_bytes(b"previous gate")
            original = target.with_name(target.name + ".agybak")
            original.write_bytes(b"original")
            accepted = {aicode.digest(before.read_bytes()), aicode.digest(original.read_bytes())}
            with patch.object(aicode, "MANAGER_SOURCE_HASHES", accepted):
                self.assertEqual(aicode.resolve_source(target, "manager"), before)

    def test_unknown_manager_never_downloads_a_different_product(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "language_server.exe"
            target.write_bytes(b"unknown build")
            with patch.object(aicode.urllib.request, "urlopen", side_effect=AssertionError("must not download")):
                with self.assertRaises(ValueError):
                    aicode.resolve_source(target, "manager")

    def test_bad_official_download_is_not_cached_or_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agy.exe"
            target.write_bytes(b"unknown")
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                with patch.object(aicode.urllib.request, "urlopen", return_value=io.BytesIO(b"bad download")):
                    with self.assertRaisesRegex(ValueError, "checksum/size"):
                        aicode.resolve_source(target, "cli")
            self.assertEqual(target.read_bytes(), b"unknown")
            self.assertEqual(list((Path(directory) / "MirrorPatch/cache").iterdir()), [])

    def test_about_and_update_links_point_to_this_project(self):
        self.assertEqual(update.GITHUB_REPO, PROJECT_REPO)
        output = io.StringIO()
        with patch("patcher.cli.clear_screen"), contextlib.redirect_stdout(output):
            show_about()
        text = output.getvalue()
        self.assertIn(PROJECT_REPO, text)
        self.assertIn(DONATION_WALLET, text)
        self.assertNotIn("t.me/", text)
        self.assertNotIn("2202 2050", text)


if __name__ == "__main__":
    unittest.main()
