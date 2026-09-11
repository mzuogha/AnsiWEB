"""Secret storage using Ansible Vault encryption.

- inventory/group_vars/windows/vault.yml : secrets Ansible needs (the PC password)
- app_secrets.vault                       : secrets only AnsiWEB needs (GitHub token)
Both are encrypted with the key in .vault_pass (created on first start, mode 600).
"""
import json
import os
import secrets
import threading

import yaml
from ansible.constants import DEFAULT_VAULT_ID_MATCH
from ansible.parsing.vault import VaultLib, VaultSecret

from . import paths

_lock = threading.Lock()

ANSIBLE_SECRET_NAMES = {
    "vault_ansible_svc_password": "Password of the ansible_svc account on the PCs",
}


def _write_private(path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def ensure_vault_pass() -> None:
    if not paths.VAULT_PASS_FILE.exists():
        _write_private(paths.VAULT_PASS_FILE, secrets.token_urlsafe(48).encode())


def _vault() -> VaultLib:
    ensure_vault_pass()
    key = paths.VAULT_PASS_FILE.read_bytes().strip()
    return VaultLib([(DEFAULT_VAULT_ID_MATCH, VaultSecret(key))])


def _read(path) -> dict:
    if not path.exists():
        return {}
    data = _vault().decrypt(path.read_bytes())
    return yaml.safe_load(data) or {}


def _write(path, values: dict) -> None:
    text = yaml.safe_dump(values, default_flow_style=False)
    _write_private(path, _vault().encrypt(text.encode()))


# ---- Ansible secrets -------------------------------------------------------
def ansible_secrets() -> dict:
    with _lock:
        return _read(paths.VAULT_FILE)


def set_ansible_secret(name: str, value: str) -> None:
    if name not in ANSIBLE_SECRET_NAMES:
        raise KeyError(name)
    with _lock:
        values = _read(paths.VAULT_FILE)
        values[name] = value
        _write(paths.VAULT_FILE, values)


def ensure_ansible_vault() -> None:
    with _lock:
        if not paths.VAULT_FILE.exists():
            _write(paths.VAULT_FILE, {k: "" for k in ANSIBLE_SECRET_NAMES})


# ---- AnsiWEB-only secrets --------------------------------------------------
def app_secret(name: str, default: str = "") -> str:
    with _lock:
        return _read(paths.APP_SECRETS_FILE).get(name, default)


def set_app_secret(name: str, value: str) -> None:
    with _lock:
        values = _read(paths.APP_SECRETS_FILE)
        values[name] = value
        _write(paths.APP_SECRETS_FILE, values)


def secret_status() -> dict:
    """Which secrets are set, without revealing them."""
    a = ansible_secrets()
    status = {k: bool(a.get(k)) for k in ANSIBLE_SECRET_NAMES}
    status["github_token"] = bool(app_secret("github_token"))
    return status


# ---- Admin login -----------------------------------------------------------
def admin_record() -> dict:
    if not paths.ADMIN_FILE.exists():
        return {}
    return json.loads(paths.ADMIN_FILE.read_text())


def set_admin(username: str, password_hash: str) -> None:
    _write_private(paths.ADMIN_FILE, json.dumps({"username": username, "password_hash": password_hash}).encode())


def flask_secret() -> bytes:
    if not paths.FLASK_SECRET_FILE.exists():
        _write_private(paths.FLASK_SECRET_FILE, secrets.token_bytes(48))
    return paths.FLASK_SECRET_FILE.read_bytes()
