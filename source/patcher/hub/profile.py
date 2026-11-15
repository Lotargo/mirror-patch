"""Проверенный профиль протокола IDE 2.7.0 / agy 1.5.9, только чтение."""

import hashlib


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
    if digest in KNOWN_BINARIES:
        result.update(supported=True, version="1.5.9", platform="windows_x64",
                      binary_state=KNOWN_BINARIES[digest])
    return result
