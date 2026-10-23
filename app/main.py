
import os
import re
import tiktoken
from typing import Literal
from uuid import uuid4
from datetime import date
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.loaders import load_database, load_turns
from dotenv import load_dotenv
from google import genai
from google.genai import types


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

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


class GroundedAnswer(BaseModel):
    route: Literal["answer", "no_answer"]
    answer: str
    evidence_indices: list[int]


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def ask_gemini(question: str, evidence: list[dict]) -> GroundedAnswer:
    evidence_text = "\n".join(
        "[{}] {} {} {}: {}".format(
            index,
            turn["meeting_date"],
            turn["timestamp"],
            turn["speaker"],
            turn["text"],
        )
        for index, turn in enumerate(evidence)
    )
    prompt = f"""You are MeetingMind. Answer only from the supplied evidence.

Return route=no_answer when the evidence does not answer the question. For no_answer,
say the available meeting records do not cover the answer and suggest asking the meeting
owner. Use an empty evidence_indices list.

For route=answer, cite only the zero-based evidence indices that directly support the
answer. Treat settled language such as 'decision', 'we go with', or 'is set to' as a
decision. Treat 'maybe', 'what if', or 'let's revisit' as a proposal, not a decision.
When a later decision explicitly reverses an earlier decision, the later decision wins.
For decision-history questions, list decisions chronologically.

Question: {question}

Evidence:
{evidence_text}
"""
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(
            timeout=30000,
            client_args={"trust_env": False},
        ),
    )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=500,
            response_mime_type="application/json",
            response_schema=GroundedAnswer,
        ),
    )
    return GroundedAnswer.model_validate_json(response.text)


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

    if request.date_from and request.date_to and request.date_from > request.date_to:
        raise HTTPException(
            status_code=400,
            detail="date_from must be on or before date_to",
        )

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
        model_answer = ask_gemini(request.question, selected)
        evidence_indices = sorted(
            {
                index
                for index in model_answer.evidence_indices
                if 0 <= index < len(selected)
            }
        )

        if model_answer.route == "answer" and evidence_indices:
            answer = model_answer.answer
            route = "answer"
            citations = [
                {
                    "meeting_id": selected[index]["meeting_id"],
                    "timestamp": selected[index]["timestamp"],
                    "speaker": selected[index]["speaker"],
                }
                for index in evidence_indices
            ]
        else:
            answer = (
                "The available meeting records do not provide enough evidence "
                "to answer this question. Consider asking the meeting owner."
            )
            route = "no_answer"
            citations = []

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
