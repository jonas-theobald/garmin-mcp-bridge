"""Tests for stream downsampling and summarization (context discipline, see
CLAUDE.md "Context discipline").
"""

from __future__ import annotations

from garmin_mcp_bridge.client import downsample, summarize_stream


def test_downsample_reduces_to_at_most_target_points():
    values = list(range(1000))
    result = downsample(values, target_points=200)
    assert len(result) <= 200


def test_downsample_leaves_shorter_series_unchanged():
    values = [1, 2, 3, 4, 5]
    assert downsample(values, target_points=200) == values


def test_downsample_handles_empty_list():
    assert downsample([], target_points=200) == []


def test_downsample_handles_non_numeric_values():
    # latlng streams are [lat, lng] pairs, not plain numbers.
    values = [[52.5 + i * 0.0001, 13.4 + i * 0.0001] for i in range(500)]
    result = downsample(values, target_points=200)
    assert len(result) <= 200
    assert all(isinstance(point, list) for point in result)


def test_summarize_stream_min_max_mean_computed_from_raw_data():
    values = list(range(1000))  # min 0, max 999, mean 499.5
    summary = summarize_stream("heartrate", values)

    assert summary["points"] == 1000
    assert summary["min"] == 0
    assert summary["max"] == 999
    assert summary["mean"] == 499.5
    # The series is downsampled, but min/max/mean must reflect the raw data,
    # not the reduced series.
    assert len(summary["series"]) <= 200
