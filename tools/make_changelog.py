#!/usr/bin/env python3
"""Regenerate CHANGELOG.md from ansiweb/defaults/release_notes.yml.

Run after adding a release entry:  python3 tools/make_changelog.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ansiweb import release   # noqa: E402

HEADER = """# Changelog

All notable changes to AnsiWEB. This file is generated from
`ansiweb/defaults/release_notes.yml`, which the Release notes page in the web
interface also renders - edit that file, then run `python3 tools/make_changelog.py`.
"""


def main() -> int:
    notes = release.all_notes()
    if not notes:
        print("No release notes found.")
        return 1
    out = [HEADER]
    for note in notes:
        out.append(f"\n## {note['version']} - {note.get('date', '')}\n")
        if note.get("summary"):
            out.append(" ".join(str(note["summary"]).split()) + "\n")
        for key, label in release.SECTIONS:
            if note.get(key):
                out.append(f"\n### {label}\n")
                for item in note[key]:
                    out.append("- " + " ".join(str(item).split()))
                out.append("")
    text = "\n".join(out).rstrip() + "\n"
    target = pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    target.write_text(text, encoding="utf-8")
    print(f"Wrote {target} with {len(notes)} release(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Publishing a release:
#   1. add an entry at the top of ansiweb/defaults/release_notes.yml
#   2. bump __version__ in ansiweb/__init__.py to match
#   3. run this script to refresh CHANGELOG.md
#   4. commit both, and the notes appear in the web interface after an upgrade
