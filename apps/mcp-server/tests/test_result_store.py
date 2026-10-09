"""Result store tests: save, fetch, expiry, eviction, pagination."""

import pytest

from src import result_store


@pytest.fixture(autouse=True)
def clean_store():
    result_store.clear()
    yield
    result_store.clear()


def test_save_and_get_round_trip():
    payload = {"satellite": "ISS", "daily": [{"day": "2026-09-18"}]}
    result_id = result_store.save_result(payload)
    assert isinstance(result_id, str) and len(result_id) == 16
    assert result_store.get_result(result_id) == payload


def test_ids_are_unique():
    first = result_store.save_result({"n": 1})
    second = result_store.save_result({"n": 2})
    assert first != second
    assert result_store.count() == 2


def test_unknown_and_blank_ids_fail_with_guidance():
    with pytest.raises(ValueError, match="Unknown result_id"):
        result_store.get_result("missing")
    with pytest.raises(ValueError, match="non-empty string"):
        result_store.get_result("  ")
    with pytest.raises(ValueError, match="non-empty string"):
        result_store.get_result(None)


def test_expired_results_are_dropped(monkeypatch):
    result_id = result_store.save_result({"n": 1})
    result_store._results[result_id]["saved_at"] -= result_store.TTL_SECONDS + 1
    with pytest.raises(ValueError, match="expired"):
        result_store.get_result(result_id)
    assert result_store.count() == 0


def test_store_evicts_oldest_beyond_cap(monkeypatch):
    monkeypatch.setattr(result_store, "MAX_RESULTS", 3)
    ids = [result_store.save_result({"n": n}) for n in range(4)]
    assert result_store.count() == 3
    with pytest.raises(ValueError, match="Unknown result_id"):
        result_store.get_result(ids[0])
    assert result_store.get_result(ids[3]) == {"n": 3}


def test_paginate_slices_and_validates():
    items = list(range(5))
    page = result_store.paginate(items, 1, 2, "daily")
    assert (page["offset"], page["limit"], page["total"]) == (1, 2, 5)
    assert page["items"] == [1, 2]
    assert result_store.paginate(items, 10, 5, "daily")["items"] == []
    with pytest.raises(ValueError, match="between 1 and 366"):
        result_store.paginate(items, 0, 0, "daily")
    with pytest.raises(ValueError, match="between 1 and 366"):
        result_store.paginate(items, 0, 400, "daily")
    with pytest.raises(ValueError, match="0 or more"):
        result_store.paginate(items, -1, 5, "daily")
    with pytest.raises(ValueError, match="must be an integer"):
        result_store.paginate(items, True, 5, "daily")


def test_drafts_save_fetch_expire():
    draft_id = result_store.save_draft({"satellite": "ISS"})
    assert draft_id.startswith("draft_")
    assert result_store.get_draft(draft_id) == {"satellite": "ISS"}
    with pytest.raises(ValueError, match="Unknown draft_id"):
        result_store.get_draft("draft_missing")
    with pytest.raises(ValueError, match="non-empty string"):
        result_store.get_draft("  ")
    result_store._drafts[draft_id]["saved_at"] -= result_store.TTL_SECONDS + 1
    with pytest.raises(ValueError, match="expired"):
        result_store.get_draft(draft_id)
