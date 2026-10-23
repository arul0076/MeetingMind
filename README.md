# MeetingMind

MeetingMind is a FastAPI service that answers questions about meeting transcripts and returns source citations.

## Setup

Python 3.10+ is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `GEMINI_API_KEY` in `.env`. Never commit `.env`, API keys, or `data/`.

Start the service:

```powershell
python -m uvicorn app.main:app --reload
```

The API runs on `http://127.0.0.1:8000`; interactive documentation is at `/docs`.

## Endpoints

`GET /health` returns `{"status":"ok"}`.

`POST /ask` accepts `question` plus optional `project`, `speaker`, `date_from`, `date_to`, and `as_of` filters.

```json
{
  "question": "What was the mobile launch date?",
  "project": "mobile-app-launch",
  "as_of": "2026-04-01"
}
```

## How it works

1. At startup, the service reads the manifest, SQLite database, and transcript files.
2. It parses VTT, SRT, TXT, and JSON files; adjacent same-speaker timed captions are merged into turns.
3. It resolves speaker names and aliases case- and accent-insensitively, then ignores duplicate exports for the same meeting ID.
4. Python validates and applies every filter before retrieval.
5. Keyword retrieval ranks relevant turns. Evidence is capped at 2,600 transcript tokens with `tiktoken` and `cl100k_base`, below the 3,000-token limit.
6. Gemini receives only selected, chronological evidence and returns JSON containing a route, answer, and evidence indices. Python turns those indices into citations, so the model never invents citation metadata.

The prompt requires evidence-only answers, distinguishes proposals from decisions, selects explicit later reversals, and lists decision history chronologically.

## Validation and routes

- Invalid JSON, blank/missing questions, invalid dates, reversed date ranges, and unknown project/speaker IDs return HTTP 400.
- Questions without evidence return HTTP 200 with `route: "no_answer"` and no citations.
- Email-writing and summary requests return HTTP 200 with `route: "out_of_scope"` and no citations.
- Answer routes always include one or more citations containing only `meeting_id`, `timestamp`, and `speaker`.

## Assumptions and limitations

- The first manifest entry is used when a meeting has duplicate transcript exports.
- Adjacent same-speaker captions are merged when the following caption starts within two seconds of the previous caption's end.
- Retrieval is keyword-based, so unusual wording may miss relevant evidence. An embedding retriever would improve recall.
- Gemini free-tier availability and rate limits are controlled by Google. The application uses one model call only when retrieved evidence exists.

## Example questions tested

- “What pricing decision was made?” for `pricing-v2` returns the later $49 reversal with March and May citations.
- “How did the Pro tier price change?” returns the $59 decision followed by the $49 reversal.
- The same question with `as_of: "2026-04-01"` returns only the March $59 decision.
- “What is the office parking policy?” returns `no_answer` with no citations.
