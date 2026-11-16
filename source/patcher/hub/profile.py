"""Проверенный профиль протокола IDE 2.7.0 / agy 1.5.9, только чтение."""

import hashlib
import json
from pathlib import Path


PROFILE = "hub_auth"
KNOWN_BINARIES = {
    "8803c289cac04d6e53f2211592d9981c2d5760bc0d9b4a7f222ef6ecc7abb38a": "original",
    "834c15e66600b2a5893e93a6343dbff7602eb82eae5952c65a4465cecef3e0c5": "legacy_cli_patch",
}


def inspect_backend(path):
    result = {
        "profile": PROFILE, "supported": False, "version": None,
        "platform": None, "binary_state": "unknown", "sha256": None,
        "read_only": True, "auth_status": "not_checked",
    }
    with open(path, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result["sha256"] = digest
    manifest_path = Path(path).with_name("agy-aicode-patch.json")
    if manifest_path.is_file():
        from patcher.agy.aicode import status, PREBUILT_NATIVE_HASHES
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
            if (manifest.get("profile") == "hub" and status(Path(path))["state"] == "installed"
                    and manifest.get("native_sha256") in set(PREBUILT_NATIVE_HASHES['hub']) | {
                        "f4aa5af8dec3ce520d801411f6a52e9f3dcfe9014d1285c98715519623cef7f9",
                        "e903bb38c63a58a7e4bde7099ae02879f4ab65e6d7c1f5a772d2822e2aa26794"}):
                result.update(supported=True, version="1.5.9", platform="windows_x64", binary_state="aicode_compatibility")
                return result
        except (ValueError, KeyError):
            return result
    if digest in KNOWN_BINARIES:
        result.update(supported=True, version="1.5.9", platform="windows_x64",
                      binary_state=KNOWN_BINARIES[digest])
    return result
