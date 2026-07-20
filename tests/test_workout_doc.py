"""Regression tests for the workout_doc trap (see README "The workout_doc trap").

Sending a `workout_doc` field when creating a calendar event — even `{}` —
makes Intervals.icu skip parsing the description into structured steps, so
the workout lands unstructured and never reaches the watch. This is the
single most important, and most counter-intuitive, behaviour in the codebase.
These tests fail loudly if the field is ever reintroduced.
"""

from __future__ import annotations

import json

import httpx

from garmin_mcp_bridge import server


def _event_response(**overrides: object) -> dict:
    base = {
        "id": 123,
        "start_date_local": "2026-08-01T00:00:00",
        "name": "Test",
        "type": "Run",
        "moving_time": 1800,
    }
    base.update(overrides)
    return base


def test_single_event_path_never_sends_workout_doc(install_server_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_event_response())

    install_server_client(handler)

    server.create_planned_workout(
        start_date="2026-08-01",
        name="Test",
        description="- 15m Z2 Warmup",
    )

    assert captured["path"].endswith("/events")
    assert "workout_doc" not in captured["body"]


def test_bulk_event_path_never_sends_workout_doc(install_server_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[_event_response()])

    install_server_client(handler)

    server.create_planned_workout(
        start_date="2026-08-01",
        name="Test",
        description="- 15m Z2 Warmup",
        external_id="week-32-mon",
    )

    assert captured["path"].endswith("/events/bulk")
    assert isinstance(captured["body"], list)
    assert "workout_doc" not in captured["body"][0]


def test_event_payload_is_correctly_shaped(install_server_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_event_response())

    install_server_client(handler)

    server.create_planned_workout(
        start_date="2026-08-01",
        name="3x8min Threshold",
        description="- 15m Z2 Warmup",
        activity_type="Ride",
        target="POWER",
    )

    body = captured["body"]
    assert body["category"] == "WORKOUT"
    assert body["start_date_local"] == "2026-08-01T00:00:00"
    assert body["start_date_local"].endswith("T00:00:00")
    assert body["type"] == "Ride"
    assert body["description"] == "- 15m Z2 Warmup"
    assert body["target"] == "POWER"


def test_garmin_sync_likely_true_when_steps_returned(install_server_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_event_response(workout_doc={"steps": [{}, {}, {}]}),
        )

    install_server_client(handler)

    result = server.create_planned_workout(
        start_date="2026-08-01", name="Test", description="- 15m Z2 Warmup"
    )

    assert result["garmin_sync_likely"] is True
    assert result["parsed_steps"] == 3


def test_garmin_sync_likely_false_with_note_when_workout_doc_empty(install_server_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_event_response(workout_doc={}))

    install_server_client(handler)

    result = server.create_planned_workout(
        start_date="2026-08-01", name="Test", description="- 15m Z2 Warmup"
    )

    assert result["garmin_sync_likely"] is False
    assert result["parsed_steps"] == 0
    assert "garmin" in result["note"].lower()
    assert "not" in result["note"].lower()
