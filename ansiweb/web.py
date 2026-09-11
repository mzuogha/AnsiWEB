"""AnsiWEB web interface."""
import copy
import csv
import functools
import io
import re
import secrets as pysecrets

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash

from . import __version__, cache, jobs, paths, plan, store, vault

DAY_LABELS = [("mon", "Mon"), ("tue", "Tue"), ("wed", "Wed"), ("thu", "Thu"),
              ("fri", "Fri"), ("sat", "Sat"), ("sun", "Sun")]


def create_app(start_background: bool = True) -> Flask:
    paths.ensure_dirs()
    vault.ensure_vault_pass()
    vault.ensure_ansible_vault()
    jobs.init_db()
    store.regenerate_all()

    app = Flask(__name__)
    app.secret_key = vault.flask_secret()
    app.config.update(
        MAX_CONTENT_LENGTH=6 * 1024 ** 3,      # large installers / Office setup
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=8 * 3600,
    )

    # ---- security helpers ----------------------------------------------------
    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = pysecrets.token_urlsafe(32)
        return session["csrf"]

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "version": __version__, "running_jobs": jobs.running(),
                "job_kinds": jobs.KINDS}

    @app.before_request
    def protect():
        if request.endpoint in ("static", "login"):
            if request.method == "POST" and request.endpoint == "login":
                _check_csrf()
            return None
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        if request.method == "POST":
            _check_csrf()
        return None

    def _check_csrf():
        token = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
        if not token or not pysecrets.compare_digest(token, session.get("csrf", "")):
            abort(400, "Invalid or missing form token. Reload the page and try again.")

    @app.after_request
    def headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'"
        return resp

    def save_or_flash(cfg) -> bool:
        try:
            store.save(cfg)
            return True
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return False

    # ---- auth ------------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        admin = vault.admin_record()
        if request.method == "POST":
            if (admin and request.form.get("username") == admin["username"]
                    and check_password_hash(admin["password_hash"], request.form.get("password", ""))):
                session.clear()
                session.permanent = True
                session["user"] = admin["username"]
                nxt = request.args.get("next", "/")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
            flash("Wrong username or password.", "error")
        return render_template("login.html", no_admin=not admin)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---- dashboard -------------------------------------------------------------
    @app.route("/")
    def dashboard():
        cfg = store.load()
        manifest = cache.load_manifest()
        p = store.read_json(paths.PLAN_FILE, {})
        reports = load_reports()
        app_rows = app_status_rows(cfg, manifest)
        pcs_needing = sum(1 for r in reports.values() if any(a.get("needed") for a in r.get("apps", [])))
        stats = {
            "pcs": len(cfg.get("pcs", [])),
            "apps": len([a for a in cfg["apps"] if a.get("enabled", True)]),
            "cached": sum(1 for r in app_rows if r["state"] in ("ok", "pinned")),
            "problems": sum(1 for r in app_rows if r["state"] in ("error", "stale", "missing")),
            "pcs_reported": len(reports),
            "pcs_needing": pcs_needing,
        }
        warnings = setup_warnings(cfg)
        return render_template("dashboard.html", cfg=cfg, stats=stats, apps=app_rows, plan=p,
                               recent=jobs.list_jobs(8), warnings=warnings,
                               last_cache=jobs.last_job("cache_update"), last_deploy=jobs.last_job("deploy"),
                               office=manifest.get(cache.OFFICE_KEY, {}))

    # ---- apps --------------------------------------------------------------------
    @app.route("/apps")
    def apps_page():
        cfg = store.load()
        return render_template("apps.html", apps=app_status_rows(cfg, cache.load_manifest()), cfg=cfg)

    def app_from_form(existing: dict | None) -> dict:
        f = request.form
        app_ = copy.deepcopy(existing) if existing else {}
        app_.update({
            "id": (existing or {}).get("id") or f.get("id", "").strip().lower(),
            "name": f.get("name", "").strip() or f.get("id", ""),
            "enabled": f.get("enabled") == "on",
            "source": f.get("source", "winget"),
            "arguments": f.get("arguments", "").strip(),
            "detect_pattern": f.get("detect_pattern", "").strip(),
            "pinned": f.get("pinned") == "on",
            "targets": request.form.getlist("targets") or ["all"],
        })
        if app_["source"] == "winget":
            app_["winget_id"] = f.get("winget_id", "").strip()
            app_["installer_types"] = [t.strip().lower() for t in f.get("installer_types", "").split(",") if t.strip()]
            app_["version"] = f.get("version", "").strip() if app_["pinned"] else ""
        elif app_["source"] == "url":
            app_["url"] = f.get("url", "").strip()
            app_["version"] = f.get("version", "").strip()
            app_["sha256"] = f.get("sha256", "").strip().upper()
        else:
            app_["version"] = f.get("version", "").strip()
        app_["firefox_disable_updates"] = f.get("firefox_disable_updates") == "on"
        codes = [c for c in re.split(r"[,\s]+", f.get("extra_success_codes", "")) if c]
        try:
            app_["extra_success_codes"] = [int(c) for c in codes]
        except ValueError:
            raise store.ValidationError("Extra success codes must be numbers, e.g. 1638, 1641")
        return app_

    @app.route("/apps/new", methods=["GET", "POST"])
    def app_new():
        cfg = store.load()
        blank = {"id": "", "name": "", "enabled": True, "source": "winget", "installer_types": ["msi", "wix", "exe"],
                 "arguments": "", "detect_pattern": "", "pinned": False, "targets": ["all"]}
        if request.method == "POST":
            try:
                new = app_from_form(None)
            except store.ValidationError as exc:
                flash(str(exc), "error")
                return render_template("app_form.html", app=request.form, cfg=cfg, is_new=True,
                                       targets=store.target_choices(cfg))
            cfg["apps"].append(new)
            if save_or_flash(cfg):
                flash(f"Added {new['name']}. Run 'Check for updates' to download it into the cache.", "ok")
                return redirect(url_for("apps_page"))
            return render_template("app_form.html", app=new, cfg=cfg, is_new=True, targets=store.target_choices(cfg))
        return render_template("app_form.html", app=blank, cfg=cfg, is_new=True, targets=store.target_choices(cfg))

    def find_app(cfg, app_id):
        for i, a in enumerate(cfg["apps"]):
            if a["id"] == app_id:
                return i, a
        abort(404)

    @app.route("/apps/<app_id>/edit", methods=["GET", "POST"])
    def app_edit(app_id):
        cfg = store.load()
        idx, existing = find_app(cfg, app_id)
        if request.method == "POST":
            try:
                cfg["apps"][idx] = app_from_form(existing)
            except store.ValidationError as exc:
                flash(str(exc), "error")
                return redirect(url_for("app_edit", app_id=app_id))
            if save_or_flash(cfg):
                flash("Saved.", "ok")
                return redirect(url_for("apps_page"))
        return render_template("app_form.html", app=existing, cfg=cfg, is_new=False,
                               targets=store.target_choices(cfg),
                               entry=cache.load_manifest().get(app_id, {}))

    @app.route("/apps/<app_id>/delete", methods=["POST"])
    def app_delete(app_id):
        cfg = store.load()
        idx, existing = find_app(cfg, app_id)
        del cfg["apps"][idx]
        if save_or_flash(cfg):
            flash(f"Removed {existing['name']} from the standard apps. Its cached files are deleted at the next "
                  "update check. It is not uninstalled from PCs.", "ok")
        return redirect(url_for("apps_page"))

    @app.route("/apps/<app_id>/upload", methods=["POST"])
    def app_upload(app_id):
        cfg = store.load()
        _, existing = find_app(cfg, app_id)
        f = request.files.get("installer")
        version = request.form.get("version", "").strip()
        if not f or not f.filename or not version:
            flash("Choose an installer file and enter its version.", "error")
        elif not f.filename.lower().endswith((".msi", ".exe")):
            flash("Only .msi and .exe installers are supported.", "error")
        else:
            cache.store_upload(existing, f, version)
            if existing.get("source") == "upload":
                existing["version"] = version
                store.save(cfg)
            else:
                store.regenerate_all(cfg)
            flash(f"Uploaded {f.filename} as {existing['name']} {version}.", "ok")
        return redirect(url_for("app_edit", app_id=app_id))

    @app.route("/apps/<app_id>/refresh", methods=["POST"])
    def app_refresh(app_id):
        cfg = store.load()
        _, existing = find_app(cfg, app_id)
        status = cache.update_app(existing, vault.app_secret("github_token"), log=lambda *_: None, force=True)
        store.regenerate_all(cfg)
        entry = cache.load_manifest().get(app_id, {})
        if status == "error":
            flash(f"{existing['name']}: {entry.get('error')}", "error")
        else:
            flash(f"{existing['name']}: cached version {entry.get('version')}.", "ok")
        return redirect(url_for("apps_page"))

    # ---- PCs -----------------------------------------------------------------------
    @app.route("/pcs")
    def pcs_page():
        cfg = store.load()
        reports = load_reports()
        p = store.read_json(paths.PLAN_FILE, {})
        return render_template("pcs.html", cfg=cfg, reports=reports, plan=p,
                               app_names={a["id"]: a["name"] for a in cfg["apps"]})

    def pc_from_form():
        f = request.form
        return {"name": f.get("name", "").strip(), "ip": f.get("ip", "").strip(), "site": f.get("site", ""),
                "groups": [g.strip() for g in f.get("groups", "").split(",") if g.strip()],
                "notes": f.get("notes", "").strip()}

    @app.route("/pcs/add", methods=["POST"])
    def pc_add():
        cfg = store.load()
        cfg["pcs"].append(pc_from_form())
        if save_or_flash(cfg):
            flash("PC added.", "ok")
        return redirect(url_for("pcs_page"))

    @app.route("/pcs/<name>/edit", methods=["GET", "POST"])
    def pc_edit(name):
        cfg = store.load()
        idx = next((i for i, pc in enumerate(cfg["pcs"]) if pc["name"] == name), None)
        if idx is None:
            abort(404)
        if request.method == "POST":
            new = pc_from_form()
            old_name = cfg["pcs"][idx]["name"]
            cfg["pcs"][idx] = new
            if cfg["office"].get("helper_pc") == old_name:
                cfg["office"]["helper_pc"] = new["name"]
            if save_or_flash(cfg):
                flash("Saved.", "ok")
                return redirect(url_for("pcs_page"))
        return render_template("pc_form.html", pc=cfg["pcs"][idx], cfg=cfg,
                               report=load_reports().get(name))

    @app.route("/pcs/<name>/delete", methods=["POST"])
    def pc_delete(name):
        cfg = store.load()
        cfg["pcs"] = [pc for pc in cfg["pcs"] if pc["name"] != name]
        if cfg["office"].get("helper_pc") == name:
            cfg["office"]["helper_pc"] = ""
        if save_or_flash(cfg):
            (paths.REPORT_DIR / f"{name}.json").unlink(missing_ok=True)
            flash(f"Removed {name}.", "ok")
        return redirect(url_for("pcs_page"))

    @app.route("/pcs/import", methods=["POST"])
    def pc_import():
        cfg = store.load()
        text = request.form.get("csv", "")
        added = 0
        existing = {pc["name"].lower(): pc for pc in cfg["pcs"]}
        for row in csv.reader(io.StringIO(text)):
            row = [c.strip() for c in row]
            if not row or not row[0] or row[0].lower() in ("name", "hostname", "#"):
                continue
            if len(row) < 3:
                flash(f"Skipped line '{','.join(row)}': need name,ip,site", "error")
                continue
            if row[2] not in cfg["sites"]:
                cfg["sites"].append(row[2])
            pc = {"name": row[0], "ip": row[1], "site": row[2],
                  "groups": [g for g in (row[3].split(";") if len(row) > 3 else []) if g], "notes": ""}
            if pc["name"].lower() in existing:
                existing[pc["name"].lower()].update(pc)
            else:
                cfg["pcs"].append(pc)
            added += 1
        if save_or_flash(cfg):
            flash(f"Imported {added} PC(s).", "ok")
        return redirect(url_for("pcs_page"))

    @app.route("/sites", methods=["POST"])
    def sites():
        cfg = store.load()
        name = request.form.get("site", "").strip()
        action = request.form.get("action")
        if action == "add" and name and name not in cfg["sites"]:
            cfg["sites"].append(name)
        elif action == "delete":
            if any(pc["site"] == name for pc in cfg["pcs"]):
                flash(f"Site '{name}' still has PCs. Move or remove them first.", "error")
                return redirect(url_for("pcs_page"))
            cfg["sites"] = [s for s in cfg["sites"] if s != name]
        save_or_flash(cfg)
        return redirect(url_for("pcs_page"))

    @app.route("/prepare-script")
    def prepare_script():
        cfg = store.load()
        text = (paths.SCRIPTS_DIR / "Prepare-AnsibleHost.ps1").read_text(encoding="utf-8")
        ip = cfg["settings"].get("server_ip") or ""
        text = text.replace("__CONTROL_NODE_IP__", ip)
        return Response(text.encode("utf-8-sig"), mimetype="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename=Prepare-AnsibleHost.ps1"})

    # ---- Office ------------------------------------------------------------------------
    @app.route("/office", methods=["GET", "POST"])
    def office_page():
        cfg = store.load()
        if request.method == "POST":
            f = request.form
            cfg["office"].update({
                "enabled": f.get("enabled") == "on",
                "product_id": f.get("product_id", "Standard2024Volume").strip(),
                "channel": f.get("channel", "PerpetualVL2024").strip(),
                "language": f.get("language", "en-us").strip(),
                "exclude_apps": [x.strip() for x in f.get("exclude_apps", "").split(",") if x.strip()],
                "helper_pc": f.get("helper_pc", ""),
                "targets": request.form.getlist("targets") or ["all"],
            })
            if save_or_flash(cfg):
                flash("Office settings saved.", "ok")
            return redirect(url_for("office_page"))
        return render_template("office.html", cfg=cfg, office=cfg["office"],
                               entry=cache.load_manifest().get(cache.OFFICE_KEY, {}),
                               setup_present=cache.office_setup_present(), targets=store.target_choices(cfg),
                               mak_set=vault.secret_status()["vault_office_mak_key"],
                               plan=store.read_json(paths.PLAN_FILE, {}).get("office", {}))

    @app.route("/office/setup", methods=["POST"])
    def office_setup():
        f = request.files.get("setup")
        if not f or f.filename.lower() != "setup.exe":
            flash("Upload the setup.exe extracted from the Office Deployment Tool.", "error")
        else:
            cache.store_office_setup(f)
            store.regenerate_all()
            flash("Office Deployment Tool setup.exe uploaded.", "ok")
        return redirect(url_for("office_page"))

    # ---- jobs ----------------------------------------------------------------------------
    @app.route("/jobs")
    def jobs_page():
        return render_template("jobs.html", jobs=jobs.list_jobs(100))

    @app.route("/jobs/start", methods=["POST"])
    def job_start():
        kind = request.form.get("kind", "")
        target = request.form.get("target", "")
        if kind in ("deploy", "ping"):
            try:
                store.limit_for(target or "all")
            except store.ValidationError as exc:
                flash(str(exc), "error")
                return redirect(request.referrer or url_for("dashboard"))
        try:
            job_id = jobs.start(kind, target, trigger=f"manual ({session.get('user')})")
        except (jobs.JobBusy, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(request.referrer or url_for("dashboard"))
        return redirect(url_for("job_view", job_id=job_id))

    @app.route("/jobs/<int:job_id>")
    def job_view(job_id):
        job = jobs.get_job(job_id) or abort(404)
        return render_template("job.html", job=job)

    @app.route("/jobs/<int:job_id>/log")
    def job_log(job_id):
        job = jobs.get_job(job_id) or abort(404)
        text, offset = jobs.read_log(job_id, int(request.args.get("offset", 0)))
        return jsonify({"text": text, "offset": offset, "status": job["status"]})

    # ---- settings ------------------------------------------------------------------------
    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        cfg = store.load()
        if request.method == "POST":
            f = request.form
            try:
                cfg["settings"].update({
                    "server_ip": f.get("server_ip", "").strip(),
                    "allow_reboot": f.get("allow_reboot") == "on",
                    "forks": max(1, min(100, int(f.get("forks", 20)))),
                    "batch_size": max(1, min(500, int(f.get("batch_size", 20)))),
                })
                cfg["schedules"]["cache_check"] = {"enabled": f.get("cc_enabled") == "on",
                                                   "every_hours": max(1, int(f.get("cc_hours", 24)))}
                cfg["schedules"]["deploy"] = {"enabled": f.get("dep_enabled") == "on",
                                              "time": f.get("dep_time", "19:00"),
                                              "days": request.form.getlist("dep_days")}
                cfg["schedules"]["office_cache"] = {"enabled": f.get("oc_enabled") == "on",
                                                    "day": f.get("oc_day", "sun"),
                                                    "time": f.get("oc_time", "02:00")}
            except ValueError:
                flash("Numbers expected for forks, batch size and hours.", "error")
                return redirect(url_for("settings_page"))
            if save_or_flash(cfg):
                flash("Settings saved.", "ok")
            return redirect(url_for("settings_page"))
        return render_template("settings.html", cfg=cfg, days=DAY_LABELS, secrets=vault.secret_status(),
                               secret_names=vault.ANSIBLE_SECRET_NAMES)

    @app.route("/settings/secret", methods=["POST"])
    def settings_secret():
        name = request.form.get("name", "")
        value = request.form.get("value", "")
        if name == "github_token":
            vault.set_app_secret("github_token", value.strip())
        elif name in vault.ANSIBLE_SECRET_NAMES:
            vault.set_ansible_secret(name, value)
        else:
            abort(400)
        flash("Secret updated." if value else "Secret cleared.", "ok")
        return redirect(request.referrer or url_for("settings_page"))

    @app.route("/settings/password", methods=["POST"])
    def settings_password():
        from werkzeug.security import generate_password_hash
        admin = vault.admin_record()
        if not check_password_hash(admin["password_hash"], request.form.get("current", "")):
            flash("Current password is wrong.", "error")
        elif len(request.form.get("new", "")) < 10:
            flash("New password must be at least 10 characters.", "error")
        elif request.form.get("new") != request.form.get("confirm"):
            flash("New passwords do not match.", "error")
        else:
            vault.set_admin(admin["username"], generate_password_hash(request.form["new"]))
            flash("Password changed.", "ok")
        return redirect(url_for("settings_page"))

    if start_background:
        jobs.start_scheduler()
    return app


# ---- view helpers -------------------------------------------------------------------------
def load_reports() -> dict:
    out = {}
    for p in sorted(paths.REPORT_DIR.glob("*.json")):
        data = store.read_json(p, {})
        if data:
            out[p.stem] = data
    return out


def app_status_rows(cfg: dict, manifest: dict) -> list:
    rows = []
    for a in cfg.get("apps", []):
        e = manifest.get(a["id"], {})
        cached = bool(e.get("file")) and (paths.APPS_DIR / e["file"]).exists()
        if not a.get("enabled", True):
            state = "disabled"
        elif e.get("status") == "error" or (not cached and e.get("error")):
            state = "error"
        elif e.get("status") == "stale":
            state = "stale"
        elif not cached:
            state = "missing"
        elif a.get("pinned"):
            state = "pinned"
        else:
            state = "ok"
        rows.append({"app": a, "entry": e, "state": state, "cached": cached})
    return rows


def setup_warnings(cfg: dict) -> list:
    w = []
    s = vault.secret_status()
    if not cfg["settings"].get("server_ip"):
        w.append(("Set this server's IP address in Settings, so PCs know where to download apps from.", "settings_page"))
    if not s["vault_ansible_svc_password"]:
        w.append(("Enter the ansible_svc password (the one used in the PC prep script) in Settings.", "settings_page"))
    if not cfg.get("pcs"):
        w.append(("Add your PCs on the PCs page.", "pcs_page"))
    if cfg["office"].get("enabled"):
        if not cache.office_setup_present():
            w.append(("Office: upload the Office Deployment Tool setup.exe.", "office_page"))
        if not cfg["office"].get("helper_pc"):
            w.append(("Office: choose a helper PC to download Office builds.", "office_page"))
    return w
