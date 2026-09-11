"""Offline smoke test of the web interface: python3 tests/smoke_test.py"""
import re, io, time, json
from werkzeug.security import generate_password_hash
import os, sys, tempfile
os.environ["ANSIWEB_DATA"] = tempfile.mkdtemp(prefix="ansiweb-test-")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ansiweb import vault, web, store, jobs
vault.set_admin("admin", generate_password_hash("correct-horse-1"))
app = web.create_app(start_background=False)
c = app.test_client()

def csrf(html):
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)

r = c.get("/"); assert r.status_code == 302, r.status_code
r = c.get("/login"); tok = csrf(r.text)
r = c.post("/login", data={"username":"admin","password":"wrong","csrf":tok}); assert "Wrong username" in r.text
r = c.post("/login", data={"username":"admin","password":"correct-horse-1","csrf":tok}, follow_redirects=True)
assert "Dashboard" in r.text, r.text[:500]
tok = csrf(r.text)
# CSRF enforcement
assert c.post("/settings", data={}).status_code == 400
# settings
r = c.post("/settings", data={"csrf":tok,"server_ip":"192.168.1.10","forks":"20","batch_size":"20","cc_enabled":"on","cc_hours":"24",
     "dep_time":"19:00","dep_days":["mon","fri"],"oc_day":"sun","oc_time":"02:00"}, follow_redirects=True)
assert "Settings saved" in r.text, r.text[:2000]
r = c.post("/settings", data={"csrf":tok,"server_ip":"not-an-ip","forks":"20","batch_size":"20","cc_hours":"24","dep_time":"19:00","oc_day":"sun","oc_time":"02:00"}, follow_redirects=True)
assert "must be an IP" in r.text
# secrets
r = c.post("/settings/secret", data={"csrf":tok,"name":"vault_ansible_svc_password","value":"S3cret!pw"}, follow_redirects=True)
assert "Secret updated" in r.text and "S3cret" not in r.text
assert vault.ansible_secrets()["vault_ansible_svc_password"] == "S3cret!pw"
# sites + pcs
c.post("/sites", data={"csrf":tok,"action":"add","site":"Branch1"})
r = c.post("/pcs/add", data={"csrf":tok,"name":"PC-HQ-001","ip":"192.168.1.21","site":"HQ","groups":"finance"}, follow_redirects=True)
assert "PC added" in r.text
r = c.post("/pcs/add", data={"csrf":tok,"name":"PC-HQ-001","ip":"192.168.1.22","site":"HQ"}, follow_redirects=True)
assert "Duplicate PC" in r.text
r = c.post("/pcs/import", data={"csrf":tok,"csv":"name,ip,site\nPC-BR1-001,192.168.2.21,Branch1\nPC-BR2-001,192.168.3.21,Branch2,kiosk;finance\nbad-line\n"}, follow_redirects=True)
assert "Imported 2" in r.text, r.text[:3000]
# app add/edit
r = c.post("/apps/new", data={"csrf":tok,"id":"notepadpp","name":"Notepad++","enabled":"on","source":"winget","winget_id":"Notepad++.Notepad++",
     "installer_types":"exe","arguments":"/S","detect_pattern":r"^Notepad\+\+","targets":["group:finance"]}, follow_redirects=True)
assert "Added Notepad++" in r.text, r.text[:3000]
r = c.post("/apps/new", data={"csrf":tok,"id":"bad","name":"Bad","source":"winget","winget_id":"x","detect_pattern":"([","targets":["all"]}, follow_redirects=True)
assert "not a valid regular expression" in r.text
r = c.get("/apps/firefox/edit"); assert "65.0.2" in r.text
# office settings
r = c.post("/office", data={"csrf":tok,"enabled":"on","product_id":"Standard2024Volume","channel":"PerpetualVL2024","language":"en-us",
    "exclude_apps":"Groove","helper_pc":"PC-HQ-001","targets":["all"]}, follow_redirects=True)
assert "Office settings saved" in r.text
# upload an app installer
r = c.post("/apps/java8/upload", data={"csrf":tok,"version":"8.0.5030.1","installer":(io.BytesIO(b"MZfake-installer"),"jre-8u503-windows-x64.exe")},
    content_type="multipart/form-data", follow_redirects=True)
assert "Uploaded jre-8u503" in r.text, r.text[:2000]
# every page renders
for url in ["/","/apps","/apps/new","/apps/7zip/edit","/pcs","/pcs/PC-HQ-001/edit","/office","/jobs","/settings"]:
    r = c.get(url); assert r.status_code == 200, (url, r.status_code, r.text[:500])
r = c.get("/prepare-script"); assert b"192.168.1.10" in r.data and r.data.startswith(b"\xef\xbb\xbf")
print(open(os.environ["ANSIWEB_DATA"] + "/inventory/hosts.yml").read())
plan = json.load(open(os.environ["ANSIWEB_DATA"] + "/deploy_plan.json"))
print("plan apps:", [a["id"] for a in plan["apps"]], "hosts:", plan["hosts"])
print("skipped:", [s["id"] for s in plan["skipped"]])
print("ALL WEB TESTS PASSED")
