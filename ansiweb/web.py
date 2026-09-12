"""AnsiWEB web interface."""
import copy
import csv
import io
import os
import re
import secrets as pysecrets
import time
from datetime import timedelta

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)

from . import (__version__, audit, backup, cache, jobs, paths, payloads, plan, release,
               report_state, store, users, vault)

# What each route needs. Anything not listed here requires an administrator,
# so a new route is never accidentally open to a lesser role.
ENDPOINT_PERMISSIONS = {
    # read-only
    "dashboard": users.VIEW, "apps_page": users.VIEW, "pcs_page": users.VIEW,
    "resources_page": users.VIEW, "resource_download": users.VIEW,
    "registry_redirect": users.VIEW,
    "reports_page": users.VIEW, "reports_export": users.VIEW,
    "jobs_page": users.VIEW, "job_view": users.VIEW, "job_log": users.VIEW,
    "job_download": users.VIEW, "release_notes": users.VIEW, "settings_page": users.VIEW,
    "help_page": users.VIEW, "uninstalls_page": users.VIEW, "logo": users.VIEW,
    "audit_page": users.VIEW,
    "inventory_page": users.VIEW, "inventory_export": users.VIEW,
    "prepare_script": users.VIEW, "logout": users.VIEW, "own_password": users.VIEW,
    "pc_edit": users.VIEW,            # the form itself; saving is checked below
    "app_edit": users.VIEW, "app_new": users.VIEW, "resource_edit": users.VIEW,
    # running things
    "job_start": users.RUN_JOBS, "resource_run": users.RUN_JOBS,
    # what gets deployed
    "app_save": users.MANAGE_CONTENT, "app_delete": users.MANAGE_CONTENT,
    "app_upload": users.MANAGE_CONTENT, "app_quick_upload": users.MANAGE_CONTENT,
    "app_refresh": users.MANAGE_CONTENT, "resource_add": users.MANAGE_CONTENT,
    "uninstall_add": users.MANAGE_CONTENT, "uninstall_delete": users.MANAGE_CONTENT,
    "uninstall_toggle": users.MANAGE_CONTENT, "uninstall_preview": users.MANAGE_CONTENT,
    "uninstall_run": users.MANAGE_CONTENT,
    "resource_save": users.MANAGE_CONTENT, "resource_delete": users.MANAGE_CONTENT,
    # the PC list
    "pc_add": users.MANAGE_PCS, "pc_save": users.MANAGE_PCS, "pc_delete": users.MANAGE_PCS,
    "pc_import": users.MANAGE_PCS, "sites": users.MANAGE_PCS, "report_delete": users.MANAGE_PCS,
}
# POSTs to these endpoints need more than the GET does
POST_PERMISSIONS = {
    "app_new": users.MANAGE_CONTENT, "app_edit": users.MANAGE_CONTENT,
    "resource_edit": users.MANAGE_CONTENT, "pc_edit": users.MANAGE_PCS,
}

DAY_LABELS = [("mon", "Mon"), ("tue", "Tue"), ("wed", "Wed"), ("thu", "Thu"),
              ("fri", "Fri"), ("sat", "Sat"), ("sun", "Sun")]


