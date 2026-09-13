"""Offline smoke test of the web interface: python3 tests/smoke_test.py"""
import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile

os.environ["ANSIWEB_DATA"] = DATA = tempfile.mkdtemp(prefix="ansiweb-test-")
os.environ["ANSIWEB_HTTPS"] = "0"          # test client speaks plain HTTP
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from werkzeug.security import generate_password_hash            # noqa: E402
from ansiweb import (__version__, audit, backup, cache, jobs, release,  # noqa: E402
                     report_state, store, users, vault, web)

# Jobs run for real here would invoke Ansible against PCs that do not exist, which
# is slow and leaves jobs running while later checks want to start their own.
# The job machinery is still exercised; only the Ansible call itself is stubbed.
def _fake_run(cmd, log, env=None):
    log("[test] would run: " + " ".join(cmd))
    return 0


jobs.run_command = _fake_run


def wait_for_jobs(seconds=10):
    deadline = time.time() + seconds
    while jobs.running() and time.time() < deadline:
        time.sleep(0.05)


PW = "correct-horse-1"
vault.set_admin("admin", generate_password_hash(PW))   # the pre-roles single admin
app = web.create_app(start_background=False)
c = app.test_client()
checks = 0


def ok(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def csrf(html):
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def zip_bytes(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, "; driver stub\n")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- login / CSRF
ok(c.get("/").status_code == 302, "anonymous request is redirected")
tok = csrf(c.get("/login").text)
ok("Wrong user name or password" in c.post("/login", data={"username": "admin", "password": "no",
                                                           "csrf": tok}).text,
   "wrong password is rejected")
r = c.post("/login", data={"username": "admin", "password": PW, "csrf": tok}, follow_redirects=True)
ok("Dashboard" in r.text, "login works")
tok = csrf(r.text)
ok(c.post("/settings", data={}).status_code == 400, "POST without a CSRF token is refused")

# ---------------------------------------------------------------- session timeout
ok(store.load()["settings"]["session_timeout_minutes"] == 60, "default idle timeout is 60 minutes")
r = c.get("/settings")
ok("Sign out after inactivity" in r.text, "Settings offers the timeout field")
ok('name="idle-timeout"' in c.get("/").text, "pages tell the browser the idle timeout")
tok = csrf(c.get("/").text)
r = c.post("/settings", data={"csrf": tok, "server_ip": "192.168.1.10", "forks": "20", "batch_size": "20",
                              "log_retention_days": "45", "session_timeout_minutes": "15",
                              "cc_hours": "24", "dep_time": "19:00"}, follow_redirects=True)
ok("Settings saved" in r.text, "a new timeout saves")
ok(store.load()["settings"]["session_timeout_minutes"] == 15, "timeout stored")
ok('content="900"' in c.get("/").text, "the new timeout reaches the browser")
ok("between 5 and 1440" in c.post("/settings", data={"csrf": tok, "forks": "20", "batch_size": "20",
                                                     "log_retention_days": "45",
                                                     "session_timeout_minutes": "2", "cc_hours": "24",
                                                     "dep_time": "19:00"}, follow_redirects=True).text,
   "too short a timeout is rejected")
ok("between 5 and 1440" in c.post("/settings", data={"csrf": tok, "forks": "20", "batch_size": "20",
                                                     "log_retention_days": "45",
                                                     "session_timeout_minutes": "4000", "cc_hours": "24",
                                                     "dep_time": "19:00"}, follow_redirects=True).text,
   "too long a timeout is rejected")

# activity keeps the session alive
with c.session_transaction() as sess:
    sess["seen"] = time.time() - 600          # 10 minutes idle, timeout is 15
ok(c.get("/apps").status_code == 200, "a session inside the timeout still works")
with c.session_transaction() as sess:
    ok(sess["seen"] > time.time() - 5, "each request slides the idle clock forward")

# the live job-log poll must not keep an unattended page signed in
with c.session_transaction() as sess:
    sess["seen"] = time.time() - 600
c.get("/jobs/1/log")
with c.session_transaction() as sess:
    ok(sess["seen"] < time.time() - 300, "polling the job log does not slide the idle clock")

# going over the timeout signs the session out
with c.session_transaction() as sess:
    sess["seen"] = time.time() - 1000         # 16 minutes idle
r = c.get("/apps", follow_redirects=True)
ok("Sign in" in r.text and "15 minutes without activity" in r.text,
   "an idle session is signed out with an explanation")
with c.session_transaction() as sess:
    ok("user" not in sess, "the expired session is cleared server-side")
ok(c.get("/apps").status_code == 302, "the expired session cannot reach a page")
ok("session timed out" in c.get("/login?expired=1").text, "the login page explains a browser-side expiry")

# sign back in for the rest of the test
tok = csrf(c.get("/login").text)
r = c.post("/login", data={"username": "admin", "password": PW, "csrf": tok}, follow_redirects=True)
ok("Dashboard" in r.text, "signing back in works after a timeout")
tok = csrf(r.text)
with c.session_transaction() as sess:
    ok(sess.get("seen") is not None, "signing in starts the idle clock")
c.post("/settings", data={"csrf": tok, "server_ip": "192.168.1.10", "forks": "20", "batch_size": "20",
                          "log_retention_days": "45", "session_timeout_minutes": "60",
                          "cc_hours": "24", "dep_time": "19:00"}, follow_redirects=True)

# ---------------------------------------------------------------- settings
r = c.post("/settings", data={"csrf": tok, "server_ip": "192.168.1.10", "forks": "20", "batch_size": "20",
                              "log_retention_days": "45", "cc_enabled": "on", "cc_hours": "24",
                              "dep_time": "19:00", "dep_days": ["mon", "fri"]}, follow_redirects=True)
ok("Settings saved" in r.text, "settings save")
ok("must be an IP" in c.post("/settings", data={"csrf": tok, "server_ip": "nope", "forks": "20",
                                                "batch_size": "20", "log_retention_days": "45",
                                                "cc_hours": "24", "dep_time": "19:00"},
                             follow_redirects=True).text, "bad IP is rejected")
r = c.post("/settings/secret", data={"csrf": tok, "name": "vault_ansible_svc_password", "value": "S3cret!pw"},
           follow_redirects=True)
ok("Secret updated" in r.text and "S3cret" not in r.text, "secret stored without echoing it")
ok(vault.ansible_secrets()["vault_ansible_svc_password"] == "S3cret!pw", "secret readable by Ansible")

# ---------------------------------------------------------------- PC account (default Admin)
ok(store.load()["settings"]["pc_account"] == "Admin", "default PC account is Admin")
conn = open(os.path.join(DATA, "inventory/group_vars/windows/connection.yml")).read()
ok("ansible_user: Admin" in conn, "inventory connects as Admin")
r = c.post("/pcs/account", data={"csrf": tok, "pc_account": "AnsiAdmin", "password": "NewPw123456"},
           follow_redirects=True)
ok("connect to PCs as" in r.text and "AnsiAdmin" in r.text, "account rename is reported")
ok("ansible_user: AnsiAdmin" in
   open(os.path.join(DATA, "inventory/group_vars/windows/connection.yml")).read(), "inventory follows the account")
ok(vault.ansible_secrets()["vault_ansible_svc_password"] == "NewPw123456", "account password updated")
ok("only contain letters" in c.post("/pcs/account", data={"csrf": tok, "pc_account": "bad name!"},
                                    follow_redirects=True).text, "invalid account name is rejected")
c.post("/pcs/account", data={"csrf": tok, "pc_account": "Admin"}, follow_redirects=True)

# ---------------------------------------------------------------- sites and PCs
c.post("/sites", data={"csrf": tok, "action": "add", "site": "Branch1"})
ok("PC added" in c.post("/pcs/add", data={"csrf": tok, "name": "PC-HQ-001", "ip": "192.168.1.21",
                                          "site": "HQ", "groups": "finance"}, follow_redirects=True).text,
   "add a PC")
ok("Duplicate PC" in c.post("/pcs/add", data={"csrf": tok, "name": "PC-HQ-001", "ip": "192.168.1.22",
                                              "site": "HQ"}, follow_redirects=True).text, "duplicate PC rejected")
ok("not a valid Windows computer name" in
   c.post("/pcs/add", data={"csrf": tok, "name": "way-too-long-computer-name", "ip": "1.2.3.4", "site": "HQ"},
          follow_redirects=True).text, "over-long computer name rejected")
r = c.post("/pcs/import", data={"csvers": "", "csv": "name,ip,site\nPC-BR1-001,192.168.2.21,Branch1\n"
                                "PC-BR2-001,192.168.3.21,Branch2,kiosk;finance\n", "csrf": tok},
           follow_redirects=True)
ok("Imported 2" in r.text, "CSV import")

# ---------------------------------------------------------------- rename a PC
r = c.post("/pcs/PC-BR1-001/edit", data={"csrf": tok, "name": "PC-BR1-009", "ip": "192.168.2.21",
                                         "site": "Branch1", "groups": "", "sync_hostname": "on"},
           follow_redirects=True)
ok("Apply computer name" in r.text, "rename with hostname sync explains the next step")
names = [pc["name"] for pc in store.load()["pcs"]]
ok("PC-BR1-009" in names and "PC-BR1-001" not in names, "rename stored")
hosts = open(os.path.join(DATA, "inventory/hosts.yml")).read()
ok("PC-BR1-009" in hosts and "PC-BR1-001" not in hosts, "inventory renamed")
plan = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan["hosts"]["PC-BR1-009"]["hostname"] == "PC-BR1-009", "plan carries the hostname to apply")
ok(plan["hosts"]["PC-HQ-001"]["hostname"] == "", "PCs without sync are not renamed")

