
import logging
import os
import re
import tiktoken
from math import ceil
from typing import Literal
from uuid import uuid4
from datetime import date
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.loaders import load_database, load_turns
from dotenv import load_dotenv
from google import genai
from google.genai import types


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)
MAX_EVIDENCE_TOKENS = 2600
try:
    ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception as error:
    ENCODING = None
    logger.warning("Could not load cl100k_base; using a conservative estimate: %s", error)

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
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider != "gemini":
        logger.warning("LLM_PROVIDER=%s is unsupported; using Gemini", provider)
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

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class GroundedAnswer(BaseModel):
    route: Literal["answer", "no_answer", "out_of_scope"]
    answer: str
    evidence_indices: list[int]


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def count_tokens(text: str) -> int:
    """Count evidence tokens with the required tokenizer, with a safe fallback."""
    if ENCODING is not None:
        return len(ENCODING.encode(text))
    return max(1, ceil(len(text) / 3))


def format_evidence_turn(index: int, turn: dict) -> str:
    return "[{}] {} {} {}: {}".format(
        index,
        turn["meeting_date"],
        turn["timestamp"],
        turn["speaker"],
        turn["text"],
    )


def ask_gemini(question: str, evidence: list[dict]) -> GroundedAnswer | None:
    evidence_text = "\n".join(
        format_evidence_turn(index, turn) for index, turn in enumerate(evidence)
    ) or "(no turns available)"
    prompt = f"""You are MeetingMind. Answer only from the supplied evidence.

Return JSON with route, answer, and evidence_indices.

Route definitions:
- out_of_scope: not about what was said in the meetings, including email writing,
  summaries, translation, general knowledge, chit-chat, or questions about this AI.
- no_answer: about the meetings, but the evidence does not cover it, or a topic was
  only proposed and was never agreed. Say the records do not cover the answer and
  suggest asking the meeting owner. Use an empty evidence_indices list.
- answer: the evidence answers the question. Cite only supporting evidence indices.

For route=answer, cite only the zero-based evidence indices that directly support the
answer. Treat settled language such as 'decision', 'we go with', or 'is set to' as a
decision. Treat 'maybe', 'what if', or 'let's revisit' as a proposal, not a decision.
When a later decision explicitly reverses an earlier decision, the later decision wins.
For decision-history questions, list decisions chronologically.

Question: {question}

Evidence:
{evidence_text}
"""
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        logger.error("No GOOGLE_API_KEY or GEMINI_API_KEY is configured")
        return None

    try:
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=25000),
        )
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=500,
                response_mime_type="application/json",
                response_schema=GroundedAnswer,
            ),
        )
        if not response.text:
            raise ValueError("Gemini returned an empty response")
        return GroundedAnswer.model_validate_json(response.text)
    except Exception as error:
        logger.exception("Gemini request failed: %s", error)
        return None


@app.get("/health")
def health():
    return {"status": "ok"}


def response_payload(request: AskRequest, reference_date: date, answer: str, route: str,
                     citations: list[dict] | None = None) -> dict:
    return {
        "request_id": str(uuid4()),
        "answer": answer,
        "route": route,
        "citations": citations or [],
        "filters": {
            "date_from": request.date_from.isoformat() if request.date_from else None,
            "date_to": request.date_to.isoformat() if request.date_to else None,
            "project": request.project,
            "speaker": request.speaker,
        },
        "reference_date": reference_date.isoformat(),
    }


def no_answer(request: AskRequest, reference_date: date, message: str | None = None) -> dict:
    return response_payload(
        request,
        reference_date,
        message or "The available meeting records do not cover this answer. Please ask the meeting owner.",
        "no_answer",
    )


def rank_turns(turns: list[dict], question: str) -> list[dict]:
    question_words = words(question) - {
        "what", "when", "where", "who", "why", "how", "the", "a", "an",
        "is", "was", "were", "did", "do", "does", "to", "of", "in", "on",
        "for", "and", "or", "we", "they", "it", "this", "that",
    }
    document_frequency: dict[str, int] = {}
    for turn in turns:
        for word in question_words & words(turn["text"]):
            document_frequency[word] = document_frequency.get(word, 0) + 1

    ranked: list[tuple[float, dict]] = []
    decision_words = ("decision", "go with", "moves to", "set to", "maybe", "revisit")
    for turn in turns:
        matching_words = question_words & words(turn["text"])
        if not matching_words:
            continue
        score = sum(1 / document_frequency[word] for word in matching_words)
        if question_words and question_words.issubset(words(turn["text"])):
            score += 1
        if any(phrase in turn["text"].lower() for phrase in decision_words):
            score *= 1.25
        ranked.append((score, turn))

    ranked.sort(key=lambda item: (-item[0], item[1]["meeting_date"], item[1]["start_seconds"]))
    return [turn for _, turn in ranked]


