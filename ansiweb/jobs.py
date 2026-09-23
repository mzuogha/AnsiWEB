"""Background jobs (cache updates, deployments, connectivity tests), their logs,
and the built-in scheduler."""
import datetime as dt
import json
import os
import signal
import re
import sqlite3
import subprocess
import threading
import time
import traceback

from . import cache, paths, store, util

KINDS = {
    "cache_update": "Check for updates & refresh cache",
    "deploy": "Deploy everything",
    "deploy_apps": "Deploy apps",
    "deploy_drivers": "Install drivers",
    "deploy_scripts": "Run scripts",
    "deploy_registry": "Merge registry files",
    "deploy_automation": "Run scripts and merge registry files",
    "deploy_files": "Apply drivers, scripts and registry files",
    "rename": "Apply computer names",
    "activate": "Activate Windows",
    "set_time": "Set the time and time zone",
    "printers": "Set up printers",
    "shares": "Set up shared folders",
    "inventory": "Collect inventory from PCs",
    "health": "Check the PCs' condition",
    "winsettings": "Apply Windows settings",
    "uninstall_preview": "Preview an uninstall",
    "uninstall_run": "Uninstall apps from PCs",
    "ping": "Connection test",
}

# Job kinds that run deploy.yml, with the Ansible tags they limit it to
DEPLOY_TAGS = {
    "deploy": "",
    "deploy_apps": "apps",
    "deploy_drivers": "drivers",
    "deploy_scripts": "scripts",
    "deploy_registry": "registry",
    "deploy_automation": "scripts,registry",
    "deploy_files": "drivers,scripts,registry",
    "rename": "hostname",
    "activate": "activation",
    "set_time": "time",
    "printers": "printers",
    "shares": "shares",
    "inventory": "inventory",
    "health": "health",
    "winsettings": "winsettings",
    "uninstall_preview": "uninstall",
    "uninstall_run": "uninstall",
}

_db_lock = threading.Lock()
_running = {}          # kind -> job id (one job of each kind at a time)
_run_lock = threading.Lock()


class JobBusy(Exception):
    pass


