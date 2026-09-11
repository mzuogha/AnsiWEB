"""Background jobs (cache updates, deployments, connectivity tests, Office cache refresh),
their logs, and the built-in scheduler."""
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
    "deploy": "Deploy apps",
    "ping": "Connection test",
    "office_cache": "Refresh Office cache",
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


def playbook_cmd(playbook: str, limit: str = "", extra: dict | None = None) -> list:
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


def start(kind: str, target: str = "", trigger: str = "manual") -> int:
    """Start a job in the background. Raises JobBusy if one of this kind is running."""
    if kind not in KINDS:
        raise ValueError(kind)
    with _run_lock:
        if kind in _running:
            raise JobBusy(f"A '{KINDS[kind]}' job (#{_running[kind]}) is already running.")
        with _db_lock, _conn() as c:
            cur = c.execute("INSERT INTO jobs(kind,target,status,started,trigger) VALUES(?,?,?,?,?)",
                            (kind, target, "running", _stamp(), trigger))
            job_id = cur.lastrowid
        _running[kind] = job_id
    threading.Thread(target=_run, args=(job_id, kind, target), daemon=True, name=f"job-{job_id}").start()
    return job_id


def _run(job_id: int, kind: str, target: str) -> None:
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
        elif kind == "deploy":
            rc = run_command(playbook_cmd("deploy.yml", store.limit_for(target or "all")), log)
            store.regenerate_all()
        elif kind == "ping":
            cmd = [paths.venv_bin("ansible"), store.limit_for(target or "all"), "-i", str(paths.HOSTS_FILE),
                   "--vault-password-file", str(paths.VAULT_PASS_FILE), "-m", "ansible.windows.win_ping"]
            rc = run_command(cmd, log)
        elif kind == "office_cache":
            rc = _office_cache(log)
    except Exception as exc:  # keep the service alive whatever happens
        log(f"ERROR: {exc}")
        log(traceback.format_exc())
        rc = 1
    finally:
        status = "success" if rc == 0 else ("warning" if rc == 2 else "failed")
        log(f"[{_stamp()}] Finished with status: {status}")
        fh.close()
        with _db_lock, _conn() as c:
            c.execute("UPDATE jobs SET status=?, finished=?, rc=? WHERE id=?", (status, _stamp(), rc, job_id))
        with _run_lock:
            _running.pop(kind, None)


def _office_cache(log) -> int:
    cfg = store.load()
    helper = (cfg.get("office") or {}).get("helper_pc")
    if not helper:
        log("No Office helper PC selected. Choose one on the Office page.")
        return 1
    if not cache.office_setup_present():
        log("Office Deployment Tool setup.exe has not been uploaded yet (Office page).")
        return 1
    result_file = paths.DATA_DIR / "office_pull.json"
    if result_file.exists():
        result_file.unlink()
    rc = run_command(playbook_cmd("office_cache.yml"), log)
    if rc != 0:
        return rc
    result = store.read_json(result_file, {})
    if result.get("pulled"):
        cache.office_finalize(result["version"], log)
    else:
        cache.update_manifest_entry(cache.OFFICE_KEY, checked=cache.now())
        log(f"Office build {result.get('version', '?')} is already cached; nothing to copy.")
    return 0


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

    oc = sch.get("office_cache", {})
    if oc.get("enabled") and _due_daily("office_cache", oc, now, [oc.get("day", "sun")]):
        try:
            start("office_cache", trigger="schedule")
            hh, mm = map(int, oc["time"].split(":"))
            kv_set("sched:office_cache", now.replace(hour=hh, minute=mm, second=0, microsecond=0).isoformat())
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
