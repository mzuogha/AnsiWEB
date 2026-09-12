"""Background jobs (cache updates, deployments, connectivity tests), their logs,
and the built-in scheduler."""
import datetime as dt
import os
import sqlite3
import subprocess
import threading
import time
import traceback

from . import cache, paths, store

KINDS = {
    "cache_update": "Check for updates & refresh cache",
    "deploy": "Deploy everything",
    "deploy_apps": "Deploy apps",
    "deploy_drivers": "Install drivers",
    "deploy_scripts": "Run scripts",
    "deploy_registry": "Merge registry files",
    "rename": "Apply computer names",
    "activate": "Activate Windows",
    "set_time": "Set the time and time zone",
    "updates": "Install Windows updates",
    "ping": "Connection test",
}

# Job kinds that run deploy.yml, with the Ansible tags they limit it to
DEPLOY_TAGS = {
    "deploy": "",
    "deploy_apps": "apps",
    "deploy_drivers": "drivers",
    "deploy_scripts": "scripts",
    "deploy_registry": "registry",
    "rename": "hostname",
    "activate": "activation",
    "set_time": "time",
    "updates": "updates",
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
        # jobs left 'running' by a restart can never finish
        c.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('running','queued')")


def kv_get(key: str, default: str = "") -> str:
    with _db_lock, _conn() as c:
        row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default


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
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


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


def run_command(cmd: list, log, env=None) -> int:
    log("$ " + " ".join(c if " " not in c else repr(c) for c in cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env or ansible_env(), cwd=str(paths.ANSIBLE_DIR), text=True, bufsize=1)
    for line in proc.stdout:
        log(line.rstrip("\n"))
    return proc.wait()


def start(kind: str, target: str = "", trigger: str = "manual", only: str = "") -> int:
    """Start a job in the background. Raises JobBusy if one of this kind is running."""
    if kind not in KINDS:
        raise ValueError(kind)
    with _run_lock:
        if kind in _running:
            raise JobBusy(f"A '{KINDS[kind]}' job (#{_running[kind]}) is already running.")
        # Only one deployment at a time, whatever it covers
        if kind in DEPLOY_TAGS:
            for other in DEPLOY_TAGS:
                if other in _running:
                    raise JobBusy(f"A '{KINDS[other]}' job (#{_running[other]}) is already running.")
        with _db_lock, _conn() as c:
            cur = c.execute("INSERT INTO jobs(kind,target,status,started,trigger) VALUES(?,?,?,?,?)",
                            (kind, target, "running", _stamp(), trigger))
            job_id = cur.lastrowid
        _running[kind] = job_id
    threading.Thread(target=_run, args=(job_id, kind, target, only),
                     daemon=True, name=f"job-{job_id}").start()
    return job_id


def _run(job_id: int, kind: str, target: str, only: str = "") -> None:
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
            extra = {"aw_only": only} if only else None
            rc = run_command(playbook_cmd("deploy.yml", store.limit_for(target or "all"),
                                          extra=extra, tags=DEPLOY_TAGS[kind]), log)
            store.regenerate_all()
        elif kind == "ping":
            cmd = [paths.venv_bin("ansible"), store.limit_for(target or "all"), "-i", str(paths.HOSTS_FILE),
                   "--vault-password-file", str(paths.VAULT_PASS_FILE), "-m", "ansible.windows.win_ping"]
            rc = run_command(cmd, log)
    except Exception as exc:  # keep the service alive whatever happens
        log(f"ERROR: {exc}")
        log(traceback.format_exc())
        rc = 1
    finally:
        prune_logs()
        status = "success" if rc == 0 else ("warning" if rc == 2 else "failed")
        log(f"[{_stamp()}] Finished with status: {status}")
        fh.close()
        with _db_lock, _conn() as c:
            c.execute("UPDATE jobs SET status=?, finished=?, rc=? WHERE id=?", (status, _stamp(), rc, job_id))
        with _run_lock:
            _running.pop(kind, None)


def prune_logs() -> None:
    """Delete job logs and job rows older than the configured retention."""
    try:
        days = max(int(store.load()["settings"].get("log_retention_days", 60)), 1)
    except Exception:
        days = 60
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

    _schedule_updates(cfg, sch, now)

    dep = sch.get("deploy", {})
    if dep.get("enabled") and cfg.get("pcs") and _due_daily("deploy", dep, now, dep.get("days", [])):
        try:
            start("deploy", "all", trigger="schedule")
            hh, mm = map(int, dep["time"].split(":"))
            kv_set("sched:deploy", now.replace(hour=hh, minute=mm, second=0, microsecond=0).isoformat())
        except JobBusy:
            pass


def _schedule_updates(cfg, sch, now) -> None:
    upd = sch.get("updates", {})
    if not (upd.get("enabled") and cfg.get("pcs")):
        return
    if not _due_daily("updates", upd, now, upd.get("days", [])):
        return
    try:
        start("updates", "all", trigger="schedule")
        hh, mm = map(int, upd["time"].split(":"))
        kv_set("sched:updates", now.replace(hour=hh, minute=mm, second=0, microsecond=0).isoformat())
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
