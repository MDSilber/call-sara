"""The three-tier classifier, end to end through the real rewrite path.

Tier 2 (the Plaid signal) is exercised against the scratch vault's actual
ledger files — apply, low-confidence queue, unmapped queue, missing-target
queue, and the [plaid_category_map] override — and tier 3 with injected
backends (no network, no SDK, no Swift): apply at threshold, queue below
it, refuse unknown categories, refuse a malformed reply wholesale, and the
whole escalation ladder — low-confidence residue climbing rung to rung,
unavailable rungs skipped with a note, the real Apple/Ollama backend
classes driven through their fake seams. The write path is the same atomic
+ bean-check rewrite recategorize uses; with FINANCE_TEST_VENV set the
real bean-check gate runs too.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sara.classify import (
    APPLE_BATCH_SIZE,
    APPLE_MAX_HISTORY_EXAMPLES,
    APPLE_MAX_RULE_EXAMPLES,
    RESPONSE_SCHEMA,
    AppleBackend,
    BatchContext,
    HaikuBackend,
    Judgment,
    ModelBackend,
    ModelUsage,
    OllamaBackend,
    ReviewTxn,
    Summary,
    _backend_flag,  # pyright: ignore[reportPrivateUsage]
    apple_payee,
    guard_reason,
    load_config,
    main,
    parse_model_reply,
    run_classification,
)
from sara.vault import reset_rules_cache

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
             txn("MYSTERY SANDWICHES #4", "-18.00"),
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
    assert [t["payee"] for t in sent] == ["MYSTERY SANDWICHES #4", "UNKNOWABLE LLC"]
    assert sent[0]["amount"] == "-18.00"


def test_model_never_invents_categories(vault: Path) -> None:
    f = seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
    before = f.read_text()
    fake = FakeModel(reply(row(0, "Expenses:Made:Up", "0.99")))
    s = run_classification(write=True, model_call=fake)
    assert (s.applied_model, s.queued) == (0, 1)
    assert f.read_text() == before


def test_malformed_model_reply_refuses_whole_batch(vault: Path) -> None:
    f = seed(vault,
             txn("MYSTERY SANDWICHES #4", "-18.00"),
             txn("UNKNOWABLE LLC", "-99.00", when="2026-07-04"))
    before = f.read_text()
    fake = FakeModel("I think these are probably groceries!")
    s = run_classification(write=True, model_call=fake)
    assert (s.applied_model, s.queued) == (0, 2)
    assert f.read_text() == before


def test_model_limit_caps_what_is_sent(vault: Path) -> None:
    seed(vault,
         txn("MYSTERY SANDWICHES #4", "-18.00"),
         txn("UNKNOWABLE LLC", "-99.00", when="2026-07-04"))
    fake = FakeModel(reply(row(0, "Expenses:Food:Dining", "0.91")))
    s = run_classification(write=True, model_call=fake, model_limit=1)
    assert (s.applied_model, s.queued) == (1, 1)
    assert len(fake.calls) == 1
    assert len(json.loads(fake.calls[0][2])["transactions"]) == 1


def test_no_key_and_no_injection_skips_model_tier(vault: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
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


# ------------------------------------------------------------ tier 3 ladder
class FakeBackend:
    """An injected ModelBackend rung: canned per-payee judgments, an optional
    probe skip, an optional mid-run explosion — and a record of every batch."""

    detail = "fake"
    batch_size = 40

    def __init__(self, name: str,
                 judgments: dict[str, Judgment | None] | None = None,
                 skip: str | None = None, boom: str | None = None) -> None:
        self.name = name
        self._judgments = judgments or {}
        self._skip = skip
        self._boom = boom
        self.batches: list[list[str]] = []  # payees, per classify_batch call

    def probe(self) -> str | None:
        return self._skip

    def classify_batch(self, txns: Sequence[ReviewTxn],
                       context: BatchContext) -> list[Judgment | None]:
        self.batches.append([t.payee for t in txns])
        if self._boom is not None:
            raise RuntimeError(self._boom)
        return [self._judgments.get(t.payee) for t in txns]


def _rungs(*backends: ModelBackend) -> list[ModelBackend]:
    """pyright proves ModelBackend conformance for whatever rides through."""
    return list(backends)


def J(category: str, conf: str, reason: str = "looks right") -> Judgment:
    return Judgment(category, Decimal(conf), reason)


def test_ladder_only_low_confidence_residue_escalates(vault: Path) -> None:
    f = seed(vault,
             txn("MYSTERY SANDWICHES #4", "-18.00"),
             txn("UNKNOWABLE LLC", "-99.00", when="2026-07-04"))
    apple = FakeBackend("apple", {
        "MYSTERY SANDWICHES #4": J("Expenses:Food:Dining", "0.91", "clearly lunch"),
        "UNKNOWABLE LLC": J("Expenses:Food:Groceries", "0.40", "shrug"),
    })
    haiku = FakeBackend("haiku", {"UNKNOWABLE LLC": J("Expenses:Food:Groceries", "0.88")})
    s = run_classification(write=True, backends=_rungs(apple, haiku))
    assert (s.applied_model, s.queued) == (2, 0)
    text = f.read_text()
    assert '  classifier: "apple:0.91"\n' in text
    assert '  classifier: "haiku:0.88"\n' in text
    assert apple.batches == [["MYSTERY SANDWICHES #4", "UNKNOWABLE LLC"]]
    assert haiku.batches == [["UNKNOWABLE LLC"]]  # the sure one never left rung 1


def test_ladder_both_unsure_queues_with_both_suggestions(
        vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = seed(vault, txn("UNKNOWABLE LLC", "-99.00"))
    before = f.read_text()
    apple = FakeBackend("apple",
                        {"UNKNOWABLE LLC": J("Expenses:Food:Dining", "0.62", "maybe catering")})
    haiku = FakeBackend("haiku", {"UNKNOWABLE LLC": J("Expenses:Food:Groceries", "0.41")})
    s = run_classification(write=True, backends=_rungs(apple, haiku))
    assert (s.applied_model, s.queued) == (0, 1)
    assert f.read_text() == before  # nothing rewritten
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "UNKNOWABLE" in ln and "suggest" in ln)
    assert "Expenses:Food:Dining (apple 0.62) — maybe catering" in line
    assert "Expenses:Food:Groceries (haiku 0.41)" in line
    assert line.index("apple 0.62") < line.index("haiku 0.41")  # best confidence first


def test_unavailable_rung_skips_to_next_with_note(
        vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
    apple = FakeBackend("apple", skip="Apple Intelligence is off")
    haiku = FakeBackend("haiku", {"MYSTERY SANDWICHES #4": J("Expenses:Food:Dining", "0.90")})
    s = run_classification(write=True, backends=_rungs(apple, haiku))
    assert (s.applied_model, s.queued) == (1, 0)
    assert apple.batches == []  # a skipped rung is never asked
    assert "apple: skipped — Apple Intelligence is off" in capsys.readouterr().out
    assert '  classifier: "haiku:0.90"\n' in f.read_text()


def test_rung_failure_escalates_and_notes(
        vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
    apple = FakeBackend("apple", boom="shim exploded")
    haiku = FakeBackend("haiku", {"MYSTERY SANDWICHES #4": J("Expenses:Food:Dining", "0.90")})
    s = run_classification(write=True, backends=_rungs(apple, haiku))
    assert (s.applied_model, s.queued) == (1, 0)
    assert "! apple: call failed — shim exploded" in capsys.readouterr().out


def review_txn(payee: str) -> ReviewTxn:
    return ReviewTxn(file=Path("2026.beancount"), start=0, when=date(2026, 7, 2),
                     payee=payee, ofx_type="", amount=Decimal("-18.00"),
                     primary=CHECKING, review_account="Expenses:Uncategorized",
                     target_line=3, meta_insert=1, classifier_line=None,
                     plaid_detailed="", plaid_confidence="", rewritable=True)


def test_apple_backend_end_to_end_via_fake_shim(vault: Path) -> None:
    f = seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
    requests: list[str] = []

    def runner(args: list[str], stdin: str) -> tuple[int, str, str]:
        if args == ["--probe"]:
            return 0, "available\n", ""
        requests.append(stdin)
        return 0, ('{"results": [{"index": 0, "category": "Expenses:Food:Dining", '
                   '"confidence": 0.92, "reason": "sandwich shop"}]}'), ""

    s = run_classification(write=True, backends=_rungs(AppleBackend(runner=runner)))
    assert (s.applied_model, s.queued) == (1, 0)
    assert '  classifier: "apple:0.92"\n' in f.read_text()
    sent = json.loads(requests[0])
    assert "Expenses:Food:Dining" in sent["categories"]
    assert "Expenses:Uncategorized" not in sent["categories"]  # review buckets never offered
    assert [t["payee"] for t in sent["txns"]] == ["MYSTERY SANDWICHES #4"]
    assert any("WHOLE FOODS" in e for e in sent["examples"])  # taught rules ride along


def test_apple_probe_unavailable_skips_onward(
        vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    msg = "unavailable: Apple Intelligence is off — enable it in System Settings"

    def runner(args: list[str], stdin: str) -> tuple[int, str, str]:
        assert args == ["--probe"]  # an unavailable shim must never be asked to classify
        return 1, "", msg + "\n"

    haiku = FakeBackend("haiku", {"MYSTERY SANDWICHES #4": J("Expenses:Food:Dining", "0.90")})
    seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
    s = run_classification(write=True, backends=_rungs(AppleBackend(runner=runner), haiku))
    assert (s.applied_model, s.queued) == (1, 0)
    assert f"apple: skipped — {msg}" in capsys.readouterr().out


def test_apple_malformed_shim_output_refuses_batch_loudly(
        vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
    before = f.read_text()

    def runner(args: list[str], stdin: str) -> tuple[int, str, str]:
        if args == ["--probe"]:
            return 0, "available\n", ""
        return 0, "probably lunch?", ""

    s = run_classification(write=True, backends=_rungs(AppleBackend(runner=runner)))
    assert (s.applied_model, s.queued) == (0, 1)
    assert f.read_text() == before  # a refused batch never touches the ledger
    out = capsys.readouterr().out
    assert "! apple: batch 1 refused — reply was not valid JSON" in out
    assert "apple reply refused (malformed)" in out


def test_ollama_backend_end_to_end_via_fake_transport(vault: Path) -> None:
    f = seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
    calls: list[tuple[str, bytes | None]] = []

    def transport(url: str, body: bytes | None, timeout: float) -> str:
        calls.append((url, body))
        if body is None:  # the /api/tags probe
            return '{"models": []}'
        return json.dumps({"message": {
            "role": "assistant",
            "content": reply(row(0, "Expenses:Food:Dining", "0.91"))}})

    backend = OllamaBackend("http://127.0.0.1:11434", "llama3.2:3b", transport=transport)
    s = run_classification(write=True, backends=_rungs(backend))
    assert (s.applied_model, s.queued) == (1, 0)
    assert '  classifier: "ollama:0.91"\n' in f.read_text()
    assert calls[0] == ("http://127.0.0.1:11434/api/tags", None)
    chat_url, chat_body = calls[1]
    assert chat_url == "http://127.0.0.1:11434/api/chat"
    assert chat_body is not None
    sent = json.loads(chat_body)
    assert sent["model"] == "llama3.2:3b"
    assert sent["format"] == RESPONSE_SCHEMA  # structured outputs armed
    assert sent["options"] == {"temperature": 0}


def test_ollama_connection_refused_skips_with_note(
        vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))

    def transport(url: str, body: bytes | None, timeout: float) -> str:
        raise OSError(61, "Connection refused")

    ollama = OllamaBackend("http://127.0.0.1:11434", "llama3.2:3b", transport=transport)
    haiku = FakeBackend("haiku", {"MYSTERY SANDWICHES #4": J("Expenses:Food:Dining", "0.90")})
    s = run_classification(write=True, backends=_rungs(ollama, haiku))
    assert (s.applied_model, s.queued) == (1, 0)
    assert ("ollama: skipped — nothing listening at http://127.0.0.1:11434"
            in capsys.readouterr().out)


def test_haiku_backend_direct_probe_and_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    why = HaikuBackend("claude-haiku-4-5", None).probe()
    assert why is not None and "ANTHROPIC_API_KEY" in why  # keyless rung says how to arm
    fake = FakeModel(reply(row(0, "Expenses:Food:Dining", "0.93")))
    armed = HaikuBackend("claude-haiku-4-5", None, call=fake)
    assert armed.probe() is None
    got = armed.classify_batch([review_txn("MYSTERY SANDWICHES #4")],
                               BatchContext(("Expenses:Food:Dining",), (), ()))
    assert got == [Judgment("Expenses:Food:Dining", Decimal("0.93"), "looks right")]
    assert armed.usage == ModelUsage(1_000, 200)



def test_apple_batches_bite_small_with_a_trimmed_briefing(vault: Path) -> None:
    """The on-device window is 4096 tokens total, so the apple rung takes
    APPLE_BATCH_SIZE txns per call and shortens the example lists."""
    (vault / "rules.toml").write_text(RULES_TOML + "".join(
        f'\n[[payee_rules]]\nmatch = "SHOP{i}"\naccount = "Expenses:Food:Dining"\n'
        for i in range(20)))
    reset_rules_cache()
    seed(vault, *[txn(f"MYSTERY SANDWICHES #{i}", "-18.00", when="2026-07-02")
                  for i in range(APPLE_BATCH_SIZE + 1)])
    sizes: list[int] = []
    example_counts: list[int] = []

    def runner(args: list[str], stdin: str) -> tuple[int, str, str]:
        if args == ["--probe"]:
            return 0, "available\n", ""
        req = json.loads(stdin)
        sizes.append(len(req["txns"]))
        example_counts.append(len(req["examples"]))
        results = [{"index": t["id"], "category": "Expenses:Food:Dining",
                    "confidence": 0.9, "reason": "ok"} for t in req["txns"]]
        return 0, json.dumps({"results": results}), ""

    s = run_classification(write=True, backends=_rungs(AppleBackend(runner=runner)))
    assert s.applied_model == APPLE_BATCH_SIZE + 1
    assert sizes == [APPLE_BATCH_SIZE, 1]
    assert all(n <= APPLE_MAX_RULE_EXAMPLES + APPLE_MAX_HISTORY_EXAMPLES
               for n in example_counts)


def test_apple_context_overflow_refuses_batch_not_rung(vault: Path) -> None:
    """A too-big request must not kill the rung: the overflowing batch moves
    on down the ladder and the NEXT batch still runs (the pre-fix behavior
    marked the whole rung dead on the first overflow)."""
    seed(vault, *[txn(f"MYSTERY SANDWICHES #{i}", "-18.00", when="2026-07-02")
                  for i in range(APPLE_BATCH_SIZE + 1)])
    calls: list[int] = []

    def runner(args: list[str], stdin: str) -> tuple[int, str, str]:
        if args == ["--probe"]:
            return 0, "available\n", ""
        req = json.loads(stdin)
        calls.append(len(req["txns"]))
        if len(calls) == 1:
            return 1, "", ("generation failed: exceededContextWindowSize"
                           "(maximum allowed is 4,096)")
        results = [{"index": t["id"], "category": "Expenses:Food:Dining",
                    "confidence": 0.9, "reason": "ok"} for t in req["txns"]]
        return 0, json.dumps({"results": results}), ""

    s = run_classification(write=True, backends=_rungs(AppleBackend(runner=runner)))
    assert calls == [APPLE_BATCH_SIZE, 1]  # the second batch still ran
    assert (s.applied_model, s.queued) == (1, APPLE_BATCH_SIZE)


def test_apple_language_guardrail_refuses_batch_and_payees_are_sanitized(
        vault: Path) -> None:
    """Digit-soup payees ("VENMO PAYMENT 1034278654754 WEB ID: 3264681992")
    trip the on-device language guardrail: the id runs are stripped from what
    the model SEES, and a guardrail hit refuses only that batch."""
    seed(vault, txn("NETFLIX.COM", "-15.49"))
    seen: list[str] = []

    def runner(args: list[str], stdin: str) -> tuple[int, str, str]:
        if args == ["--probe"]:
            return 0, "available\n", ""
        req = json.loads(stdin)
        seen.extend(t["payee"] for t in req["txns"])
        return 1, "", ("generation failed: unsupportedLanguageOrLocale("
                       "debugDescription: \"Unsupported language.\")")

    s = run_classification(write=True, backends=_rungs(AppleBackend(runner=runner)))
    assert (s.applied_model, s.queued) == (0, 1)  # refused, not rung-dead
    assert seen == ["NETFLIX.COM"]
    assert apple_payee("VENMO PAYMENT 1034278654754 WEB ID: 3264681992") == \
        "VENMO PAYMENT WEB ID:"
    assert apple_payee("WHOLE FOODS MKT #123") == "WHOLE FOODS MKT #123"


# --------------------------------------------------------------- p2p guard
def test_guard_reason_layers_and_curation() -> None:
    # layer (a) — the rails, including the real miss that taught the law
    assert guard_reason("Zelle to Alicia") == "P2P rail"
    assert guard_reason("VENMO PAYMENT 1034396132515 WEB ID: 3264681992") == "P2P rail"
    assert guard_reason("Zelle payment to DARA L PHILLIPS JPM99bep4jma") == "P2P rail"
    assert guard_reason("PAYPAL INST XFER~ Tran: ACHDW") == "P2P rail"
    assert guard_reason("APPLE CASH SENT MONEY") == "P2P rail"
    assert guard_reason("CASHAPP*JOHN") == "P2P rail"
    assert guard_reason("Payment to WISE") == "P2P rail"
    # PayPal CHECKOUT at a merchant is a merchant; WISEACRE is a brewery
    assert guard_reason("PAYPAL *NETFLIX") is None
    assert guard_reason("PAYPAL*SPOTIFY") is None
    assert guard_reason("WISEACRE BREWING CO") is None
    # layer (b) — person-shaped payees; "SQ *NAME" counts despite the star
    assert guard_reason("Alicia Weiss") == "person-shaped payee"
    assert guard_reason("DARA L PHILLIPS") == "person-shaped payee"
    assert guard_reason("SQ *JANE DOE") == "person-shaped payee"
    assert guard_reason("O'Brien Smith-Jones") == "person-shaped payee"
    # business markers disarm the shape test
    assert guard_reason("NETFLIX.COM") is None
    assert guard_reason("WHOLE FOODS MKT #123") is None
    assert guard_reason("UNKNOWABLE LLC") is None
    assert guard_reason("AT&T") is None
    assert guard_reason("TARGET 00023") is None
    assert guard_reason("Brians Ice Cream Inc.") is None
    assert guard_reason("") is None


def test_guard_default_is_on() -> None:
    assert load_config().p2p_guard is True


def test_guard_holds_rail_payee_at_high_confidence(vault: Path) -> None:
    f = seed(vault, txn("VENMO PAYMENT 1034396132515 WEB", "-120.00"))
    before = f.read_text()
    rung = FakeBackend("apple", {
        "VENMO PAYMENT 1034396132515 WEB": J("Expenses:Food:Dining", "0.95")})
    s = run_classification(write=True, backends=_rungs(rung))
    assert (s.applied_model, s.queued) == (0, 1)
    assert f.read_text() == before  # a write run, and still nothing moved


def test_guard_holds_person_shaped_payee_and_displays_suggestion(
        vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(vault, txn("Alicia Weiss", "-90.00"))
    rung = FakeBackend("apple", {
        "Alicia Weiss": J("Expenses:Food:Dining", "0.95", "probably a caterer")})
    s = run_classification(write=True, backends=_rungs(rung))
    assert (s.applied_model, s.queued) == (0, 1)
    out = capsys.readouterr().out
    assert "held 1 (p2p guard)" in out
    assert "person-shaped payee — a person needs your word" in out
    assert "suggest Expenses:Food:Dining (apple 0.95) — probably a caterer" in out


def test_the_alicia_zelle_string_never_auto_applies(vault: Path) -> None:
    """The 2026-08-06 miss, pinned as a fixture: the model booked "Zelle to
    Alicia" as Childcare:Nanny at 0.90 — she is the household's
    acupuncturist. A person-payee verdict is a guess at ANY confidence."""
    f = seed(vault,
             "2000-01-02 open Expenses:Childcare:Nanny  USD",
             txn("Zelle to Alicia", "-150.00"))
    rung = FakeBackend("apple", {
        "Zelle to Alicia": J("Expenses:Childcare:Nanny", "0.90",
                             "recurring payment, likely childcare")})
    s = run_classification(write=True, backends=_rungs(rung))
    assert (s.applied_model, s.queued) == (0, 1)
    text = f.read_text()
    assert text.count("Expenses:Uncategorized\n") == 1  # the txn leg stayed put
    assert "classifier:" not in text


def test_guard_off_lets_the_model_apply(vault: Path) -> None:
    (vault / "rules.toml").write_text(
        RULES_TOML + "\n[classification]\np2p_guard = false\n")
    reset_rules_cache()
    f = seed(vault, txn("Alicia Weiss", "-90.00"))
    rung = FakeBackend("apple", {"Alicia Weiss": J("Expenses:Food:Dining", "0.95")})
    s = run_classification(write=True, backends=_rungs(rung))
    assert (s.applied_model, s.queued) == (1, 0)
    assert '  classifier: "apple:0.95"\n' in f.read_text()


def test_business_payee_applies_straight_through_the_guard(vault: Path) -> None:
    f = seed(vault, txn("NETFLIX.COM", "-15.49"))
    rung = FakeBackend("apple", {"NETFLIX.COM": J("Expenses:Food:Dining", "0.95")})
    s = run_classification(write=True, backends=_rungs(rung))
    assert (s.applied_model, s.queued) == (1, 0)
    assert '  classifier: "apple:0.95"\n' in f.read_text()


def test_guarded_below_floor_still_escalates_for_a_better_suggestion(
        vault: Path) -> None:
    seed(vault, txn("Alicia Weiss", "-90.00"))
    a = FakeBackend("apple", {"Alicia Weiss": J("Expenses:Food:Dining", "0.30")})
    b = FakeBackend("ollama", {"Alicia Weiss": J("Expenses:Food:Groceries", "0.95")})
    s = run_classification(write=True, backends=_rungs(a, b))
    assert (s.applied_model, s.queued) == (0, 1)
    assert b.batches == [["Alicia Weiss"]]  # unsure rung 1 escalated; rung 2 held


# ------------------------------------------------------------ ladder config
def test_config_defaults_keep_todays_behavior(vault: Path) -> None:
    cfg = load_config()
    assert cfg.backends == ("haiku",)  # no config -> exactly the old single rung
    assert cfg.floor("apple") == cfg.floor("haiku") == Decimal("0.8")
    assert (cfg.ollama_url, cfg.ollama_model) == ("http://127.0.0.1:11434", "llama3.2:3b")


def test_config_ladder_order_thresholds_and_unknowns(
        vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (vault / "rules.toml").write_text(RULES_TOML + '''
[classification]
model_backends = ["ollama", "apple", "apple", "grok"]
model_min_confidence = 0.7
apple_min_confidence = 0.9
ollama_url = "http://127.0.0.1:11435/"
ollama_model = "qwen3:4b"
''')
    reset_rules_cache()
    cfg = load_config()
    assert cfg.backends == ("ollama", "apple")  # order kept; dupes and unknowns dropped
    assert '"grok" ignored' in capsys.readouterr().err
    assert cfg.floor("apple") == Decimal("0.9")  # per-backend floor wins
    assert cfg.floor("ollama") == Decimal("0.7")  # others fall to the default
    assert cfg.ollama_url == "http://127.0.0.1:11435"  # trailing slash dropped
    assert cfg.ollama_model == "qwen3:4b"


def test_empty_configured_ladder_queues_residue(vault: Path) -> None:
    (vault / "rules.toml").write_text(
        RULES_TOML + "\n[classification]\nmodel_backends = []\n")
    seed(vault, txn("MYSTERY SANDWICHES #4", "-18.00"))
    s = run_classification(write=True)
    assert (s.applied, s.queued) == (0, 1)


def test_backend_flag_parses_and_rejects_unknown() -> None:
    names, rest = _backend_flag(["--backend", "Apple,haiku", "--write"], "usage")
    assert (names, rest) == (["apple", "haiku"], ["--write"])
    assert _backend_flag(["--write"], "usage") == (None, ["--write"])
    with pytest.raises(SystemExit) as e:
        main(["--backend", "grok"])
    assert "--backend needs" in str(e.value)


# ----------------------------------------------------- bean-check gated path
@needs_venv
def test_write_survives_real_bean_check(vault: Path) -> None:
    f = seed(vault, txn("CORNER CAFE", "-12.50", "FOOD_AND_DRINK_COFFEE (high)"))
    s = run_classification(write=True, skip_model=True)
    assert s.applied_plaid == 1 and "Expenses:Food:Dining" in f.read_text()
