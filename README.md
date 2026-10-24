# MeetingMind

MeetingMind is a FastAPI service that answers questions about meeting transcripts and returns source citations. It was built for the MeetingMind Junior AI Developer assessment.

The service loads the supplied transcript files and SQLite database at startup, retrieves relevant meeting turns, asks Gemini to answer only from that evidence, and builds citations in Python. No meeting fact is hardcoded, so replacement assessment data with the same layout can be used without changing the application.

## Features

- Parses VTT, SRT, TXT, and JSON transcript exports.
- Resolves speaker names and aliases case- and accent-insensitively.
- Handles duplicate meeting exports, using the first successfully parsed copy.
- Merges only adjacent timed captions from the same resolved speaker.
- Filters by project, speaker, date range, and an `as_of` reference date before retrieval.
- Returns `answer`, `no_answer`, or `out_of_scope` with grounded citations.
- Sends fewer than 3,000 transcript tokens to Gemini per request.

## Architecture

```text
HTTP request
  -> validate request and filters
  -> load eligible transcript turns
  -> keyword ranking with per-meeting coverage
  -> token-capped evidence block (maximum 2,600 tokens)
  -> Gemini structured response
  -> Python validates evidence indexes and creates citations
  -> JSON response
```

### Startup and data loading

`app/loaders.py` reads `manifest.json`, `orbit.db`, and the transcript files. Parsed content becomes normalized turns with meeting metadata, a start timestamp, resolved speaker ID, and text. A duplicate manifest entry for the same meeting ID is skipped only after an earlier export has parsed successfully; a broken first export therefore does not block a usable fallback copy.

### Parsing and turn merging

`app/parsers.py` supports VTT, SRT, TXT, and JSON. Timed formats retain start and end values. `app/loaders.py` merges adjacent captions only when both have the same resolved speaker and the next caption begins within two seconds of the previous caption ending. Untimed TXT lines are not merged.

### Speaker resolution

`app/normalization.py` removes case, accent, punctuation, and whitespace differences before matching database full names and aliases. If an alias maps to several people, meeting attendees may break the tie; otherwise the speaker remains unresolved. The service logs this instead of silently assigning a potentially incorrect citation.

### Retrieval and grounding

All filters are applied in Python before retrieval and before the LLM sees evidence. The transparent retriever ranks overlapping question terms, gives a modest boost to decision/proposal language, preserves up to two strong matches per meeting, and adds the following turn as useful context. Evidence is sorted chronologically.

Gemini receives the question plus indexed evidence only. It returns structured JSON with a route, answer, and evidence indexes. Python validates every index, de-duplicates and chronologically orders citations, and maps them to trusted local `meeting_id`, `timestamp`, and `speaker` fields. The model never invents citation metadata.

## Requirements

- Python 3.10 or newer
- A Google Gemini API key (a free-tier key is sufficient for normal testing)
- Local assessment data in `data/`:
  - `data/manifest.json`
  - `data/orbit.db`
  - `data/transcripts/`

## Setup

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and set your key. `GOOGLE_API_KEY` is the primary variable; `GEMINI_API_KEY` remains a compatibility fallback.

```text
GOOGLE_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
```

Never commit `.env`, API keys, or the supplied `data/` files.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TRANSCRIPTS_DIR` | `./data/transcripts` | Transcript folder |
| `MANIFEST_PATH` | `./data/manifest.json` | Transcript manifest |
| `DB_PATH` | `./data/orbit.db` | People and projects database |
| `APP_TODAY` | `2026-07-01` | Default reference date when `as_of` is absent |
| `LLM_PROVIDER` | `gemini` | Informational setting; Gemini is the supported provider |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Gemini model name |
| `GOOGLE_API_KEY` | none | Primary Gemini API key |
| `GEMINI_API_KEY` | none | Compatibility fallback key |
| `PORT` | `8000` | HTTP port for `python main.py` |

If `LLM_PROVIDER` has another value, the application logs a warning and still uses Gemini. It is not a multi-provider implementation.

## Run

Recommended command:

```powershell
python main.py
```

Development reload command:

```powershell
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the interactive API documentation.

