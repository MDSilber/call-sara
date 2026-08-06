"""The three-tier classifier, end to end through the real rewrite path.

Tier 2 (the Plaid signal) is exercised against the scratch vault's actual
ledger files — apply, low-confidence queue, unmapped queue, missing-target
queue, and the [plaid_category_map] override — and tier 3 with an injected
ModelCall (no network, no SDK): apply at threshold, queue below it, refuse
unknown categories, and refuse a malformed reply wholesale. The write path
is the same atomic + bean-check rewrite recategorize uses; with
FINANCE_TEST_VENV set the real bean-check gate runs too.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from sara.classify import (
    ModelUsage,
    Summary,
    parse_model_reply,
    run_classification,
)

from .conftest import RULES_TOML, needs_venv

CHECKING = "Assets:US:Demo:Checking0766"


@pytest.fixture()
def vault(fresh_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The scratch vault with the rules.toml parse cache dropped, so tests
    that rewrite rules.toml are actually read (and the standard parse is
    restored for the rest of the suite afterwards)."""
    monkeypatch.setattr("sara.vault._rules_cache", None)
    return fresh_vault


def seed(vault: Path, *entries: str) -> Path:
    f = vault / "ledger" / "2026.beancount"
    f.write_text(f.read_text() + "\n" + "\n\n".join(e.strip("\n") + "\n" for e in entries))
    return f


def txn(payee: str, amount: str, plaid: str | None = None,
        counter: str = "Expenses:Uncategorized", account: str = CHECKING,
        when: str = "2026-07-02") -> str:
    plaid_line = f'  plaid-category: "{plaid}"\n' if plaid else ""
    return (f'{when} * "{payee}" ""\n'
            f'  plaid-id: "t-{abs(hash((payee, amount, when)))}"\n'
            f"{plaid_line}"
            f"  {account}  {amount} USD\n"
            f"  {counter}\n")


