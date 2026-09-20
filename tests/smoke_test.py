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


import yaml as _y


def _role_tasks():
    return _y.safe_load(open(os.path.join(os.path.dirname(__file__), "..",
                                          "ansible/roles/ansiweb_apps/tasks/main.yml")))


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

# ---------------------------------------------------------------- sign-in throttling
tc = app.test_client()
t = csrf(tc.get("/login").text)
for n in range(users.MAX_FAILURES):
    r = tc.post("/login", data={"username": "admin", "password": "wrong", "csrf": t})
    ok("Wrong user name" in r.text, f"failed attempt {n + 1} is rejected")
r = tc.post("/login", data={"username": "admin", "password": "wrong", "csrf": t})
ok("Too many failed attempts" in r.text, "further attempts are locked out")
r = tc.post("/login", data={"username": "admin", "password": PW, "csrf": t})
ok("Too many failed attempts" in r.text, "even the correct password is refused while locked out")
ok(users.locked_for("admin", "127.0.0.1") > 0, "the lockout is recorded for that user and address")
ok(users.locked_for("someone-else", "127.0.0.1") == 0, "other accounts are unaffected")
users.clear_failures("admin", "127.0.0.1")
ok(users.locked_for("admin", "127.0.0.1") == 0, "a correct sign-in clears the count")
tc2 = app.test_client()
t2 = csrf(tc2.get("/login").text)
r = tc2.post("/login", data={"username": "admin", "password": PW, "csrf": t2}, follow_redirects=True)
ok("Dashboard" in r.text, "signing in works again afterwards")

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

# a PC can be found by the person it belongs to
c.post("/pcs/add", data={"csrf": tok, "name": "PC-USR-001", "ip": "10.4.4.4", "site": "HQ",
                         "user": "Jane Doe"}, follow_redirects=True)
saved = [pc for pc in store.load()["pcs"] if pc["name"] == "PC-USR-001"][0]
ok(saved["user"] == "Jane Doe", "a PC records who it is assigned to")
body = c.get("/pcs").text
ok("Assigned to" in body and "Jane Doe" in body, "the PC list shows the person")
found = c.get("/pcs?q=jane").text
table = found.split("Add a PC")[0]          # the list, not the import example below it
ok("PC-USR-001" in table and "PC-HQ-001" not in table, "searching by person finds their PC")
ok("1 of" in found, "and says how many matched")
ok("PC-USR-001" in c.get("/pcs?q=10.4.4").text, "and searching by address still works")
ok("Jane Doe" in c.get("/deploy?target=all").text, "the deploy picker names the person")
ok("Jane Doe" in c.get("/pcs/PC-USR-001/edit").text, "the PC's own page shows them")
ok("Jane Doe" in c.get("/reports").text, "so does the reports table")
csv_user = c.get("/reports/export.csv").text
ok("assigned_to" in csv_user.splitlines()[0] and "Jane Doe" in csv_user, "and the CSV export")
# imported from CSV as a fifth column
r = c.post("/pcs/import", data={"csvfile": "", "csv": "PC-CSV-001,10.4.4.5,HQ,finance,John Smith",
                                "csrf": tok}, follow_redirects=True)
imported = [pc for pc in store.load()["pcs"] if pc["name"] == "PC-CSV-001"]
ok(imported and imported[0]["user"] == "John Smith", "a CSV import can carry the person")
c.post("/pcs/PC-USR-001/delete", data={"csrf": tok}, follow_redirects=True)
c.post("/pcs/PC-CSV-001/delete", data={"csrf": tok}, follow_redirects=True)

# a PC can be removed from its own row
body = c.get("/pcs").text
ok(body.count("Remove this PC from AnsiWEB") >= 1, "each PC row offers Remove")
before = len(store.load()["pcs"])
c.post("/pcs/add", data={"csrf": tok, "name": "PC-TMP-001", "ip": "10.9.9.9", "site": "HQ"},
       follow_redirects=True)
ok(len(store.load()["pcs"]) == before + 1, "a PC can be added")
r = c.post("/pcs/PC-TMP-001/delete", data={"csrf": tok}, follow_redirects=True)
ok(len(store.load()["pcs"]) == before, "and removed again from its row")

# ---------------------------------------------------------------- the playbook wrapper
_play = _yaml_play = __import__("yaml").safe_load(
    open(os.path.join(os.path.dirname(__file__), "..", "ansible/playbooks/deploy.yml")))[0]
_wrapper = _play["tasks"][0]
_include_tags = set(_wrapper["block"][0].get("tags") or [])
_job_tags = {t for t in jobs.DEPLOY_TAGS.values() if t}
_part_tags = {p[0] for p in jobs.DEPLOY_PARTS}
for _t in sorted({x for tag in _job_tags for x in tag.split(",")} | _part_tags):
    ok(_t in _include_tags,
       f"a job limited to '{_t}' can still enter the role")
ok("always" not in _include_tags,
   "the role is not tagged 'always', which would run every task on every job")
ok(all("always" in (_t.get("tags") or [])
       for _t in _wrapper["rescue"] + _wrapper["always"]),
   "the failure report and the summary run whatever tags a job uses")
ok(any("failed" in str(_t).lower() for _t in _wrapper["rescue"]),
   "a failure names the task that failed")
ok(any("aw_results" in str(_t) for _t in _wrapper["always"]),
   "and the summary lists what was applied")

# ------------------------------------------- a deployment that applies nothing says so
idle_log = """
TASK [ansiweb_apps : Install or upgrade apps] ***
skipping: [PC-A]
TASK [What was applied on this PC] ***
ok: [PC-A] =>
    msg:
    - 'PC-A - 0 item(s):'
"""
ok(jobs.applied_nothing(idle_log) == ["PC-A"], "a PC that had nothing applied is spotted")
idle_summary = jobs.summarise_run(idle_log)
ok("Nothing was applied on: PC-A" in idle_summary, "and the summary says so plainly")
ok("not in the cache yet" in idle_summary, "with the usual reason")
busy_log = """
TASK [ansiweb_apps : Install or upgrade apps] ***
changed: [PC-A]
TASK [What was applied on this PC] ***
ok: [PC-A] =>
    msg:
    - 'PC-A - 3 item(s):'
"""
ok(not jobs.applied_nothing(busy_log), "a deployment that did something is not flagged")
ok("Nothing was applied" not in jobs.summarise_run(busy_log), "and its summary stays quiet")

# apps that are configured but not cached are called out before you deploy
_plan = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(any(x["kind"] == "apps" for x in _plan.get("skipped", [])) or _plan["apps"],
   "the plan records apps it could not include")
_dash = c.get("/").text
if any(x["kind"] == "apps" for x in _plan.get("skipped", [])):
    ok("not in the cache yet" in _dash, "and the dashboard warns about them")

