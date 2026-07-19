#!/usr/bin/env python3
"""
End-to-end check against the live Intervals.icu API.

Run this before wiring the server into an MCP client, so a failure points at
credentials or the sync rather than at the MCP plumbing.

    INTERVALS_API_KEY=your_key uv run selftest.py

Add --write to also create and immediately delete a throwaway planned workout.
That is the only way to confirm the path that pushes sessions to your watch.
"""

import sys
from datetime import date, timedelta

from garmin_mcp_bridge.client import IntervalsClient, IntervalsError

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def check(name: str, fn):
    try:
        results.append((PASS, name, fn()))
    except IntervalsError as exc:
        results.append((FAIL, name, str(exc)))
    except Exception as exc:  # noqa: BLE001 - a selftest reports everything
        results.append((FAIL, name, f"{type(exc).__name__}: {exc}"))


def main() -> int:
    write_mode = "--write" in sys.argv

    try:
        api = IntervalsClient()
    except IntervalsError as exc:
        print(f"{FAIL}  {exc}")
        return 1

    today = date.today()
    month_ago = (today - timedelta(days=30)).isoformat()
    today_str = today.isoformat()
    state: dict = {}

    def _auth():
        athlete = api.get_athlete()
        state["athlete"] = athlete
        return f"athlete {athlete.get('id')} ({athlete.get('name')})"

    def _zones():
        settings = state["athlete"].get("sportSettings") or []
        run = next(
            (s for s in settings if "Run" in (s.get("types") or [])),
            settings[0] if settings else None,
        )
        if not run:
            raise RuntimeError("no sport settings found")
        return (
            f"LTHR={run.get('lthr')} maxHR={run.get('max_hr')} "
            f"zones={len(run.get('hr_zones') or [])}"
        )

    def _activities():
        acts = api.list_activities(month_ago, today_str)
        state["activities"] = acts
        if not acts:
            return "0 activities in last 30d — has the Garmin backfill run?"
        newest = acts[0]
        return f"{len(acts)} activities, newest: {newest.get('type')} {newest.get('start_date_local')}"

    def _detail():
        acts = state.get("activities") or []
        if not acts:
            raise RuntimeError("no activity available to inspect")
        act = api.get_activity(acts[0]["id"], intervals=True)
        return f"{act.get('name')} — {len(act.get('icu_intervals') or [])} intervals"

    def _streams():
        acts = state.get("activities") or []
        if not acts:
            raise RuntimeError("no activity available to inspect")
        streams = api.get_streams(acts[0]["id"], ["heartrate", "altitude"])
        return f"streams: {({s.get('type'): len(s.get('data') or []) for s in streams})}"

    def _wellness():
        records = api.get_wellness(month_ago, today_str)
        with_hrv = sum(1 for r in records if r.get("hrv") is not None)
        return f"{len(records)} days, {with_hrv} with HRV"

    def _events():
        events = api.list_events(today_str, (today + timedelta(days=30)).isoformat())
        return f"{len(events)} planned events in next 30d"

    check("auth + athlete profile", _auth)
    check("sport settings / zones", _zones)
    check("list activities (30d)", _activities)
    check("activity detail + intervals", _detail)
    check("activity streams", _streams)
    check("wellness (HRV/sleep/RHR)", _wellness)
    check("calendar events", _events)

    if write_mode:
        def _write():
            target = (today + timedelta(days=90)).isoformat()
            event = {
                "category": "WORKOUT",
                "start_date_local": f"{target}T00:00:00",
                "type": "Run",
                "name": "SELFTEST - delete me",
                "description": "- 10m Z1\n\n2x\n- 2m Z3\n- 2m Z1\n\n- 5m Z1",
                "target": "HR",
                # No workout_doc key on purpose. Sending one, even empty, makes
                # the server skip parsing the description. See README.
            }
            created = api.create_event(event)
            steps = len((created.get("workout_doc") or {}).get("steps") or [])
            api.delete_event(created["id"])
            if steps == 0:
                raise RuntimeError(
                    "workout created but the description was NOT parsed into "
                    "steps — it would not sync to your watch"
                )
            return f"created+deleted, {steps} steps parsed"

        check("create + delete planned workout", _write)
    else:
        results.append((SKIP, "create + delete planned workout", "re-run with --write"))

    print()
    for status, name, detail in results:
        print(f"{status:4}  {name:34}  {detail}")
    print()

    failed = sum(1 for s, _, _ in results if s == FAIL)
    skipped = sum(1 for s, _, _ in results if s == SKIP)
    api.close()
    print(f"{len(results) - failed - skipped} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
