"""An explicit, hash-verified choice to keep native prediction routing."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

SUFFIX = ".official-routing.json"
AUTO_UPDATE_HOST = "antigravity-cli-auto-updater-974169037036.us-central1.run.app"


def verify_update_record(target, record, version):
    target = Path(target)
    if not isinstance(record, dict):
        return False
    base = urlparse(record.get("releaseBaseUrl", ""))
    recorded = record.get("sha512")
    if (record.get("schemaVersion") != 1 or record.get("version") != version
            or base.scheme != "https" or base.hostname != AUTO_UPDATE_HOST
            or base.username is not None or base.password is not None
            or base.port not in (None, 443) or target.is_symlink()
            or record.get("size") != target.stat().st_size
            or not isinstance(recorded, str)):
        return False
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha512").hexdigest()
    return digest == recorded.lower()


def sync_update_record(target, record):
    target = Path(target)
    record = dict(record)
    stats = target.stat()
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha512").hexdigest()
    record.update(size=stats.st_size, mtimeMs=stats.st_mtime_ns / 1e6,
                  ino=stats.st_ino, sha512=digest)
    return (json.dumps(record, indent=2) + "\n").encode()


def latest_update_backup(target, manifest):
    """Find the paired updater recovery saved by a verified compatibility heal."""
    version = manifest.get("auto_update_replaced_version")
    if version is None:
        return None
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid recorded native update version")
    target = Path(target)
    prefix = target.name + ".before-heal-" + version + "-"
    for candidate in sorted(target.parent.glob(prefix + "*"), reverse=True):
        stamp = candidate.name.removeprefix(prefix)
        sidecar = target.with_name("agy.version.json.before-heal-" + version + "-" + stamp)
        if not re.fullmatch(r"\d{14,24}", stamp):
            continue
        try:
            if (candidate.is_symlink() or sidecar.is_symlink()
                    or not candidate.is_file() or not sidecar.is_file()
                    or sidecar.stat().st_size > 16384):
                continue
            record = json.loads(sidecar.read_text("utf-8"))
            if not verify_update_record(candidate, record, version):
                continue
            return candidate, record
        except (OSError, ValueError, UnicodeError, AttributeError):
            continue
    raise ValueError("No verified official updater recovery pair; native restore was not applied")


def marker_path(target):
    target = Path(target)
    return target.with_name(target.name + SUFFIX)


def inspect(target):
    """None means no policy; a changed binary must never trigger AICode repair."""
    target = Path(target)
    marker = marker_path(target)
    if not marker.exists() and not marker.is_symlink():
        return None
    try:
        if marker.is_symlink() or marker.stat().st_size > 4096:
            raise ValueError("Invalid official routing policy")
        data = json.loads(marker.read_text("utf-8"))
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or data.get("prediction_route") != "official"
                or data.get("profile") not in ("manager", "hub", "cli")
                or not isinstance(data.get("sha256"), str)):
            raise ValueError("Invalid official routing policy")
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        verified = not target.is_symlink() and actual == data["sha256"]
        return {"verified": verified, "profile": data["profile"],
                "detail": "Штатный маршрут выбран; целостность нативного файла подтверждена."
                if verified else "Выбран штатный маршрут, но файл изменился; повторная проверка нужна до лечения."}
    except (OSError, ValueError, UnicodeError):
        return {"verified": False, "detail": "Не удалось проверить выбранный штатный маршрут; автоматический возврат AICode запрещён."}


def record(target, profile):
    """Called only after an explicit verified native restore, never as detection."""
    from .aicode import atomic_write
    target = Path(target)
    if profile not in ("manager", "hub", "cli") or not target.is_file() or target.is_symlink():
        raise ValueError("Cannot record official routing for this target")
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    data = {"schema_version": 1, "prediction_route": "official", "profile": profile, "sha256": digest}
    atomic_write(marker_path(target), (json.dumps(data, indent=2) + "\n").encode())
    return inspect(target)
