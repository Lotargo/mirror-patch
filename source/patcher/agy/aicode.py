"""Pinned Windows CLI compatibility patch with a server-configured experience adapter.

This is separate from the historical eligibility gates: it preserves native OAuth,
licensing and entitlement checks, and refuses unknown binaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SOURCE_SHA256 = "8803c289cac04d6e53f2211592d9981c2d5760bc0d9b4a7f222ef6ecc7abb38a"
SOURCE_SHA512 = "539a9415a686d9ec45f5c59fe7d32c485c6fa9abaea50208ae8ede4ed3622b9fb2bfd6680c87dc52344981172a96ddacb261ff4231276b5572fb5b62a9b0221e"
SOURCE_URL = "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.9-4515810895593472/windows-x64/cli_windows_x64.exe"
NATIVE_NAME = "agy-aicode-native.exe"
MANIFEST_NAME = "agy-aicode-patch.json"
RUNTIME = Path(__file__).with_name("aicode_runtime")
PATCH_VERSION = 1

# File offsets in the hash-pinned official PE. Keep native stack frames, Go
# safepoints and write barriers intact. Reuse a GC-scanned converter pointer
# (unused by both response methods) to retain the original CCPA request.
PATCHES = (
    (0x2742F06, "488b9424e0020000", "488b9424d8020000"),  # stream, barrier off
    (0x2742F1D, "488b9424e0020000", "488b9424d8020000"),  # stream, barrier on
    (0x2743916, "488b9424d0020000", "488b9424c8020000"),  # non-stream constructor
    (0x27835B6, "48c7401017000000488d15eb80b00148895008",
     "488b542448488b120f1042300f114008909090"),
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_native(data: bytes) -> bytes:
    if digest(data) != SOURCE_SHA256 or hashlib.sha512(data).hexdigest() != SOURCE_SHA512:
        raise ValueError("Expected pristine official agy 1.5.9 Windows x64; unknown or modified build")
    result = bytearray(data)
    for offset, old_hex, new_hex in PATCHES:
        old, new = bytes.fromhex(old_hex), bytes.fromhex(new_hex)
        if len(old) != len(new) or result[offset:offset + len(old)] != old:
            raise ValueError(f"Native patch precondition failed at {offset:#x}")
        result[offset:offset + len(old)] = new
    return bytes(result)


def atomic_write(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def paths(target: Path) -> tuple[Path, Path, Path]:
    return (target.with_name(target.name + ".before-aicode"),
            target.with_name(NATIVE_NAME), target.with_name(MANIFEST_NAME))


def status(target: Path) -> dict:
    backup, native, manifest_path = paths(target)
    if not manifest_path.exists():
        return {"state": "not-installed"}
    manifest = json.loads(manifest_path.read_text("utf-8"))
    checks = {"launcher": (target, manifest["launcher_sha256"]),
              "native": (native, manifest["native_sha256"]),
              "backup": (backup, manifest["previous_sha256"])}
    verified = {k: p.is_file() and not p.is_symlink() and digest(p.read_bytes()) == expected
                for k, (p, expected) in checks.items()}
    return {"state": "installed" if all(verified.values()) else "modified", "checks": verified,
            "patch_version": manifest["patch_version"], "native_version": "1.5.9"}


def build_launcher(output: Path, native_hash: str) -> None:
    go = shutil.which("go")
    if not go:
        raise ValueError("Go compiler is required to build the compatibility launcher")
    env = dict(os.environ, GOOS="windows", GOARCH="amd64", CGO_ENABLED="0", GOTOOLCHAIN="local")
    subprocess.run([go, "build", "-trimpath", "-buildvcs=false", "-ldflags",
                    f"-s -w -buildid= -X main.nativeSHA256={native_hash}", "-o", str(output), "."],
                   cwd=RUNTIME, env=env, check=True)


def install(target: Path, source: Path) -> dict:
    target = target.absolute()
    if os.name != "nt":
        raise ValueError("This pinned native patch supports Windows x64 only")
    if not target.is_file() or target.is_symlink():
        raise ValueError("Target must be an existing regular agy executable")
    backup, native, manifest_path = paths(target)
    if manifest_path.exists():
        current = status(target)
        if current["state"] == "installed" and current["patch_version"] == PATCH_VERSION:
            return current
        raise ValueError("Existing compatibility installation differs; restore or review it first")
    if backup.exists() or native.exists():
        raise ValueError("Companion or backup already exists without manifest; review before installing")
    patched = patch_native(source.read_bytes())
    previous = target.read_bytes()
    with tempfile.TemporaryDirectory(prefix="agy-aicode-build-") as directory:
        launcher_path = Path(directory) / "agy.exe"
        build_launcher(launcher_path, digest(patched))
        launcher = launcher_path.read_bytes()
    manifest = {"patch_version": PATCH_VERSION, "native_version": "1.5.9",
                "source_sha256": SOURCE_SHA256, "source_url": SOURCE_URL,
                "native_sha256": digest(patched), "launcher_sha256": digest(launcher),
                "previous_sha256": digest(previous), "license_checks_preserved": True,
                "changes": [{"offset": hex(at), "before": old, "after": new} for at, old, new in PATCHES]}
    # Install companions first and expose the launcher last. Preserve old .agybak.
    atomic_write(backup, previous)
    try:
        atomic_write(native, patched)
        atomic_write(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
        atomic_write(target, launcher)
    except Exception:
        # No partially deployed launcher should outlive its companions.
        if target.read_bytes() != previous:
            atomic_write(target, previous)
        for file in (native, manifest_path, backup):
            file.unlink(missing_ok=True)
        raise
    return status(target)


def restore(target: Path) -> dict:
    target = target.absolute()
    if status(target)["state"] != "installed":
        raise ValueError("Restore requires an intact manifest, launcher, native companion and backup")
    backup, native, manifest_path = paths(target)
    atomic_write(target, backup.read_bytes())
    # Keep the manifest and companions until the original binary is restored.
    for file in (native, manifest_path, backup):
        file.unlink()
    return {"state": "restored"}


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agy-aicode", choices=("install", "status", "restore"), required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="Pristine official Windows x64 agy 1.5.9 executable")
    options = parser.parse_args(args)
    try:
        if options.agy_aicode == "install":
            if options.source is None:
                parser.error("install requires --source")
            result = install(options.target, options.source)
        elif options.agy_aicode == "restore":
            result = restore(options.target)
        else:
            result = status(options.target)
        print(json.dumps(result, indent=2))
        return 0 if result["state"] != "modified" else 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"state": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