# ---------------------------------------------------------------- offline app upload
r = c.post("/apps/upload", data={"csrf": tok, "name": "Vendor App", "version": "5.2.1",
                                 "detect_pattern": "^Vendor App", "arguments": "/S",
                                 "targets": ["group:finance"],
                                 "installer": (io.BytesIO(b"MZ-stub"), "VendorApp.exe")},
           content_type="multipart/form-data", follow_redirects=True)
ok("Added Vendor App 5.2.1" in r.text, "offline app added from the Apps page")
entry = cache.load_manifest()["vendor-app"]
ok(entry["version"] == "5.2.1" and (os.path.join(DATA, "cache/apps", entry["file"])), "installer cached")
ok("Enter a detection pattern" in c.post("/apps/upload", data={"csrf": tok, "name": "X", "version": "1",
                                                               "installer": (io.BytesIO(b"x"), "x.msi")},
                                         content_type="multipart/form-data", follow_redirects=True).text,
   "missing detection pattern is refused")
ok("Only .msi and .exe" in c.post("/apps/upload", data={"csrf": tok, "name": "Y", "version": "1",
                                                        "detect_pattern": "^Y",
                                                        "installer": (io.BytesIO(b"x"), "y.txt")},
                                  content_type="multipart/form-data", follow_redirects=True).text,
   "wrong installer type is refused")
# upload while adding via the full form
r = c.post("/apps/new", data={"csrf": tok, "id": "line-of-business", "name": "LOB client", "enabled": "on",
                              "source": "upload", "version": "2.0", "detect_pattern": "^LOB",
                              "targets": ["all"], "installer": (io.BytesIO(b"MZ"), "lob.msi")},
           content_type="multipart/form-data", follow_redirects=True)
ok("cached the uploaded installer" in r.text, "upload on the add-app form")
ok("Choose the installer file" in c.post("/apps/new", data={"csrf": tok, "id": "noreally", "name": "No file",
                                                            "source": "upload", "version": "1",
                                                            "detect_pattern": "^No"},
                                         content_type="multipart/form-data", follow_redirects=True).text,
   "uploaded app without a file is refused")

# ---------------------------------------------------------------- remove an app from the Apps page
cached_file = cache.load_manifest()["vendor-app"]["file"]
cached_path = os.path.join(DATA, "cache/apps", cached_file)
ok(os.path.exists(cached_path), "uploaded installer is on disk before removal")
r = c.get("/apps")
ok('/apps/vendor-app/delete' in r.text, "the Apps page offers a Remove button per app")
r = c.post("/apps/vendor-app/delete", data={"csrf": tok}, follow_redirects=True)
ok("Removed Vendor App" in r.text, "removal is confirmed")
ok("stays installed on the PCs" in r.text, "removal explains that PCs keep the app")
ok(not any(a["id"] == "vendor-app" for a in store.load()["apps"]), "app is gone from the configuration")
ok("vendor-app" not in cache.load_manifest(), "cache manifest entry is gone")
ok(not os.path.exists(cached_path), "cached installer file is deleted")
plan_after = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(not any(a["id"] == "vendor-app" for a in plan_after["apps"]), "app is gone from the deployment plan")
ok("vendor-app" not in plan_after["hosts"]["PC-HQ-001"]["apps"], "PCs no longer target the app")
ok(c.post("/apps/vendor-app/delete", data={"csrf": tok}).status_code == 404,
   "removing it twice gives a clean 404")
ok(c.get("/apps/line-of-business/edit").status_code == 200, "other apps are untouched")
# put it back for the rest of the test
c.post("/apps/upload", data={"csrf": tok, "name": "Vendor App", "version": "5.2.1",
                             "detect_pattern": "^Vendor App", "arguments": "/S",
                             "targets": ["group:finance"],
                             "installer": (io.BytesIO(b"MZ-stub"), "VendorApp.exe")},
       content_type="multipart/form-data", follow_redirects=True)

# ---------------------------------------------------------------- drivers / scripts / registry
r = c.post("/files/add", data={"csrf": tok, "name": "Intel NIC", "run_mode": "once", "targets": ["all"],
                                 "payload": (zip_bytes(["net.inf", "net.cat"]), "intel-nic.zip")},
           content_type="multipart/form-data", follow_redirects=True)
ok("uploaded" in r.text and "Intel NIC" in r.text, "driver upload")
r = c.post("/files/add", data={"csrf": tok, "name": "Set power plan", "run_mode": "always",
                                 "arguments": "-Plan High", "success_codes": "3010", "timeout": "600",
                                 "reboot": "on", "targets": ["site:HQ"],
                                 "payload": (io.BytesIO(b"Write-Host hi\n"), "power.ps1")},
           content_type="multipart/form-data", follow_redirects=True)
ok("Set power plan" in r.text, "script upload")
r = c.post("/files/add", data={"csrf": tok, "name": "Disable autostart", "run_mode": "changed",
                                 "targets": ["all"],
                                 "payload": (io.BytesIO(b"Windows Registry Editor Version 5.00\n"), "no-auto.reg")},
           content_type="multipart/form-data", follow_redirects=True)
ok("Disable autostart" in r.text, "registry upload")
ok("not supported here" in c.post("/files/add", data={"csrf": tok, "name": "Bad", "run_mode": "once",
                                                      "payload": (io.BytesIO(b"x"), "installer.exe")},
                                  content_type="multipart/form-data", follow_redirects=True).text,
   "an unsupported file type is refused")
ok("Choose a file" in c.post("/files/add", data={"csrf": tok, "name": "Nofile", "run_mode": "once"},
                             content_type="multipart/form-data", follow_redirects=True).text,
   "script without a file refused")

# drivers, scripts and registry files share one page
body = c.get("/files").text
ok("Drivers, scripts &amp; registry" in body or "Drivers, scripts & registry" in body,
   "one page covers all three kinds")
ok("Intel NIC" in body and "Set power plan" in body and "Disable autostart" in body,
   "drivers, scripts and registry files are listed together")
ok("Device driver" in body and "Registry file" in body and "Script" in body,
   "each row shows which kind it is")
for old in ("/drivers", "/scripts", "/registry"):
    ok(c.get(old).status_code == 302, f"the old {old} URL redirects")
    ok("Intel NIC" in c.get(old, follow_redirects=True).text, f"{old} lands on the merged page")
ok(jobs.DEPLOY_TAGS.get("deploy_files") == "drivers,scripts,registry",
   "one job covers all three kinds")
for kind, label in [("deploy_drivers", "Apply drivers"), ("deploy_scripts", "Apply scripts"),
                    ("deploy_registry", "Apply registry"), ("deploy_files", "Apply all")]:
    ok(label in body, f"the page offers '{label}'")
    ok(f'value="{kind}"' in body, f"'{label}' starts the {kind} job")
r = c.post("/jobs/start", data={"csrf": tok, "kind": "deploy_registry", "target": "all"},
           follow_redirects=True)
wait_for_jobs()
ok(jobs.last_job("deploy_registry") is not None, "applying one kind on its own starts that job")
ok(c.get("/nonsense").status_code == 404, "an unknown page is still 404")

cfg = store.load()
plan = json.load(open(os.path.join(DATA, "deploy_plan.json")))
drv = plan["drivers"][0]
ok(drv["win_file"].startswith("C:\\ProgramData\\AnsiWEB\\cache\\"), "driver has a Windows path")
ok(drv["win_state"].endswith(".done") and drv["state_key"].startswith("drivers-intel-nic-"),
   "driver has a state marker path")
scr = plan["scripts"][0]
ok(scr["shell"] == "powershell" and scr["timeout"] == 600 and scr["reboot"] is True
   and scr["success_codes"] == [0, 3010], "script settings reach the plan")
ok(plan["hosts"]["PC-HQ-001"]["scripts"] == ["set-power-plan"], "HQ PC gets the site-targeted script")
ok(plan["hosts"]["PC-BR1-009"]["scripts"] == [], "other sites do not")
ok(plan["hosts"]["PC-HQ-001"]["drivers"] == ["intel-nic"], "driver targeted at all PCs")
ok(plan["registry"][0]["run_mode"] == "changed", "registry run mode")
ok(c.get("/drivers/intel-nic/download").status_code == 200, "driver file can be downloaded back")
# replacing a script's file changes its state key, so PCs re-run it
old_key = scr["state_key"]
c.post("/scripts/set-power-plan/edit", data={"csrf": tok, "name": "Set power plan", "enabled": "on",
                                             "run_mode": "always", "timeout": "600", "targets": ["site:HQ"],
                                             "payload": (io.BytesIO(b"Write-Host changed\n"), "power.ps1")},
       content_type="multipart/form-data", follow_redirects=True)
