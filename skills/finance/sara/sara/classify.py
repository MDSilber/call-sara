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
  tier 3  a cheap batched claude-haiku call over the weak-signal residue,
          JSON-schema-constrained, applied only at >= the confidence floor;
          weaker guesses are printed as suggestions and the posting stays
          in review. The model picks from the vault's real chart — it can
          never invent a category.

Every rewrite lands through the same machinery as recategorize.py (atomic
tmp+rename per file, bean-check, full rollback on failure), and every
machine-moved posting gains `classifier:` metadata naming its tier and
signal ("plaid:FOOD_AND_DRINK_COFFEE", "haiku:0.91") so the move is
auditable and re-doable. Machine classifications never create payee_rules —
rules stay human-taught via the app/review loop; instead the report calls
out recurring residue worth teaching a rule for.

Usage:
  python -m sara.classify                dry run (default): report only
  python -m sara.classify --write        apply (atomic + bean-check)
  python -m sara.classify --skip-model   tiers 1-2 only — no API call
  python -m sara.classify --model-limit N  send at most N txns to the model
  (or: tools/run classify.py — same flags. A dry run still calls the model
  for suggestions when tier 3 is armed; --skip-model keeps the run free.)

CONFIG — $VAULT/rules.toml (all optional; defaults shown):

  [classification]
  tier2 = true                    # Plaid-signal tier
  tier3 = true                    # model tier (also needs the key below)
  plaid_min_confidence = "high"   # or "very_high"
  model_min_confidence = 0.8      # apply threshold, 0-1
  model = "claude-haiku-4-5"      # Messages API model id

  [plaid_category_map]
  "FOOD_AND_DRINK_COFFEE" = "Expenses:Food:Coffee"

CREDENTIALS: $VAULT/.secrets/anthropic.env holds ANTHROPIC_API_KEY=sk-...
(0600; .secrets/ is gitignored). Without it tier 3 quietly skips and the
report says how to enable it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NamedTuple

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

DEFAULT_MODEL = "claude-haiku-4-5"
MODEL_BATCH_SIZE = 40  # txns per Messages API request — small enough to stay sharp
MODEL_MAX_TOKENS = 8192
MAX_HISTORY_EXAMPLES = 40  # recent payee->category pairs shown to the model
MAX_RULE_EXAMPLES = 25
RULE_SUGGESTION_MIN = 3  # a payee seen this often in the residue earns a rule hint
# claude-haiku-4-5 list price (USD per million tokens, 2026-08) — estimate only.
PRICE_IN_PER_MTOK = Decimal("1.00")
PRICE_OUT_PER_MTOK = Decimal("5.00")

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
    model_min: Decimal  # 0-1
    model: str