# ---------------------------------------------------------------- what each task did
# The playbook's own error handling should not appear as extra failures
_noisy = jobs.summarise_run("""
TASK [ansiweb_apps : Add the driver for IT Ricoh to the Windows driver store] ***
fatal: [PC-A]: FAILED! => {"msg": "catalogue mismatch"}
TASK [Say which task failed] ***
ok: [PC-A]
TASK [Keep the job's failed status] ***
fatal: [PC-A]: FAILED! => {"msg": "stopped at"}
TASK [What was applied on this PC] ***
ok: [PC-A]
""")
ok(_noisy.count("[x]") == 1, "only the task that really failed is marked failed")
ok("Keep the job's failed status" not in _noisy, "AnsiWEB's own bookkeeping is left out")
ok("Add the driver for IT Ricoh" in _noisy, "and the real cause is named")
sample = """
TASK [ansiweb_apps : Load the plan] ***
ok: [PC-A]
ok: [PC-B]
TASK [ansiweb_apps : Install or upgrade apps] ***
changed: [PC-A] => (item=7-Zip)
ok: [PC-B]
TASK [ansiweb_apps : Set up shared folders] ***
skipping: [PC-A]
skipping: [PC-B]
TASK [ansiweb_apps : Show what will be done] ***
fatal: [PC-A]: FAILED! => {"msg": "undefined"}
ok: [PC-B]
"""
summary = jobs.summarise_run(sample)
ok("What each task did" in summary, "a run is summarised task by task")
ok("[v] Load the plan  (2 ok)" in summary, "a task that worked everywhere is marked")
ok("[v] Install or upgrade apps  (1 changed, 1 ok)" in summary, "changes are counted separately")
ok("[-] Set up shared folders  (2 skipped)" in summary, "skipped tasks are marked apart from failures")
ok("[x] Show what will be done  (1 FAILED, 1 ok)" in summary, "a task that failed anywhere is marked failed")
ok("- Show what will be done  on PC-A" in summary, "the failures are listed with their PCs")
ok("re-running is safe" in summary, "and it says what to do next")
ok("[x]" not in jobs.summarise_run("""
TASK [ansiweb_apps : Install apps] ***
changed: [PC-A]
"""), "a clean run has nothing marked failed")
ok("Failed:" not in jobs.summarise_run("""
TASK [ansiweb_apps : Install apps] ***
ok: [PC-A]
"""), "and no failure list")
# the playbook's own error handling reports a failure rather than being one
_book = jobs.summarise_run("""
TASK [ansiweb_apps : Add the driver for IT Ricoh to the Windows driver store] ***
fatal: [PC-A]: FAILED! => {"msg": "catalog hash"}
TASK [Say which task failed] ***
ok: [PC-A]
TASK [Keep the job's failed status] ***
fatal: [PC-A]: FAILED! => {"msg": "stopped at"}
TASK [What was applied on this PC] ***
ok: [PC-A]
""")
ok("Add the driver" in _book, "a real failure is reported")
ok("Keep the job" not in _book and "Say which task failed" not in _book,
   "AnsiWEB's own error handling is not listed as tasks")
ok(_book.count("[x]") == 1, "so one failure reads as one failure")

ok(jobs.summarise_run("nothing that looks like ansible") == "",
   "output that is not a playbook run is left alone")
ok("[x] Gathering Facts" in jobs.summarise_run("""
TASK [Gathering Facts] ***
unreachable: [PC-C]: UNREACHABLE! => {"msg": "timed out"}
"""), "an unreachable PC counts as a failure")
ok("[v] Save the report  (1 changed)" in jobs.summarise_run("""
TASK [ansiweb_apps : Save the report] ***
changed: [PC-A -> localhost]
"""), "a delegated task is attributed to the PC, not the server")

# the role gives its registered results a default, so a skipped task cannot
# leave a later one referring to something undefined
_setup = [t for t in _role_tasks() if "Work out what this PC should have" == t.get("name")][0]
_facts = _setup["ansible.builtin.set_fact"]
ok("aw_detect" in _facts and "aw_inventory" in _facts,
   "results that later tasks read start out empty")

# ---------------------------------------------------------------- the role's setup tasks
# Every job that limits itself to tags still needs the facts these tasks set;
# without "always" they are skipped and the run dies on an undefined variable.
_role = _role_tasks()
_defined = {}
for _task in _role:
    for _var in (_task.get("ansible.builtin.set_fact") or {}):
        _defined.setdefault(_var, []).append(_task)
# Tasks every job needs, whatever parts it covers. Without "always" a job
# limited to one part skips them, which has bitten us: no working folders to
# download into, a reboot nobody performs, and no report of what happened.
for _needed in ("Create the AnsiWEB working folders on the PC",
                "Reboot where something asked for it",
                "List PCs that still need a reboot",
                "Collect facts for the report",
                "Read this PC's previous report",
                "Save the report for the AnsiWEB dashboard"):
    _task = next((t for t in _role if t.get("name") == _needed), None)
    ok(_task, f"the role still has '{_needed}'")
    ok("always" in (_task.get("tags") or []), f"'{_needed}' runs on every job")
# and nothing else is left untagged, since untagged means skipped by any
# tag-limited job
_untagged = [t.get("name") for t in _role if not t.get("tags")]
ok(not _untagged, f"no task is left untagged: {_untagged}")

for _var in ("aw_plan", "aw_host", "aw_results", "aw_shares", "aw_printers", "aw_apps",
             "aw_drivers", "aw_scripts", "aw_registry", "aw_uninstalls"):
    _tasks = _defined.get(_var, [])
    ok(_tasks, f"{_var} is set somewhere in the role")
    ok(all("always" in (_t.get("tags") or []) for _t in _tasks),
       f"{_var} is set on every run, whatever tags a job uses")

# ---------------------------------------------------------------- choosing what to deploy
r = c.get("/deploy?target=pc:PC-HQ-001")
ok(r.status_code == 200 and "What to deploy" in r.text, "the deploy page offers a choice")
for key, label, _note in jobs.DEPLOY_PARTS:
    ok(f'value="{key}"' in r.text, f"it offers {label.lower()}")
ok(r.text.count('checked') >= len(jobs.DEPLOY_PARTS), "everything is ticked by default")
ok("nothing set up" in r.text, "parts with nothing configured are marked")
ok("Deploy…" in c.get("/pcs").text, "the PCs page opens the chooser")
ok("Deploy…" in c.get("/").text, "and so does the dashboard")
# a selection becomes the job's tags
wait_for_jobs()
r = c.post("/deploy", data={"csrf": tok, "parts": ["apps", "printers"], "target": "pc:PC-HQ-001"},
           follow_redirects=True)
wait_for_jobs()
log = jobs.log_path(jobs.last_job("deploy")["id"]).read_text()
ok("--tags apps,printers" in log, "only the chosen parts are run")
ok("--limit PC-HQ-001" in log, "on the chosen PC")
# everything selected is a plain deployment, with no tag limit
wait_for_jobs()
c.post("/deploy", data={"csrf": tok, "parts": [p[0] for p in jobs.DEPLOY_PARTS], "target": "all"},
       follow_redirects=True)
wait_for_jobs()
log = jobs.log_path(jobs.last_job("deploy")["id"]).read_text()
ok("--tags" not in log, "choosing everything runs the whole deployment")
# picking individual PCs, and refusing an empty choice
wait_for_jobs()
c.post("/deploy", data={"csrf": tok, "parts": ["apps"], "pcs": ["PC-HQ-001", "PC-BR2-001"]},
       follow_redirects=True)
wait_for_jobs()
ok(jobs.last_job("deploy")["target"] == "list:PC-HQ-001,PC-BR2-001", "chosen PCs become the target")
r = c.post("/deploy", data={"csrf": tok, "target": "all"}, follow_redirects=True)
ok("Choose at least one thing" in r.text, "an empty choice is refused")
r = c.post("/deploy", data={"csrf": tok, "parts": ["rm -rf /"], "target": "all"},
           follow_redirects=True)
ok("Choose at least one thing" in r.text, "an invented part is ignored, not passed through")

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
]},
          open(os.path.join(DATA, "reports/PC-HQ-001.json"), "w"))
r = c.get("/reports")
ok("PC-HQ-001" in r.text and "Windows 11 Pro" in r.text, "report page shows the PC")
ok("reboot pending" in r.text, "pending reboot is visible")
ok("Intel NIC" in r.text, "driver result is visible")
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
ok(c.get("/uninstalls").status_code == 302, "the old uninstall URL redirects")
r = c.get("/inventory")
ok(r.status_code == 200 and "Standing uninstalls" in r.text,
   "standing uninstalls live on the Inventory page")
ok("No standing uninstalls" in r.text, "and start empty")
r = c.post("/uninstalls/add", data={"csrf": tok, "name": "Old PDF reader",
                                    "detect_pattern": "^Foxit Reader", "targets": ["site:HQ"],
                                    "notes": "replaced"}, follow_redirects=True)
ok("Preview it before removing anything" in r.text, "an uninstall entry is added with a warning")
ok("Old PDF reader" in c.get("/inventory").text, "the entry shows on the Inventory page")
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
# the stale notice can be dismissed, and returns when a different PC goes quiet
body = c.get("/").text
ok("PC(s) have never reported" in body or "have not reported" in body, "the notice is shown")
import re as _re
sig = _re.search(r'name="signature" value="([^"]*)"', body).group(1)
ok(sig, "the notice carries which PCs it is about")
r = c.post("/dismiss/stale", data={"csrf": tok, "signature": sig}, follow_redirects=True)
ok("See which" not in r.text, "dismissing hides it")
ok(c.get("/").text.count("See which") == 0, "and it stays hidden on a reload")
c.post("/pcs/add", data={"csrf": tok, "name": "PC-QUIET-1", "ip": "10.9.9.8", "site": "HQ"},
       follow_redirects=True)
