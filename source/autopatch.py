"""
Auto-patch runner and status checker for Antigravity components.
Can be executed silently at startup, on a schedule, or via PowerShell check-and-patch commands.
"""

import os
import sys
import json
import datetime

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
if SOURCE_DIR not in sys.path:
    sys.path.insert(0, SOURCE_DIR)

from patcher.ide.discovery import find_install_root, find_main_js
from patcher.ide.patcher import is_already_patched as is_ide_patched, do_patch as do_patch_ide

from patcher.manager.discovery import find_manager_binary
from patcher.manager.patcher import is_already_patched as is_mgr_patched, do_patch_manager

from patcher.agy.discovery import find_agy_binary
from patcher.agy.patcher import is_already_patched as is_agy_patched, do_patch_agy

from patcher.vscode.discovery import find_extension_js, find_gemini_antigravity_binary
from patcher.vscode.patcher import is_already_patched as is_vscode_patched, do_patch_vscode


def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    log_file = os.path.join(os.path.dirname(SOURCE_DIR), "autopatch.log")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


def get_components_status():
    """
    Checks patch status of all supported Antigravity targets.
    Returns a dict with detailed status per component and overall health summary.
    """
    components = []

    # 1. Antigravity IDE
    ide_info = {
        "id": "ide",
        "name": "Antigravity IDE",
        "path": None,
        "installed": False,
        "patched": None,
        "error": None,
    }
    try:
        ide_root = find_install_root()
        main_js = find_main_js(ide_root) if ide_root else None
        if main_js and os.path.isfile(main_js):
            ide_info["path"] = main_js
            ide_info["installed"] = True
            with open(main_js, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            ide_info["patched"] = bool(is_ide_patched(content))
    except Exception as e:
        ide_info["error"] = str(e)
    components.append(ide_info)

    # 2. Antigravity 2.0 (language_server)
    mgr_info = {
        "id": "manager",
        "name": "Antigravity 2.0 (Manager)",
        "path": None,
        "installed": False,
        "patched": None,
        "error": None,
    }
    try:
        mgr_binary = find_manager_binary()
        if mgr_binary and os.path.isfile(mgr_binary):
            mgr_info["path"] = mgr_binary
            mgr_info["installed"] = True
            mgr_info["patched"] = bool(is_mgr_patched(mgr_binary))
    except Exception as e:
        mgr_info["error"] = str(e)
    components.append(mgr_info)

    # 3. Antigravity CLI (agy)
    agy_info = {
        "id": "agy",
        "name": "Antigravity CLI (agy)",
        "path": None,
        "installed": False,
        "patched": None,
        "error": None,
    }
    try:
        agy_binary = find_agy_binary()
        if agy_binary and os.path.isfile(agy_binary):
            agy_info["path"] = agy_binary
            agy_info["installed"] = True
            agy_info["patched"] = bool(is_agy_patched(agy_binary))
    except Exception as e:
        agy_info["error"] = str(e)
    components.append(agy_info)

    # 4. VS Code Extension
    vscode_ext_info = {
        "id": "vscode_ext",
        "name": "VS Code Extension",
        "path": None,
        "installed": False,
        "patched": None,
        "error": None,
    }
    try:
        vsc_ext = find_extension_js()
        if vsc_ext and os.path.isfile(vsc_ext):
            vscode_ext_info["path"] = vsc_ext
            vscode_ext_info["installed"] = True
            with open(vsc_ext, "r", encoding="utf-8", errors="ignore") as f:
                vsc_content = f.read()
            vscode_ext_info["patched"] = bool(is_vscode_patched(vsc_content))
    except Exception as e:
        vscode_ext_info["error"] = str(e)
    components.append(vscode_ext_info)

    # 5. VS Code AGY Binary
    vscode_bin_info = {
        "id": "vscode_bin",
        "name": "VS Code AGY Binary",
        "path": None,
        "installed": False,
        "patched": None,
        "error": None,
    }
    try:
        vsc_bin = find_gemini_antigravity_binary()
        if vsc_bin and os.path.isfile(vsc_bin):
            vscode_bin_info["path"] = vsc_bin
            vscode_bin_info["installed"] = True
            vscode_bin_info["patched"] = bool(is_agy_patched(vsc_bin))
    except Exception as e:
        vscode_bin_info["error"] = str(e)
    components.append(vscode_bin_info)

    installed = [c for c in components if c["installed"]]
    unpatched = [c for c in installed if c["patched"] is False]
    patched = [c for c in installed if c["patched"] is True]

    return {
        "components": components,
        "installed_count": len(installed),
        "patched_count": len(patched),
        "unpatched_count": len(unpatched),
        "all_patched": len(unpatched) == 0 and len(installed) > 0,
        "unpatched_components": [c["name"] for c in unpatched],
    }


def run_autopatch(auto_confirm: bool = True):
    """
    Checks and patches any unpatched components.
    Sets AUTO_CONFIRM=1 to gracefully close locking processes and avoid hanging.
    Returns number of actions taken.
    """
    if auto_confirm:
        os.environ["AUTO_CONFIRM"] = "1"

    log("=== Starting auto-patch check ===")
    actions_taken = 0

    # 1. Antigravity IDE (main.js expects content for is_already_patched)
    try:
        ide_root = find_install_root()
        main_js = find_main_js(ide_root) if ide_root else None
        if main_js and os.path.isfile(main_js):
            with open(main_js, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if not is_ide_patched(content):
                log(f"Antigravity IDE needs patch: {main_js}")
                do_patch_ide(main_js)
                actions_taken += 1
            else:
                log("Antigravity IDE: already patched.")
        else:
            log("Antigravity IDE: not found.")
    except Exception as e:
        log(f"Error checking Antigravity IDE: {e}")

    # 2. Antigravity 2.0 (language_server binary)
    try:
        mgr_binary = find_manager_binary()
        if mgr_binary and os.path.isfile(mgr_binary):
            if not is_mgr_patched(mgr_binary):
                log(f"Antigravity 2.0 needs patch: {mgr_binary}")
                do_patch_manager(mgr_binary)
                actions_taken += 1
            else:
                log("Antigravity 2.0: already patched.")
        else:
            log("Antigravity 2.0: not found.")
    except Exception as e:
        log(f"Error checking Antigravity 2.0: {e}")

    # 3. Antigravity CLI (agy binary)
    try:
        agy_binary = find_agy_binary()
        if agy_binary and os.path.isfile(agy_binary):
            if not is_agy_patched(agy_binary):
                log(f"Antigravity CLI (agy) needs patch: {agy_binary}")
                do_patch_agy(agy_binary)
                actions_taken += 1
            else:
                log("Antigravity CLI (agy): already patched.")
        else:
            log("Antigravity CLI (agy): not found.")
    except Exception as e:
        log(f"Error checking Antigravity CLI: {e}")

    # 4. VS Code Extension & binary
    try:
        vsc_ext = find_extension_js()
        vsc_bin = find_gemini_antigravity_binary()
        if vsc_ext and os.path.isfile(vsc_ext):
            with open(vsc_ext, "r", encoding="utf-8", errors="ignore") as f:
                vsc_content = f.read()
            if not is_vscode_patched(vsc_content):
                log(f"VS Code extension needs patch: {vsc_ext}")
                do_patch_vscode(vsc_ext)
                actions_taken += 1
            else:
                log("VS Code extension: already patched.")
        if vsc_bin and os.path.isfile(vsc_bin):
            if not is_agy_patched(vsc_bin):
                log(f"VS Code agy binary needs patch: {vsc_bin}")
                do_patch_agy(vsc_bin)
                actions_taken += 1
            else:
                log("VS Code agy binary: already patched.")
    except Exception as e:
        log(f"Error checking VS Code targets: {e}")

    log(f"=== Auto-patch check finished ({actions_taken} action(s) performed) ===")
    return actions_taken


def check_and_patch(auto_confirm: bool = True):
    """
    1. Checks initial status.
    2. If any component is unpatched, runs auto-patch.
    3. Re-checks final status.
    Returns a dict with overall success and detailed before/after status.
    """
    initial_status = get_components_status()
    repatched = False

    if not initial_status["all_patched"] and initial_status["unpatched_count"] > 0:
        run_autopatch(auto_confirm=auto_confirm)
        repatched = True

    final_status = get_components_status()
    success = final_status["all_patched"]

    return {
        "success": success,
        "repatched": repatched,
        "initial_status": initial_status,
        "final_status": final_status,
    }


if __name__ == "__main__":
    if "--status" in sys.argv or "--check" in sys.argv:
        status = get_components_status()
        if "--json" in sys.argv:
            print(json.dumps(status, indent=2))
        else:
            print(f"Components checked: {status['installed_count']} installed")
            for c in status["components"]:
                if c["installed"]:
                    state = "PATCHED" if c["patched"] else "NOT PATCHED"
                    print(f"  [{state}] {c['name']} -> {c['path']}")
                else:
                    print(f"  [NOT INSTALLED] {c['name']}")
            print(f"All patched: {status['all_patched']}")
        sys.exit(0 if status["all_patched"] else 2)
    elif "--check-and-patch" in sys.argv:
        result = check_and_patch()
        if "--json" in sys.argv:
            print(json.dumps(result, indent=2))
        else:
            print(f"Check and patch result: {'SUCCESS' if result['success'] else 'FAILED'}")
            print(f"Repatched actions needed: {result['repatched']}")
        sys.exit(0 if result["success"] else 2)
    else:
        run_autopatch()