plan = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan["scripts"][0]["state_key"] != old_key, "replacing the file changes the state key")

# ---------------------------------------------------------------- Windows updates
r = c.get("/settings")
ok("Windows updates" in r.text and "Security updates" in r.text, "Settings offers an updates panel")
r = c.post("/settings/updates", data={"csrf": tok, "enabled": "on",
                                      "categories": ["SecurityUpdates", "CriticalUpdates"],
                                      "exclude": "KB5001234, Malicious Software Removal",
                                      "source": "managed_server", "reboot": "on",
                                      "timeout_minutes": "240", "targets": ["site:HQ"],
                                      "sched_enabled": "on", "sched_time": "22:30",
                                      "sched_days": ["sat", "sun"]}, follow_redirects=True)
ok("Update settings saved" in r.text, "update settings save")
u = store.load()["updates"]
ok(u["categories"] == ["SecurityUpdates", "CriticalUpdates"], "categories stored")
ok(u["exclude"] == ["KB5001234", "Malicious Software Removal"], "exclusions split into a list")
ok(u["source"] == "managed_server" and u["reboot"] is True and u["timeout_minutes"] == 240,
   "source, reboot and timeout stored")
ok(store.load()["schedules"]["updates"] == {"enabled": True, "time": "22:30", "days": ["sat", "sun"]},
   "update schedule stored")
plan_u = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan_u["updates"]["enabled"] and plan_u["updates"]["timeout_minutes"] == 240, "updates reach the plan")
ok(plan_u["hosts"]["PC-HQ-001"]["update"] is True, "targeted PCs get updates")
ok(plan_u["hosts"]["PC-BR1-009"]["update"] is False, "PCs outside the target do not")
ok("is not a Windows update category" in
   c.post("/settings/updates", data={"csrf": tok, "categories": ["Nonsense"], "source": "default",
                                     "timeout_minutes": "180", "targets": ["all"]},
          follow_redirects=True).text, "an unknown category is rejected")
ok("Choose at least one update category" in
   c.post("/settings/updates", data={"csrf": tok, "enabled": "on", "source": "default",
                                     "timeout_minutes": "180", "targets": ["all"]},
          follow_redirects=True).text, "turning updates on with no category is refused")
ok("between 10 and 1440" in
   c.post("/settings/updates", data={"csrf": tok, "categories": ["SecurityUpdates"], "source": "default",
                                     "timeout_minutes": "5", "targets": ["all"]},
          follow_redirects=True).text, "an unreasonable timeout is rejected")
ok("not a valid pattern" in
   c.post("/settings/updates", data={"csrf": tok, "categories": ["SecurityUpdates"], "source": "default",
                                     "exclude": "(", "timeout_minutes": "180", "targets": ["all"]},
          follow_redirects=True).text, "a broken exclusion pattern is rejected")
ok("must be HH:MM" in
   c.post("/settings/updates", data={"csrf": tok, "categories": ["SecurityUpdates"], "source": "default",
                                     "timeout_minutes": "180", "targets": ["all"],
                                     "sched_time": "99:99"}, follow_redirects=True).text,
   "a bad schedule time is rejected")
ok(jobs.DEPLOY_TAGS.get("updates") == "updates", "an updates-only job exists")
# leave updates off for the rest of the test
c.post("/settings/updates", data={"csrf": tok, "categories": ["SecurityUpdates"], "source": "default",
                                  "timeout_minutes": "180", "targets": ["all"], "sched_time": "22:00"},
       follow_redirects=True)

# ---------------------------------------------------------------- time and time zone
r = c.get("/settings")
ok("Time and time zone" in r.text and "W. Europe Standard Time" in r.text,
   "Settings offers a time panel with time zone choices")
r = c.post("/settings/time", data={"csrf": tok, "enabled": "on", "timezone": "W. Central Africa Standard Time",
                                   "ntp_servers": "10.0.0.1, time.windows.com", "sync_now": "on",
                                   "targets": ["site:HQ"]}, follow_redirects=True)
ok("Time settings saved" in r.text, "time settings save")
tm = store.load()["time"]
ok(tm["timezone"] == "W. Central Africa Standard Time", "time zone stored")
ok(tm["ntp_servers"] == ["10.0.0.1", "time.windows.com"], "time servers split into a list")
plan_tm = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan_tm["time"]["enabled"] and plan_tm["time"]["sync_now"], "time settings reach the plan")
ok(plan_tm["hosts"]["PC-HQ-001"]["set_time"] is True, "targeted PCs get the clock settings")
ok(plan_tm["hosts"]["PC-BR1-009"]["set_time"] is False, "PCs outside the target do not")
# a typed ID overrides the dropdown
r = c.post("/settings/time", data={"csrf": tok, "enabled": "on", "timezone": "UTC",
                                   "timezone_custom": "Tokyo Standard Time",
                                   "ntp_servers": "", "targets": ["all"]}, follow_redirects=True)
ok(store.load()["time"]["timezone"] == "Tokyo Standard Time", "a typed time zone wins over the dropdown")
ok(c.get("/settings").text.count("Tokyo Standard Time") >= 1, "the stored zone is shown as selected")
ok("does not look like a Windows time zone" in
   c.post("/settings/time", data={"csrf": tok, "timezone_custom": "!!nope!!", "targets": ["all"]},
          follow_redirects=True).text, "a malformed time zone ID is rejected")
ok("not a valid time server" in
   c.post("/settings/time", data={"csrf": tok, "ntp_servers": "time.ok.local, nope!!", "targets": ["all"]},
          follow_redirects=True).text, "a malformed time server is rejected")
r = c.post("/settings/time", data={"csrf": tok, "ntp_servers": "10.0.0.1 10.0.0.2", "targets": ["all"]},
           follow_redirects=True)
ok(store.load()["time"]["ntp_servers"] == ["10.0.0.1", "10.0.0.2"],
   "time servers can also be separated by spaces")
ok("Set a time zone or at least one time server" in
   c.post("/settings/time", data={"csrf": tok, "enabled": "on", "ntp_servers": "", "targets": ["all"]},
          follow_redirects=True).text, "turning it on with nothing set is refused")
ok(jobs.DEPLOY_TAGS.get("set_time") == "time", "a time-only job exists")
# leave it off for the rest of the test
c.post("/settings/time", data={"csrf": tok, "timezone": "", "ntp_servers": "", "targets": ["all"]},
       follow_redirects=True)

# ---------------------------------------------------------------- Windows activation
r = c.get("/settings")
ok("Windows activation" in r.text and "XXXXX-XXXXX" in r.text, "Settings offers an activation panel with a key field")
ok(not vault.secret_status()["vault_windows_product_key"], "no key stored to begin with")
r = c.post("/settings/activation", data={"csrf": tok, "enabled": "on", "mode": "mak",
                                         "product_key": "aaaaa-bbbbb-ccccc-ddddd-eeeee",
                                         "kms_port": "1688", "skip_if_activated": "on",
                                         "targets": ["all"]}, follow_redirects=True)
ok("Activation settings saved" in r.text, "activation settings save")
ok(vault.ansible_secrets()["vault_windows_product_key"] == "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
   "the key is stored upper-cased in the vault")
ok("AAAAA-BBBBB" not in c.get("/settings").text, "the stored key is never shown again")
plan_act = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan_act["activation"]["enabled"] and plan_act["activation"]["mode"] == "mak", "activation reaches the plan")
ok("AAAAA-BBBBB" not in open(os.path.join(DATA, "deploy_plan.json")).read(),
   "the key is NOT written to the world-readable plan file")
ok(plan_act["hosts"]["PC-HQ-001"]["activate"] is True, "targeted PCs are marked for activation")
ok("looks like XXXXX" in c.post("/settings/activation", data={"csrf": tok, "mode": "mak",
                                                              "product_key": "not-a-key", "kms_port": "1688"},
                                follow_redirects=True).text, "a malformed key is rejected")
ok(vault.ansible_secrets()["vault_windows_product_key"] == "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
   "a rejected key does not overwrite the stored one")
# KMS mode
r = c.post("/settings/activation", data={"csrf": tok, "enabled": "on", "mode": "kms",
                                         "kms_host": "kms.example.local", "kms_port": "1688",
                                         "targets": ["site:HQ"]}, follow_redirects=True)
ok("Activation settings saved" in r.text, "KMS settings save")
plan_act = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan_act["activation"]["kms_host"] == "kms.example.local", "KMS host reaches the plan")
ok(plan_act["hosts"]["PC-BR1-009"]["activate"] is False, "PCs outside the target are not activated")
ok("Enter the KMS host" in c.post("/settings/activation", data={"csrf": tok, "enabled": "on", "mode": "kms",
                                                                "kms_host": "", "kms_port": "1688"},
                                  follow_redirects=True).text, "KMS mode needs a host")
