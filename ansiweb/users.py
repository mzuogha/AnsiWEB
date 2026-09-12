"""User accounts and roles.

Accounts live in users.json in the data folder, mode 600, with passwords
stored as hashes. Roles grant permissions; every route is mapped to a
permission in web.py, and anything unmapped requires an admin, so a new
feature is never accidentally exposed to a lesser role.
"""
import datetime as dt
import json
import threading

from werkzeug.security import check_password_hash, generate_password_hash

from . import paths, vault

_lock = threading.RLock()

# Permissions, in increasing order of trust
VIEW = "view"                      # see everything except secrets
RUN_JOBS = "run_jobs"              # deploy, test connections, install updates
MANAGE_CONTENT = "manage_content"  # apps, drivers, scripts, registry files
MANAGE_PCS = "manage_pcs"          # add, edit, rename and remove PCs and sites
ADMIN = "admin"                    # settings, secrets, backup/restore, users

# A scope limits a non-administrator to certain sites or groups. An empty scope
# means every PC. Administrators ignore scopes entirely - they manage the whole
# installation by definition.
SCOPABLE_ROLES = ("operator", "helpdesk", "viewer")

ROLES = {
    "admin": {
        "label": "Administrator",
        "description": "Everything, including settings, stored secrets, backup and restore, "
                       "and managing these accounts.",
        "permissions": {VIEW, RUN_JOBS, MANAGE_CONTENT, MANAGE_PCS, ADMIN},
    },
    "operator": {
        "label": "Operator",
        "description": "Manage apps, drivers, scripts, registry files and PCs, and run any job. "
                       "Cannot change settings, see or set secrets, or manage accounts.",
        "permissions": {VIEW, RUN_JOBS, MANAGE_CONTENT, MANAGE_PCS},
    },
    "helpdesk": {
        "label": "Helpdesk",
        "description": "Run deployments and connection tests against PCs, and read reports. "
                       "Cannot change what is deployed or any settings.",
        "permissions": {VIEW, RUN_JOBS},
    },
    "viewer": {
        "label": "Viewer",
        "description": "Read-only: dashboard, apps, PCs, reports and job logs.",
        "permissions": {VIEW},
    },
}
DEFAULT_ROLE = "helpdesk"
MIN_PASSWORD = 10