## API

### `GET /health`

```json
{"status":"ok"}
```

### `POST /ask`

Only `question` is required.

```json
{
  "question": "How did the Pro tier price change?",
  "project": "pricing-v2",
  "as_of": "2026-07-01"
}
```

Optional filters are `project`, `speaker`, `date_from`, `date_to`, and `as_of`. Dates are inclusive. `as_of` excludes meetings after its date; if omitted, `APP_TODAY` is used instead of the computer clock.

An answer response has exactly these top-level fields:

```json
{
  "request_id": "uuid",
  "answer": "The Pro tier was raised to $59 and later reversed to $49.",
  "route": "answer",
  "citations": [
    {"meeting_id": "m003", "timestamp": "00:03:29", "speaker": "p01"},
    {"meeting_id": "m005", "timestamp": "00:03:54", "speaker": "p01"}
  ],
  "filters": {
    "date_from": null,
    "date_to": null,
    "project": "pricing-v2",
    "speaker": null
  },
  "reference_date": "2026-07-01"
}
```

| Route | Status | Citations | Meaning |
| --- | --- | --- | --- |
| `answer` | 200 | At least one | Supported by meeting evidence |
| `no_answer` | 200 | Empty | Relevant records do not cover the answer |
| `out_of_scope` | 200 | Empty | The request is clearly not a meeting question |

HTTP 400 is reserved for invalid JSON, a missing or blank question, an invalid date, or an unknown project/speaker ID. A reversed valid date range is a normal `200 no_answer` response.

## Token and error handling

The full formatted evidence block—including labels, timestamps, speakers, and text—is counted with `tiktoken` `cl100k_base` and capped at 2,600 tokens. This is safely below the 3,000-token assessment limit. If the tokenizer is unavailable, a conservative one-token-per-three-characters estimate is used and logged.

Gemini is called with a 25-second timeout. Missing keys, network failures, rate limits, malformed model output, and other LLM errors are logged but never become a 500 response. The client receives:

```json
{
  "answer": "The language model could not be reached. Please try again.",
  "route": "no_answer",
  "citations": []
}
```

## Testing notes

Use [MANUAL_TEST_CHECKLIST.md](MANUAL_TEST_CHECKLIST.md) to test health, grounding, decision history, `as_of`, filters, no-answer, out-of-scope, error behavior, loader integrity, and Git safety.

Important expected scenarios:

- Latest pricing question: March’s $59 decision is later reversed to $49 in May.
- `as_of: 2026-04-01`: the May reversal is excluded, so the March decision remains current.
- Office parking policy: `200 no_answer` with no citations.
- Email drafting: `200 out_of_scope` with no citations.
- Unknown project/speaker: `400`.
- Reversed date range: `200 no_answer` with no citations.

## Assumptions and limitations

- The manifest, database schema, and transcript layouts follow the supplied assessment data structure.
- Keyword retrieval is deliberately simple and explainable. Hybrid or embedding retrieval would improve paraphrase recall.
- A small local phrase check handles only obvious non-meeting requests; Gemini decides the route for all other questions, including when no evidence is retrieved.
- Gemini availability, model access, and rate limits are controlled by Google.
- The service supports Gemini only. A real provider abstraction would be needed before adding OpenAI or Anthropic.
- No automated test suite is included yet; adding `pytest` tests for parsers, filtering, retrieval, and LLM failure handling is the natural next improvement.

## Security and repository hygiene

- `.env`, `.venv/`, caches, `data/`, and the personal `PROJECT_STUDY_GUIDE.md` are ignored by Git.
- No API keys or supplied meeting data belong in commits.
- The personal study guide can stay on the local machine without becoming part of the submitted repository.
