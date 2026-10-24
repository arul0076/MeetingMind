import json
import logging
import sqlite3
from pathlib import Path
from .parsers import parse_transcript
from .normalization import build_speaker_lookup, resolve_speaker


logger = logging.getLogger(__name__)

def load_database(db_path: Path):
    with sqlite3.connect(db_path) as conn:
        people = conn.execute(
            "SELECT person_id, full_name, role, team, aliases FROM people"
        ).fetchall()
        projects = conn.execute(
            "SELECT project_id, name, status, started_on FROM projects"
        ).fetchall()
    return people, projects

def load_turns(transcripts_dir: Path, manifest_path: Path, db_path: Path) -> list[dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    people, _projects = load_database(db_path)
    speaker_lookup = build_speaker_lookup(people)
    person_ids = {str(person[0]) for person in people}
    turns = []
    loaded_meetings = set()

    # Prefer the first *successfully parsed* manifest entry for a meeting ID.  This
    # lets a duplicate export act as a fallback when its earlier counterpart is bad.
    for meeting in manifest:
        meeting_id = meeting["meeting_id"]
        if meeting_id in loaded_meetings:
            continue
        path = transcripts_dir / meeting["file"]
        try:
            segments = parse_transcript(path, meeting.get("format"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            logger.warning("Could not parse transcript %s: %s", path, error)
            continue
        loaded_meetings.add(meeting_id)

        attendee_ids = set()
        for attendee in meeting.get("attendees", []) or []:
            attendee_text = str(attendee)
            if attendee_text in person_ids:
                attendee_ids.add(attendee_text)
                continue
            resolved_attendee = resolve_speaker(attendee_text, speaker_lookup)
            if resolved_attendee:
                attendee_ids.add(resolved_attendee)

        # Caption formats can split a single speaker's turn across adjacent cues.
        merged = []
        for seg in segments:
            speaker_id = resolve_speaker(
                seg["speaker_raw"], speaker_lookup, attendee_ids
            )
            if speaker_id is None:
                logger.warning(
                    "Unresolved or ambiguous speaker %r in meeting %s",
                    seg["speaker_raw"], meeting_id,
                )
            current = {**seg, "speaker": speaker_id}
            previous_end = merged[-1]["last_end_seconds"] if merged else None

            if (
                merged
                and merged[-1]["speaker"] == speaker_id
                and speaker_id is not None
                and "end_seconds" in merged[-1]
                and "end_seconds" in current
                and previous_end is not None
                and current["start_seconds"] - previous_end <= 2.0):
                merged[-1]["text"] = (
                    merged[-1]["text"] + " " + current["text"]
                ).strip()
                merged[-1]["last_end_seconds"] = current.get(
                    "end_seconds", current["start_seconds"]
                )
            else:
                merged.append({
                    **current,
                    "last_end_seconds": current.get(
                        "end_seconds", current["start_seconds"]
                    ),
                })
        for idx, seg in enumerate(merged):
            turns.append({
                "turn_id": f"{meeting_id}-{idx+1}",
                "meeting_id": meeting_id,
                "meeting_date": meeting["date"],
                "meeting_title": meeting["title"],
                "project_id": meeting["project"],
                "timestamp": seg["timestamp"],
                "start_seconds": seg["start_seconds"],
                "speaker": seg["speaker"],
                "speaker_raw": seg["speaker_raw"],
                "text": seg["text"],
            })
    turns.sort(key=lambda x: (x["meeting_date"], x["start_seconds"]))
    return turns