class FakeModel:
    """An injected ModelCall: canned replies, captured requests."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, str, int]] = []

    def __call__(self, model: str, system: str, user: str,
                 max_tokens: int) -> tuple[str, ModelUsage]:
        self.calls.append((model, system, user, max_tokens))
        return self.replies.pop(0), ModelUsage(1_000, 200)


# ------------------------------------------------------------------- tier 2
def test_tier2_high_confidence_applies_through_rewrite(vault: Path) -> None:
    f = seed(vault, txn("CORNER CAFE", "-12.50", "FOOD_AND_DRINK_COFFEE (high)"))
    s = run_classification(write=True, skip_model=True)
    assert s == Summary(0, 1, 0, 0, True)
    text = f.read_text()
    assert "  Expenses:Food:Dining\n" in text
    assert '  classifier: "plaid:FOOD_AND_DRINK_COFFEE"\n' in text
    assert "Expenses:Uncategorized\n" not in text.split("Opening balances")[-1]


def test_tier2_low_confidence_queues(vault: Path) -> None:
    f = seed(vault, txn("CORNER CAFE", "-12.50", "FOOD_AND_DRINK_COFFEE (low)"))
    before = f.read_text()
    s = run_classification(write=True, skip_model=True)
    assert (s.applied, s.queued) == (0, 1)
    assert f.read_text() == before  # nothing rewritten


def test_tier2_very_high_floor_from_config(vault: Path) -> None:
    (vault / "rules.toml").write_text(
        RULES_TOML + '\n[classification]\nplaid_min_confidence = "very_high"\n')
    seed(vault,
         txn("CORNER CAFE", "-12.50", "FOOD_AND_DRINK_COFFEE (high)"),
         txn("BIG MARKET", "-80.00", "FOOD_AND_DRINK_GROCERIES (very_high)",
             when="2026-07-03"))
    s = run_classification(write=True, skip_model=True)
    assert (s.applied_plaid, s.queued) == (1, 1)  # only very_high clears the bar


def test_tier2_unmapped_category_queues(vault: Path) -> None:
    seed(vault, txn("WIRE OUT", "-500.00", "TRANSFER_OUT_WIRE (very_high)"))
    s = run_classification(write=True, skip_model=True)
    assert (s.applied, s.queued) == (0, 1)


def test_tier2_missing_target_account_queues(vault: Path) -> None:
    # TRANSPORTATION_GAS maps to Expenses:Transport, which the scratch chart
    # never opens — applying it would fail bean-check, so it must queue.
    seed(vault, txn("SHELL OIL", "-40.00", "TRANSPORTATION_GAS (very_high)"))
    s = run_classification(write=True, skip_model=True)
    assert (s.applied, s.queued) == (0, 1)


def test_plaid_category_map_override_wins(vault: Path) -> None:
    (vault / "rules.toml").write_text(
        RULES_TOML + '\n[plaid_category_map]\n'
                     '"FOOD_AND_DRINK_COFFEE" = "Expenses:Food:Groceries"\n')
    f = seed(vault, txn("CORNER CAFE", "-12.50", "FOOD_AND_DRINK_COFFEE (high)"))
    s = run_classification(write=True, skip_model=True)
    assert s.applied_plaid == 1
    assert "  Expenses:Food:Groceries\n" in f.read_text()


def test_payee_rule_always_wins_over_plaid(vault: Path) -> None:
    # WHOLE FOODS has a [[payee_rules]] entry -> Groceries; Plaid says
    # restaurant at high confidence. The human-taught rule must win.
    f = seed(vault, txn("WHOLE FOODS MARKET", "-64.10",
                        "FOOD_AND_DRINK_RESTAURANT (very_high)"))
    s = run_classification(write=True, skip_model=True)
    assert (s.applied_rule, s.applied_plaid) == (1, 0)
    text = f.read_text()
    assert "  Expenses:Food:Groceries\n" in text
    assert '  classifier: "rule"\n' in text


def test_income_queue_is_worked_too(vault: Path) -> None:
    f = seed(vault, txn("MONTHLY INTEREST", "4.20",
                        "INCOME_INTEREST_EARNED (high)", counter="Income:US:Other"))
    s = run_classification(write=True, skip_model=True)
    assert s.applied_plaid == 1
    assert "  Income:US:Interest\n" in f.read_text()


def test_second_run_finds_nothing(vault: Path) -> None:
    seed(vault, txn("CORNER CAFE", "-12.50", "FOOD_AND_DRINK_COFFEE (high)"))
    assert run_classification(write=True, skip_model=True).applied == 1
    again = run_classification(write=True, skip_model=True)
    assert (again.applied, again.queued) == (0, 0)


def test_dry_run_writes_nothing(vault: Path) -> None:
    f = seed(vault, txn("CORNER CAFE", "-12.50", "FOOD_AND_DRINK_COFFEE (high)"))
    before = f.read_text()
    s = run_classification(write=False, skip_model=True)
    assert (s.applied_plaid, s.wrote) == (1, False)
    assert f.read_text() == before


# ------------------------------------------------------------------- tier 3
def reply(*rows: str) -> str:
    return '{"results": [' + ", ".join(rows) + "]}"


def row(i: int, category: str, conf: str, reason: str = "looks right") -> str:
    return (f'{{"id": {i}, "category": "{category}", '
            f'"confidence": {conf}, "reason": "{reason}"}}')


def test_model_applies_at_threshold_and_queues_below(vault: Path) -> None:
    f = seed(vault,
             txn("MYSTERY SANDWICHES", "-18.00"),
             txn("UNKNOWABLE LLC", "-99.00", when="2026-07-04"))
    fake = FakeModel(reply(row(0, "Expenses:Food:Dining", "0.93"),
                           row(1, "Expenses:Food:Groceries", "0.55")))
    s = run_classification(write=True, model_call=fake)
    assert (s.applied_model, s.queued) == (1, 1)
    text = f.read_text()
    assert '  classifier: "haiku:0.93"\n' in text
    assert text.count("Expenses:Uncategorized\n") == 1  # the unsure one stayed
    # the request itself: right model, chart + payees in context, txns as JSON
    assert len(fake.calls) == 1
    model, system, user, _ = fake.calls[0]
    assert model == "claude-haiku-4-5"
    assert "Expenses:Food:Dining" in system and "WHOLE FOODS" in system
    sent = json.loads(user)["transactions"]
    assert [t["payee"] for t in sent] == ["MYSTERY SANDWICHES", "UNKNOWABLE LLC"]
    assert sent[0]["amount"] == "-18.00"


def test_model_never_invents_categories(vault: Path) -> None:
    f = seed(vault, txn("MYSTERY SANDWICHES", "-18.00"))
    before = f.read_text()
    fake = FakeModel(reply(row(0, "Expenses:Made:Up", "0.99")))
    s = run_classification(write=True, model_call=fake)
    assert (s.applied_model, s.queued) == (0, 1)
    assert f.read_text() == before


def test_malformed_model_reply_refuses_whole_batch(vault: Path) -> None:
    f = seed(vault,
             txn("MYSTERY SANDWICHES", "-18.00"),
             txn("UNKNOWABLE LLC", "-99.00", when="2026-07-04"))
    before = f.read_text()
    fake = FakeModel("I think these are probably groceries!")
    s = run_classification(write=True, model_call=fake)
    assert (s.applied_model, s.queued) == (0, 2)
    assert f.read_text() == before


def test_model_limit_caps_what_is_sent(vault: Path) -> None:
    seed(vault,
         txn("MYSTERY SANDWICHES", "-18.00"),
         txn("UNKNOWABLE LLC", "-99.00", when="2026-07-04"))
    fake = FakeModel(reply(row(0, "Expenses:Food:Dining", "0.91")))
    s = run_classification(write=True, model_call=fake, model_limit=1)
    assert (s.applied_model, s.queued) == (1, 1)
    assert len(fake.calls) == 1
    assert len(json.loads(fake.calls[0][2])["transactions"]) == 1


def test_no_key_and_no_injection_skips_model_tier(vault: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seed(vault, txn("MYSTERY SANDWICHES", "-18.00"))
    s = run_classification(write=True)  # no model_call injected, no key file
    assert (s.applied, s.queued) == (0, 1)


# ------------------------------------------------------------- reply parsing
def test_parse_model_reply_edges() -> None:
    ok = parse_model_reply(reply(row(0, "Expenses:X", "1")), 1)
    assert isinstance(ok, dict) and ok[0][1] == Decimal(1)  # int confidence fine
    assert isinstance(parse_model_reply("nope", 1), str)
    assert isinstance(parse_model_reply(reply(row(3, "Expenses:X", "0.9")), 1), str)
    assert isinstance(parse_model_reply(
        reply(row(0, "Expenses:X", "0.9"), row(0, "Expenses:Y", "0.9")), 1), str)
    assert isinstance(parse_model_reply(reply(row(0, "Expenses:X", "1.7")), 1), str)


# ----------------------------------------------------- bean-check gated path
@needs_venv
def test_write_survives_real_bean_check(vault: Path) -> None:
    f = seed(vault, txn("CORNER CAFE", "-12.50", "FOOD_AND_DRINK_COFFEE (high)"))
    s = run_classification(write=True, skip_model=True)
    assert s.applied_plaid == 1 and "Expenses:Food:Dining" in f.read_text()
