#!/usr/bin/env python3
"""Publish the current version's release notes as a GitHub Release.

  GITHUB_TOKEN=... python3 tools/publish_release.py [version]

Reads ansiweb/defaults/release_notes.yml, tags the current commit and creates
the release. Run it after bumping __version__ and committing.
"""
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ansiweb import __version__, release   # noqa: E402

REPO = os.environ.get("ANSIWEB_REPO", "mzuogha/AnsiWEB")


def body(note: dict) -> str:
    out = [" ".join(str(note.get("summary", "")).split())]
    for key, label in release.SECTIONS:
        if note.get(key):
            out.append(f"\n### {label}\n")
            out += ["- " + " ".join(str(i).split()) for i in note[key]]
    out.append("\nUpgrade with `git pull && sudo ./install.sh`; your configuration and cache are kept.")
    return "\n".join(out)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("Set GITHUB_TOKEN to a token that can create releases.")
        return 1
    version = sys.argv[1] if len(sys.argv) > 1 else __version__
    note = next((n for n in release.all_notes() if n["version"] == version), None)
    if not note:
        print(f"No release notes entry for {version}. Add one first.")
        return 1
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    payload = {"tag_name": f"v{version}", "target_commitish": sha, "name": f"AnsiWEB {version}",
               "body": body(note), "draft": False, "prerelease": False}
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases", method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "application/vnd.github+json",
                                          "User-Agent": "AnsiWEB-release"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Published", json.load(r)["html_url"])
    except urllib.error.HTTPError as exc:
        print(f"Failed: HTTP {exc.code} {exc.read().decode()[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
