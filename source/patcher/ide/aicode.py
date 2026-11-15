"""Personal-account AICode licensing adapter for the audited IDE 2.7.0 frontend."""
import json
from pathlib import Path

from patcher.agy.aicode import atomic_write, digest

SOURCE_SHA256 = "166eb8c169ecf32aeef13c113c203af615ca192c485e025f6b7a851e8d61fa2c"
MARKER = "/* agy-aicode-personal-licenses-v1 */"
START = "async loadCodeAssist(t,n){"
END = "async onboardUser(t,n,r){"
LEGACY_START = 'let r=await this.makePostRequest("v1internal:loadCodeAssist"'


def patch_frontend(data: bytes) -> bytes:
    if digest(data) != SOURCE_SHA256:
        raise ValueError("Expected audited IDE 2.7.0 frontend; unknown or modified build")
    text = data.decode("utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("IDE licensing method signatures are not unique")
    start = text.index(START) + len(START)
    end = text.index(END, start)
    body = text[start:end]
    tail = body.index("const i=r.paidTier?.availableCredits")
    legacy = body[:tail]
    if not legacy.startswith(LEGACY_START):
        raise ValueError("Unexpected IDE loadCodeAssist implementation")
    # Reuse the application's authenticated OAuth transport; never create a
    # synthetic free license. No-license/HTTP errors remain failures.
    personal = (
        MARKER + "let r;if(!t?.isGcpTos){"
        "const token=t??await this.getOAuthTokenFromStateSyncIfUnexpired();"
        'if(!token)throw new Error("No OAuth token available");'
        "const response=await DKe(token,this._unifiedStateSyncService).request({"
        'url:"https://aicode.googleapis.com/v1alpha:fetchLicenses",method:"GET",'
        'headers:{"Content-Type":"application/json","User-Agent":this.userAgent}});'
        "const licenses=VAe(response.data).licenses;"
        'const license=Array.isArray(licenses)?licenses.find(x=>typeof x.userTier==="string"&&x.userTier.length>0):void 0;'
        'if(!license)throw new Error("AICode returned no license for this account");'
        "const tier={id:license.userTier,name:license.tierDisplayName??license.userTier,"
        "description:license.tierDisplayName??license.userTier};"
        "r=QP(Fre).fromJson({currentTier:tier,paidTier:tier,cloudaicompanionProject:\"\"},{ignoreUnknownFields:!0});"
        "}else{" + legacy.removeprefix("let ") + "}"
    )
    return (text[:start] + personal + body[tail:] + text[end:]).encode("utf-8")


def paths(target):
    target = Path(target)
    return target.with_name(target.name + ".before-aicode"), target.with_name(target.name + ".aicode.json")


def status(target):
    target = Path(target)
    backup, manifest_path = paths(target)
    if not manifest_path.exists():
        return {"state": "not-installed", "profile": "ide"}
    manifest = json.loads(manifest_path.read_text("utf-8"))
    checks = {"frontend": target.is_file() and digest(target.read_bytes()) == manifest["sha256"],
              "backup": backup.is_file() and digest(backup.read_bytes()) == manifest["previous_sha256"]}
    return {"state": "installed" if all(checks.values()) else "modified", "checks": checks,
            "profile": "ide", "version": "2.7.0"}


def install(target):
    target = Path(target).absolute()
    if not target.is_file() or target.is_symlink():
        raise ValueError("IDE target must be a regular main.js file")
    backup, manifest_path = paths(target)
    if manifest_path.exists():
        result = status(target)
        if result["state"] == "installed":
            return result
        raise ValueError("Existing IDE AICode files differ; review before replacing")
    if backup.exists():
        raise ValueError("IDE AICode backup already exists without manifest")
    original = target.read_bytes()
    result = patch_frontend(original)
    manifest = {"profile": "ide", "version": "2.7.0", "previous_sha256": digest(original),
                "sha256": digest(result), "license_source": "AICode fetchLicenses"}
    atomic_write(backup, original)
    try:
        atomic_write(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
        atomic_write(target, result)
    except Exception:
        if target.read_bytes() != original:
            atomic_write(target, original)
        manifest_path.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        raise
    return status(target)


def restore(target):
    target = Path(target).absolute()
    if status(target)["state"] != "installed":
        raise ValueError("IDE restore requires intact frontend, manifest and backup")
    backup, manifest_path = paths(target)
    atomic_write(target, backup.read_bytes())
    manifest_path.unlink()
    backup.unlink()
    return {"state": "restored", "profile": "ide"}
