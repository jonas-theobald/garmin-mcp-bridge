# CLAUDE.md

Context for Claude Code working in this repository.

## What this is

An MCP server that exposes Garmin training data to MCP clients by talking to the
Intervals.icu REST API. Garmin has no public API for individuals, so Intervals.icu
— which syncs bidirectionally with Garmin Connect — is the bridge.

Read-only tools cover activities, streams and wellness. The write path creates
planned workouts that Intervals.icu pushes to the user's watch.

## Architecture

```
garmin_mcp_bridge/
  client.py   Intervals.icu REST client. NO MCP dependency — keep it that way.
  server.py   FastMCP tool definitions, stdio transport. Thin layer over client.
selftest.py   End-to-end check against the live API.
scripts/      install.sh (macOS/Linux), install.ps1 (Windows)
```

The separation is deliberate: `client.py` must stay transport-agnostic so the
server can later be moved to HTTP/SSE (e.g. on a Raspberry Pi reacting to
webhooks) by replacing `server.py` alone. Do not import `mcp` in `client.py`.

## Hard constraints

**Never send a `workout_doc` field when creating a calendar event.** Including
it — even as `{}` — makes Intervals.icu skip parsing the workout description
into structured steps. The workout then lands in the calendar unstructured and
never reaches the watch. This was established empirically against the live API;
the README documents the full test matrix. This is the single most important
behaviour in the codebase and it is counter-intuitive, so it is easy to
"helpfully" reintroduce. Do not.

**Keep the browser-like User-Agent** in `client.py`. Intervals.icu sits behind
Cloudflare, which challenges default Python library user agents and returns
sporadic 403s.

**Never commit an API key.** `INTERVALS_API_KEY` comes from the environment.
There is no key in the repo and none should appear in tests, fixtures or docs —
use obviously fake placeholders.

## Testing

There is no unit test suite. Verification runs against the live API:

```bash
INTERVALS_API_KEY=your_key uv run selftest.py          # read paths
INTERVALS_API_KEY=your_key uv run selftest.py --write  # plus workout creation
```

The `--write` run creates a workout 90 days out and deletes it again. It fails
deliberately if the description was not parsed into steps, which is the
regression guard for the `workout_doc` constraint above.

When changing request-building logic without a key at hand, test against
`httpx.MockTransport` rather than hitting the API.

## Conventions

- **Language:** English for all code, comments, docs and commit messages. The
  maintainer is German; user-facing conversation may be German, the repo is not.
- **Commits:** Conventional Commits. `feat:`, `fix:`, `docs:`, `refactor:`,
  `chore:`. Body explains why, not what.
- **Style:** Comments explain reasoning that is not evident from the code —
  especially undocumented API behaviour. Do not narrate what the next line does.
- **Tool docstrings are the interface.** The model calling these tools sees only
  the docstring. Keep them precise about units, formats and side effects; the
  workout syntax reference in `create_planned_workout` is load-bearing.

## Context discipline

Tool responses go into a model's context window. Two rules follow:

- Streams are downsampled to ~200 points, with min/max/mean computed from the
  raw data first. A long activity is 10,000+ samples per stream.
- Activity payloads are filtered through `_ACTIVITY_FIELDS` in `server.py`.
  The raw API returns 200+ fields, mostly cycling power metrics. Add fields
  there when needed rather than returning everything.

## API notes

- Auth: HTTP Basic, username is the literal string `API_KEY`, password is the key.
- Athlete id `0` resolves to the key owner. No athlete id needs configuring.
- Rate limits: 5000/day, 2500 per rolling 15 min, 10/s per IP. The client waits
  out short 429 backoffs and raises on longer ones rather than hanging a call.
- Intervals.icu does not compute planned training load for HR-based runs, only
  for power-based workouts. Callers must pass `training_load` explicitly.

## Known gaps

Garmin-proprietary metrics — Body Battery, stress score, Training Readiness,
detailed sleep stages — are computed by Garmin and not fully exposed through
Intervals.icu. They are not available here. Adding them would require talking to
Garmin directly via the unofficial `python-garminconnect` library, which needs
full account credentials and breaks whenever Garmin changes something. Weigh
that trade-off carefully before adding it.
