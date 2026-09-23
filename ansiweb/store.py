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
TIMEZONE_RE = re.compile(r"^[A-Za-z0-9 .,'()+/-]{3,80}$")

# A short list for the dropdown. Any valid Windows ID can be typed in;
# run "tzutil /l" on a PC to see them all.
COMMON_TIMEZONES = [
    "GMT Standard Time", "Greenwich Standard Time", "W. Europe Standard Time",
    "Central Europe Standard Time", "Romance Standard Time", "E. Europe Standard Time",
    "FLE Standard Time", "Turkey Standard Time", "Israel Standard Time",
    "Arabian Standard Time", "Arab Standard Time", "W. Central Africa Standard Time",
    "South Africa Standard Time", "E. Africa Standard Time", "India Standard Time",
    "Pakistan Standard Time", "China Standard Time", "Singapore Standard Time",
    "Tokyo Standard Time", "Korea Standard Time", "AUS Eastern Standard Time",
    "New Zealand Standard Time", "UTC", "Azores Standard Time",
    "E. South America Standard Time", "Argentina Standard Time", "SA Pacific Standard Time",
    "Eastern Standard Time", "Central Standard Time", "Mountain Standard Time",
    "US Mountain Standard Time", "Pacific Standard Time", "Alaskan Standard Time",
    "Hawaiian Standard Time",
]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
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
    # Only the service reads these; nginx serves the cache, not the plan or
    # the manifest, so they do not need to be world-readable.
    _atomic_write(path, json.dumps(data, indent=2, sort_keys=True), 0o640)


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
        # Printers are network-only now: keep any shared queue from an older
        # configuration, but disabled, with its path noted so nothing is lost.
        for pr in cfg.get("printers", []) or []:
            if pr.get("kind") and pr["kind"] != "tcpip":
                note = f"was a shared queue: {pr.get('connection', '')}".strip()
                pr["comment"] = (pr.get("comment") or "") + (" " if pr.get("comment") else "") + note
                pr["enabled"] = False
                pr["kind"] = "tcpip"
                pr["host"] = pr.get("host") or "0.0.0.0"
                pr["driver"] = pr.get("driver") or "unknown"
        # Office support was removed; drop its leftovers from older configurations.
        cfg.pop("office", None)
        # Printers are network-only now. An old shared-queue entry is kept but
        # switched off, so nothing disappears silently.
        for pr in cfg.get("printers") or []:
            if pr.pop("kind", "tcpip") == "shared" or pr.pop("connection", ""):
                pr["enabled"] = False
                pr["notes"] = ("shared print-server queues are no longer supported - "
                               "re-enter this as a network printer")
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
    if s.get("pc_connection", "https") not in ("https", "ntlm"):
        raise ValidationError("The connection to the PCs must be HTTPS or NTLM.")
    if not ACCOUNT_RE.match(s.get("pc_account", "") or ""):
        raise ValidationError("The PC account name may only contain letters, digits, . - _ "
                              "and must be at most 20 characters.")
    try:
        timeout = int(s.get("session_timeout_minutes", 60))
    except (TypeError, ValueError):
        raise ValidationError("The session timeout must be a number of minutes.")
    if not 5 <= timeout <= 1440:
        raise ValidationError("The session timeout must be between 5 and 1440 minutes (24 hours).")
    try:
        stale = int(s.get("stale_after_days", 14))
    except (TypeError, ValueError):
        raise ValidationError("The stale-PC threshold must be a number of days.")
    if not 1 <= stale <= 365:
        raise ValidationError("The stale-PC threshold must be between 1 and 365 days.")
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
        # An address is optional: with DHCP a PC reports its own, and failing
        # that AnsiWEB reaches it by name. What is typed must still make sense.
        typed = (pc.get("ip") or "").strip()
        if typed:
            try:
                ipaddress.ip_address(typed)
            except ValueError:
                if not HOSTNAME_RE.match(typed):
                    raise ValidationError(
                        f"{pc['name']}: '{typed}' is neither an IP address nor a host name. "
                        "Leave it empty to let the PC report its own address.")
        if pc.get("site") not in cfg.get("sites", []):
            raise ValidationError(f"{pc['name']}: site '{pc.get('site')}' does not exist")
    tm = cfg.get("time") or {}
    if tm.get("timezone") and not TIMEZONE_RE.match(tm["timezone"]):
        raise ValidationError("That does not look like a Windows time zone ID, e.g. "
                              "W. Europe Standard Time")
    for server in tm.get("ntp_servers") or []:
        if not HOSTNAME_RE.match(server):
            raise ValidationError(f"'{server}' is not a valid time server name or IP address")
    if tm.get("enabled") and not tm.get("timezone") and not (tm.get("ntp_servers") or []):
        raise ValidationError("Set a time zone or at least one time server before turning this on.")

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

    for key in ("deploy",):
        t = cfg.get("schedules", {}).get(key, {}).get("time", "00:00")
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", t):
            raise ValidationError(f"Schedule time '{t}' must be HH:MM (24-hour)")

    seen = set()
    for sh in cfg.get("shares", []):
        if not ID_RE.match(sh.get("id", "")):
            raise ValidationError(f"shares: '{sh.get('id')}' is not a valid ID")
        if sh["id"] in seen:
            raise ValidationError(f"shares: duplicate ID '{sh['id']}'")
        seen.add(sh["id"])
        if not re.match(r"^[^\\/:*?\"<>|]{1,80}$", sh.get("name", "")):
            raise ValidationError("A share name cannot contain \\ / : * ? \" < > or |")
        if not sh.get("remove") and not re.match(r"^[A-Za-z]:\\", sh.get("path", "")):
            raise ValidationError(f"{sh['name']}: the folder must be a full path on the PC, "
                                  "e.g. D:\\Shared\\Team")

    seen = set()
    for pr in cfg.get("printers", []):
        if not ID_RE.match(pr.get("id", "")):
            raise ValidationError(f"printers: '{pr.get('id')}' is not a valid ID")
        if pr["id"] in seen:
            raise ValidationError(f"printers: duplicate ID '{pr['id']}'")
        seen.add(pr["id"])
        if not pr.get("name"):
            raise ValidationError("Every printer needs a name.")
        if not pr.get("host"):
            raise ValidationError(f"{pr['name']}: enter the printer's IP address or host name.")
        if not HOSTNAME_RE.match(pr["host"]):
            raise ValidationError(f"{pr['name']}: '{pr['host']}' is not a valid address.")
        if not pr.get("driver"):
            raise ValidationError(f"{pr['name']}: enter the Windows driver name to use.")
        try:
            port = int(pr.get("port") or 9100)
        except (TypeError, ValueError):
            raise ValidationError(f"{pr['name']}: the port must be a number.")
        if not 1 <= port <= 65535:
            raise ValidationError(f"{pr['name']}: the port must be between 1 and 65535.")

    seen = set()
    for entry in cfg.get("uninstalls", []):
        if not ID_RE.match(entry.get("id", "")):
            raise ValidationError(f"uninstalls: '{entry.get('id')}' is not a valid ID")
        if entry["id"] in seen:
            raise ValidationError(f"uninstalls: duplicate ID '{entry['id']}'")
        seen.add(entry["id"])
        if not entry.get("name"):
            raise ValidationError("Every uninstall entry needs a name.")
        if not entry.get("detect_pattern"):
            raise ValidationError(f"{entry['name']}: a detection pattern is required, so AnsiWEB "
                                  "knows what to look for on the PCs")
        try:
            re.compile(entry["detect_pattern"])
        except re.error as exc:
            raise ValidationError(f"{entry['name']}: '{entry['detect_pattern']}' is not a valid "
                                  f"regular expression ({exc})")
        if entry["detect_pattern"].strip() in (".", ".*", "^.*$", ""):
            raise ValidationError(f"{entry['name']}: that pattern matches every installed program. "
                                  "Use something specific, e.g. ^7-Zip")

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


