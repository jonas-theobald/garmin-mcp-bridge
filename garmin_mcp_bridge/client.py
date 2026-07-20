"""
Transport-agnostic client for the Intervals.icu REST API.

Deliberately contains no MCP-specific code so the same logic can later be
wrapped in an HTTP/SSE server (e.g. on a Raspberry Pi) without changes.

API reference:
  https://intervals.icu/api-docs.html
  https://forum.intervals.icu/t/api-access-to-intervals-icu/609
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://intervals.icu/api/v1"

# Intervals.icu sits behind Cloudflare, which challenges or blocks default
# library user agents such as "python-httpx/0.27". Documented in the API
# access forum post under "Cloudflare Note".
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 intervals-mcp/0.1"
)


class IntervalsError(RuntimeError):
    """Raised for API errors with a message safe to surface to the model."""


@dataclass
class RateLimit:
    """Snapshot of the rate limit headers returned on every response."""

    window_remaining: int | None = None
    daily_remaining: int | None = None


class IntervalsClient:
    """Thin synchronous wrapper around the Intervals.icu API.

    Authentication uses HTTP Basic with the literal username ``API_KEY`` and
    the personal API key as the password. The athlete id ``0`` resolves to the
    athlete who owns the key, so no athlete id needs to be configured.
    """

    def __init__(
        self,
        api_key: str | None = None,
        athlete_id: str = "0",
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        key = api_key or os.environ.get("INTERVALS_API_KEY")
        if not key:
            raise IntervalsError(
                "No API key. Set INTERVALS_API_KEY (Intervals.icu > Settings > "
                "Developer Settings)."
            )
        self.athlete_id = athlete_id or os.environ.get("INTERVALS_ATHLETE_ID", "0")
        self.rate_limit = RateLimit()
        self._http = httpx.Client(
            base_url=BASE_URL,
            auth=("API_KEY", key),
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            transport=transport,
        )

    # ---------------------------------------------------------------- internals

    def _record_rate_limit(self, response: httpx.Response) -> None:
        raw = response.headers.get("X-RateLimit-Remaining")
        if not raw:
            return
        parts = raw.split(",")
        try:
            self.rate_limit = RateLimit(
                window_remaining=int(parts[0].strip()),
                daily_remaining=int(parts[1].strip()) if len(parts) > 1 else None,
            )
        except ValueError:
            pass

    def _request(self, method: str, path: str, *, retries: int = 1, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise IntervalsError(f"Network error calling {path}: {exc}") from exc

        self._record_rate_limit(response)

        if response.status_code == 429 and retries > 0:
            wait = int(response.headers.get("Retry-After", "5"))
            # Only wait out short throttles; a multi-minute backoff should
            # surface to the user rather than hang the MCP call.
            if wait <= 30:
                time.sleep(wait)
                return self._request(method, path, retries=retries - 1, **kwargs)
            raise IntervalsError(
                f"Rate limited by Intervals.icu. Retry in {wait}s "
                f"({wait // 60}min). Limits: 2500 per 15min, 5000 per day."
            )

        if response.status_code in (401, 403):
            raise IntervalsError(
                f"Authentication failed ({response.status_code}). Check that "
                "INTERVALS_API_KEY is the key from Settings > Developer Settings "
                "and has not been regenerated."
            )

        if response.status_code == 404:
            raise IntervalsError(f"Not found: {path}")

        if response.status_code >= 400:
            raise IntervalsError(
                f"Intervals.icu returned {response.status_code} for {path}: "
                f"{response.text[:400]}"
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def _athlete_path(self, suffix: str) -> str:
        return f"/athlete/{self.athlete_id}{suffix}"

    # ------------------------------------------------------------------- reads

    def get_athlete(self) -> dict:
        """Profile including sport settings, thresholds and zones."""
        return self._request("GET", self._athlete_path(""))

    def list_activities(self, oldest: str, newest: str) -> list[dict]:
        """Completed activities in a local-date range (YYYY-MM-DD, inclusive)."""
        return self._request(
            "GET",
            self._athlete_path("/activities"),
            params={"oldest": oldest, "newest": newest},
        ) or []

    def get_activity(self, activity_id: str, intervals: bool = False) -> dict:
        """Full activity detail; ``intervals=True`` adds detected lap/interval data."""
        return self._request(
            "GET",
            f"/activity/{activity_id}",
            params={"intervals": str(intervals).lower()},
        )

    def get_streams(self, activity_id: str, types: list[str]) -> list[dict]:
        """Raw per-second streams. Returns a list of {type, data} objects."""
        return self._request(
            "GET",
            f"/activity/{activity_id}/streams",
            params={"types": ",".join(types)},
        ) or []

    def get_wellness(self, oldest: str, newest: str) -> list[dict]:
        """Daily wellness records: HRV, resting HR, sleep, weight, readiness."""
        return self._request(
            "GET",
            self._athlete_path("/wellness"),
            params={"oldest": oldest, "newest": newest},
        ) or []

    def list_events(self, oldest: str, newest: str) -> list[dict]:
        """Calendar events, including planned (future) workouts."""
        return self._request(
            "GET",
            self._athlete_path("/events"),
            params={"oldest": oldest, "newest": newest},
        ) or []

    # ------------------------------------------------------------------ writes

    def create_event(self, event: dict) -> dict:
        """Create a single calendar event (planned workout, note, race)."""
        return self._request("POST", self._athlete_path("/events"), json=event)

    def upsert_events(self, events: list[dict]) -> list[dict]:
        """Bulk create/update keyed on ``external_id``. Safe to re-run."""
        return self._request(
            "POST",
            self._athlete_path("/events/bulk"),
            params={"upsert": "true"},
            json=events,
        ) or []

    def delete_event(self, event_id: int) -> None:
        self._request("DELETE", self._athlete_path(f"/events/{event_id}"))

    def close(self) -> None:
        self._http.close()


# --------------------------------------------------------------------- helpers


def downsample(values: list, target_points: int = 200) -> list:
    """Reduce a stream to at most ``target_points`` by block averaging.

    A three hour trail run is ~11k samples per stream. Handing that to a model
    verbatim wastes the context window and buys no extra insight, so numeric
    streams are averaged into blocks and non-numeric ones are sampled.
    """
    if not values or len(values) <= target_points:
        return values

    block = len(values) / target_points
    out = []
    for i in range(target_points):
        chunk = values[int(i * block) : int((i + 1) * block)] or [values[int(i * block)]]
        numeric = [v for v in chunk if isinstance(v, (int, float))]
        if numeric:
            out.append(round(sum(numeric) / len(numeric), 2))
        else:
            out.append(chunk[0])
    return out


def summarize_stream(name: str, values: list) -> dict:
    """Min/max/mean plus a downsampled series, which is what a coach needs."""
    numeric = [v for v in values if isinstance(v, (int, float))]
    summary: dict[str, Any] = {"type": name, "points": len(values)}
    if numeric:
        summary |= {
            "min": round(min(numeric), 2),
            "max": round(max(numeric), 2),
            "mean": round(sum(numeric) / len(numeric), 2),
        }
    summary["series"] = downsample(values)
    return summary