# ---- database --------------------------------------------------------------
def _conn():
    c = sqlite3.connect(paths.DB_FILE, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _db_lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, target TEXT, status TEXT,
            started TEXT, finished TEXT, rc INTEGER, trigger TEXT)""")
        c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, user TEXT, role TEXT,
            action TEXT, detail TEXT, outcome TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS audit_time ON audit(time)")
        # jobs left 'running' by a restart can never finish
        c.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('running','queued')")


def kv_get(key: str, default: str = "") -> str:
    with _db_lock, _conn() as c:
        row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default


def kv_keys(prefix: str) -> list:
    with _db_lock, _conn() as c:
        rows = c.execute("SELECT k FROM kv WHERE k LIKE ? ORDER BY k", (prefix + "%",)).fetchall()
    return [r["k"] for r in rows]


def kv_delete(key: str) -> None:
    with _db_lock, _conn() as c:
        c.execute("DELETE FROM kv WHERE k = ?", (key,))


def kv_set(key: str, value: str) -> None:
    with _db_lock, _conn() as c:
        c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))


def list_jobs(limit: int = 50) -> list:
    with _db_lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))]


def get_job(job_id: int):
    with _db_lock, _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def last_job(kind: str):
    with _db_lock, _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
        return dict(row) if row else None


def log_path(job_id: int):
    return paths.LOG_DIR / f"job-{job_id}.log"


def read_log(job_id: int, offset: int = 0):
    p = log_path(job_id)
    if not p.exists():
        return "", offset
    with open(p, "rb") as fh:
        fh.seek(offset)
        data = fh.read(512 * 1024)
    return data.decode("utf-8", errors="replace"), offset + len(data)


def _stamp() -> str:
    return util.now()


# ---- running ---------------------------------------------------------------
def ansible_env() -> dict:
    env = dict(os.environ)
    env.update({
        "ANSIBLE_CONFIG": str(paths.ANSIBLE_DIR / "ansible.cfg"),
        "ANSIBLE_FORCE_COLOR": "0",
        "ANSIBLE_NOCOLOR": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(paths.CODE_DIR),
        "ANSIWEB_DATA": str(paths.DATA_DIR),
        "ANSIBLE_LOCAL_TEMP": str(paths.DATA_DIR / ".ansible" / "tmp"),
        "ANSIBLE_HOME": str(paths.DATA_DIR / ".ansible"),
    })
    return env


def playbook_cmd(playbook: str, limit: str = "", extra: dict | None = None, tags: str = "") -> list:
    cfg = store.load()
    cmd = [paths.venv_bin("ansible-playbook"),
           "-i", str(paths.HOSTS_FILE),
           "--vault-password-file", str(paths.VAULT_PASS_FILE),
           "-f", str(cfg["settings"].get("forks", 20)),
           "-e", f"ansiweb_data={paths.DATA_DIR}",
           "-e", f"ansiweb_code={paths.CODE_DIR}",
           "-e", f"aw_batch={cfg['settings'].get('batch_size', 20)}",
           str(paths.ANSIBLE_DIR / "playbooks" / playbook)]
    if limit:
        cmd += ["--limit", limit]
    if tags:
        cmd += ["--tags", tags]
    for k, v in (extra or {}).items():
        cmd += ["-e", f"{k}={v}"]
    return cmd


_procs: dict = {}


def run_command(cmd: list, log, env=None, job_id: int | None = None) -> int:
    log("$ " + " ".join(c if " " not in c else repr(c) for c in cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env or ansible_env(), cwd=str(paths.ANSIBLE_DIR), text=True,
                            bufsize=1, start_new_session=True)
    if job_id is not None:
        _procs[job_id] = proc
    try:
        for line in proc.stdout:
            log(line.rstrip("\n"))
        return proc.wait()
    finally:
        if job_id is not None:
            _procs.pop(job_id, None)


def stop_job(job_id: int) -> bool:
    """Stop waiting for a running job.

    Ansible cannot be told to abandon one task and carry on, so this ends the
    run. Whatever finished stays done, and the job can be started again without
    the part that was holding it up.
    """
    proc = _procs.get(job_id)
    if not proc or proc.poll() is not None:
        return False
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    kv_set(f"stopped:{job_id}", _stamp())
    return True


# What a deployment can cover, in the order it runs on the PC. The label is
# what the Deploy page offers; the value is the tag the playbook uses.
DEPLOY_PARTS = [
    ("apps", "Applications", "install or upgrade the standard app set"),
    ("drivers", "Device drivers", "add uploaded driver packages"),
    ("scripts", "Scripts", "run uploaded scripts"),
    ("registry", "Registry files", "merge uploaded .reg files"),
    ("shares", "Shared folders", "create shares and turn file sharing on"),
    ("printers", "Printers", "set up the printers for this PC"),
    ("time", "Time and time zone", "set the time zone and time source"),
    ("winsettings", "Windows settings", "apply the chosen Windows settings"),
    ("activation", "Windows activation", "activate Windows if it is not already"),
    ("hostname", "Computer name", "rename Windows to match AnsiWEB"),
    ("inventory", "Inventory", "collect the installed-software list"),
]


QUEUE_LIMIT = 25


def max_concurrent() -> int:
    try:
        return max(1, min(int(store.load()["settings"].get("concurrent_jobs", 3)), 10))
    except (ValueError, KeyError, TypeError):
        return 3


def _running_targets() -> dict:
    """{job id: target} for everything currently running."""
    with _db_lock, _conn() as c:
        rows = c.execute("SELECT id, target FROM jobs WHERE status = 'running'").fetchall()
    return {r["id"]: r["target"] or "all" for r in rows}


def _blocked_by(kind: str, target: str = ""):
    """What stops this job starting now, if anything.

    Jobs run side by side. Two jobs on the same PC do not: they would fight over
    the same files and the same installer, so the second waits.
    """
    running = _running_targets()
    if len(running) >= max_concurrent():
        return "busy", sorted(running)[0]
    cfg = store.load()
    mine = store.pcs_for_target(cfg, target or "all")
    for job_id, other in running.items():
        if mine & store.pcs_for_target(cfg, other):
            return "same PCs", job_id
    return None


def queued_jobs() -> list:
    with _db_lock, _conn() as c:
        rows = c.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def start(kind: str, target: str = "", trigger: str = "manual", only: str = "",
          adhoc: dict | None = None, tags: str = "") -> int:
    """Start a job, or queue it behind whatever is already running.

    Two deployments at once would fight over the same PCs, so a second one waits
    its turn rather than being refused - the work still happens, unattended.
    """
    if kind not in KINDS:
        raise ValueError(kind)
    with _run_lock:
        blocker = _blocked_by(kind, target)
        if blocker and len(queued_jobs()) >= QUEUE_LIMIT:
            raise JobBusy(f"{QUEUE_LIMIT} jobs are already waiting; try again when some have run.")
        status = "queued" if blocker else "running"
        with _db_lock, _conn() as c:
            cur = c.execute("INSERT INTO jobs(kind,target,status,started,trigger) VALUES(?,?,?,?,?)",
                            (kind, target, status, _stamp(), trigger))
            job_id = cur.lastrowid
        kv_set(f"job-args:{job_id}", json.dumps({"only": only, "tags": tags,
                                                 "adhoc": adhoc or {}}))
        if blocker:
            return job_id
        _running[job_id] = kind
    threading.Thread(target=_run, args=(job_id, kind, target, only, adhoc, tags),
                     daemon=True, name=f"job-{job_id}").start()
    return job_id


# Ansible's own output interleaves tasks and hosts, and a failure buries the
# rest. These read it back and say plainly what each task did.
_TASK_RE = re.compile(r"^TASK \[(?:[^:]+ : )?(.+?)\]")
_RESULT_RE = re.compile(r"^(ok|changed|skipping|failed|fatal|unreachable):\s*\[([^\]]+?)(?:\s*->.*)?\]")
# Ansible writes a failure as either "failed:" or "fatal:"; treat them alike.
_CANONICAL = {"fatal": "failed"}
_STATUS_ORDER = {"failed": 0, "unreachable": 1, "changed": 2, "ok": 3, "skipping": 4}
_STATUS_LABEL = {"failed": "FAILED", "fatal": "FAILED", "unreachable": "UNREACHABLE",
                 "changed": "changed", "ok": "ok", "skipping": "skipped"}


# The playbook's own error handling: useful to Ansible, noise in a summary that
# is meant to say what happened on the PC.
_BOOKKEEPING = {"Say which task failed", "Keep the job's failed status",
                "What was applied on this PC"}


# The playbook's own error handling: these report a failure rather than being
# one, so they are left out of the summary.
# ("Gathering Facts" stays in: an unreachable PC shows up there, which is
# exactly what the summary should report.)
_BOOKKEEPING = {"Say which task failed", "Keep the job's failed status",
                "What was applied on this PC"}


_NOTHING_RE = re.compile(r"^\s*- '?([\w.-]+) - 0 item\(s\)", re.M)


_RECAP_RE = re.compile(r"^(\S+)\s*:\s*ok=\d+.*?failed=(\d+)", re.M)
_UNREACHABLE_RE = re.compile(r"^(\S+)\s*:\s*ok=\d+.*?unreachable=(\d+)", re.M)


def failed_hosts(text: str) -> list:
    """PCs that failed or were unreachable, from Ansible's own recap."""
    bad = []
    for host, count in _RECAP_RE.findall(text):
        if int(count) > 0 and host not in bad:
            bad.append(host)
    for host, count in _UNREACHABLE_RE.findall(text):
        if int(count) > 0 and host not in bad:
            bad.append(host)
    return bad


