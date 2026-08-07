"""Live category suggestion for ONE transaction — the popover's sidekick.

The teach popover opens instantly; GET /api/suggest answers on the side.
The ladder mirrors the classifier's trust order, sized for a single txn:

  1. rules.toml [[payee_rules]] would-match       -> "rule"   always preselects
  2. the banked Plaid category through the map    -> "plaid"  preselects at the
     classifier's own confidence bar
  3. one on-device Apple Intelligence call        -> "apple"  preselects at the
     apple floor — only when the shim binary already exists AND its
     availability probe said yes (probed once, cached), under a hard 4s
     budget so a slow model can never hold the popover hostage.

The P2P guard rides every rung: a person-payee's suggestion is still shown
("Sara thinks…") but ``preselect`` stays false — a person needs the owner's
word, so the model's verdict is a preloaded option, never a default. Apple
verdicts are cached per payee for the server's lifetime; teaching a rule
resets the cache (a new rule changes what every tier would say).
"""

from __future__ import annotations

import json
import subprocess
import threading
from decimal import Decimal
from typing import TypeAlias

from sara.classify import (
    CONF_RANK,
    PLAID_CATEGORY_META,
    REVIEW_ACCOUNTS,
    SHIM_BINARY,
    apple_payee,
    guard_reason,
    load_config,
    model_categories,
    parse_model_reply,
    plaid_map,
    rule_examples,
)
from sara.ledger.queries import opened_accounts
from sara.rules import match_rule
from sara.typed import as_dict

from .readmodel import DB, Row

SUGGEST_TIMEOUT = 4.0  # seconds — the popover's whole on-device budget
PROBE_TIMEOUT = 4.0

Judgment: TypeAlias = tuple[str, Decimal, str]  # (category, confidence, reason)

_probe_lock = threading.Lock()
_probe_ok: bool | None = None  # None = not yet probed this server lifetime
_model_lock = threading.Lock()  # one on-device generation at a time
_cache_lock = threading.Lock()
_apple_cache: dict[str, Judgment | None] = {}  # payee key -> verdict (or miss)


def reset_cache() -> None:
    """Drop the per-payee apple verdicts — call whenever rules.toml or the
    chart changes (a taught rule outranks any cached model opinion)."""
    with _cache_lock:
        _apple_cache.clear()


def suggest(posting_id: int) -> dict[str, object] | None:
    """The suggestion payload for one posting, or None when the id is
    unknown. Shape: {posting_id, payee, guarded, guard, suggestion} where
    suggestion is {account, source, confidence, reason, preselect} | null."""
    row = DB.one(
        """
        SELECT p.posting_id, p.date, p.payee, p.account, p.other_account,
               p.amount_home, t.meta AS txn_meta
        FROM postings p
        LEFT JOIN transactions t ON t.txn_id = p.txn_id
        WHERE p.posting_id = $pid
        """,
        {"pid": posting_id})
    if row is None:
        return None
    payee = str(row.get("payee") or "").strip()
    cfg = load_config()
    guard = guard_reason(payee) if cfg.p2p_guard else None
    out: dict[str, object] = {
        "posting_id": posting_id,
        "payee": payee,
        "guarded": guard is not None,
        "guard": guard,
        "suggestion": None,
    }
    meta = _txn_meta(row.get("txn_meta"))
    chart = opened_accounts()

    # rung 1 — would a taught rule place it?
    ruled = match_rule(payee, _ofx_type(meta), _signed_amount(row))
    if ruled in REVIEW_ACCOUNTS:
        return out  # the human pinned it here; suggesting otherwise is noise
    if ruled and ruled in chart:
        out["suggestion"] = _payload(ruled, "rule", None,
                                     "matches a rule you taught",
                                     preselect=guard is None)
        return out

    # rung 2 — the banked Plaid signal through the category map
    detailed, conf_word = _plaid_category(meta)
    target = plaid_map().get(detailed, "")
    if target and target in chart and target not in REVIEW_ACCOUNTS:
        confident = CONF_RANK.get(conf_word, -1) >= CONF_RANK[cfg.plaid_min]
        out["suggestion"] = _payload(
            target, "plaid", None,
            f"your bank filed it under {detailed} ({conf_word or 'no confidence'})",
            preselect=guard is None and confident)
        return out

    # rung 3 — one on-device call, if the apple rung is armed and alive
    if payee and cfg.tier3 and "apple" in cfg.backends and _apple_ready():
        verdict = _apple_judgment(payee, row, detailed, chart)
        if verdict is not None:
            category, confidence, reason = verdict
            out["suggestion"] = _payload(
                category, "apple", float(confidence), reason,
                preselect=guard is None and confidence >= cfg.floor("apple"))
    return out


