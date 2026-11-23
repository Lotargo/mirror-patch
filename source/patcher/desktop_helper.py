"""Bounded JSON interface for the portable AGY desktop. Local files only."""

from __future__ import annotations
import argparse, contextlib, datetime, hashlib, io, json, os, re, tempfile
from pathlib import Path
from urllib.parse import urlparse
from patcher import diagnostics
from patcher.agy import aicode
from patcher.agy import official
from patcher.ide import aicode as ide_aicode
from patcher.utils.runtime import unattended

MODULES = {
    "ide": ("Antigravity IDE", ["ide", "ide_ext", "ide_hub"]),
    "desktop": ("Antigravity Desktop", ["manager"]),
    "vscode": ("Расширение VS Code", ["vscode_ext", "vscode_bin"]),
    "cli": ("CLI", ["agy"]),
}

AUTO_UPDATE_SIDECAR = "agy.version.json"
AUTO_UPDATE_HOST = "antigravity-cli-auto-updater-974169037036.us-central1.run.app"


def auto_update_info(path: Path):
    """Проверяет, что неизвестный agy.exe это официальное автообновление."""
    sidecar = path.with_name(AUTO_UPDATE_SIDECAR)
    try:
        if path.is_symlink() or sidecar.is_symlink() or sidecar.stat().st_size > 16384:
            return None
        data = json.loads(sidecar.read_text("utf-8"))
        stats = path.stat()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha512").hexdigest()
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict) or data.get("schemaVersion") != 1:
        return None
    version, size, recorded = data.get("version"), data.get("size"), data.get("sha512")
    base = data.get("releaseBaseUrl")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return None
    if not isinstance(size, int) or size != stats.st_size:
        return None
    if not isinstance(recorded, str) or not re.fullmatch(r"[0-9a-fA-F]{128}", recorded):
        return None
    if recorded.lower() != digest.lower():
        return None
    if not isinstance(base, str):
        return None
    try:
        origin = urlparse(base)
        if (origin.scheme != "https" or origin.hostname != AUTO_UPDATE_HOST
                or origin.username is not None or origin.password is not None
                or origin.port not in (None, 443)):
            return None
    except ValueError:
        return None
    return {"version": version, "sha512": recorded, "releaseBaseUrl": base}


def native_profile(c):
    return (
        "manager"
        if c["id"] == "manager"
        else "hub"
        if c["id"] == "ide_hub"
        or c.get("mode") == "hub"
        or c.get("shared_with") == "ide_hub"
        else "cli"
    )


def prior_files(path):
    return (
        any(
            Path(str(path) + suffix).exists()
            for suffix in [".agybak", ".bak", ".before-aicode", ".aicode.json"]
        )
        or path.with_name(aicode.MANIFEST_NAME).exists()
    )


def native_info(path, profile):
    policy = official.inspect(path)
    if policy is not None:
        return ("not_required" if policy["verified"] else "modified"), False
    backup, native, manifest = aicode.paths(path)
    if manifest.exists():
        data = json.loads(manifest.read_text("utf-8"))
        status = aicode.status(path)
        if data.get("profile") != profile:
            raise ValueError(
                "Профиль установленного backend не соответствует назначению."
            )
        if status["state"] == "installed":
            return (
                "patched"
                if data.get("runtime_sha256") == aicode.runtime_fingerprint()
                else "outdated",
                True,
            )
        # Never overwrite unrecognized changed executables, or a damaged historical backup.
        valid_backup = backup.is_file() and aicode.digest(
            backup.read_bytes()
        ) == data.get("previous_sha256")
        accepted = (
            aicode.MANAGER_SOURCE_HASHES
            if profile == "manager"
            else {aicode.SOURCE_SHA256}
        )
        target_known = aicode.digest(path.read_bytes()) in accepted | {
            data.get("launcher_sha256")
        } or auto_update_info(path) is not None
        native_known = not native.exists() or aicode.digest(
            native.read_bytes()
        ) in accepted | set(aicode.PREBUILT_NATIVE_HASHES.get(profile, ()))
        return "modified", bool(valid_backup and target_known and native_known)
    accepted = (
        aicode.MANAGER_SOURCE_HASHES if profile == "manager" else {aicode.SOURCE_SHA256}
    )
    return "unpatched", aicode.digest(path.read_bytes()) in accepted