ok("must be a number" in c.post("/settings/activation", data={"csrf": tok, "mode": "mak", "kms_port": "abc"},
                                follow_redirects=True).text, "a bad KMS port is rejected")
r = c.post("/settings/activation/clear-key", data={"csrf": tok}, follow_redirects=True)
ok("product key removed" in r.text and not vault.secret_status()["vault_windows_product_key"],
   "the stored key can be removed")
ok("activate" in jobs.DEPLOY_TAGS and jobs.DEPLOY_TAGS["activate"] == "activation",
   "an activation-only job exists")
# leave activation off for the rest of the test
c.post("/settings/activation", data={"csrf": tok, "mode": "mak", "kms_port": "1688", "targets": ["all"]},
       follow_redirects=True)

# ---------------------------------------------------------------- reports
os.makedirs(os.path.join(DATA, "reports"), exist_ok=True)
json.dump({"host": "PC-HQ-001", "time": "2026-09-12 09:00:00", "reboot_pending": True,
           "facts": {"hostname": "PC-HQ-001", "os": "Windows 11 Pro", "build": "26100",
                     "model": "Dell OptiPlex", "ram_gb": 16, "serial": "ABC123", "boot": "2026-09-12 07:00:00", "activated": False, "timezone": "W. Central Africa Standard Time",
                     "local_time": "2026-09-12 10:05:00"},
           "apps": [{"id": "7zip", "name": "7-Zip", "installed": "22.01", "target": "26.03",
                     "needed": True, "mismatch": False, "reason": "update 22.01 -> 26.03"},
                    {"id": "vlc", "name": "VLC", "installed": "3.0.23", "target": "3.0.23",
                     "needed": False, "mismatch": False, "reason": "up to date"}],
           "results": [{"kind": "drivers", "id": "intel-nic", "name": "Intel NIC", "status": "installed",
                      "detail": "1 of 1 driver file(s) added"},
                     {"kind": "scripts", "id": "set-power-plan", "name": "Set power plan",
                      "status": "ran (exit 0)", "detail": "done"},
                     {"kind": "updates", "id": "windows-updates", "name": "Windows updates",
                      "status": "3 installed (reboot needed)", "detail": "2026-09 Cumulative Update"}]},
          open(os.path.join(DATA, "reports/PC-HQ-001.json"), "w"))
r = c.get("/reports")
ok("PC-HQ-001" in r.text and "Windows 11 Pro" in r.text, "report page shows the PC")
ok("reboot pending" in r.text, "pending reboot is visible")
ok("Intel NIC" in r.text, "driver result is visible")
ok("3 installed" in r.text, "Windows update result is visible")
csv_text = c.get("/reports/export.csv").text
ok("PC-HQ-001" in csv_text and "7-Zip" in csv_text and "Dell OptiPlex" in csv_text, "CSV export")
ok("activated" in csv_text.splitlines()[0], "CSV has an activation column")
ok("not activated" in c.get("/reports").text, "reports flag PCs that are not activated")
ok(csv_text.count("\n") > 3, "CSV has a row per item")
ok("Dell OptiPlex" in c.get("/pcs/PC-HQ-001/edit").text, "PC page shows the report")
ok("W. Central Africa Standard Time" in c.get("/pcs/PC-HQ-001/edit").text,
   "PC page shows the reported time zone")

# ---------------------------------------------------------------- jobs
ok(c.get("/jobs").status_code == 200, "jobs page")
ok("deploy_drivers" in jobs.DEPLOY_TAGS and jobs.DEPLOY_TAGS["deploy_scripts"] == "scripts",
   "tag-limited deploy kinds exist")

# ---------------------------------------------------------------- backup and restore
data = backup.create()
ok(len(data) > 500, "backup produced an archive")
import tarfile
with tarfile.open(fileobj=io.BytesIO(data)) as t:
    members = t.getnames()
ok("config.yml" in members and "cache/drivers/intel-nic.zip" in members, "backup holds config and payloads")
ok(not any(m.startswith("cache/apps") for m in members), "backup excludes cached app installers")

# change something, then restore
c.post("/pcs/PC-HQ-001/delete", data={"csrf": tok}, follow_redirects=True)
ok(len(store.load()["pcs"]) == 2, "PC deleted before restore")
os.remove(os.path.join(DATA, "cache/drivers/intel-nic.zip"))
r = c.post("/settings/restore", data={"csrf": tok, "confirm": "REPLACE",
                                      "archive": (io.BytesIO(data), "ansiweb-backup.tar.gz")},
           content_type="multipart/form-data", follow_redirects=True)
ok("Restored" in r.text, "restore reports success")
ok(any(pc["name"] == "PC-HQ-001" for pc in store.load()["pcs"]), "restore brought the PC back")
ok(os.path.exists(os.path.join(DATA, "cache/drivers/intel-nic.zip")), "restore brought the driver file back")
# restore signs the session out
ok(c.get("/pcs").status_code == 302, "restore ends the session")
tok = csrf(c.get("/login").text)
c.post("/login", data={"username": "admin", "password": PW, "csrf": tok}, follow_redirects=True)
tok = csrf(c.get("/").text)
ok("Type REPLACE" in c.post("/settings/restore", data={"csrf": tok, "confirm": "",
                                                       "archive": (io.BytesIO(data), "b.tar.gz")},
                            content_type="multipart/form-data", follow_redirects=True).text,
   "restore needs confirmation")
ok("not a readable" in c.post("/settings/restore", data={"csrf": tok, "confirm": "REPLACE",
                                                         "archive": (io.BytesIO(b"not a tarball"), "b.tar.gz")},
                              content_type="multipart/form-data", follow_redirects=True).text,
   "a junk archive is rejected")
# a tarball that tries to escape the data folder
evil = io.BytesIO()
with tarfile.open(fileobj=evil, mode="w:gz") as t:
    info = tarfile.TarInfo("../../etc/evil")
    info.size = 3
    t.addfile(info, io.BytesIO(b"bad"))
evil.seek(0)
ok("no AnsiWEB data" in c.post("/settings/restore", data={"csrf": tok, "confirm": "REPLACE",
                                                          "archive": (evil, "evil.tar.gz")},
                               content_type="multipart/form-data", follow_redirects=True).text,
   "path traversal in an archive is refused")
ok(not os.path.exists("/etc/evil"), "nothing escaped the data folder")

# ---------------------------------------------------------------- uninstalling apps from PCs
r = c.get("/uninstalls")
ok(r.status_code == 200 and "Nothing queued" in r.text, "the uninstall page starts empty")
r = c.post("/uninstalls/add", data={"csrf": tok, "name": "Old PDF reader",
                                    "detect_pattern": "^Foxit Reader", "targets": ["site:HQ"],
                                    "notes": "replaced"}, follow_redirects=True)
ok("Preview it before removing anything" in r.text, "an uninstall entry is added with a warning")
u = store.load()["uninstalls"][0]
ok(u["detect_pattern"] == "^Foxit Reader" and u["enabled"] is True, "entry stored")
plan_un = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan_un["uninstalls"][0]["id"] == u["id"], "the entry reaches the plan")
ok(plan_un["hosts"]["PC-HQ-001"]["uninstalls"] == [u["id"]], "targeted PCs are listed")
ok(plan_un["hosts"]["PC-BR1-009"]["uninstalls"] == [], "PCs outside the target are not")
# a pattern that would match everything is refused
ok("matches every installed program" in
   c.post("/uninstalls/add", data={"csrf": tok, "name": "Everything", "detect_pattern": ".*",
                                   "targets": ["all"]}, follow_redirects=True).text,
   "a catch-all pattern is refused")
ok("not a valid regular expression" in
   c.post("/uninstalls/add", data={"csrf": tok, "name": "Broken", "detect_pattern": "(",
                                   "targets": ["all"]}, follow_redirects=True).text,
   "a broken pattern is refused")
ok("Enter a name" in c.post("/uninstalls/add", data={"csrf": tok, "detect_pattern": "^X",
                                                     "targets": ["all"]},
                            follow_redirects=True).text, "a nameless entry is refused")
# running needs typed confirmation; previewing does not
r = c.post(f"/uninstalls/{u['id']}/run", data={"csrf": tok, "target": "all"}, follow_redirects=True)
ok("Type REMOVE to confirm" in r.text, "uninstalling requires typed confirmation")
ok(jobs.last_job("uninstall_run") is None, "no uninstall job ran without confirmation")
wait_for_jobs()
r = c.post(f"/uninstalls/{u['id']}/preview", data={"csrf": tok, "target": "all"}, follow_redirects=True)
ok("Preview an uninstall" in r.text, "previewing starts a preview job")
wait_for_jobs()
ok(jobs.last_job("uninstall_preview") is not None, "the preview job was recorded")
ok(jobs.DEPLOY_TAGS["uninstall_preview"] == "uninstall"
   and jobs.DEPLOY_TAGS["uninstall_run"] == "uninstall", "both uninstall jobs use the uninstall tag")
