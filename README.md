# MeetingMind

MeetingMind is a FastAPI service for asking grounded questions about team meetings. It reads the supplied transcripts and database at startup, retrieves relevant turns, asks Gemini to answer from that evidence only, and returns verifiable source citations.

It is built for the MeetingMind Junior AI Developer assessment. No transcript fact is hardcoded, so the same code works when the assessment data is replaced with another folder that has the same structure.

## What it does

- Answers questions such as “What pricing decision was made?” and “How did the Pro tier price change?”
- Returns citations containing `meeting_id`, turn start `timestamp`, and `speaker` ID.
- Returns `no_answer` instead of guessing when the records do not cover a question.
- Returns `out_of_scope` for non-meeting requests such as drafting an email.
- Supports project, speaker, date range, and `as_of` filters.

## Technology

| Tool | Purpose |
| --- | --- |
| Python 3.10+ | Application language |
| FastAPI | Web API and interactive Swagger documentation |
| SQLite | Supplied people and projects database |
| Pydantic | Request and Gemini response validation |
| Google Gemini API | Evidence-grounded answer generation |
| `google-genai` | Gemini Python SDK |
| `tiktoken` | Required `cl100k_base` token counting |
| `python-dotenv` | Loads local environment settings and API key |

## Project structure

```text
meetingmind/
├── app/
│   ├── main.py            # API, validation, retrieval, Gemini call
│   ├── parsers.py         # VTT, SRT, TXT, JSON parsers
│   ├── loaders.py         # DB/manifest loading and turn construction
│   ├── normalization.py   # Speaker and alias matching
│   └── __init__.py
├── data/                  # Local assessment files; intentionally not committed
├── .env.example           # Safe configuration template
├── requirements.txt
└── README.md
```

## Architecture and data flow

### 1. Startup loading

Before accepting requests, FastAPI reads `manifest.json`, `orbit.db`, and every transcript named by the manifest. Normalized turns and valid project/speaker IDs are retained in application state.

### 2. Transcript parsing and speaker resolution

The project has a parser for each source format: VTT, SRT, TXT, and JSON. Timed VTT, SRT, and JSON captions preserve start and end times. Adjacent captions are merged only when they are from the same resolved speaker and the next cue begins within two seconds of the previous cue ending. TXT has no end time, so its lines are not merged.

Speaker text is normalized by lowercasing and removing accents before matching `full_name` and aliases from SQLite. If a meeting has two exports, only the first manifest entry for that `meeting_id` is used, preventing duplicate retrieval and citations.

### 3. Request validation and filtering

The service validates missing/blank questions, malformed dates, reversed date ranges, and unknown project or speaker IDs. Invalid requests return HTTP 400.

Python applies all filters before retrieval and before Gemini sees any evidence:

- `project`: project ID to include
- `speaker`: person ID to include
- `date_from`, `date_to`: inclusive meeting-date range
- `as_of`: excludes meetings held after this reference date

When `as_of` is omitted, `APP_TODAY` defaults to `2026-07-01`; the computer clock is not used.

### 4. Retrieval

MeetingMind uses a transparent keyword retriever. It removes common question words, finds overlapping terms in filtered transcript turns, and weights rarer matching words more strongly. Selected turns are then sorted by meeting date and timestamp so Gemini sees decision history chronologically.

### 5. Token limit

The assessment limit is fewer than 3,000 transcript tokens per LLM call. `tiktoken` with `cl100k_base` counts selected turn text, and MeetingMind stops at 2,600 tokens. This leaves headroom while ensuring the transcript evidence stays below the limit.

### 6. Grounded Gemini answer

Gemini receives only the question and numbered evidence turns. The prompt requires it to:

- use only the supplied evidence;
- distinguish proposals from settled decisions;
- prefer a later decision if it explicitly reverses an earlier one;
- list decision history chronologically;
- return `no_answer` when evidence is insufficient.

Gemini returns structured JSON containing `route`, `answer`, and `evidence_indices`. Python validates these indices and constructs final citations from trusted local metadata. The model never creates meeting IDs, timestamps, or speaker IDs itself.

## Setup

### Prerequisites

- Python 3.10 or newer
- A Gemini API key (the Gemini free tier is suitable for this project)
- The assessment `data/` folder available locally

