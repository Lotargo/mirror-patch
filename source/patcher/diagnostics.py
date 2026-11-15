"""Инвентаризация установок и покрытие патчами без изменения файлов и входа."""

import os
from packaging.version import InvalidVersion, Version

from patcher.ide.discovery import find_install_root, find_main_js, get_ag_version, get_app_root
from patcher.ide.patcher import get_status as ide_status
from patcher.manager.discovery import find_manager_binary, get_antigravity_version
from patcher.manager.patcher import get_status as manager_status
from patcher.agy.discovery import find_agy_binary
from patcher.agy.patcher import get_status as agy_status
from patcher.hub.profile import inspect_backend as inspect_hub_backend
from patcher.vscode.discovery import (
    find_extension_dir, inspect_extension_dir, inspect_extension_path, find_bundled_extension_dir,
    find_gemini_antigravity_binary, expected_hub_binary,
)
from patcher.vscode.patcher import get_status as extension_status


def _component(identifier, name, mode="legacy", required=False):
    return {
        "id": identifier, "name": name, "path": None, "installed": False,
        "status": "missing", "file_status": "missing", "patched": None,
        "error": None, "reason": None, "version": None, "mode": mode,
        "required": required, "can_apply": False, "auth_status": "not_checked",
    }


def _state(component, state):
    component["status"] = "unsupported" if state == "unknown" else state
    component["patched"] = (True if state == "patched" else
                            False if state in ("unpatched", "partial") else None)
    component["can_apply"] = state == "unpatched" and component["mode"] == "legacy"


def _error(component, exc):
    state = "missing" if isinstance(exc, FileNotFoundError) else "error"
    _state(component, state)
    component["file_status"] = state
    component["error"] = str(exc)


def _text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _ide_path():
    root = find_install_root()
    return find_main_js(root) if root else ""


def _target(targets, key, discover):
    return targets[key] if key in targets else discover()


def _inspect_extension(component, directory=None, path=None):
    if not directory and not path:
        return
    component["required"] = True
    component["path"] = path or os.path.join(directory, "package.json")
    component["installed"] = os.path.isfile(component["path"])
    info = inspect_extension_path(path) if path else inspect_extension_dir(directory)
    component.update({key: info[key] for key in ("path", "entry_path", "version", "mode")})
    component["installed"] = True
    component["file_status"] = extension_status(_text(info["path"]))
    _state(component, component["file_status"])
    if info["mode"] == "hub":
        _state(component, "not_applicable")
        component["reason"] = "Hub extension detected; legacy installer patch does not apply."


def _inspect_binary(component, path, scanner, cache):
    component["path"] = path or None
    if not path:
        return
    # Найденный/выбранный файл может исчезнуть при обновлении до первого чтения.
    component["required"] = True
    component["installed"] = os.path.isfile(path)
    key = (os.path.normcase(os.path.realpath(path)), scanner)
    if key not in cache:
        cache[key] = scanner(path, strict=True)[0]
    component["file_status"] = cache[key]
    _state(component, cache[key])
    if component["mode"] == "hub" and cache[key] not in ("missing", "error"):
        _state(component, "unsupported")
        component["patch_scope"] = "hub_auth"
        component["legacy_patch_scope"] = "cli_tui"
        component["launch_args"] = ["--hub"]
        component["profile"] = "hub_auth"
        component["auth_profile"] = inspect_hub_backend(path)
        component["version"] = component["auth_profile"]["version"]
        component["auth_probe_supported"] = component["auth_profile"]["supported"]
        component["reason"] = "Hub auth diagnostics are read-only; CLI markers do not establish successful Hub login."
        if component["auth_profile"]["binary_state"] == "aicode_compatibility":
            _state(component, "patched")
            component["patch_scope"] = "aicode_hub"
            component["reason"] = "AICode Hub compatibility is installed; runtime authentication is checked separately."


def summarize_components(components):
    installed = [c for c in components if c["installed"]]
    relevant = [c for c in components if c["installed"] or c["required"] or c["error"]]
    issues = [c for c in relevant if c["error"] or c["status"] not in ("patched", "not_applicable")]
    counts = {state: sum(c["status"] == state for c in relevant)
              for state in ("patched", "unpatched", "partial", "unsupported", "error", "missing", "not_applicable")}
    return {
        "components": components, "installed_count": len(installed),
        **{state + "_count": count for state, count in counts.items()},
        "all_patched": bool(installed) and not issues,
        "unpatched_components": [c["name"] for c in relevant if c["status"] == "unpatched"],
        "issue_components": [c["name"] for c in issues],
        "actionable_count": sum(c["can_apply"] for c in relevant),
        "auth_status": "not_checked",
    }