class UserError(Exception):
    pass


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _read() -> dict:
    try:
        data = json.loads(paths.USERS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(users: dict) -> None:
    vault._write_private(paths.USERS_FILE, json.dumps(users, indent=2, sort_keys=True).encode())


def migrate() -> None:
    """Carry a single-admin installation (admin.json) over to the user list."""
    with _lock:
        if paths.USERS_FILE.exists():
            return
        old = vault.admin_record()
        if old.get("username"):
            _write({old["username"].lower(): {
                "username": old["username"], "password_hash": old["password_hash"],
                "role": "admin", "created": _now(), "disabled": False}})


def all_users() -> list:
    with _lock:
        return sorted(_read().values(), key=lambda u: (u["role"] != "admin", u["username"].lower()))


def get(username: str) -> dict:
    with _lock:
        return _read().get((username or "").lower(), {})


def count_admins(exclude: str = "") -> int:
    return len([u for u in all_users()
                if u["role"] == "admin" and not u.get("disabled")
                and u["username"].lower() != (exclude or "").lower()])


def any_users() -> bool:
    return bool(all_users())


def validate_username(username: str) -> str:
    name = (username or "").strip()
    if not 2 <= len(name) <= 32 or not all(c.isalnum() or c in "._-@" for c in name):
        raise UserError("A user name is 2 to 32 characters: letters, digits, . _ - @")
    return name


def check_password_rules(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise UserError(f"The password must be at least {MIN_PASSWORD} characters.")


def create(username: str, password: str, role: str, scope: list | None = None) -> None:
    name = validate_username(username)
    check_password_rules(password)
    if role not in ROLES:
        raise UserError("Unknown role.")
    with _lock:
        users = _read()
        if name.lower() in users:
            raise UserError(f"A user called '{name}' already exists.")
        users[name.lower()] = {"username": name, "password_hash": generate_password_hash(password),
                              "role": role, "created": _now(), "disabled": False,
                              "scope": clean_scope(role, scope)}
        _write(users)


def clean_scope(role: str, scope: list | None) -> list:
    """Only site: and group: entries mean anything, and only for lesser roles."""
    if role == "admin":
        return []
    out = []
    for item in scope or []:
        item = str(item).strip()
        if item.startswith(("site:", "group:")) and item not in out:
            out.append(item)
    return out


def set_scope(username: str, scope: list) -> None:
    with _lock:
        users = _read()
        key = (username or "").lower()
        if key not in users:
            raise UserError("No such user.")
        users[key]["scope"] = clean_scope(users[key]["role"], scope)
        _write(users)


def scope_of(user: dict) -> list:
    """The sites and groups this user is limited to; empty means everything."""
    if not user or user.get("role") == "admin":
        return []
    return user.get("scope") or []


def pc_in_scope(pc: dict, scope: list) -> bool:
    if not scope:
        return True
    for item in scope:
        if item.startswith("site:") and pc.get("site") == item[5:]:
            return True
        if item.startswith("group:") and item[6:] in (pc.get("groups") or []):
            return True
    return False


def narrow_target(pcs: list, scope: list, requested: str) -> str:
    """Narrow a requested job target to the PCs this scope allows.

    An unscoped user gets their request unchanged. For a scoped user the target
    becomes an explicit list of PC names, so a job can never reach a PC outside
    their sites or groups, even if the form was tampered with. Raises UserError
    when nothing in the target is theirs.
    """
    from . import plan
    wanted = requested or "all"
    if not scope:
        return wanted
    allowed = [pc["name"] for pc in pcs if pc_in_scope(pc, scope) and plan.pc_matches(pc, [wanted])]
    if not allowed:
        raise UserError(f"None of the PCs you can manage ({scope_label(scope)}) are in that target.")
    return "list:" + ",".join(allowed)


def scope_label(scope: list) -> str:
    if not scope:
        return "all PCs"
    return ", ".join(s.replace("site:", "Site: ").replace("group:", "Group: ") for s in scope)


def set_password(username: str, password: str) -> None:
    check_password_rules(password)
    with _lock:
        users = _read()
        key = (username or "").lower()
        if key not in users:
            raise UserError("No such user.")
        users[key]["password_hash"] = generate_password_hash(password)
        users[key]["password_changed"] = _now()
        _write(users)


def set_role(username: str, role: str) -> None:
    if role not in ROLES:
        raise UserError("Unknown role.")
    with _lock:
        users = _read()
        key = (username or "").lower()
        if key not in users:
            raise UserError("No such user.")
        if users[key]["role"] == "admin" and role != "admin" and count_admins(exclude=username) == 0:
            raise UserError("This is the only administrator left. Promote someone else first.")
        users[key]["role"] = role
        # An administrator has no scope; dropping to a lesser role keeps any it had
        users[key]["scope"] = clean_scope(role, users[key].get("scope"))
        _write(users)


def set_disabled(username: str, disabled: bool) -> None:
    with _lock:
        users = _read()
        key = (username or "").lower()
        if key not in users:
            raise UserError("No such user.")
        if disabled and users[key]["role"] == "admin" and count_admins(exclude=username) == 0:
            raise UserError("This is the only administrator left, so it cannot be disabled.")
        users[key]["disabled"] = bool(disabled)
        _write(users)


def delete(username: str) -> None:
    with _lock:
        users = _read()
        key = (username or "").lower()
        if key not in users:
            raise UserError("No such user.")
        if users[key]["role"] == "admin" and count_admins(exclude=username) == 0:
            raise UserError("This is the only administrator left, so it cannot be removed.")
        del users[key]
        _write(users)


def authenticate(username: str, password: str) -> dict:
    """Return the user record on success, or an empty dict."""
    user = get(username)
    if not user or user.get("disabled"):
        return {}
    if not check_password_hash(user["password_hash"], password or ""):
        return {}
    return user


def permissions(role: str) -> set:
    return ROLES.get(role, {}).get("permissions", set())


def can(role: str, permission: str) -> bool:
    return permission in permissions(role)
