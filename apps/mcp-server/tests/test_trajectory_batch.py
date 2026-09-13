"""Edge cases for format_trajectory_batch: malformed entries must be skipped,
never crash the whole batch (MCP stdio framing must stay intact)."""

from datetime import datetime, timezone

from src import schema_formatter as fmt

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)


def good(t=T0):
    return (t, 10.0, 20.0, 400.0)


def test_valid_batch_passes_through():
    out = fmt.format_trajectory_batch([good(T0), good(T1)])
    assert len(out) == 2
    assert out[0]["position_lla"] == fmt.format_position_lla(10.0, 20.0, 400.0)


def test_short_tuple_entry_is_skipped():
    out = fmt.format_trajectory_batch([good(T0), (T1, 10.0, 20.0), good(T1)])
    assert len(out) == 2


def test_non_iterable_entry_is_skipped():
    out = fmt.format_trajectory_batch([good(T0), None, 42, good(T1)])
    assert len(out) == 2


def test_out_of_range_coords_entry_is_skipped():
    out = fmt.format_trajectory_batch([good(T0), (T1, 999.0, 20.0, 400.0)])
    assert len(out) == 1


def test_empty_batch_returns_empty():
    assert fmt.format_trajectory_batch([]) == []


def test_all_bad_batch_returns_empty():
    assert fmt.format_trajectory_batch([None, (T1, 1.0)]) == []