def pc_facts() -> dict:
    """{pc name: facts} from the last report each PC sent."""
    out = {}
    try:
        for f in paths.REPORT_DIR.glob("*.json"):
            if f.name.startswith("health-"):
                continue
            data = read_json(f, {})
            if data.get("host"):
                out[data["host"]] = data.get("facts") or {}
    except OSError:
        pass
    return out


def hardware_groups(cfg: dict) -> dict:
    """{"make:HP": [names], "model:HP EliteBook 840 G8": [names]} from reports."""
    out: dict = {}
    known = pc_facts()
    for pc in cfg.get("pcs", []):
        facts = pc.get("facts") or known.get(pc["name"], {})
        make = (facts.get("manufacturer") or "").strip()
        model = (facts.get("model") or "").strip()
        if make:
            out.setdefault(f"make:{make}", []).append(pc["name"])
        if model:
            out.setdefault(f"model:{model}", []).append(pc["name"])
    return dict(sorted(out.items()))


def target_choices(cfg: dict) -> list:
    """Values usable in an app's 'targets' list and as a deploy --limit."""
    return (["all"] + [f"site:{s}" for s in cfg.get("sites", [])]
            + [f"group:{g}" for g in all_groups(cfg)]
            + list(hardware_groups(cfg)))


def pcs_for_target(cfg: dict, target: str) -> set:
    """The PCs a target covers, for deciding whether two jobs would collide."""
    from . import plan as _plan
    if target in ("", "all", "windows"):
        return {pc["name"] for pc in cfg.get("pcs", [])}
    if target.startswith("list:"):
        return {n for n in target[5:].split(",") if n}
    names = set()
    known = pc_facts()
    for pc in cfg.get("pcs", []):
        enriched = {**pc, "facts": pc.get("facts") or known.get(pc["name"], {})}
        if _plan.pc_matches(enriched, [target]):
            names.add(pc["name"])
    return names