def select_evidence(filtered: list[dict], question: str) -> list[dict]:
    ranked = rank_turns(filtered, question)
    by_meeting: dict[str, list[dict]] = {}
    for turn in filtered:
        by_meeting.setdefault(turn["meeting_id"], []).append(turn)
    for turns in by_meeting.values():
        turns.sort(key=lambda turn: turn["start_seconds"])

    candidates: list[dict] = []
    per_meeting: dict[str, int] = {}
    seen_turns: set[str] = set()

    def add_candidate(turn: dict) -> None:
        if turn["turn_id"] not in seen_turns:
            candidates.append(turn)
            seen_turns.add(turn["turn_id"])

    # Preserve coverage: start with up to two strong matches per meeting.
    for turn in ranked:
        meeting_id = turn["meeting_id"]
        if per_meeting.get(meeting_id, 0) >= 2:
            continue
        add_candidate(turn)
        per_meeting[meeting_id] = per_meeting.get(meeting_id, 0) + 1
        meeting_turns = by_meeting[meeting_id]
        position = meeting_turns.index(turn)
        if position + 1 < len(meeting_turns):
            add_candidate(meeting_turns[position + 1])

    # Fill remaining room by overall score.
    for turn in ranked:
        add_candidate(turn)

    selected: list[dict] = []
    used_tokens = 0
    for turn in sorted(candidates, key=lambda item: (item["meeting_date"], item["start_seconds"])):
        evidence_line = format_evidence_turn(len(selected), turn)
        line_tokens = count_tokens(evidence_line)
        if used_tokens + line_tokens > MAX_EVIDENCE_TOKENS:
            continue
        selected.append(turn)
        used_tokens += line_tokens

    evidence_text = "\n".join(format_evidence_turn(index, turn) for index, turn in enumerate(selected))
    assert count_tokens(evidence_text) <= MAX_EVIDENCE_TOKENS
    logger.info("Selected %s evidence turns using %s transcript tokens", len(selected), count_tokens(evidence_text))
    return selected


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
        return no_answer(request, reference_date)

    if any(
        phrase in request.question.lower()
        for phrase in (
            "write an email", "draft an email", "send an email", "summarize",
            "summary", "translate",
        )
    ):
        return response_payload(
            request,
            reference_date,
            "I can only answer questions about what was said in the meetings.",
            "out_of_scope",
        )

    filtered = [
        turn for turn in app.state.turns
        if (not request.project or turn["project_id"] == request.project)
        and (not request.speaker or turn["speaker"] == request.speaker)
        and (not request.date_from or turn["meeting_date"] >= request.date_from.isoformat())
        and (not request.date_to or turn["meeting_date"] <= request.date_to.isoformat())
        and turn["meeting_date"] <= reference_date.isoformat()
    ]
    selected = select_evidence(filtered, request.question)
    model_answer = ask_gemini(request.question, selected)
    if model_answer is None:
        return no_answer(request, reference_date, "The language model could not be reached. Please try again.")

    if model_answer.route == "out_of_scope":
        return response_payload(request, reference_date, model_answer.answer, "out_of_scope")
    if model_answer.route != "answer":
        return no_answer(request, reference_date, model_answer.answer)

    valid_indices = sorted({
        index for index in model_answer.evidence_indices
        if 0 <= index < len(selected) and selected[index]["speaker"] is not None
    })
    if not valid_indices:
        return no_answer(request, reference_date)

    citations = [
        {
            "meeting_id": selected[index]["meeting_id"],
            "timestamp": selected[index]["timestamp"],
            "speaker": selected[index]["speaker"],
        }
        for index in valid_indices
    ]
    return response_payload(request, reference_date, model_answer.answer, "answer", citations)
