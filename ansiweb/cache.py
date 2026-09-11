"""Local installer cache.

The control node downloads installers once and serves them to PCs from
http://<server>/software/, so PCs never need internet access to install apps.

Sources:
  winget  - version + download URL + SHA256 come from Microsoft's public winget
            package manifests on GitHub. The newest version is cached automatically.
  url     - a fixed download URL for a fixed version (used for pinned apps).
  upload  - an installer file uploaded through the web interface.
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import paths, store, vault

GITHUB_API = "https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests/"
GITHUB_RAW = "https://raw.githubusercontent.com/microsoft/winget-pkgs/master/manifests/"
USER_AGENT = "AnsiWEB/1.0 (+https://github.com/mzuogha/AnsiWEB)"
OFFICE_KEY = "__office__"
SKIP_TYPES = {"zip", "portable", "msix", "appx", "pwa"}
OK_RESPONSES = {"alreadyInstalled", "rebootRequiredToFinish", "rebootRequiredForInstall", "rebootInitiated"}

_manifest_lock = threading.RLock()


class CacheError(Exception):
    pass


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def version_key(v: str):
    return [int(x) for x in re.findall(r"\d+", str(v))] or [0]


def safe_version(v: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(v))[:60]


# ---- manifest ----------------------------------------------------------------
def load_manifest() -> dict:
    with _manifest_lock:
        return store.read_json(paths.MANIFEST_FILE, {})


def save_manifest(m: dict) -> None:
    with _manifest_lock:
        store.write_json(paths.MANIFEST_FILE, m)


def update_manifest_entry(key: str, **values) -> dict:
    with _manifest_lock:
        m = load_manifest()
        entry = m.get(key, {})
        entry.update(values)
        m[key] = entry
        save_manifest(m)
        return entry


# ---- HTTP helpers ------------------------------------------------------------
def _request(url: str, token: str = "", accept: str = ""):
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def get_json(url: str, token: str = ""):
    try:
        with urllib.request.urlopen(_request(url, token, "application/vnd.github+json"), timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        if exc.code == 403 and "rate limit" in body.lower():
            raise CacheError("GitHub rate limit reached. Add a GitHub token in Settings, or wait an hour.")
        if exc.code == 404:
            raise CacheError(f"Not found: {url}")
        raise CacheError(f"HTTP {exc.code} for {url}: {body}")
    except urllib.error.URLError as exc:
        raise CacheError(f"Cannot reach {url}: {exc.reason}")


def get_text(url: str) -> str:
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as r:
            return r.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        raise CacheError(f"HTTP {exc.code} for {url}")
    except urllib.error.URLError as exc:
        raise CacheError(f"Cannot reach {url}: {exc.reason}")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def download(url: str, dest, expected_sha: str = "", log=print) -> str:
    """Download to a temp file, verify SHA256, then move into place. Returns the SHA256."""
    dest_dir = os.path.dirname(dest)
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".dl-")
    os.close(fd)
    h = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(_request(url), timeout=120) as r, open(tmp, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            next_report = 0
            for chunk in iter(lambda: r.read(1024 * 1024), b""):
                out.write(chunk)
                h.update(chunk)
                size += len(chunk)
                if total and size >= next_report:
                    log(f"    {size * 100 // total:3d}%  ({size // 1048576} of {total // 1048576} MB)")
                    next_report += max(total // 5, 1)
        digest = h.hexdigest().upper()
        if expected_sha and digest != expected_sha.upper():
            raise CacheError(f"SHA256 mismatch: expected {expected_sha.upper()}, got {digest}. "
                             "The vendor may have just published a new build; it will be retried on the next check.")
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
        return digest
    except urllib.error.HTTPError as exc:
        raise CacheError(f"Download failed: HTTP {exc.code} for {url}")
    except urllib.error.URLError as exc:
        raise CacheError(f"Download failed: {exc.reason}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---- winget manifests ----------------------------------------------------------
def winget_dir(pkg_id: str) -> str:
    parts = pkg_id.split(".")
    return f"{pkg_id[0].lower()}/" + "/".join(urllib.parse.quote(p) for p in parts)


def winget_versions(pkg_id: str, token: str = "") -> list:
    listing = get_json(GITHUB_API + winget_dir(pkg_id), token)
    if not isinstance(listing, list):
        raise CacheError(f"Unexpected response for {pkg_id}")
    versions = [x["name"] for x in listing if x.get("type") == "dir" and re.match(r"^\d", x["name"])]
    if not versions:
        raise CacheError(f"No versions found for winget package {pkg_id}")
    return sorted(versions, key=version_key)


def winget_installer(pkg_id: str, version: str, types: list, arch: str = "x64") -> dict:
    url = f"{GITHUB_RAW}{winget_dir(pkg_id)}/{urllib.parse.quote(version)}/{urllib.parse.quote(pkg_id)}.installer.yaml"
    doc = yaml.safe_load(get_text(url)) or {}
    root = {k: v for k, v in doc.items() if k != "Installers"}
    candidates = []
    for inst in doc.get("Installers", []):
        merged = {**root, **inst}
        itype = str(merged.get("InstallerType", "")).lower()
        if merged.get("Architecture") not in (arch, "neutral") or itype in SKIP_TYPES:
            continue
        if merged.get("NestedInstallerType"):
            continue
        type_rank = types.index(itype) if itype in types else len(types)
        scope_rank = {"machine": 0, None: 1}.get(merged.get("Scope"), 2)
        locale = merged.get("InstallerLocale")
        locale_rank = 0 if locale in (None, "en-US") else 1
        candidates.append(((type_rank, scope_rank, locale_rank), merged, itype))
    if not candidates:
        raise CacheError(f"No {arch} installer in winget manifest {pkg_id} {version}")
    candidates.sort(key=lambda c: c[0])
    _, inst, itype = candidates[0]
    codes = {0, 3010}
    codes.update(int(c) for c in inst.get("InstallerSuccessCodes", []) or [])
    for rc in inst.get("ExpectedReturnCodes", []) or []:
        if rc.get("ReturnResponse") in OK_RESPONSES:
            codes.add(int(rc["InstallerReturnCode"]))
    ext = ".msi" if itype in ("msi", "wix") else ".exe"
    return {"url": inst["InstallerUrl"], "sha256": str(inst.get("InstallerSha256", "")).upper(),
            "installer_type": itype, "ext": ext, "success_codes": sorted(codes)}


# ---- per-app update --------------------------------------------------------------
def _replace_cached(app_id: str, new_file: str, new_version: str, **extra) -> None:
    """Record the new file; keep one previous version for rollback, delete older ones."""
    with _manifest_lock:
        m = load_manifest()
        entry = m.get(app_id, {})
        old_file, old_version = entry.get("file"), entry.get("version")
        old_prev = (entry.get("previous") or {}).get("file")
        if old_prev and old_prev not in (new_file, old_file):
            try:
                (paths.APPS_DIR / old_prev).unlink()
            except FileNotFoundError:
                pass
        if old_file and old_file != new_file:
            entry["previous"] = {"file": old_file, "version": old_version}
        entry.update({"file": new_file, "version": new_version, "status": "ok", "error": "",
                      "updated": now(), "checked": now()}, **extra)
        entry["size"] = (paths.APPS_DIR / new_file).stat().st_size
        m[app_id] = entry
        save_manifest(m)


def update_app(app: dict, token: str = "", log=print, force: bool = False) -> str:
    """Bring one app's cached installer up to date. Returns a short status string."""
    app_id = app["id"]
    entry = load_manifest().get(app_id, {})
    cached_ok = entry.get("file") and (paths.APPS_DIR / entry["file"]).exists()
    try:
        if app["source"] == "winget":
            if app.get("pinned") and app.get("version"):
                version = app["version"]
            else:
                version = winget_versions(app["winget_id"], token)[-1]
            update_manifest_entry(app_id, latest_seen=version, checked=now())
            if cached_ok and entry.get("version") == version and not force:
                log(f"  {app['name']}: {version} already cached")
                update_manifest_entry(app_id, status="ok", error="")
                return "current"
            types = [t.lower() for t in app.get("installer_types") or []]
            inst = winget_installer(app["winget_id"], version, types)
            fname = f"{app_id}_{safe_version(version)}{inst['ext']}"
            log(f"  {app['name']}: downloading {version} ({inst['installer_type']}) from {inst['url']}")
            sha = download(inst["url"], paths.APPS_DIR / fname, inst["sha256"], log)
            _replace_cached(app_id, fname, version, sha256=sha, source_url=inst["url"],
                            installer_type=inst["installer_type"], success_codes=inst["success_codes"])
            log(f"  {app['name']}: cached {version} (SHA256 verified)")
            return "updated"

        if app["source"] == "url":
            version = app["version"]
            update_manifest_entry(app_id, latest_seen=version, checked=now())
            if cached_ok and entry.get("version") == version and entry.get("source_url") == app["url"] and not force:
                log(f"  {app['name']}: {version} already cached (pinned)")
                update_manifest_entry(app_id, status="ok", error="")
                return "current"
            path = urllib.parse.urlparse(app["url"]).path.lower()
            ext = ".msi" if path.endswith(".msi") else ".exe"
            fname = f"{app_id}_{safe_version(version)}{ext}"
            log(f"  {app['name']}: downloading {version} from {app['url']}")
            sha = download(app["url"], paths.APPS_DIR / fname, app.get("sha256", ""), log)
            _replace_cached(app_id, fname, version, sha256=sha, source_url=app["url"],
                            installer_type=ext[1:], success_codes=[0, 3010, 1641])
            log(f"  {app['name']}: cached {version}")
            return "updated"

        # upload: nothing to fetch, just confirm the file is there
        if cached_ok:
            update_manifest_entry(app_id, status="ok", error="", checked=now())
            log(f"  {app['name']}: uploaded file {entry['file']} present")
            return "current"
        raise CacheError("No installer uploaded yet. Upload one on the Apps page.")
    except CacheError as exc:
        update_manifest_entry(app_id, status="error" if not cached_ok else "stale",
                              error=str(exc), checked=now())
        log(f"  {app['name']}: ERROR - {exc}" + ("  (keeping the previously cached version)" if cached_ok else ""))
        return "error"