# disable / enable / delete
r = c.post(f"/uninstalls/{u['id']}/toggle", data={"csrf": tok}, follow_redirects=True)
ok("disabled" in r.text and store.load()["uninstalls"][0]["enabled"] is False, "an entry can be disabled")
ok(json.load(open(os.path.join(DATA, "deploy_plan.json")))["uninstalls"] == [],
   "a disabled entry is left out of the plan")
c.post(f"/uninstalls/{u['id']}/toggle", data={"csrf": tok}, follow_redirects=True)
# removing an app can queue it for removal from the PCs
before = len(store.load()["uninstalls"])
r = c.post("/apps/line-of-business/delete", data={"csrf": tok, "uninstall": "on"}, follow_redirects=True)
ok("queued for removal from the PCs" in r.text, "removing an app can queue the uninstall")
queued = store.load()["uninstalls"]
ok(len(queued) == before + 1 and any(e["name"] == "LOB client" for e in queued),
   "the queued entry carries the app's name and pattern")
r = c.post(f"/uninstalls/{queued[-1]['id']}/delete", data={"csrf": tok}, follow_redirects=True)
ok("stay uninstalled" in r.text, "an entry can be deleted")
c.post(f"/uninstalls/{u['id']}/delete", data={"csrf": tok}, follow_redirects=True)
ok(store.load()["uninstalls"] == [], "the uninstall list can be emptied again")

# ---------------------------------------------------------------- stale PCs
import datetime as _dt
now = _dt.datetime(2026, 9, 12, 12, 0, 0)
ok(report_state.classify({"time": "2026-09-12 09:00:00"}, 14, now)["state"] == "ok",
   "a PC that reported today is current")
ok(report_state.classify({"time": "2026-08-01 09:00:00"}, 14, now)["state"] == "stale",
   "a PC silent for six weeks is stale")
ok(report_state.classify({}, 14, now)["state"] == "never", "a PC with no report is flagged")
ok(report_state.classify({"time": "not a date"}, 14, now)["state"] == "never",
   "an unreadable timestamp counts as never")
ok(report_state.classify({"time": "2026-09-13 09:00:00"}, 14, now)["age_days"] == 0,
   "a PC whose clock is ahead is not treated as negative-age")
ok(report_state.classify({"time": "2026-08-29 12:00:00"}, 14, now)["state"] == "stale",
   "exactly at the threshold counts as stale")
ok(report_state.classify({"time": "2026-08-30 12:00:00"}, 14, now)["state"] == "ok",
   "one day inside the threshold is still current")
summary = report_state.summarise([{"name": "A"}, {"name": "B"}, {"name": "C"}],
                                 {"A": {"time": "2026-09-12 09:00:00"}, "B": {"time": "2026-06-01 09:00:00"}},
                                 14, now)
ok(summary == {"ok": 1, "stale": 1, "never": 1, "stale_names": ["B"], "never_names": ["C"],
               "needs_attention": 2}, "the dashboard summary counts each state")
# PC-HQ-001 has a report from the fixture; the others never reported
r = c.get("/")
ok("PCs not reporting" in r.text, "the dashboard has a card for PCs that are not reporting")
ok("never reported" in c.get("/reports").text, "reports flag PCs that never reported")
r = c.get("/reports?only=stale")
ok("Show all PCs" in r.text, "reports can be filtered to only those PCs")
ok("PC-BR1-009" in r.text and "PC-HQ-001" not in r.text.split("Recent jobs")[0],
   "the filter hides PCs that are reporting")
csv_stale = c.get("/reports/export.csv").text
ok("freshness" in csv_stale.splitlines()[0] and "days_since_report" in csv_stale.splitlines()[0],
   "the CSV export carries freshness")
r = c.post("/settings", data={"csrf": tok, "server_ip": "192.168.1.10", "forks": "20", "batch_size": "20",
                              "log_retention_days": "45", "session_timeout_minutes": "60",
                              "stale_after_days": "3", "cc_hours": "24", "dep_time": "19:00"},
           follow_redirects=True)
ok("Settings saved" in r.text and store.load()["settings"]["stale_after_days"] == 3,
   "the threshold is configurable")
ok("between 1 and 365" in c.post("/settings", data={"csrf": tok, "forks": "20", "batch_size": "20",
                                                    "log_retention_days": "45",
                                                    "session_timeout_minutes": "60",
                                                    "stale_after_days": "0", "cc_hours": "24",
                                                    "dep_time": "19:00"}, follow_redirects=True).text,
   "a nonsense threshold is refused")
c.post("/settings", data={"csrf": tok, "server_ip": "192.168.1.10", "forks": "20", "batch_size": "20",
                          "log_retention_days": "45", "session_timeout_minutes": "60",
                          "stale_after_days": "14", "cc_hours": "24", "dep_time": "19:00"},
       follow_redirects=True)

# ---------------------------------------------------------------- audit log
entries = audit.entries(limit=1000)
ok(entries, "the audit log has entries")
actions = {e["action"] for e in entries}
ok("uninstall_add" in actions and "settings_page" in actions and "login" in actions,
   "changes, settings edits and sign-ins are all recorded")
ok(all(e["user"] for e in entries), "every entry names a user")
detail_blob = " ".join(e["detail"] for e in entries)
ok("S3cret!pw" not in detail_blob and "NewPw123456" not in detail_blob
   and "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE" not in detail_blob and PW not in detail_blob,
   "no password or product key was ever written to the audit log")
ok("(hidden)" in detail_blob, "secret fields are recorded as hidden")
ok(any("Old PDF reader" in e["detail"] for e in entries), "useful detail is kept")
refused = [e for e in entries if e["outcome"] != "ok"]
ok(refused, "refused attempts are recorded")
ok(any(e["action"] == "login" and e["outcome"] != "ok" for e in entries),
   "a failed sign-in is recorded")
# the audit log is a section of the Reports page now
r = c.get("/reports")
ok(r.status_code == 200 and "Audit log" in r.text, "the audit log is part of the Reports page")
ok("Added an app to uninstall" in r.text, "actions are shown in readable words")
ok("Jump to the audit log" in r.text, "the page links straight to it")
ok(c.get("/audit").status_code == 302, "the old audit URL redirects")
ok("Audit log" in c.get("/audit", follow_redirects=True).text, "and lands on the merged page")
ok(len(audit.entries(limit=1000, action="uninstall_add")) >= 1, "filtering by action works")
ok(all(e["user"] == "admin" for e in audit.entries(limit=50, user="admin")), "filtering by user works")
csv_audit = c.get("/audit/export.csv").text
ok("time,user,role,action" in csv_audit.splitlines()[0], "the audit CSV has a header")
ok("uninstall_add" in csv_audit, "the audit CSV carries the entries")
# retention
audit.record("olduser", "admin", "app_edit", "long ago")
with jobs._conn() as _c:
    _c.execute("UPDATE audit SET time='2020-01-01 00:00:00' WHERE user='olduser'")
removed = audit.prune(30)
ok(removed >= 1, "old audit entries are pruned")
ok(not audit.entries(limit=10, user="olduser"), "the pruned entry is gone")
ok(audit.entries(limit=10), "recent entries survive pruning")

# ---------------------------------------------------------------- software inventory
# the fixture report gains a full inventory, as the role would write it
_rep = os.path.join(DATA, "reports/PC-HQ-001.json")
_data = json.load(open(_rep))
_data["inventory_count"] = 4
_data["inventory_truncated"] = False
_data["inventory"] = [
    {"name": "7-Zip 24.09 (x64)", "version": "24.09", "publisher": "Igor Pavlov", "arch": "x64"},
    {"name": "Google Chrome", "version": "153.0.8010.37", "publisher": "Google LLC", "arch": "x64"},
    {"name": "Ancient Toolbar", "version": "2.1", "publisher": "Nobody Ltd", "arch": "x86"},
    {"name": "VLC media player", "version": "3.0.21", "publisher": "VideoLAN", "arch": "x86"}]
json.dump(_data, open(_rep, "w"))
json.dump({"host": "PC-BR1-009", "time": "2026-09-12 09:30:00", "facts": {"hostname": "PC-BR1-009"},
           "apps": [], "results": [], "inventory_count": 2, "inventory_truncated": False,
           "inventory": [{"name": "7-Zip 24.09 (x64)", "version": "24.09", "publisher": "Igor Pavlov",
                          "arch": "x64"},
                         {"name": "Ancient Toolbar", "version": "1.0", "publisher": "Nobody Ltd",
                          "arch": "x86"}]},
          open(os.path.join(DATA, "reports/PC-BR1-009.json"), "w"))

