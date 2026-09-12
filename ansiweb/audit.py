"""Audit log: who changed what, and when.

Every request that changes something is recorded automatically in web.py, so a
new route is covered without anyone remembering to add a call. Entries are kept
for the same number of days as job logs.
"""
import datetime as dt

from . import jobs

# Form fields that must never be written to the log
SECRET_FIELDS = {"password", "new", "confirm", "current", "value", "product_key", "csrf",
                 "vault_ansible_svc_password", "vault_windows_product_key"}
MAX_DETAIL = 500

# Readable names for the routes people will look for
ACTIONS = {
    "login": "Signed in",
    "logout": "Signed out",
    "job_start": "Started a job",
    "resource_run": "Ran an uploaded item",
    "uninstall_run": "Uninstalled an app from PCs",
    "uninstall_preview": "Previewed an uninstall",
    "uninstall_add": "Added an app to uninstall",
    "uninstall_delete": "Removed an uninstall entry",
    "app_new": "Added an app",
    "app_edit": "Changed an app",
    "app_delete": "Removed an app",
    "app_upload": "Uploaded an installer",
    "app_quick_upload": "Added an app from an upload",
    "app_refresh": "Re-downloaded an installer",
    "resource_add": "Uploaded a driver, script or registry file",
    "resource_edit": "Changed a driver, script or registry file",
    "resource_delete": "Removed a driver, script or registry file",
    "pc_add": "Added a PC",
    "pc_edit": "Changed a PC",
    "pc_delete": "Removed a PC",
    "pc_import": "Imported PCs",
    "pc_account": "Changed the PC management account",
    "sites": "Changed sites",
    "settings_page": "Changed settings",
    "settings_secret": "Changed a stored secret",
    "settings_activation": "Changed activation settings",
    "settings_time": "Changed time settings",
    "settings_updates": "Changed update settings",
    "settings_backup": "Downloaded a backup",
    "settings_restore": "Restored a backup",
    "own_password": "Changed their own password",
    "user_add": "Added a user",
    "user_role": "Changed a user's role",
    "user_password": "Reset a user's password",
    "user_disable": "Enabled or disabled a user",
    "user_delete": "Removed a user",
    "report_delete": "Cleared a PC report",
}


def init() -> None:
    with jobs._db_lock, jobs._conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, user TEXT, role TEXT,
            action TEXT, detail TEXT, outcome TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS audit_time ON audit(time)")


def describe(action: str) -> str:
    return ACTIONS.get(action, action.replace("_", " ").capitalize())


def summarise(form, files=None, view_args=None) -> str:
    """A short, secret-free description of what was submitted."""
    parts = []
    for key, value in (view_args or {}).items():
        parts.append(f"{key}={value}")
    for key in sorted(form or {}):
        if key in SECRET_FIELDS:
            parts.append(f"{key}=(hidden)")
            continue
        values = form.getlist(key) if hasattr(form, "getlist") else [form[key]]
        text = ", ".join(str(v) for v in values if str(v) != "")
        if text:
            parts.append(f"{key}={text}")
    for name in (files or {}):
        filename = files[name].filename if hasattr(files[name], "filename") else ""
        if filename:
            parts.append(f"file={filename}")
    detail = "; ".join(parts)
    return detail[:MAX_DETAIL - 3] + "..." if len(detail) > MAX_DETAIL else detail


def record(user: str, role: str, action: str, detail: str = "", outcome: str = "ok") -> None:
    with jobs._db_lock, jobs._conn() as c:
        c.execute("INSERT INTO audit(time,user,role,action,detail,outcome) VALUES(?,?,?,?,?,?)",
                  (dt.datetime.now().replace(microsecond=0).isoformat(sep=" "),
                   user or "-", role or "-", action, detail, outcome))


def entries(limit: int = 200, user: str = "", action: str = "", days: int = 0) -> list:
    sql = "SELECT * FROM audit WHERE 1=1"
    args = []
    if user:
        sql += " AND user = ?"
        args.append(user)
    if action:
        sql += " AND action = ?"
        args.append(action)
    if days:
        since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(sep=" ")
        sql += " AND time >= ?"
        args.append(since)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with jobs._db_lock, jobs._conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def known_users() -> list:
    with jobs._db_lock, jobs._conn() as c:
        return [r["user"] for r in c.execute("SELECT DISTINCT user FROM audit ORDER BY user")]


def known_actions() -> list:
    with jobs._db_lock, jobs._conn() as c:
        return [r["action"] for r in c.execute("SELECT DISTINCT action FROM audit ORDER BY action")]


def prune(days: int) -> int:
    """Drop entries older than the retention period. Returns how many went."""
    cutoff = (dt.datetime.now() - dt.timedelta(days=max(int(days), 1))).isoformat(sep=" ")
    with jobs._db_lock, jobs._conn() as c:
        cur = c.execute("DELETE FROM audit WHERE time < ?", (cutoff,))
        return cur.rowcount or 0
