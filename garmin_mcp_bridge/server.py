"""
MCP server exposing Intervals.icu training data to Claude.

Transport is stdio: Claude Desktop launches this file as a subprocess. All
domain logic lives in client.py, so swapping in an HTTP transport later means
replacing only the final ``mcp.run()`` call.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import IntervalsClient, IntervalsError, summarize_stream

mcp = FastMCP("intervals-icu")

_client: IntervalsClient | None = None


def client() -> IntervalsClient:
    """Lazily construct the client so a missing key surfaces as a tool error."""
    global _client
    if _client is None:
        _client = IntervalsClient()
    return _client


def _default_range(days: int) -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


# Summary keys worth returning. The raw activity payload has 200+ fields, most
# of which are cycling power metrics that are noise for a trail block.
_ACTIVITY_FIELDS = [
    "id", "start_date_local", "name", "type", "description",
    "distance", "moving_time", "elapsed_time", "total_elevation_gain",
    "average_speed", "max_speed", "pace", "gap",
    "average_heartrate", "max_heartrate", "icu_hr_zone_times",
    "icu_training_load", "icu_intensity", "icu_efficiency_factor",
    "icu_atl", "icu_ctl", "trimp", "calories", "feel", "perceived_exertion",
    "average_cadence", "icu_average_watts", "decoupling",
]

_WELLNESS_FIELDS = [
    "id", "hrv", "hrvSDNN", "restingHR", "sleepSecs", "sleepScore",
    "sleepQuality", "weight", "readiness", "fatigue", "soreness", "stress",
    "mood", "motivation", "injury", "steps", "ctl", "atl", "rampRate",
    "vo2max", "respiration", "spO2",
]


def _pick(record: dict, fields: list[str]) -> dict:
    return {k: record[k] for k in fields if record.get(k) is not None}


# ------------------------------------------------------------------ read tools


@mcp.tool()
def get_athlete_profile() -> dict:
    """Get athlete profile: thresholds, HR/pace zones, FTP, weight, sport settings.

    Call this first in a coaching session. Zone boundaries are needed to
    interpret every other number, and to write workouts with correct targets.
    """
    athlete = client().get_athlete()
    profile = _pick(athlete, [
        "id", "name", "sex", "bio", "city", "country", "timezone",
        "icu_weight", "icu_resting_hr", "email",
    ])

    sports = []
    for setting in athlete.get("sportSettings") or []:
        sports.append(_pick(setting, [
            "types", "ftp", "lthr", "max_hr", "threshold_pace",
            "hr_zones", "hr_zone_names", "pace_zones", "pace_zone_names",
            "power_zones", "warmup_time", "cooldown_time",
        ]))
    profile["sport_settings"] = sports
    return profile


@mcp.tool()
def list_activities(
    oldest: str | None = None,
    newest: str | None = None,
    days_back: int = 14,
    activity_type: str | None = None,
) -> dict:
    """List completed activities with training load, HR and elevation.

    Args:
        oldest: Start date YYYY-MM-DD. Defaults to ``days_back`` before today.
        newest: End date YYYY-MM-DD (inclusive). Defaults to today.
        days_back: Used only when explicit dates are omitted.
        activity_type: Optional filter, e.g. "Run", "Ride", "Hike",
            "WeightTraining". Matched case-insensitively.
    """
    if not oldest or not newest:
        default_oldest, default_newest = _default_range(days_back)
        oldest = oldest or default_oldest
        newest = newest or default_newest

    activities = client().list_activities(oldest, newest)
    if activity_type:
        wanted = activity_type.lower()
        activities = [a for a in activities if (a.get("type") or "").lower() == wanted]

    rows = [_pick(a, _ACTIVITY_FIELDS) for a in activities]
    total_time = sum(a.get("moving_time") or 0 for a in activities)
    return {
        "range": {"oldest": oldest, "newest": newest},
        "count": len(rows),
        "totals": {
            "moving_time_h": round(total_time / 3600, 2),
            "distance_km": round(sum(a.get("distance") or 0 for a in activities) / 1000, 2),
            "elevation_gain_m": round(
                sum(a.get("total_elevation_gain") or 0 for a in activities)
            ),
            "training_load": round(sum(a.get("icu_training_load") or 0 for a in activities)),
        },
        "activities": rows,
    }


@mcp.tool()
def get_activity(activity_id: str, include_intervals: bool = True) -> dict:
    """Get one activity in detail, optionally with detected intervals/laps.

    Use for post-session analysis: how each interval actually went, HR drift,
    and where elevation was gained. Activity ids look like "i55751783".
    """
    activity = client().get_activity(activity_id, intervals=include_intervals)
    result = _pick(activity, _ACTIVITY_FIELDS + [
        "icu_zone_times", "icu_hr_zone_times", "icu_pace_zone_times",
        "icu_weighted_avg_watts", "lthr", "threshold_pace", "max_hr",
        "session_rpe", "compliance", "icu_recording_time",
    ])
    if include_intervals:
        result["intervals"] = [
            _pick(iv, [
                "type", "label", "distance", "moving_time", "average_heartrate",
                "max_heartrate", "average_speed", "gap", "total_elevation_gain",
                "average_gradient", "zone", "intensity", "decoupling",
                "average_cadence", "average_watts",
            ])
            for iv in (activity.get("icu_intervals") or [])
        ]
    return result


@mcp.tool()
def get_activity_streams(
    activity_id: str,
    types: list[str] | None = None,
) -> dict:
    """Get time-series streams for an activity, downsampled to ~200 points each.

    Args:
        activity_id: e.g. "i55751783".
        types: Stream names. Defaults to heartrate, altitude, velocity_smooth,
            distance, cadence, grade_smooth. Others: watts, temp, latlng,
            time, moving, fixed_watts.

    Returns min/max/mean plus a downsampled series per stream. Full per-second
    data is deliberately not returned: a long trail run is >10k samples per
    stream and the shape is what matters for coaching, not every sample.
    """
    types = types or [
        "heartrate", "altitude", "velocity_smooth", "distance",
        "cadence", "grade_smooth",
    ]
    streams = client().get_streams(activity_id, types)
    return {
        "activity_id": activity_id,
        "streams": [
            summarize_stream(s.get("type", "unknown"), s.get("data") or [])
            for s in streams
        ],
    }


@mcp.tool()
def get_wellness(
    oldest: str | None = None,
    newest: str | None = None,
    days_back: int = 14,
) -> dict:
    """Get daily wellness: HRV, resting HR, sleep, weight, CTL/ATL, soreness.

    This is the autoregulation input. Check it before prescribing a hard
    session, and compare HRV and resting HR against the athlete's own recent
    baseline rather than population norms.
    """
    if not oldest or not newest:
        default_oldest, default_newest = _default_range(days_back)
        oldest = oldest or default_oldest
        newest = newest or default_newest

    records = [_pick(r, _WELLNESS_FIELDS) for r in client().get_wellness(oldest, newest)]

    def _mean(key: str) -> float | None:
        vals = [r[key] for r in records if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "range": {"oldest": oldest, "newest": newest},
        "count": len(records),
        "baselines": {
            "hrv_mean": _mean("hrv"),
            "resting_hr_mean": _mean("restingHR"),
            "sleep_h_mean": round(_mean("sleepSecs") / 3600, 2) if _mean("sleepSecs") else None,
            "weight_mean": _mean("weight"),
        },
        "days": records,
    }


@mcp.tool()
def list_planned_workouts(
    oldest: str | None = None,
    newest: str | None = None,
    days_ahead: int = 14,
) -> dict:
    """List calendar events: planned workouts, notes and races.

    Use before writing a new week so existing plan entries are not duplicated.
    Every event id returned here can be passed to ``delete_planned_workout``.
    """
    today = date.today()
    oldest = oldest or today.isoformat()
    newest = newest or (today + timedelta(days=days_ahead)).isoformat()

    events = client().list_events(oldest, newest)
    return {
        "range": {"oldest": oldest, "newest": newest},
        "count": len(events),
        "events": [
            _pick(e, [
                "id", "start_date_local", "category", "name", "description",
                "type", "moving_time", "distance", "icu_training_load",
                "icu_intensity", "target", "external_id", "color",
            ])
            for e in events
        ],
    }


# ----------------------------------------------------------------- write tools


@mcp.tool()
def create_planned_workout(
    start_date: str,
    name: str,
    description: str,
    activity_type: str = "Run",
    moving_time: int | None = None,
    target: str = "HR",
    training_load: int | None = None,
    external_id: str | None = None,
) -> dict:
    """Create a structured planned workout on the Intervals.icu calendar.

    Intervals.icu syncs planned workouts to a connected Garmin Connect account,
    so this is the path for getting a session onto the watch. See the README
    caveat: confirm the first workout actually reaches Garmin before relying
    on this in a training block.

    Args:
        start_date: Local date YYYY-MM-DD. Time is forced to 00:00:00, which
            the API requires for calendar events.
        name: Short session title, e.g. "3x8min Schwelle".
        description: Workout in Intervals.icu syntax (see below).
        activity_type: "Run", "Ride", "Hike", "WeightTraining", "Swim".
        moving_time: Total duration in seconds. Optional; Intervals.icu derives
            it from the parsed description.
        target: "HR", "PACE" or "POWER". Use "HR" for trail and hiking work
            where pace is meaningless on steep terrain.
        training_load: Optional manual load override.
        external_id: Stable id of your own. Reusing it with the same date makes
            the call idempotent, so a re-planned week updates instead of
            duplicating.

    Workout syntax — one step per line, prefixed "- ":

        - 15m Z2 Warmup
        3x
        - 8m Z4
        - 3m Z1
        - 10m Z1 Cooldown

    A bare "Nx" line on its own starts a repeated block; the following steps
    until the next blank line are repeated. Durations use m/s/h. Targets can be
    zones ("Z2"), ranges of threshold ("75-82%"), or free text for steps with
    no measurable target. Blank lines separate blocks.
    """
    event: dict[str, Any] = {
        "category": "WORKOUT",
        "start_date_local": f"{start_date}T00:00:00",
        "type": activity_type,
        "name": name,
        "description": description,
        "target": target,
        # Deliberately no workout_doc key. Sending one — even empty — makes
        # Intervals.icu treat the steps as already supplied and skip parsing
        # the description, which leaves the workout unstructured and therefore
        # unable to sync to Garmin. Verified against the live API by
        # the probe script; omitting the key is the only variant that
        # parsed.
    }
    if moving_time is not None:
        event["moving_time"] = moving_time
    if training_load is not None:
        event["icu_training_load"] = training_load

    if external_id:
        event["external_id"] = external_id
        created = client().upsert_events([event])
        result = created[0] if created else {}
    else:
        result = client().create_event(event)

    parsed = result.get("workout_doc") or {}
    return {
        "id": result.get("id"),
        "start_date_local": result.get("start_date_local"),
        "name": result.get("name"),
        "type": result.get("type"),
        "moving_time": result.get("moving_time"),
        "icu_training_load": result.get("icu_training_load"),
        "parsed_steps": len(parsed.get("steps") or []),
        "garmin_sync_likely": bool(parsed.get("steps")),
        "note": (
            "parsed_steps is 0 — the description was not parsed into structured "
            "steps, so this will NOT sync to Garmin. Open the workout in the "
            "Intervals.icu web app and save it to trigger parsing, then check "
            "the description syntax."
            if not parsed.get("steps")
            else "Workout parsed into structured steps and should sync to Garmin."
        ),
    }


@mcp.tool()
def delete_planned_workout(event_id: int) -> dict:
    """Delete a calendar event by its numeric id (from list_planned_workouts)."""
    client().delete_event(event_id)
    return {"deleted": event_id}


@mcp.tool()
def check_connection() -> dict:
    """Verify the API key works and report rate limit headroom."""
    athlete = client().get_athlete()
    limit = client().rate_limit
    return {
        "ok": True,
        "athlete_id": athlete.get("id"),
        "name": athlete.get("name"),
        "rate_limit_remaining_15min": limit.window_remaining,
        "rate_limit_remaining_day": limit.daily_remaining,
    }


def main() -> None:
    if not os.environ.get("INTERVALS_API_KEY"):
        # Fail loudly at startup rather than on the first tool call, so the
        # error lands in the Claude Desktop MCP log where it can be found.
        raise SystemExit(
            "INTERVALS_API_KEY is not set. Add it to the env block of the "
            "server entry in claude_desktop_config.json."
        )
    mcp.run()


if __name__ == "__main__":
    main()