def limit_for(target: str, cfg: dict | None = None) -> str:
    """Translate a UI target into an Ansible --limit pattern."""
    if target in ("", "all"):
        return "windows"
    if target.startswith(("make:", "model:")):
        # Hardware is known from reports, not from the inventory file, so the
        # matching PCs are named directly.
        names = hardware_groups(cfg if cfg is not None else load()).get(target, [])
        return ",".join(names) or "no-such-pc"
    if target.startswith("site:"):
        return "site_" + slug(target[5:])
    if target.startswith("group:"):
        return "grp_" + slug(target[6:])
    if target.startswith("pc:"):
        return target[3:]
    if target.startswith("list:"):
        names = [n for n in target[5:].split(",") if n]
        if not names:
            raise ValidationError("No PCs are in range for you.")
        return ",".join(names)
    raise ValidationError(f"Unknown target {target}")


def write_inventory(cfg: dict) -> None:
    sites = {}
    groups = {}
    for pc in cfg.get("pcs", []):
        # Prefer the address the PC itself last reported (DHCP moves it about),
        # then anything typed in, which may be a name rather than an address.
        host = {"ansible_host": (pc.get("seen_ip") or pc.get("ip")
                                 or pc.get("hostname") or pc["name"])}
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
    settings = cfg.get("settings") or {}
    account = settings.get("pc_account") or "Admin"
    if settings.get("pc_connection", "https") == "ntlm":
        text = f"""# Generated by AnsiWEB. Simple mode: the PCs were prepared with the .cmd
# script, so there is no certificate. NTLM encrypts each message instead, and
# unencrypted traffic is refused at both ends.
ansible_connection: winrm
ansible_port: 5985
ansible_winrm_scheme: http
ansible_winrm_transport: ntlm
ansible_winrm_message_encryption: always
ansible_user: {account}
ansible_password: "{{{{ vault_ansible_svc_password }}}}"
"""
        paths.GROUP_VARS_WINDOWS.mkdir(parents=True, exist_ok=True)
        _atomic_write(paths.CONNECTION_FILE, text)
        return
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
