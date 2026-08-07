"""Three-tier transaction classifier — the review queue's autopilot.

Postings booked to the review accounts (Expenses:Uncategorized and
Income:US:Other) are re-decided by three tiers, cheapest and most trusted
first — and the first tier to speak wins:

  tier 1  rules.toml [[payee_rules]] — human-taught, always win
  tier 2  Plaid's own category signal (the `plaid-category:` metadata banked
          at ingest), applied only at high confidence and only when the
          mapped account exists in this vault's chart. The shipped table
          lives in sara/plaid_map.py; rules.toml [plaid_category_map]
          overrides/extends it.
  tier 3  a ladder of model backends over the weak-signal residue. The
          first backend takes the whole batch and only what it can't
          decide escalates to the next: `apple` (on-device Apple
          Intelligence, free, merchant strings never leave the Mac),
          `ollama` (a local daemon, free), `haiku` (the Claude API, needs
          a key). Every backend is JSON-schema-constrained and applied
          only at >= its confidence floor; the final residue stays in
          review with the best below-floor guesses printed as
          suggestions. A model picks from the vault's real chart — it can
          never invent a category.

Every rewrite lands through the same machinery as recategorize.py (atomic
tmp+rename per file, bean-check, full rollback on failure), and every
machine-moved posting gains `classifier:` metadata naming its tier and
signal ("plaid:FOOD_AND_DRINK_COFFEE", "apple:0.92", "haiku:0.91") so the
move is auditable, re-doable, and says which brain judged it. Machine classifications never create payee_rules —
rules stay human-taught via the app/review loop; instead the report calls
out recurring residue worth teaching a rule for.

Usage:
  python -m sara.classify                dry run (default): report only
  python -m sara.classify --write        apply (atomic + bean-check)
  python -m sara.classify --skip-model   tiers 1-2 only — no model calls
  python -m sara.classify --model-limit N  send at most N txns to the ladder
  python -m sara.classify --backend apple,haiku  override the ladder once
  (or: tools/run classify.py — same flags. A dry run still runs the ladder
  for suggestions when tier 3 is armed; --skip-model keeps the run silent.)

CONFIG — $VAULT/rules.toml (all optional; defaults shown):

  [classification]
  tier2 = true                    # Plaid-signal tier
  tier3 = true                    # model tier (the backend ladder)
  plaid_min_confidence = "high"   # or "very_high"
  model_backends = ["haiku"]      # the tier-3 ladder, in escalation order:
                                  #   "apple"  on-device (macOS 26+, $0)
                                  #   "ollama" local daemon ($0)
                                  #   "haiku"  Claude API (needs the key below)
  model_min_confidence = 0.8      # apply threshold, 0-1, for every backend...
  apple_min_confidence = 0.8      # ...unless a per-backend floor overrides
  model = "claude-haiku-4-5"      # the haiku rung's Messages API model id
  ollama_url = "http://127.0.0.1:11434"
  ollama_model = "llama3.2:3b"

  [plaid_category_map]
  "FOOD_AND_DRINK_COFFEE" = "Expenses:Food:Coffee"

CREDENTIALS: only the haiku rung needs any — $VAULT/.secrets/anthropic.env
holds ANTHROPIC_API_KEY=sk-... (0600; .secrets/ is gitignored). A rung that
can't run (no key, Apple Intelligence off, no ollama daemon) is skipped
with a one-line note and the rest of the ladder still works; the report
says how to enable what's missing. The apple rung compiles its tiny Swift
shim (shim/sara-classify-shim/) on first use — one `swift build`, cached.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from sara.cli.shared import err, reject_unknown_flags
from sara.ledger.queries import opened_accounts
from sara.ledger.writer import rewrite_ledger_files
from sara.plaid_map import DEFAULT_PLAID_MAP
from sara.rules import EXPENSE_DEFAULT, INCOME_DEFAULT, TRANSFER_ACCOUNT, match_rule, payee_rules
from sara.typed import as_dict, as_list
from sara.vault import SECRETS_DIR, VAULT, load_env_file, require_vault, rules

REVIEW_ACCOUNTS = frozenset({EXPENSE_DEFAULT, INCOME_DEFAULT})
ANTHROPIC_ENV_FILE = SECRETS_DIR / "anthropic.env"
FLAGS = frozenset({"--write", "--skip-model", "--verbose"})

BACKEND_NAMES = ("apple", "ollama", "haiku")  # everything a ladder may name
DEFAULT_BACKENDS = ("haiku",)  # the pre-ladder behavior, so nothing regresses
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
MODEL_BATCH_SIZE = 40  # txns per backend call — small enough to stay sharp
MODEL_MAX_TOKENS = 8192
MAX_HISTORY_EXAMPLES = 40  # recent payee->category pairs shown to the model
MAX_RULE_EXAMPLES = 25
RULE_SUGGESTION_MIN = 3  # a payee seen this often in the residue earns a rule hint
NO_SIGNAL = "no signal (model tier not run)"
# claude-haiku-4-5 list price (USD per million tokens, 2026-08) — estimate only.
PRICE_IN_PER_MTOK = Decimal("1.00")
PRICE_OUT_PER_MTOK = Decimal("5.00")

# The on-device shim (see shim/sara-classify-shim/): built lazily, cached in
# its .build/ tree, spoken to over stdin/stdout JSON. One subprocess per batch.
SHIM_DIR = Path(__file__).resolve().parent.parent / "shim" / "sara-classify-shim"
SHIM_BINARY = SHIM_DIR / ".build" / "release" / "sara-classify-shim"
SHIM_TIMEOUT = 600.0  # seconds per batch — on-device generation is not instant
OLLAMA_PROBE_TIMEOUT = 2.0
OLLAMA_TIMEOUT = 600.0  # local models chew on 40-txn batches

ZERO = Decimal(0)
ONE = Decimal(1)
CONF_RANK = {"low": 0, "medium": 1, "high": 2, "very_high": 3}

TXN_HEADER = re.compile(r'^(\d{4}-\d{2}-\d{2}) [*!] "([^"]*)"')
POSTING = re.compile(r"^(\s+)([A-Z][\w:-]+)(\s+(-?[\d.,]+) USD)?\s*$")
META = re.compile(r'^\s+([a-z][\w-]*):\s+"([^"]*)"\s*$')
PLAID_CATEGORY_META = re.compile(r"^([A-Z0-9_]+)(?:\s+\(([a-z_]+)\))?$")

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "category": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "category", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ parsing
@dataclass
class ReviewTxn:
    """One transaction whose counter-posting sits in the review queue."""

    file: Path
    start: int  # header line index within the file
    when: date
    payee: str
    ofx_type: str
    amount: Decimal  # signed, from the primary account's point of view
    primary: str
    review_account: str  # which queue account it sits in
    target_line: int  # absolute index of the bare review posting line
    meta_insert: int  # absolute index where new metadata belongs
    classifier_line: int | None  # existing classifier: line, if any
    plaid_detailed: str
    plaid_confidence: str
    rewritable: bool  # bare counter-posting with a readable residual


class Example(NamedTuple):
    when: date
    payee: str
    account: str


class Scan(NamedTuple):
    lines: dict[Path, list[str]]
    txns: list[ReviewTxn]
    history: list[Example]  # categorized payee->account pairs, for the model


def _dec(text: str) -> Decimal | None:
    try:
        d = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _scan_block(f: Path, lines: list[str], start: int, end: int,
                out: Scan) -> None:
    """Classify one buffered transaction block into a ReviewTxn or a
    history example (mirrors recategorize.py's residual math: the bare
    review leg interpolates to -(sum of explicit legs), so the signed
    amount from the primary account's point of view is that sum itself)."""
    h = TXN_HEADER.match(lines[start])
    if not h:
        return
    try:
        when = datetime.strptime(h.group(1), "%Y-%m-%d").date()
    except ValueError:
        return
    payee = h.group(2)
    meta: dict[str, str] = {}
    meta_insert = start + 1
    classifier_line: int | None = None
    explicit_sum: Decimal = ZERO
    parse_ok = True
    primary = ""
    target_line: int | None = None
    review_account = ""
    example_account = ""
    for i in range(start + 1, end):
        mm = META.match(lines[i])
        if mm and target_line is None and primary == "":
            meta[mm.group(1)] = mm.group(2)
            if mm.group(1) == "classifier":
                classifier_line = i
            meta_insert = i + 1
            continue
        p = POSTING.match(lines[i])
        if not p:
            continue
        account = p.group(2)
        if p.group(4):
            amt = _dec(p.group(4))
            if amt is None:
                parse_ok = False
            else:
                explicit_sum += amt
            if not primary:
                primary = account
            if account in REVIEW_ACCOUNTS and target_line is None:
                target_line = i  # explicit-amount review leg: not rewritable
                review_account = account
        elif account in REVIEW_ACCOUNTS and target_line is None:
            target_line = i
            review_account = account
        elif not example_account and account not in REVIEW_ACCOUNTS and \
                account.split(":", 1)[0] in ("Expenses", "Income"):
            example_account = account
    if target_line is None:
        if payee and example_account:
            out.history.append(Example(when, payee, example_account))
        return
    detailed, conf = "", ""
    pc = PLAID_CATEGORY_META.match(meta.get("plaid-category", ""))
    if pc:
        detailed, conf = pc.group(1), pc.group(2) or ""
    bare = POSTING.match(lines[target_line])
    rewritable = bool(bare and not bare.group(4)) and parse_ok
    out.txns.append(ReviewTxn(
        file=f, start=start, when=when, payee=payee,
        ofx_type=(meta.get("ofx-type") or meta.get("plaid-type")
                  or meta.get("chase-type") or meta.get("type") or ""),
        amount=explicit_sum, primary=primary, review_account=review_account,
        target_line=target_line, meta_insert=meta_insert,
        classifier_line=classifier_line,
        plaid_detailed=detailed, plaid_confidence=conf, rewritable=rewritable))


def scan_ledger() -> Scan:
    """One pass over ledger/*.beancount -> review candidates + history.

    Parsed from the files directly (like the writer's indexes) so classify
    works before the vault venv exists and never needs bean-query.
    """
    out = Scan({}, [], [])
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            lines = f.read_text().splitlines(keepends=True)
        except OSError:
            continue
        out.lines[f] = lines
        block_start: int | None = None
        for i, line in enumerate(lines):
            indented = line[:1] in (" ", "\t") and bool(line.strip())
            if block_start is not None and indented:
                continue
            if block_start is not None:
                _scan_block(f, lines, block_start, i, out)
                block_start = None
            if TXN_HEADER.match(line):
                block_start = i
        if block_start is not None:
            _scan_block(f, lines, block_start, len(lines), out)
    out.history.sort(key=lambda e: e.when)
    return out


# ------------------------------------------------------------------- config
class Config(NamedTuple):
    tier2: bool
    tier3: bool
    plaid_min: str  # "high" | "very_high"
    model_min: Decimal  # 0-1, the apply threshold every backend defaults to
    model: str  # the haiku rung's Messages API model id
    backends: tuple[str, ...]  # the tier-3 ladder, escalation order
    floors: dict[str, Decimal]  # per-backend threshold overrides
    ollama_url: str
    ollama_model: str

    def floor(self, backend: str) -> Decimal:
        return self.floors.get(backend, self.model_min)


def _conf_floor(value: object) -> Decimal | None:
    """A 0-1 confidence from config, or None when absent/unusable."""
    if value is None:
        return None
    d = _dec(str(value))
    return d if d is not None and ZERO <= d <= ONE else None


def _backend_ladder(raw: object) -> tuple[str, ...]:
    """model_backends validated: known names only, order kept, dupes dropped.
    Absent means DEFAULT_BACKENDS; an explicit empty list means no ladder."""
    if raw is None:
        return DEFAULT_BACKENDS
    names: list[str] = []
    for v in as_list(raw):
        s = str(v).strip().lower()
        if s in BACKEND_NAMES and s not in names:
            names.append(s)
        elif s and s not in names:
            print(f'; warning: rules.toml model_backends entry "{s}" ignored '
                  f"(known: {', '.join(BACKEND_NAMES)})", file=sys.stderr)
    return tuple(names)


def _config() -> Config:
    c = as_dict(rules().get("classification"))
    plaid_min = str(c.get("plaid_min_confidence") or "high").lower()
    if plaid_min not in CONF_RANK:
        plaid_min = "high"
    model_min = _conf_floor(c.get("model_min_confidence"))
    if model_min is None:
        model_min = Decimal("0.8")
    floors = {name: floor for name in BACKEND_NAMES
              if (floor := _conf_floor(c.get(f"{name}_min_confidence"))) is not None}
    return Config(
        tier2=bool(c.get("tier2", True)),
        tier3=bool(c.get("tier3", True)),
        plaid_min=plaid_min,
        model_min=model_min,
        model=str(c.get("model") or DEFAULT_MODEL),
        backends=_backend_ladder(c.get("model_backends")),
        floors=floors,
        ollama_url=str(c.get("ollama_url") or DEFAULT_OLLAMA_URL).rstrip("/"),
        ollama_model=str(c.get("ollama_model") or DEFAULT_OLLAMA_MODEL),
    )


def plaid_map() -> dict[str, str]:
    """The shipped table with the vault's [plaid_category_map] laid over it."""
    merged = dict(DEFAULT_PLAID_MAP)
    for k, v in as_dict(rules().get("plaid_category_map")).items():
        if isinstance(v, str):
            merged[k.strip().upper()] = v
    return merged


def model_api_key() -> str | None:
    """ANTHROPIC_API_KEY from $VAULT/.secrets/anthropic.env (the convention),
    with the process environment as an override for one-off runs."""
    key = os.environ.get("ANTHROPIC_API_KEY") or \
        load_env_file(ANTHROPIC_ENV_FILE).get("ANTHROPIC_API_KEY")
    return key or None


# ---------------------------------------------------------------- decisions
@dataclass
class Decision:
    txn: ReviewTxn
    account: str
    tier: str  # "rule" | "plaid" | "model"
    label: str  # the classifier: metadata value
    note: str = ""


@dataclass
class Queued:
    txn: ReviewTxn
    reason: str
    suggestion: str = ""  # "Expenses:X (0.62)" from a below-threshold model guess


def _confidence_ok(conf: str, floor: str) -> bool:
    return CONF_RANK.get(conf, -1) >= CONF_RANK[floor]


def decide_tier12(txns: list[ReviewTxn], cfg: Config, chart: set[str],
                  mapping: dict[str, str]) -> tuple[list[Decision], list[ReviewTxn], list[Queued]]:
    """Tiers 1-2 -> (decisions, residue for tier 3, hard-queued)."""
    decisions: list[Decision] = []
    residue: list[ReviewTxn] = []
    queued: list[Queued] = []
    for t in txns:
        if not t.rewritable:
            queued.append(Queued(t, "counter-posting not rewritable (explicit amount "
                                    "or unreadable legs) — fix by hand"))
            continue
        ruled = match_rule(t.payee, t.ofx_type, t.amount)
        if ruled:
            if ruled == t.review_account:
                queued.append(Queued(t, "pinned here by a payee rule"))
            elif ruled not in chart:
                queued.append(Queued(t, f"rule target {ruled} is not an open account"))
            else:
                decisions.append(Decision(t, ruled, "rule", "rule"))
            continue
        if cfg.tier2 and t.plaid_detailed:
            target = mapping.get(t.plaid_detailed, "")
            if target and _confidence_ok(t.plaid_confidence, cfg.plaid_min):
                if target == t.review_account:
                    residue.append(t)  # mapping is a no-op here; let tier 3 try
                elif target not in chart:
                    queued.append(Queued(
                        t, f"plaid {t.plaid_detailed} -> {target} not in this chart "
                           f"(add the account or a [plaid_category_map] override)"))
                else:
                    decisions.append(Decision(
                        t, target, "plaid", f"plaid:{t.plaid_detailed}",
                        note=f"{t.plaid_detailed} ({t.plaid_confidence})"))
                continue
        residue.append(t)
    return decisions, residue, queued


# -------------------------------------------------------------- model tier
class ModelUsage(NamedTuple):
    input_tokens: int
    output_tokens: int


ModelCall = Callable[[str, str, str, int], tuple[str, ModelUsage]]
"""(model, system, user_json, max_tokens) -> (response text, usage).

The one seam to the Messages API: tests inject a fake; the CLI builds the
real one below only when a key exists, so no network and no SDK import ever
happen outside an armed tier 3.
"""


def anthropic_model_call(api_key: str) -> ModelCall:
    """The real Messages API caller (anthropic SDK, JSON-schema-constrained
    via output_config so the reply parses or the batch is refused)."""
    import anthropic  # lazy: tier 3 only, so tiers 1-2 run on any older venv

    client = anthropic.Anthropic(api_key=api_key)

    def call(model: str, system: str, user: str, max_tokens: int) -> tuple[str, ModelUsage]:
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            # output_config rides extra_body so any SDK version >= 0.40 can
            # send it; the API enforces RESPONSE_SCHEMA either way.
            extra_body={"output_config": {
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}}},
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, ModelUsage(resp.usage.input_tokens, resp.usage.output_tokens)

    return call