ok("See which" in c.get("/").text, "a newly quiet PC brings the notice back")
c.post("/pcs/PC-QUIET-1/delete", data={"csrf": tok}, follow_redirects=True)
ok(c.post("/dismiss/nonsense", data={"csrf": tok}).status_code == 404, "an unknown notice is 404")
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

# ------------------------------------------------ a restored backup still deploys
# The point is not just that the files come back, but that everything derived
# from them - inventory, plan, vault - is rebuilt and Ansible still accepts it.
import subprocess as _sp

before_cfg = store.load()
before_plan = json.load(open(os.path.join(DATA, "deploy_plan.json")))
archive = backup.create()
ok(len(archive) > 1000, "a backup is produced")

# scribble over everything a restore is supposed to put back
wrecked = store.load()
wrecked["pcs"] = []
wrecked["apps"] = []
wrecked["printers"] = []
store.save(wrecked)
for name in os.listdir(os.path.join(DATA, "cache", "drivers")):
    os.remove(os.path.join(DATA, "cache", "drivers", name))
os.remove(os.path.join(DATA, "inventory", "group_vars", "windows", "vault.yml"))
ok(store.load()["pcs"] == [], "the configuration is wrecked")

backup.restore(io.BytesIO(archive))    # restore takes a file-like object

after_cfg = store.load()
ok([pc["name"] for pc in after_cfg["pcs"]] == [pc["name"] for pc in before_cfg["pcs"]],
   "the PCs come back")
ok([a["id"] for a in after_cfg["apps"]] == [a["id"] for a in before_cfg["apps"]], "so do the apps")
ok([p["id"] for p in after_cfg["printers"]] == [p["id"] for p in before_cfg["printers"]],
   "and the printers")
ok(os.listdir(os.path.join(DATA, "cache", "drivers")), "the uploaded driver files are back")
# the parts a deployment actually reads
ok(os.path.exists(os.path.join(DATA, "inventory", "hosts.yml")), "the inventory exists again")
hosts = open(os.path.join(DATA, "inventory", "hosts.yml")).read()
for pc in before_cfg["pcs"]:
    ok(pc["name"] in hosts, f"{pc['name']} is in the inventory")
vault_text = open(os.path.join(DATA, "inventory", "group_vars", "windows", "vault.yml")).read()
ok(vault_text.startswith("$ANSIBLE_VAULT"), "the secrets file is back and still encrypted")
ok(vault.secret_status().get("vault_ansible_svc_password"),
   "and the account password still decrypts, so deployments can sign in")
after_plan = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(sorted(after_plan["hosts"]) == sorted(before_plan["hosts"]),
   "the deployment plan covers the same PCs")
ok(after_plan["apps"] == before_plan["apps"], "and the same apps")
# and Ansible itself still accepts the restored files
check = _sp.run(["ansible-playbook", "-i", os.path.join(DATA, "inventory", "hosts.yml"),
                 "--vault-password-file", os.path.join(DATA, ".vault_pass"),
                 "-e", f"ansiweb_data={DATA}",
                 os.path.join(os.path.dirname(__file__), "..", "ansible/playbooks/deploy.yml"),
                 "--syntax-check"],
                capture_output=True, text=True,
                env={**os.environ, "ANSIBLE_CONFIG": os.path.join(os.path.dirname(__file__), "..",
                                                                  "ansible/ansible.cfg")})
ok(check.returncode == 0, f"a deployment still runs against the restored data: {check.stderr[:200]}")

# ---------------------------------------------------------------- choosing your own password
users.create("newbie", "given-by-admin", "helpdesk")
ok(users.must_change_password("newbie"), "a new account must choose its own password")
nc = app.test_client()
t = csrf(nc.get("/login").text)
r = nc.post("/login", data={"username": "newbie", "password": "given-by-admin", "csrf": t},
            follow_redirects=True)
ok("Choose your own password" in r.text, "and is asked to on the first sign-in")
ok(nc.get("/pcs", follow_redirects=True).text.count("Choose your own password") == 1,
   "every other page leads back there")
ok(nc.get("/reports", follow_redirects=True).status_code == 200, "without erroring")
ok("Choose your own password" not in nc.get("/help").text, "help is still readable")
t = csrf(nc.get("/password/new").text)
r = nc.post("/settings/password", data={"csrf": t, "current": "given-by-admin",
                                        "new": "mine-alone-1", "confirm": "mine-alone-1"},
            follow_redirects=True)
ok("Password changed" in r.text, "changing it works")
ok(not users.must_change_password("newbie"), "and the requirement is cleared")
ok("Dashboard" in nc.get("/").text, "the rest of AnsiWEB is then available")
# an administrator resetting a password asks for the same again
r = c.post("/users/newbie/password", data={"csrf": tok, "password": "reset-by-admin"},
           follow_redirects=True)
ok("must change it" in r.text, "resetting a password says they must change it")
ok(users.must_change_password("newbie"), "and it is required again")
c.post("/users/newbie/delete", data={"csrf": tok}, follow_redirects=True)

# ---------------------------------------------------------------- per-role scoping
# PC-HQ-001 is in site HQ; PC-BR1-009 is in Branch1; PC-BR2-001 is in Branch2 with group finance
users.create("sam", "sam-pass-12345", "helpdesk", ["site:Branch1"], force_change=False)
sc = app.test_client()
t = csrf(sc.get("/login").text)
r = sc.post("/login", data={"username": "sam", "password": "sam-pass-12345", "csrf": t},
            follow_redirects=True)
ok("Dashboard" in r.text, "a scoped user can sign in")
ok("Site: Branch1" in r.text, "the scope is shown in the interface")
t = csrf(sc.get("/").text)
body = sc.get("/pcs").text
ok("PC-BR1-009" in body and "PC-HQ-001" not in body, "only in-scope PCs are listed")
_rep = sc.get("/reports").text
ok("PC-BR1-009" in _rep and "PC-HQ-001" not in _rep, "reports are limited to the scope")
ok("PC-HQ-001" not in sc.get("/jobs").text,
   "and job history does not name PCs outside the scope")
ok("PC-HQ-001" not in sc.get("/").text, "nor does the dashboard")
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

# ---------------------------------------------------------------- shared folders
r = c.get("/shares")
ok(r.status_code == 200 and "No shared folders yet" in r.text, "the shares page starts empty")
ok("Currently off" in r.text, "it warns that file sharing is off")
r = c.post("/shares/file-sharing", data={"csrf": tok, "enabled": "on", "targets": ["all"]},
           follow_redirects=True)
ok("settings saved" in r.text, "file sharing can be turned on")
ok(store.load()["file_sharing"]["enabled"] is True, "the setting is stored")
r = c.post("/shares/add", data={"csrf": tok, "name": "Team", "path": r"D:\Shared\Team",
                                "description": "Team files", "change": r"CORP\Team, PC-A\Users",
                                "read": "Everyone", "targets": ["site:HQ"]}, follow_redirects=True)
ok("Team" in r.text and "added" in r.text, "a shared folder is added")
sh = store.load()["shares"][0]
ok(sh["change"] == [r"CORP\Team", r"PC-A\Users"] and sh["read"] == ["Everyone"],
   "accounts are split into a list")
plan_sh = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan_sh["shares"][0]["path"].endswith("Team"), "the share reaches the plan")
ok(plan_sh["file_sharing"]["enabled"] is True, "so does the sharing setting")
ok(plan_sh["hosts"]["PC-HQ-001"]["shares"] == [sh["id"]], "an HQ PC gets the share")
ok(plan_sh["hosts"]["PC-BR1-009"]["shares"] == [], "a branch PC does not")
ok(plan_sh["hosts"]["PC-BR1-009"]["file_sharing"] is True, "but does get file sharing, targeted at all")
for bad, label in [({"name": "bad/name", "path": r"D:\X"}, "a share name with a slash"),
                   ({"name": "Ok", "path": "not-a-path"}, "a folder that is not a full path"),
                   ({"name": "", "path": r"D:\X"}, "a nameless share")]:
    resp = c.post("/shares/add", data={"csrf": tok, "targets": ["all"], **bad}, follow_redirects=True)
    ok("flash error" in resp.text, f"{label} is refused")
