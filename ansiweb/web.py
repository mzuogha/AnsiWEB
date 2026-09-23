"""AnsiWEB web interface."""
import copy
import csv
import hmac
import io
import json
import os
import re
import secrets as pysecrets
import time
from datetime import timedelta

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)

from . import (__version__, audit, backup, cache, infparse, jobs, paths, payloads, release,
               report_state, store, users, util, vault)
from . import plan as plan_mod

# What each route needs. Anything not listed here requires an administrator,
# so a new route is never accidentally open to a lesser role.
ENDPOINT_PERMISSIONS = {
    # read-only
    "dashboard": users.VIEW, "apps_page": users.VIEW, "pcs_page": users.VIEW,
    "resources_page": users.VIEW, "resource_download": users.VIEW,
    "registry_redirect": users.VIEW,
    "reports_page": users.VIEW, "health_page": users.VIEW, "reports_export": users.VIEW,
    "jobs_page": users.VIEW, "job_view": users.VIEW, "job_log": users.VIEW,
    "job_download": users.VIEW, "release_notes": users.VIEW, "settings_page": users.VIEW,
    "help_page": users.VIEW, "uninstalls_page": users.VIEW, "logo": users.VIEW,
    "first_password": users.VIEW,
    "coffee": users.VIEW,
    "dismiss": users.VIEW,
    "printers_page": users.VIEW, "shares_page": users.VIEW,
    "audit_page": users.VIEW,
    "inventory_page": users.VIEW, "inventory_export": users.VIEW,
    "prepare_script": users.VIEW, "undo_script_cmd": users.VIEW, "prepare_script_cmd": users.VIEW, "logout": users.VIEW, "own_password": users.VIEW,
    "pc_edit": users.VIEW,            # the form itself; saving is checked below
    "app_edit": users.VIEW, "app_new": users.VIEW, "resource_edit": users.VIEW,
    "app_search": users.VIEW,
    # running things
    "job_start": users.RUN_JOBS, "job_retry": users.RUN_JOBS, "job_stop": users.RUN_JOBS,
    "resource_run": users.RUN_JOBS,
    "deploy_page": users.RUN_JOBS, "deploy_preview": users.RUN_JOBS, "deploy_start": users.RUN_JOBS,
    # what gets deployed
    "app_delete": users.MANAGE_CONTENT,
    "app_upload": users.MANAGE_CONTENT, "app_quick_upload": users.MANAGE_CONTENT,
    "app_search_add": users.MANAGE_CONTENT,
    "app_refresh": users.MANAGE_CONTENT, "resource_add": users.MANAGE_CONTENT,
    "share_add": users.MANAGE_CONTENT, "share_delete": users.MANAGE_CONTENT,
    "share_edit": users.MANAGE_CONTENT,
    "share_toggle": users.MANAGE_CONTENT, "share_run": users.RUN_JOBS,
    "file_sharing_save": users.MANAGE_CONTENT,
    "printer_add": users.MANAGE_CONTENT, "printer_delete": users.MANAGE_CONTENT,
    "printer_driver": users.MANAGE_CONTENT,
    "printer_toggle": users.MANAGE_CONTENT, "printer_features": users.MANAGE_CONTENT, "printer_run": users.RUN_JOBS,
    # An ad-hoc uninstall from the Inventory page is an operational action, so
    # helpdesk can do it; adding a standing uninstall entry still needs more.
    "inventory_uninstall": users.RUN_JOBS,
    "uninstall_add": users.MANAGE_CONTENT, "uninstall_delete": users.MANAGE_CONTENT,
    "uninstall_toggle": users.MANAGE_CONTENT, "uninstall_preview": users.MANAGE_CONTENT,
    "uninstall_run": users.MANAGE_CONTENT, "resource_delete": users.MANAGE_CONTENT,
    # the PC list
    "pc_add": users.MANAGE_PCS, "pc_delete": users.MANAGE_PCS, "pc_move": users.MANAGE_PCS, "pc_connection": users.ADMIN,
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
    if not vault.app_secret("checkin_token"):
        # PCs prove themselves with this when reporting their address
        vault.set_app_secret("checkin_token", pysecrets.token_urlsafe(32))
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

    def client_address() -> str:
        """Where a request really came from.

        X-Forwarded-For is set by whoever sent the request, so it is only worth
        anything when the request reached us from our own nginx on this machine.
        """
        direct = request.remote_addr or ""
        if direct in ("127.0.0.1", "::1"):
            forwarded = request.headers.get("X-Forwarded-For", "")
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        return direct

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
        if request.endpoint in ("static", "logo", "checkin"):
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

        # A new account, or one whose password an administrator has reset, has a
        # password somebody else chose. Nothing else is reachable until it is
        # changed - including the API-ish pages, so nothing can be done with it.
        if users.must_change_password(session["user"]) and request.endpoint not in (
                "first_password", "own_password", "logout", "logo", "help_page"):
            return redirect(url_for("first_password"))

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
        resp.headers["X-Clacks-Overhead"] = "GNU Terry Pratchett"
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

    def visible_jobs(cfg: dict, rows: list) -> list:
        """Job history for this person, with other PCs' names kept out of it.

        A scoped user should not learn the names of PCs outside their sites and
        groups, and a job target names them plainly.
        """
        scope = my_scope()
        if not scope:
            return rows
        allowed = {pc["name"] for pc in cfg.get("pcs", []) if users.pc_in_scope(pc, scope)}
        out = []
        for row in rows:
            target = row.get("target") or ""
            named = []
            if target.startswith("list:"):
                named = target[5:].split(",")
            elif target.startswith("pc:"):
                named = [target[3:]]
            if named and not set(named) <= allowed:
                row = dict(row)
                mine = [n for n in named if n in allowed]
                row["target"] = "list:" + ",".join(mine) if mine else "other PCs"
            out.append(row)
        return out

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
            name = request.form.get("username", "")
            source = client_address() or "-"
            wait = users.locked_for(name, source)
            if wait:
                flash(f"Too many failed attempts. Try again in {wait // 60 + 1} minute(s).", "error")
                return render_template("login.html", no_admin=not users.any_users(), branding=branding())
            user = users.authenticate(name, request.form.get("password", ""))
            if user:
                users.clear_failures(name, source)
                session.clear()
                session.permanent = True
                session["user"] = user["username"]
                session["role"] = user["role"]
                session["seen"] = time.time()
                nxt = request.args.get("next", "/")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
            users.record_failure(name, source)
            flash("Wrong user name or password, or the account is disabled.", "error")
        return render_template("login.html", no_admin=not users.any_users(), branding=branding())

    @app.route("/coffee")
    def coffee():
        cfg = store.load()
        return render_template("coffee.html", cfg=cfg), 418

    @app.route("/checkin", methods=["POST"])
    def checkin():
        """A PC telling AnsiWEB where it is.

        DHCP moves PCs about, so each one reports in at startup and hourly. No
        session: the PC proves itself with the shared token, and the address is
        taken from the connection rather than from what the PC claims, so a
        stolen token cannot redirect deployments at some other machine.
        """
        token = vault.app_secret("checkin_token")
        given = (request.headers.get("X-AnsiWEB-Token")
                 or request.form.get("token", "")).strip()
        if not token or not given or not hmac.compare_digest(token, given):
            return Response("no", status=403, mimetype="text/plain")

        name = (request.form.get("name") or "").strip()
        if not store.NAME_RE.match(name or ""):
            return Response("bad name", status=400, mimetype="text/plain")

        source = client_address()
        cfg = store.load()
        for pc in cfg.get("pcs", []):
            if pc["name"].lower() == name.lower():
                pc["seen_ip"] = source
                pc["seen_at"] = util.now()
                store.save(cfg)
                return Response("ok", status=200, mimetype="text/plain")
        # A PC nobody has added. What happens next is a deliberate choice,
        # because a PC in the list is a PC that gets deployed to.
        mode = (cfg["settings"].get("registration") or "pending").lower()
        if mode == "ignore":
            return Response("unknown", status=404, mimetype="text/plain")

        site = cfg["settings"].get("registration_site") or ""
        if mode == "auto" and site in cfg.get("sites", []):
            cfg.setdefault("pcs", []).append({
                "name": name, "ip": "", "site": site, "groups": [], "user": "",
                "sync_hostname": False, "notes": f"registered itself {util.now()}",
                "seen_ip": source, "seen_at": util.now(),
            })
            try:
                store.save(cfg)
            except store.ValidationError:
                return Response("rejected", status=400, mimetype="text/plain")
            audit.record(name, "pc", "pc_registered", f"{name} registered itself from {source}")
            return Response("added", status=201, mimetype="text/plain")

        jobs.kv_set(f"pending-pc:{name.lower()}",
                    json.dumps({"name": name, "ip": source, "first_seen": util.now()}))
        return Response("waiting", status=202, mimetype="text/plain")

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
        # A signature of which PCs are involved: dismissing hides this set, so a
        # different PC going quiet brings the notice back rather than staying hidden.
        stale_signature = ",".join(sorted(stale["stale_names"] + stale["never_names"]))
        stale_dismissed = (stale_signature != ""
                           and jobs.kv_get(f"dismiss:stale:{session.get('user', '-')}")
                           == stale_signature)
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
                               recent=visible_jobs(cfg, jobs.list_jobs(8)),
                               warnings=setup_warnings(cfg),
                               upgraded_from=upgraded_from,
                               last_cache=jobs.last_job("cache_update"),
                               last_deploy=jobs.last_job("deploy"),
                               stale=stale, stale_signature=stale_signature,
                               stale_dismissed=stale_dismissed,
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
            "archive": f.get("archive") == "on",
            "install_command": f.get("install_command", "").strip()[:300],
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

    @app.route("/apps/search")
    def app_search():
        """Look up packages in the winget catalogue to add to the standard set."""
        term = request.args.get("q", "").strip()
        results, error = [], ""
        if term:
            try:
                results = cache.winget_search(term, vault.app_secret("github_token"))
            except cache.CacheError as exc:
                error = str(exc)
        cfg = store.load()
        have = {a.get("winget_id", "").lower() for a in cfg.get("apps", []) if a.get("winget_id")}
        for r in results:
            r["already"] = r["id"].lower() in have
        return render_template("app_search.html", cfg=cfg, term=term, results=results, error=error)

    @app.route("/apps/search/add", methods=["POST"])
    def app_search_add():
        cfg = store.load()
        pkg_id = request.form.get("winget_id", "").strip()
        name = request.form.get("name", "").strip() or pkg_id.split(".")[-1]
        try:
            if not pkg_id:
                raise store.ValidationError("No package was chosen.")
            if any(a.get("winget_id", "").lower() == pkg_id.lower() for a in cfg.get("apps", [])):
                raise store.ValidationError(f"{pkg_id} is already in the standard set.")
            app_id = payloads.new_id(cfg, "apps", name)
            cfg.setdefault("apps", []).append({
                "id": app_id, "name": name, "enabled": True, "source": "winget",
                "winget_id": pkg_id, "pinned_version": "",
                # A sensible starting point; the app's own page can refine it
                "detect_pattern": "^" + re.escape(name.split()[0]),
                "install_if_missing_only": False,
                "targets": request.form.getlist("targets") or ["all"],
            })
            store.save(cfg)
            flash(f"Added {name} ({pkg_id}). Check its detection pattern, then run an update check "
                  "to download it.", "ok")
            return redirect(url_for("app_edit", app_id=app_id))
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("app_search", q=request.form.get("q", "")))

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
                if has_file:
                    # A .zip is extracted installation media, which needs the
                    # command that installs it
                    allowed = (".msi", ".exe", ".zip") if new.get("archive") else (".msi", ".exe")
                    if not upload.filename.lower().endswith(allowed):
                        raise store.ValidationError(
                            "Upload a .msi or .exe installer, or a .zip of extracted media with "
                            "'This upload is a .zip of extracted installation media' ticked.")
                    if new.get("archive"):
                        if not upload.filename.lower().endswith(".zip"):
                            raise store.ValidationError(
                                "Extracted installation media has to be a .zip.")
                        if not new.get("install_command"):
                            raise store.ValidationError(
                                "Give the command that installs it, such as "
                                "'setup.exe /configure configuration.xml'.")
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
        archive = request.form.get("archive") == "on"
        install_command = request.form.get("install_command", "").strip()[:300]
        try:
            if not f or not f.filename:
                raise store.ValidationError("Choose an installer file.")
            allowed = (".msi", ".exe", ".zip") if archive else (".msi", ".exe")
            if not f.filename.lower().endswith(allowed):
                raise store.ValidationError(
                    "Upload a .msi or .exe installer, or a .zip of extracted media with "
                    "'This upload is a .zip of extracted installation media' ticked.")
            if archive:
                if not f.filename.lower().endswith(".zip"):
                    raise store.ValidationError("Extracted installation media has to be a .zip.")
                if not install_command:
                    raise store.ValidationError(
                        "Give the command that installs it, such as "
                        "'setup.exe /configure configuration.xml'.")
            if not name:
                raise store.ValidationError("Enter a name for the app.")
            if not pattern:
                raise store.ValidationError(
                    "Enter a detection pattern, so AnsiWEB can tell whether the app is already installed.")
            new = {
                "id": payloads.new_id(cfg, "apps", name),
                "name": name, "enabled": True, "source": "upload", "version": version,
                "arguments": arguments, "detect_pattern": pattern, "pinned": True,
                "archive": archive, "install_command": install_command,
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

    @app.route("/inventory/uninstall", methods=["POST"])
    def inventory_uninstall():
        """Uninstall one program straight from the inventory, without adding an entry.

        This is an operational action rather than a change to what AnsiWEB
        deploys, so Helpdesk can do it; the target is still narrowed to the PCs
        the person is allowed to touch.
        """
        cfg = store.load()
        program = request.form.get("program", "").strip()
        target = request.form.get("target", "") or "all"
        apply_it = request.form.get("confirm") == "REMOVE"
        if not program:
            flash("No program was chosen.", "error")
            return redirect(url_for("inventory_page"))
        try:
            limit = scoped_target(cfg, target)
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("inventory_page", q=request.form.get("q", "")))
        # Match this exact program name, nothing else
        pattern = "^" + re.escape(program) + "$"
        kind = "uninstall_run" if apply_it else "uninstall_preview"
        try:
            job_id = jobs.start(kind, limit, trigger=f"manual ({session.get('user')})",
                                adhoc={"name": program, "pattern": pattern})
        except (jobs.JobBusy, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("inventory_page", q=request.form.get("q", "")))
        if not apply_it:
            flash(f"Previewing what removing '{program}' would do. Nothing has been changed.", "ok")
        return redirect(url_for("job_view", job_id=job_id))

    # ---- shared folders ------------------------------------------------------------
    @app.route("/shares")
    def shares_page():
        # Shared folders now live under Printers & Shares
        if not request.args.get("standalone"):
            return redirect(url_for("printers_page") + "#shares")
        cfg = store.load()
        return render_template("shares.html", cfg=cfg, shares=cfg.get("shares", []),
                               file_sharing=cfg.get("file_sharing", {}),
                               targets=store.target_choices(cfg))

    @app.route("/shares/add", methods=["POST"])
    def share_add():
        cfg = store.load()
        f = request.form

        def accounts(field):
            return [a.strip() for a in re.split(r"[,\n]+", f.get(field, "")) if a.strip()]

        try:
            share = {
                "name": f.get("name", "").strip(),
                "path": f.get("path", "").strip(),
                "description": f.get("description", "").strip(),
                "enabled": True,
                "read": accounts("read"), "change": accounts("change"), "full": accounts("full"),
                "remove": f.get("remove") == "on",
                "targets": request.form.getlist("targets") or ["all"],
            }
            if not share["name"]:
                raise store.ValidationError("Enter a name for the share.")
            share["id"] = payloads.new_id(cfg, "shares", share["name"])
            cfg.setdefault("shares", []).append(share)
            store.save(cfg)
            flash(f"Shared folder '{share['name']}' added. Use 'Set up now' to create it on the PCs.", "ok")
        except store.ValidationError as exc:
            flash(str(exc), "error")
        return redirect(url_for("shares_page"))

    def find_share(cfg, sid):
        for i, sh in enumerate(cfg.get("shares", [])):
            if sh["id"] == sid:
                return i, sh
        abort(404)

    @app.route("/shares/<sid>/edit", methods=["GET", "POST"])
    def share_edit(sid):
        cfg = store.load()
        idx, share = find_share(cfg, sid)
        if request.method == "POST":
            f = request.form

            def accounts(field):
                return [a.strip() for a in re.split(r"[,\n]+", f.get(field, "")) if a.strip()]

            try:
                cfg["shares"][idx].update({
                    "name": f.get("name", "").strip() or share["name"],
                    "path": f.get("path", "").strip(),
                    "description": f.get("description", "").strip(),
                    "enabled": f.get("enabled") == "on",
                    "read": accounts("read"), "change": accounts("change"), "full": accounts("full"),
                    "remove": f.get("remove") == "on",
                    "targets": request.form.getlist("targets") or ["all"],
                })
                store.save(cfg)
                flash(f"'{cfg['shares'][idx]['name']}' saved. Press 'Set up now' to apply the change "
                      "to the PCs.", "ok")
                return redirect(url_for("shares_page"))
            except store.ValidationError as exc:
                flash(str(exc), "error")
        return render_template("share_form.html", cfg=cfg, share=cfg["shares"][idx],
                               targets=store.target_choices(cfg))

    @app.route("/shares/<sid>/toggle", methods=["POST"])
    def share_toggle(sid):
        cfg = store.load()
        idx, share = find_share(cfg, sid)
        cfg["shares"][idx]["enabled"] = not share.get("enabled", True)
        if save_or_flash(cfg):
            flash(f"'{share['name']}' {'enabled' if cfg['shares'][idx]['enabled'] else 'disabled'}.", "ok")
        return redirect(url_for("shares_page"))

    @app.route("/shares/<sid>/delete", methods=["POST"])
    def share_delete(sid):
        cfg = store.load()
        idx, share = find_share(cfg, sid)
        del cfg["shares"][idx]
        if save_or_flash(cfg):
            flash(f"Removed '{share['name']}' from AnsiWEB. The share and its folder stay on the PCs; "
                  "tick 'Remove this share from the PCs' on an entry to take it down.", "ok")
        return redirect(url_for("shares_page"))

    @app.route("/shares/<sid>/run", methods=["POST"])
    def share_run(sid):
        _, share = find_share(store.load(), sid)
        try:
            cfg = store.load()
            job_id = jobs.start("shares", scoped_target(cfg, request.form.get("target", "all")),
                                trigger=f"manual ({session.get('user')})", only=share["id"])
        except (jobs.JobBusy, ValueError, store.ValidationError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("shares_page"))
        return redirect(url_for("job_view", job_id=job_id))

    @app.route("/shares/file-sharing", methods=["POST"])
    def file_sharing_save():
        cfg = store.load()
        cfg["file_sharing"] = {
            "enabled": request.form.get("enabled") == "on",
            "network_discovery": request.form.get("network_discovery") == "on",
            "targets": request.form.getlist("targets") or ["all"],
        }
        if save_or_flash(cfg):
            flash("File and printer sharing settings saved. Run 'Set up all' to apply them.", "ok")
        return redirect(url_for("shares_page"))

    # ---- printers ------------------------------------------------------------------
    @app.route("/printers")
    def printers_page():
        cfg = store.load()
        return render_template("printers.html", cfg=cfg, printers=cfg.get("printers", []),
                               s=cfg.get("win_settings") or {},
                               shares=cfg.get("shares", []),
                               file_sharing=cfg.get("file_sharing", {}),
                               package_features=package_features,
                               feature_labels=infparse.FEATURE_LABELS,
                               targets=store.target_choices(cfg),
                               pcs=[pc for pc in cfg.get("pcs", []) if pc_allowed(cfg, pc["name"])],
                               driver_present=payloads.printer_driver_present,
                               package_models=package_models,
                               drivers=[d for d in cfg.get("drivers", []) if d.get("enabled", True)])

    def package_features(printer) -> list:
        """Features the printer's driver package mentions - a hint, not a promise."""
        if payloads.printer_driver_present(printer):
            return infparse.features_in_zip(payloads.printer_driver_dir() / printer["driver_file"])
        linked = next((d for d in store.load().get("drivers", [])
                       if d.get("id") == printer.get("driver_ref")), None)
        if linked and payloads.present("drivers", linked):
            return infparse.features_in_zip(payloads.kind_dir("drivers") / linked["file"])
        return []

    def package_models(printer) -> dict:
        """The models a printer's driver package offers, if it has one."""
        if payloads.printer_driver_present(printer):
            return infparse.models_in_zip(payloads.printer_driver_dir() / printer["driver_file"])
        linked = next((d for d in store.load().get("drivers", [])
                       if d.get("id") == printer.get("driver_ref")), None)
        if linked and payloads.present("drivers", linked):
            return infparse.models_in_zip(payloads.kind_dir("drivers") / linked["file"])
        return {"models": [], "infs": [], "error": ""}

    @app.route("/printers/<pid>/driver-name", methods=["POST"])
    def printer_driver_name(pid):
        """Pick which model in the package this printer should use."""
        cfg = store.load()
        idx, printer = find_printer(cfg, pid)
        chosen = request.form.get("driver", "").strip()
        if not chosen:
            flash("Choose a model from the package.", "error")
            return redirect(url_for("printers_page"))
        cfg["printers"][idx]["driver"] = chosen
        # Install only the .inf that offers it, rather than the whole archive
        cfg["printers"][idx]["driver_inf"] = request.form.get("inf", "").strip()
        if save_or_flash(cfg):
            flash(f"'{printer['name']}' will use the '{chosen}' driver. Press 'Set up now' to apply it.", "ok")
        return redirect(url_for("printers_page"))

    def printer_from_form(existing=None):
        f = request.form
        p = dict(existing or {})
        p.update({
            "name": f.get("name", "").strip(),
            "enabled": f.get("enabled", "on") == "on",
            "driver": f.get("driver", "").strip(),
            "host": f.get("host", "").strip(),
            "port": int(f.get("port") or 9100) if (f.get("port") or "9100").isdigit() else 0,
            "port_name": f.get("port_name", "").strip(),
            "comment": f.get("comment", "").strip(),
            "location": f.get("location", "").strip(),
            "default": f.get("default") == "on",
            "remove": f.get("remove") == "on",
            "driver_inf": f.get("driver_inf", "").strip()[:80],
            # "targets" may hold all / site: / group: entries and individual pc: entries
            "targets": request.form.getlist("targets") or ["all"],
            # A driver package uploaded on the drivers page can be linked here
            # instead of attaching a second copy to the printer.
            "driver_ref": f.get("driver_ref", "").strip(),
        })
        return p

    @app.route("/printers/add", methods=["POST"])
    def printer_add():
        cfg = store.load()
        try:
            printer = printer_from_form()
            if not printer["name"]:
                raise store.ValidationError("Enter a name for the printer.")
            printer["id"] = payloads.new_id(cfg, "printers", printer["name"])
            driver = request.files.get("driver_package")
            if driver and driver.filename:
                printer.update(payloads.store_printer_driver(printer["id"], driver))
                _warn_about_driver("drivers", {"file": printer.get("driver_file")},
                                   payloads.printer_driver_dir())
            cfg.setdefault("printers", []).append(printer)
            store.save(cfg)
            staged = " The driver is staged and will be installed before the printer." \
                if printer.get("driver_file") else ""
            flash(f"Printer '{printer['name']}' added.{staged} Use 'Set up on' to push it to PCs.", "ok")
        except store.ValidationError as exc:
            flash(str(exc), "error")
        return redirect(url_for("printers_page"))

    def find_printer(cfg, pid):
        for i, p in enumerate(cfg.get("printers", [])):
            if p["id"] == pid:
                return i, p
        abort(404)

    @app.route("/printers/<pid>/features", methods=["POST"])
    def printer_features(pid):
        """Printing defaults applied to this printer on every PC."""
        cfg = store.load()
        idx, printer = find_printer(cfg, pid)
        f = request.form
        cfg["printers"][idx].update({
            "duplex": f.get("duplex", ""), "colour": f.get("colour", ""),
            "collate": f.get("collate", ""), "paper_size": f.get("paper_size", "").strip()[:24],
        })
        if save_or_flash(cfg):
            flash(f"Printing defaults saved for '{printer['name']}'. Press 'Set up now' to apply them. "
                  "Anything this printer does not support is reported and left as it was.", "ok")
        return redirect(url_for("printers_page"))

    @app.route("/printers/<pid>/toggle", methods=["POST"])
    def printer_toggle(pid):
        cfg = store.load()
        idx, printer = find_printer(cfg, pid)
        cfg["printers"][idx]["enabled"] = not printer.get("enabled", True)
        if save_or_flash(cfg):
            flash(f"'{printer['name']}' {'enabled' if cfg['printers'][idx]['enabled'] else 'disabled'}.", "ok")
        return redirect(url_for("printers_page"))

    @app.route("/printers/<pid>/delete", methods=["POST"])
    def printer_delete(pid):
        cfg = store.load()
        idx, printer = find_printer(cfg, pid)
        del cfg["printers"][idx]
        if save_or_flash(cfg):
            payloads.delete_printer_driver(printer)
            flash(f"Removed '{printer['name']}' from AnsiWEB. It stays installed on the PCs; tick "
                  "'Remove this printer from the PCs' on an entry to take it off them.", "ok")
        return redirect(url_for("printers_page"))

    @app.route("/printers/<pid>/driver", methods=["POST"])
    def printer_driver(pid):
        cfg = store.load()
        idx, printer = find_printer(cfg, pid)
        f = request.files.get("driver_package")
        try:
            if not f or not f.filename:
                raise store.ValidationError("Choose a driver package (.zip of the vendor's .inf files).")
            cfg["printers"][idx].update(payloads.store_printer_driver(pid, f))
            store.save(cfg)
            _warn_about_driver("drivers", {"file": cfg["printers"][idx].get("driver_file")},
                               payloads.printer_driver_dir())
            flash(f"Driver staged for '{printer['name']}'. It is installed on each PC before the printer.", "ok")
        except store.ValidationError as exc:
            flash(str(exc), "error")
        return redirect(url_for("printers_page"))

    @app.route("/printers/<pid>/run", methods=["POST"])
    def printer_run(pid):
        cfg = store.load()
        _, printer = find_printer(cfg, pid)
        # Either a group target, or individual PCs ticked on the page
        chosen = [n for n in request.form.getlist("pcs") if n]
        target = "list:" + ",".join(chosen) if chosen else request.form.get("target", "all")
        try:
            job_id = jobs.start("printers", scoped_target(cfg, target),
                                trigger=f"manual ({session.get('user')})", only=printer["id"])
        except (jobs.JobBusy, ValueError, store.ValidationError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("printers_page"))
        return redirect(url_for("job_view", job_id=job_id))

    # ---- uninstalling apps from the PCs --------------------------------------------
    @app.route("/uninstalls")
    def uninstalls_page():
        """Uninstalling lives on the Inventory page now; keep old links working."""
        return redirect(url_for("inventory_page") + "#uninstall")

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
        return redirect(url_for("inventory_page") + "#uninstall")

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
        return redirect(url_for("inventory_page") + "#uninstall")

    @app.route("/uninstalls/<uid>/delete", methods=["POST"])
    def uninstall_delete(uid):
        cfg = store.load()
        idx, entry = find_uninstall(cfg, uid)
        del cfg["uninstalls"][idx]
        if save_or_flash(cfg):
            flash(f"Removed the '{entry['name']}' uninstall entry. Apps already removed stay removed.", "ok")
        return redirect(url_for("inventory_page") + "#uninstall")

    def _start_uninstall(kind, uid):
        cfg = store.load()
        _, entry = find_uninstall(cfg, uid)
        try:
            target = scoped_target(cfg, request.form.get("target", "") or "all")
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("inventory_page") + "#uninstall")
        try:
            job_id = jobs.start(kind, target, trigger=f"manual ({session.get('user')})", only=entry["id"])
        except (jobs.JobBusy, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("inventory_page") + "#uninstall")
        return redirect(url_for("job_view", job_id=job_id))

    @app.route("/uninstalls/<uid>/preview", methods=["POST"])
    def uninstall_preview(uid):
        return _start_uninstall("uninstall_preview", uid)

    @app.route("/uninstalls/<uid>/run", methods=["POST"])
    def uninstall_run(uid):
        if request.form.get("confirm") != "REMOVE":
            flash("Type REMOVE to confirm that the app should be uninstalled from the targeted PCs.", "error")
            return redirect(url_for("inventory_page") + "#uninstall")
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
                               pcs=visible_pcs(cfg),
                               kinds=[payloads.KINDS[k] for k in meta["kinds"]],
                               accept=",".join(ext for k in meta["kinds"]
                                               for ext in payloads.KINDS[k]["extensions"]),
                               cfg=cfg, targets=store.target_choices(cfg),
                               run_modes=payloads.RUN_MODES, reports=load_reports(),
                               job_kind="deploy_files")

    def _warn_about_driver(kind: str, entry: dict, folder=None) -> None:
        """Say so at upload time when a driver package looks like Windows will refuse it."""
        if kind != "drivers" or not entry.get("file"):
            return
        try:
            info = payloads.inspect_driver_zip((folder or payloads.kind_dir(kind)) / entry["file"])
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return
        warn = payloads.driver_warning(info)
        if warn:
            flash(warn, "error")

    def payload_fields(kind: str, entry: dict) -> dict:
        """The form fields shared by adding and editing a driver, script or registry file."""
        f = request.form
        entry.update({
            "run_mode": f.get("run_mode", "once"),
            "targets": request.form.getlist("targets") or ["all"],
            "notes": f.get("notes", "").strip(),
        })
        if kind == "drivers":
            # Optional: only install the .inf files matching this, for packages
            # that hold several and not all of them are wanted.
            entry["inf_filter"] = f.get("inf_filter", "").strip()[:80]
        if kind == "scripts":
            entry.update({"arguments": f.get("arguments", "").strip(),
                          "timeout": int(f.get("timeout") or 1800),
                          "reboot": f.get("reboot") == "on",
                          "success_codes": parse_codes(f.get("success_codes", ""))})
        return entry

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
            entry = payload_fields(kind, {
                "id": payloads.new_id(cfg, kind, name),
                "name": name,
                "enabled": True,
            })
            entry.update(payloads.store_file(kind, entry["id"], f))
            _warn_about_driver(kind, entry)
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
                entry.update({"name": request.form.get("name", "").strip() or entry["name"],
                              "enabled": request.form.get("enabled") == "on"})
                entry = payload_fields(kind, entry)
                f = request.files.get("payload")
                if f and f.filename:
                    entry.update(payloads.store_file(kind, entry["id"], f))
                    _warn_about_driver(kind, entry)
                cfg[kind][idx] = entry
                store.save(cfg)
                flash("Saved.", "ok")
                return redirect(url_for("resources_page", group=payloads.GROUP_OF[kind]))
            except (store.ValidationError, ValueError) as exc:
                flash(str(exc), "error")
        return render_template("resource_form.html", kind=kind, meta=payloads.KINDS[kind],
                               group=payloads.GROUP_OF[kind], entry=entry, pcs=visible_pcs(cfg),
                               models=(infparse.models_in_zip(payloads.kind_dir(kind) / entry["file"])
                                       if kind == "drivers" and payloads.present(kind, entry) else {}),
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
        query = request.args.get("q", "").strip().lower()
        shown = visible_pcs(cfg)
        if query == "xyzzy":
            flash("Nothing happens.", "ok")
        if query:
            shown = [pc for pc in shown
                     if query in pc["name"].lower()
                     or query in (pc.get("ip") or "").lower()
                     or query in (pc.get("user") or "").lower()
                     or query in (pc.get("site") or "").lower()]
        return render_template("pcs.html", cfg=cfg, reports=load_reports(), visible=shown,
                               pending=pending_pcs() if users.can(session.get("role", ""), users.MANAGE_PCS) else [],
                               query=query, total=len(visible_pcs(cfg)),
                               plan=store.read_json(paths.PLAN_FILE, {}),
                               secrets=vault.secret_status())

    def pc_from_form():
        f = request.form
        return {"name": f.get("name", "").strip(), "ip": f.get("ip", "").strip(), "site": f.get("site", ""),
                "user": f.get("user", "").strip()[:60],
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

    def pending_pcs() -> list:
        """PCs that have reported in but nobody has added."""
        out = []
        for key in jobs.kv_keys("pending-pc:"):
            try:
                out.append(json.loads(jobs.kv_get(key) or "{}"))
            except ValueError:
                continue
        return sorted([p for p in out if p.get("name")], key=lambda p: p["name"])

    @app.route("/pcs/pending/<name>", methods=["POST"])
    def pc_pending(name):
        """Accept a PC that registered itself, or dismiss it."""
        cfg = store.load()
        key = f"pending-pc:{name.lower()}"
        record = json.loads(jobs.kv_get(key) or "{}")
        if not record:
            abort(404)
        if request.form.get("action") == "ignore":
            jobs.kv_delete(key)
            flash(f"{name} dismissed. It will reappear if it reports in again.", "ok")
            return redirect(url_for("pcs_page"))
        site = request.form.get("site", "")
        if site not in cfg.get("sites", []):
            flash("Choose the site this PC belongs to.", "error")
            return redirect(url_for("pcs_page"))
        if any(pc["name"].lower() == name.lower() for pc in cfg.get("pcs", [])):
            jobs.kv_delete(key)
            flash(f"{name} was already in the list.", "ok")
            return redirect(url_for("pcs_page"))
        cfg.setdefault("pcs", []).append({
            "name": record["name"], "ip": "", "site": site, "groups": [], "user": "",
            "sync_hostname": False, "notes": f"registered itself {record.get('first_seen', '')}",
            "seen_ip": record.get("ip", ""), "seen_at": record.get("first_seen", ""),
        })
        if save_or_flash(cfg):
            jobs.kv_delete(key)
            flash(f"{name} added to {site}. It will be included in the next deployment "
                  "that covers that site.", "ok")
        return redirect(url_for("pcs_page"))

    @app.route("/pcs/move", methods=["POST"])
    def pc_move():
        """Move one or several PCs to another site."""
        cfg = store.load()
        site = request.form.get("site", "").strip()
        names = [n for n in request.form.getlist("pcs") if n]
        one = request.form.get("pc", "").strip()
        if one:
            names = [one]
        if site not in cfg.get("sites", []):
            flash("Choose a site that exists.", "error")
            return redirect(url_for("pcs_page"))
        if not names:
            flash("Tick the PCs to move first.", "error")
            return redirect(url_for("pcs_page"))
        moved = []
        for pc in cfg["pcs"]:
            if pc["name"] in names and pc_allowed(cfg, pc["name"]):
                if pc.get("site") != site:
                    pc["site"] = site
                    moved.append(pc["name"])
        if not moved:
            flash("Nothing to move: those PCs are already at that site.", "ok")
            return redirect(url_for("pcs_page"))
        if save_or_flash(cfg):
            flash(f"Moved {len(moved)} PC(s) to {site}: {', '.join(moved[:6])}"
                  f"{' and more' if len(moved) > 6 else ''}. What each PC gets is decided by site, "
                  "so check the deployment still suits them.", "ok")
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
                  "user": (row[4][:60] if len(row) > 4 else ""),
                  "sync_hostname": False, "notes": ""}
            if pc["name"].lower() in existing:
                existing[pc["name"].lower()].update(pc)
            else:
                cfg["pcs"].append(pc)
            added += 1
        if save_or_flash(cfg):
            flash(f"Imported {added} PC(s).", "ok")
        return redirect(url_for("pcs_page"))

    @app.route("/pcs/connection", methods=["POST"])
    def pc_connection():
        """Which way AnsiWEB reaches the PCs; must match how they were prepared."""
        cfg = store.load()
        cfg["settings"]["pc_connection"] = request.form.get("pc_connection", "https")
        if save_or_flash(cfg):
            mode = cfg["settings"]["pc_connection"]
            flash("AnsiWEB will connect on port 5985 using NTLM, for PCs prepared with the .cmd script."
                  if mode == "ntlm" else
                  "AnsiWEB will connect on port 5986 over HTTPS, for PCs prepared with the PowerShell script.",
                  "ok")
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
        if action == "add":
            if not name:
                flash("Enter a name for the site.", "error")
            elif name in cfg["sites"]:
                flash(f"There is already a site called '{name}'.", "error")
            else:
                cfg["sites"].append(name)
                save_or_flash(cfg)
                flash(f"Site '{name}' added.", "ok")
            return redirect(url_for("pcs_page"))

        if action == "rename":
            new = request.form.get("new_name", "").strip()
            if name not in cfg["sites"]:
                abort(404)
            if not new:
                flash("Enter the new name for the site.", "error")
            elif new != name and new in cfg["sites"]:
                flash(f"There is already a site called '{new}'.", "error")
            elif new != name:
                cfg["sites"] = [new if x == name else x for x in cfg["sites"]]
                moved = 0
                for pc in cfg["pcs"]:
                    if pc.get("site") == name:
                        pc["site"] = new
                        moved += 1
                # Anything aimed at the old site follows it, so nothing silently
                # stops being deployed.
                retargeted = 0
                for group in ("apps", "drivers", "scripts", "registry", "printers",
                              "shares", "uninstalls"):
                    for item in cfg.get(group, []) or []:
                        targets = item.get("targets") or []
                        if f"site:{name}" in targets:
                            item["targets"] = [f"site:{new}" if t == f"site:{name}" else t
                                               for t in targets]
                            retargeted += 1
                for block in ("time", "activation", "file_sharing"):
                    section = cfg.get(block) or {}
                    if f"site:{name}" in (section.get("targets") or []):
                        section["targets"] = [f"site:{new}" if t == f"site:{name}" else t
                                              for t in section["targets"]]
                        retargeted += 1
                for user in users.all_users():
                    scope = users.scope_of(user)
                    if f"site:{name}" in scope:
                        users.set_scope(user["username"],
                                        [f"site:{new}" if t == f"site:{name}" else t for t in scope])
                if save_or_flash(cfg):
                    flash(f"Site renamed to '{new}'. {moved} PC(s) moved with it, and "
                          f"{retargeted} item(s) that targeted it were updated.", "ok")
            return redirect(url_for("pcs_page"))

        if action == "delete":
            if any(pc["site"] == name for pc in cfg["pcs"]):
                flash(f"Site '{name}' still has PCs. Move or remove them first.", "error")
                return redirect(url_for("pcs_page"))
            cfg["sites"] = [s for s in cfg["sites"] if s != name]
            if save_or_flash(cfg):
                flash(f"Site '{name}' removed.", "ok")
            return redirect(url_for("pcs_page"))

        return redirect(url_for("pcs_page"))

    @app.route("/prepare-script.cmd")
    def prepare_script_cmd():
        """The simple preparation method: a .cmd using only built-in commands."""
        cfg = store.load()
        text = (paths.SCRIPTS_DIR / "Prepare-AnsibleHost.cmd").read_text(encoding="utf-8")
        text = text.replace("__CONTROL_NODE_IP__", cfg["settings"].get("server_ip") or "")
        text = text.replace("__ACCOUNT_NAME__", cfg["settings"].get("pc_account") or "Admin")
        text = text.replace("__CHECKIN_TOKEN__", vault.app_secret("checkin_token") or "__CHECKIN_TOKEN__")
        # Batch files want CRLF, and no byte-order mark
        return Response(text.replace("\n", "\r\n").encode("ascii", "replace"),
                        mimetype="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename=Prepare-AnsibleHost.cmd"})

    @app.route("/undo-script.cmd")
    def undo_script_cmd():
        """Undo what the preparation script did to a PC."""
        cfg = store.load()
        text = (paths.SCRIPTS_DIR / "Undo-AnsibleHost.cmd").read_text(encoding="utf-8")
        text = text.replace("__ACCOUNT_NAME__", cfg["settings"].get("pc_account") or "Admin")
        return Response(text.replace("\n", "\r\n").encode("ascii", "replace"),
                        mimetype="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename=Undo-AnsibleHost.cmd"})

    @app.route("/prepare-script")
    def prepare_script():
        cfg = store.load()
        text = (paths.SCRIPTS_DIR / "Prepare-AnsibleHost.ps1").read_text(encoding="utf-8")
        text = text.replace("__CONTROL_NODE_IP__", cfg["settings"].get("server_ip") or "")
        text = text.replace("__ACCOUNT_NAME__", cfg["settings"].get("pc_account") or "Admin")
        return Response(text.encode("utf-8-sig"), mimetype="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename=Prepare-AnsibleHost.ps1"})

    # ---- reports ---------------------------------------------------------------------
    @app.route("/windows-settings")
    def winsettings_page():
        # Windows settings now sit under Printers, Shares & Settings
        if not request.args.get("standalone"):
            return redirect(url_for("printers_page") + "#windows-settings")
        cfg = store.load()
        return render_template("winsettings.html", cfg=cfg,
                               s=cfg.get("win_settings") or {},
                               targets=store.target_choices(cfg))

    @app.route("/windows-settings", methods=["POST"])
    def winsettings_save():
        cfg = store.load()
        f = request.form
        out = {}
        for key in ("file_extensions", "hidden_files", "fast_startup", "remote_desktop"):
            raw = f.get(key, "")
            if raw in ("0", "1"):
                out[key] = raw == "1"
        if f.get("power_plan") in ("balanced", "performance", "saver"):
            out["power_plan"] = f["power_plan"]
        for key, ceiling in (("sleep_minutes_ac", 480), ("lock_screen_timeout", 240)):
            raw = f.get(key, "").strip()
            if raw:
                try:
                    out[key] = max(0, min(int(raw), ceiling))
                except ValueError:
                    pass
        cfg["win_settings"] = out
        if save_or_flash(cfg):
            flash(f"{len(out)} setting(s) recorded. They reach the PCs on the next deployment "
                  "that includes Windows settings, or press Apply now.", "ok")
        return redirect(url_for("winsettings_page"))

    def health_overview(cfg) -> list:
        """What each PC last reported about its condition."""
        rows = []
        for pc in visible_pcs(cfg):
            data = store.read_json(paths.REPORT_DIR / f"health-{pc['name']}.json", {})
            worst = min([d.get("free_pct", 100) for d in (data.get("disks") or [])] or [100])
            rows.append({"pc": pc, "data": data, "worst_free": worst,
                         "concerns": [c for c in (
                             "little disk space left" if worst < 10 else "",
                             "waiting for a reboot" if data.get("reboot_pending") else "",
                             "not restarted in over 30 days"
                             if (data.get("uptime_days") or 0) > 30 else "",
                             "virus signatures over a week old"
                             if ((data.get("defender") or {}).get("age_days") or 0) > 7 else "",
                         ) if c]})
        return rows

    @app.route("/health")
    def health_page():
        # PC health now sits under Inventory, Health & Uninstall
        if not request.args.get("standalone"):
            return redirect(url_for("inventory_page") + "#health")
        cfg = store.load()
        rows = health_overview(cfg)
        return render_template("health.html", cfg=cfg, rows=rows,
                               targets=store.target_choices(cfg))

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
            results = r.get("results") or []
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
                               jobs=visible_jobs(cfg, jobs.list_jobs(25)), counts=jobs.job_counts(30),
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
        w.writerow(["pc", "assigned_to", "site", "groups", "ip", "reported", "os", "build", "model", "serial",
                    "activated", "reboot_pending", "freshness", "days_since_report",
                    "kind", "item", "installed", "target", "status"])
        for pc in visible_pcs(cfg):
            r = reports.get(pc["name"], {})
            facts = r.get("facts") or {}
            base = [pc["name"], pc.get("user", ""), pc.get("site", ""),
                    ";".join(pc.get("groups", [])), pc.get("ip", ""),
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
            for i in r.get("results", []):
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
        # Grouped by PC as well as by program: "what is on this machine" is a
        # different question from "who has this program".
        by_pc = []
        reports = load_reports()
        for pc in visible_pcs(cfg):
            if pc_filter and pc["name"] != pc_filter:
                continue
            report = reports.get(pc["name"]) or {}
            programs = sorted((report.get("inventory") or []),
                              key=lambda x: (x.get("name") or "").lower())
            if query:
                needle = query.lower()
                programs = [x for x in programs
                            if needle in (x.get("name") or "").lower()
                            or needle in (x.get("publisher") or "").lower()]
            by_pc.append({"pc": pc, "programs": programs, "reported": report.get("time", ""),
                          "ever": bool(report)})
        return render_template("inventory.html", cfg=cfg, rows=rows[:1000], total=len(rows),
                               health=health_overview(cfg),
                               by_pc=by_pc, view=request.args.get("view", "program"),
                               query=query, pc_filter=pc_filter, pcs=visible_pcs(cfg),
                               with_data=with_data,
                               collect=cfg["settings"].get("collect_inventory", True),
                               uninstalls=cfg.get("uninstalls", []),
                               targets=store.target_choices(cfg))

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
            users.set_password(username, request.form.get("password", ""), force_change=True)
            flash(f"Password for {username} updated. They must change it when they next sign in.", "ok")
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

    @app.route("/dismiss/<what>", methods=["POST"])
    def dismiss(what):
        """Hide a dashboard notice for this person."""
        me = session.get("user", "-")
        if what == "upgrade":
            jobs.kv_set("acknowledged_version", __version__)
        elif what == "stale":
            # Remembered against the PCs involved, so the notice comes back if
            # a different PC stops reporting rather than staying hidden.
            jobs.kv_set(f"dismiss:stale:{me}", request.form.get("signature", ""))
        else:
            abort(404)
        return redirect(url_for("dashboard"))

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
        cfg = store.load()
        return render_template("jobs.html", jobs=visible_jobs(cfg, jobs.list_jobs(200)), cfg=cfg,
                               targets=store.target_choices(cfg))

    @app.route("/deploy")
    def deploy_page():
        """Choose what to deploy, and where, before starting it."""
        cfg = store.load()
        target = request.args.get("target", "all")
        # Ticks come back from a preview, so nothing is lost by looking first
        return render_template("deploy.html", cfg=cfg, target=target,
                               chosen=request.args.getlist("parts"),
                               targets=store.target_choices(cfg), pcs=visible_pcs(cfg),
                               parts=jobs.DEPLOY_PARTS,
                               configured=deploy_relevance(cfg))

    def deploy_relevance(cfg) -> dict:
        """Which parts have anything set up, so the page can say so."""
        s = cfg.get("settings", {})
        return {
            "apps": any(a.get("enabled", True) for a in cfg.get("apps", [])),
            "drivers": bool(cfg.get("drivers")),
            "scripts": bool(cfg.get("scripts")),
            "registry": bool(cfg.get("registry")),
            "shares": bool(cfg.get("shares")) or bool((cfg.get("file_sharing") or {}).get("enabled")),
            "printers": bool(cfg.get("printers")),
            "time": bool((cfg.get("time") or {}).get("enabled")),
            "activation": bool((cfg.get("activation") or {}).get("enabled")),
            "hostname": any(pc.get("sync_hostname") for pc in cfg.get("pcs", [])),
            "updates": bool((cfg.get("updates") or {}).get("enabled")),
            "inventory": bool(s.get("collect_inventory", True)),
        }

    @app.route("/deploy/preview")
    def deploy_preview():
        """What a deployment would do, per PC, without touching anything."""
        cfg = store.load()
        target = request.args.get("target", "all")
        chosen = [p for p in request.args.getlist("parts")] or [p[0] for p in jobs.DEPLOY_PARTS]
        plan = store.read_json(paths.PLAN_FILE, {})
        reports = load_reports()
        rows = []
        for pc in visible_pcs(cfg):
            if not plan_mod.pc_matches(pc, [target]):
                continue
            host = (plan.get("hosts") or {}).get(pc["name"], {})
            report = reports.get(pc["name"]) or {}
            seen = {a.get("id"): a for a in (report.get("apps") or [])}
            changes = []
            if "apps" in chosen:
                for app_id in host.get("apps", []):
                    app = next((a for a in plan.get("apps", []) if a["id"] == app_id), None)
                    if not app:
                        continue
                    was = seen.get(app_id)
                    if was is None:
                        changes.append(("app", app["name"], f"to {app['version']}", "not checked yet"))
                    elif was.get("needed"):
                        changes.append(("app", app["name"], f"to {app['version']}", was.get("reason", "")))
            for key, label in (("drivers", "driver"), ("scripts", "script"),
                               ("registry", "registry file"), ("printers", "printer"),
                               ("shares", "shared folder")):
                if key not in chosen:
                    continue
                for item_id in host.get(key, []):
                    named = next((x for x in plan.get(key, []) if x.get("id") == item_id), None)
                    changes.append((label, (named or {}).get("name", item_id), "", ""))
            if "hostname" in chosen and host.get("hostname"):
                changes.append(("computer name", host["hostname"], "", "renamed if it differs"))
            if "time" in chosen and host.get("time"):
                changes.append(("time settings", "time zone and clock", "", ""))
            if "activation" in chosen and host.get("activate"):
                changes.append(("activation", "Windows activation", "", ""))
            rows.append({"pc": pc, "changes": changes,
                         "reported": (report.get("time") or ""), "fresh": bool(report)})
        return render_template("preview.html", cfg=cfg, rows=rows, target=target,
                               chosen=chosen, parts=jobs.DEPLOY_PARTS,
                               targets=store.target_choices(cfg))

    @app.route("/deploy", methods=["POST"])
    def deploy_start():
        cfg = store.load()
        chosen = [p for p in request.form.getlist("parts") if p in dict((k, 1) for k, _, _ in jobs.DEPLOY_PARTS)]
        picked_pcs = [n for n in request.form.getlist("pcs") if n]
        target = "list:" + ",".join(picked_pcs) if picked_pcs else request.form.get("target", "all")
        try:
            limit = scoped_target(cfg, target)
        except store.ValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("deploy_page", target=target))
        if not chosen:
            flash("Choose at least one thing to deploy.", "error")
            return redirect(url_for("deploy_page", target=target))
        everything = len(chosen) == len(jobs.DEPLOY_PARTS)
        try:
            job_id = jobs.start("deploy", limit, trigger=f"manual ({session.get('user')})",
                                tags="" if everything else ",".join(chosen))
        except (jobs.JobBusy, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("deploy_page", target=target))
        return redirect(url_for("job_view", job_id=job_id))

    @app.route("/jobs/<int:job_id>/stop", methods=["POST"])
    def job_stop(job_id):
        """Stop waiting for a job that is stuck on something."""
        if jobs.stop_job(job_id):
            flash("Stopping. What had already finished on each PC stays done - run the job again, "
                  "with the part that was holding it up unticked, to carry on.", "ok")
        else:
            flash("That job is not running, so there is nothing to stop.", "error")
        return redirect(url_for("job_view", job_id=job_id))

    @app.route("/jobs/<int:job_id>/retry", methods=["POST"])
    def job_retry(job_id):
        """Run a job again on just the PCs it failed on."""
        job = jobs.get_job(job_id)
        if not job:
            abort(404)
        cfg = store.load()
        failed = [h for h in jobs.kv_get(f"failed-hosts:{job_id}").split(",") if h]
        mine = [h for h in failed if pc_allowed(cfg, h)]
        if not mine:
            flash("There are no failed PCs recorded for that job.", "error")
            return redirect(url_for("job_view", job_id=job_id))
        try:
            args = json.loads(jobs.kv_get(f"job-args:{job_id}") or "{}")
            new_id = jobs.start(job["kind"], "list:" + ",".join(mine),
                                trigger=f"retry of #{job_id} ({session.get('user')})",
                                only=args.get("only", ""), tags=args.get("tags", ""))
        except (jobs.JobBusy, ValueError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("job_view", job_id=job_id))
        return redirect(url_for("job_view", job_id=new_id))

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
)

    @app.route("/settings/concurrency", methods=["POST"])
    def settings_concurrency():
        cfg = store.load()
        try:
            n = int(request.form.get("concurrent_jobs", 3))
        except ValueError:
            n = 3
        cfg["settings"]["concurrent_jobs"] = max(1, min(n, 10))
        if save_or_flash(cfg):
            flash("Saved.", "ok")
        return redirect(url_for("settings_page") + "#concurrency")

    @app.route("/settings/timeout", methods=["POST"])
    def settings_timeout():
        cfg = store.load()
        try:
            minutes = int(request.form.get("step_timeout_minutes", 30))
        except ValueError:
            minutes = 30
        cfg["settings"]["step_timeout_minutes"] = max(1, min(minutes, 600))
        try:
            at_once = int(request.form.get("concurrent_jobs", 3))
        except ValueError:
            at_once = 3
        cfg["settings"]["concurrent_jobs"] = max(1, min(at_once, 10))
        if save_or_flash(cfg):
            flash("Saved.", "ok")
        return redirect(url_for("settings_page") + "#timeouts")

    @app.route("/settings/registration", methods=["POST"])
    def settings_registration():
        cfg = store.load()
        mode = request.form.get("registration", "pending")
        cfg["settings"]["registration"] = mode if mode in ("ignore", "pending", "auto") else "pending"
        cfg["settings"]["registration_site"] = request.form.get("registration_site", "")
        if save_or_flash(cfg):
            flash("Saved.", "ok")
        return redirect(url_for("settings_page") + "#registration")

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

    @app.route("/password/new", methods=["GET"])
    def first_password():
        """Shown until a new or reset password has been replaced."""
        if not users.must_change_password(session.get("user", "")):
            return redirect(url_for("dashboard"))
        return render_template("first_password.html", me=session.get("user"),
                               min_password=users.MIN_PASSWORD)

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
                users.set_password(me, request.form.get("new", ""), force_change=False)
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

    # Apps that are switched on but have no installer cached will not deploy,
    # and a deployment that skips them still finishes cleanly - so say it here.
    plan = store.read_json(paths.PLAN_FILE, {})
    waiting = [x for x in (plan.get("skipped") or []) if x.get("kind") == "apps"]
    if waiting:
        names = ", ".join(x["name"] for x in waiting[:4])
        more = f" and {len(waiting) - 4} more" if len(waiting) > 4 else ""
        w.append((f"{len(waiting)} app(s) are not in the cache yet, so they will not install: "
                  f"{names}{more}. Run 'Check for updates now'.", "apps_page"))
    return w