def update_all(log=print, only: list | None = None, force: bool = False) -> dict:
    cfg = store.load()
    token = vault.app_secret("github_token")
    counts = {"current": 0, "updated": 0, "error": 0}
    apps = [a for a in cfg.get("apps", []) if a.get("enabled", True) and (not only or a["id"] in only)]
    log(f"Checking {len(apps)} app(s) for updates at {now()}")
    for app in apps:
        counts[update_app(app, token, log, force)] += 1
    # drop manifest entries for apps that no longer exist
    with _manifest_lock:
        m = load_manifest()
        known = {a["id"] for a in cfg.get("apps", [])} | {OFFICE_KEY}
        for key in [k for k in m if k not in known]:
            for f in (m[key].get("file"), (m[key].get("previous") or {}).get("file")):
                if f:
                    try:
                        (paths.APPS_DIR / f).unlink()
                    except FileNotFoundError:
                        pass
            del m[key]
        save_manifest(m)
    store.regenerate_all(cfg)
    log(f"Done: {counts['updated']} updated, {counts['current']} already current, {counts['error']} error(s)")
    return counts


def store_upload(app: dict, file_storage, version: str) -> None:
    """Save an installer uploaded through the web interface."""
    name = file_storage.filename or ""
    ext = ".msi" if name.lower().endswith(".msi") else ".exe"
    fname = f"{app['id']}_{safe_version(version)}{ext}"
    fd, tmp = tempfile.mkstemp(dir=paths.APPS_DIR, prefix=".up-")
    os.close(fd)
    try:
        file_storage.save(tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, paths.APPS_DIR / fname)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    _replace_cached(app["id"], fname, version, sha256=sha256_file(paths.APPS_DIR / fname),
                    source_url="uploaded: " + name, installer_type=ext[1:], success_codes=[0, 3010, 1641],
                    latest_seen=version)