ok(len(store.load()["shares"]) == 1, "nothing invalid was stored")
r = c.post(f"/shares/{sh['id']}/toggle", data={"csrf": tok}, follow_redirects=True)
ok(len(json.load(open(os.path.join(DATA, "deploy_plan.json")))["shares"]) == 0,
   "a disabled share is left out of the plan")
c.post(f"/shares/{sh['id']}/toggle", data={"csrf": tok}, follow_redirects=True)
wait_for_jobs()
r = c.post(f"/shares/{sh['id']}/run", data={"csrf": tok, "target": "all"}, follow_redirects=True)
wait_for_jobs()
ok(jobs.last_job("shares") is not None, "a share can be set up on its own")
ok(jobs.DEPLOY_TAGS.get("shares") == "shares", "shares have their own job tag")
# a share's permissions can be changed after the fact
r = c.get(f"/shares/{sh['id']}/edit")
ok(r.status_code == 200 and "Who can use it" in r.text, "a share can be opened for editing")
ok("CORP\\Team" in r.text, "with its current access shown")
r = c.post(f"/shares/{sh['id']}/edit", data={"csrf": tok, "name": "Team", "path": r"D:\Shared\Team",
                                             "description": "Team files", "enabled": "on",
                                             "read": "Everyone", "change": "CORP\\Team, CORP\\Leads",
                                             "full": "Administrators", "targets": ["all"]},
           follow_redirects=True)
ok("saved" in r.text, "changes save")
edited = [x for x in store.load()["shares"] if x["id"] == sh["id"]][0]
ok(edited["change"] == ["CORP\\Team", "CORP\\Leads"], "the new access list is stored")
ok(edited["full"] == ["Administrators"] and edited["read"] == ["Everyone"], "as are the others")
ok(edited["targets"] == ["all"], "and the targets")
plan_sh2 = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(plan_sh2["shares"][0]["change"] == ["CORP\\Team", "CORP\\Leads"], "and it reaches the PCs")
ok("Edit" in c.get("/shares").text, "the list links to the editor")
ok("not-a-path" not in c.post(f"/shares/{sh['id']}/edit",
                              data={"csrf": tok, "name": "Team", "path": "not-a-path",
                                    "targets": ["all"]}, follow_redirects=True).text.split("value=")[0],
   "a bad path is refused")
ok([x for x in store.load()["shares"] if x["id"] == sh["id"]][0]["path"].startswith("D:"),
   "and the old path is kept")

r = c.post(f"/shares/{sh['id']}/delete", data={"csrf": tok}, follow_redirects=True)
ok("stay on the PCs" in r.text, "deleting explains the share stays on the PCs")

# ---------------------------------------------------------------- finding apps in winget
# The GitHub API is stood in for, so the search logic is tested without the network.
_FAKE_TREE = {
    "m": ["Microsoft", "Mozilla"],
    "m/Mozilla": ["Firefox", "Thunderbird"],
    "m/Microsoft": ["VisualStudioCode"],
    "v": ["VideoLAN"],
    "v/VideoLAN": ["VLC"],
}
_real_get_json = cache.get_json
cache.get_json = lambda url, token="": [{"name": n, "type": "dir"}
                                        for n in _FAKE_TREE.get(url.split("manifests/")[1], [])]
cache._search_cache.clear()

ok(c.get("/apps").text.count("Find in winget") == 1, "Apps & Cache links to the catalogue search")
r = c.get("/apps/search")
ok(r.status_code == 200 and "winget catalogue" in r.text, "the search page opens")
r = c.get("/apps/search?q=mozilla")
ok("Mozilla.Firefox" in r.text and "Mozilla.Thunderbird" in r.text, "a publisher search lists its packages")
ok("Mozilla.Firefox" in c.get("/apps/search?q=Mozilla.Fire").text, "a partial package ID resolves")
ok("Nothing matched" in c.get("/apps/search?q=nosuchthing").text, "a miss says so")
ok("at least two characters" in c.get("/apps/search?q=m").text, "a one-letter search is refused")
# adding one from the results
before_apps = len(store.load()["apps"])
r = c.post("/apps/search/add", data={"csrf": tok, "winget_id": "Mozilla.Thunderbird",
                                     "name": "Thunderbird", "targets": ["all"]},
           follow_redirects=True)
ok("Added Thunderbird" in r.text, "a result can be added to the standard set")
added = [a for a in store.load()["apps"] if a.get("winget_id") == "Mozilla.Thunderbird"]
ok(added, "it is stored as a winget app")
ok(added[0]["source"] == "winget" and added[0]["detect_pattern"].startswith("^"),
   "with a source and a starting detection pattern")
ok(len(store.load()["apps"]) == before_apps + 1, "and nothing else changed")
ok("already in the set" in c.get("/apps/search?q=mozilla").text,
   "something already in the set is marked, not offered twice")
r = c.post("/apps/search/add", data={"csrf": tok, "winget_id": "Mozilla.Thunderbird",
                                     "name": "Thunderbird again"}, follow_redirects=True)
ok("already in the standard set" in r.text, "and cannot be added twice")
r = c.post("/apps/search/add", data={"csrf": tok, "winget_id": "", "name": "Nothing"},
           follow_redirects=True)
ok("No package was chosen" in r.text, "an empty choice is refused")
# a rate limit or outage is reported, not swallowed
def _boom(url, token=""):
    raise cache.CacheError("GitHub rate limit reached; add a GitHub token in Settings")
cache.get_json = _boom
cache._search_cache.clear()
ok("rate limit" in c.get("/apps/search?q=mozilla").text, "an API failure is explained on the page")
cache.get_json = _real_get_json
cache._search_cache.clear()
c.post("/apps/thunderbird/delete", data={"csrf": tok}, follow_redirects=True)

# ------------------------------------------------ a package whose .inf files are not all usable
r = c.post("/files/add", data={"csrf": tok, "name": "Ricoh printer driver", "run_mode": "once",
                               "targets": ["all"], "inf_filter": "MPC*",
                               "payload": (zip_bytes(["MPC3000_.inf", "oemsetup.inf", "ricoh.cat"]),
                                           "ricoh.zip")},
           content_type="multipart/form-data", follow_redirects=True)
ok("uploaded" in r.text, "a driver package can name which .inf files to use")
drv_f = [d for d in store.load()["drivers"] if d["name"] == "Ricoh printer driver"][0]
ok(drv_f["inf_filter"] == "MPC*", "the filter is stored")
plan_f = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok([d for d in plan_f["drivers"] if d["id"] == drv_f["id"]][0]["inf_filter"] == "MPC*",
   "and reaches the PCs in the plan")
ok("Only use these .inf files" in c.get(f"/drivers/{drv_f['id']}/edit").text,
   "the driver's page offers it")
r = c.post("/printers/add", data={"csrf": tok, "name": "Ricoh MP", "host": "10.0.0.21",
                                  "driver": "RICOH PCL6", "driver_inf": "MPC*",
                                  "targets": ["all"]}, follow_redirects=True)
pr_f = [p for p in store.load()["printers"] if p["name"] == "Ricoh MP"][0]
ok(pr_f["driver_inf"] == "MPC*", "a printer can do the same for its staged driver")
ok(".inf: MPC*" in c.get("/printers").text, "and the page shows it")
c.post(f"/printers/{pr_f['id']}/delete", data={"csrf": tok}, follow_redirects=True)
c.post(f"/drivers/{drv_f['id']}/delete", data={"csrf": tok}, follow_redirects=True)

