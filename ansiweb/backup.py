"""Backup and restore of everything in the data folder except the cache.

The cache is excluded because it can be re-downloaded, but uploaded payloads
(drivers, scripts, registry files) cannot, so those are included.
"""
import datetime as dt
import io
import json
import os
import shutil
import tarfile
import tempfile

from . import __version__, paths, store

# Relative to DATA_DIR. Everything here is either configuration or unrecoverable.
INCLUDE = ["config.yml", "manifest.json", "inventory", "reports", "admin.json", "users.json",
           ".vault_pass", "app_secrets.vault", "ansiweb.db", ".flask_secret"]
PAYLOAD_DIRS = ["cache/drivers", "cache/scripts", "cache/registry", "branding"]
MARKER = "ansiweb-backup.json"


def filename() -> str:
    return "ansiweb-backup-" + dt.datetime.now().strftime("%Y%m%d-%H%M") + ".tar.gz"


def create() -> bytes:
    """Return a .tar.gz of the configuration, secrets and uploaded payloads."""
    buf = io.BytesIO()
    marker = json.dumps({"product": "AnsiWEB", "version": __version__,
                         "created": dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")},
                        indent=2).encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(MARKER)
        info.size = len(marker)
        info.mtime = int(dt.datetime.now().timestamp())
        tar.addfile(info, io.BytesIO(marker))
        for rel in INCLUDE + PAYLOAD_DIRS:
            src = paths.DATA_DIR / rel
            if src.exists():
                tar.add(src, arcname=rel)
    return buf.getvalue()


def _safe_members(tar: tarfile.TarFile, allowed: list):
    """Yield only regular files/dirs inside the allowed paths - no absolute paths,
    no '..', no symlinks or devices."""
    for m in tar.getmembers():
        name = m.name.lstrip("./")
        if name == MARKER:
            continue
        if not (m.isfile() or m.isdir()):
            continue
        if name.startswith("/") or ".." in name.split("/"):
            continue
        if any(name == a or name.startswith(a + "/") for a in allowed):
            m.name = name
            yield m


def restore(fileobj) -> dict:
    """Replace the current configuration and payloads with a backup archive.

    The archive is unpacked to a temporary folder and checked before anything
    live is touched, so a bad file cannot leave a half-restored installation.
    """
    tmp = tempfile.mkdtemp(prefix="ansiweb-restore-", dir=paths.DATA_DIR)
    try:
        try:
            with tarfile.open(fileobj=fileobj, mode="r:gz") as tar:
                members = list(_safe_members(tar, INCLUDE + PAYLOAD_DIRS))
                if not members:
                    raise store.ValidationError("That archive holds no AnsiWEB data.")
                # Our own filter above already rejects anything outside the data
                # folder; Python's data filter is a second, independent check.
                try:
                    tar.extractall(tmp, members=members, filter="data")
                except TypeError:          # Python older than 3.12
                    tar.extractall(tmp, members=members)
        except tarfile.TarError:
            raise store.ValidationError("That file is not a readable .tar.gz archive.")

        if not os.path.exists(os.path.join(tmp, "config.yml")):
            raise store.ValidationError("That archive has no config.yml, so it is not an AnsiWEB backup.")
        # Fail before touching anything live if the configuration is unusable
        import yaml
        cfg = yaml.safe_load(open(os.path.join(tmp, "config.yml"), encoding="utf-8").read()) or {}
        if "apps" not in cfg or "settings" not in cfg:
            raise store.ValidationError("The config.yml in that archive is not valid.")

        restored = []
        for rel in INCLUDE + PAYLOAD_DIRS:
            src = os.path.join(tmp, rel)
            if not os.path.exists(src):
                continue
            dst = paths.DATA_DIR / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
            shutil.move(src, dst)
            if os.path.isfile(dst):
                os.chmod(dst, 0o600 if rel.startswith(".") or rel.endswith(("vault", "json")) else 0o644)
            restored.append(rel)
        for rel in PAYLOAD_DIRS:
            d = paths.DATA_DIR / rel
            if d.is_dir():
                os.chmod(d, 0o755)
                for f in d.iterdir():
                    if f.is_file():
                        os.chmod(f, 0o644)
        store.regenerate_all()
        return {"restored": restored}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