def applied_nothing(text: str) -> list:
    """PCs the playbook reported as having had nothing applied."""
    return _NOTHING_RE.findall(text)


# Jobs that only look at a PC. "Nothing was applied" is their normal outcome.
READ_ONLY_KINDS = {"health", "inventory", "ping", "uninstall_preview"}


def unreachable_hosts(text: str) -> list:
    return [h for h, count in _UNREACHABLE_RE.findall(text) if int(count) > 0]


def summarise_run(text: str, kind: str = "") -> str:
    """A short 'what each task did' list, from Ansible's output."""
    tasks: list = []
    index: dict = {}
    current = ""
    for line in text.splitlines():
        task = _TASK_RE.match(line)
        if task:
            current = task.group(1).strip()
            continue
        result = _RESULT_RE.match(line)
        if not result or not current:
            continue
        raw = _CANONICAL.get(result.group(1), result.group(1))
        status = _STATUS_LABEL.get(raw, raw)
        host = result.group(2).strip()
        if current in _BOOKKEEPING:
            continue
        if current in _BOOKKEEPING:
            continue
        if current not in index:
            index[current] = {}
            tasks.append(current)
        hosts = index[current]
        # A host's worst outcome for this task is the one worth showing
        if host not in hosts or _STATUS_ORDER.get(raw, 9) < _STATUS_ORDER.get(hosts[host][1], 9):
            hosts[host] = (status, raw)

    if not tasks:
        return ""

    lines = ["", "=" * 64, "What each task did", "=" * 64]
    failed = []
    for name in tasks:
        hosts = index[name]
        counts: dict = {}
        for status, _raw in hosts.values():
            counts[status] = counts.get(status, 0) + 1
        worst = min((raw for _s, raw in hosts.values()), key=lambda r: _STATUS_ORDER.get(r, 9))
        mark = "x" if _STATUS_LABEL.get(worst) in ("FAILED", "UNREACHABLE") else \
               "-" if _STATUS_LABEL.get(worst) == "skipped" else "v"
        detail = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
        lines.append(f"  [{mark}] {name}  ({detail})")
        if mark == "x":
            failed.append((name, [h for h, (_s, raw) in hosts.items()
                                  if _STATUS_LABEL.get(raw) in ("FAILED", "UNREACHABLE")]))

    hosts_seen = {host for task in index.values() for host in task}
    if len(hosts_seen) == 42:
        lines.append("")
        lines.append("  42 PCs. The answer to life, the universe, and everything.")

    if failed:
        lines.append("")
        lines.append("Failed:")
        for name, hosts in failed:
            lines.append(f"  - {name}  on {', '.join(sorted(hosts))}")
        lines.append("")
        lines.append("Everything above the first failure was applied; anything after it did not run")
        lines.append("on that PC. Fix the cause and run the job again - re-running is safe.")
    stranded = unreachable_hosts(text)
    if stranded:
        lines.append("")
        lines.append("Could not be reached: " + ", ".join(stranded))
        lines.append("Nothing was attempted on those - they were not answering, so this is not a")
        lines.append("failure of the work itself. Check the PC is on, that its address is right or")
        lines.append("that it has reported one in, and that its connection mode matches AnsiWEB's.")

    idle = applied_nothing(text)
    if idle and kind not in READ_ONLY_KINDS:
        lines.append("")
        lines.append("Nothing was applied on: " + ", ".join(idle))
        lines.append("Tasks ran, but no application, driver, script or setting was due. The lines")
        lines.append("above say why - most often the apps are not in the cache yet, or nothing is")
        lines.append("targeted at these PCs.")
    lines.append("=" * 64)
    return "\n".join(lines)


