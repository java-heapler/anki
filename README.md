# Anki

A JSON CLI and agent skill for a running local Anki desktop app with [AnkiConnect](https://git.sr.ht/~foosoft/anki-connect). Uses API v6 with Python's standard library; no runtime dependencies and no direct collection database access.

## Install

Install Anki and the AnkiConnect add-on separately, then keep Anki open. This repository does not install or configure either application.

```sh
uv tool install -e .
anki-agent doctor
anki-agent decks
anki-agent models
anki-agent model-fields Basic
anki-agent find-notes 'deck:Default' --limit 20
anki-agent notes 123456789
anki-agent find-cards 'is:due'
anki-agent cards 123456789
```

Use `--offset` with the returned `next_offset` for searches. IDs are sorted numerically before pagination; searches fetch all matching IDs from AnkiConnect, but only print the requested slice. Collection changes between requests may shift pages. Note/card inspection returns AnkiConnect fields unchanged, including HTML.

## Writes

Create a UTF-8 JSON file containing exact model field names, for example `{"Front":"What is O(n)?","Back":"Linear time"}`. Preview is local and makes no API requests:

```sh
anki-agent add-note --deck Default --model Basic --fields-file fields.json --tag swe
anki-agent add-note --deck Default --model Basic --fields-file fields.json --tag swe --confirm
anki-agent update-note 123456789 --fields-file fields.json --confirm
```

Add-note rejects duplicates using AnkiConnect's default duplicate rules. Update-note changes only supplied fields and reads them back to verify; close the note in Anki's editor first, because the editor can interfere with updates. No write is retried automatically. A timeout or failed verification can mean the write already happened: inspect the collection before retrying. No delete, grading, scheduling, sync, remote media download, or arbitrary API action command is exposed.

## Configuration and output

`ANKI_CONNECT_URL` defaults to `http://127.0.0.1:8765`; `--endpoint` overrides it. Only loopback HTTP endpoints are accepted. `ANKI_CONNECT_KEY` supplies the optional API key through the environment; it is never included in output. HTTP redirects and proxies are not used. Global options precede the command. `--timeout` defaults to 10 seconds and must be within (0,120]; responses are capped at 8 MiB. Transport and server errors are sanitized because the server may echo private data. Successful read results and write previews intentionally contain your notes; treat captured output as private.

Commands return JSON on stdout. Exit 0 indicates success, 1 an operational JSON error, and 2 an argparse usage error on stderr. Doctor verifies API availability, not every action's permission. The active Anki profile determines the collection.

## Development

```sh
uv sync --extra dev
uv run pytest --cov=anki_agent --cov-branch --cov-report=term-missing
uv build
```

Tests use a local synthetic HTTP server, mocked failures, temporary JSON files, and installed CLI subprocesses. They do not access a personal collection. Live Anki integration remains unverified when the application/add-on is unavailable; a passing simulated API contract does not prove compatibility with every add-on version.

Measured baseline: **58 tests passed; 142/142 executable lines (100%) and 54/54 branches (100%) covered** on Python 3.13.15. Coverage is not a correctness guarantee. Tests include Unicode, API envelopes, pagination, uncertain writes, malformed read-back, duplicate prevention configuration, and no-network previews. Build and skill frontmatter validation also pass. No real Anki application or personal collection was exercised.
