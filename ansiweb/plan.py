"""Build deploy_plan.json: everything the Ansible role needs, computed from the
configuration and the cache manifest. Only apps with a verified cached installer
are included, so a PC is never pointed at something that isn't on the server."""
from . import cache, paths, store


def pc_matches(pc: dict, targets: list) -> bool:
    for t in targets or ["all"]:
        if t == "all":
            return True
        if t.startswith("site:") and pc.get("site") == t[5:]:
            return True
        if t.startswith("group:") and t[6:] in pc.get("groups", []):
            return True
        if t.startswith("pc:") and pc.get("name") == t[3:]:
            return True
    return False


def build_plan(cfg: dict, manifest: dict) -> dict:
    base = store.software_url(cfg)
    apps, skipped = [], []
    for app in cfg.get("apps", []):
        if not app.get("enabled", True):
            continue
        entry = manifest.get(app["id"], {})
        f = entry.get("file")
        if not f or not (paths.APPS_DIR / f).exists():
            skipped.append({"id": app["id"], "reason": entry.get("error") or "not cached yet"})
            continue
        codes = set(entry.get("success_codes") or [0, 3010])
        codes.update(int(c) for c in app.get("extra_success_codes", []) or [])
        apps.append({
            "id": app["id"],
            "name": app["name"],
            "version": entry["version"],
            "file": f,
            "url": f"{base}/apps/{f}",
            "sha256": entry.get("sha256", ""),
            "arguments": (app.get("arguments") or "").strip(),
            "success_codes": sorted(codes),
            "detect_pattern": app["detect_pattern"],
            "mode": "pinned" if app.get("pinned") else "latest",
            "firefox_disable_updates": bool(app.get("firefox_disable_updates")),
            "targets": app.get("targets") or ["all"],
        })

    hosts = {}
    for pc in cfg.get("pcs", []):
        hosts[pc["name"]] = {"apps": [a["id"] for a in apps if pc_matches(pc, a["targets"])]}

    return {
        "generated": cache.now(),
        "software_url": base,
        "server_ip": (cfg.get("settings") or {}).get("server_ip", ""),
        "allow_reboot": bool((cfg.get("settings") or {}).get("allow_reboot")),
        "apps": apps,
        "skipped": skipped,
        "hosts": hosts,
    }


def write_plan(cfg: dict | None = None) -> dict:
    cfg = cfg or store.load()
    plan = build_plan(cfg, cache.load_manifest())
    store.write_json(paths.PLAN_FILE, plan)
    return plan
