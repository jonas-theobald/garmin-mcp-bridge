# garmin-mcp-bridge

Bring your Garmin training data into Claude — and push structured workouts back to your watch.

Garmin has no public API for individuals. Their Connect Developer API is gated behind a partner agreement, and the unofficial scraping libraries break whenever Garmin changes something. This bridges the gap through [Intervals.icu](https://intervals.icu), which syncs with Garmin Connect in both directions and offers a documented API with a plain API key.

```
Garmin watch ──► Garmin Connect ──► Intervals.icu ──► this MCP server ──► Claude
                       ▲                                                    │
                       └────────── planned workouts ◄───────────────────────┘
```

Free, runs locally, no third-party service holding your data. Intervals.icu is free to use (donation-supported).

## What you get

Nine tools your MCP client can call:

| Tool | What it does |
|---|---|
| `check_connection` | Verify the API key, report rate limit headroom |
| `get_athlete_profile` | Thresholds, HR/pace/power zones, FTP, weight |
| `list_activities` | Activities in a date range, with totals for time, distance, elevation and load |
| `get_activity` | One session in detail, including detected intervals |
| `get_activity_streams` | Time series (HR, altitude, pace, gradient, cadence), downsampled |
| `get_wellness` | HRV, resting HR, sleep, weight, CTL/ATL, plus computed baselines |
| `list_planned_workouts` | Calendar: planned sessions, notes, races |
| `create_planned_workout` | Create a structured workout → syncs to your watch |
| `delete_planned_workout` | Remove a calendar entry |

Then ask things like *"compare my last four weeks of load to the block before"*, *"was my easy pace drifting?"*, or *"build tomorrow's threshold session and put it on my watch"*.

## Requirements

- An MCP client. Claude Desktop is the reference; anything speaking MCP works.
- A Garmin Connect account with activities in it.
- An Intervals.icu account (free).
- Python 3.10+. The installer sets this up via [uv](https://docs.astral.sh/uv/) if you do not have it.

## Setup

### 1. Create an Intervals.icu account

Sign up at [intervals.icu/signup](https://intervals.icu/signup). You can log in with Strava, Google, or email.

### 2. Connect Garmin

In **Settings → Connections**, find the Garmin Connect card and authorise it. Make sure these are ticked:

- **Download activities**
- **Download wellness data** — HRV, sleep, resting HR
- **Upload planned workouts** — required if you want workouts to reach your watch

### 3. Run the backfill

This is the step people miss. Garmin only pushes *new* activities from the moment you connect. Your history does not arrive on its own.

On the same Garmin card, click **Download old data** — once under *Download activities* and again under *Download wellness data*. They are separate. Pick a start date far enough back to cover the training you care about.

Give it a few minutes. If nothing arrives after an hour, the connection itself is the problem: disconnect on *both* sides — in Intervals.icu and in Garmin Connect under Account Settings → Connected Apps — then reconnect.

### 4. Get your API key

**Settings → Developer Settings** (bottom of the page) → generate a key.

This key grants full access to your Intervals.icu data. Treat it like a password.

### 5. Install

```bash
git clone https://github.com/jonas-theobald/garmin-mcp-bridge.git
cd garmin-mcp-bridge
```

**macOS / Linux**

```bash
./scripts/install.sh
```

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

The installer checks for uv, builds the environment, asks for your API key, runs the full selftest against the live API, and registers the server with Claude Desktop — backing up your existing config first.

### 6. Restart your client

Quit Claude Desktop **completely** (Cmd+Q on macOS, tray icon → Quit on Windows) and start it again. It only reads the MCP config at startup.

Then ask: *"Call check_connection."*

## Manual install

If you would rather not run a script:

```bash
uv sync
INTERVALS_API_KEY=your_key uv run selftest.py
```

Then add this to your client's config file:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/absolute/path/to/garmin-mcp-bridge/.venv/bin/python",
      "args": ["-m", "garmin_mcp_bridge.server"],
      "env": {
        "INTERVALS_API_KEY": "your_key"
      }
    }
  }
}
```

Use an absolute path to the interpreter inside `.venv`, not `uv run`. The client starts the server without your shell environment, so `uv` will not be on its PATH. On Windows the interpreter is at `.venv\Scripts\python.exe`.

If the file already has other settings, merge this in rather than replacing it — and mind the commas. Invalid JSON is ignored silently, which looks exactly like the server failing to start.

## Verifying it works

The selftest exercises every read path plus, with `--write`, the workout creation path:

```bash
INTERVALS_API_KEY=your_key uv run selftest.py --write
```

```
PASS  auth + athlete profile              athlete i123456 (Your Name)
PASS  sport settings / zones              LTHR=172 maxHR=190 zones=7
PASS  list activities (30d)               15 activities, newest: TrailRun 2026-07-18T06:59:59
PASS  activity detail + intervals         Race — 49 intervals
PASS  activity streams                    streams: {'heartrate': 37292, 'altitude': 37292}
PASS  wellness (HRV/sleep/RHR)            31 days, 27 with HRV
PASS  calendar events                     0 planned events in next 30d
PASS  create + delete planned workout     created+deleted, 3 steps parsed
```

To check the config and server as your client will actually launch them:

```bash
python3 - <<'EOF'
import json, os, subprocess
path = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
srv = json.load(open(path))["mcpServers"]["garmin"]
msgs = [
    {"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2024-11-05","capabilities":{},
        "clientInfo":{"name":"verify","version":"1"}}},
    {"jsonrpc":"2.0","method":"notifications/initialized"},
    {"jsonrpc":"2.0","id":2,"method":"tools/list"},
]
proc = subprocess.run([srv["command"], *srv["args"]],
                      input="".join(json.dumps(m)+"\n" for m in msgs),
                      capture_output=True, text=True,
                      env={**os.environ, **srv.get("env", {})}, timeout=30)
