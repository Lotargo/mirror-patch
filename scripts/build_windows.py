"""Build the standalone Windows patcher and package the matching committed source."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))
from patcher.agy.aicode import PREBUILT_NATIVE_HASHES, RUNTIME, build_launcher, digest, runtime_fingerprint
from patcher.constants import VERSION


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def collect_licenses(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "licenses").glob("*.txt"):
        shutil.copyfile(source, directory / source.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copyfile(python_license, directory / "Python-LICENSE.txt")
    for package in ("PyInstaller", "packaging", "altgraph", "pefile", "pywin32-ctypes", "setuptools", "pyinstaller-hooks-contrib"):
        distribution = importlib.metadata.distribution(package)
        candidates = [p for p in distribution.files or [] if
                      "dist-info" in str(p) and any(k in p.name.lower() for k in ("license", "copying"))]
        if not candidates:
            raise RuntimeError(f"No distribution license found for {package}")
        for i, file in enumerate(candidates):
            shutil.copyfile(distribution.locate_file(file), directory / f"{package}-{i}-{file.name}")
    go_root = subprocess.check_output(["go", "env", "GOROOT"], text=True).strip()
    shutil.copyfile(Path(go_root) / "LICENSE", directory / "Go-LICENSE.txt")


def build(output: Path, work: Path):
    runtime = work / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    for p in RUNTIME.iterdir():
        if p.name == "go.mod" or (p.suffix == ".go" and not p.name.endswith("_test.go")):
            shutil.copyfile(p, runtime / p.name)
    prebuilt = runtime / "prebuilt"
    prebuilt.mkdir(exist_ok=True)
    # A release must not silently bundle launchers left by an older patch.
    expected_launchers = {
        f"{profile}-{native_hash}.exe"
        for profile, hashes in PREBUILT_NATIVE_HASHES.items()
        for native_hash in hashes
    }
    for previous in prebuilt.glob("*.exe"):
        if previous.name not in expected_launchers:
            previous.unlink()
    manifest = {"runtime_sha256": runtime_fingerprint(), "assets": {}}
    for profile, hashes in PREBUILT_NATIVE_HASHES.items():
        for native_hash in hashes:
            name = f"{profile}-{native_hash}.exe"
            destination = prebuilt / name
            build_launcher(destination, native_hash, profile=profile)
            manifest["assets"][name] = digest(destination.read_bytes())
    (prebuilt / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    licenses = work / "licenses"
    collect_licenses(licenses)
    env = dict(os.environ, PYTHONHASHSEED="0", SOURCE_DATE_EPOCH=git("show", "-s", "--format=%ct", "HEAD"))
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console", "--noupx",
               "--name", "MirrorPatch-windows-x64", "--paths", str(ROOT / "source"),
               "--version-file", str(ROOT / "source/version.txt"),
               "--distpath", str(output), "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
               "--add-data", str(runtime) + os.pathsep + "patcher/agy/aicode_runtime",
               "--add-data", str(licenses) + os.pathsep + "licenses",
               "--add-data", str(ROOT / "LICENSE") + os.pathsep + ".",
               "--add-data", str(ROOT / "THIRD_PARTY_NOTICES.md") + os.pathsep + ".",
               str(ROOT / "source/main.py")]
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    info = {"version": VERSION, "source_commit": git("rev-parse", "HEAD"),
            "working_tree_clean": not bool(git("status", "--porcelain")),
            "runtime_sha256": manifest["runtime_sha256"],
            "python": sys.version.split()[0], "go": subprocess.check_output(["go", "version"], text=True).strip(),
            "pyinstaller": importlib.metadata.version("PyInstaller"),
            "exe_sha256": digest((output / "MirrorPatch-windows-x64.exe").read_bytes())}
    (output / "build-info.json").write_text(json.dumps(info, indent=2) + "\n", "utf-8")


def package(output: Path, work: Path):
    if git("status", "--porcelain"):
        raise RuntimeError("Commit the release source and documentation before packaging")
    info = json.loads((output / "build-info.json").read_text("utf-8"))
    if info["source_commit"] != git("rev-parse", "HEAD") or not info["working_tree_clean"]:
        raise RuntimeError("Build must match the clean release commit; rebuild before packaging")
    exe = output / "MirrorPatch-windows-x64.exe"
    if digest(exe.read_bytes()) != info["exe_sha256"]:
        raise RuntimeError("Built EXE changed before packaging")
    source_zip = output / f"MirrorPatch-v{VERSION}-source.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=MirrorPatch-{VERSION}/",
                    f"--output={source_zip}", "HEAD"], cwd=ROOT, check=True)
    bundle = output / "MirrorPatch-windows-x64.zip"
    timestamp = time.gmtime(int(git("show", "-s", "--format=%ct", "HEAD")))[:6]
    files = {"MirrorPatch-windows-x64.exe": exe, "README.md": ROOT / "README.md",
             "LICENSE": ROOT / "LICENSE", "THIRD_PARTY_NOTICES.md": ROOT / "THIRD_PARTY_NOTICES.md",
             "CHANGELOG.md": ROOT / "CHANGELOG.md",
             "build-info.json": output / "build-info.json"}
    for folder in (ROOT / "docs", ROOT / "releases"):
        files.update({p.relative_to(ROOT).as_posix(): p for p in folder.rglob("*")
                      if p.is_file() and p.suffix in (".md", ".svg")})
    files.update({"licenses/" + p.name: p for p in (work / "licenses").iterdir() if p.is_file()})
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(files.items()):
            entry = zipfile.ZipInfo("MirrorPatch/" + name, timestamp)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())
    assets = [exe, bundle, source_zip, output / "build-info.json"]
    checksums = "".join(f"{digest(p.read_bytes())}  {p.name}\n" for p in assets)
    (output / "SHA256SUMS.txt").write_text(checksums, "ascii")
    print(checksums)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--build-only", action="store_true", help="Build a candidate without packaging source")
    modes.add_argument("--package-only", action="store_true", help="Package a matching clean build")
    options = parser.parse_args()
    if os.name != "nt":
        parser.error("Build the Windows executable on Windows")
    output, work = ROOT / "dist/release", ROOT / "build/windows"
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    if not options.package_only:
        build(output, work)
    if not options.build_only:
        package(output, work)


if __name__ == "__main__":
    main()