def get_components_status(targets=None):
    """targets переопределяет пути для ручного выбора и изолированных проверок."""
    targets = targets or {}
    cache = {}
    components = []
    ide = _component("ide", "Antigravity IDE")
    bundled = _component("ide_ext", "IDE built-in Antigravity extension")
    modern = False
    try:
        main = _target(targets, "ide", _ide_path)
        ide["path"] = main or None
        if main:
            ide["required"] = True
            ide["installed"] = os.path.isfile(main)
            ide["version"] = get_ag_version(main)[0]
            if ide["version"]:
                try:
                    modern = Version(ide["version"]) >= Version("2.6.0")
                except InvalidVersion:
                    pass
            directory = find_bundled_extension_dir(main)
            bundled["required"] = modern or bool(directory)
            if directory:
                try:
                    _inspect_extension(bundled, directory=directory)
                    modern = modern or bundled["mode"] == "hub"
                except Exception as exc:
                    _error(bundled, exc)
            elif modern:
                bundled["path"] = os.path.join(get_app_root(main), "extensions", "agy", "package.json")
                bundled["reason"] = "Modern IDE requires its built-in extension."
            if modern and bundled["installed"] and bundled["status"] not in ("error", "missing") and bundled["mode"] != "hub":
                _state(bundled, "unsupported")
                bundled["reason"] = "Modern IDE extension launch mode could not be identified as Hub."
            ide["file_status"] = ide_status(_text(main))
            _state(ide, ide["file_status"])
            if modern:
                ide["mode"] = "hub"
                ide["profile"] = "ide_hub"
                _state(ide, "not_applicable")
                ide["reason"] = "IDE >= 2.6 uses the built-in extension and agy --hub; main.js is legacy coverage."
                from pathlib import Path
                from patcher.ide import aicode as ide_aicode
                if ide_aicode.paths(Path(main))[1].is_file():
                    compatibility = ide_aicode.status(Path(main))
                    state = "patched" if compatibility["state"] == "installed" else "unknown"
                    _state(ide, state)
                    ide["profile"] = "aicode_ide"
                    ide["file_status"] = state
                    ide["reason"] = "Personal AICode licensing frontend; Hub generation is tracked separately."
            else:
                ide["profile"] = "ide_legacy"
    except Exception as exc:
        _error(ide, exc)
    components.append(ide)
    if modern or bundled["required"]:
        components.append(bundled)

    for identifier, name, discover, scanner in (
        ("manager", "Antigravity App (Manager)", find_manager_binary, manager_status),
        ("agy", "Antigravity CLI (standalone)", find_agy_binary, agy_status),
    ):
        component = _component(identifier, name)
        try:
            path = _target(targets, identifier, discover)
            _inspect_binary(component, path, scanner, cache)
            if identifier == "manager" and component["installed"]:
                component["version"] = get_antigravity_version(path)
        except Exception as exc:
            _error(component, exc)
        components.append(component)

    extension = _component("vscode_ext", "VS Code Antigravity extension")
    try:
        if "vscode_ext" in targets:
            _inspect_extension(extension, path=targets["vscode_ext"])
        else:
            _inspect_extension(extension, directory=find_extension_dir())
    except Exception as exc:
        _error(extension, exc)
    components.append(extension)

    hub_consumers = (["ide"] if modern else []) + (["vscode"] if extension["mode"] == "hub" else [])
    binary = _component("ide_hub" if hub_consumers else "vscode_bin",
                        "Antigravity Hub backend" if hub_consumers else "VS Code AGY binary",
                        mode="hub" if hub_consumers else "legacy", required=bool(hub_consumers))
    binary["consumers"] = hub_consumers
    try:
        path = (_target(targets, "hub", expected_hub_binary) if hub_consumers else
                _target(targets, "vscode_bin", find_gemini_antigravity_binary))
        _inspect_binary(binary, path, agy_status, cache)
        if not hub_consumers and binary["installed"] and (
                not extension["installed"] or extension["status"] in ("error", "unsupported", "missing")):
            if binary["status"] not in ("error", "missing"):
                binary["mode"] = "unknown"
                _state(binary, "unsupported")
                binary["reason"] = "Cached backend has no recognized extension owner; launch mode is unknown."
    except Exception as exc:
        _error(binary, exc)
    components.append(binary)
    if hub_consumers and binary["path"]:
        identity = os.path.normcase(os.path.realpath(binary["path"]))
        for component in components:
            if component["id"] == "agy" and component["path"] and os.path.normcase(os.path.realpath(component["path"])) == identity:
                # Один файл может обслуживать CLI и Hub. До профиля Hub его не меняем фоном.
                component["can_apply"] = False
                component["shared_with"] = binary["id"]
                component["reason"] = "CLI and Hub share this binary; automatic terminal-only patch is disabled."
                binary["consumers"].append("cli")
    return summarize_components(components)


def print_status(status):
    for component in status["components"]:
        print(f"[{component['status'].upper()}] {component['name']}")
        if component["path"]:
            print(f"  {component['path']}")
        print(f"  mode={component['mode']}; file_status={component['file_status']}; auth=not_checked")
        if component["version"]:
            print(f"  version={component['version']}")
        if component["reason"] or component["error"]:
            print(f"  {component['reason'] or component['error']}")
    print(f"All required local patches covered: {status['all_patched']}. Authentication: not checked.")
