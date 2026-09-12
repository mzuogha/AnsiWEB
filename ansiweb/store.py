"""Configuration storage and generation of Ansible inventory files."""
import copy
import ipaddress
import json
import os
import re
import threading

import yaml

from . import paths

_lock = threading.RLock()

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,14}$")   # NetBIOS computer name rules
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,253}$")
PRODUCT_KEY_RE = re.compile(r"^[A-Z0-9]{5}(-[A-Z0-9]{5}){4}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SOURCES = ["winget", "url", "upload"]


class ValidationError(ValueError):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


def _atomic_write(path, text: str, mode: int = 0o640) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_json(path, data) -> None:
    _atomic_write(path, json.dumps(data, indent=2, sort_keys=True), 0o644)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return copy.deepcopy(default) if default is not None else {}


# ---- config ----------------------------------------------------------------
def _defaults() -> dict:
    return yaml.safe_load((paths.DEFAULTS_DIR / "config.yml").read_text())


def load() -> dict:
    with _lock:
        if not paths.CONFIG_FILE.exists():
            cfg = _defaults()
            save(cfg, regenerate=False)
            return cfg
        cfg = yaml.safe_load(paths.CONFIG_FILE.read_text()) or {}
        # Office support was removed; drop its leftovers from older configurations.
        cfg.pop("office", None)
        cfg.get("schedules", {}).pop("office_cache", None)
        # fill in any keys added in newer versions
        base = _defaults()
        for key, value in base.items():
            if key not in cfg:
                cfg[key] = value
            elif isinstance(value, dict):
                for k2, v2 in value.items():
                    cfg[key].setdefault(k2, v2)
        return cfg


def save(cfg: dict, regenerate: bool = True) -> None:
    with _lock:
        validate(cfg)
        _atomic_write(paths.CONFIG_FILE, yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
        if regenerate:
            regenerate_all(cfg)


def regenerate_all(cfg: dict | None = None) -> None:
    """Rewrite inventory + deployment plan after any change."""
    from . import plan  # local import to avoid a cycle
    cfg = cfg or load()
    write_inventory(cfg)
    write_connection_vars(cfg)
    plan.write_plan(cfg)


def software_url(cfg: dict) -> str:
    ip = (cfg.get("settings") or {}).get("server_ip") or "127.0.0.1"
    return f"http://{ip}/software"


# ---- validation ------------------------------------------------------------
def validate(cfg: dict) -> None:
    from . import payloads
    s = cfg.get("settings", {})
    if not ACCOUNT_RE.match(s.get("pc_account", "") or ""):
        raise ValidationError("The PC account name may only contain letters, digits, . - _ "
                              "and must be at most 20 characters.")
    if s.get("server_ip"):
        try:
            ipaddress.ip_address(s["server_ip"])
        except ValueError:
            raise ValidationError("Server IP must be an IP address, e.g. 192.168.1.10")
    ids = set()
    for app in cfg.get("apps", []):
        if not ID_RE.match(app.get("id", "")):
            raise ValidationError(f"App ID '{app.get('id')}' may only contain lowercase letters, digits, - and _")
        if app["id"] in ids:
            raise ValidationError(f"Duplicate app ID '{app['id']}'")
        ids.add(app["id"])
        if app.get("source") not in SOURCES:
            raise ValidationError(f"{app['id']}: unknown source")
        if app["source"] == "winget" and not app.get("winget_id"):
            raise ValidationError(f"{app['id']}: winget ID is required")
        if app["source"] == "url" and not (app.get("url", "").startswith("https://") or app.get("url", "").startswith("http://")):
            raise ValidationError(f"{app['id']}: a download URL is required")
        if app["source"] in ("url", "upload") and not app.get("version"):
            raise ValidationError(f"{app['id']}: a version is required for {app['source']} apps")
        if not app.get("detect_pattern"):
            raise ValidationError(f"{app['id']}: detection pattern is required")
        try:
            re.compile(app["detect_pattern"])
        except re.error as exc:
            raise ValidationError(f"{app['id']}: detection pattern is not a valid regular expression ({exc})")
    names = set()
    for pc in cfg.get("pcs", []):
        if not NAME_RE.match(pc.get("name", "")):
            raise ValidationError(
                f"PC name '{pc.get('name')}' is not a valid Windows computer name "
                "(letters, digits and hyphens, up to 15 characters)")
        if pc["name"].lower() in names:
            raise ValidationError(f"Duplicate PC name '{pc['name']}'")
        names.add(pc["name"].lower())
        try:
            ipaddress.ip_address(pc.get("ip", ""))
        except ValueError:
            raise ValidationError(f"{pc['name']}: '{pc.get('ip')}' is not a valid IP address")
        if pc.get("site") not in cfg.get("sites", []):
            raise ValidationError(f"{pc['name']}: site '{pc.get('site')}' does not exist")
    act = cfg.get("activation") or {}
    if act.get("mode") not in ("mak", "kms"):
        raise ValidationError("Activation mode must be either MAK or KMS.")
    try:
        port = int(act.get("kms_port") or 1688)
    except (TypeError, ValueError):
        raise ValidationError("The KMS port must be a number.")
    if not 1 <= port <= 65535:
        raise ValidationError("The KMS port must be between 1 and 65535.")
    if act.get("enabled") and act.get("mode") == "kms" and not act.get("kms_host"):
        raise ValidationError("Enter the KMS host name to activate against.")
    if act.get("kms_host") and not HOSTNAME_RE.match(act["kms_host"]):
        raise ValidationError("The KMS host must be a host name or IP address.")

    t = cfg.get("schedules", {}).get("deploy", {}).get("time", "00:00")
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", t):
        raise ValidationError(f"Schedule time '{t}' must be HH:MM (24-hour)")

    for kind in payloads.KINDS:
        seen = set()
        for e in cfg.get(kind, []):
            if not ID_RE.match(e.get("id", "")):
                raise ValidationError(f"{kind}: '{e.get('id')}' is not a valid ID")
            if e["id"] in seen:
                raise ValidationError(f"{kind}: duplicate ID '{e['id']}'")
            seen.add(e["id"])
            if not e.get("name"):
                raise ValidationError(f"{kind}: every entry needs a name")
            if e.get("run_mode", "once") not in payloads.RUN_MODES:
                raise ValidationError(f"{kind}: unknown run mode for '{e['id']}'")
            if kind == "scripts" and e.get("arguments"):
                from . import plan as _plan
                _plan.split_arguments(e["arguments"])   # raises if the quoting is broken


# ---- inventory ---------------------------------------------------------------
def all_groups(cfg: dict) -> list:
    groups = set()
    for pc in cfg.get("pcs", []):
        groups.update(g for g in pc.get("groups", []) if g)
    return sorted(groups)


def target_choices(cfg: dict) -> list:
    """Values usable in an app's 'targets' list and as a deploy --limit."""
    return (["all"] + [f"site:{s}" for s in cfg.get("sites", [])]
            + [f"group:{g}" for g in all_groups(cfg)])


def limit_for(target: str) -> str:
    """Translate a UI target into an Ansible --limit pattern."""
    if target in ("", "all"):
        return "windows"
    if target.startswith("site:"):
        return "site_" + slug(target[5:])
    if target.startswith("group:"):
        return "grp_" + slug(target[6:])
    if target.startswith("pc:"):
        return target[3:]
    raise ValidationError(f"Unknown target {target}")


def write_inventory(cfg: dict) -> None:
    sites = {}
    groups = {}
    for pc in cfg.get("pcs", []):
        host = {"ansible_host": pc["ip"]}
        sites.setdefault("site_" + slug(pc["site"]), {})[pc["name"]] = host
        for g in pc.get("groups", []):
            groups.setdefault("grp_" + slug(g), {})[pc["name"]] = None
    children = {"windows": {"children": {name: {"hosts": hosts} for name, hosts in sites.items()}}}
    for name, hosts in groups.items():
        children[name] = {"hosts": hosts}
    inventory = {"all": {"children": children}}
    header = "# Generated by AnsiWEB - edit PCs in the web interface, not here.\n"
    paths.INVENTORY_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write(paths.HOSTS_FILE, header + yaml.safe_dump(inventory, sort_keys=True))


def write_connection_vars(cfg: dict | None = None) -> None:
    cfg = cfg or load()
    account = (cfg.get("settings") or {}).get("pc_account") or "Admin"
    text = f"""# Generated by AnsiWEB. How Ansible connects to the Windows PCs.
ansible_connection: winrm
ansible_port: 5986
ansible_winrm_scheme: https
ansible_winrm_transport: ntlm
ansible_user: {account}
ansible_password: "{{{{ vault_ansible_svc_password }}}}"
# PCs use self-signed certificates from Prepare-AnsibleHost.ps1;
# the WinRM firewall rule only allows this server to connect.
ansible_winrm_server_cert_validation: ignore
"""
    paths.GROUP_VARS_WINDOWS.mkdir(parents=True, exist_ok=True)
    _atomic_write(paths.CONNECTION_FILE, text)