# ------------------------------------------------ reading models out of a driver package
from ansiweb import infparse                                            # noqa: E402
_ricoh_inf = b"""
[Version]
Signature="$Windows NT$"
Provider=%Ricoh%
CatalogFile=RICOHJBP.cat
[Manufacturer]
%Ricoh%=Ricoh,NTamd64.10.0
[Ricoh.NTamd64.10.0]
"RICOH MP C3003 PCL 6" = MPC3003, RICOH_MPC3003
%MPC4504% = MPC4504, RICOH_MPC4504
[Strings]
Ricoh="Ricoh"
MPC4504="RICOH MP C4504 PCL 6"
"""
_pkg = io.BytesIO()
with zipfile.ZipFile(_pkg, "w") as _z:
    _z.writestr("disk1/MPC3000_.inf", _ricoh_inf)
    _z.writestr("disk1/oemsetup.inf", b'[Version]\nSignature="$Windows NT$"\n')
    _z.writestr("z06594L17/disk1/MPC3000_.inf", _ricoh_inf)      # the duplicate copy vendors ship
_pkg.seek(0)
_read = infparse.models_in_zip(_pkg)
_names = [m["model"] for m in _read["models"]]
ok(_names == ["RICOH MP C3003 PCL 6", "RICOH MP C4504 PCL 6"],
   "every model in a package is listed, in order")
ok(all(m["inf"] == "MPC3000_.inf" for m in _read["models"]),
   "each model says which .inf offers it")
ok(len(_read["models"]) == 2, "a duplicate copy of the same .inf is not listed twice")
ok(infparse.models_in_zip(io.BytesIO(b"not a zip"))["error"], "an unreadable package is reported")
ok(infparse.models_in_zip(io.BytesIO(_pkg.getvalue()))["models"], "and a readable one is not")

# choosing one of them for a printer
_pkg.seek(0)
r = c.post("/printers/add", data={"csrf": tok, "name": "Ricoh IT", "host": "10.0.0.31",
                                  "driver": "placeholder", "targets": ["all"],
                                  "driver_package": (_pkg, "ricoh.zip")},
           content_type="multipart/form-data", follow_redirects=True)
ok("added" in r.text, "a printer can be added with a multi-model package")
rp = [p for p in store.load()["printers"] if p["name"] == "Ricoh IT"][0]
body = c.get("/printers").text
ok("model(s) in the package" in body, "the page offers the models it found")
ok("RICOH MP C4504 PCL 6" in body, "including one behind a %token%")
r = c.post(f"/printers/{rp['id']}/driver-name",
           data={"csrf": tok, "driver": "RICOH MP C4504 PCL 6", "inf": "MPC3000_.inf"},
           follow_redirects=True)
ok("will use the &#39;RICOH MP C4504 PCL 6&#39; driver" in r.text or
   "will use the 'RICOH MP C4504 PCL 6' driver" in r.text, "a model can be chosen")
rp = [p for p in store.load()["printers"] if p["name"] == "Ricoh IT"][0]
ok(rp["driver"] == "RICOH MP C4504 PCL 6" and rp["driver_inf"] == "MPC3000_.inf",
   "the choice and its .inf are stored")
plan_m = json.load(open(os.path.join(DATA, "deploy_plan.json")))
chosen = [p for p in plan_m["printers"] if p["name"] == "Ricoh IT"][0]
ok(chosen["driver_inf"] == "MPC3000_.inf", "so only that .inf is installed on the PCs")
ok("Choose a model" in c.post(f"/printers/{rp['id']}/driver-name", data={"csrf": tok, "driver": ""},
                              follow_redirects=True).text, "an empty choice is refused")
c.post(f"/printers/{rp['id']}/delete", data={"csrf": tok}, follow_redirects=True)

# the inventory row controls are visible rather than hidden behind a summary
body = c.get("/inventory").text
ok(">Remove<" in body, "each program row has a visible Remove button")
ok("Preview" in body, "and a Preview beside it")
c.post("/uninstalls/add", data={"csrf": tok, "name": "Old viewer", "detect_pattern": "^OldViewer",
                                "targets": ["all"]}, follow_redirects=True)
with_entry = c.get("/inventory").text
ok("Remove entry" in with_entry, "a standing uninstall can be removed")
ok("Old viewer" in with_entry, "and is listed on the same page")
_u = [u for u in store.load()["uninstalls"] if u["name"] == "Old viewer"][0]
c.post(f"/uninstalls/{_u['id']}/delete", data={"csrf": tok}, follow_redirects=True)
ok("tablewrap" in body, "wide tables scroll instead of losing their last column")

# ------------------------------------------------------ printer features and tooltips
_zf = io.BytesIO()
import zipfile as _zip
with _zip.ZipFile(_zf, "w") as _z:
    _z.writestr("disk1/MPC.inf", b'[Manufacturer]\n%R%=R,NTamd64\n[R.NTamd64]\n"RICOH MP C3003"=X.GPD\n')
    _z.writestr("disk1/MPC.dsc", b"*Feature: Duplex\nStaple finisher\nCollate\nTray 3")
_zf.seek(0)
ok(infparse.features_in_zip(_zf) == ["duplex", "staple", "collate", "trays"],
   "the features a package mentions are read from it")
ok(infparse.features_in_zip(io.BytesIO(b"not a zip")) == [], "an unreadable package gives none")
_zf.seek(0)
r = c.post("/printers/add", data={"csrf": tok, "name": "Feature printer", "host": "10.0.0.78",
                                  "driver": "RICOH MP C3003", "targets": ["all"],
                                  "driver_package": (_zf, "ricoh.zip")},
           content_type="multipart/form-data", follow_redirects=True)
fp = [p for p in store.load()["printers"] if p["name"] == "Feature printer"][0]
body = c.get("/printers").text
ok("Printing defaults" in body, "each printer offers its printing defaults")
ok("Two-sided printing" in body, "and the package's features are shown as a hint")
r = c.post(f"/printers/{fp['id']}/features",
           data={"csrf": tok, "duplex": "TwoSidedLongEdge", "colour": "Grayscale",
                 "collate": "yes", "paper_size": "A4"}, follow_redirects=True)
ok("Printing defaults saved" in r.text, "they can be saved")
fp = [p for p in store.load()["printers"] if p["name"] == "Feature printer"][0]
ok(fp["duplex"] == "TwoSidedLongEdge" and fp["colour"] == "Grayscale"
   and fp["collate"] == "yes" and fp["paper_size"] == "A4", "and are stored")
plan_f = json.load(open(os.path.join(DATA, "deploy_plan.json")))
pf = [p for p in plan_f["printers"] if p["name"] == "Feature printer"][0]
ok(pf["duplex"] == "TwoSidedLongEdge" and pf["paper_size"] == "A4", "and reach the PCs")
ok("does not support" in r.text or "not support" in r.text,
   "with a note that the printer decides what it can do")
c.post(f"/printers/{fp['id']}/delete", data={"csrf": tok}, follow_redirects=True)

# tooltips are drawn in one floating box, so nothing can clip them
_css = open(os.path.join(os.path.dirname(__file__), "..", "ansiweb/static/style.css")).read()
_js = open(os.path.join(os.path.dirname(__file__), "..", "ansiweb/static/app.js")).read()
ok("#tipbox" in _css and "position: fixed" in _css.split("#tipbox")[1][:200],
   "the tooltip box is fixed to the window")
ok("z-index: 9999" in _css.split("#tipbox")[1][:400], "and sits above everything else")
ok("[data-tip]:hover::after" not in _css,
   "the old in-element tooltip, which scrolling containers clipped, is gone")
ok("document.body.appendChild(box)" in _js, "it is attached to the page body")

# ------------------------------------------- PCs that report their own address
_secret = vault.app_secret("checkin_token")
ok(_secret and len(_secret) > 20, "a check-in token is generated at startup")
anon2 = app.test_client()
ok(anon2.post("/checkin", data={"name": "PC-HQ-001"}).status_code == 403,
   "checking in without the token is refused")
ok(anon2.post("/checkin", data={"name": "PC-HQ-001", "token": "wrong"}).status_code == 403,
   "and with the wrong one")
r = anon2.post("/checkin", data={"name": "PC-HQ-001", "token": _secret},
               environ_overrides={"REMOTE_ADDR": "10.7.7.7"})
