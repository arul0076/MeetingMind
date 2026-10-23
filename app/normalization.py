import json
import re
import unicodedata
from typing import Iterable

def normalize_name(value: str) -> str:
    """Normalize names for case- and accent-insensitive matching."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())

def build_speaker_lookup(people_rows: Iterable[tuple]) -> dict[str, str]:
    """Map full names and aliases to person IDs; exact normalized matches first."""
    lookup = {}
    for person_id, full_name, _role, _team, aliases_json in people_rows:
        names = [full_name]
        try:
            aliases = json.loads(aliases_json or "[]")
            if isinstance(aliases, list):
                names.extend(str(x) for x in aliases)
        except (json.JSONDecodeError, TypeError):
            pass
        for name in names:
            key = normalize_name(name)
            if key:
                lookup[key] = person_id
    return lookup

def resolve_speaker(raw_name: str, lookup: dict[str, str]) -> str | None:
    key = normalize_name(raw_name)
    if key in lookup:
        return lookup[key]
    # Conservative initial-based fallback for labels such as "A. Rao" or "A Rao".
    # Never guess if the initials are ambiguous.
    parts = key.split()
    if len(parts) >= 2:
        initials = " ".join([parts[0][0], *parts[1:]])
        candidates = {pid for alias, pid in lookup.items()
                      if alias == key or alias.startswith(initials)}
        if len(candidates) == 1:
            return next(iter(candidates))
    return None
