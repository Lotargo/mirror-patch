"""Pinned Windows CLI/Hub/Manager compatibility with server experiences and quotas.

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
import sys
import tempfile
import urllib.request

SOURCE_SHA256 = "8803c289cac04d6e53f2211592d9981c2d5760bc0d9b4a7f222ef6ecc7abb38a"
SOURCE_SHA512 = "539a9415a686d9ec45f5c59fe7d32c485c6fa9abaea50208ae8ede4ed3622b9fb2bfd6680c87dc52344981172a96ddacb261ff4231276b5572fb5b62a9b0221e"
SOURCE_URL = "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.9-4515810895593472/windows-x64/cli_windows_x64.exe"
NATIVE_NAME = "agy-aicode-native.exe"
MANIFEST_NAME = "agy-aicode-patch.json"
RUNTIME = Path(__file__).with_name("aicode_runtime")
PATCH_VERSION = 2
PREBUILT_NATIVE_HASHES = {
    "cli": ("e903bb38c63a58a7e4bde7099ae02879f4ab65e6d7c1f5a772d2822e2aa26794",),
    "hub": ("e903bb38c63a58a7e4bde7099ae02879f4ab65e6d7c1f5a772d2822e2aa26794",),
    "manager": ("f6bbfcc78c30aff2d520605563888c5d026fe737c47046fb8597747f8ea5c83c",
                "be201731a12046cd8c5a837d6a5d00dc99f7968d172dee21f905625ef22daea1"),
}
MANAGER_SOURCE_HASHES = {
    "33beef9814027cd5ed2ecbbcd0126645252c4b2516c0296e15091ca7e113b76f",  # existing local gate
    "65cdb8d132fc61056625081be7d07d4d22f57eba485de7edf0d24d98e77c891f",  # original bundled build
}
MANAGER_PATCHES = (
    (0x2809EA6, "488b9424e0020000", "488b9424d8020000"),
    (0x2809EBD, "488b9424e0020000", "488b9424d8020000"),
    (0x280A8B6, "488b9424d0020000", "488b9424c8020000"),
    (0x2847565, "48c7401017000000488d1516a92c0148895008",
     "488b542448488b120f1042300f114008909090"),
    # Same ABI: delegate quota cache fetch to Google's existing AICode
    # implementation, including its native quota-summary converter.
    (0x28623C0, "4c8d6424d0", "e9dbb7faff"),
)

# File offsets in the hash-pinned official PE. Keep native stack frames, Go
# safepoints and write barriers intact. Reuse a GC-scanned converter pointer
# (unused by both response methods) to retain the original CCPA request.
PATCHES = (
    (0x2742F06, "488b9424e0020000", "488b9424d8020000"),  # stream, barrier off
    (0x2742F1D, "488b9424e0020000", "488b9424d8020000"),  # stream, barrier on
    (0x2743916, "488b9424d0020000", "488b9424c8020000"),  # non-stream constructor
    (0x27835B6, "48c7401017000000488d15eb80b00148895008",
     "488b542448488b120f1042300f114008909090"),
    (0x279E7A0, "4c8d6424d0", "e95b84faff"),
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def runtime_fingerprint() -> str:
    files = sorted(p for p in RUNTIME.iterdir()
                   if p.name == "go.mod" or (p.suffix == ".go" and not p.name.endswith("_test.go")))
    return digest(b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in files))


def patch_native(data: bytes, profile="cli") -> bytes:
    if profile not in ("cli", "hub", "manager"):
        raise ValueError("Unknown AICode native profile")
    patches = MANAGER_PATCHES if profile == "manager" else PATCHES
    if profile == "manager":
        if digest(data) not in MANAGER_SOURCE_HASHES:
            raise ValueError("Expected audited Manager 2.32.1 Windows x64 build")
    elif digest(data) != SOURCE_SHA256 or hashlib.sha512(data).hexdigest() != SOURCE_SHA512:
        raise ValueError("Expected pristine official agy 1.5.9 Windows x64; unknown or modified build")
    result = bytearray(data)
    for offset, old_hex, new_hex in patches:
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
            "patch_version": manifest["patch_version"], "native_version": manifest.get("native_version", "1.5.9"),
            "profile": manifest.get("profile", "cli")}


def build_launcher(output: Path, native_hash: str, profile="cli") -> None:
    prebuilt = RUNTIME / "prebuilt"
    manifest_path = prebuilt / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text("utf-8"))
        name = f"{profile}-{native_hash}.exe"
        if (manifest.get("runtime_sha256") != runtime_fingerprint()
                or native_hash not in PREBUILT_NATIVE_HASHES.get(profile, ())):
            raise ValueError("Bundled launcher does not match the audited runtime/profile")
        candidate = prebuilt / name
        data = candidate.read_bytes()
        if digest(data) != manifest.get("assets", {}).get(name):
            raise ValueError("Bundled launcher checksum mismatch")
        output.write_bytes(data)
        return
    if getattr(sys, "frozen", False):
        raise ValueError("Release package is missing its bundled compatibility launchers")
    go = shutil.which("go")
    if not go:
        raise ValueError("Go compiler is required to build the compatibility launcher")
    env = dict(os.environ, GOOS="windows", GOARCH="amd64", CGO_ENABLED="0", GOTOOLCHAIN="local")
    subprocess.run([go, "build", "-trimpath", "-buildvcs=false", "-ldflags",
                    f"-s -w -buildid= -X main.nativeSHA256={native_hash} -X main.hubEnabled={str(profile == 'hub').lower()}", "-o", str(output), "."],
                   cwd=RUNTIME, env=env, check=True)


def resolve_source(target: Path, profile: str) -> Path:
    """Use a verified local original; only CLI/Hub can download the pinned public build."""
    accepted = MANAGER_SOURCE_HASHES if profile == "manager" else {SOURCE_SHA256}
    for candidate in (target, target.with_name(target.name + ".before-aicode"),
                      target.with_name(target.name + ".agybak")):
        if candidate.is_file() and digest(candidate.read_bytes()) in accepted:
            return candidate
    if profile == "manager":
        raise ValueError("Manager 2.32.1 source is not recognized; provide an audited --source executable")
    cache = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "MirrorPatch/cache"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / f"agy-1.5.9-{SOURCE_SHA256}.exe"
    if destination.is_file() and digest(destination.read_bytes()) == SOURCE_SHA256:
        return destination
    fd, temp_name = tempfile.mkstemp(prefix="agy-download-", suffix=".tmp", dir=cache)
    try:
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "MirrorPatch"})
        count = 0
        sha = hashlib.sha512()
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(request, timeout=30) as response:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > 231726744:
                    raise ValueError("Official CLI download exceeds the pinned build size")
                sha.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if count != 231726744 or sha.hexdigest() != SOURCE_SHA512:
            raise ValueError("Official CLI download checksum/size mismatch")
        os.replace(temp_name, destination)
        return destination
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def install(target: Path, source: Path | None = None, profile="cli") -> dict:
    target = target.absolute()
    if os.name != "nt":
        raise ValueError("This pinned native patch supports Windows x64 only")
    if not target.is_file() or target.is_symlink():
        raise ValueError("Target must be an existing regular agy executable")
    backup, native, manifest_path = paths(target)
    upgrading = False
    if manifest_path.exists():
        current = status(target)
        old_manifest = json.loads(manifest_path.read_text("utf-8"))
        if (current["state"] == "installed" and current["patch_version"] == PATCH_VERSION
                and current.get("profile", "cli") == profile
                and old_manifest.get("runtime_sha256") == runtime_fingerprint()):
            return current
        if (current["state"] == "installed" and current.get("profile", "cli") == profile
                and current["patch_version"] in (1, 2) and PATCH_VERSION == 2):
            upgrading = True
        else:
            raise ValueError("Existing compatibility installation differs; restore or review it first")
    if not upgrading and (backup.exists() or native.exists()):
        raise ValueError("Companion or backup already exists without manifest; review before installing")
    source = source or resolve_source(target, profile)
    source_data = source.read_bytes()
    patched = patch_native(source_data, profile=profile)
    previous = backup.read_bytes() if upgrading else target.read_bytes()
    with tempfile.TemporaryDirectory(prefix="agy-aicode-build-") as directory:
        launcher_path = Path(directory) / "agy.exe"
        if profile == "cli":
            build_launcher(launcher_path, digest(patched))
        else:
            build_launcher(launcher_path, digest(patched), profile=profile)
        launcher = launcher_path.read_bytes()
    manifest = {"patch_version": PATCH_VERSION, "native_version": "2.32.1" if profile == "manager" else "1.5.9",
                "runtime_sha256": runtime_fingerprint(),
                "profile": profile, "source_sha256": digest(source_data),
                "source_url": SOURCE_URL if profile != "manager" else "installed Manager 2.32.1 bundle",
                "native_sha256": digest(patched), "launcher_sha256": digest(launcher),
                "previous_sha256": digest(previous), "license_checks_preserved": True,
                "changes": [{"offset": hex(at), "before": old, "after": new}
                            for at, old, new in (MANAGER_PATCHES if profile == "manager" else PATCHES)]}
    if upgrading:
        # Windows maps active executables read-only. Fail before changing any
        # file if a foreground app still owns either executable.
        for executable in (target, native):
            with executable.open("r+b"):
                pass
        old_files = {file: file.read_bytes() for file in (native, target, manifest_path)}
        changed = []
        try:
            for file, content in ((native, patched), (target, launcher),
                                  (manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())):
                atomic_write(file, content)
                changed.append(file)
        except Exception:
            for file in reversed(changed):
                atomic_write(file, old_files[file])
            raise
        return status(target)
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
    parser.add_argument("--profile", choices=("cli", "hub", "manager", "ide"), default="cli")
    parser.add_argument("--source", type=Path, help="Audited source executable for the selected Windows x64 profile")
    options = parser.parse_args(args)
    try:
        if options.profile == "ide":
            from patcher.ide.aicode import install as ide_install, status as ide_status, restore as ide_restore
            operation = {"install": ide_install, "status": ide_status, "restore": ide_restore}[options.agy_aicode]
            result = operation(options.target)
            print(json.dumps(result, indent=2))
            return 1 if result["state"] == "modified" else 0
        if options.agy_aicode == "install":
            result = install(options.target, options.source, profile=options.profile)
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
