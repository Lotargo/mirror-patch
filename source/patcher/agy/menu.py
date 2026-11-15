"""Interactive entry point for the audited personal AICode profiles."""
import os
from pathlib import Path

from patcher.agy.aicode import install, restore, status
from patcher.ide import aicode as frontend
from patcher.vscode.discovery import expected_hub_binary
from patcher.utils.console import info, ok, warn, err, print_menu_section, print_menu_row


def run_aicode_menu(main_js_path, manager_path, agy_path):
    if os.name != "nt":
        warn("AICode release profiles currently support Windows x64 only.")
        return
    targets = {"hub": expected_hub_binary(), "ide": main_js_path,
               "manager": manager_path, "cli": agy_path}
    available = {key: Path(value) for key, value in targets.items() if value and Path(value).is_file()}
    print_menu_section("AICODE PERSONAL ACCESS")
    info("Audited builds: IDE 2.7.0, CLI/Hub 1.5.9, Manager 2.32.1.")
    info("Close target applications before installing or restoring. Backups are preserved.")
    for profile, path in available.items():
        info(f"{profile}: {path}")
    if not available:
        warn("No supported application paths found. Install and start Antigravity once first.")
        return
    print_menu_row("1", "Install for IDE + Hub", "personal sign-in, generation and quotas")
    print_menu_row("2", "Install for Manager", "Antigravity 2.0")
    print_menu_row("3", "Install for CLI", "ordinary agy command")
    print_menu_row("4", "Install for all detected targets", "audited hashes only")
    print_menu_row("5", "Check AICode patch files", "no network or authentication changes")
    print_menu_row("6", "Restore AICode backups", "choose one target")
    print_menu_row("0", "Back")
    choice = input("\n  AICode option > ").strip()
    if choice == "0":
        return
    if choice == "5":
        for profile, path in available.items():
            result = frontend.status(path) if profile == "ide" else status(path)
            info(f"{profile}: {result['state']}")
        return
    if choice == "6":
        profile = input("  Restore profile (ide/hub/manager/cli) > ").strip()
        if profile not in available:
            err("That target was not detected.")
            return
        try:
            result = frontend.restore(available[profile]) if profile == "ide" else restore(available[profile])
            ok(f"{profile}: {result['state']}")
        except (OSError, ValueError, KeyError) as exc:
            err(str(exc))
        return
    selected = {"1": ["hub", "ide"], "2": ["manager"], "3": ["cli"],
                "4": [p for p in ("hub", "ide", "manager", "cli") if p in available]}.get(choice)
    if selected is None:
        err("Invalid AICode option.")
        return
    if "ide" in selected and "hub" not in available:
        err("IDE Hub backend is missing. Start the official IDE once, then retry.")
        return
    missing = [p for p in selected if p not in available]
    if missing:
        err("Missing installation: " + ", ".join(missing) + ". Start the official app once, then retry.")
        return
    for profile in selected:
        try:
            info(f"Installing {profile}; a verified official CLI source may be downloaded if needed...")
            result = frontend.install(available[profile]) if profile == "ide" else install(available[profile], profile=profile)
            ok(f"{profile}: {result['state']}")
        except (OSError, ValueError, KeyError) as exc:
            err(f"{profile}: {exc}")
            info("No later targets were changed. Close the app or check the supported version and retry.")
            return
    info("Start the applications again and check a short model reply and Models & Usage.")
