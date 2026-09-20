"""Reading what a driver package actually offers.

A vendor archive usually holds one .inf per printer family and each .inf lists
several models. Windows installs a printer by the model's exact name, so this
digs those names out of the package rather than making somebody open the .inf
and copy one by hand.
"""
import re
import zipfile

MAX_INF_BYTES = 2_000_000          # an .inf is tiny; ignore anything absurd
MAX_MODELS = 400

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_TOKEN = re.compile(r"%([^%]+)%")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1", errors="replace")


def _sections(text: str) -> dict:
    out, current = {}, None
    for line in text.splitlines():
        line = line.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        found = _SECTION.match(line)
        if found:
            current = found.group(1).strip().lower()
            out.setdefault(current, [])
        elif current:
            out[current].append(line.strip())
    return out


def _strings(sections: dict) -> dict:
    values = {}
    for name, lines in sections.items():
        # [Strings], and its localised variants such as [Strings.0409]
        if not name.startswith("strings"):
            continue
        for line in lines:
            if "=" in line:
                key, _, value = line.partition("=")
                values.setdefault(key.strip().lower(), value.strip().strip('"'))
    return values


def _expand(text: str, strings: dict) -> str:
    def swap(match):
        return strings.get(match.group(1).strip().lower(), match.group(0))
    # Two passes: a string can refer to another
    return _TOKEN.sub(swap, _TOKEN.sub(swap, text)).strip().strip('"')


def models_in_inf(raw: bytes, inf_name: str) -> list:
    """The printer (or device) models one .inf offers."""
    sections = _sections(_decode(raw))
    strings = _strings(sections)
    models = []

    # [Manufacturer] points at the sections that list the models
    targets = []
    for line in sections.get("manufacturer", []):
        _, _, right = line.partition("=")
        parts = [p.strip() for p in right.split(",") if p.strip()]
        if not parts:
            continue
        base = parts[0]
        targets.append(base.lower())
        # %Ricoh%=Ricoh,NTamd64.10.0  ->  [Ricoh.NTamd64.10.0]
        for suffix in parts[1:]:
            targets.append(f"{base}.{suffix}".lower())

    for target in targets:
        for line in sections.get(target, []):
            if "=" not in line:
                continue
            left, _, _ = line.partition("=")
            name = _expand(left, strings)
            if name and name not in models:
                models.append(name)
            if len(models) >= MAX_MODELS:
                break

    return [{"inf": inf_name, "model": m} for m in models]


def models_in_zip(path) -> dict:
    """{"models": [...], "infs": [...], "error": ""} for a driver package."""
    models, infs = [], []
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".inf")]
            # A vendor archive often ships the same files twice; one copy is enough
            seen = set()
            for name in sorted(names):
                info = z.getinfo(name)
                key = (name.rsplit("/", 1)[-1].lower(), info.file_size)
                if key in seen or info.file_size > MAX_INF_BYTES:
                    continue
                seen.add(key)
                short = name.rsplit("/", 1)[-1]
                infs.append(name)
                models.extend(models_in_inf(z.read(name), short))
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        return {"models": [], "infs": [], "error": f"Could not read the package: {exc}"}

    # Same model offered by several .inf files: keep the first
    unique, seen_models = [], set()
    for entry in models:
        if entry["model"].lower() in seen_models:
            continue
        seen_models.add(entry["model"].lower())
        unique.append(entry)
    unique.sort(key=lambda e: e["model"].lower())
    return {"models": unique[:MAX_MODELS], "infs": infs, "error": ""}


# ---- what a printer package appears to support --------------------------------
# The detail of a printer's features lives in its GPD/PPD data, usually inside a
# .cab, so this is a hint rather than an inventory: it looks for the names of
# common features in the package's text files. Windows is the authority - the
# settings below are applied with Set-PrintConfiguration, which fails plainly if
# a printer does not support one.
FEATURE_WORDS = {
    "duplex": ("duplex", "two-sided", "2-sided"),
    "colour": ("color", "colour"),
    "staple": ("staple", "stapling", "finisher"),
    "punch": ("punch",),
    "collate": ("collate",),
    "trays": ("tray", "cassette", "paperfeed"),
}
FEATURE_LABELS = {
    "duplex": "Two-sided printing",
    "colour": "Colour",
    "staple": "Stapling or finisher",
    "punch": "Hole punch",
    "collate": "Collate",
    "trays": "Extra paper trays",
}
_TEXT_SUFFIXES = (".inf", ".dsc", ".gpd", ".ppd", ".txt", ".rcf")


def features_in_zip(path, limit_bytes: int = 8_000_000) -> list:
    """Common printer features the package mentions, as a hint for the operator."""
    found = set()
    read = 0
    try:
        with zipfile.ZipFile(path) as z:
            for info in sorted(z.infolist(), key=lambda i: i.filename.lower()):
                if info.is_dir() or not info.filename.lower().endswith(_TEXT_SUFFIXES):
                    continue
                if info.file_size > MAX_INF_BYTES or read > limit_bytes:
                    continue
                read += info.file_size
                text = _decode(z.read(info)).lower()
                for key, words in FEATURE_WORDS.items():
                    if key in found:
                        continue
                    if any(w in text for w in words):
                        found.add(key)
                if len(found) == len(FEATURE_WORDS):
                    break
    except (zipfile.BadZipFile, OSError, KeyError):
        return []
    return [k for k in FEATURE_WORDS if k in found]