INSTALL_HINTS = {
    "1603": "the installer hit a general failure - often it is already part-installed, "
            "or needs a reboot first",
    "1618": "another installation was already running on that PC; try again shortly",
    "1619": "the installer file could not be opened - the download may be truncated",
    "1620": "the file is not a valid installer package",
    "1625": "policy on that PC forbids the installation",
    "3010": "it installed but wants a reboot; add 3010 to the app's success codes if that is fine",
}


def install_hint(text: str) -> str:
    """Point at the likely cause when an app installer fails."""
    for code, meaning in INSTALL_HINTS.items():
        if f"exit code {code}" in text or f"rc={code}" in text or f"return code {code}" in text:
            return (f"\nOne of the installers exited with {code}: {meaning}.\n"
                    "The app's page has an 'extra success codes' box if that code is acceptable,\n"
                    "and an 'arguments' box if it needs different silent switches.\n")
    if "checksum" in text.lower():
        return ("\nA download did not match its checksum. Run 'Check for updates now' on the\n"
                "Apps & Cache page to fetch the installer again, then deploy.\n")
    if "cannot reach the AnsiWEB cache" in text:
        return ("\nThe PC could not reach this server's cache on port 80. Everything a PC installs\n"
                "comes from there, so nothing can proceed until that works.\n")
    return ""