def create_app(start_background: bool = True) -> Flask:
    paths.ensure_dirs()
    users.migrate()
    vault.ensure_vault_pass()
    vault.ensure_ansible_vault()
    jobs.init_db()
    audit.init()
    store.regenerate_all()

    app = Flask(__name__)
    app.secret_key = vault.flask_secret()
    # Set by the service unit when nginx terminates TLS, so session cookies are
    # only sent over HTTPS. Set ANSIWEB_HTTPS=0 for a plain-HTTP installation.
    https = os.environ.get("ANSIWEB_HTTPS", "1") != "0"
    app.config.update(
        MAX_CONTENT_LENGTH=4 * 1024 ** 3,      # large installers and driver packages
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=https,
        # The cookie outlives the longest allowed idle timeout; the actual
        # timeout is enforced per request below, so changing it takes effect at once.
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=1441),
    )

    def branding() -> dict:
        s = store.load()["settings"]
        name = (s.get("logo_file") or "").strip()
        return {"logo": name if name and (paths.BRANDING_DIR / name).exists() else "",
                "site_name": (s.get("site_name") or "").strip()}

    def idle_timeout() -> int:
        """Seconds of inactivity before the web session is signed out."""
        try:
            return int(store.load()["settings"].get("session_timeout_minutes", 60)) * 60
        except (ValueError, TypeError, KeyError):
            return 3600

    # ---- security helpers ----------------------------------------------------
    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = pysecrets.token_urlsafe(32)
        return session["csrf"]

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "version": __version__, "running_jobs": jobs.running(),
                "job_kinds": jobs.KINDS, "kinds": payloads.KINDS,
                "idle_timeout": idle_timeout(),
                "branding": branding(),
                "can": lambda perm: users.can(session.get("role", ""), perm),
                "perms": {"view": users.VIEW, "run": users.RUN_JOBS,
                          "content": users.MANAGE_CONTENT, "pcs": users.MANAGE_PCS,
                          "admin": users.ADMIN},
                "role_label": users.ROLES.get(session.get("role", ""), {}).get("label", ""),
                "my_scope": my_scope(), "scope_label": users.scope_label(my_scope())}

    @app.before_request
    def protect():
        if request.endpoint in ("static", "logo"):
            return None

        # Sign out an idle session, whichever page is asked for
        timeout = idle_timeout()
        if session.get("user"):
            seen = session.get("seen")
            if seen and time.time() - seen > timeout:
                session.clear()
                flash(f"You were signed out after {timeout // 60} minutes without activity.", "ok")
                return redirect(url_for("login"))
            # Sliding window: activity keeps the session alive. The live job log
            # polls on its own, so it does not count - an unattended job page
            # still times out.
            if request.endpoint != "job_log":
                session["seen"] = time.time()
            session.permanent = True

        if request.endpoint == "login":
            if request.method == "POST":
                _check_csrf()
            return None
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))

        # The role may have been changed or the account disabled since sign-in
        record = users.get(session["user"])
        if not record or record.get("disabled"):
            session.clear()
            flash("Your account is no longer available. Ask an administrator.", "error")
            return redirect(url_for("login"))
        session["role"] = record["role"]

        if request.method == "POST":
            _check_csrf()
        needed = (POST_PERMISSIONS.get(request.endpoint) if request.method == "POST" else None) \
            or ENDPOINT_PERMISSIONS.get(request.endpoint, users.ADMIN)
        if not users.can(record["role"], needed):
            return render_template("denied.html", needed=needed,
                                   role=users.ROLES[record["role"]]), 403
        return None

    def _check_csrf():
        token = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
        if not token or not pysecrets.compare_digest(token, session.get("csrf", "")):
            abort(400, "Invalid or missing form token. Reload the page and try again.")

    # Record anything that changes state, plus backup downloads and sign-ins.
    AUDITED_GETS = {"settings_backup", "prepare_script"}

    @app.after_request
    def write_audit(resp):
        try:
            endpoint = request.endpoint or ""
            if endpoint in ("static", "job_log"):
                return resp
            audit_it = request.method in ("POST", "PUT", "DELETE") or endpoint in AUDITED_GETS
            if not audit_it:
                return resp
            outcome = "ok" if resp.status_code < 400 else f"refused ({resp.status_code})"
            if endpoint == "login" and resp.status_code < 400 and not session.get("user"):
                outcome = "refused (wrong credentials)"
            audit.record(session.get("user") or request.form.get("username") or "-",
                         session.get("role", ""), endpoint,
                         audit.summarise(request.form, request.files, request.view_args), outcome)
        except Exception:      # noqa: BLE001 - auditing must never break a response
            pass
        return resp

    @app.after_request
    def headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'"
        if https:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    def my_scope() -> list:
        return users.scope_of(users.get(session.get("user", "")))

    def visible_pcs(cfg: dict) -> list:
        """The PCs this user may see, in their configured order."""
        scope = my_scope()
        return [pc for pc in cfg.get("pcs", []) if users.pc_in_scope(pc, scope)]

    def pc_allowed(cfg: dict, name: str) -> bool:
        scope = my_scope()
        if not scope:
            return True
        for pc in cfg.get("pcs", []):
            if pc["name"] == name:
                return users.pc_in_scope(pc, scope)
        return False

    def scoped_target(cfg: dict, requested: str) -> str:
        """Narrow a requested job target to what this user is allowed to touch.

        Unscoped users get their request unchanged. For a scoped user the target
        is turned into an explicit list of PCs, so a job can never reach a PC
        outside their sites or groups - even if the form was tampered with.
        """
        try:
            return users.narrow_target(cfg.get("pcs", []), my_scope(), requested)
        except users.UserError as exc:
            raise store.ValidationError(str(exc))

    def save_or_flash(cfg) -> bool:
        try:
            store.save(cfg)
            return True
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return False

    def back(default="dashboard"):
        ref = request.referrer
        return redirect(ref) if ref else redirect(url_for(default))

    # ---- auth ------------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = users.authenticate(request.form.get("username", ""), request.form.get("password", ""))
            if user:
                session.clear()
                session.permanent = True
                session["user"] = user["username"]
                session["role"] = user["role"]
                session["seen"] = time.time()
                nxt = request.args.get("next", "/")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
            flash("Wrong user name or password, or the account is disabled.", "error")
        return render_template("login.html", no_admin=not users.any_users(), branding=branding())

    @app.route("/logo")
    def logo():
        """The uploaded logo, served without a sign-in so the login page can show it."""
        name = (store.load()["settings"].get("logo_file") or "").strip()
        if not name or "/" in name or "\\" in name:
            abort(404)
        path = paths.BRANDING_DIR / name
        if not path.exists():
            abort(404)
        return send_from_directory(paths.BRANDING_DIR, name, max_age=300)

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
        mine = visible_pcs(cfg)
        stale = report_state.summarise(mine, reports,
                                       cfg["settings"].get("stale_after_days", 14))
        stats = {
            "pcs": len(mine),
            "apps": len([a for a in cfg["apps"] if a.get("enabled", True)]),
            "cached": sum(1 for r in app_rows if r["state"] in ("ok", "pinned")),
            "problems": sum(1 for r in app_rows if r["state"] in ("error", "stale", "missing")),
            "pcs_reported": len(reports),
            "pcs_needing": sum(1 for r in reports.values()
                               if any(a.get("needed") for a in r.get("apps", []))),
            "stale": stale["needs_attention"],
            "payloads": sum(len([e for e in cfg.get(k, []) if e.get("enabled", True)])
                            for k in payloads.KINDS),
        }
        seen = jobs.kv_get("acknowledged_version")
        if not seen:                      # first run: nothing to announce
            jobs.kv_set("acknowledged_version", __version__)
        upgraded_from = seen if seen and seen != __version__ else ""
        return render_template("dashboard.html", cfg=cfg, stats=stats, apps=app_rows, plan=p,
                               recent=jobs.list_jobs(8), warnings=setup_warnings(cfg),
                               upgraded_from=upgraded_from,
                               last_cache=jobs.last_job("cache_update"),
                               last_deploy=jobs.last_job("deploy"),
                               stale=stale,
                               stale_days=cfg["settings"].get("stale_after_days", 14))

    # ---- apps --------------------------------------------------------------------
    @app.route("/apps")
    def apps_page():
        cfg = store.load()
        return render_template("apps.html", apps=app_status_rows(cfg, cache.load_manifest()), cfg=cfg,
                               targets=store.target_choices(cfg))

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
        blank = {"id": "", "name": "", "enabled": True, "source": "winget",
                 "installer_types": ["msi", "wix", "exe"], "arguments": "", "detect_pattern": "",
                 "pinned": False, "targets": ["all"]}
        if request.method == "POST":
            try:
                new = app_from_form(None)
                upload = request.files.get("installer")
                has_file = bool(upload and upload.filename)
                if new["source"] == "upload" and not has_file:
                    raise store.ValidationError("Choose the installer file to upload.")
                if has_file and not upload.filename.lower().endswith((".msi", ".exe")):
                    raise store.ValidationError("Only .msi and .exe installers can be uploaded.")
                cfg["apps"].append(new)
                store.save(cfg)
                # Save the file only once the entry itself is valid and stored
                if has_file:
                    cache.store_upload(new, upload, new["version"])
                    store.regenerate_all(cfg)
                    flash(f"Added {new['name']} and cached the uploaded installer.", "ok")
                else:
                    flash(f"Added {new['name']}. Run 'Check for updates' to download it into the cache.", "ok")
                return redirect(url_for("apps_page"))
            except store.ValidationError as exc:
                flash(str(exc), "error")
                form = dict(request.form)
                form["targets"] = request.form.getlist("targets")
                return render_template("app_form.html", app=form, cfg=cfg, is_new=True,
                                       targets=store.target_choices(cfg))
        return render_template("app_form.html", app=blank, cfg=cfg, is_new=True,
                              targets=store.target_choices(cfg))

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
        also_uninstall = request.form.get("uninstall") == "on"
        if save_or_flash(cfg):
            if also_uninstall and existing.get("detect_pattern"):
                cfg = store.load()
                cfg.setdefault("uninstalls", []).append({
                    "id": payloads.new_id(cfg, "uninstalls", existing["name"]),
                    "name": existing["name"],
                    "detect_pattern": existing["detect_pattern"],
                    "enabled": True,
                    "targets": existing.get("targets") or ["all"],
                    "notes": "added when the app was removed from the standard set",
                    "created": cache.now(),
                })
                store.save(cfg)
                flash(f"{existing['name']} is queued for removal from the PCs. "
                      "Preview it on the Uninstall page before removing anything.", "ok")
            freed = cache.forget_app(app_id)
            flash(f"Removed {existing['name']} from the standard apps"
                  + (f" and deleted {freed // 1048576} MB of cached installers" if freed else "")
                  + ". It stays installed on the PCs.", "ok")
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
        return back("apps_page")

    @app.route("/apps/upload", methods=["POST"])
    def app_quick_upload():
        """Add an offline app straight from the Apps page: file + name is enough."""
        cfg = store.load()
        f = request.files.get("installer")
        name = request.form.get("name", "").strip()
        version = request.form.get("version", "").strip() or "1.0"
        pattern = request.form.get("detect_pattern", "").strip()
        arguments = request.form.get("arguments", "").strip()
        try:
            if not f or not f.filename:
                raise store.ValidationError("Choose an installer file.")
            if not f.filename.lower().endswith((".msi", ".exe")):
                raise store.ValidationError("Only .msi and .exe installers can be uploaded.")
            if not name:
                raise store.ValidationError("Enter a name for the app.")
            if not pattern:
                raise store.ValidationError(
                    "Enter a detection pattern, so AnsiWEB can tell whether the app is already installed.")
            new = {
                "id": payloads.new_id(cfg, "apps", name),
                "name": name, "enabled": True, "source": "upload", "version": version,
                "arguments": arguments, "detect_pattern": pattern, "pinned": True,
                "targets": request.form.getlist("targets") or ["all"],
            }
            cfg["apps"].append(new)
            store.save(cfg)
            cache.store_upload(new, f, version)
            store.regenerate_all(cfg)
            flash(f"Added {name} {version} from the uploaded installer.", "ok")
            return redirect(url_for("app_edit", app_id=new["id"]))
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("apps_page"))

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

    # ---- uninstalling apps from the PCs --------------------------------------------
    @app.route("/uninstalls")
    def uninstalls_page():
        cfg = store.load()
        return render_template("uninstalls.html", cfg=cfg, entries=cfg.get("uninstalls", []),
                               targets=store.target_choices(cfg), reports=load_reports(),
                               apps=cfg.get("apps", []))

    @app.route("/uninstalls/add", methods=["POST"])
    def uninstall_add():
        cfg = store.load()
        f = request.form
        name = f.get("name", "").strip()
        try:
            if not name:
                raise store.ValidationError("Enter a name for what you are removing.")
            entry = {
                "id": payloads.new_id(cfg, "uninstalls", name),
                "name": name,
                "detect_pattern": f.get("detect_pattern", "").strip(),
                "enabled": True,
                "targets": request.form.getlist("targets") or ["all"],
                "notes": f.get("notes", "").strip(),
                "created": cache.now(),
            }
            cfg.setdefault("uninstalls", []).append(entry)
            store.save(cfg)
            flash(f"'{name}' added. Preview it before removing anything.", "ok")
        except store.ValidationError as exc:
            flash(str(exc), "error")
        return redirect(url_for("uninstalls_page"))

    def find_uninstall(cfg, uid):
        for i, e in enumerate(cfg.get("uninstalls", [])):
            if e["id"] == uid:
                return i, e
        abort(404)

    @app.route("/uninstalls/<uid>/toggle", methods=["POST"])
    def uninstall_toggle(uid):
        cfg = store.load()
        idx, entry = find_uninstall(cfg, uid)
        cfg["uninstalls"][idx]["enabled"] = not entry.get("enabled", True)
        if save_or_flash(cfg):
            flash(f"'{entry['name']}' {'enabled' if cfg['uninstalls'][idx]['enabled'] else 'disabled'}.", "ok")
        return redirect(url_for("uninstalls_page"))

    @app.route("/uninstalls/<uid>/delete", methods=["POST"])
    def uninstall_delete(uid):
        cfg = store.load()
        idx, entry = find_uninstall(cfg, uid)
        del cfg["uninstalls"][idx]
        if save_or_flash(cfg):
            flash(f"Removed the '{entry['name']}' uninstall entry. Apps already removed stay removed.", "ok")
        return redirect(url_for("uninstalls_page"))

    def _start_uninstall(kind, uid):
        cfg = store.load()
        _, entry = find_uninstall(cfg, uid)
        try:
            target = scoped_target(cfg, request.form.get("target", "") or "all")
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("uninstalls_page"))
        try:
            job_id = jobs.start(kind, target, trigger=f"manual ({session.get('user')})", only=entry["id"])
        except (jobs.JobBusy, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("uninstalls_page"))
        return redirect(url_for("job_view", job_id=job_id))

    @app.route("/uninstalls/<uid>/preview", methods=["POST"])
    def uninstall_preview(uid):
        return _start_uninstall("uninstall_preview", uid)

    @app.route("/uninstalls/<uid>/run", methods=["POST"])
    def uninstall_run(uid):
        if request.form.get("confirm") != "REMOVE":
            flash("Type REMOVE to confirm that the app should be uninstalled from the targeted PCs.", "error")
            return redirect(url_for("uninstalls_page"))
        return _start_uninstall("uninstall_run", uid)

    # ---- drivers / scripts / registry --------------------------------------------
    def check_kind(kind):
        if kind not in payloads.KINDS:
            abort(404)

    def check_group(group):
        if group not in payloads.GROUPS:
            abort(404)

    @app.route("/drivers")
    @app.route("/scripts")
    @app.route("/registry")
    def registry_redirect():
        """Drivers, scripts and registry files share one page; keep old links working."""
        return redirect(url_for("resources_page", group="files"))

    @app.route("/<group>")
    def resources_page(group):
        check_group(group)
        cfg = store.load()
        meta = payloads.GROUPS[group]
        rows = [{"entry": e, "kind": kind, "label": payloads.KINDS[kind]["label"],
                 "present": payloads.present(kind, e)}
                for kind in meta["kinds"] for e in cfg.get(kind, [])]
        rows.sort(key=lambda r: r["entry"]["name"].lower())
        return render_template("resources.html", group=group, meta=meta, rows=rows,
                               kinds=[payloads.KINDS[k] for k in meta["kinds"]],
                               accept=",".join(ext for k in meta["kinds"]
                                               for ext in payloads.KINDS[k]["extensions"]),
                               cfg=cfg, targets=store.target_choices(cfg),
                               run_modes=payloads.RUN_MODES, reports=load_reports(),
                               job_kind="deploy_files")

    @app.route("/<group>/add", methods=["POST"])
    def resource_add(group):
        check_group(group)
        cfg = store.load()
        f = request.files.get("payload")
        name = request.form.get("name", "").strip()
        try:
            if not name:
                raise store.ValidationError("Enter a name.")
            if not f or not f.filename:
                raise store.ValidationError("Choose a file to upload.")
            kind = payloads.kind_for_filename(f.filename, payloads.GROUPS[group]["kinds"])
            entry = {
                "id": payloads.new_id(cfg, kind, name),
                "name": name,
                "enabled": True,
                "run_mode": request.form.get("run_mode", "once"),
                "targets": request.form.getlist("targets") or ["all"],
                "notes": request.form.get("notes", "").strip(),
            }
            if kind == "scripts":
                entry.update({"arguments": request.form.get("arguments", "").strip(),
                              "timeout": int(request.form.get("timeout") or 1800),
                              "reboot": request.form.get("reboot") == "on",
                              "success_codes": parse_codes(request.form.get("success_codes", ""))})
            entry.update(payloads.store_file(kind, entry["id"], f))
            cfg.setdefault(kind, []).append(entry)
            store.save(cfg)
            flash(f"{payloads.KINDS[kind]['label']} '{name}' uploaded.", "ok")
        except (store.ValidationError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("resources_page", group=group))

    def find_resource(cfg, kind, rid):
        for i, e in enumerate(cfg.get(kind, [])):
            if e["id"] == rid:
                return i, e
        abort(404)

    @app.route("/<kind>/<rid>/edit", methods=["GET", "POST"])
    def resource_edit(kind, rid):
        check_kind(kind)
        cfg = store.load()
        idx, entry = find_resource(cfg, kind, rid)
        if request.method == "POST":
            try:
                entry.update({
                    "name": request.form.get("name", "").strip() or entry["name"],
                    "enabled": request.form.get("enabled") == "on",
                    "run_mode": request.form.get("run_mode", "once"),
                    "targets": request.form.getlist("targets") or ["all"],
                    "notes": request.form.get("notes", "").strip(),
                })
                if kind == "scripts":
                    entry.update({"arguments": request.form.get("arguments", "").strip(),
                                  "timeout": int(request.form.get("timeout") or 1800),
                                  "reboot": request.form.get("reboot") == "on",
                                  "success_codes": parse_codes(request.form.get("success_codes", ""))})
                f = request.files.get("payload")
                if f and f.filename:
                    entry.update(payloads.store_file(kind, entry["id"], f))
                cfg[kind][idx] = entry
                store.save(cfg)
                flash("Saved.", "ok")
                return redirect(url_for("resources_page", group=payloads.GROUP_OF[kind]))
            except (store.ValidationError, ValueError) as exc:
                flash(str(exc), "error")
        return render_template("resource_form.html", kind=kind, meta=payloads.KINDS[kind],
                               group=payloads.GROUP_OF[kind], entry=entry,
                               cfg=cfg, targets=store.target_choices(cfg), run_modes=payloads.RUN_MODES,
                               present=payloads.present(kind, entry))

    @app.route("/<kind>/<rid>/delete", methods=["POST"])
    def resource_delete(kind, rid):
        check_kind(kind)
        cfg = store.load()
        idx, entry = find_resource(cfg, kind, rid)
        del cfg[kind][idx]
        if save_or_flash(cfg):
            payloads.delete_file(kind, entry)
            flash(f"Removed '{entry['name']}'. Anything already applied on the PCs stays as it is.", "ok")
        return redirect(url_for("resources_page", group=payloads.GROUP_OF[kind]))

    @app.route("/<kind>/<rid>/download")
    def resource_download(kind, rid):
        check_kind(kind)
        _, entry = find_resource(store.load(), kind, rid)
        if not payloads.present(kind, entry):
            abort(404)
        return send_file(payloads.kind_dir(kind) / entry["file"], as_attachment=True,
                         download_name=entry.get("original_name") or entry["file"])

    @app.route("/<kind>/<rid>/run", methods=["POST"])
    def resource_run(kind, rid):
        check_kind(kind)
        _, entry = find_resource(store.load(), kind, rid)
        kind_job = {"drivers": "deploy_drivers", "scripts": "deploy_scripts",
                    "registry": "deploy_registry"}[kind]
        try:
            target = scoped_target(store.load(), request.form.get("target", "") or "all")
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("resources_page", kind=kind))
        try:
            job_id = jobs.start(kind_job, target, trigger=f"manual ({session.get('user')})", only=entry["id"])
        except (jobs.JobBusy, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("resources_page", group=payloads.GROUP_OF[kind]))
        return redirect(url_for("job_view", job_id=job_id))

    # ---- PCs -----------------------------------------------------------------------
    @app.route("/pcs")
    def pcs_page():
        cfg = store.load()
        return render_template("pcs.html", cfg=cfg, reports=load_reports(), visible=visible_pcs(cfg),
                               plan=store.read_json(paths.PLAN_FILE, {}),
                               secrets=vault.secret_status())

    def pc_from_form():
        f = request.form
        return {"name": f.get("name", "").strip(), "ip": f.get("ip", "").strip(), "site": f.get("site", ""),
                "groups": [g.strip() for g in f.get("groups", "").split(",") if g.strip()],
                "sync_hostname": f.get("sync_hostname") == "on",
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
        if not pc_allowed(cfg, name):
            abort(403)
        if request.method == "POST":
            new = pc_from_form()
            renamed = new["name"] != name
            cfg["pcs"][idx] = new
            if save_or_flash(cfg):
                if renamed:
                    (paths.REPORT_DIR / f"{name}.json").rename(paths.REPORT_DIR / f"{new['name']}.json") \
                        if (paths.REPORT_DIR / f"{name}.json").exists() else None
                    if new["sync_hostname"]:
                        flash(f"Renamed to {new['name']}. Run 'Apply computer name' to rename it in Windows "
                              "as well; the PC reboots to finish.", "ok")
                    else:
                        flash(f"Renamed to {new['name']} in AnsiWEB only. Tick 'Keep the Windows computer name "
                              "in sync' if the PC itself should be renamed too.", "ok")
                else:
                    flash("Saved.", "ok")
                return redirect(url_for("pc_edit", name=new["name"]))
        return render_template("pc_form.html", pc=cfg["pcs"][idx], cfg=cfg,
                               report=load_reports().get(cfg["pcs"][idx]["name"]))

    @app.route("/pcs/<name>/delete", methods=["POST"])
    def pc_delete(name):
        cfg = store.load()
        if not pc_allowed(cfg, name):
            abort(403)
        cfg["pcs"] = [pc for pc in cfg["pcs"] if pc["name"] != name]
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
                  "groups": [g for g in (row[3].split(";") if len(row) > 3 else []) if g],
                  "sync_hostname": False, "notes": ""}
            if pc["name"].lower() in existing:
                existing[pc["name"].lower()].update(pc)
            else:
                cfg["pcs"].append(pc)
            added += 1
        if save_or_flash(cfg):
            flash(f"Imported {added} PC(s).", "ok")
        return redirect(url_for("pcs_page"))

    @app.route("/pcs/account", methods=["POST"])
    def pc_account():
        """Change the local administrator account AnsiWEB uses on the PCs."""
        cfg = store.load()
        old = cfg["settings"].get("pc_account", "Admin")
        new = request.form.get("pc_account", "").strip()
        password = request.form.get("password", "")
        cfg["settings"]["pc_account"] = new
        if not save_or_flash(cfg):
            return redirect(url_for("pcs_page"))
        if password:
            vault.set_ansible_secret("vault_ansible_svc_password", password)
        if new != old:
            flash(f"AnsiWEB will now connect to PCs as '{new}'. Every PC must already have that account: "
                  "download the prep script again and run it on each PC, then test the connections.", "ok")
        elif password:
            flash("Password updated. It must match the account on the PCs.", "ok")
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
        text = text.replace("__CONTROL_NODE_IP__", cfg["settings"].get("server_ip") or "")
        text = text.replace("__ACCOUNT_NAME__", cfg["settings"].get("pc_account") or "Admin")
        return Response(text.encode("utf-8-sig"), mimetype="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename=Prepare-AnsibleHost.ps1"})

    # ---- reports ---------------------------------------------------------------------
    @app.route("/reports")
    def reports_page():
        cfg = store.load()
        reports = load_reports()
        p = store.read_json(paths.PLAN_FILE, {})
        threshold = cfg["settings"].get("stale_after_days", 14)
        rows = []
        for pc in visible_pcs(cfg):
            r = reports.get(pc["name"], {})
            apps = r.get("apps", [])
            results = r.get("results") or r.get("items") or []
            rows.append({
                "pc": pc,
                "report": r,
                "freshness": report_state.classify(r, threshold),
                "assigned": (p.get("hosts") or {}).get(pc["name"], {}),
                "needed": [a for a in apps if a.get("needed")],
                "mismatch": [a for a in apps if a.get("mismatch")],
                "ok": [a for a in apps if not a.get("needed") and not a.get("mismatch")],
                "results": results,
                "failed": [i for i in results if "exit" in str(i.get("status", ""))
                           and not str(i.get("status", "")).endswith("exit 0)")],
            })
        # The audit log is part of this page, but only an administrator may see it
        show_audit = users.can(session.get("role", ""), users.ADMIN)
        audit_days = max(0, min(int(request.args.get("days", 7) or 0), 3650))
        audit_data = {}
        if show_audit:
            audit_data = {
                "entries": audit.entries(limit=200, user=request.args.get("user", ""),
                                         action=request.args.get("action", ""), days=audit_days),
                "users_seen": audit.known_users(), "actions": audit.known_actions(),
                "filters": {"user": request.args.get("user", ""),
                            "action": request.args.get("action", ""), "days": audit_days},
                "retention": cfg["settings"].get("log_retention_days", 60),
            }
        return render_template("reports.html", cfg=cfg, rows=rows, plan=p,
                               jobs=jobs.list_jobs(25), counts=jobs.job_counts(30),
                               show_audit=show_audit, audit=audit_data, describe=audit.describe,
                               stale=report_state.summarise(visible_pcs(cfg), reports, threshold),
                               stale_days=threshold,
                               only=request.args.get("only", ""))

    @app.route("/reports/export.csv")
    def reports_export():
        cfg = store.load()
        reports = load_reports()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["pc", "site", "groups", "ip", "reported", "os", "build", "model", "serial",
                    "activated", "reboot_pending", "freshness", "days_since_report",
                    "kind", "item", "installed", "target", "status"])
        for pc in visible_pcs(cfg):
            r = reports.get(pc["name"], {})
            facts = r.get("facts") or {}
            base = [pc["name"], pc.get("site", ""), ";".join(pc.get("groups", [])), pc.get("ip", ""),
                    r.get("time", ""), facts.get("os", ""), facts.get("build", ""),
                    facts.get("model", ""), facts.get("serial", ""),
                    facts.get("activated", ""), r.get("reboot_pending", ""),
                    report_state.classify(r, cfg["settings"].get("stale_after_days", 14))["state"],
                    report_state.age_days(r) if r else ""]
            if not r:
                w.writerow(base + ["", "", "", "", "no report yet"])
                continue
            for a in r.get("apps", []):
                w.writerow(base + ["app", a.get("name", ""), a.get("installed") or "",
                                   a.get("target", ""), a.get("reason", "")])
            for i in (r.get("results") or r.get("items") or []):
                w.writerow(base + [i.get("kind", ""), i.get("name", ""), "", "", i.get("status", "")])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=ansiweb-report.csv"})

    @app.route("/reports/<name>/delete", methods=["POST"])
    def report_delete(name):
        if not store.NAME_RE.match(name):
            abort(400)
        if not pc_allowed(store.load(), name):
            abort(403)
        (paths.REPORT_DIR / f"{name}.json").unlink(missing_ok=True)
        flash(f"Cleared the stored report for {name}.", "ok")
        return redirect(url_for("reports_page"))

    # ---- jobs ----------------------------------------------------------------------------
    # ---- software inventory -------------------------------------------------------------
    def inventory_rows(cfg, query: str = "", pc_filter: str = ""):
        """Every program reported by the visible PCs, grouped by name and version."""
        reports = load_reports()
        needle = query.strip().lower()
        grouped = {}
        pcs_with_data = 0
        for pc in visible_pcs(cfg):
            report = reports.get(pc["name"], {})
            apps = report.get("inventory") or []
            if apps:
                pcs_with_data += 1
            if pc_filter and pc["name"] != pc_filter:
                continue
            for app in apps:
                name = str(app.get("name", ""))
                version = str(app.get("version", ""))
                publisher = str(app.get("publisher", ""))
                if needle and needle not in f"{name} {version} {publisher}".lower():
                    continue
                key = (name.lower(), version)
                row = grouped.setdefault(key, {"name": name, "version": version,
                                               "publisher": publisher, "arch": app.get("arch", ""),
                                               "pcs": []})
                row["pcs"].append(pc["name"])
        rows = sorted(grouped.values(), key=lambda r: (r["name"].lower(), r["version"]))
        return rows, pcs_with_data

    @app.route("/inventory")
    def inventory_page():
        cfg = store.load()
        query = request.args.get("q", "")
        pc_filter = request.args.get("pc", "")
        rows, with_data = inventory_rows(cfg, query, pc_filter)
        return render_template("inventory.html", cfg=cfg, rows=rows[:1000], total=len(rows),
                               query=query, pc_filter=pc_filter, pcs=visible_pcs(cfg),
                               with_data=with_data,
                               collect=cfg["settings"].get("collect_inventory", True))

    @app.route("/inventory/export.csv")
    def inventory_export():
        cfg = store.load()
        rows, _ = inventory_rows(cfg, request.args.get("q", ""), request.args.get("pc", ""))
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["program", "version", "publisher", "architecture", "pc_count", "pcs"])
        for r in rows:
            w.writerow([r["name"], r["version"], r["publisher"], r["arch"],
                        len(r["pcs"]), ";".join(sorted(r["pcs"]))])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=ansiweb-inventory.csv"})

    # ---- audit log ---------------------------------------------------------------------
    @app.route("/audit")
    def audit_page():
        """The audit log lives on the Reports page now; keep old links working."""
        return redirect(url_for("reports_page", **request.args.to_dict()) + "#audit")

    @app.route("/audit/export.csv")
    def audit_export():
        rows = audit.entries(limit=20000, user=request.args.get("user", ""),
                             action=request.args.get("action", ""),
                             days=max(0, int(request.args.get("days", 0) or 0)))
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["time", "user", "role", "action", "description", "detail", "outcome"])
        for r in rows:
            w.writerow([r["time"], r["user"], r["role"], r["action"], audit.describe(r["action"]),
                        r["detail"], r["outcome"]])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=ansiweb-audit.csv"})

    # ---- help ---------------------------------------------------------------------------
    @app.route("/help")
    def help_page():
        cfg = store.load()
        ip = cfg["settings"].get("server_ip") or "192.168.1.10"
        return render_template("help.html", cfg=cfg, server_ip=ip,
                               account=cfg["settings"].get("pc_account", "Admin"))

    # ---- users and roles ---------------------------------------------------------------
    @app.route("/users")
    def users_page():
        cfg = store.load()
        return render_template("users.html", users=users.all_users(), roles=users.ROLES,
                               me=session.get("user"), min_password=users.MIN_PASSWORD,
                               scope_choices=[t for t in store.target_choices(cfg) if t != "all"],
                               scope_label=users.scope_label)

    @app.route("/users/add", methods=["POST"])
    def user_add():
        f = request.form
        try:
            users.create(f.get("username", ""), f.get("password", ""), f.get("role", users.DEFAULT_ROLE),
                         request.form.getlist("scope"))
            flash(f"Added {f.get('username')} as {users.ROLES[f.get('role')]['label']}.", "ok")
        except (users.UserError, KeyError) as exc:
            flash(str(exc) if isinstance(exc, users.UserError) else "Unknown role.", "error")
        return redirect(url_for("users_page"))

    @app.route("/users/<username>/role", methods=["POST"])
    def user_role(username):
        try:
            users.set_role(username, request.form.get("role", ""))
            flash(f"{username} is now a {users.ROLES[request.form['role']]['label']}.", "ok")
        except (users.UserError, KeyError) as exc:
            flash(str(exc) if isinstance(exc, users.UserError) else "Unknown role.", "error")
        return redirect(url_for("users_page"))

    @app.route("/users/<username>/scope", methods=["POST"])
    def user_scope(username):
        try:
            users.set_scope(username, request.form.getlist("scope"))
            scope = users.scope_of(users.get(username))
            flash(f"{username} can now manage {users.scope_label(scope)}."
                  if users.get(username).get("role") != "admin" else
                  f"{username} is an administrator, so scopes do not apply.", "ok")
        except users.UserError as exc:
            flash(str(exc), "error")
        return redirect(url_for("users_page"))

    @app.route("/users/<username>/password", methods=["POST"])
    def user_password(username):
        try:
            users.set_password(username, request.form.get("password", ""))
            flash(f"Password for {username} updated.", "ok")
        except users.UserError as exc:
            flash(str(exc), "error")
        return redirect(url_for("users_page"))

    @app.route("/users/<username>/disable", methods=["POST"])
    def user_disable(username):
        if username.lower() == (session.get("user") or "").lower():
            flash("You cannot disable your own account.", "error")
            return redirect(url_for("users_page"))
        try:
            disable = request.form.get("disabled") == "on"
            users.set_disabled(username, disable)
            flash(f"{username} {'disabled' if disable else 'enabled'}.", "ok")
        except users.UserError as exc:
            flash(str(exc), "error")
        return redirect(url_for("users_page"))

    @app.route("/users/<username>/delete", methods=["POST"])
    def user_delete(username):
        if username.lower() == (session.get("user") or "").lower():
            flash("You cannot remove your own account.", "error")
            return redirect(url_for("users_page"))
        try:
            users.delete(username)
            flash(f"Removed {username}.", "ok")
        except users.UserError as exc:
            flash(str(exc), "error")
        return redirect(url_for("users_page"))

    # ---- release notes -----------------------------------------------------------------
    @app.route("/release-notes")
    def release_notes():
        notes = release.all_notes()
        seen = jobs.kv_get("acknowledged_version")
        new_since = release.since(seen) if seen and seen != __version__ else []
        # Opening the page counts as having read what changed
        jobs.kv_set("acknowledged_version", __version__)
        return render_template("release_notes.html", notes=notes, sections=release.SECTIONS,
                               current=__version__, new_since=[n["version"] for n in new_since],
                               previous=seen)

    @app.route("/jobs")
    def jobs_page():
        return render_template("jobs.html", jobs=jobs.list_jobs(200), cfg=store.load(),
                               targets=store.target_choices(store.load()))

    @app.route("/jobs/start", methods=["POST"])
    def job_start():
        kind = request.form.get("kind", "")
        target = request.form.get("target", "")
        if kind in jobs.DEPLOY_TAGS or kind == "ping":
            try:
                target = scoped_target(store.load(), target)
                store.limit_for(target)
            except store.ValidationError as exc:
                flash(str(exc), "error")
                return back()
        try:
            job_id = jobs.start(kind, target, trigger=f"manual ({session.get('user')})")
        except (jobs.JobBusy, ValueError) as exc:
            flash(str(exc), "error")
            return back()
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

    @app.route("/jobs/<int:job_id>/download")
    def job_download(job_id):
        jobs.get_job(job_id) or abort(404)
        p = jobs.log_path(job_id)
        if not p.exists():
            abort(404)
        return send_file(p, as_attachment=True, download_name=f"ansiweb-job-{job_id}.log",
                         mimetype="text/plain")

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
                    "log_retention_days": max(1, min(3650, int(f.get("log_retention_days", 60)))),
                    "session_timeout_minutes": int(f.get("session_timeout_minutes", 60)),
                    "stale_after_days": int(f.get("stale_after_days", 14)),
                    "collect_inventory": f.get("collect_inventory") == "on",
                })
                cfg["schedules"]["cache_check"] = {"enabled": f.get("cc_enabled") == "on",
                                                   "every_hours": max(1, int(f.get("cc_hours", 24)))}
                cfg["schedules"]["deploy"] = {"enabled": f.get("dep_enabled") == "on",
                                              "time": f.get("dep_time", "19:00"),
                                              "days": request.form.getlist("dep_days")}
            except ValueError:
                flash("Numbers expected for forks, batch size, hours, retention and the session timeout.", "error")
                return redirect(url_for("settings_page"))
            if save_or_flash(cfg):
                flash("Settings saved.", "ok")
            return redirect(url_for("settings_page"))
        return render_template("settings.html", cfg=cfg, days=DAY_LABELS, secrets=vault.secret_status(),
                               secret_names=vault.ANSIBLE_SECRET_NAMES, https=https,
                               targets=store.target_choices(cfg),
                               timezones=store.COMMON_TIMEZONES,
                               update_categories=store.UPDATE_CATEGORIES,
                               update_sources=store.UPDATE_SOURCES)

    @app.route("/settings/updates", methods=["POST"])
    def settings_updates():
        cfg = store.load()
        f = request.form
        try:
            cfg["updates"].update({
                "enabled": f.get("enabled") == "on",
                "categories": request.form.getlist("categories"),
                "exclude": [x.strip() for x in re.split(r"[,\n]+", f.get("exclude", "")) if x.strip()],
                "source": f.get("source", "default"),
                "reboot": f.get("reboot") == "on",
                "timeout_minutes": int(f.get("timeout_minutes") or 180),
                "targets": request.form.getlist("targets") or ["all"],
            })
            cfg["schedules"]["updates"] = {"enabled": f.get("sched_enabled") == "on",
                                           "time": f.get("sched_time", "22:00"),
                                           "days": request.form.getlist("sched_days")}
        except ValueError:
            flash("The update timeout must be a number of minutes.", "error")
            return redirect(url_for("settings_page"))
        if save_or_flash(cfg):
            flash("Update settings saved.", "ok")
        return redirect(url_for("settings_page"))

    @app.route("/settings/time", methods=["POST"])
    def settings_time():
        cfg = store.load()
        f = request.form
        cfg["time"].update({
            "enabled": f.get("enabled") == "on",
            "timezone": (f.get("timezone_custom", "").strip() or f.get("timezone", "").strip()),
            "ntp_servers": [x.strip() for x in re.split(r"[,\s]+", f.get("ntp_servers", "")) if x.strip()],
            "sync_now": f.get("sync_now") == "on",
            "targets": request.form.getlist("targets") or ["all"],
        })
        if save_or_flash(cfg):
            flash("Time settings saved.", "ok")
        return redirect(url_for("settings_page"))

    @app.route("/settings/activation", methods=["POST"])
    def settings_activation():
        cfg = store.load()
        f = request.form
        try:
            cfg["activation"].update({
                "enabled": f.get("enabled") == "on",
                "mode": f.get("mode", "mak"),
                "kms_host": f.get("kms_host", "").strip(),
                "kms_port": int(f.get("kms_port") or 1688),
                "skip_if_activated": f.get("skip_if_activated") == "on",
                "targets": request.form.getlist("targets") or ["all"],
            })
        except ValueError:
            flash("The KMS port must be a number.", "error")
            return redirect(url_for("settings_page"))
        key = f.get("product_key", "").strip().upper()
        if key:
            if not store.PRODUCT_KEY_RE.match(key):
                flash("A product key looks like XXXXX-XXXXX-XXXXX-XXXXX-XXXXX.", "error")
                return redirect(url_for("settings_page"))
            vault.set_ansible_secret("vault_windows_product_key", key)
        if save_or_flash(cfg):
            if cfg["activation"]["enabled"] and not vault.secret_status()["vault_windows_product_key"] \
                    and cfg["activation"]["mode"] == "mak":
                flash("Activation settings saved, but no product key is stored yet.", "error")
            else:
                flash("Activation settings saved." + (" Key updated." if key else ""), "ok")
        return redirect(url_for("settings_page"))

    @app.route("/settings/activation/clear-key", methods=["POST"])
    def settings_activation_clear():
        vault.set_ansible_secret("vault_windows_product_key", "")
        flash("Stored product key removed.", "ok")
        return redirect(url_for("settings_page"))

    @app.route("/settings/branding", methods=["POST"])
    def settings_branding():
        cfg = store.load()
        cfg["settings"]["site_name"] = request.form.get("site_name", "").strip()[:60]
        f = request.files.get("logo")
        try:
            if f and f.filename:
                name = (f.filename or "").lower()
                if not name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    raise store.ValidationError("The logo must be a PNG, JPG, GIF or WEBP image.")
                f.stream.seek(0, os.SEEK_END)
                if f.stream.tell() > 2 * 1024 * 1024:
                    raise store.ValidationError("The logo must be under 2 MB.")
                f.stream.seek(0)
                paths.BRANDING_DIR.mkdir(parents=True, exist_ok=True)
                for old in paths.BRANDING_DIR.glob("logo.*"):
                    old.unlink()
                ext = os.path.splitext(name)[1]
                dest = paths.BRANDING_DIR / f"logo{ext}"
                f.save(dest)
                os.chmod(dest, 0o644)
                cfg["settings"]["logo_file"] = dest.name
            store.save(cfg)
            flash("Sign-in page updated." if not (f and f.filename) else "Logo uploaded.", "ok")
        except store.ValidationError as exc:
            flash(str(exc), "error")
        return redirect(url_for("settings_page"))

    @app.route("/settings/branding/remove", methods=["POST"])
    def settings_branding_remove():
        cfg = store.load()
        for old in paths.BRANDING_DIR.glob("logo.*"):
            old.unlink()
        cfg["settings"]["logo_file"] = ""
        if save_or_flash(cfg):
            flash("Logo removed.", "ok")
        return redirect(url_for("settings_page"))

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
        return back("settings_page")

    @app.route("/settings/password", methods=["POST"])
    def own_password():
        """Any signed-in user can change their own password."""
        me = session.get("user", "")
        if not users.authenticate(me, request.form.get("current", "")):
            flash("Current password is wrong.", "error")
        elif request.form.get("new") != request.form.get("confirm"):
            flash("New passwords do not match.", "error")
        else:
            try:
                users.set_password(me, request.form.get("new", ""))
                flash("Password changed.", "ok")
            except users.UserError as exc:
                flash(str(exc), "error")
        return redirect(url_for("settings_page"))

    @app.route("/settings/backup")
    def settings_backup():
        data = backup.create()
        return send_file(io.BytesIO(data), mimetype="application/gzip",
                         as_attachment=True, download_name=backup.filename())

    @app.route("/settings/restore", methods=["POST"])
    def settings_restore():
        f = request.files.get("archive")
        if not f or not f.filename:
            flash("Choose a backup file.", "error")
        elif request.form.get("confirm") != "REPLACE":
            flash("Type REPLACE to confirm that the current configuration will be overwritten.", "error")
        else:
            try:
                result = backup.restore(f.stream)
                session.clear()   # the restored backup has its own admin account
                flash(f"Restored {len(result['restored'])} item(s) from {f.filename}. "
                      "Sign in with the credentials from that backup.", "ok")
                return redirect(url_for("login"))
            except store.ValidationError as exc:
                flash(str(exc), "error")
            except Exception as exc:   # noqa: BLE001 - show the user what went wrong
                flash(f"Restore failed: {exc}", "error")
        return redirect(url_for("settings_page"))

    if start_background:
        jobs.start_scheduler()
    return app


# ---- view helpers -------------------------------------------------------------------------
def parse_codes(text: str) -> list:
    codes = [c for c in re.split(r"[,\s]+", text or "") if c]
    try:
        return sorted({int(c) for c in codes})
    except ValueError:
        raise store.ValidationError("Exit codes must be numbers, e.g. 0, 3010")


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
        w.append(("Set this server's IP address in Settings, so PCs know where to download from.", "settings_page"))
    if not s["vault_ansible_svc_password"]:
        w.append((f"Enter the password for the '{cfg['settings'].get('pc_account')}' account on the PCs.",
                  "pcs_page"))
    if not cfg.get("pcs"):
        w.append(("Add your PCs on the PCs page.", "pcs_page"))
    act = cfg.get("activation") or {}
    if act.get("enabled") and act.get("mode") == "mak" and not s.get("vault_windows_product_key"):
        w.append(("Windows activation is on but no product key is stored.", "settings_page"))
    return w