def inspect():
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        raw = diagnostics.get_components_status()
    components = []
    for original in raw["components"]:
        c = {
            k: original.get(k)
            for k in [
                "id",
                "name",
                "path",
                "installed",
                "required",
                "version",
                "mode",
                "profile",
                "shared_with",
                "prediction_route",
            ]
        }
        c["status"] = original["status"]
        c["can_heal"] = False
        c["detail"] = ""
        path = Path(c["path"]) if c["path"] else None
        try:
            if not c["installed"]:
                c["status"] = "missing"
            elif c["id"] in ["manager", "agy", "ide_hub", "vscode_bin"]:
                c["operation"] = "native"
                c["repair_profile"] = native_profile(c)
                c["status"], c["can_heal"] = native_info(path, c["repair_profile"])
                if c["status"] == "unpatched" and prior_files(path):
                    c["status"] = "modified"
            elif c["id"] == "ide" and (
                c.get("mode") == "hub" or c.get("profile") == "aicode_ide"
            ):
                c["operation"] = "ide"
                backup, manifest = ide_aicode.paths(path)
                if manifest.exists():
                    result = ide_aicode.status(path)
                    c["status"] = (
                        "patched" if result["state"] == "installed" else "modified"
                    )
                    if result["state"] == "installed":
                        c["can_heal"] = True
                    else:
                        data = json.loads(manifest.read_text("utf-8"))
                        c["can_heal"] = (
                            backup.is_file()
                            and aicode.digest(backup.read_bytes())
                            == data.get("previous_sha256")
                            and aicode.digest(path.read_bytes())
                            in {
                                ide_aicode.SOURCE_SHA256,
                                ide_aicode.PRISTINE_SOURCE_SHA256,
                            }
                        )
                else:
                    c["status"] = "modified" if backup.exists() else "unpatched"
                    c["can_heal"] = not backup.exists() and aicode.digest(
                        path.read_bytes()
                    ) in {ide_aicode.SOURCE_SHA256, ide_aicode.PRISTINE_SOURCE_SHA256}
            elif c["mode"] == "hub":
                c["status"] = "not_required"
                c["detail"] = (
                    "Использует общий Hub; отдельный патч расширения не требуется."
                )
            else:
                c["operation"] = "legacy_ide" if c["id"] == "ide" else "extension"
                c["can_heal"] = c["status"] in ["patched", "unpatched", "partial"]
                if c["status"] in ["partial", "unpatched"] and prior_files(path):
                    c["status"] = "modified"
            if c["status"] == "patched":
                c["detail"] = "Целостность файлов подтверждена."
            elif c["status"] == "outdated":
                c["detail"] = "Есть обновление runtime патча."
            elif c["status"] == "not_required" and c.get("prediction_route") == "official":
                c["detail"] = "Используется проверенный штатный backend; AICode-лаунчер не требуется."
            elif c["status"] == "modified":
                c["detail"] = "Файлы изменились или патч применён частично."
                if path and c.get("operation") == "native":
                    auto = auto_update_info(path)
                    if auto:
                        c["auto_update_version"] = auto["version"]
                        c["detail"] = (
                            f"Официальное автообновление v{auto['version']} перезаписало "
                            "лаунчер; лечение сохранит его копию и восстановит патч."
                        )
            elif c["status"] != "not_required" and c["installed"] and not c["can_heal"]:
                c["detail"] = (
                    "Неизвестная сборка или неполный набор резервных файлов; автоматическая запись запрещена."
                )
        except (OSError, ValueError, KeyError) as exc:
            c.update(status="error", can_heal=False, detail=str(exc))
        components.append(c)
    indexed = {c["id"]: c for c in components}
    modules = []
    for mid, (name, ids) in MODULES.items():
        selected = [indexed[i] for i in ids if i in indexed]
        primary = indexed.get(
            {"desktop": "manager", "cli": "agy", "vscode": "vscode_ext", "ide": "ide"}[
                mid
            ]
        )
        installed = bool(primary and primary["installed"])
        if (
            installed
            and mid == "vscode"
            and primary.get("mode") == "hub"
            and "ide_hub" in indexed
        ):
            selected.append(indexed["ide_hub"])
        if installed and mid == "cli" and primary.get("shared_with") in indexed:
            selected.append(indexed[primary["shared_with"]])
        relevant = [c for c in selected if c["installed"] or c.get("required")]
        states = {c["status"] for c in relevant}
        state = (
            "missing"
            if not installed
            else "patched"
            if states and states <= {"patched", "not_required"}
            else "error"
            if "error" in states
            else "modified"
            if states & {"modified", "partial"}
            else "outdated"
            if "outdated" in states
            else "unpatched"
        )
        modules.append(
            {
                "id": mid,
                "name": name,
                "installed": installed,
                "status": state,
                "can_heal": installed
                and any(
                    c["can_heal"] and c["status"] not in ["patched", "not_required"]
                    for c in selected
                ),
                "components": selected,
            }
        )
    return {
        "protocol": 1,
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "modules": modules,
    }


