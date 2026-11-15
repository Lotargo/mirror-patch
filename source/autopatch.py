"""Фоновое применение известных локальных патчей с обязательной перепроверкой."""

import argparse
import contextlib
import datetime
import json
import os
import sys

from patcher.diagnostics import get_components_status, print_status
from patcher.ide.patcher import do_patch as do_patch_ide
from patcher.manager.patcher import do_patch_manager
from patcher.agy.patcher import do_patch_agy
from patcher.vscode.patcher import do_patch_vscode
from patcher.utils.runtime import unattended


def log(message):
    formatted = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(formatted, file=sys.stderr)
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "autopatch.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except OSError:
        pass


def _read_status():
    # Поиск portable-установок тоже может печатать сообщения с фигурными скобками.
    with contextlib.redirect_stdout(sys.stderr):
        return get_components_status()


def run_autopatch(auto_confirm=False):
    # Параметр оставлен для совместимости; фоновые вопросы всегда отклоняются.
    initial = _read_status()
    actions = []
    handlers = {
        "ide": do_patch_ide, "manager": do_patch_manager, "agy": do_patch_agy,
        "vscode_ext": do_patch_vscode, "vscode_bin": do_patch_agy,
    }
    seen = set()
    log("Starting local patch verification")
    with unattended(), contextlib.redirect_stdout(sys.stderr):
        for component in initial["components"]:
            if not component["can_apply"] or component["id"] not in handlers:
                continue
            path = component["path"]
            identity = os.path.normcase(os.path.realpath(path))
            if identity in seen:
                continue
            seen.add(identity)
            action = {"id": component["id"], "path": path, "status": "pending", "error": None}
            log(f"Applying known patch: {component['name']}")
            try:
                if handlers[component["id"]](path) is False:
                    action["error"] = "Patch operation reported failure"
            except Exception as exc:
                action["error"] = str(exc)
            actions.append(action)

    final = _read_status()
    indexed = {component["id"]: component for component in final["components"]}
    for action in actions:
        component = indexed.get(action["id"])
        verified = (component is not None and component["status"] == "patched"
                    and os.path.normcase(os.path.realpath(component["path"] or ""))
                    == os.path.normcase(os.path.realpath(action["path"])))
        action["status"] = "applied" if verified and not action["error"] else "failed"
        if not verified and not action["error"]:
            action["error"] = "Post-write verification did not confirm the local patch"
    applied = sum(action["status"] == "applied" for action in actions)
    success = final["all_patched"] and all(action["status"] == "applied" for action in actions)
    log(f"Verification finished: {applied}/{len(actions)} applied; local coverage complete={success}")
    return {
        "success": success, "repatched": applied > 0, "attempted_count": len(actions),
        "applied_count": applied, "actions": actions, "initial_status": initial, "final_status": final,
    }


def check_and_patch(auto_confirm=False):
    return run_autopatch(auto_confirm=auto_confirm)


def run_command(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", "--status", dest="check", action="store_true")
    modes.add_argument("--check-and-patch", action="store_true")
    modes.add_argument("--auto", action="store_true")
    modes.add_argument("--hub-auth-status", action="store_true",
                       help="Read GetAuthStatus from a running local Hub without changing auth")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repeat", type=int, default=2,
                        help="Number of Hub status observations, from 1 to 10")
    parser.add_argument("--hub-pid", type=int, help="Select a running Hub if several exist")
    parser.add_argument("--force", action="store_true",
                        help="Re-verify all targets; never overwrite an already patched file")
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 10:
        parser.error("--repeat must be between 1 and 10")
    if args.hub_auth_status:
        from patcher.hub.probe import exit_code, print_report, probe_running_hub
        result = probe_running_hub(repeat=args.repeat, pid=args.hub_pid)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_report(result)
        return exit_code(result)
    if args.check:
        result = _read_status()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print_status(result)
        return 0 if result["all_patched"] else 2

    # Каждый запуск полностью проверяет файлы, в том числе с --force.
    result = check_and_patch()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_status(result["final_status"])
        print(f"Verified changes: {result['applied_count']}/{result['attempted_count']}")
    return 0 if result["success"] else 2


if __name__ == "__main__":
    sys.exit(run_command())