r = c.get("/inventory")
ok(r.status_code == 200 and "Software inventory" in r.text, "the inventory page renders")
ok("Ancient Toolbar" in r.text and "Google Chrome" in r.text, "programs from all PCs are listed")
ok("2 PC(s) have reported an inventory" in r.text, "it says how many PCs have reported")
# the same program and version on two PCs is one row counting both
r = c.get("/inventory?q=7-Zip")
ok("PC-HQ-001" in r.text and "PC-BR1-009" in r.text, "one row lists every PC that has it")
ok("Google Chrome" not in r.text, "the search filters out everything else")
ok("VideoLAN" in c.get("/inventory?q=videolan").text, "search matches the publisher, case-insensitively")
ok("3.0.21" in c.get("/inventory?q=3.0.21").text, "search matches the version")
# two versions of the same program stay separate rows
r = c.get("/inventory?q=Ancient")
ok("2.1" in r.text and "1.0" in r.text, "different versions are listed separately")
r = c.get("/inventory?pc=PC-BR1-009")
ok("Ancient Toolbar" in r.text and "Google Chrome" not in r.text, "the list can be limited to one PC")
csv_inv = c.get("/inventory/export.csv").text
ok("program,version,publisher,architecture,pc_count,pcs" in csv_inv.splitlines()[0], "inventory CSV header")
ok("Ancient Toolbar" in csv_inv and "PC-HQ-001;PC-BR1-009" in csv_inv.replace("PC-BR1-009;PC-HQ-001",
                                                                              "PC-HQ-001;PC-BR1-009"),
   "the CSV lists the PCs per program")
ok("Ancient Toolbar" in c.get("/pcs/PC-HQ-001/edit").text, "a PC's page shows its installed programs")
SETTINGS_BASE = {"server_ip": "192.168.1.10", "forks": "20", "batch_size": "20",
                 "log_retention_days": "45", "session_timeout_minutes": "60",
                 "stale_after_days": "14", "cc_hours": "24", "dep_time": "19:00"}
ok(store.load()["settings"]["collect_inventory"] in (True, False), "the collection setting exists")
c.post("/settings", data={"csrf": tok, "collect_inventory": "on", **SETTINGS_BASE}, follow_redirects=True)
ok(json.load(open(os.path.join(DATA, "deploy_plan.json")))["collect_inventory"] is True,
   "collection can be switched on and reaches the plan")
# unticking the box in the form switches it off
r = c.post("/settings", data={"csrf": tok, **SETTINGS_BASE}, follow_redirects=True)
ok(json.load(open(os.path.join(DATA, "deploy_plan.json")))["collect_inventory"] is False,
   "collection can be switched off")
ok("switched off in Settings" in c.get("/inventory").text, "the page says when collection is off")
c.post("/settings", data={"csrf": tok, "collect_inventory": "on", **SETTINGS_BASE}, follow_redirects=True)
ok(store.load()["settings"]["collect_inventory"] is True, "and back on again")

# ---------------------------------------------------------------- per-role scoping
# PC-HQ-001 is in site HQ; PC-BR1-009 is in Branch1; PC-BR2-001 is in Branch2 with group finance
users.create("sam", "sam-pass-12345", "helpdesk", ["site:Branch1"])
sc = app.test_client()
t = csrf(sc.get("/login").text)
r = sc.post("/login", data={"username": "sam", "password": "sam-pass-12345", "csrf": t},
            follow_redirects=True)
ok("Dashboard" in r.text, "a scoped user can sign in")
ok("Site: Branch1" in r.text, "the scope is shown in the interface")
t = csrf(sc.get("/").text)
body = sc.get("/pcs").text
ok("PC-BR1-009" in body and "PC-HQ-001" not in body, "only in-scope PCs are listed")
ok("PC-BR1-009" in sc.get("/reports").text and "PC-HQ-001" not in sc.get("/reports").text,
   "reports are limited to the scope")
ok("PC-HQ-001" not in sc.get("/inventory").text, "the inventory is limited to the scope")
ok("Google Chrome" not in sc.get("/inventory").text, "out-of-scope programs are not shown")
ok(sc.get("/pcs/PC-BR1-009/edit").status_code == 200, "an in-scope PC page opens")
ok(sc.get("/pcs/PC-HQ-001/edit").status_code == 403, "an out-of-scope PC page is refused")
# a scoped job is narrowed to the PCs allowed, whatever was asked for
r = sc.post("/jobs/start", data={"csrf": t, "kind": "ping", "target": "all"}, follow_redirects=True)
job = jobs.last_job("ping")
ok(job["target"] == "list:PC-BR1-009", "asking for all PCs is narrowed to the scope")
ok(store.limit_for(job["target"]) == "PC-BR1-009", "the limit passed to Ansible names only that PC")
r = sc.post("/jobs/start", data={"csrf": t, "kind": "ping", "target": "pc:PC-HQ-001"},
            follow_redirects=True)
ok("None of the PCs you can manage" in r.text, "targeting an out-of-scope PC is refused")
ok(jobs.last_job("ping")["target"] == "list:PC-BR1-009", "and no job ran for it")
r = sc.post("/jobs/start", data={"csrf": t, "kind": "ping", "target": "site:Branch2"},
            follow_redirects=True)
ok("None of the PCs you can manage" in r.text, "targeting another site is refused")
# an admin is never scoped
ok(users.scope_of(users.get("admin")) == [], "administrators have no scope")
_pcs = store.load()["pcs"]
ok(users.narrow_target(_pcs, [], "all") == "all", "an admin's target is left alone")
ok(users.narrow_target(_pcs, [], "site:HQ") == "site:HQ", "an admin's site target is left alone")
ok(users.narrow_target(_pcs, ["site:Branch1"], "all") == "list:PC-BR1-009",
   "a scoped target is narrowed to that site's PCs")
ok(users.narrow_target(_pcs, ["site:Branch1"], "site:Branch1") == "list:PC-BR1-009",
   "asking for your own site gives the same list")
try:
    users.narrow_target(_pcs, ["site:Branch1"], "pc:PC-HQ-001")
    ok(False, "targeting a PC outside the scope raises")
except users.UserError as exc:
    ok("None of the PCs you can manage" in str(exc), "targeting a PC outside the scope raises")
try:
    users.narrow_target(_pcs, ["group:nosuchgroup"], "all")
    ok(False, "a scope matching no PC raises")
except users.UserError:
    ok(True, "a scope matching no PC raises")
# scopes can be changed, and are dropped on promotion to admin
r = c.post("/users/sam/scope", data={"csrf": tok, "scope": ["site:Branch1", "group:finance"]},
           follow_redirects=True)
ok("Site: Branch1, Group: finance" in r.text, "a scope can be widened")
body = sc.get("/pcs").text
ok("PC-BR2-001" in body, "the widened scope takes effect on an open session")
c.post("/users/sam/role", data={"csrf": tok, "role": "admin"}, follow_redirects=True)
ok(users.get("sam")["scope"] == [], "promotion to administrator clears the scope")
c.post("/users/sam/role", data={"csrf": tok, "role": "viewer"}, follow_redirects=True)
c.post("/users/sam/scope", data={"csrf": tok, "scope": ["bogus:x"]}, follow_redirects=True)
ok(users.get("sam")["scope"] == [], "a meaningless scope entry is dropped")
c.post("/users/sam/delete", data={"csrf": tok}, follow_redirects=True)

ok(jobs.DEPLOY_TAGS.get("inventory") == "inventory", "an inventory-only job exists")
ok("Collect from all PCs" in c.get("/inventory").text, "the inventory page offers a collect button")

# ---------------------------------------------------------------- printers
r = c.get("/printers")
ok(r.status_code == 200 and "No printers yet" in r.text, "the printers page starts empty")
r = c.post("/printers/add", data={"csrf": tok, "name": "HQ LaserJet", "kind": "tcpip",
                                  "host": "10.0.0.9", "port": "9100",
                                  "driver": "HP Universal Printing PCL 6", "default": "on",
                                  "location": "2nd floor", "targets": ["site:HQ"]},
           follow_redirects=True)
ok("added" in r.text and "HQ LaserJet" in r.text, "a network printer is added")
r = c.post("/printers/add", data={"csrf": tok, "name": "Finance queue", "kind": "shared",
                                  "connection": r"\\printsrv\Finance", "targets": ["all"]},
           follow_redirects=True)
ok("Finance queue" in r.text, "a shared queue is added")
pr = store.load()["printers"]
ok(len(pr) == 2 and pr[0]["default"] is True and pr[0]["port"] == 9100, "printer settings stored")
plan_pr = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok([p["id"] for p in plan_pr["printers"]] == [pr[0]["id"], pr[1]["id"]], "printers reach the plan")
ok(pr[0]["id"] in plan_pr["hosts"]["PC-HQ-001"]["printers"], "an HQ PC gets the HQ printer")
ok(pr[0]["id"] not in plan_pr["hosts"]["PC-BR1-009"]["printers"], "a branch PC does not")
ok(pr[1]["id"] in plan_pr["hosts"]["PC-BR1-009"]["printers"], "but does get the shared queue")
# what is refused
for bad, label in [
        ({"name": "", "kind": "tcpip", "host": "10.0.0.9", "driver": "d"}, "a nameless printer"),
        ({"name": "X", "kind": "tcpip", "host": "", "driver": "d"}, "a network printer with no address"),
        ({"name": "X", "kind": "tcpip", "host": "10.0.0.9", "driver": ""}, "one with no driver"),
        ({"name": "X", "kind": "tcpip", "host": "10.0.0.9", "driver": "d", "port": "99999"}, "a silly port"),
        ({"name": "X", "kind": "shared", "connection": "not-a-path"}, "a malformed queue path")]:
    resp = c.post("/printers/add", data={"csrf": tok, "targets": ["all"], **bad}, follow_redirects=True)
    ok("flash error" in resp.text or "error" in resp.text, f"{label} is refused")
