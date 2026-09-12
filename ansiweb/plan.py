"""Build deploy_plan.json: everything the Ansible role needs, computed from the
configuration and the cache manifest. Only items with a file actually present on
the server are included, so a PC is never pointed at something that isn't there."""
import shlex

from . import cache, payloads, paths, store

# Fixed working folders on each PC. Windows paths are built here rather than in
# Jinja, where backslash escaping is easy to get wrong.
PC_CACHE = r"C:\ProgramData\AnsiWEB\cache"
PC_STATE = r"C:\ProgramData\AnsiWEB\state"


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


def split_arguments(text: str) -> list:
    """Split a script's arguments the way a shell would, keeping quoted values together."""
    try:
        return shlex.split(text.strip())
    except ValueError as exc:
        raise store.ValidationError(f"Could not read the script arguments ({exc}). Check the quotes.")


def build_plan(cfg: dict, manifest: dict) -> dict:
    base = store.software_url(cfg)
    apps, skipped = [], []
    for app in cfg.get("apps", []):
        if not app.get("enabled", True):
            continue
        entry = manifest.get(app["id"], {})
        f = entry.get("file")
        if not f or not (paths.APPS_DIR / f).exists():
            skipped.append({"kind": "apps", "id": app["id"], "name": app["name"],
                            "reason": entry.get("error") or "not cached yet"})
            continue
        codes = set(entry.get("success_codes") or [0, 3010])
        codes.update(int(c) for c in app.get("extra_success_codes", []) or [])
        apps.append({
            "id": app["id"],
            "name": app["name"],
            "version": entry["version"],
            "file": f,
            "url": f"{base}/apps/{f}",
            "win_file": f"{PC_CACHE}\\{f}",
            "sha256": entry.get("sha256", ""),
            "arguments": (app.get("arguments") or "").strip(),
            "success_codes": sorted(codes),
            "detect_pattern": app["detect_pattern"],
            "mode": "pinned" if app.get("pinned") else "latest",
            "firefox_disable_updates": bool(app.get("firefox_disable_updates")),
            "targets": app.get("targets") or ["all"],
        })

    # Uploaded payloads: drivers, scripts, registry files
    payload_sets = {}
    for kind in payloads.KINDS:
        items = []
        for e in cfg.get(kind, []):
            if not e.get("enabled", True):
                continue
            if not payloads.present(kind, e):
                skipped.append({"kind": kind, "id": e["id"], "name": e["name"],
                                "reason": "no file uploaded"})
                continue
            item = {
                "id": e["id"],
                "name": e["name"],
                "file": e["file"],
                "url": f"{base}/{kind}/{e['file']}",
                "sha256": e.get("sha256", ""),
                "run_mode": e.get("run_mode", "once"),
                # marker written on the PC, so "once" and "changed" work across runs
                "state_key": f"{kind}-{e['id']}-{(e.get('sha256') or '')[:12]}",
                "targets": e.get("targets") or ["all"],
            }
            item["win_file"] = f"{PC_CACHE}\\{e['file']}"
            item["win_state"] = f"{PC_STATE}\\{item['state_key']}.done"
            item["win_unpack"] = f"{PC_CACHE}\\{e['id']}"
            if kind == "scripts":
                shell = payloads.script_shell(e)
                launcher = (["powershell.exe", "-NoProfile", "-NonInteractive",
                             "-ExecutionPolicy", "Bypass", "-File", item["win_file"]]
                            if shell == "powershell" else ["cmd.exe", "/c", item["win_file"]])
                item.update({
                    "shell": shell,
                    "arguments": (e.get("arguments") or "").strip(),
                    # Built here so quoted arguments survive intact
                    "argv": launcher + split_arguments(e.get("arguments") or ""),
                    "success_codes": sorted({0} | {int(c) for c in e.get("success_codes", []) or []}),
                    "timeout": int(e.get("timeout") or 1800),
                    "reboot": bool(e.get("reboot")),
                })
            items.append(item)
        payload_sets[kind] = items

    tm = cfg.get("time") or {}
    clock = {
        "enabled": bool(tm.get("enabled")),
        "timezone": tm.get("timezone", ""),
        "ntp_servers": [str(x) for x in (tm.get("ntp_servers") or [])],
        "sync_now": bool(tm.get("sync_now", True)),
        "targets": tm.get("targets") or ["all"],
    }

    act = cfg.get("activation") or {}
    activation = {
        "enabled": bool(act.get("enabled")),
        "mode": act.get("mode", "mak"),
        "kms_host": act.get("kms_host", ""),
        "kms_port": int(act.get("kms_port") or 1688),
        "skip_if_activated": bool(act.get("skip_if_activated", True)),
        "targets": act.get("targets") or ["all"],
    }

    hosts = {}
    for pc in cfg.get("pcs", []):
        entry = {"apps": [a["id"] for a in apps if pc_matches(pc, a["targets"])]}
        for kind, items in payload_sets.items():
            entry[kind] = [i["id"] for i in items if pc_matches(pc, i["targets"])]
        entry["hostname"] = pc["name"] if pc.get("sync_hostname") else ""
        entry["activate"] = activation["enabled"] and pc_matches(pc, activation["targets"])
        entry["set_time"] = clock["enabled"] and pc_matches(pc, clock["targets"])
        hosts[pc["name"]] = entry

    return {
        "generated": cache.now(),
        "software_url": base,
        "server_ip": (cfg.get("settings") or {}).get("server_ip", ""),
        "allow_reboot": bool((cfg.get("settings") or {}).get("allow_reboot")),
        "pc_account": (cfg.get("settings") or {}).get("pc_account", "Admin"),
        "pc_cache": PC_CACHE,
        "pc_state": PC_STATE,
        "time": clock,
        "activation": activation,
        "apps": apps,
        "drivers": payload_sets["drivers"],
        "scripts": payload_sets["scripts"],
        "registry": payload_sets["registry"],
        "skipped": skipped,
        "hosts": hosts,
    }


def write_plan(cfg: dict | None = None) -> dict:
    cfg = cfg or store.load()
    plan = build_plan(cfg, cache.load_manifest())
    store.write_json(paths.PLAN_FILE, plan)
    return plan