def transaction(updates):
    originals = {p: p.read_bytes() if p.exists() else None for p, _ in updates}
    changed = []
    try:
        for path, data in updates:
            aicode.atomic_write(path, data() if callable(data) else data)
            changed.append(path)
    except Exception:
        for path in reversed(changed):
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                aicode.atomic_write(path, originals[path])
        raise


def repair_native(path, profile):
    if official.inspect(path) is not None:
        raise ValueError("Выбран штатный маршрут. Автоматическая установка AICode-лаунчера запрещена.")
    backup, native, manifest_path = aicode.paths(path)
    if not manifest_path.exists() or aicode.status(path)["state"] == "installed":
        return aicode.install(path, profile=profile)
    state, allowed = native_info(path, profile)
    if not allowed:
        raise ValueError("Не удалось проверить изменённую сборку и резервную копию.")
    old = json.loads(manifest_path.read_text("utf-8"))
    native_data = native.read_bytes() if native.exists() else b""
    if aicode.digest(native_data) not in aicode.PREBUILT_NATIVE_HASHES.get(profile, ()):
        source = aicode.resolve_source(path, profile)
        native_data = aicode.patch_native(source.read_bytes(), profile=profile)
        old["source_sha256"] = aicode.digest(source.read_bytes())
    with tempfile.TemporaryDirectory(prefix="agy-heal-") as directory:
        launcher = Path(directory) / "launcher.exe"
        aicode.build_launcher(launcher, aicode.digest(native_data), profile=profile)
        launcher_data = launcher.read_bytes()
    old.update(
        native_sha256=aicode.digest(native_data),
        launcher_sha256=aicode.digest(launcher_data),
        runtime_sha256=aicode.runtime_fingerprint(),
    )
    updates = [
        (native, native_data),
        (path, launcher_data),
        (manifest_path, (json.dumps(old, indent=2) + "\n").encode()),
    ]
    auto = auto_update_info(path)
    if auto:
        # Сохраняем официальный бинарник и его sidecar до замены, чтобы
        # автообновление можно было вернуть без повторной загрузки.
        stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
        suffix = f".before-heal-{auto['version']}-{stamp}"
        atomic = aicode.atomic_write
        atomic(path.with_name(path.name + suffix), path.read_bytes())
        sidecar = path.with_name(AUTO_UPDATE_SIDECAR)
        sidecar_data = sidecar.read_bytes()
        atomic(path.with_name(sidecar.name + suffix), sidecar_data)

        def synced_sidecar():
            record = json.loads(sidecar_data)
            stats = path.stat()
            with path.open("rb") as stream:
                sha512 = hashlib.file_digest(stream, "sha512").hexdigest()
            record.update(
                size=stats.st_size, mtimeMs=stats.st_mtime_ns / 1e6,
                ino=stats.st_ino, sha512=sha512,
            )
            return (json.dumps(record, indent=2) + "\n").encode()

        updates.append((sidecar, synced_sidecar))
        old["auto_update_replaced_version"] = auto["version"]
        old["sidecar_synced"] = True
        updates[2] = (manifest_path, (json.dumps(old, indent=2) + "\n").encode())
    transaction(updates)
    return aicode.status(path)