# ---- Office -------------------------------------------------------------------
def office_finalize(version: str, log=print) -> None:
    """Move a freshly pulled Office build from staging into the cache and index it."""
    staged = paths.OFFICE_STAGING / "Office"
    data_dir = staged / "Data" / version
    if not data_dir.is_dir():
        raise CacheError(f"Staged Office build {version} not found in {paths.OFFICE_STAGING}")
    target = paths.OFFICE_DIR / "Office"
    old = paths.OFFICE_DIR / "Office.old"
    if old.exists():
        shutil.rmtree(old)
    if target.exists():
        target.rename(old)
    shutil.move(str(staged), str(target))
    if old.exists():
        shutil.rmtree(old)
    files = []
    for root, _dirs, names in os.walk(target):
        for n in sorted(names):
            full = os.path.join(root, n)
            os.chmod(full, 0o644)
            rel = os.path.relpath(full, paths.OFFICE_DIR).replace(os.sep, "/")
            log(f"  hashing {rel}")
            files.append({"path": rel, "size": os.path.getsize(full), "sha256": sha256_file(full)})
        os.chmod(root, 0o755)
    update_manifest_entry(OFFICE_KEY, version=version, files=files, status="ok", error="", updated=now(),
                          checked=now(), size=sum(f["size"] for f in files))
    store.regenerate_all()
    log(f"Office {version} cached: {len(files)} files, {sum(f['size'] for f in files) // 1048576} MB")


def office_setup_present() -> bool:
    return (paths.OFFICE_DIR / "setup.exe").exists()


def store_office_setup(file_storage) -> None:
    dest = paths.OFFICE_DIR / "setup.exe"
    file_storage.save(dest)
    os.chmod(dest, 0o644)