for line in proc.stdout.splitlines():
    try: msg = json.loads(line)
    except ValueError: continue
    if msg.get("id") == 2:
        print("OK —", len(msg["result"]["tools"]), "tools")
        break
else:
    print("FAILED\n", proc.stdout[:500], "\n", proc.stderr[:1000])
EOF
```

## Writing workouts

`create_planned_workout` takes a description in Intervals.icu's native workout syntax:

```
- 15m Z2 Warmup

3x
- 8m Z4
- 3m Z1

- 10m Z1 Cooldown
```

One step per line, prefixed `- `. A bare `Nx` line starts a repeat block covering the steps up to the next blank line. Durations use `s`, `m`, `h`. Targets can be zones (`Z2`), percentages of threshold (`75-82%`), absolute heart rates (`150-160bpm`), or paces (`5:30/km`).

Set `target` to `HR`, `PACE` or `POWER`. For trail and hiking, use `HR` — pace means nothing on a 25% gradient.

### The workout_doc trap

**Never include a `workout_doc` field in the payload.**

A planned workout only syncs to your watch if Intervals.icu parsed the description into structured steps. What triggers that parsing is undocumented, and the intuitive guess is wrong: sending `workout_doc: {}` to *request* parsing actually **suppresses** it. The server reads the presence of the field as "steps already supplied" and skips the parser. The workout lands in the calendar unstructured and never reaches the watch.

Measured against the live API — every case below sent `workout_doc: {}` except the last:

| Payload | Result |
|---|---|
| single endpoint / Run / zones / HR | not parsed |
| bulk endpoint / Run / zones / HR | not parsed |
| single / Run / % of LTHR / HR | not parsed |
| single / Run / absolute bpm / HR | not parsed |
| single / Run / pace / PACE | not parsed |
| single / Ride / % of FTP / POWER | not parsed |
| **single / Run / zones / HR, no `workout_doc`** | **parsed — 3 steps, 1380s** |

With the field omitted, parsing works across both endpoints, both sports tested, and zone, percentage, bpm and pace syntax alike.

This server omits it and reports back after every write:

- `parsed_steps` — how many steps the server recognised
- `garmin_sync_likely` — `false` when nothing parsed

If you get `false`, open the workout in the Intervals.icu web app and save it — that forces parsing — then check your description syntax.

## Troubleshooting

**Tools do not appear in the client.** The config is only read at startup; quit fully and relaunch. Then check the file is valid JSON (`python3 -m json.tool <path>`) and that the `command` path exists. Invalid JSON is ignored without an error message.

**401 Unauthorized.** The key was regenerated or copied incompletely. Get a fresh one from Developer Settings.

**Zero activities.** The backfill has not run — see step 3. Wellness and activities backfill separately.

**Sporadic 403s.** Intervals.icu sits behind Cloudflare, which challenges default library user agents. The client sends a browser-like `User-Agent` for this reason; do not remove it.

**429 Rate limited.** 5000 requests per day, 2500 per rolling 15 minutes, 10 per second per IP. Effectively unreachable in personal use. The client waits out short backoffs and surfaces longer ones as errors rather than hanging.

**Planned load is empty for runs.** Intervals.icu computes planned training load for power-based workouts but not for HR-based runs. Pass `training_load` explicitly if you want future CTL projections to be meaningful.

## Keeping the key out of the config

The installer writes your API key in plain text into the client config. That is normal for MCP servers, but if you would rather not:

**macOS** — store it in the Keychain:

```bash
security add-generic-password -a "$USER" -s intervals-icu -w "your_key"
```

Then point `command` at a wrapper script (`chmod +x` it) and drop the `env` block:

```bash
#!/bin/bash
export INTERVALS_API_KEY=$(security find-generic-password -a "$USER" -s intervals-icu -w)
exec /absolute/path/to/.venv/bin/python -m garmin_mcp_bridge.server
```

**Windows** — the same pattern works with a `.cmd` wrapper reading from Credential Manager via `cmdkey`.

## Design notes

**Streams are downsampled.** A three hour trail run is over 10,000 samples per stream. The tool returns min, max and mean computed from the raw data, plus a series block-averaged to roughly 200 points. The shape is what informs coaching; every individual sample just burns context.

**Fields are filtered.** A raw activity payload carries 200+ fields, most of them cycling power metrics. The tools return a curated subset. If you need something that is missing, add it to `_ACTIVITY_FIELDS` in `server.py`.

**Transport is separate from logic.** `client.py` has no MCP dependency. Moving this to an HTTP/SSE server — say, on a Raspberry Pi so a webhook can react to every upload — means replacing `server.py` only.

**Why not run it on a Pi today?** A stdio server is launched as a subprocess by the client, so it must live on the same machine. Remote hosting means HTTP transport, an auth layer, and TLS, for no benefit while the client only runs on your desktop anyway.

## Sources

The undocumented behaviour above was established empirically against the live API. The documented parts:

- [API access to Intervals.icu](https://forum.intervals.icu/t/api-access-to-intervals-icu/609) — auth, rate limits, the Cloudflare note
- [Uploading planned workouts](https://forum.intervals.icu/t/uploading-planned-workouts-to-intervals-icu/63624) — event payloads
- [API Integration Cookbook](https://forum.intervals.icu/t/intervals-icu-api-integration-cookbook/80090) — wellness, activities, webhooks
- [API reference](https://intervals.icu/api-docs.html)

## License

MIT. See [LICENSE](LICENSE).

Not affiliated with Garmin or Intervals.icu.
