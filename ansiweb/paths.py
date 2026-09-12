"""Filesystem locations. Everything that changes at runtime lives under ANSIWEB_DATA."""
import os
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
ANSIBLE_DIR = CODE_DIR / "ansible"
DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
SCRIPTS_DIR = CODE_DIR / "scripts"

DATA_DIR = Path(os.environ.get("ANSIWEB_DATA", "/var/lib/ansiweb")).resolve()
CONFIG_FILE = DATA_DIR / "config.yml"
MANIFEST_FILE = DATA_DIR / "manifest.json"
PLAN_FILE = DATA_DIR / "deploy_plan.json"
DB_FILE = DATA_DIR / "ansiweb.db"
LOG_DIR = DATA_DIR / "logs"
REPORT_DIR = DATA_DIR / "reports"

# Served by nginx at http://<server>/software/
CACHE_DIR = DATA_DIR / "cache"
APPS_DIR = CACHE_DIR / "apps"

INVENTORY_DIR = DATA_DIR / "inventory"
HOSTS_FILE = INVENTORY_DIR / "hosts.yml"
GROUP_VARS_WINDOWS = INVENTORY_DIR / "group_vars" / "windows"
CONNECTION_FILE = GROUP_VARS_WINDOWS / "connection.yml"
VAULT_FILE = GROUP_VARS_WINDOWS / "vault.yml"

BRANDING_DIR = DATA_DIR / "branding"
VAULT_PASS_FILE = DATA_DIR / ".vault_pass"
APP_SECRETS_FILE = DATA_DIR / "app_secrets.vault"
ADMIN_FILE = DATA_DIR / "admin.json"      # single-admin file from before roles existed
USERS_FILE = DATA_DIR / "users.json"
FLASK_SECRET_FILE = DATA_DIR / ".flask_secret"


def venv_bin(name: str) -> str:
    """Path to a console script installed next to the running Python (the app's venv)."""
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.exists() else name


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, REPORT_DIR, CACHE_DIR, APPS_DIR, GROUP_VARS_WINDOWS, BRANDING_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # nginx (www-data) must be able to traverse DATA_DIR and read the cache
    try:
        os.chmod(DATA_DIR, 0o711)
        for d in (CACHE_DIR, APPS_DIR):
            os.chmod(d, 0o755)
    except PermissionError:
        pass
