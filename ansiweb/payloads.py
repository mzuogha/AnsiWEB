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
        "extensions": (".zip",),
        "hint": "A .zip containing the driver's .inf file (and its .cat/.sys files). "
                "AnsiWEB unpacks it on the PC and installs it with pnputil.",
    },
    "scripts": {
        "label": "Script",
        "extensions": (".ps1", ".cmd", ".bat"),
        "hint": "A PowerShell (.ps1) or command (.cmd/.bat) script. It runs as SYSTEM on the PC.",
    },
    "registry": {
        "label": "Registry file",
        "extensions": (".reg",),
        "hint": "A .reg file exported from Registry Editor. It is merged with reg import.",
    },
}

# Scripts and registry files are managed on one page: both are "run this on the
# PC" items, and which one a file is can be told from its extension.
GROUPS = {
    "files": {
        "kinds": ["drivers", "scripts", "registry"],
        "title": "Drivers, scripts & registry",
        "hint": "Everything you upload for the PCs to apply: driver packages (.zip of .inf files) are "
                "added to the Windows driver store, scripts (.ps1, .cmd, .bat) run as SYSTEM, and "
                "registry files (.reg) are merged with reg import. Upload any of them here - AnsiWEB "
                "works out which it is from the file.",
    },
}
# Which page a kind is managed on
GROUP_OF = {kind: group for group, meta in GROUPS.items() for kind in meta["kinds"]}


def kind_for_filename(filename: str, allowed: list | None = None) -> str:
    """Work out which kind an uploaded file is, from its extension."""
    kinds = allowed or list(KINDS)
    for kind in kinds:
        if (filename or "").lower().endswith(KINDS[kind]["extensions"]):
            return kind
    wanted = ", ".join(ext for kind in kinds for ext in KINDS[kind]["extensions"])
    raise store.ValidationError(f"That file type is not supported here. Expected one of: {wanted}")


RUN_MODES = {
    "once": "Once per PC (skipped afterwards)",
    "changed": "Again whenever the file changes",
    "always": "Every deployment",
}


PRINTER_DRIVERS = "printers"     # cache/printers/, served at /software/printers/


def store_printer_driver(entry_id: str, file_storage) -> dict:
    """Save a driver package (.zip of .inf files) staged with a printer."""
    if not (file_storage.filename or "").lower().endswith(".zip"):
        raise store.ValidationError("A printer driver must be a .zip containing the driver's .inf files.")
    saved = store_file(PRINTER_DRIVERS, entry_id, file_storage, extensions=(".zip",))
    return {"driver_file": saved["file"], "driver_original": saved["original_name"],
            "driver_sha256": saved["sha256"], "driver_size": saved["size"],
            "driver_uploaded": saved["uploaded"]}


def delete_printer_driver(entry: dict) -> None:
    f = entry.get("driver_file")
    if f:
        try:
            (kind_dir(PRINTER_DRIVERS) / f).unlink()
        except FileNotFoundError:
            pass


def printer_driver_present(entry: dict) -> bool:
    f = entry.get("driver_file")
    return bool(f) and (kind_dir(PRINTER_DRIVERS) / f).exists()


HOTFIX_DIR = "updates"


def hotfix_dir():
    return kind_dir(HOTFIX_DIR)


def store_hotfix(kb: str, file_storage) -> dict:
    """Save an update package (.msu or .cab) downloaded from the catalogue."""
    name = (file_storage.filename or "").lower()
    if not name.endswith((".msu", ".cab")):
        raise store.ValidationError("A Windows update is a .msu (or .cab) file from the "
                                    "Microsoft Update Catalog.")
    d = hotfix_dir()
    fname = f"{kb}{os.path.splitext(name)[1]}"
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
    return {"file": fname, "original": file_storage.filename,
            "sha256": cache.sha256_file(path), "size": path.stat().st_size,
            "added": cache.now()}


def hotfix_present(entry: dict) -> bool:
    f = entry.get("file")
    return bool(f) and (hotfix_dir() / f).exists()


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


def store_file(kind: str, entry_id: str, file_storage, extensions=None) -> dict:
    """Save an uploaded payload. Returns metadata to merge into the config entry."""
    name = file_storage.filename or ""
    ext = os.path.splitext(name)[1].lower()
    if extensions is None and not allowed(kind, name):
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
