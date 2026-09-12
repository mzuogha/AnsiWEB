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
from ansiweb import backup, cache, jobs, payloads, store, vault, web   # noqa: E402

PW = "correct-horse-1"
vault.set_admin("admin", generate_password_hash(PW))
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
ok("Wrong username" in c.post("/login", data={"username": "admin", "password": "no", "csrf": tok}).text,
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
import glob as _glob
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
r = c.post("/drivers/add", data={"csrf": tok, "name": "Intel NIC", "run_mode": "once", "targets": ["all"],
                                 "payload": (zip_bytes(["net.inf", "net.cat"]), "intel-nic.zip")},
           content_type="multipart/form-data", follow_redirects=True)
ok("uploaded" in r.text and "Intel NIC" in r.text, "driver upload")
r = c.post("/scripts/add", data={"csrf": tok, "name": "Set power plan", "run_mode": "always",
                                 "arguments": "-Plan High", "success_codes": "3010", "timeout": "600",
                                 "reboot": "on", "targets": ["site:HQ"],
                                 "payload": (io.BytesIO(b"Write-Host hi\n"), "power.ps1")},
           content_type="multipart/form-data", follow_redirects=True)
ok("Set power plan" in r.text, "script upload")
r = c.post("/registry/add", data={"csrf": tok, "name": "Disable autostart", "run_mode": "changed",
                                  "targets": ["all"],
                                  "payload": (io.BytesIO(b"Windows Registry Editor Version 5.00\n"), "no-auto.reg")},
           content_type="multipart/form-data", follow_redirects=True)
ok("Disable autostart" in r.text, "registry upload")
ok("must be a .zip" in c.post("/drivers/add", data={"csrf": tok, "name": "Bad", "run_mode": "once",
                                                    "payload": (io.BytesIO(b"x"), "driver.exe")},
                              content_type="multipart/form-data", follow_redirects=True).text,
   "wrong driver file type refused")
ok("Choose a file" in c.post("/scripts/add", data={"csrf": tok, "name": "Nofile", "run_mode": "once"},
                             content_type="multipart/form-data", follow_redirects=True).text,
   "script without a file refused")

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

# ---------------------------------------------------------------- every page renders
for url in ["/", "/apps", "/apps/new", "/apps/7zip/edit", "/apps/vendor-app/edit", "/pcs",
            "/pcs/PC-HQ-001/edit", "/drivers", "/scripts", "/registry", "/drivers/intel-nic/edit",
            "/scripts/set-power-plan/edit", "/registry/disable-autostart/edit", "/reports", "/jobs",
            "/settings"]:
    ok(c.get(url).status_code == 200, f"page renders: {url}")
ok(c.get("/office").status_code == 404, "the removed Office page is gone")
ok(c.get("/directory").status_code == 404, "there is no Active Directory page")
ok(c.get("/nonsense").status_code == 404, "unknown resource kind is 404")
r = c.get("/prepare-script")
ok(b"$ControlNodeIP = '192.168.1.10'" in r.data and b"$AccountName = 'Admin'" in r.data,
   "prep script is filled in with the server IP and account")

print(f"ALL {checks} CHECKS PASSED")
