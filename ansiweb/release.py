"""Release notes, read from defaults/release_notes.yml.

One file is the source for the Release notes page and for the "what changed"
notice after an upgrade, so publishing a release only means adding an entry.
"""
import yaml

from . import __version__, paths

NOTES_FILE = paths.DEFAULTS_DIR / "release_notes.yml"
SECTIONS = [("added", "New"), ("changed", "Changed"), ("fixed", "Fixed"), ("removed", "Removed")]


def version_key(v: str):
    return [int(x) for x in str(v).replace("-", ".").split(".") if x.isdigit()]


def all_notes() -> list:
    """Every release, newest first."""
    try:
        notes = yaml.safe_load(NOTES_FILE.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        return []
    notes = [n for n in notes if isinstance(n, dict) and n.get("version")]
    notes.sort(key=lambda n: version_key(n["version"]), reverse=True)
    return notes


def current() -> dict:
    """The entry for the running version, if there is one."""
    for note in all_notes():
        if str(note["version"]) == __version__:
            return note
    return {}


def since(version: str) -> list:
    """Releases newer than the given version - what changed since an upgrade."""
    if not version:
        return []
    return [n for n in all_notes() if version_key(n["version"]) > version_key(version)]