### Install on Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env` and add your secret key:

```text
GEMINI_API_KEY=your_key_here
```

Never commit `.env`, real API keys, or `data/`.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRANSCRIPTS_DIR` | `./data/transcripts` | Transcript directory |
| `MANIFEST_PATH` | `./data/manifest.json` | Transcript metadata |
| `DB_PATH` | `./data/orbit.db` | SQLite database |
| `APP_TODAY` | `2026-07-01` | Default reference date |
| `LLM_PROVIDER` | `gemini` | Provider setting |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Model used for answer generation |
| `GEMINI_API_KEY` | none | Gemini API key |

### Run

```powershell
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for Swagger UI.

## API

### Health

```http
GET /health
```

```json
{"status": "ok"}
```

### Ask a question

```http
POST /ask
Content-Type: application/json
```

```json
{
  "question": "What was the mobile launch date?",
  "project": "mobile-app-launch",
  "as_of": "2026-04-01"
}
```

Only `question` is required. `project`, `speaker`, `date_from`, `date_to`, and `as_of` may be omitted or set to `null`.

Example answer:

```json
{
  "request_id": "example-id",
  "answer": "As of 1 April 2026, the mobile launch was set for 6 May 2026.",
  "route": "answer",
  "citations": [
    {"meeting_id": "m002", "timestamp": "00:03:38", "speaker": "p01"}
  ],
  "filters": {
    "project": "mobile-app-launch",
    "speaker": null,
    "date_from": null,
    "date_to": null
  },
  "reference_date": "2026-04-01"
}
```

### Routes and errors

| Result | HTTP status | Citations | Meaning |
| --- | --- | --- | --- |
| `answer` | 200 | One or more | Evidence supports an answer |
| `no_answer` | 200 | Empty | Allowed records do not answer the question |
| `out_of_scope` | 200 | Empty | Request is not about meeting records |
| Invalid request | 400 | N/A | Invalid JSON, invalid date, blank question, reversed date range, or unknown ID |

## Tests performed

| Scenario | Verified behavior |
| --- | --- |
| Latest pricing decision | Returned the later $49 Pro-tier reversal with supporting citations |
| Pricing history | Returned the March $59 decision, then the May $49 reversal |
| `as_of: 2026-04-01` | Excluded the May reversal and returned only the March decision |
| Unknown project | HTTP 400 |
| Invalid date range | HTTP 400 |
| Office parking policy | `no_answer` and no citations |
| Draft an email | `out_of_scope` and no citations |
| `/health` | HTTP 200 and `{"status": "ok"}` |

## Challenges and solutions

### Incomplete caption evidence

Initial VTT evidence ended mid-sentence because only cue start times were stored. The loader compared the next caption start with the previous caption start rather than its end. Adding `end_seconds` to timed parsers and comparing against the prior end time correctly merged sentence fragments.

### Stable citations after merging

Merging captions changes later internal turn numbers. Tests therefore verify source text and timestamps, rather than depending on unstable intermediate turn IDs.

### Safe structured output

Plain text model output could return malformed JSON or invented citation metadata. Gemini structured output returns evidence indexes instead. Python maps them to local turns, ensuring citations remain trustworthy.

### Free-tier model availability

An earlier Gemini model was unavailable to new API users. The configured model is `gemini-3.5-flash-lite`.

### Cost and token control

No Gemini call occurs for out-of-scope requests or requests with no retrieved evidence. For answers, only ranked evidence is sent, and transcript text is capped at 2,600 tokens.

## Limitations and future improvements

- Retrieval is keyword-based; embedding or hybrid retrieval would improve recall for unusual phrasing.
- Out-of-scope detection uses a small phrase list; an intent classifier would be more complete.
- Gemini free-tier rate limits and availability are controlled by Google.
- The current implementation uses Gemini. Supporting OpenAI and Anthropic behind a shared provider interface would make `LLM_PROVIDER` fully interchangeable.
- Automated tests are not included. Adding `pytest` coverage for parsers, filters, routes, and token limits would improve long-term maintainability.

## Security and submission notes

- `.env`, `.venv/`, cache files, and `data/` are ignored by Git.
- No real API key or assessment transcript data is included in this repository.
- All meeting facts come from runtime-loaded source files, never from hardcoded application facts.