def model_categories(chart: set[str]) -> list[str]:
    """What the model may pick from: this vault's real spend/income accounts
    plus the transfers parking account — never the review buckets, so 'leave
    it queued' is expressed as low confidence, not a category."""
    cats = sorted(a for a in chart
                  if a.split(":", 1)[0] in ("Expenses", "Income")
                  and a not in REVIEW_ACCOUNTS)
    if TRANSFER_ACCOUNT in chart:
        cats.append(TRANSFER_ACCOUNT)
    return cats


def _rule_examples() -> list[str]:
    out: list[str] = []
    for r in payee_rules():
        account = r.get("account")
        pattern = r.get("match")
        if isinstance(account, str) and isinstance(pattern, str):
            out.append(f"{pattern} -> {account}")
    return out[:MAX_RULE_EXAMPLES]


def _history_examples(history: list[Example]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for e in reversed(history):  # newest first
        key = e.payee.strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(f"{e.payee} -> {e.account}")
        if len(out) >= MAX_HISTORY_EXAMPLES:
            break
    return out


def model_system_prompt(categories: Sequence[str], rule_lines: Sequence[str],
                        history_lines: Sequence[str]) -> str:
    parts = [
        "You are the bookkeeper for one household's plain-text ledger. "
        "Classify each bank/card transaction into exactly one account from "
        "the chart below. Amounts are signed from the listed account's point "
        "of view: negative = money out (an expense), positive = money in "
        "(income, a refund, or an incoming transfer). Movements between the "
        "household's own accounts (card autopay, brokerage funding) belong "
        "in Assets:US:Transfers when it is listed. Some transactions carry a "
        "`hint`: Plaid's low-confidence guess — weigh it, don't trust it. "
        "If no listed account clearly fits, still name your best candidate "
        "but give it confidence below 0.5; NEVER answer with an account that "
        "is not in the chart. confidence is your 0-1 certainty; reason is "
        "one short line.",
        "CHART (the only legal answers):\n" + "\n".join(categories),
    ]
    if rule_lines:
        parts.append("HOUSEHOLD RULES (payee regex -> account):\n" + "\n".join(rule_lines))
    if history_lines:
        parts.append("RECENTLY CATEGORIZED (payee -> account):\n" + "\n".join(history_lines))
    return "\n\n".join(parts)


def _txn_rows(batch: Sequence[ReviewTxn]) -> list[dict[str, object]]:
    return [{"id": i, "date": t.when.isoformat(), "payee": t.payee,
             "amount": f"{t.amount:.2f}", "account": t.primary,
             "hint": t.plaid_detailed} for i, t in enumerate(batch)]


def _batch_payload(batch: Sequence[ReviewTxn]) -> str:
    return json.dumps({"transactions": _txn_rows(batch)}, ensure_ascii=False)


def parse_model_reply(text: str, batch_size: int,
                      id_key: str = "id") -> dict[int, tuple[str, Decimal, str]] | str:
    """-> {id: (category, confidence, reason)}, or a refusal reason.

    Structural problems (bad JSON, wrong shapes, out-of-range or duplicate
    ids) refuse the WHOLE batch — a reply that breaks the schema has
    forfeited trust. Unknown categories are judged per-txn by the caller.
    (The apple shim's guided type calls the id "index" — id_key covers it.)
    """
    try:
        data = json.loads(text, parse_float=Decimal)
    except ValueError:
        return "reply was not valid JSON"
    results = as_list(as_dict(data).get("results"))
    out: dict[int, tuple[str, Decimal, str]] = {}
    for item in results:
        row = as_dict(item)
        raw_id, category = row.get(id_key), row.get("category")
        raw_conf, reason = row.get("confidence"), row.get("reason")
        if not isinstance(raw_id, int) or isinstance(raw_id, bool) \
                or not 0 <= raw_id < batch_size:
            return f"reply carried an unknown txn id ({raw_id!r})"
        if raw_id in out:
            return f"reply answered txn {raw_id} twice"
        if not isinstance(category, str) or not isinstance(reason, str):
            return "reply fields had the wrong types"
        if isinstance(raw_conf, Decimal):
            conf = raw_conf
        elif isinstance(raw_conf, int) and not isinstance(raw_conf, bool):
            conf = Decimal(raw_conf)
        else:
            return "reply confidence was not a number"
        if not ZERO <= conf <= ONE:
            return f"reply confidence {conf} outside 0-1"
        out[raw_id] = (category, conf, reason)
    return out


class Judgment(NamedTuple):
    """One backend's verdict on one transaction."""

    category: str
    confidence: Decimal
    reason: str


class BatchContext(NamedTuple):
    """Everything a backend needs beyond the txns themselves."""

    categories: tuple[str, ...]  # the only legal answers
    rule_examples: tuple[str, ...]  # "REGEX -> account"
    history_examples: tuple[str, ...]  # "payee -> account", newest first


class BatchRefused(Exception):
    """This batch's reply forfeited trust (malformed/mis-shaped) — the txns
    move on down the ladder, the backend itself stays in the run."""


class ModelBackend(Protocol):
    """One rung of the tier-3 ladder.

    `name` tags provenance ("apple:0.92") and the report; `detail` is report
    color (a model id, "on-device"); probe() says why the rung can't run
    right now (None = it can); classify_batch() judges one batch, one answer
    per txn in order (None = no answer for that txn), raising BatchRefused
    when a reply forfeits trust and anything else when the backend itself
    has died for the rest of the run.
    """

    @property
    def name(self) -> str: ...

    @property
    def detail(self) -> str: ...

    def probe(self) -> str | None: ...

    def classify_batch(self, txns: Sequence[ReviewTxn],
                       context: BatchContext) -> list[Judgment | None]: ...


def _judgments(parsed: dict[int, tuple[str, Decimal, str]],
               count: int) -> list[Judgment | None]:
    return [Judgment(*parsed[i]) if i in parsed else None for i in range(count)]


class HaikuBackend:
    """The API rung — the original tier-3 Messages path behind the protocol.
    The only rung that costs money and the only one that leaves the machine,
    so it belongs at the BOTTOM of a ladder, mopping up what the free local
    rungs weren't sure about."""

    name = "haiku"

    def __init__(self, model: str, api_key: str | None,
                 call: ModelCall | None = None) -> None:
        self._model = model
        self._key = api_key
        self._call = call
        self.usage = ModelUsage(0, 0)  # summed across batches, for the cost line

    @property
    def detail(self) -> str:
        return self._model

    def probe(self) -> str | None:
        if self._call or self._key:
            return None
        return f"no key — put ANTHROPIC_API_KEY=sk-... in {ANTHROPIC_ENV_FILE} to enable"

    def classify_batch(self, txns: Sequence[ReviewTxn],
                       context: BatchContext) -> list[Judgment | None]:
        if self._call is None:
            self._call = anthropic_model_call(self._key or "")
        system = model_system_prompt(context.categories, context.rule_examples,
                                     context.history_examples)
        text, usage = self._call(self._model, system, _batch_payload(txns), MODEL_MAX_TOKENS)
        self.usage = ModelUsage(self.usage.input_tokens + usage.input_tokens,
                                self.usage.output_tokens + usage.output_tokens)
        parsed = parse_model_reply(text, len(txns))
        if isinstance(parsed, str):
            raise BatchRefused(parsed)
        return _judgments(parsed, len(txns))


Transport = Callable[[str, bytes | None, float], str]
"""(url, POST body | None for GET, timeout) -> response body text.

The one seam to the local HTTP daemon: tests inject a fake; OSError from it
means nobody is listening.
"""


def _http_transport(url: str, body: bytes | None, timeout: float) -> str:
    req = urllib.request.Request(url, data=body,
                                 method="POST" if body is not None else "GET")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw: bytes = resp.read()
    return raw.decode("utf-8", "replace")


class OllamaBackend:
    """The local-daemon rung: any Ollama model over localhost HTTP with
    structured outputs (`format` = the same JSON schema the API rung uses).
    Free, cross-platform, and merchant strings stay on the machine as long
    as ollama_url points at it."""

    name = "ollama"

    def __init__(self, url: str, model: str,
                 transport: Transport | None = None) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._transport = transport or _http_transport

    @property
    def detail(self) -> str:
        return self._model

    def probe(self) -> str | None:
        try:
            self._transport(f"{self._url}/api/tags", None, OLLAMA_PROBE_TIMEOUT)
        except OSError:
            return (f"nothing listening at {self._url} — start it (`ollama serve`, "
                    f"then `ollama pull {self._model}` once) or drop \"ollama\" "
                    f"from model_backends")
        return None

    def classify_batch(self, txns: Sequence[ReviewTxn],
                       context: BatchContext) -> list[Judgment | None]:
        system = model_system_prompt(context.categories, context.rule_examples,
                                     context.history_examples)
        body = json.dumps({
            "model": self._model, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": _batch_payload(txns)}],
            "format": RESPONSE_SCHEMA,  # structured outputs: the reply parses or refuses
            "options": {"temperature": 0},
        }).encode()
        try:
            raw = self._transport(f"{self._url}/api/chat", body, OLLAMA_TIMEOUT)
        except urllib.error.HTTPError as e:  # daemon up, request rejected
            raise RuntimeError(f"HTTP {e.code} from ollama — is the model pulled? "
                               f"(`ollama pull {self._model}`)") from e
        try:
            data: object = json.loads(raw)
        except ValueError:
            raise BatchRefused("response was not JSON") from None
        content = as_dict(as_dict(data).get("message")).get("content")
        if not isinstance(content, str):
            raise BatchRefused("response had no message.content")
        parsed = parse_model_reply(content, len(txns))
        if isinstance(parsed, str):
            raise BatchRefused(parsed)
        return _judgments(parsed, len(txns))


ShimRunner = Callable[[list[str], str], tuple[int, str, str]]
"""(argv tail, stdin text) -> (returncode, stdout, stderr) for the shim.

The one seam to the on-device binary: tests inject a fake and never touch
Swift; the real one is built lazily by AppleBackend._prepare().
"""


def _shim_runner(binary: Path) -> ShimRunner:
    def run(args: list[str], stdin: str) -> tuple[int, str, str]:
        proc = subprocess.run([str(binary), *args], input=stdin,
                              capture_output=True, text=True, timeout=SHIM_TIMEOUT)
        return proc.returncode, proc.stdout, proc.stderr
    return run


def _last_line(*streams: str) -> str:
    for text in streams:
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        if lines:
            return lines[-1]
    return ""


class AppleBackend:
    """The on-device rung: Apple's Foundation Models via a tiny Swift shim
    (shim/sara-classify-shim/). Zero setup when Apple Intelligence is on —
    the shim is compiled once on first use and cached; merchant strings
    never leave the Mac. probe() reports the SPECIFIC unavailability reason
    the shim sees (device not eligible, Apple Intelligence off, model still
    downloading) so the fix is always named."""

    name = "apple"
    detail = "on-device"

    def __init__(self, runner: ShimRunner | None = None) -> None:
        self._runner = runner

    def probe(self) -> str | None:
        if self._runner is None:
            not_ready = self._prepare()
            if not_ready:
                return not_ready
        assert self._runner is not None
        rc, out, errtxt = self._runner(["--probe"], "")
        if rc != 0:
            return _last_line(errtxt, out) or "the shim's availability probe failed"
        return None

    def _prepare(self) -> str | None:
        """Platform gate + one-time `swift build` -> arm the real runner,
        or say in one line why this Mac can't run the rung."""
        if sys.platform != "darwin":
            return "needs macOS 26+ (Apple Intelligence)"
        macos = platform.mac_ver()[0]
        major = macos.split(".", 1)[0]
        if not major.isdigit() or int(major) < 26:
            return f"needs macOS 26+ (this Mac reports {macos or 'an unknown version'})"
        if not SHIM_BINARY.is_file():
            if shutil.which("swift") is None:
                return ("the on-device shim isn't built and there's no Swift "
                        "toolchain — `xcode-select --install` once, then re-run")
            err("building the on-device classify shim (one-time, ~30s)…")
            try:
                proc = subprocess.run(["swift", "build", "-c", "release"],
                                      cwd=SHIM_DIR, capture_output=True, text=True,
                                      timeout=SHIM_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as e:
                return f"shim build failed ({e})"
            if proc.returncode != 0 or not SHIM_BINARY.is_file():
                return (f"shim build failed "
                        f"({_last_line(proc.stderr, proc.stdout) or 'no output'}; "
                        f"run `swift build -c release` in {SHIM_DIR} to see why)")
        self._runner = _shim_runner(SHIM_BINARY)
        return None

    def classify_batch(self, txns: Sequence[ReviewTxn],
                       context: BatchContext) -> list[Judgment | None]:
        if self._runner is None:
            raise RuntimeError("probe() must arm the shim before classify_batch()")
        request = json.dumps({
            "categories": list(context.categories),
            "examples": [*context.rule_examples, *context.history_examples],
            "txns": _txn_rows(txns),
        }, ensure_ascii=False)
        rc, out, errtxt = self._runner([], request)
        if rc != 0:  # went unavailable mid-run, guardrails, context overflow…
            raise RuntimeError(_last_line(errtxt, out) or f"shim exited {rc}")
        parsed = parse_model_reply(out, len(txns), id_key="index")
        if isinstance(parsed, str):
            raise BatchRefused(parsed)
        return _judgments(parsed, len(txns))


def build_backends(cfg: Config, names: Sequence[str],
                   model_call: ModelCall | None = None) -> list[ModelBackend]:
    """The configured ladder, instantiated. The key file is read only when
    haiku is actually on the ladder — an all-local run never touches
    .secrets. model_call is the test seam for the haiku rung."""
    ladder: list[ModelBackend] = []
    for n in names:
        if n == "apple":
            ladder.append(AppleBackend())
        elif n == "ollama":
            ladder.append(OllamaBackend(cfg.ollama_url, cfg.ollama_model))
        else:
            ladder.append(HaikuBackend(cfg.model, model_api_key(), model_call))
    return ladder


# ---------------------------------------------------------------- the ladder
@dataclass
class RungStats:
    """What one rung did, for the report."""

    name: str
    detail: str
    floor: Decimal
    skipped: str = ""  # probe reason; the rung never ran
    judged: int = 0
    applied: int = 0
    unsure: int = 0  # below the floor -> escalated (or queued at the end)
    refused: int = 0  # malformed batches, unknown categories, no answer, died


def _plaid_hint(t: ReviewTxn) -> str:
    if not t.plaid_detailed:
        return ""
    return (f"plaid {t.plaid_detailed} "
            f"({t.plaid_confidence or 'no confidence'}) — below the bar")


@dataclass
class _Work:
    """One residue txn riding the ladder: the latest reason it's still
    unplaced plus every below-floor suggestion collected on the way down."""

    txn: ReviewTxn
    reason: str = NO_SIGNAL
    suggestions: list[tuple[Decimal, str, str]] = field(
        default_factory=list[tuple[Decimal, str, str]])

    def suggest(self, backend: str, answer: Judgment) -> None:
        self.suggestions.append((answer.confidence,
                                 f"{answer.category} ({backend} {answer.confidence:.2f})",
                                 answer.reason))

    def queued(self) -> Queued:
        """Best-confidence suggestion first (with its reason); the others
        trail so a two-brain disagreement is visible at a glance."""
        ranked = sorted(self.suggestions, key=lambda s: s[0], reverse=True)
        if not ranked:
            return Queued(self.txn, self.reason,
                          suggestion=_plaid_hint(self.txn) if self.reason == NO_SIGNAL else "")
        _, best_text, best_reason = ranked[0]
        parts = [f"{best_text} — {best_reason}" if best_reason else best_text]
        parts.extend(text for _, text, _ in ranked[1:])
        return Queued(self.txn, self.reason, suggestion=" / ".join(parts))


class LadderRun(NamedTuple):
    decisions: list[Decision]
    queued: list[Queued]
    sent: int
    stats: list[RungStats]  # one per rung, ladder order
    notes: list[str]  # batch-level refusals / failures, printed loudly


def run_ladder(residue: list[ReviewTxn], cfg: Config, chart: set[str],
               history: list[Example], ladder: Sequence[ModelBackend],
               limit: int | None) -> LadderRun:
    """Tier 3: each rung takes everything still undecided, batch by batch;
    what it can't decide (below its floor, refused, unanswered, or the rung
    died) escalates to the next rung; whatever survives the whole ladder
    queues with the best suggestions attached."""
    to_send = residue if limit is None else residue[:limit]
    overflow = [] if limit is None else residue[limit:]
    context = BatchContext(tuple(model_categories(chart)),
                           tuple(_rule_examples()),
                           tuple(_history_examples(history)))
    allowed = set(context.categories)
    work = [_Work(t) for t in to_send]
    decisions: list[Decision] = []
    notes: list[str] = []
    stats: list[RungStats] = []
    for backend in ladder:
        if not work:
            break
        floor = cfg.floor(backend.name)
        st = RungStats(backend.name, backend.detail, floor)
        stats.append(st)
        skip = backend.probe()
        if skip is not None:
            st.skipped = skip
            continue
        passed: list[_Work] = []
        dead = False
        for at in range(0, len(work), MODEL_BATCH_SIZE):
            batch = work[at:at + MODEL_BATCH_SIZE]
            if dead:
                for w in batch:
                    w.reason = f"{backend.name} call aborted earlier in this run"
                passed.extend(batch)
                continue
            try:
                answers = backend.classify_batch([w.txn for w in batch], context)
            except BatchRefused as e:
                notes.append(f"{backend.name}: batch {at // MODEL_BATCH_SIZE + 1} "
                             f"refused — {e}; its {len(batch)} txns move on")
                st.judged += len(batch)
                st.refused += len(batch)
                for w in batch:
                    w.reason = f"{backend.name} reply refused (malformed)"
                passed.extend(batch)
                continue
            except Exception as e:  # subprocess/network/API death: rung is done
                notes.append(f"{backend.name}: call failed — {e}")
                st.judged += len(batch)
                st.refused += len(batch)
                for w in batch:
                    w.reason = f"{backend.name} call failed"
                passed.extend(batch)
                dead = True
                continue
            if len(answers) != len(batch):
                notes.append(f"{backend.name}: {len(answers)} answers for "
                             f"{len(batch)} txns — batch refused")
                st.judged += len(batch)
                st.refused += len(batch)
                for w in batch:
                    w.reason = f"{backend.name} reply refused (wrong batch size)"
                passed.extend(batch)
                continue
            st.judged += len(batch)
            for w, answer in zip(batch, answers, strict=True):
                if answer is None:
                    st.refused += 1
                    w.reason = f"{backend.name} gave no answer"
                    passed.append(w)
                elif answer.category not in allowed:
                    st.refused += 1
                    w.reason = f"{backend.name} named unknown category {answer.category!r}"
                    passed.append(w)
                elif answer.confidence < floor:
                    st.unsure += 1
                    w.reason = f"{backend.name} unsure ({answer.confidence:.2f} < {floor})"
                    w.suggest(backend.name, answer)
                    passed.append(w)
                else:
                    st.applied += 1
                    decisions.append(Decision(
                        w.txn, answer.category, "model",
                        f"{backend.name}:{answer.confidence:.2f}", note=answer.reason))
        work = passed
    queued = [w.queued() for w in work]
    queued.extend(Queued(t, f"past --model-limit {limit}") for t in overflow)
    return LadderRun(decisions, queued, len(to_send), stats, notes)


# ------------------------------------------------------------------ rewrite
def apply_decisions(decisions: list[Decision],
                    lines: dict[Path, list[str]]) -> dict[Path, str]:
    """-> {file: new text}. Bottom-up per file so line indexes stay honest:
    the counter-posting account is swapped in place and a `classifier:` line
    is inserted with (or replaces one in) the transaction's metadata."""
    by_file: dict[Path, list[Decision]] = {}
    for d in decisions:
        by_file.setdefault(d.txn.file, []).append(d)
    out: dict[Path, str] = {}
    for f, ds in by_file.items():
        text = lines[f][:]
        for d in sorted(ds, key=lambda d: d.txn.start, reverse=True):
            t = d.txn
            pm = POSTING.match(text[t.target_line])
            indent = pm.group(1) if pm else "  "
            text[t.target_line] = f"{indent}{d.account}\n"
            meta_line = f'  classifier: "{d.label}"\n'
            if t.classifier_line is not None:
                text[t.classifier_line] = meta_line
            else:
                text.insert(t.meta_insert, meta_line)
        out[f] = "".join(text)
    return out


# ------------------------------------------------------------------- report
class Summary(NamedTuple):
    applied_rule: int
    applied_plaid: int
    applied_model: int
    queued: int
    wrote: bool

    @property
    def applied(self) -> int:
        return self.applied_rule + self.applied_plaid + self.applied_model


def _txn_line(t: ReviewTxn, tail: str) -> str:
    payee = (t.payee or "(no payee)")[:36]
    return f"    {t.when} {t.amount:>10.2f}  {payee:<36} {tail}"


def _print_decisions(name: str, ds: list[Decision], write: bool) -> None:
    verb = "applied" if write else "would apply"
    print(f"  {name}: {len(ds)} {verb}")
    for d in sorted(ds, key=lambda d: d.txn.when):
        note = f"  ({d.note})" if d.note else ""
        print(_txn_line(d.txn, f"-> {d.account}{note}"))


def _rule_suggestions(queue: list[Queued]) -> list[str]:
    counts: dict[str, tuple[int, str]] = {}
    for q in queue:
        key = q.txn.payee.strip().upper()
        if not key:
            continue
        n, hint = counts.get(key, (0, ""))
        counts[key] = (n + 1, hint or q.suggestion)
    lines: list[str] = []
    for key, (n, hint) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
        if n < RULE_SUGGESTION_MIN:
            break
        tail = f"  (model suggests {hint})" if hint else ""
        lines.append(f'    "{key}" seen {n}x{tail}')
    return lines


def run_classification(write: bool, skip_model: bool = False,
                       model_limit: int | None = None,
                       model_call: ModelCall | None = None,
                       backend_names: Sequence[str] | None = None,
                       backends: Sequence[ModelBackend] | None = None) -> Summary:
    """The whole run: scan -> tiers -> report -> (optionally) rewrite.

    backend_names overrides the configured ladder (the --backend flag);
    backends injects ready-made rungs and model_call injects the haiku
    rung's API seam — both test seams, neither touches the network.
    """
    require_vault()
    cfg = _config()
    chart = opened_accounts()
    scan = scan_ledger()
    mode = "WRITE" if write else "DRY RUN (re-run with --write to apply)"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"== sara classify — {stamp} — {mode} ==")
    by_queue: dict[str, int] = {}
    for t in scan.txns:
        by_queue[t.review_account] = by_queue.get(t.review_account, 0) + 1
    detail = ", ".join(f"{a} {n}" for a, n in sorted(by_queue.items()))
    print(f"{len(scan.txns)} postings in review ({detail or 'queue empty'})")
    if not scan.txns:
        return Summary(0, 0, 0, 0, False)

    mapping = plaid_map()
    decisions, residue, queued = decide_tier12(scan.txns, cfg, chart, mapping)
    rule_ds = [d for d in decisions if d.tier == "rule"]
    plaid_ds = [d for d in decisions if d.tier == "plaid"]
    _print_decisions("tier 1 — payee rules", rule_ds, write)
    if cfg.tier2:
        _print_decisions(f"tier 2 — plaid category ({cfg.plaid_min}+)", plaid_ds, write)
    else:
        print("  tier 2 — plaid category: off ([classification] tier2 = false)")

    model_ds: list[Decision] = []
    ladder: Sequence[ModelBackend] = []
    if residue and not skip_model and cfg.tier3:
        names = tuple(backend_names) if backend_names is not None else cfg.backends
        ladder = backends if backends is not None else build_backends(cfg, names, model_call)
    if ladder:
        run = run_ladder(residue, cfg, chart, scan.history, ladder, model_limit)
        model_ds = run.decisions
        queued.extend(run.queued)
        verb = "applied" if write else "would apply"
        print(f"  tier 3 — ladder: {' → '.join(b.name for b in ladder)}"
              f" — {run.sent} sent")
        for i, st in enumerate(run.stats):
            if st.skipped:
                print(f"    {st.name}: skipped — {st.skipped}")
                continue
            later = any(not s.skipped for s in run.stats[i + 1:])
            line = (f"    {st.name} ({st.detail}): judged {st.judged} — "
                    f"{verb} {st.applied} (>= {st.floor})")
            if st.unsure:
                line += f", {'escalated' if later else 'unsure'} {st.unsure}"
            if st.refused:
                line += f", refused {st.refused}"
            print(line)
        for d in sorted(model_ds, key=lambda d: d.txn.when):
            print(_txn_line(d.txn, f"-> {d.account}  [{d.label}] {d.note}"))
        for note in run.notes:
            print(f"    ! {note}")
        for b in ladder:  # the cost line, API rungs only — local rungs are $0
            if isinstance(b, HaikuBackend) and \
                    (b.usage.input_tokens or b.usage.output_tokens):
                cost = (b.usage.input_tokens * PRICE_IN_PER_MTOK
                        + b.usage.output_tokens * PRICE_OUT_PER_MTOK) / 1_000_000
                print(f"    cost ({b.name}): {b.usage.input_tokens:,} in + "
                      f"{b.usage.output_tokens:,} out tokens ~ ${cost:.4f}")
        decisions.extend(model_ds)
    else:
        why = ("--skip-model" if skip_model
               else "off ([classification] tier3 = false)" if not cfg.tier3
               else "nothing left for it" if not residue
               else "ladder empty ([classification] model_backends = [])")
        print(f"  tier 3 — model: {why}")
        queued.extend(Queued(t, NO_SIGNAL, suggestion=_plaid_hint(t))
                      for t in residue)

    print(f"  queued: {len(queued)} stay in review")
    for q in sorted(queued, key=lambda q: q.txn.when):
        tail = f"— {q.reason}" + (f"; suggest {q.suggestion}" if q.suggestion else "")
        print(_txn_line(q.txn, tail))
    suggestions = _rule_suggestions(queued)
    if suggestions:
        print("  recurring residue — consider teaching a [[payee_rules]] entry:")
        for line in suggestions:
            print(line)

    summary = Summary(len(rule_ds), len(plaid_ds), len(model_ds), len(queued), write)
    print(f"\n{summary.applied_rule} rule / {summary.applied_plaid} plaid / "
          f"{summary.applied_model} model {'applied' if write else 'would apply'} "
          f"· {summary.queued} queued")
    if not decisions:
        print("nothing to rewrite.")
        return summary._replace(wrote=False)
    if not write:
        return summary
    wrote = rewrite_ledger_files(apply_decisions(decisions, scan.lines))
    print(f"rewrote {summary.applied} postings in {', '.join(sorted(wrote))} "
          f"(bean-check passed)")
    return summary


# --------------------------------------------------------------------- main
def _backend_flag(argv: list[str], usage: str) -> tuple[list[str] | None, list[str]]:
    """Pull '--backend apple[,ollama,haiku]' out of argv -> (names, rest).
    Unknown names exit loudly — a typo'd ladder must not silently become
    the default one."""
    if "--backend" not in argv:
        return None, argv
    i = argv.index("--backend")
    value = argv[i + 1] if i + 1 < len(argv) else ""
    names: list[str] = []
    for s in value.split(","):
        s = s.strip().lower()
        if s and s not in names:
            names.append(s)
    bad = [s for s in names if s not in BACKEND_NAMES]
    if not names or bad:
        raise SystemExit(f"--backend needs a comma-separated subset of "
                         f"{', '.join(BACKEND_NAMES)} (got {value or 'nothing'})\n\n{usage}")
    return names, argv[:i] + argv[i + 2:]


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = __doc__ or ""
    model_limit: int | None = None
    if "--model-limit" in argv:
        i = argv.index("--model-limit")
        value = argv[i + 1] if i + 1 < len(argv) else ""
        if not value.isdigit():
            raise SystemExit(f"--model-limit needs a whole number "
                             f"(got {value or 'nothing'})\n\n{usage}")
        model_limit = int(value)
        argv = argv[:i] + argv[i + 2:]
    backend_names, argv = _backend_flag(argv, usage)
    reject_unknown_flags(argv, FLAGS, usage)
    try:
        run_classification(write="--write" in argv,
                           skip_model="--skip-model" in argv,
                           model_limit=model_limit,
                           backend_names=backend_names)
    except KeyboardInterrupt:
        err("interrupted — nothing was written (writes are all-or-nothing)")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