def _payload(account: str, source: str, confidence: float | None,
             reason: str, preselect: bool) -> dict[str, object]:
    return {"account": account, "source": source, "confidence": confidence,
            "reason": reason, "preselect": preselect}


# ------------------------------------------------------------- posting bits
def _txn_meta(raw: object) -> dict[str, str]:
    """The transaction's metadata (plaid-category and friends live on the
    TRANSACTION, not the posting) out of its JSON column."""
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        data: object = json.loads(raw)
    except ValueError:
        return {}
    return {k: v for k, v in as_dict(data).items() if isinstance(v, str)}


def _ofx_type(meta: dict[str, str]) -> str:
    return (meta.get("ofx-type") or meta.get("plaid-type")
            or meta.get("chase-type") or meta.get("type") or "")


def _signed_amount(row: Row) -> Decimal:
    """The amount from the funding account's point of view (money out is
    negative) — the Expenses/Income leg carries the opposite sign."""
    raw = row.get("amount_home")
    if isinstance(raw, Decimal):
        return -raw
    if isinstance(raw, (int, float)):
        return -Decimal(str(raw))
    return Decimal(0)


def _plaid_category(meta: dict[str, str]) -> tuple[str, str]:
    pc = PLAID_CATEGORY_META.match(meta.get("plaid-category", ""))
    if not pc:
        return "", ""
    return pc.group(1), pc.group(2) or ""


# ---------------------------------------------------------------- the model
def _apple_ready() -> bool:
    """Binary on disk AND the availability probe said yes — probed once per
    server lifetime. Never builds the shim: the popover is not the place to
    discover Swift toolchains (the classify CLI owns that flow)."""
    global _probe_ok
    if not SHIM_BINARY.is_file():
        return False
    with _probe_lock:
        if _probe_ok is None:
            try:
                proc = subprocess.run([str(SHIM_BINARY), "--probe"],
                                      capture_output=True, text=True,
                                      timeout=PROBE_TIMEOUT)
                _probe_ok = proc.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                _probe_ok = False
        return _probe_ok


def _apple_judgment(payee: str, row: Row, hint: str,
                    chart: set[str]) -> Judgment | None:
    """One guided-generation call for one transaction, cached per payee.
    A timeout or refusal caches the miss too — the popover asks once."""
    key = payee.upper()
    with _cache_lock:
        if key in _apple_cache:
            return _apple_cache[key]
    categories = model_categories(chart)
    request = json.dumps({
        "categories": categories,
        "examples": rule_examples(),
        "txns": [{
            "id": 0,
            "date": str(row.get("date") or ""),
            # digit-soup ids read as no-language noise on-device
            "payee": apple_payee(payee),
            "amount": f"{_signed_amount(row):.2f}",
            "account": str(row.get("other_account") or ""),
            "hint": hint,
        }],
    }, ensure_ascii=False)
    verdict: Judgment | None = None
    try:
        with _model_lock:  # serialize generations; each holds the 4s budget
            proc = subprocess.run([str(SHIM_BINARY)], input=request,
                                  capture_output=True, text=True,
                                  timeout=SUGGEST_TIMEOUT)
        if proc.returncode == 0:
            parsed = parse_model_reply(proc.stdout, 1, id_key="index")
            if not isinstance(parsed, str) and 0 in parsed:
                category = parsed[0][0]
                if category in categories:
                    verdict = parsed[0]
    except (OSError, subprocess.TimeoutExpired):
        verdict = None
    with _cache_lock:
        _apple_cache[key] = verdict
    return verdict
