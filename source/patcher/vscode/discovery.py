import os
import glob
import json
import re


def _user_home():
    """Домашний каталог пользователя. На POSIX при запуске через sudo
    возвращает дом вызвавшего пользователя (~user), а не /root,
    чтобы ~/.vscode-server и ~/.gemini находились корректно."""
    if os.name == "posix":
        from patcher.utils.file import get_posix_invoking_user_home
        home = get_posix_invoking_user_home()
        if home:
            return home
    return os.path.expanduser("~")


def clean_path(raw_path):
    """Убирает кавычки/пробелы по краям — как в ide/agy discovery."""
    return raw_path.strip().strip('"').strip("'")


def _extension_roots():
    """Корневые каталоги расширений VS Code (обычный, Insiders, OSS, remote-server).
    Первым идёт переопределение через env VSCODE_EXTENSIONS, если задано."""
    home = _user_home()
    roots = []
    env = os.environ.get("VSCODE_EXTENSIONS")
    if env and os.path.isdir(env):
        roots.append(env)
    roots += [
        os.path.join(home, ".vscode", "extensions"),
        os.path.join(home, ".vscode-insiders", "extensions"),
        os.path.join(home, ".vscode-oss", "extensions"),
        os.path.join(home, ".vscode-server", "extensions"),
        os.path.join(home, ".vscode-server-insiders", "extensions"),
    ]
    seen = set()
    out = []
    for r in roots:
        key = os.path.normcase(os.path.abspath(r))
        if key not in seen and os.path.isdir(r):
            seen.add(key)
            out.append(r)
    return out


def find_extension_dir():
    """Возвращает новейший каталог расширения google.google-antigravity-*.
    Имя каталога версионированное (например google.google-antigravity-1.0.0),
    поэтому ищем по маске и сортируем по mtime (новейший первым)."""
    candidates = []
    for root in _extension_roots():
        candidates += glob.glob(
            os.path.join(root, "google.google-antigravity-*")
        )
    dirs = [d for d in candidates if os.path.isdir(d)]
    if not dirs:
        return ""
    dirs.sort(key=lambda d: os.path.getmtime(d), reverse=True)
    return dirs[0]


def find_extension_js():
    """Разрешает main из манифеста, включая dist и делегирование в vscode/."""
    ext_dir = find_extension_dir()
    if not ext_dir:
        return ""
    try:
        return inspect_extension_dir(ext_dir)["path"]
    except (OSError, ValueError):
        return ""


def _contained_path(directory, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError("Extension main must be a non-empty relative path")
    root = os.path.realpath(directory)
    target = os.path.realpath(os.path.join(root, relative))
    if os.path.isabs(relative) or os.path.commonpath([root, target]) != root:
        raise ValueError("Extension entry escapes its installation directory")
    return target


def inspect_extension_dir(directory):
    """Читает main/version и определяет режим без запуска JS и backend."""
    manifest_path = os.path.join(directory, "package.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        manifest = {}
    if not isinstance(manifest, dict):
        raise ValueError("Extension manifest must be a JSON object")
    entry = _contained_path(directory, manifest.get("main", "extension.js"))
    with open(entry, "r", encoding="utf-8") as f:
        content = f.read()
    implementation = entry
    # Встроенный адаптер IDE 2.7.0 передаёт управление этому пакету.
    if re.search(r"require\(\s*['\"]\.\./vscode/extension\.js['\"]\s*\)", content):
        implementation = _contained_path(directory, "vscode/extension.js")
        with open(implementation, "r", encoding="utf-8") as f:
            content = f.read()
    return {
        "directory": os.path.abspath(directory),
        "entry_path": entry,
        "path": implementation,
        "version": manifest.get("version"),
        "mode": "hub" if re.search(r"['\"]--hub['\"]", content) else "legacy",
    }


def find_bundled_extension_dir(main_js_path):
    from patcher.ide.discovery import get_app_root

    if not main_js_path:
        return ""
    root = os.path.join(get_app_root(main_js_path), "extensions")
    for name in ("agy", "google.google-antigravity"):
        candidate = os.path.join(root, name)
        if os.path.isdir(candidate):
            return candidate
    return ""


def inspect_extension_path(path):
    """Разрешает выбранный файл через манифест его расширения, если он есть."""
    parent = os.path.dirname(os.path.abspath(path))
    for directory in (parent, os.path.dirname(parent)):
        if os.path.isfile(os.path.join(directory, "package.json")):
            info = inspect_extension_dir(directory)
            if os.path.realpath(path) in (info["entry_path"], info["path"]):
                return info
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"path": path, "entry_path": path, "version": None,
            "mode": "hub" if re.search(r"['\"]--hub['\"]", content) else "legacy"}


def expected_hub_binary():
    """IDE/расширение с --hub используют agy, независимо от старого antigravity."""
    return os.path.join(_user_home(), ".gemini", "bin", "agy.exe" if os.name == "nt" else "agy")


def resolve_extension_path(raw_path):
    """Разрешает пользовательский путь к extension.js.
    Принимает: сам файл extension.js, каталог расширения
    (google.google-antigravity-*) или корневой каталог extensions."""
    if not raw_path:
        return ""
    cleaned = clean_path(raw_path)
    if not cleaned:
        return ""
    resolved = os.path.abspath(os.path.expandvars(os.path.expanduser(cleaned)))

    if os.path.isfile(resolved):
        if os.path.basename(resolved).lower() == "extension.js":
            return resolved
        # Произвольный файл — принимаем как есть (пользователь лучше знает)
        return resolved

    if os.path.isdir(resolved):
        if os.path.isfile(os.path.join(resolved, "package.json")):
            try:
                return inspect_extension_dir(resolved)["path"]
            except (OSError, ValueError):
                return ""
        # Каталог самого расширения
        direct = os.path.join(resolved, "extension.js")
        if os.path.isfile(direct):
            return direct
        # Корневой каталог extensions — ищем внутри google.google-antigravity-*
        hits = glob.glob(os.path.join(resolved, "google.google-antigravity-*"))
        hits = [h for h in hits if os.path.isdir(h)]
        if hits:
            hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            try:
                return inspect_extension_dir(hits[0])["path"]
            except (OSError, ValueError):
                return ""

    return ""


def find_gemini_antigravity_binary():
    """Бинарь, скачиваемый расширением в ~/.gemini/bin.
    Windows: antigravity.exe / agy.exe, macOS/Linux: antigravity / agy.
    Возвращает путь или ''. Используется ТОЛЬКО пунктом 'Antigravity VS Code Patch'."""
    home = _user_home()
    ext = ".exe" if os.name == "nt" else ""
    for name in ("antigravity", "agy"):
        p = os.path.join(home, ".gemini", "bin", name + ext)
        if os.path.isfile(p):
            return p
    return ""


def describe_gemini_binary_path():
    """Человекочитаемый ожидаемый путь к бинарю (для сообщений)."""
    name = "antigravity.exe" if os.name == "nt" else "antigravity"
    return os.path.join("~", ".gemini", "bin", name)
