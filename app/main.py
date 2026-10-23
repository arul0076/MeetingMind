
import os
import re
from uuid import uuid4
from datetime import date
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.loaders import load_turns


BASE_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.turns = load_turns(
        BASE_DIR / os.getenv("TRANSCRIPTS_DIR", "data/transcripts"),
        BASE_DIR / os.getenv("MANIFEST_PATH", "data/manifest.json"),
        BASE_DIR / os.getenv("DB_PATH", "data/orbit.db"),
    )
    yield


app = FastAPI(title="MeetingMind API", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    project: str | None = None
    speaker: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    as_of: date | None = None


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask")
def ask(request: AskRequest):
    reference_date = request.as_of or date.fromisoformat(
        os.getenv("APP_TODAY", "2026-07-01")
    )

    turns = app.state.turns
    question_words = words(request.question) - {
        "what", "when", "where", "who", "why", "how",
        "the", "a", "an", "is", "was", "were", "did",
        "do", "does", "to", "of", "in", "on", "for",
        "and", "or", "we", "they", "it", "this", "that",
    }

    filtered = [
        turn for turn in turns
        if (not request.project or turn["project_id"] == request.project)
        and (not request.speaker or turn["speaker"] == request.speaker)
        and (not request.date_from or turn["meeting_date"] >= request.date_from.isoformat())
        and (not request.date_to or turn["meeting_date"] <= request.date_to.isoformat())
        and turn["meeting_date"] <= reference_date.isoformat()
    ]

    ranked = []
    for turn in filtered:
        overlap = len(question_words & words(turn["text"]))
        if overlap:
            ranked.append((overlap, turn))

    ranked.sort(
        key=lambda item: (
            -item[0],
            item[1]["meeting_date"],
            item[1]["start_seconds"],
        )
    )
    selected = [turn for _, turn in ranked[:5]]

    if not selected:
        answer = (
            "The available meeting records do not provide enough evidence "
            "to answer this question. Consider asking the meeting owner."
        )
        route = "no_answer"
        citations = []
    else:
        answer = (
            "I found potentially relevant meeting statements, but this "
            "basic retrieval version does not yet synthesize an answer:\n"
            + "\n".join(
                f'{t["meeting_date"]} — {t["speaker_raw"]}: {t["text"]}'
                for t in selected
            )
        )
        route = "answer"
        citations = [
            {
                "meeting_id": t["meeting_id"],
                "meeting_date": t["meeting_date"],
                "meeting_title": t["meeting_title"],
                "timestamp": t["timestamp"],
                "speaker": t["speaker"],
                "turn_id": t["turn_id"],
                "text": t["text"],
            }
            for t in selected
        ]

    return {
        "request_id": str(uuid4()),
        "answer": answer,
        "route": route,
        "citations": citations,
        "filters": {
            "project": request.project,
            "speaker": request.speaker,
            "date_from": request.date_from.isoformat() if request.date_from else None,
            "date_to": request.date_to.isoformat() if request.date_to else None,
        },
        "reference_date": reference_date.isoformat(),
    }