ok(len(store.load()["printers"]) == 2, "nothing invalid was stored")
# disable, run and delete
r = c.post(f"/printers/{pr[0]['id']}/toggle", data={"csrf": tok}, follow_redirects=True)
ok("disabled" in r.text and store.load()["printers"][0]["enabled"] is False, "a printer can be disabled")
ok(len(json.load(open(os.path.join(DATA, "deploy_plan.json")))["printers"]) == 1,
   "a disabled printer is left out of the plan")
c.post(f"/printers/{pr[0]['id']}/toggle", data={"csrf": tok}, follow_redirects=True)
wait_for_jobs()
r = c.post(f"/printers/{pr[1]['id']}/run", data={"csrf": tok, "target": "all"}, follow_redirects=True)
wait_for_jobs()
ok(jobs.last_job("printers") is not None, "a printer can be pushed on its own")
ok(jobs.DEPLOY_TAGS.get("printers") == "printers", "printers have their own job tag")
r = c.post(f"/printers/{pr[1]['id']}/delete", data={"csrf": tok}, follow_redirects=True)
ok("stays installed on the PCs" in r.text, "deleting explains it stays on the PCs")
ok(len(store.load()["printers"]) == 1, "the printer list shrinks")

# ---------------------------------------------------------------- uninstall from the inventory
r = c.get("/inventory")
ok("Remove from" in r.text, "the inventory offers an uninstall action")
ok("REMOVE" in r.text, "it asks for typed confirmation")
wait_for_jobs()
r = c.post("/inventory/uninstall", data={"csrf": tok, "program": "7-Zip 24.09", "target": "all"},
           follow_redirects=True)
ok("Previewing what removing" in r.text, "without confirmation it only previews")
ok(jobs.last_job("uninstall_preview") is not None, "a preview job ran")
ok(not store.load()["uninstalls"], "previewing adds no standing uninstall entry")
wait_for_jobs()
r = c.post("/inventory/uninstall", data={"csrf": tok, "program": "7-Zip 24.09", "target": "pc:PC-HQ-001",
                                         "confirm": "REMOVE"}, follow_redirects=True)
wait_for_jobs()
ok(jobs.last_job("uninstall_run") is not None, "typing REMOVE runs the uninstall")
ok(not store.load()["uninstalls"], "and still adds no standing entry")
r = c.post("/inventory/uninstall", data={"csrf": tok, "program": "", "target": "all"},
           follow_redirects=True)
ok("No program was chosen" in r.text, "an empty program is refused")
ok(web.ENDPOINT_PERMISSIONS["inventory_uninstall"] == users.RUN_JOBS,
   "an ad-hoc uninstall is an operational action, so helpdesk can do it")
ok(web.ENDPOINT_PERMISSIONS["uninstall_add"] == users.MANAGE_CONTENT,
   "but adding a standing uninstall entry still needs more")

# ---------------------------------------------------------------- login page branding
png = bytes.fromhex("89504e470d0a1a0a") + b"fake-but-png-enough"
ok(c.get("/logo").status_code == 404, "there is no logo to begin with")
ok("No logo uploaded" in c.get("/settings").text, "Settings says so")
r = c.post("/settings/branding", data={"csrf": tok, "site_name": "Aava IT",
                                       "logo": (io.BytesIO(png), "company.png")},
           content_type="multipart/form-data", follow_redirects=True)
ok("Logo uploaded" in r.text, "a logo can be uploaded")
ok(store.load()["settings"]["logo_file"] == "logo.png", "it is stored under a fixed name")
ok(os.path.exists(os.path.join(DATA, "branding/logo.png")), "the file is on disk")
ok(c.get("/logo").status_code == 200, "the logo is served")
ok(c.get("/logo").data == png, "and is the file that was uploaded")
# the sign-in page shows it, without needing a session
anon = app.test_client()
login_page = anon.get("/login").text
ok("/logo" in login_page and "Aava IT" in login_page,
   "the sign-in page shows the logo and name to anyone")
ok(anon.get("/logo").status_code == 200, "the logo needs no sign-in, since the sign-in page needs it")
ok(anon.get("/reports").status_code == 302, "but other pages still require one")
# only images, and not huge ones
ok("must be a PNG" in c.post("/settings/branding", data={"csrf": tok, "site_name": "Aava IT",
                                                         "logo": (io.BytesIO(b"MZ"), "evil.exe")},
                             content_type="multipart/form-data", follow_redirects=True).text,
   "a non-image is refused")
ok("must be a PNG" in c.post("/settings/branding", data={"csrf": tok, "site_name": "Aava IT",
                                                         "logo": (io.BytesIO(b"<svg/>"), "logo.svg")},
                             content_type="multipart/form-data", follow_redirects=True).text,
   "an SVG is refused, since it can carry scripts")
ok("under 2 MB" in c.post("/settings/branding", data={"csrf": tok, "site_name": "Aava IT",
                                                      "logo": (io.BytesIO(b"x" * (3 * 1024 * 1024)), "big.png")},
                          content_type="multipart/form-data", follow_redirects=True).text,
   "an oversized logo is refused")
ok(store.load()["settings"]["logo_file"] == "logo.png", "a refused upload leaves the old logo alone")
# the name can be changed without touching the logo
r = c.post("/settings/branding", data={"csrf": tok, "site_name": "Aava IT - Lagos"},
           follow_redirects=True)
ok(store.load()["settings"]["site_name"] == "Aava IT - Lagos" and c.get("/logo").status_code == 200,
   "the name can be changed on its own")
# and removed again
r = c.post("/settings/branding/remove", data={"csrf": tok}, follow_redirects=True)
ok("Logo removed" in r.text and c.get("/logo").status_code == 404, "the logo can be removed")
ok(not os.path.exists(os.path.join(DATA, "branding/logo.png")), "the file is deleted")
ok("AnsiWEB" in anon.get("/login").text, "the sign-in page still works without a logo")
# a backup carries the branding
c.post("/settings/branding", data={"csrf": tok, "site_name": "Aava IT",
                                   "logo": (io.BytesIO(png), "company.png")},
       content_type="multipart/form-data", follow_redirects=True)
import tarfile as _tar
with _tar.open(fileobj=io.BytesIO(backup.create())) as _t:
    ok("branding/logo.png" in _t.getnames(), "the logo is included in a backup")

# ---------------------------------------------------------------- roles and permissions
ok([u["username"] for u in users.all_users()] == ["admin"], "the old single admin was migrated")
ok(users.get("admin")["role"] == "admin", "the migrated account is an administrator")
r = c.get("/users")
ok(r.status_code == 200 and "Administrator" in r.text, "an admin can open the users page")
for name, role, pw in [("olivia", "operator", "operator-pass-1"),
                       ("hank", "helpdesk", "helpdesk-pass-1"),
                       ("vicky", "viewer", "viewer-pass-111")]:
    r = c.post("/users/add", data={"csrf": tok, "username": name, "password": pw, "role": role},
               follow_redirects=True)
    ok(f"Added {name}" in r.text, f"added a {role}")
ok("at least 10 characters" in c.post("/users/add", data={"csrf": tok, "username": "shorty",
                                                          "password": "abc", "role": "viewer"},
                                      follow_redirects=True).text, "a short password is refused")
ok("already exists" in c.post("/users/add", data={"csrf": tok, "username": "olivia",
                                                  "password": "another-pass-1", "role": "viewer"},
                              follow_redirects=True).text, "duplicate user names are refused")
ok("Unknown role" in c.post("/users/add", data={"csrf": tok, "username": "ghost",
                                                "password": "ghost-pass-11", "role": "superuser"},
                            follow_redirects=True).text, "an invented role is refused")

# a throwaway app for the "may change what is deployed" probe
c.post("/apps/new", data={"csrf": tok, "id": "rbac-probe", "name": "RBAC probe", "enabled": "on",
                          "source": "url", "url": "https://example.invalid/probe.msi", "version": "1.0",
                          "detect_pattern": "^RBAC probe", "targets": ["all"]}, follow_redirects=True)
ok(any(a["id"] == "rbac-probe" for a in store.load()["apps"]), "probe app created for the matrix")

