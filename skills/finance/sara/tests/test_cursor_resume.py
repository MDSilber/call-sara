"""Cursor discipline — exactly-once ingestion across crashes and mutations.

Three invariants:
  1. one sync pass drains every page from the stored cursor;
  2. Plaid's mutation-during-pagination error restarts the whole pass from
     the ORIGINAL cursor (never a mid-pass one), once;
  3. the stored cursor advances only after a successful --write — a crash
     (or a report-only run) re-fetches, and dedupe makes the re-fetch free.
"""

from __future__ import annotations

from typing import Any

import pytest

import sara.plaid_api as plaid_api
from sara.plaid_api import sync_transactions


class FakeResp:
    def __init__(self, page: dict[str, Any]) -> None:
        self._page = page

    def to_dict(self) -> dict[str, Any]:
        return self._page


class FakePlaidError(Exception):
    def __init__(self, code: str) -> None:
        self.body = f'{{"error_code": "{code}"}}'


class FakeClient:
    """Serves a scripted cursor->page graph and records every request."""

    def __init__(self, pages: dict[str, dict[str, Any]],
                 poison: set[str] | None = None) -> None:
        self.pages = pages
        self.poison = set(poison or ())  # cursors that raise ONCE, then heal
        self.calls: list[str] = []

    def transactions_sync(self, request: Any) -> FakeResp:
        cursor = dict(request).get("cursor", "")
        self.calls.append(cursor)
        if cursor in self.poison:
            self.poison.discard(cursor)
            raise FakePlaidError("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION")
        return FakeResp(self.pages[cursor])


class FakeRequest(dict[str, Any]):  # stands in for TransactionsSyncRequest
    pass


@pytest.fixture(autouse=True)
def fake_plaid(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePlaidModule:
        ApiException = FakePlaidError

    monkeypatch.setattr(plaid_api, "_plaid", lambda: FakePlaidModule)
    import sys
    import types

    mod = types.ModuleType("plaid.model.transactions_sync_request")
    mod.TransactionsSyncRequest = FakeRequest  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "plaid.model.transactions_sync_request", mod)


def three_pages() -> dict[str, dict[str, Any]]:
    return {
        "": {"added": [{"transaction_id": "t1"}], "next_cursor": "c1", "has_more": True},
        "c1": {"added": [{"transaction_id": "t2"}], "next_cursor": "c2", "has_more": True},
        "c2": {"added": [{"transaction_id": "t3"}], "next_cursor": "c3", "has_more": False},
    }


def test_drains_every_page_from_the_stored_cursor() -> None:
    client = FakeClient(three_pages())
    pages = sync_transactions(client, "tok", "")  # type: ignore[arg-type]
    assert [p["next_cursor"] for p in pages] == ["c1", "c2", "c3"]
    assert client.calls == ["", "c1", "c2"]


def test_resumes_mid_history_from_a_saved_cursor() -> None:
    client = FakeClient(three_pages())
    pages = sync_transactions(client, "tok", "c1")  # type: ignore[arg-type]
    assert [t["transaction_id"] for p in pages for t in p["added"]] == ["t2", "t3"]


def test_mutation_mid_pass_restarts_from_the_original_cursor() -> None:
    client = FakeClient(three_pages(), poison={"c1"})
    pages = sync_transactions(client, "tok", "")  # type: ignore[arg-type]
    # first pass: "" then c1 (poisoned) -> restart: "", c1, c2 — never resumes
    # from the mid-pass cursor, so no page is skipped
    assert client.calls == ["", "c1", "", "c1", "c2"]
    assert [t["transaction_id"] for p in pages for t in p["added"]] == ["t1", "t2", "t3"]


def test_a_second_mutation_error_propagates() -> None:
    client = FakeClient(three_pages(), poison={"c1"})
    client.poison = {"c1", ""}  # poison the restart too
    with pytest.raises(FakePlaidError):
        sync_transactions(client, "tok", "")  # type: ignore[arg-type]


def test_non_mutation_errors_propagate_untouched() -> None:
    class Angry:
        def transactions_sync(self, request: Any) -> FakeResp:
            raise FakePlaidError("RATE_LIMIT_EXCEEDED")

    with pytest.raises(FakePlaidError):
        sync_transactions(Angry(), "tok", "")  # type: ignore[arg-type]
