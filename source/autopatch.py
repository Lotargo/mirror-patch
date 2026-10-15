"""
Auto-patch runner for Antigravity components.
Can be executed silently at startup or on a schedule.
"""

import os
import sys
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


def run_autopatch():
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


if __name__ == "__main__":
    run_autopatch()
