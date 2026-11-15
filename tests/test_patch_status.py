"""Регрессии статусов, обнаружения Hub и фонового применения на временных файлах."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

import autopatch
from patcher import diagnostics as doctor
from patcher.agy import patcher as agy
from patcher.manager import patcher as manager
from patcher.ide import patcher as ide
from patcher.ide.discovery import get_ag_version
from patcher.vscode import discovery as extensions
from patcher.vscode import patcher as vscode
from patcher.utils.runtime import unattended


CLI_ORIGINAL = bytes.fromhex("48 85 c0 0f 84 11 22 33 44 80 78 08 00 0f 85 11 22 33 44")
MANAGER_ORIGINAL = bytes.fromhex("80 78 08 00 74 10 48 8b 44 24 28 48 89 41 60")
IDE_ORIGINAL = "resetIsTierGCPTos(),this.auth.isGoogleInternal"
EXT_ORIGINAL = vscode.PART1_ANCHOR + "\nconst isChannelChanged = manifestFetched && lastInstalledUrl !== releaseBaseUrl;"


def patched_bytes(original, gate):
    data = bytearray(original)
    data[gate.offset:gate.offset + len(gate.fix)] = gate.fix
    return bytes(data)


CLI_PATCHED = patched_bytes(CLI_ORIGINAL, agy.CLI_GATE_X64)
MANAGER_PATCHED = patched_bytes(MANAGER_ORIGINAL, manager.MANAGER_GATE_X64)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agy-status-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return str(path)

    def targets(self):
        return dict(ide="", manager="", agy="", vscode_ext="", vscode_bin="", hub="")

    def inventory(self, **overrides):
        targets = self.targets()
        targets.update(overrides)
        return doctor.get_components_status(targets)

    def modern_ide(self):
        main = self.write("ide/resources/app/out/main.js", ide.IDE_DONE)
        self.write("ide/resources/app/product.json", json.dumps({"ideVersion": "2.7.0", "version": "1.107.0"}))
        self.write("ide/resources/app/extensions/agy/package.json", json.dumps({
            "name": "google-antigravity", "publisher": "google", "version": "1.11.0",
            "main": "./dist/extension.js",
        }))
        self.write("ide/resources/app/extensions/agy/dist/extension.js", 'module.exports=require("../vscode/extension.js");')
        implementation = self.write("ide/resources/app/extensions/agy/vscode/extension.js", 'spawn(binary, ["--hub"]);')
        return main, implementation


class GateStatusTests(Fixture):
    def test_known_original_and_patched_cli(self):
        for data, expected in ((CLI_ORIGINAL * 2, "unpatched"), (CLI_PATCHED * 2, "patched")):
            with self.subTest(expected=expected):
                path = self.write("agy.bin", data)
                self.assertEqual(agy.get_status(path)[0], expected)

    def test_mixed_cli_occurrences_are_partial(self):
        path = self.write("agy.bin", CLI_ORIGINAL + b"\0" * 32 + CLI_PATCHED)
        self.assertEqual(agy.get_status(path)[0], "partial")
        self.assertFalse(agy.is_already_patched(path))

    def test_different_cli_variants_are_combined(self):
        first = agy.Gate(b"ONE0", b"ONE1", b"1", offset=3)
        second = agy.Gate(b"TWO0", b"TWO1", b"1", offset=3)
        gates = [(agy.MultiGate(first, second), "test")]
        with patch.object(agy, "ALL_GATES", gates):
            path = self.write("agy.bin", b"ONE1---TWO0")
            self.assertEqual(agy.get_status(path)[0], "partial")
            self.assertEqual(len(gates[0][0].resolve_all(b"ONE0---TWO0")), 2)

    def test_mixed_manager_occurrences_are_partial(self):
        path = self.write("manager.bin", MANAGER_ORIGINAL + b"\0" * 32 + MANAGER_PATCHED)
        self.assertEqual(manager.get_status(path)[0], "partial")
        self.assertFalse(manager.is_already_patched(path))

    def test_duplicate_patched_manager_is_ambiguous(self):
        path = self.write("manager.bin", MANAGER_PATCHED * 2)
        self.assertEqual(manager.get_status(path)[0], "unknown")
        self.assertFalse(manager.is_already_patched(path))

    def test_manager_rejects_two_matching_variants(self):
        first = manager.Gate(b"ONE0", b"ONE1", b"ONE1")
        second = manager.Gate(b"TWO0", b"TWO1", b"TWO1")
        with self.assertRaises(LookupError):
            manager.MultiGate(first, second).resolve(b"ONE1---TWO1")

    def test_read_failures_remain_distinct_from_unknown(self):
        for module in (agy, manager):
            with self.subTest(module=module.__name__):
                with patch.object(module, "_mapped", side_effect=PermissionError("access denied")):
                    self.assertEqual(module.get_status("present")[0], "error")
                    with self.assertRaises(PermissionError):
                        module.get_status("present", strict=True)
                self.assertEqual(module.get_status(str(self.root / "absent"))[0], "missing")

    def test_mixed_ide_markers_are_partial(self):
        self.assertEqual(ide.get_status(IDE_ORIGINAL + ide.IDE_DONE), "partial")
        self.assertFalse(ide.is_already_patched(IDE_ORIGINAL + ide.IDE_DONE))
        self.assertEqual(ide.get_status("unrelated script"), "unknown")

    def test_extension_requires_both_parts_without_original_second_part(self):
        self.assertEqual(vscode.get_status(EXT_ORIGINAL), "unpatched")
        data, _ = vscode._apply_part1(EXT_ORIGINAL)
        self.assertEqual(vscode.get_status(data), "partial")
        data, _ = vscode._apply_part2(data)
        self.assertEqual(vscode.get_status(data), "patched")
        self.assertEqual(vscode.get_status(data + EXT_ORIGINAL), "partial")
        self.assertEqual(vscode.get_status("minified unrelated code"), "unknown")

    def test_extension_does_not_hide_an_unmodified_installer_copy(self):
        data, _ = vscode._apply_part1(EXT_ORIGINAL)
        data, _ = vscode._apply_part2(data)
        self.assertEqual(vscode.get_status(data + vscode.PART1_ANCHOR), "partial")
        self.assertFalse(vscode.is_already_patched(data + vscode.PART1_ANCHOR))


class DiscoveryTests(Fixture):
    def test_product_ide_version_wins_over_vscode_core_version(self):
        main, _ = self.modern_ide()
        self.assertEqual(get_ag_version(main)[0], "2.7.0")

    def test_manifest_entry_and_hub_delegate_are_resolved(self):
        main, implementation = self.modern_ide()
        directory = extensions.find_bundled_extension_dir(main)
        info = extensions.inspect_extension_dir(directory)
        self.assertEqual(info["path"], implementation)
        self.assertTrue(info["entry_path"].endswith(os.path.join("dist", "extension.js")))
        self.assertEqual(info["mode"], "hub")
        self.assertEqual(info["version"], "1.11.0")
        self.assertEqual(extensions.resolve_extension_path(directory), implementation)

    def test_manifest_dist_entry_for_standalone_vscode(self):
        self.write("extension/package.json", json.dumps({"main": "./dist/extension.js", "version": "1.0.0"}))
        entry = self.write("extension/dist/extension.js", EXT_ORIGINAL)
        with patch.object(extensions, "find_extension_dir", return_value=str(self.root / "extension")):
            self.assertEqual(extensions.find_extension_js(), entry)

    def test_legacy_extension_without_manifest_still_works(self):
        entry = self.write("extension/extension.js", EXT_ORIGINAL)
        self.assertEqual(extensions.inspect_extension_dir(str(self.root / "extension"))["path"], entry)

    def test_manifest_entry_cannot_escape_installation(self):
        self.write("outside.js", "do not load")
        for entry in ("../outside.js", str(self.root / "outside.js")):
            with self.subTest(entry=entry):
                self.write("extension/package.json", json.dumps({"main": entry}))
                with self.assertRaises(ValueError):
                    extensions.inspect_extension_dir(str(self.root / "extension"))

    def test_missing_declared_entry_does_not_fall_back_to_stale_root_script(self):
        self.write("extension/extension.js", EXT_ORIGINAL)
        self.write("extension/package.json", json.dumps({"main": "./dist/missing.js"}))
        with self.assertRaises(FileNotFoundError):
            extensions.inspect_extension_dir(str(self.root / "extension"))

    def test_hub_binary_is_agy_even_with_legacy_antigravity_present(self):
        self.write(".gemini/bin/antigravity.exe", b"old")
        with patch.object(extensions, "_user_home", return_value=str(self.root)):
            self.assertEqual(Path(extensions.expected_hub_binary()).stem, "agy")


class InventoryTests(Fixture):
    def test_no_installations_is_not_success(self):
        status = self.inventory()
        self.assertFalse(status["all_patched"])
        self.assertEqual(status["installed_count"], 0)

    def test_unknown_binary_is_unsupported_and_not_actionable(self):
        path = self.write("unknown.bin", b"new build with no known signature")
        status = self.inventory(agy=path)
        self.assertFalse(status["all_patched"])
        self.assertEqual(status["unsupported_count"], 1)
        self.assertEqual(status["unpatched_count"], 0)
        self.assertEqual(status["actionable_count"], 0)

    def test_error_in_installed_component_blocks_overall_success(self):
        mgr = self.write("manager.bin", MANAGER_PATCHED)
        cli = self.write("agy.bin", CLI_PATCHED)
        with patch.object(doctor, "agy_status", side_effect=PermissionError("unreadable")):
            status = self.inventory(manager=mgr, agy=cli)
        self.assertFalse(status["all_patched"])
        self.assertEqual(status["patched_count"], 1)
        self.assertEqual(status["error_count"], 1)
        self.assertEqual(status["installed_count"], 2)

    def test_discovery_error_also_blocks_success(self):
        targets = self.targets()
        del targets["agy"]
        with patch.object(doctor, "find_agy_binary", side_effect=RuntimeError("discovery failed")):
            status = doctor.get_components_status(targets)
        self.assertFalse(status["all_patched"])
        self.assertEqual(status["error_count"], 1)

    def test_discovered_file_disappearing_during_update_blocks_success(self):
        targets = self.targets()
        targets["manager"] = self.write("manager.bin", MANAGER_PATCHED)
        del targets["agy"]
        with patch.object(doctor, "find_agy_binary", return_value=str(self.root / "removed-by-updater")):
            status = doctor.get_components_status(targets)
        self.assertFalse(status["all_patched"])
        self.assertEqual(status["missing_count"], 1)
        self.assertEqual(status["patched_count"], 1)

    def test_modern_ide_links_extension_and_distinct_hub_backend(self):
        main, implementation = self.modern_ide()
        hub = self.write("gemini/agy.bin", CLI_PATCHED)
        cli = self.write("standalone/agy.bin", CLI_PATCHED)
        status = self.inventory(ide=main, agy=cli, hub=hub)
        indexed = {c["id"]: c for c in status["components"]}
        self.assertEqual(indexed["ide"]["profile"], "ide_hub")
        self.assertEqual(indexed["ide"]["version"], "2.7.0")
        self.assertEqual(indexed["ide"]["status"], "not_applicable")
        self.assertEqual(indexed["ide_ext"]["path"], implementation)
        self.assertEqual(indexed["ide_ext"]["version"], "1.11.0")
        self.assertEqual(indexed["ide_hub"]["path"], hub)
        self.assertEqual(indexed["ide_hub"]["launch_args"], ["--hub"])
        self.assertEqual(indexed["ide_hub"]["file_status"], "patched")
        self.assertEqual(indexed["ide_hub"]["status"], "unsupported")
        self.assertEqual(indexed["agy"]["path"], cli)
        self.assertEqual(indexed["agy"]["status"], "patched")
        self.assertFalse(status["all_patched"])
        self.assertEqual(status["auth_status"], "not_checked")
        self.assertEqual(status["actionable_count"], 0)

    def test_missing_hub_is_required(self):
        main, _ = self.modern_ide()
        status = self.inventory(ide=main, hub=str(self.root / "missing-agy"))
        hub = next(c for c in status["components"] if c["id"] == "ide_hub")
        self.assertTrue(hub["required"])
        self.assertEqual(hub["status"], "missing")
        self.assertFalse(status["all_patched"])
        self.assertGreater(status["missing_count"], 0)

    def test_corrupt_builtin_manifest_is_an_error(self):
        main, _ = self.modern_ide()
        self.write("ide/resources/app/extensions/agy/package.json", "{")
        status = self.inventory(ide=main)
        self.assertFalse(status["all_patched"])
        self.assertEqual(status["error_count"], 1)

    def test_unrecognized_modern_extension_mode_is_not_actionable(self):
        main, implementation = self.modern_ide()
        Path(implementation).write_text(EXT_ORIGINAL, encoding="utf-8")
        status = self.inventory(ide=main)
        bundled = next(c for c in status["components"] if c["id"] == "ide_ext")
        self.assertEqual(bundled["status"], "unsupported")
        self.assertFalse(bundled["can_apply"])

    def test_legacy_ide_remains_actionable(self):
        main = self.write("ide/out/main.js", IDE_ORIGINAL)
        self.write("ide/product.json", json.dumps({"ideVersion": "2.5.5"}))
        status = self.inventory(ide=main)
        self.assertEqual(status["actionable_count"], 1)
        self.assertEqual(status["unpatched_count"], 1)

    def test_legacy_patched_component_does_not_claim_authentication(self):
        status = self.inventory(agy=self.write("agy.bin", CLI_PATCHED))
        self.assertTrue(status["all_patched"])
        self.assertEqual(status["auth_status"], "not_checked")

    def test_cached_backend_without_an_extension_owner_is_not_actionable(self):
        path = self.write("cached/agy.bin", CLI_ORIGINAL)
        status = self.inventory(vscode_bin=path)
        self.assertEqual(status["actionable_count"], 0)
        self.assertEqual(status["unsupported_count"], 1)
        self.assertFalse(status["all_patched"])

    def test_cli_alias_to_hub_does_not_allow_automatic_terminal_patch(self):
        main, _ = self.modern_ide()
        path = self.write("shared/agy.bin", CLI_ORIGINAL)
        status = self.inventory(ide=main, agy=path, hub=path)
        cli = next(c for c in status["components"] if c["id"] == "agy")
        self.assertEqual(cli["shared_with"], "ide_hub")
        self.assertFalse(cli["can_apply"])
        self.assertEqual(status["actionable_count"], 0)


class BackgroundTests(Fixture):
    def run_engine(self, targets, handler=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(autopatch, "get_components_status", side_effect=lambda: doctor.get_components_status(targets)))
            stack.enter_context(patch.object(autopatch, "log"))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            if handler is not None:
                stack.enter_context(patch.object(autopatch, "do_patch_manager", handler))
            return autopatch.run_autopatch()

    def test_noop_failure_is_not_counted_as_applied(self):
        path = self.write("language_server.bin", MANAGER_ORIGINAL)
        targets = self.targets()
        targets["manager"] = path
        result = self.run_engine(targets, lambda path: None)
        self.assertEqual(result["attempted_count"], 1)
        self.assertEqual(result["applied_count"], 0)
        self.assertFalse(result["success"])
        self.assertFalse(result["repatched"])
        self.assertEqual(Path(path).read_bytes(), MANAGER_ORIGINAL)

    def test_real_patch_and_backup_are_verified_on_temporary_copy(self):
        path = self.write("language_server.bin", MANAGER_ORIGINAL)
        targets = self.targets()
        targets["manager"] = path
        result = self.run_engine(targets)
        self.assertTrue(result["success"])
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(Path(path).read_bytes(), MANAGER_PATCHED)
        self.assertEqual(Path(path + ".agybak").read_bytes(), MANAGER_ORIGINAL)

    def test_unsupported_targets_are_not_written(self):
        path = self.write("language_server.bin", b"unknown build")
        targets = self.targets()
        targets["manager"] = path
        with patch.object(autopatch, "do_patch_manager") as handler:
            result = self.run_engine(targets)
        handler.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["attempted_count"], 0)
        self.assertEqual(Path(path).read_bytes(), b"unknown build")

    def test_partial_targets_are_not_written(self):
        path = self.write("agy.bin", CLI_ORIGINAL + CLI_PATCHED)
        targets = self.targets()
        targets["agy"] = path
        with patch.object(autopatch, "do_patch_agy") as handler:
            result = self.run_engine(targets)
        handler.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["final_status"]["partial_count"], 1)

    def test_existing_auto_confirm_does_not_close_locked_application(self):
        from patcher.cli import confirmed
        with patch.dict(os.environ, {"AUTO_CONFIRM": "1"}):
            with unattended():
                self.assertFalse(confirmed("Close running applications?"))
        path = self.write("language_server.bin", MANAGER_ORIGINAL)
        targets = self.targets()
        targets["manager"] = path
        with patch.object(manager, "is_locked", return_value=True), patch.object(manager, "terminate_processes") as terminate:
            result = self.run_engine(targets)
        terminate.assert_not_called()
        self.assertFalse(result["success"])

    def test_json_repair_output_contains_only_one_document(self):
        path = self.write("language_server.bin", MANAGER_ORIGINAL)
        targets = self.targets()
        targets["manager"] = path
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(autopatch, "get_components_status", side_effect=lambda: doctor.get_components_status(targets)), patch.object(autopatch, "log"), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = autopatch.run_command(["--check-and-patch", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["applied_count"], 1)
        self.assertIn("PATCH COMPLETE", stderr.getvalue())

    def test_auto_exit_is_failure_even_when_no_actions_were_taken(self):
        result = {"success": False, "attempted_count": 0, "applied_count": 0}
        with patch.object(autopatch, "check_and_patch", return_value=result), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(autopatch.run_command(["--auto", "--json"]), 2)

    def test_json_status_does_not_include_portable_discovery_messages(self):
        expected = self.inventory(agy=self.write("agy.bin", CLI_PATCHED))
        def discover():
            print('Found portable installation: {example}')
            return expected
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(autopatch, "get_components_status", side_effect=discover), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = autopatch.run_command(["--check", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["all_patched"])
        self.assertIn("{example}", stderr.getvalue())

    def test_modern_ide_and_hub_are_not_modified_by_background_runner(self):
        main, implementation = self.modern_ide()
        hub = self.write("gemini/agy.bin", CLI_ORIGINAL)
        targets = self.targets()
        targets.update(ide=main, hub=hub)
        with patch.object(autopatch, "do_patch_ide") as shell, patch.object(autopatch, "do_patch_agy") as binary:
            result = self.run_engine(targets)
        shell.assert_not_called()
        binary.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["attempted_count"], 0)
        self.assertEqual(Path(hub).read_bytes(), CLI_ORIGINAL)
        self.assertEqual(Path(main).read_text("utf-8"), ide.IDE_DONE)

    def test_manual_legacy_ide_patch_rejects_modern_profile(self):
        main, _ = self.modern_ide()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(ide.do_patch(main))
        self.assertFalse(Path(main + ".bak").exists())

    def test_manual_extension_failure_stops_binary_patch(self):
        from patcher import cli
        entry = self.write("extension.js", EXT_ORIGINAL)
        binary = self.write("agy.bin", CLI_ORIGINAL)
        with patch.object(cli, "do_patch_vscode", return_value=False), patch.object(agy, "do_patch_agy") as apply_binary:
            self.assertFalse(cli.do_patch_vscode_flow(entry, binary))
        apply_binary.assert_not_called()

    def test_manual_hub_extension_does_not_run_legacy_patch(self):
        from patcher import cli
        _, implementation = self.modern_ide()
        binary = self.write("agy.bin", CLI_ORIGINAL)
        with patch.object(cli, "do_patch_vscode") as apply_extension, patch.object(agy, "do_patch_agy") as apply_binary, contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(cli.do_patch_vscode_flow(implementation, binary))
        apply_extension.assert_not_called()
        apply_binary.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
