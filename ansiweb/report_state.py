"""Working out which PCs have gone quiet.

A PC writes a report at the end of every deployment. A PC that has not reported
for a while is either switched off, off the network, or gone - either way it is
not being managed, which is worth surfacing rather than leaving silent.
"""
import datetime as dt


def parse_time(text: str):
    try:
        return dt.datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def age_days(report: dict, now: dt.datetime | None = None):
    """Days since this PC last reported, or None if it never has."""
    when = parse_time((report or {}).get("time", ""))
    if not when:
        return None
    return max((now or dt.datetime.now()) - when, dt.timedelta(0)).days


def classify(report: dict, threshold_days: int, now: dt.datetime | None = None) -> dict:
    """Return {state, age_days, label} for one PC.

    states: never (no report yet), ok (reported recently), stale (too long ago)
    """
    age = age_days(report, now)
    if age is None:
        return {"state": "never", "age_days": None, "label": "never reported"}
    if age >= max(int(threshold_days), 1):
        return {"state": "stale", "age_days": age,
                "label": f"no report for {age} day{'s' if age != 1 else ''}"}
    return {"state": "ok", "age_days": age,
            "label": "reported today" if age == 0 else f"reported {age} day{'s' if age != 1 else ''} ago"}


def summarise(pcs: list, reports: dict, threshold_days: int, now: dt.datetime | None = None) -> dict:
    """Counts across every PC, for the dashboard."""
    out = {"ok": 0, "stale": 0, "never": 0, "stale_names": [], "never_names": []}
    for pc in pcs:
        info = classify(reports.get(pc["name"], {}), threshold_days, now)
        out[info["state"]] += 1
        if info["state"] == "stale":
            out["stale_names"].append(pc["name"])
        elif info["state"] == "never":
            out["never_names"].append(pc["name"])
    out["needs_attention"] = out["stale"] + out["never"]
    return out
