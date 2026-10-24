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

def build_speaker_lookup(people_rows: Iterable[tuple]) -> dict[str, set[str]]:
    """Map normalized full names and aliases to one or more person IDs."""
    lookup: dict[str, set[str]] = {}
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
                lookup.setdefault(key, set()).add(person_id)
    return lookup


def resolve_speaker(raw_name: str, lookup: dict[str, set[str]],
                    attendee_ids: set[str] | None = None) -> str | None:
    """Resolve a speaker conservatively, using attendees to break an alias tie."""
    key = normalize_name(raw_name)
    candidates = set(lookup.get(key, set()))
    if attendee_ids:
        attendee_candidates = candidates & attendee_ids
        if len(attendee_candidates) == 1:
            return next(iter(attendee_candidates))
    if len(candidates) == 1:
        return next(iter(candidates))
    # Conservative initial-based fallback for labels such as "A. Rao" or "A Rao".
    # Never guess if the initials are ambiguous.
    parts = key.split()
    if len(parts) >= 2:
        initials = " ".join([parts[0][0], *parts[1:]])
        candidates = {
            pid for alias, person_ids in lookup.items()
            if alias == key or alias.startswith(initials)
            for pid in person_ids
        }
        if attendee_ids:
            attendee_candidates = candidates & attendee_ids
            if len(attendee_candidates) == 1:
                return next(iter(attendee_candidates))
        if len(candidates) == 1:
            return next(iter(candidates))
    return None