def apply(c):
    path = Path(c["path"])
    if path.is_symlink():
        raise ValueError("Символические ссылки автоматически не изменяются.")
    if c["operation"] == "native":
        return repair_native(path, c["repair_profile"])
    if c["operation"] == "ide":
        backup, manifest = ide_aicode.paths(path)
        if not manifest.exists():
            return ide_aicode.install(path)
        if ide_aicode.status(path)["state"] == "installed":
            return
        data = json.loads(manifest.read_text("utf-8"))
        patched = ide_aicode.patch_frontend(path.read_bytes())
        data["sha256"] = aicode.digest(patched)
        transaction(
            [(path, patched), (manifest, (json.dumps(data, indent=2) + "\n").encode())]
        )
        return
    content = path.read_bytes().decode("utf-8")
    if c["operation"] == "extension":
        from patcher.vscode.patcher import _apply_part1, _apply_part2, get_status

        result, _ = _apply_part1(content)
        result, _ = _apply_part2(result)
    else:
        from patcher.ide.patcher import apply_patches, get_status

        result, _ = apply_patches(content, c.get("version"))
    if get_status(result) != "patched":
        raise ValueError("Сигнатуры патча не подтверждены; файл не изменён.")
    recovery = path.with_name(
        path.name
        + ".before-desktop-heal-"
        + datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    )
    transaction([(recovery, path.read_bytes()), (path, result.encode("utf-8"))])


def _heal(module):
    initial = inspect()
    selected = [m for m in initial["modules"] if module == "all" or m["id"] == module]
    seen = set()
    actions = []
    with (
        unattended(),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        for m in selected:
            if not m["installed"]:
                continue
            for c in m["components"]:
                if c["status"] in ["patched", "not_required"] or not c["installed"]:
                    continue
                identity = os.path.normcase(os.path.realpath(c["path"]))
                if identity in seen:
                    continue
                seen.add(identity)
                action = {
                    "module": m["id"],
                    "id": c["id"],
                    "status": "failed",
                    "message": "",
                }
                try:
                    if not c["can_heal"]:
                        raise ValueError(c["detail"] or "Сборка не поддерживается.")
                    apply(c)
                    action["status"] = "applied"
                except PermissionError:
                    action["message"] = (
                        "Закройте приложение, использующее этот файл, и повторите лечение. Если доступ запрещён — запустите сервер с нужными правами."
                    )
                except Exception as exc:
                    action["message"] = str(exc)
                actions.append(action)
    final = inspect()
    by_id = {c["id"]: c for m in final["modules"] for c in m["components"]}
    for action in actions:
        c = by_id.get(action["id"])
        if action["status"] == "applied" and (not c or c["status"] != "patched"):
            action.update(
                status="failed", message="Повторная проверка не подтвердила лечение."
            )
    for m in final["modules"]:
        failed = [
            a
            for a in actions
            if a["status"] == "failed"
            and (
                a["module"] == m["id"]
                or any(c["id"] == a["id"] for c in m["components"])
            )
        ]
        if failed:
            m.update(status="error", last_error="; ".join(a["message"] for a in failed))
    final["actions"] = actions
    return final


@contextlib.contextmanager
def repair_lock():
    """Serialize mutations across desktop and helper processes, not only UI clicks."""
    directory = (
        Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "MirrorPatch"
    )
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "desktop-heal.lock").open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def heal(module):
    with repair_lock():
        return _heal(module)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "heal", "capabilities"])
    parser.add_argument("--module", choices=["all", *MODULES], default="all")
    args = parser.parse_args(argv)
    try:
        result = (
            {
                "protocol": 1,
                "runtime_sha256": aicode.runtime_fingerprint(),
                "modules": list(MODULES),
            }
            if args.action == "capabilities"
            else inspect()
            if args.action == "status"
            else heal(args.module)
        )
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"protocol": 1, "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=True,
            )
        )
        return 1