# the permission matrix, exercised with real sessions
MATRIX = {
    "admin":    {"view": True,  "run": True,  "content": True,  "pcs": True,  "admin": True},
    "operator": {"view": True,  "run": True,  "content": True,  "pcs": True,  "admin": False},
    "helpdesk": {"view": True,  "run": True,  "content": False, "pcs": False, "admin": False},
    "viewer":   {"view": True,  "run": False, "content": False, "pcs": False, "admin": False},
}
PROBES = {
    "view":    ("get",  "/reports", {}),
    "run":     ("post", "/jobs/start", {"kind": "ping", "target": "all"}),
    "content": ("post", "/apps/rbac-probe/delete", {}),   # a throwaway app, not part of the set
    "pcs":     ("post", "/pcs/add", {"name": "PC-RBAC-1", "ip": "10.9.9.9", "site": "HQ"}),
    "admin":   ("get",  "/users", {}),
}
for role, expected in MATRIX.items():
    who = {"admin": ("admin", PW), "operator": ("olivia", "operator-pass-1"),
           "helpdesk": ("hank", "helpdesk-pass-1"), "viewer": ("vicky", "viewer-pass-111")}[role]
    rc = app.test_client()
    t = csrf(rc.get("/login").text)
    r = rc.post("/login", data={"username": who[0], "password": who[1], "csrf": t}, follow_redirects=True)
    ok("Dashboard" in r.text, f"{role} can sign in")
    t = csrf(rc.get("/").text)
    for perm, allowed in expected.items():
        method, url, data = PROBES[perm]
        if method == "get":
            resp = rc.get(url)
        else:
            resp = rc.post(url, data={**data, "csrf": t})
        denied = resp.status_code == 403
        ok(denied != allowed, f"{role} {'may' if allowed else 'may not'} {perm} ({url})")
    if role != "admin":
        ok("Audit log" not in rc.get("/reports").text,
           f"{role} does not see the audit log on the Reports page")
        ok(rc.get("/audit/export.csv").status_code == 403,
           f"{role} cannot export the audit log")
    else:
        ok("Audit log" in rc.get("/reports").text, "an admin sees the audit log there")
    # everyone can reach help and change their own password
    ok(rc.get("/help").status_code == 200, f"{role} can open the help page")
    ok(rc.get("/settings").status_code == 200, f"{role} can open settings")
    if role != "admin":
        ok("shown read-only" in rc.get("/settings").text, f"{role} sees settings as read-only")
        ok("Save activation settings" not in rc.get("/settings").text,
           f"{role} is not offered the activation form")
        ok(rc.post("/settings/activation", data={"csrf": t, "mode": "mak"}).status_code == 403,
           f"{role} cannot post activation settings")
        ok(rc.get("/settings/backup").status_code == 403, f"{role} cannot download a backup")
        ok(rc.post("/settings/restore", data={"csrf": t, "confirm": "REPLACE"}).status_code == 403,
           f"{role} cannot restore a backup")
        ok(rc.post("/users/add", data={"csrf": t, "username": "sneak", "password": "sneaky-pass-1",
                                       "role": "admin"}).status_code == 403,
           f"{role} cannot create an account")
    # own password change works for any role
    if role == "viewer":
        r = rc.post("/settings/password", data={"csrf": t, "current": who[1],
                                                "new": "viewer-pass-222", "confirm": "viewer-pass-222"},
                    follow_redirects=True)
        ok("Password changed" in r.text, "a viewer can change their own password")
        ok(bool(users.authenticate("vicky", "viewer-pass-222")), "the new password works")
        users.set_password("vicky", "viewer-pass-111")
        ok(rc.post("/users/vicky/password", data={"csrf": t, "password": "sneaky-pass-1"}).status_code == 403,
           "a viewer cannot reset anyone's password")

# an unmapped route falls back to admin-only
ok("help_page" in web.ENDPOINT_PERMISSIONS, "the help page is mapped")
ok(web.ENDPOINT_PERMISSIONS.get("settings_updates") is None,
   "settings routes are deliberately unmapped, so they need an admin")

# role changes and the last-administrator guard
r = c.post("/users/olivia/role", data={"csrf": tok, "role": "viewer"}, follow_redirects=True)
ok("olivia is now a Viewer" in r.text, "a role can be changed")
ok("only administrator left" in c.post("/users/admin/role", data={"csrf": tok, "role": "viewer"},
                                       follow_redirects=True).text,
   "the last administrator cannot be demoted")
ok("cannot remove your own account" in c.post("/users/admin/delete", data={"csrf": tok},
                                              follow_redirects=True).text,
   "you cannot remove your own account")
try:
    users.delete("admin")
    ok(False, "the last administrator cannot be removed")
except users.UserError as exc:
    ok("only administrator left" in str(exc), "the last administrator cannot be removed")
ok("cannot disable your own account" in c.post("/users/admin/disable", data={"csrf": tok, "disabled": "on"},
                                               follow_redirects=True).text,
   "you cannot disable yourself")
# a disabled account cannot sign in, and an open session ends
r = c.post("/users/hank/disable", data={"csrf": tok, "disabled": "on"}, follow_redirects=True)
ok("hank disabled" in r.text, "an account can be disabled")
hc = app.test_client()
t = csrf(hc.get("/login").text)
ok("or the account is disabled" in hc.post("/login", data={"username": "hank", "password": "helpdesk-pass-1",
                                                           "csrf": t}).text,
   "a disabled account cannot sign in")
# role changes take effect on an existing session
oc = app.test_client()
t = csrf(oc.get("/login").text)
oc.post("/login", data={"username": "olivia", "password": "operator-pass-1", "csrf": t}, follow_redirects=True)
ok(oc.get("/reports").status_code == 200, "olivia (now viewer) can still read")
t = csrf(oc.get("/").text)
ok(oc.post("/jobs/start", data={"csrf": t, "kind": "ping", "target": "all"}).status_code == 403,
   "a demotion applies to a session that is already open")
c.post("/users/olivia/delete", data={"csrf": tok}, follow_redirects=True)
c.post("/users/vicky/delete", data={"csrf": tok}, follow_redirects=True)
c.post("/users/hank/delete", data={"csrf": tok}, follow_redirects=True)
ok([u["username"] for u in users.all_users()] == ["admin"], "users can be removed again")

# ---------------------------------------------------------------- help page
r = c.get("/help")
ok("Deploy on Ubuntu Server" in r.text and "Deploy on WSL" in r.text,
   "the help page covers both Ubuntu and WSL")
for snippet in ["sudo ./install.sh", "git clone https://github.com/mzuogha/AnsiWEB.git",
                "netsh interface portproxy add", "systemd=true", "Register-ScheduledTask",
                "sudo ansiweb deploy all", "Prepare-AnsibleHost.ps1", "journalctl -u ansiweb -f"]:
    ok(snippet in r.text, f"help includes: {snippet}")
ok("192.168.1.10" in r.text, "help uses the configured server address")
ok("what each role can do" in c.get("/users").text.lower(), "the users page explains the roles")

# ---------------------------------------------------------------- release notes
import yaml as _yaml
_raw = _yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..",
                                         "ansiweb/defaults/release_notes.yml")).read())
ok(isinstance(_raw, list) and len(_raw) >= 2, "the release notes file parses as a list of releases")
notes = release.all_notes()
ok(len(notes) == len(_raw), "every release in the file is loaded")
ok(len(notes) >= 2, "release notes are available")
ok([n["version"] for n in notes] == sorted([n["version"] for n in notes],
                                           key=release.version_key, reverse=True),
   "releases are listed newest first")
ok(release.current().get("version") == __version__, "the running version has an entry")
ok([n["version"] for n in release.since(notes[-1]["version"])] == [n["version"] for n in notes[:-1]],
   "'since' returns only newer releases")
ok(release.since("") == [], "no previous version means nothing to announce")
ok(release.version_key("1.10.0") > release.version_key("1.9.0"),
   "versions compare numerically, not as text")
r = c.get("/release-notes")
ok(r.status_code == 200 and __version__ in r.text, "the release notes page renders")
ok("this version" in r.text, "the running version is marked")
ok(notes[-1]["version"] in r.text, "older releases are listed too")
ok("what's new" in c.get("/").text, "the sidebar links to the notes")

# after an upgrade the dashboard says so, until the notes are opened
jobs.kv_set("acknowledged_version", "1.0.0")
r = c.get("/")
ok("upgraded from 1.0.0" in r.text and "See what changed" in r.text,
   "the dashboard announces an upgrade")
r = c.get("/release-notes")
ok("new to you" in r.text, "releases new since the upgrade are marked")
ok(jobs.kv_get("acknowledged_version") == __version__, "opening the page acknowledges the version")
ok("upgraded from" not in c.get("/").text, "the notice is gone once read")

# ---------------------------------------------------------------- every page renders
for url in ["/", "/apps", "/apps/new", "/apps/7zip/edit", "/apps/vendor-app/edit", "/pcs",
            "/pcs/PC-HQ-001/edit", "/files", "/drivers/intel-nic/edit",
            "/scripts/set-power-plan/edit", "/registry/disable-autostart/edit", "/reports", "/jobs",
            "/settings", "/release-notes", "/help", "/users", "/uninstalls", "/inventory"]:
    ok(c.get(url).status_code == 200, f"page renders: {url}")
ok(c.get("/office").status_code == 404, "the removed Office page is gone")
ok(c.get("/directory").status_code == 404, "there is no Active Directory page")
ok(c.get("/nonsense").status_code == 404, "unknown resource kind is 404")
r = c.get("/prepare-script")
ok(b"$ControlNodeIP = '192.168.1.10'" in r.data and b"$AccountName = 'Admin'" in r.data,
   "prep script is filled in with the server IP and account")

print(f"ALL {checks} CHECKS PASSED")