ok(r.status_code == 200, "a known PC can report in")
_pc = [pc for pc in store.load()["pcs"] if pc["name"] == "PC-HQ-001"][0]
ok(_pc["seen_ip"] == "10.7.7.7" and _pc.get("seen_at"), "its address and the time are recorded")
hosts_yml = open(os.path.join(DATA, "inventory", "hosts.yml")).read()
ok("10.7.7.7" in hosts_yml, "and the inventory uses the address it reported")
# the address comes from the connection, not from anything the caller says
anon2.post("/checkin", data={"name": "PC-HQ-001", "token": _secret, "ip": "1.2.3.4"},
           headers={"X-Forwarded-For": "6.6.6.6"}, environ_overrides={"REMOTE_ADDR": "10.7.7.8"})
_pc = [pc for pc in store.load()["pcs"] if pc["name"] == "PC-HQ-001"][0]
ok(_pc["seen_ip"] == "10.7.7.8",
   "a claimed address and a spoofed forwarding header are both ignored")
anon2.post("/checkin", data={"name": "PC-HQ-001", "token": _secret},
           headers={"X-Forwarded-For": "10.7.7.9"}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
ok([pc for pc in store.load()["pcs"] if pc["name"] == "PC-HQ-001"][0]["seen_ip"] == "10.7.7.9",
   "but the header is honoured when the request came through our own proxy")
ok(anon2.post("/checkin", data={"name": "NOT-A-PC", "token": _secret}).status_code == 404,
   "an unknown PC is not added by checking in")
ok(anon2.post("/checkin", data={"name": "../etc", "token": _secret}).status_code == 400,
   "and a name that is not a computer name is refused")
ok(not any(_secret in (e.get("detail") or "") for e in audit.entries(limit=200)),
   "the token is never written to the audit trail")
# a PC can be added with no address at all
r = c.post("/pcs/add", data={"csrf": tok, "name": "PC-DHCP-01", "ip": "", "site": "HQ"},
           follow_redirects=True)
ok(any(pc["name"] == "PC-DHCP-01" for pc in store.load()["pcs"]),
   "a PC can be added without an address")
ok("PC-DHCP-01" in open(os.path.join(DATA, "inventory", "hosts.yml")).read(),
   "and is reached by name until it reports one")
ok("neither an IP address nor a host name" in
   c.post("/pcs/add", data={"csrf": tok, "name": "PC-BAD-01", "ip": "not an address!",
                            "site": "HQ"}, follow_redirects=True).text,
   "but nonsense in that box is still refused")
c.post("/pcs/PC-DHCP-01/delete", data={"csrf": tok}, follow_redirects=True)

# downloads are restricted to http and https
for _bad in ("file:///etc/passwd", "ftp://host/x"):
    try:
        cache.check_url(_bad)
        ok(False, f"{_bad} is refused")
    except cache.CacheError:
        ok(True, f"{_bad} is refused")
ok(cache.check_url("https://example.com/x.msi"), "an ordinary download is allowed")

# --------------------------------------------- why an app deployment failed
ok("exited with 1603" in jobs.install_hint("win_package failed with exit code 1603"),
   "a common installer exit code is explained")
ok("already running" in jobs.install_hint("exit code 1618"), "so is a busy installer")
ok("Check for updates now" in jobs.install_hint("checksum mismatch on 7zip.msi"),
   "a bad download points at re-caching it")
ok("port 80" in jobs.install_hint("PC-A cannot reach the AnsiWEB cache at http://10.0.0.1/software/"),
   "and an unreachable cache is named as the cause")
ok(jobs.install_hint("some unrelated failure") == "", "anything else adds no noise")
_role_yaml = open(os.path.join(os.path.dirname(__file__), "..",
                               "ansible/roles/ansiweb_apps/tasks/main.yml")).read()
ok("Check the PC can reach the AnsiWEB cache" in _role_yaml,
   "the PC checks it can reach the cache before downloading anything")
ok("software_url" in json.load(open(os.path.join(DATA, "deploy_plan.json"))),
   "and the plan tells it where that is")

# ------------------------------------------------- editing and moving PCs
body = c.get("/pcs").text
ok(">Edit</a>" in body, "each PC row has an Edit button")
ok("Move ticked PCs" in body, "and several can be moved at once")
edit = c.get("/pcs/PC-HQ-001/edit").text
ok('name="site"' in edit and 'name="ip"' in edit and 'name="user"' in edit,
   "the edit page covers site, address and who it belongs to")
r = c.post("/pcs/PC-HQ-001/edit", data={"csrf": tok, "name": "PC-HQ-001", "ip": "192.168.1.99",
                                        "site": "HQ", "user": "Jane Doe", "groups": "finance"},
           follow_redirects=True)
edited = [pc for pc in store.load()["pcs"] if pc["name"] == "PC-HQ-001"][0]
ok(edited["ip"] == "192.168.1.99" and edited["user"] == "Jane Doe" and "finance" in edited["groups"],
   "an existing PC can be edited")
# moving one PC
r = c.post("/pcs/move", data={"csrf": tok, "pc": "PC-HQ-001", "site": "Branch1"},
           follow_redirects=True)
ok("Moved 1 PC(s) to Branch1" in r.text, "a PC can be moved to another site")
ok([pc for pc in store.load()["pcs"] if pc["name"] == "PC-HQ-001"][0]["site"] == "Branch1",
   "and the move is stored")
ok("deployment still suits them" in r.text, "with a word about what that changes")
# and several together
r = c.post("/pcs/move", data={"csrf": tok, "pcs": ["PC-HQ-001", "PC-BR1-009"], "site": "HQ"},
           follow_redirects=True)
ok("Moved 2 PC(s) to HQ" in r.text, "several PCs move together")
r = c.post("/pcs/move", data={"csrf": tok, "pcs": ["PC-HQ-001"], "site": "HQ"},
           follow_redirects=True)
ok("already at that site" in r.text, "moving one that is already there says so")
ok("Choose a site that exists" in c.post("/pcs/move", data={"csrf": tok, "pc": "PC-HQ-001",
                                                            "site": "Nowhere"},
                                         follow_redirects=True).text, "an unknown site is refused")
ok("Tick the PCs to move" in c.post("/pcs/move", data={"csrf": tok, "site": "HQ"},
                                    follow_redirects=True).text, "and moving nothing is refused")
c.post("/pcs/move", data={"csrf": tok, "pc": "PC-BR1-009", "site": "Branch1"}, follow_redirects=True)

# the theme switch
_css = open(os.path.join(os.path.dirname(__file__), "..", "ansiweb/static/style.css")).read()
_js = open(os.path.join(os.path.dirname(__file__), "..", "ansiweb/static/app.js")).read()
_base = open(os.path.join(os.path.dirname(__file__), "..", "ansiweb/templates/base.html")).read()
ok('[data-theme="dark"]' in _css, "there is a dark palette")
ok("prefers-color-scheme: dark" in _css, "which follows the system setting by default")
ok("data-theme-toggle" in _base and "themetoggle" in _css, "and a switch in the sidebar")
ok("localStorage.setItem('ansiweb-theme'" in _js, "the choice is remembered in the browser")
ok("localStorage.getItem('ansiweb-theme')" in _base,
   "and applied before the page is drawn, so it does not flash")
ok("background: #fff;" not in _css, "panels use a variable, so they follow the theme")
ok("data-theme-toggle" in c.get("/").text, "the switch is on the page")

# ------------------------------------------------------------ renaming a site
_before = store.load()
_pcs_at_hq = [pc["name"] for pc in _before["pcs"] if pc.get("site") == "HQ"]
ok(_pcs_at_hq, "there are PCs at HQ to move")
c.post("/printers/add", data={"csrf": tok, "name": "HQ site printer", "host": "10.0.0.77",
                              "driver": "HP", "targets": ["site:HQ"]}, follow_redirects=True)
r = c.post("/sites", data={"csrf": tok, "action": "rename", "site": "HQ", "new_name": "Head Office"},
           follow_redirects=True)
ok("renamed to" in r.text, "a site can be renamed")
after = store.load()
ok("Head Office" in after["sites"] and "HQ" not in after["sites"], "the name changes")
ok(all(pc.get("site") == "Head Office" for pc in after["pcs"] if pc["name"] in _pcs_at_hq),
   "its PCs move with it")
_pr = [p for p in after["printers"] if p["name"] == "HQ site printer"][0]
ok(_pr["targets"] == ["site:Head Office"], "and anything aimed at it follows")
ok("already a site called" in c.post("/sites", data={"csrf": tok, "action": "rename",
                                                     "site": "Head Office", "new_name": "Branch1"},
                                     follow_redirects=True).text,
   "renaming onto an existing site is refused")
ok("Enter the new name" in c.post("/sites", data={"csrf": tok, "action": "rename",
                                                  "site": "Head Office", "new_name": " "},
                                  follow_redirects=True).text, "an empty new name is refused")
ok("still has PCs" in c.post("/sites", data={"csrf": tok, "action": "delete", "site": "Head Office"},
                             follow_redirects=True).text, "a site with PCs cannot be deleted")
c.post(f"/printers/{_pr['id']}/delete", data={"csrf": tok}, follow_redirects=True)
c.post("/sites", data={"csrf": tok, "action": "rename", "site": "Head Office", "new_name": "HQ"},
       follow_redirects=True)
ok("HQ" in store.load()["sites"], "and back again for the rest of the tests")

# ---------------------------------------------------------------- printers
r = c.get("/printers")
ok(r.status_code == 200 and "No printers yet" in r.text, "the printers page starts empty")
r = c.post("/printers/add", data={"csrf": tok, "name": "HQ LaserJet",
                                  "host": "10.0.0.9", "port": "9100",
                                  "driver": "HP Universal Printing PCL 6", "default": "on",
                                  "location": "2nd floor", "targets": ["site:HQ"]},
           follow_redirects=True)
ok("added" in r.text and "HQ LaserJet" in r.text, "a network printer is added")
r = c.post("/printers/add", data={"csrf": tok, "name": "Branch inkjet", "host": "10.20.0.9",
                                  "port": "9100", "driver": "Brother HL", "targets": ["all"]},
           follow_redirects=True)
ok("Branch inkjet" in r.text, "a second printer is added")
pr = store.load()["printers"]
ok(len(pr) == 2 and pr[0]["default"] is True and pr[0]["port"] == 9100, "printer settings stored")
plan_pr = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok([p["id"] for p in plan_pr["printers"]] == [pr[0]["id"], pr[1]["id"]], "printers reach the plan")
ok(pr[0]["id"] in plan_pr["hosts"]["PC-HQ-001"]["printers"], "an HQ PC gets the HQ printer")
ok(pr[0]["id"] not in plan_pr["hosts"]["PC-BR1-009"]["printers"], "a branch PC does not")
ok(pr[1]["id"] in plan_pr["hosts"]["PC-BR1-009"]["printers"], "but does get the shared queue")
# what is refused
for bad, label in [
        ({"name": "", "host": "10.0.0.9", "driver": "d"}, "a nameless printer"),
        ({"name": "X", "host": "", "driver": "d"}, "a printer with no address"),
        ({"name": "X", "host": "10.0.0.9", "driver": ""}, "one with no driver"),
        ({"name": "X", "host": "10.0.0.9", "driver": "d", "port": "99999"}, "a silly port"),
        ({"name": "X", "host": "bad address!", "driver": "d"}, "a malformed address")]:
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
# a driver package can be staged with the printer
zipped = zip_bytes(["hp.inf", "hp.cat"])
r = c.post(f"/printers/{pr[0]['id']}/driver", data={"csrf": tok,
                                                    "driver_package": (zipped, "hp-driver.zip")},
           content_type="multipart/form-data", follow_redirects=True)
ok("Driver staged" in r.text, "a driver package can be staged with a printer")
staged = store.load()["printers"][0]
ok(staged["driver_file"].startswith(pr[0]["id"]) and staged["driver_sha256"],
   "the package is stored and checksummed")
ok(os.path.exists(os.path.join(DATA, "cache/printers", staged["driver_file"])), "the file is on disk")
ok(staged["driver_original"] == "hp-driver.zip", "the original file name is kept for display")
ok("hp-driver.zip" in c.get("/printers").text, "and shown on the page")
plan_d = json.load(open(os.path.join(DATA, "deploy_plan.json")))["printers"][0]
ok(plan_d["driver_url"].endswith(f"/printers/{staged['driver_file']}"), "the PCs are told where to get it")
ok(plan_d["driver_unpack"].startswith("C:\\ProgramData\\AnsiWEB"), "and where to unpack it")
ok("must be a .zip" in c.post(f"/printers/{pr[0]['id']}/driver",
                              data={"csrf": tok, "driver_package": (io.BytesIO(b"x"), "driver.exe")},
                              content_type="multipart/form-data", follow_redirects=True).text,
   "a driver package that is not a .zip is refused")
# printers can be pushed to chosen PCs
wait_for_jobs()
r = c.post(f"/printers/{pr[0]['id']}/run", data={"csrf": tok, "pcs": ["PC-HQ-001", "PC-BR2-001"]},
           follow_redirects=True)
wait_for_jobs()
ok(jobs.last_job("printers")["target"] == "list:PC-HQ-001,PC-BR2-001",
   "a printer can be pushed to individual PCs")
ok(store.limit_for("list:PC-HQ-001,PC-BR2-001") == "PC-HQ-001,PC-BR2-001",
   "which becomes an Ansible limit naming just those PCs")
# an individual PC can also be a standing target
r = c.post("/printers/add", data={"csrf": tok, "name": "Reception", "host": "10.0.0.11",
                                  "driver": "HP", "targets": ["pc:PC-HQ-001"]}, follow_redirects=True)
ok("Reception" in r.text, "a printer can target one PC")
plan_one = json.load(open(os.path.join(DATA, "deploy_plan.json")))
recep = [p for p in plan_one["printers"] if p["name"] == "Reception"][0]
ok(recep["id"] in plan_one["hosts"]["PC-HQ-001"]["printers"], "that PC gets it")
ok(recep["id"] not in plan_one["hosts"]["PC-BR1-009"]["printers"], "others do not")
# shared queues from an older version are kept but switched off
cfg_old = store.load()
cfg_old["printers"].append({"id": "old-shared", "name": "Old queue", "enabled": True,
                            "kind": "shared", "connection": r"\\srv\Q", "targets": ["all"]})
import yaml as _yaml
open(os.path.join(DATA, "config.yml"), "w").write(_yaml.safe_dump(cfg_old))
migrated = [p for p in store.load()["printers"] if p["id"] == "old-shared"][0]
ok(migrated["enabled"] is False and "no longer supported" in migrated.get("notes", ""),
   "a shared queue from an older version is disabled, not deleted")
ok("kind" not in migrated and "connection" not in migrated, "and its old fields are dropped")

# the staged driver goes with the printer
staged_path = os.path.join(DATA, "cache/printers", store.load()["printers"][0]["driver_file"])
ok(os.path.exists(staged_path), "the staged driver is on disk before deleting")
c.post(f"/printers/{pr[0]['id']}/delete", data={"csrf": tok}, follow_redirects=True)
ok(not os.path.exists(staged_path), "deleting a printer removes its staged driver file")
r = c.post(f"/printers/{pr[1]['id']}/delete", data={"csrf": tok}, follow_redirects=True)
ok("stays installed on the PCs" in r.text, "deleting explains it stays on the PCs")

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

# ---------------------------------------------------------------- linking drivers to PCs and printers
r = c.post("/files/add", data={"csrf": tok, "name": "HP printer driver", "run_mode": "once",
                               "targets": ["pc:PC-HQ-001"],
                               "payload": (zip_bytes(["hp.inf", "hp.cat"]), "hp-driver.zip")},
           content_type="multipart/form-data", follow_redirects=True)
ok("uploaded" in r.text, "a driver can be uploaded for one PC")
drv = [d for d in store.load()["drivers"] if d["name"] == "HP printer driver"][0]
ok(drv["targets"] == ["pc:PC-HQ-001"], "the driver is linked to that PC")
plan_l = json.load(open(os.path.join(DATA, "deploy_plan.json")))
ok(drv["id"] in plan_l["hosts"]["PC-HQ-001"]["drivers"], "that PC gets it")
ok(drv["id"] not in plan_l["hosts"]["PC-BR1-009"]["drivers"], "other PCs do not")
ok("individual PCs" in c.get("/files").text, "the page offers per-PC linking")
# and a printer can use that driver
r = c.post("/printers/add", data={"csrf": tok, "name": "Linked printer", "host": "10.0.0.12",
                                  "driver": "HP Universal", "driver_ref": drv["id"],
                                  "targets": ["pc:PC-HQ-001"]}, follow_redirects=True)
ok("Linked printer" in r.text, "a printer can be linked to an uploaded driver")
plan_l = json.load(open(os.path.join(DATA, "deploy_plan.json")))
linked = [p for p in plan_l["printers"] if p["name"] == "Linked printer"][0]
ok(linked["driver_url"].endswith(f"/drivers/{drv['file']}"), "the PCs fetch the linked driver")
ok("linked to the" in linked["driver_source"], "and the plan records where it came from")
ok(linked["id"] in plan_l["hosts"]["PC-HQ-001"]["printers"], "the printer goes to that PC only")
ok(linked["id"] not in plan_l["hosts"]["PC-BR1-009"]["printers"], "not to the others")
ok("linked" in c.get("/printers").text, "the page shows it as linked")

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
ok(f"v{__version__}" in login_page, "the sign-in page shows the version")
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
    ok(users.must_change_password(name), f"the new {role} must choose their own password")
    # so the rest of the suite can use these accounts as themselves
    users.set_password(name, pw, force_change=False)
ok(users.MIN_PASSWORD == 8, "the password minimum is eight characters")
ok(f"at least {users.MIN_PASSWORD} characters" in
   c.post("/users/add", data={"csrf": tok, "username": "shorty", "password": "abc",
                              "role": "viewer"}, follow_redirects=True).text,
   "a short password is refused")
ok("Added eight" in c.post("/users/add", data={"csrf": tok, "username": "eight",
                                               "password": "12345678", "role": "viewer"},
                           follow_redirects=True).text, "eight characters is accepted")
c.post("/users/eight/delete", data={"csrf": tok}, follow_redirects=True)
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
    ok(rc.get("/release-notes").status_code == 200, f"{role} can read the release notes")
    ok("what&#39;s new" in rc.get("/").text.lower() or "what's new" in rc.get("/").text.lower(),
       f"{role} can reach the release notes from the sidebar")
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
# a contents list whose links all land somewhere
import re as _re3
_ids = set(_re3.findall(r'id="([^"]+)"', r.text))
_links = set(_re3.findall(r'href="#([^"]+)"', r.text))
ok("Contents" in r.text, "the help page has a table of contents")
ok(_links and not (_links - _ids), "every contents link points at a section that exists")
for _anchor in ("where", "hyperv", "ubuntu", "wsl", "prepare", "backup", "problems"):
    ok(_anchor in _ids, f"the help page has a '{_anchor}' section")
# and says plainly what WSL is and is not for
ok("not on WSL" in r.text, "help recommends against running on WSL")
ok("does not start with Windows" in r.text, "and says why")
ok("AutomaticStartAction Start" in r.text, "the Hyper-V VM is set to start by itself")
ok("MicrosoftUEFICertificateAuthority" in r.text, "and Secure Boot is dealt with")
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
ok("dismiss" in c.get("/").text, "the upgrade notice can be dismissed")
r = c.post("/dismiss/upgrade", data={"csrf": tok}, follow_redirects=True)
ok("upgraded from" not in r.text, "dismissing the upgrade notice hides it")
ok(jobs.kv_get("acknowledged_version") == __version__, "and marks the version as read")
jobs.kv_set("acknowledged_version", "1.0.0")
r = c.get("/release-notes")
ok("new to you" in r.text, "releases new since the upgrade are marked")
ok(jobs.kv_get("acknowledged_version") == __version__, "opening the page acknowledges the version")
ok("upgraded from" not in c.get("/").text, "the notice is gone once read")

# ---------------------------------------------------------------- odds and ends
r = c.get("/coffee")
ok(r.status_code == 418, "/coffee knows what it is")
ok("teapot" in r.text.lower(), "and says so")
r = c.get("/pcs?q=xyzzy", follow_redirects=True)
ok("Nothing happens" in r.text, "searching for xyzzy does nothing, politely")
ok(r.status_code == 200 and "Find a PC" in r.text, "and the page still works")
notes_text = open(os.path.join(os.path.dirname(__file__), "..",
                               "ansiweb/defaults/release_notes.yml")).read().lower()
for word in ("coffee", "xyzzy", "konami"):
    ok(word in notes_text, f"the release notes now mention {word}")

# ---------------------------------------------------------------- every page renders
for url in ["/", "/apps", "/apps/new", "/apps/7zip/edit", "/apps/vendor-app/edit", "/pcs",
            "/pcs/PC-HQ-001/edit", "/files", "/drivers/intel-nic/edit",
            "/scripts/set-power-plan/edit", "/registry/disable-autostart/edit", "/reports", "/jobs",
            "/settings", "/release-notes", "/help", "/users", "/inventory"]:
    ok(c.get(url).status_code == 200, f"page renders: {url}")
ok(c.get("/office").status_code == 404, "the removed Office page is gone")
ok(c.get("/directory").status_code == 404, "there is no Active Directory page")
ok(c.get("/nonsense").status_code == 404, "unknown resource kind is 404")
# the simple .cmd method
r = c.get("/prepare-script.cmd")
ok(r.status_code == 200, "the .cmd prep script downloads")
body = r.data.decode()
ok("192.168.1.10" in body and 'set "ACCOUNT=Admin"' in body, "it is filled in with the server and account")
ok("__CONTROL_NODE_IP__" not in body, "no placeholder is left")
ok("\r\n" in body, "it uses Windows line endings")
ok("pause" in body, "the window stays open at the end")
commands = [l for l in body.splitlines() if l.strip() and not l.strip().startswith("rem")]
ok("net user" in body and "netsh advfirewall" in body and "winrm quickconfig" in body,
   "it uses built-in commands")
ok(not any("powershell" in l.lower() for l in commands),
   "and never calls PowerShell, which is the point of this method")
# the connection mode has to match the method used
r = c.post("/pcs/connection", data={"csrf": tok, "pc_connection": "ntlm"}, follow_redirects=True)
ok("port 5985 using NTLM" in r.text, "switching to NTLM says what it means")
conn = open(os.path.join(DATA, "inventory/group_vars/windows/connection.yml")).read()
ok("ansible_port: 5985" in conn and "message_encryption: always" in conn,
   "simple mode connects on 5985 with message encryption")
ok("ansible_winrm_scheme: http\n" in conn, "over plain HTTP, encrypted by NTLM")
r = c.post("/pcs/connection", data={"csrf": tok, "pc_connection": "https"}, follow_redirects=True)
conn = open(os.path.join(DATA, "inventory/group_vars/windows/connection.yml")).read()
ok("ansible_port: 5986" in conn and "scheme: https" in conn, "and back to HTTPS on 5986")
ok("must be HTTPS or NTLM" in c.post("/pcs/connection", data={"csrf": tok,
                                                              "pc_connection": "carrier-pigeon"},
                                     follow_redirects=True).text, "an unknown mode is refused")
body = c.get("/pcs").text
ok("Currently reaching PCs on" in body, "the PCs page states which port it will use")
ok("5986 over HTTPS" in body, "and names the current one")
# a failed connection test explains the likeliest cause
hint = jobs._ping_hint(store.load())
ok("port 5986" in hint and "simple (.cmd)" in hint,
   "the hint names the configured port and the other script")
hint_ntlm = jobs._ping_hint({"settings": {"pc_connection": "ntlm"}})
ok("port 5985" in hint_ntlm and "PowerShell (.ps1)" in hint_ntlm, "and the other way round")

r = c.get("/prepare-script")
ok(b"$ControlNodeIP = '192.168.1.10'" in r.data and b"$AccountName = 'Admin'" in r.data,
   "prep script is filled in with the server IP and account")

print(f"ALL {checks} CHECKS PASSED")