def _config() -> Config:
    c = as_dict(rules().get("classification"))
    plaid_min = str(c.get("plaid_min_confidence") or "high").lower()
    if plaid_min not in CONF_RANK:
        plaid_min = "high"
    model_min = _dec(str(c.get("model_min_confidence", "0.8")))
    if model_min is None or not ZERO <= model_min <= ONE:
        model_min = Decimal("0.8")
    return Config(
        tier2=bool(c.get("tier2", True)),
        tier3=bool(c.get("tier3", True)),
        plaid_min=plaid_min,
        model_min=model_min,
        model=str(c.get("model") or DEFAULT_MODEL),
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


def model_system_prompt(categories: list[str], rule_lines: list[str],
                        history_lines: list[str]) -> str:
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


def _batch_payload(batch: list[ReviewTxn]) -> str:
    rows = [{"id": i, "date": t.when.isoformat(), "payee": t.payee,
             "amount": f"{t.amount:.2f}", "account": t.primary,
             "hint": t.plaid_detailed} for i, t in enumerate(batch)]
    return json.dumps({"transactions": rows}, ensure_ascii=False)


def parse_model_reply(text: str, batch_size: int) -> dict[int, tuple[str, Decimal, str]] | str:
    """-> {id: (category, confidence, reason)}, or a refusal reason.

    Structural problems (bad JSON, wrong shapes, out-of-range or duplicate
    ids) refuse the WHOLE batch — a reply that breaks the schema has
    forfeited trust. Unknown categories are judged per-txn by the caller.
    """
    try:
        data = json.loads(text, parse_float=Decimal)
    except ValueError:
        return "reply was not valid JSON"
    results = as_list(as_dict(data).get("results"))
    out: dict[int, tuple[str, Decimal, str]] = {}
    for item in results:
        row = as_dict(item)
        raw_id, category = row.get("id"), row.get("category")
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


class ModelRun(NamedTuple):
    decisions: list[Decision]
    queued: list[Queued]
    sent: int
    usage: ModelUsage
    notes: list[str]  # batch-level refusals / errors


def run_model_tier(residue: list[ReviewTxn], cfg: Config, chart: set[str],
                   history: list[Example], call: ModelCall,
                   limit: int | None) -> ModelRun:
    to_send = residue if limit is None else residue[:limit]
    overflow = [] if limit is None else residue[limit:]
    categories = model_categories(chart)
    allowed = set(categories)
    system = model_system_prompt(categories, _rule_examples(),
                                 _history_examples(history))
    decisions: list[Decision] = []
    queued: list[Queued] = []
    notes: list[str] = []
    tokens_in = tokens_out = 0
    aborted = False
    for at in range(0, len(to_send), MODEL_BATCH_SIZE):
        batch = to_send[at:at + MODEL_BATCH_SIZE]
        if aborted:
            queued.extend(Queued(t, "model call aborted earlier in this run") for t in batch)
            continue
        try:
            text, usage = call(cfg.model, system, _batch_payload(batch), MODEL_MAX_TOKENS)
        except Exception as e:  # network/API failure: keep the run, keep the queue
            notes.append(f"batch {at // MODEL_BATCH_SIZE + 1}: API call failed — {e}")
            queued.extend(Queued(t, "model call failed") for t in batch)
            aborted = True
            continue
        tokens_in += usage.input_tokens
        tokens_out += usage.output_tokens
        parsed = parse_model_reply(text, len(batch))
        if isinstance(parsed, str):
            notes.append(f"batch {at // MODEL_BATCH_SIZE + 1}: refused — {parsed}; "
                         f"its {len(batch)} txns stay queued")
            queued.extend(Queued(t, "model reply refused (malformed)") for t in batch)
            continue
        for i, t in enumerate(batch):
            answer = parsed.get(i)
            if answer is None:
                queued.append(Queued(t, "model gave no answer"))
                continue
            category, conf, reason = answer
            if category not in allowed:
                queued.append(Queued(t, f"model named unknown category {category!r}"))
            elif category == t.review_account:
                queued.append(Queued(t, "model agrees it belongs in review"))
            elif conf < cfg.model_min:
                queued.append(Queued(t, f"model unsure ({conf:.2f} < {cfg.model_min})",
                                     suggestion=f"{category} ({conf:.2f}) — {reason}"))
            else:
                decisions.append(Decision(t, category, "model",
                                          f"haiku:{conf:.2f}", note=reason))
    queued.extend(Queued(t, f"past --model-limit {limit}") for t in overflow)
    return ModelRun(decisions, queued, len(to_send),
                    ModelUsage(tokens_in, tokens_out), notes)


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
                       model_call: ModelCall | None = None) -> Summary:
    """The whole run: scan -> tiers -> report -> (optionally) rewrite."""
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
    key = model_api_key()
    if residue and not skip_model and cfg.tier3 and (model_call or key):
        call = model_call or anthropic_model_call(key or "")
        run = run_model_tier(residue, cfg, chart, scan.history, call, model_limit)
        model_ds = run.decisions
        queued.extend(run.queued)
        verb = "applied" if write else "would apply"
        print(f"  tier 3 — {cfg.model}: {run.sent} sent, {len(model_ds)} {verb} "
              f"(>= {cfg.model_min}), {len(run.queued)} stay queued")
        for d in sorted(model_ds, key=lambda d: d.txn.when):
            print(_txn_line(d.txn, f"-> {d.account}  [{d.label}] {d.note}"))
        for note in run.notes:
            print(f"    ! {note}")
        cost = (run.usage.input_tokens * PRICE_IN_PER_MTOK
                + run.usage.output_tokens * PRICE_OUT_PER_MTOK) / 1_000_000
        print(f"    cost: {run.usage.input_tokens:,} in + "
              f"{run.usage.output_tokens:,} out tokens ~ ${cost:.4f}")
        decisions.extend(model_ds)
    else:
        why = ("--skip-model" if skip_model
               else "off ([classification] tier3 = false)" if not cfg.tier3
               else "no key — put ANTHROPIC_API_KEY=sk-... in "
                    f"{ANTHROPIC_ENV_FILE} to enable" if not (model_call or key)
               else "nothing left for it")
        print(f"  tier 3 — model: {why}")
        queued.extend(
            Queued(t, "no signal (model tier not run)",
                   suggestion=(f"plaid {t.plaid_detailed} "
                               f"({t.plaid_confidence or 'no confidence'}) — below the bar"
                               if t.plaid_detailed else ""))
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
    reject_unknown_flags(argv, FLAGS, usage)
    try:
        run_classification(write="--write" in argv,
                           skip_model="--skip-model" in argv,
                           model_limit=model_limit)
    except KeyboardInterrupt:
        err("interrupted — nothing was written (writes are all-or-nothing)")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
