# MeetingMind Manual Test Checklist

Run these checks before submission. Start the API first:

```powershell
python main.py
```

Use `http://127.0.0.1:8000/docs` for JSON request tests unless a PowerShell command is shown.

## 1. Service startup and health

- [ ] Start command completes with `Application startup complete`.
- [ ] Open `http://127.0.0.1:8000/health`.
- [ ] Expected response:

```json
{"status": "ok"}
```

## 2. Valid grounded answer

Send:

```json
{
  "question": "What pricing decision was made?",
  "project": "pricing-v2"
}
```

- [ ] HTTP 200.
- [ ] `route` is `answer`.
- [ ] Answer says the Pro tier returned to $49.
- [ ] Citations include only `meeting_id`, `timestamp`, and `speaker`.
- [ ] Citations include the March decision and May reversal.

## 3. Decision history

Send:

```json
{
  "question": "How did the Pro tier price change?",
  "project": "pricing-v2"
}
```

- [ ] HTTP 200 and `route` is `answer`.
- [ ] Answer lists $59 before $49.
- [ ] Citations are chronological: March, then May.

## 4. `as_of` filter

Send:

```json
{
  "question": "What was the Pro tier price?",
  "project": "pricing-v2",
  "as_of": "2026-04-01"
}
```

- [ ] HTTP 200 and `route` is `answer`.
- [ ] Answer says $59.
- [ ] Only the March citation is present.
- [ ] `reference_date` is `2026-04-01`.

## 5. Project filter

Send:

```json
{
  "question": "What was the mobile launch date?",
  "project": "mobile-app-launch",
  "as_of": "2026-04-01"
}
```

- [ ] HTTP 200 and `route` is `answer`.
- [ ] No citation belongs to `pricing-v2`.

## 6. Speaker filter

Send:

```json
{
  "question": "What pricing decision was made?",
  "project": "pricing-v2",
  "speaker": "p01"
}
```

- [ ] HTTP 200.
- [ ] Every citation has `speaker: "p01"`.

## 7. Date range filter

Send:

```json
{
  "question": "What pricing decision was made?",
  "project": "pricing-v2",
  "date_from": "2026-05-01",
  "date_to": "2026-05-31"
}
```

- [ ] HTTP 200.
- [ ] No citation uses the March meeting.
- [ ] The answer reflects only allowed May evidence.

## 8. No evidence

Send:

```json
{
  "question": "What is the office parking policy?"
}
```

- [ ] HTTP 200.
- [ ] `route` is `no_answer`.
- [ ] `citations` is `[]`.
- [ ] Answer says the records do not cover the answer and suggests asking the meeting owner.

## 9. Out of scope

Send:

```json
{
  "question": "Write an email about the pricing decision."
}
```

- [ ] HTTP 200.
- [ ] `route` is `out_of_scope`.
- [ ] `citations` is `[]`.

## 10. Invalid request tests

All tests below except the reversed range must return HTTP 400.

### Missing question

```json
{}
```

### Blank question

```json
{"question": ""}
```

### Invalid date

```json
{"question": "test", "as_of": "not-a-date"}
```

### Reversed date range

```json
{
  "question": "test",
  "date_from": "2026-05-01",
  "date_to": "2026-04-01"
}
```

- [ ] HTTP 200, `route` is `no_answer`, and `citations` is `[]`.

### Unknown project

```json
{"question": "test", "project": "unknown-project"}
```

### Unknown speaker

```json
{"question": "test", "speaker": "unknown-person"}
```

### Invalid JSON

In Swagger, temporarily send malformed JSON such as:

```text
{question: "test"}
```

- [ ] HTTP 400.

## 11. Response shape

For every successful request, check that the response contains exactly:

```text
request_id
answer
route
citations
filters
reference_date
```

- [ ] `answer` has at least one citation.
- [ ] `no_answer` and `out_of_scope` have no citations.
- [ ] A citation has exactly `meeting_id`, `timestamp`, and `speaker`.

## 12. Loader integrity

Stop the server or use a second terminal. Run:

```powershell
python -c "from app.loaders import load_turns; from pathlib import Path; ts=load_turns(Path('data/transcripts'),Path('data/manifest.json'),Path('data/orbit.db')); print('Meetings:', len(set(t['meeting_id'] for t in ts))); print('Turns:', len(ts)); print('Unresolved speakers:', sorted(set(t['speaker_raw'] for t in ts if t['speaker'] is None)))"
```

Expected for the supplied data:

```text
Meetings: 6
Turns: 453
Unresolved speakers: []
```

Check the merged pricing evidence:

```powershell
python -c "from app.loaders import load_turns; from pathlib import Path; ts=load_turns(Path('data/transcripts'),Path('data/manifest.json'),Path('data/orbit.db')); [print(t['meeting_id'], t['timestamp'], t['text']) for t in ts if t['meeting_id'] in {'m003','m005'} and 'Pro tier' in t['text']]"
```

- [ ] The $59 and $49 statements are complete sentences.

## 13. Token-limit implementation check

Run:

```powershell
python -c "from app.main import ENCODING, MAX_EVIDENCE_TOKENS; print(getattr(ENCODING, 'name', 'conservative fallback'), MAX_EVIDENCE_TOKENS)"
```

Expected:

```text
cl100k_base 2600
```

- [ ] The limit is below 3,000. If the conservative fallback is printed, check the startup warning and restore network access before submitting if possible.

## 14. Submission safety

Run:

```powershell
git status --short
git ls-files data .env
git log --oneline -10
```

- [ ] Working tree is clean before submission.
- [ ] `git ls-files data .env` prints nothing.
- [ ] No real key or transcript data is tracked.
- [ ] Recent commits show the final implementation and documentation.

## 15. Final submission check

- [ ] `README.md` explains installation, architecture, token limit, assumptions, limitations, and examples.
- [ ] `.env.example` contains no real API key.
- [ ] `requirements.txt` includes `google-genai`.
- [ ] The latest branch is pushed to GitHub.
- [ ] The final GitHub branch or main branch contains all documentation.
