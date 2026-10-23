
import os
import re
import tiktoken
from uuid import uuid4
from datetime import date
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.loaders import load_database, load_turns


BASE_DIR = Path(__file__).resolve().parent.parent

ENCODING = tiktoken.get_encoding("cl100k_base")
MAX_EVIDENCE_TOKENS = 2600

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.turns = load_turns(
        BASE_DIR / os.getenv("TRANSCRIPTS_DIR", "data/transcripts"),
        BASE_DIR / os.getenv("MANIFEST_PATH", "data/manifest.json"),
        BASE_DIR / os.getenv("DB_PATH", "data/orbit.db"),
    )
    people, projects = load_database(
        BASE_DIR / os.getenv("DB_PATH", "data/orbit.db")
    )
    app.state.project_ids = {row[0] for row in projects}
    app.state.speaker_ids = {row[0] for row in people}
    yield

app = FastAPI(title="MeetingMind API", lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


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
    if request.project and request.project not in app.state.project_ids:
        raise HTTPException(status_code=400, detail="Unknown project ID")

    if request.speaker and request.speaker not in app.state.speaker_ids:
        raise HTTPException(status_code=400, detail="Unknown speaker ID")

    if any(
        phrase in request.question.lower()
        for phrase in ("write an email", "draft an email", "send an email", "summarize", "summary")
    ):
        return {
            "request_id": str(uuid4()),
            "answer": "I can only answer questions about what was said in the meetings.",
            "route": "out_of_scope",
            "citations": [],
            "filters": {
                "project": request.project,
                "speaker": request.speaker,
                "date_from": request.date_from.isoformat() if request.date_from else None,
                "date_to": request.date_to.isoformat() if request.date_to else None,
            },
            "reference_date": reference_date.isoformat(),
        }
            
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


    # Count how many filtered turns contain each word.
    # Words found in fewer turns will receive more weight.
    document_frequency = {}
    for turn in filtered:
        turn_words = words(turn["text"])
        for word in question_words & turn_words:
            document_frequency[word] = document_frequency.get(word, 0) + 1

    ranked = []
    question_phrase = " ".join(words(request.question))

    for turn in filtered:
        turn_words = words(turn["text"])
        matching_words = question_words & turn_words

        if not matching_words:
            continue

        # Give more points to specific words that occur less often.
        score = sum(
            1 / document_frequency[word]
            for word in matching_words
        )

        # Small bonus when the question's meaningful words all appear.
        if question_words and question_words.issubset(turn_words):
            score += 1

        ranked.append((score, turn))

    ranked.sort(
        key=lambda item: (
            -item[0],
            item[1]["meeting_date"],
            item[1]["start_seconds"],
        )
    )

    selected = []
    used_tokens = 0

    for _, turn in ranked:
        turn_tokens = len(ENCODING.encode(turn["text"]))
        if used_tokens + turn_tokens > MAX_EVIDENCE_TOKENS:
            continue

        selected.append(turn)
        used_tokens += turn_tokens

        if len(selected) == 5:
            break

    selected.sort(key=lambda t: (t["meeting_date"], t["start_seconds"]))    


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
                "timestamp": t["timestamp"],
                "speaker": t["speaker"],
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
