"""Offline smoke test of the web interface: python3 tests/smoke_test.py"""
import io
import json
import os
import re
import sys
import tempfile
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

# ---------------------------------------------------------------- reports
os.makedirs(os.path.join(DATA, "reports"), exist_ok=True)
json.dump({"host": "PC-HQ-001", "time": "2026-09-12 09:00:00", "reboot_pending": True,
           "facts": {"hostname": "PC-HQ-001", "os": "Windows 11 Pro", "build": "26100",
                     "model": "Dell OptiPlex", "ram_gb": 16, "serial": "ABC123", "boot": "2026-09-12 07:00:00"},
           "apps": [{"id": "7zip", "name": "7-Zip", "installed": "22.01", "target": "26.03",
                     "needed": True, "mismatch": False, "reason": "update 22.01 -> 26.03"},
                    {"id": "vlc", "name": "VLC", "installed": "3.0.23", "target": "3.0.23",
                     "needed": False, "mismatch": False, "reason": "up to date"}],
           "results": [{"kind": "drivers", "id": "intel-nic", "name": "Intel NIC", "status": "installed",
                      "detail": "1 of 1 driver file(s) added"},
                     {"kind": "scripts", "id": "set-power-plan", "name": "Set power plan",
                      "status": "ran (exit 0)", "detail": "done"}]},
          open(os.path.join(DATA, "reports/PC-HQ-001.json"), "w"))
r = c.get("/reports")
ok("PC-HQ-001" in r.text and "Windows 11 Pro" in r.text, "report page shows the PC")
ok("reboot pending" in r.text, "pending reboot is visible")
ok("Intel NIC" in r.text, "driver result is visible")
csv_text = c.get("/reports/export.csv").text
ok("PC-HQ-001" in csv_text and "7-Zip" in csv_text and "Dell OptiPlex" in csv_text, "CSV export")
ok(csv_text.count("\n") > 3, "CSV has a row per item")
ok("Dell OptiPlex" in c.get("/pcs/PC-HQ-001/edit").text, "PC page shows the report")

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
