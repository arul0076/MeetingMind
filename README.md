# MeetingMind

A Python API that answers questions from meeting transcripts with source citations.

## Status
Day 1 starter: transcript parsing and normalization modules are implemented. API, retrieval, LLM integration, token budget enforcement, and full validation are next.

## Setup
1. Use Python 3.10 or later.
2. Create a virtual environment and install `requirements.txt`.
3. Copy `.env.example` to `.env` and add your own provider API key.
4. Place the supplied assessment data under `./data` for local development.
5. Run the transcript loader validation before starting the API.

Do not commit the assessment `data/` folder or real API keys.

## Architecture
- `app/parsers.py`: parsers for VTT, SRT, TXT, JSON.
- `app/normalization.py`: case- and accent-insensitive speaker matching.
- `app/loaders.py`: manifest/database loading, deduplication, normalized turns.

## Assumptions to verify
- The first manifest entry for a duplicated meeting ID is used.
- Adjacent same-speaker caption segments within two seconds are merged as one turn. Verify this against the provided transcripts before relying on it.
- Unresolved speakers remain `None` and must be surfaced during validation rather than silently assigned.
