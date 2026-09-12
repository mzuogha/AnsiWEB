"""Uploaded payloads: device drivers, scripts and registry files.

Unlike apps, these are files the administrator uploads rather than something
AnsiWEB fetches, so their metadata lives in config.yml next to the entry and
the file itself is stored under cache/<kind>/ and served at /software/<kind>/.
"""
import os
import re
import tempfile

from . import cache, paths, store

KINDS = {
    "drivers": {
        "label": "Device driver",
        "plural": "Device drivers",
        "extensions": (".zip",),
        "hint": "A .zip containing the driver's .inf file (and its .cat/.sys files). "
                "AnsiWEB unpacks it on the PC and installs it with pnputil.",
    },
    "scripts": {
        "label": "Script",
        "plural": "Scripts",
        "extensions": (".ps1", ".cmd", ".bat"),
        "hint": "A PowerShell (.ps1) or command (.cmd/.bat) script. It runs as SYSTEM on the PC.",
    },
    "registry": {
        "label": "Registry file",
        "plural": "Registry files",
        "extensions": (".reg",),
        "hint": "A .reg file exported from Registry Editor. It is merged with reg import.",
    },
}

RUN_MODES = {
    "once": "Once per PC (skipped afterwards)",
    "changed": "Again whenever the file changes",
    "always": "Every deployment",
}


def kind_dir(kind: str):
    d = paths.CACHE_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o755)
    except PermissionError:
        pass
    return d


def allowed(kind: str, filename: str) -> bool:
    return filename.lower().endswith(KINDS[kind]["extensions"])


def store_file(kind: str, entry_id: str, file_storage) -> dict:
    """Save an uploaded payload. Returns metadata to merge into the config entry."""
    name = file_storage.filename or ""
    ext = os.path.splitext(name)[1].lower()
    if not allowed(kind, name):
        raise store.ValidationError(
            f"{KINDS[kind]['label']} must be a {' or '.join(KINDS[kind]['extensions'])} file.")
    d = kind_dir(kind)
    fname = f"{entry_id}{ext}"
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".up-")
    os.close(fd)
    try:
        file_storage.save(tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, d / fname)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    path = d / fname
    # Remove a payload with a different extension left over from an earlier upload
    for old in d.glob(entry_id + ".*"):
        if old.name != fname:
            old.unlink()
    return {"file": fname, "original_name": name, "sha256": cache.sha256_file(path),
            "size": path.stat().st_size, "uploaded": cache.now()}


def delete_file(kind: str, entry: dict) -> None:
    f = entry.get("file")
    if f:
        try:
            (kind_dir(kind) / f).unlink()
        except FileNotFoundError:
            pass


def prune(cfg: dict) -> None:
    """Delete payload files whose config entry is gone."""
    for kind in KINDS:
        keep = {e["file"] for e in cfg.get(kind, []) if e.get("file")}
        for f in kind_dir(kind).iterdir():
            if f.is_file() and f.name not in keep and not f.name.startswith(".up-"):
                f.unlink()


def present(kind: str, entry: dict) -> bool:
    return bool(entry.get("file")) and (kind_dir(kind) / entry["file"]).exists()


def script_shell(entry: dict) -> str:
    return "powershell" if str(entry.get("file", "")).lower().endswith(".ps1") else "cmd"


def new_id(cfg: dict, kind: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or kind).lower()).strip("-")[:40] or kind
    used = {e["id"] for e in cfg.get(kind, [])}
    if base not in used:
        return base
    for n in range(2, 100):
        if f"{base}-{n}" not in used:
            return f"{base}-{n}"
    raise store.ValidationError("Too many entries with a similar name.")
