"""Exercise the frozen EXE on temporary copies with Go and Python absent from PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("dist/release/MirrorPatch-windows-x64.exe"))
    parser.add_argument("--cli-source", type=Path, required=True)
    parser.add_argument("--manager-source", type=Path)
    parser.add_argument("--ide-source", type=Path)
    parser.add_argument("--report", type=Path, default=Path("test_cache/release-smoke.json"))
    args = parser.parse_args()
    exe = args.exe.resolve()
    env = dict(os.environ, PATH=os.path.join(os.environ["SystemRoot"], "System32") + os.pathsep + os.environ["SystemRoot"],
               MIRRORPATCH_ELEVATE="0")
    assert shutil.which("go", path=env["PATH"]) is None
    assert shutil.which("python", path=env["PATH"]) is None
    results = {"no_python_or_go_on_child_path": True, "tests": []}
    with tempfile.TemporaryDirectory(prefix="mirrorpatch-release-smoke-") as temporary:
        work = Path(temporary)
        def run(arguments, codes=(0,)):
            process = subprocess.run([str(exe), *map(str, arguments)], cwd=work, env=env,
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            if process.returncode not in codes:
                raise AssertionError(f"Frozen command failed: {arguments[0]} exit {process.returncode}: {process.stdout} {process.stderr}")
            return process.stdout
        assert run(["--version"]).strip() == "MirrorPatch 1.4.0"
        assert "--agy-aicode" in run(["--help"])
        status = json.loads(run(["--check", "--json"], codes=(0,2)))
        assert isinstance(status.get("components"), list)
        results["tests"].append({"command_checks": True, "components_report_is_json": True})
        profiles = [("cli", args.cli_source), ("hub", args.cli_source)]
        if args.manager_source:
            profiles.append(("manager", args.manager_source))
        if args.ide_source:
            profiles.append(("ide", args.ide_source))
        for profile, source in profiles:
            directory = work / profile
            directory.mkdir()
            target = directory / ("main.js" if profile == "ide" else "agy.exe")
            shutil.copyfile(source, target)
            original = sha(target)
            base = ["--agy-aicode"]
            tail = ["--profile", profile, "--target", target]
            installed = json.loads(run(base + ["install"] + tail))
            assert installed["state"] == "installed"
            patched = sha(target)
            assert patched != original
            checked = json.loads(run(base + ["status"] + tail))
            assert checked["state"] == "installed"
            assert json.loads(run(base + ["install"] + tail))["state"] == "installed"
            assert sha(target) == patched
            restored = json.loads(run(base + ["restore"] + tail))
            assert restored["state"] == "restored" and sha(target) == original
            results["tests"].append({"profile": profile, "install_status_idempotence_restore": True})
        unknown = work / "unknown.exe"
        unknown.write_bytes(b"MZ unsupported fixture")
        bad = json.loads(run(["--agy-aicode", "install", "--target", unknown, "--source", unknown], codes=(1,)))
        assert bad["state"] == "error" and unknown.read_bytes() == b"MZ unsupported fixture"
        results["tests"].append({"unsupported_build_refused": True})
    results["verified"] = True
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, indent=2) + "\n", "utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