def _ping_hint(cfg) -> str:
    """What to check when a connection test fails, given how AnsiWEB is set up."""
    mode = (cfg.get("settings") or {}).get("pc_connection", "https")
    port, other = ("5985", "PowerShell (.ps1)") if mode == "ntlm" else ("5986", "simple (.cmd)")
    return (
        f"\nThe connection test failed. AnsiWEB is set to reach PCs on port {port}"
        f" ({'NTLM' if mode == 'ntlm' else 'HTTPS'}).\n"
        "Check, in this order:\n"
        f"  1. The PC was prepared with the matching script. If you used the {other} script,\n"
        f"     change 'How AnsiWEB connects' on the PCs page - a timeout on port {port} usually\n"
        "     means the PC is listening on the other port.\n"
        "  2. The PC is on and reachable: ping its IP address from this server.\n"
        "  3. The IP address in AnsiWEB matches the PC's current address.\n"
        "  4. The prep script was run with this server's address, so its firewall rule allows us.\n"
        "  5. The account password here matches the one entered on the PC.\n")


def _run(job_id: int, kind: str, target: str, only: str = "", adhoc: dict | None = None,
         tags: str = "") -> None:
    fh = open(log_path(job_id), "a", encoding="utf-8", buffering=1)

    def log(line: str) -> None:
        fh.write(line + "\n")

    rc = 1
    try:
        log(f"[{_stamp()}] Job #{job_id}: {KINDS[kind]}" + (f" -> {target}" if target else ""))
        store.regenerate_all()
        if kind == "cache_update":
            counts = cache.update_all(log)
            rc = 0 if counts["error"] == 0 else 2
        elif kind in DEPLOY_TAGS:
            extra = {"aw_only": only} if only else {}
            if kind == "uninstall_run":
                extra["aw_uninstall_apply"] = "true"   # nothing is removed without this
            if adhoc:
                extra["aw_adhoc_name"] = adhoc["name"]
                extra["aw_adhoc_pattern"] = adhoc["pattern"]
            # "deploy" with chosen parts passes its own tags; otherwise the
            # kind decides them, and a plain deployment runs everything.
            rc = run_command(playbook_cmd("deploy.yml", store.limit_for(target or "all"),
                                          extra=extra, tags=tags or DEPLOY_TAGS[kind]),
                             log, job_id=job_id)
            body = log_path(job_id).read_text(errors="replace")
            bad = failed_hosts(body)
            if bad:
                kv_set(f"failed-hosts:{job_id}", ",".join(bad))
            if rc != 0:
                hint = install_hint(body)
                if hint:
                    log(hint)
            summary = summarise_run(body, kind)
            if summary:
                log(summary)
            store.regenerate_all()
        elif kind == "ping":
            cmd = [paths.venv_bin("ansible"), store.limit_for(target or "all"), "-i", str(paths.HOSTS_FILE),
                   "--vault-password-file", str(paths.VAULT_PASS_FILE), "-m", "ansible.windows.win_ping"]
            rc = run_command(cmd, log, job_id=job_id)
            if rc != 0:
                log(_ping_hint(store.load()))
    except Exception as exc:  # keep the service alive whatever happens
        log(f"ERROR: {exc}")
        log(traceback.format_exc())
        rc = 1
    finally:
        prune_logs()
        # rc 2 means "some hosts failed", which is a failure, not a warning.
        # rc 4 is "some hosts were unreachable", which is worth distinguishing.
        status = {0: "success", 4: "warning"}.get(rc, "failed")
        if kv_get(f"stopped:{job_id}"):
            status = "stopped"
            log("\nStopped on request. What had finished stays done; run the job again, without "
                "the part that was holding it up, to carry on.")
        if status == "success" and kind.startswith("deploy"):
            # A deployment that applied nothing is not a failure, but calling it
            # a success hides the fact that nothing happened.
            if applied_nothing(log_path(job_id).read_text(errors="replace")):
                status = "warning"
        if rc == 2 and "failed=0" in log_path(job_id).read_text(errors="replace"):
            status = "warning"          # nothing actually failed on a PC
        log(f"[{_stamp()}] Finished with status: {status}")
        fh.close()
        with _db_lock, _conn() as c:
            c.execute("UPDATE jobs SET status=?, finished=?, rc=? WHERE id=?", (status, _stamp(), rc, job_id))
        with _run_lock:
            _running.pop(job_id, None)
        _start_next_queued()


