"""The one thin door to Plaid's API (official plaid-python, pinned major).

Everything network lives here, everything returns plain dicts, and the
import of `plaid` is lazy — the rest of the package (mappers, writer, the
OFX/CSV lanes) runs on machines that never installed it. Credentials come
from $VAULT/.secrets/plaid.env via `sara.ingest`; nothing in this module
reads the environment on its own, and access tokens never appear in
errors or output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

Raw = dict[str, Any]

PLAID_ENVS = ("production", "sandbox")
SYNC_PAGE_COUNT = 500  # Plaid's max page size — fewest round trips
_MUTATION_ERROR = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"


@dataclass(frozen=True)
class PlaidCreds:
    client_id: str
    secret: str
    environment: str = "production"


class PlaidUnavailable(SystemExit):
    """plaid-python is not importable in this interpreter."""


class _PlaidApi(Protocol):  # the narrow slice of plaid_api.PlaidApi we call
    def transactions_sync(self, transactions_sync_request: Any) -> Any: ...
    def investments_transactions_get(self, investments_transactions_get_request: Any) -> Any: ...
    def investments_holdings_get(self, investments_holdings_get_request: Any) -> Any: ...
    def accounts_get(self, accounts_get_request: Any) -> Any: ...
    def link_token_create(self, link_token_create_request: Any) -> Any: ...
    def item_public_token_exchange(self, item_public_token_exchange_request: Any) -> Any: ...


def _plaid() -> Any:
    try:
        import plaid
    except ImportError as e:
        raise PlaidUnavailable(
            "plaid-python is not installed in this environment — "
            "init_vault.sh installs it into the vault venv "
            "(or: <vault>/.venv/bin/pip install 'plaid-python>=42,<43')") from e
    return plaid


def make_client(creds: PlaidCreds) -> _PlaidApi:
    plaid = _plaid()
    from plaid.api import plaid_api

    if creds.environment not in PLAID_ENVS:
        raise SystemExit(f"PLAID_ENV must be one of {'/'.join(PLAID_ENVS)}, "
                         f"got {creds.environment!r}")
    host: str = getattr(plaid.Environment, creds.environment.capitalize())
    configuration = plaid.Configuration(
        host=host,
        api_key={"clientId": creds.client_id, "secret": creds.secret},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def _error_body(exc: Exception) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if not isinstance(body, str):
        return {}
    import json

    from sara.typed import as_dict

    try:
        return as_dict(json.loads(body))
    except ValueError:
        return {}


def api_error_summary(exc: Exception) -> str:
    """A safe one-line summary of a Plaid ApiException (no tokens, no dumps)."""
    parsed = _error_body(exc)
    if parsed:
        return f"{parsed.get('error_code', '?')}: {str(parsed.get('error_message', ''))[:160]}"
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def _error_code(exc: Exception) -> str:
    return str(_error_body(exc).get("error_code") or "")


def sync_transactions(client: _PlaidApi, access_token: str, cursor: str) -> list[Raw]:
    """All pages of one /transactions/sync pass, from `cursor` to caught-up.

    On Plaid's TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION the whole pass
    restarts once from the ORIGINAL cursor (per Plaid's guidance) — pages
    are only ever handed back as a complete, consistent set.
    """
    plaid = _plaid()
    from plaid.model.transactions_sync_request import TransactionsSyncRequest

    def one_pass() -> list[Raw]:
        pages: list[Raw] = []
        next_cursor = cursor
        while True:
            kwargs: dict[str, Any] = {"access_token": access_token,
                                      "count": SYNC_PAGE_COUNT}
            if next_cursor:
                kwargs["cursor"] = next_cursor
            resp = client.transactions_sync(TransactionsSyncRequest(**kwargs))
            page: Raw = resp.to_dict()
            pages.append(page)
            next_cursor = str(page.get("next_cursor") or "")
            if not page.get("has_more"):
                return pages

    try:
        return one_pass()
    except plaid.ApiException as e:  # type: ignore[attr-defined]
        if _error_code(e) == _MUTATION_ERROR:
            return one_pass()
        raise


def get_investments(client: _PlaidApi, access_token: str,
                    start: date, end: date) -> tuple[list[Raw], Raw]:
    """(/investments/transactions pages over [start, end], /holdings response)."""
    _plaid()
    from plaid.model.investments_holdings_get_request import (
        InvestmentsHoldingsGetRequest,
    )
    from plaid.model.investments_transactions_get_request import (
        InvestmentsTransactionsGetRequest,
    )
    from plaid.model.investments_transactions_get_request_options import (
        InvestmentsTransactionsGetRequestOptions,
    )

    pages: list[Raw] = []
    offset = 0
    while True:
        resp = client.investments_transactions_get(InvestmentsTransactionsGetRequest(
            access_token=access_token, start_date=start, end_date=end,
            options=InvestmentsTransactionsGetRequestOptions(count=500, offset=offset),
        ))
        page: Raw = resp.to_dict()
        pages.append(page)
        offset += len(page.get("investment_transactions") or [])
        total = int(page.get("total_investment_transactions") or 0)
        if offset >= total:
            break
    holdings: Raw = client.investments_holdings_get(
        InvestmentsHoldingsGetRequest(access_token=access_token)).to_dict()
    return pages, holdings


def get_accounts(client: _PlaidApi, access_token: str) -> list[Raw]:
    _plaid()
    from plaid.model.accounts_get_request import AccountsGetRequest

    from sara.typed import as_dicts

    resp: Raw = client.accounts_get(AccountsGetRequest(access_token=access_token)).to_dict()
    return as_dicts(resp.get("accounts"))


def create_link_token(client: _PlaidApi, *, client_name: str, user_id: str,
                      products: list[str], redirect_uri: str = "",
                      access_token: str = "") -> str:
    """A short-lived link_token. Pass `access_token` for UPDATE mode (repair
    an existing Item without burning a lifetime slot); products must then be
    empty per Plaid's contract."""
    _plaid()
    from plaid.model.country_code import CountryCode
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import (
        LinkTokenCreateRequestUser,
    )
    from plaid.model.products import Products

    kwargs: dict[str, Any] = {
        "client_name": client_name,
        "language": "en",
        "country_codes": [CountryCode("US")],
        "user": LinkTokenCreateRequestUser(client_user_id=user_id),
    }
    if access_token:
        kwargs["access_token"] = access_token
    else:
        kwargs["products"] = [Products(p) for p in products]
    if redirect_uri:
        kwargs["redirect_uri"] = redirect_uri
    resp: Raw = client.link_token_create(LinkTokenCreateRequest(**kwargs)).to_dict()
    return str(resp.get("link_token") or "")


def exchange_public_token(client: _PlaidApi, public_token: str) -> tuple[str, str]:
    """public_token -> (access_token, item_id)."""
    _plaid()
    from plaid.model.item_public_token_exchange_request import (
        ItemPublicTokenExchangeRequest,
    )

    resp: Raw = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)).to_dict()
    return str(resp.get("access_token") or ""), str(resp.get("item_id") or "")
