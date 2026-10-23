import json
import sqlite3
from pathlib import Path
from .parsers import parse_transcript
from .normalization import build_speaker_lookup, resolve_speaker

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
    turns = []
    seen_meetings = set()

    # Prefer the first manifest entry for a meeting ID; the assignment allows reading
    # either copy when a meeting has multiple transcript exports.
    for meeting in manifest:
        meeting_id = meeting["meeting_id"]
        if meeting_id in seen_meetings:
            continue
        seen_meetings.add(meeting_id)
        path = transcripts_dir / meeting["file"]
        segments = parse_transcript(path, meeting.get("format"))
        # Caption formats can split a single speaker's turn across adjacent cues.
        merged = []
        for seg in segments:
            speaker_id = resolve_speaker(seg["speaker_raw"], speaker_lookup)
            current = {**seg, "speaker": speaker_id}
            if (merged and merged[-1]["speaker"] == speaker_id and speaker_id is not None
                    and current["start_seconds"] - merged[-1]["last_end_seconds"] <= 2.0):
                merged[-1]["text"] = (merged[-1]["text"] + " " + current["text"]).strip()
                merged[-1]["last_end_seconds"] = current["start_seconds"]
            else:
                merged.append({**current, "last_end_seconds": current["start_seconds"]})
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