def _start_next_queued() -> None:
    """Begin every waiting job nothing is blocking, oldest first.

    More than one can start: a job finishing may free capacity for several, and
    they only collide if they cover the same PCs.
    """
    started = 0
    for job in queued_jobs():
        with _run_lock:
            if _blocked_by(job["kind"], job["target"] or "all"):
                continue
            with _db_lock, _conn() as c:
                changed = c.execute(
                    "UPDATE jobs SET status='running', started=? WHERE id=? AND status='queued'",
                    (_stamp(), job["id"])).rowcount
            if not changed:          # something else took it
                continue
            _running[job["id"]] = job["kind"]
        args = json.loads(kv_get(f"job-args:{job['id']}") or "{}")
        threading.Thread(target=_run,
                         args=(job["id"], job["kind"], job["target"], args.get("only", ""),
                               args.get("adhoc") or None, args.get("tags", "")),
                         daemon=True, name=f"job-{job['id']}").start()
        started += 1
    if started:
        return


def cancel_queued(job_id: int) -> bool:
    """Drop a job that has not started. A running job is left alone."""
    with _db_lock, _conn() as c:
        return c.execute("UPDATE jobs SET status='cancelled', finished=? "
                         "WHERE id=? AND status='queued'", (_stamp(), job_id)).rowcount > 0


def prune_logs() -> None:
    """Delete job logs and job rows older than the configured retention."""
    try:
        days = max(int(store.load()["settings"].get("log_retention_days", 60)), 1)
    except Exception:
        days = 60
    from . import audit
    audit.prune(days)
    cutoff = dt.datetime.now() - dt.timedelta(days=days)
    with _db_lock, _conn() as c:
        rows = c.execute("SELECT id, started FROM jobs").fetchall()
        old = [r["id"] for r in rows
               if r["started"] and dt.datetime.fromisoformat(r["started"]) < cutoff]
        for job_id in old:
            log_path(job_id).unlink(missing_ok=True)
            c.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def job_counts(days: int = 30) -> dict:
    """Totals per status for the reporting page."""
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(sep=" ")
    with _db_lock, _conn() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM jobs WHERE started >= ? GROUP BY status",
                         (since,)).fetchall()
    return {r["status"]: r["n"] for r in rows}


def running() -> dict:
    with _run_lock:
        return dict(_running)


# ---- scheduler -------------------------------------------------------------
def _due_daily(key: str, sched: dict, now: dt.datetime, days: list) -> bool:
    if now.strftime("%a").lower()[:3] not in days:
        return False
    hh, mm = map(int, sched.get("time", "00:00").split(":"))
    slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < slot or now - slot > dt.timedelta(minutes=30):
        return False
    return kv_get(f"sched:{key}") != slot.isoformat()


def scheduler_tick() -> None:
    cfg = store.load()
    sch = cfg.get("schedules", {})
    now = dt.datetime.now()

    cc = sch.get("cache_check", {})
    if cc.get("enabled"):
        last = kv_get("sched:cache_check")
        hours = max(int(cc.get("every_hours", 24)), 1)
        if not last or now - dt.datetime.fromisoformat(last) >= dt.timedelta(hours=hours):
            try:
                start("cache_update", trigger="schedule")
                kv_set("sched:cache_check", now.replace(microsecond=0).isoformat())
            except JobBusy:
                pass


    dep = sch.get("deploy", {})
    if dep.get("enabled") and cfg.get("pcs") and _due_daily("deploy", dep, now, dep.get("days", [])):
        try:
            start("deploy", "all", trigger="schedule")
            hh, mm = map(int, dep["time"].split(":"))
            kv_set("sched:deploy", now.replace(hour=hh, minute=mm, second=0, microsecond=0).isoformat())
        except JobBusy:
            pass


def start_scheduler() -> None:
    def loop():
        time.sleep(20)
        while True:
            try:
                scheduler_tick()
            except Exception:
                traceback.print_exc()
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="scheduler").start()
