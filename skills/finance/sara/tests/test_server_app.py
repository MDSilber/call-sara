"""Sara App server contract tests — run against a REAL built vault.

Set FINANCE_TEST_VAULT to any initialized vault directory (init_vault.sh
--demo makes a rich one). The suite copies it to a temp dir, points the
server there, and drives the real FastAPI app with TestClient:

  * every GET endpoint answers 200 (snapshot rooms AND the DuckDB
    exploratory surface: activity search, register, accounts, owners) —
    and the cut surfaces (findings/insights/drill/map) answer 404
  * eight-plus dollar figures match tools/run query.py to the dollar —
    including a register running balance, a per-lot value, and the owner
    lens conservation sum
  * the drag-drop upload flow lands a fixture OFX through the gated writer
    (plan → confirm → bean-check green), with the traversal/cap/sniff
    refusals exercised
  * categorize posts a rule into rules.toml, recategorize rewrites the
    planted postings, and bean-check still passes
  * set-goal edits facts/goals and survives a re-read; dismiss silences a
    finding everywhere and undoes cleanly

Without FINANCE_TEST_VAULT the whole module skips (same convention as the
FINANCE_TEST_VENV-gated importer paths).
"""
# The suite exercises untyped TestClient/json boundaries end to end:
# pyright: basic
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

SOURCE_VAULT = os.environ.get("FINANCE_TEST_VAULT", "")
if not SOURCE_VAULT or not (Path(SOURCE_VAULT) / "ledger" / "main.beancount").is_file():
    pytest.skip("FINANCE_TEST_VAULT not set to a built vault — skipping server "
                "contract tests", allow_module_level=True)

SOURCE = Path(SOURCE_VAULT).resolve()
VENV_PY = SOURCE / ".venv" / "bin" / "python"
if not VENV_PY.exists():
    pytest.skip("test vault has no .venv — skipping", allow_module_level=True)

# One throwaway copy for the whole module: reads first, writes after.
_TMP = Path(tempfile.mkdtemp(prefix="sara-app-test-"))
VAULT = _TMP / "vault"
shutil.copytree(SOURCE, VAULT, symlinks=True,
                ignore=shutil.ignore_patterns(".venv", ".git"))
# the copy leans on the SOURCE vault's venv for bean-query/bean-check
(VAULT / ".venv").symlink_to(SOURCE / ".venv")

PLANT_PAYEE = "PLANTED COFFEE 042"


def _pick_card_account() -> str:
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        m = re.search(r"^\d{4}-\d{2}-\d{2} open (Liabilities:\S+)\s+USD",
                      f.read_text(), re.M)
        if m:
            return m.group(1)
    pytest.skip("test vault has no USD liability account to plant against")


PLANT_ACCOUNT = _pick_card_account()
_ledger_files = sorted((VAULT / "ledger").glob("2*.beancount"))
PLANT_FILE = _ledger_files[-1] if _ledger_files else VAULT / "ledger" / "main.beancount"
with PLANT_FILE.open("a") as fh:
    fh.write(f'\n2026-08-04 * "{PLANT_PAYEE}" ""\n'
             f"  {PLANT_ACCOUNT}   -6.75 USD\n"
             f"  Expenses:Uncategorized\n"
             f'\n2026-08-05 * "{PLANT_PAYEE}" ""\n'
             f"  {PLANT_ACCOUNT}   -4.50 USD\n"
             f"  Expenses:Uncategorized\n")

# The Connections surface needs one configured item on the copy: the demo
# alias, routed at the template's Chase accounts, token present offline.
with (VAULT / "rules.toml").open("a") as fh:
    fh.write('\n[sources.plaid.items.demo]\n'
             'access_token_env = "PLAID_DEMO_ACCESS_TOKEN"\n'
             'products = ["transactions"]\n'
             '[sources.plaid.items.demo.accounts]\n'
             'checking = "Assets:US:Chase:Checking4321"\n'
             'card = "Liabilities:US:Chase:Card5678"\n')
_SECRETS = VAULT / ".secrets"
_SECRETS.mkdir(exist_ok=True)
(_SECRETS / "plaid.env").write_text(
    "PLAID_CLIENT_ID=demo-client\nPLAID_SECRET=demo-secret\n"
    "PLAID_DEMO_ACCESS_TOKEN=access-demo-offline\n")

# The v2 server reads from summary.json + analytics.duckdb (never the
# ledger), so the planted rows must be materialized the same way the write
# side does it: regenerate both artifacts on the copy before the app boots.
for _argv in (["-m", "sara.analytics"],
              [str(Path(__file__).resolve().parents[2] / "tools" / "summary.py")]):
    _proc = subprocess.run([str(VENV_PY), *_argv], capture_output=True, text=True,
                           env={**os.environ, "FINANCE_VAULT": str(VAULT)},
                           cwd=str(VAULT))
    assert _proc.returncode == 0, f"read-model regen failed: {_proc.stderr[-800:]}"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sara.typed import as_dict, as_dicts, as_list  # noqa: E402

GET_ENDPOINTS = ["ping", "glance", "activity", "spend", "networth",
                 "investments", "goals", "autopilot", "freshness",
                 "activity?q=trader&limit=10", "accounts", "owners",
                 "search?q=chase", "connections",
                 "investments?owner=jordan",
                 "spend?owner=alex", "spend?owner=joint"]
CUT_ENDPOINTS = ["findings", "insights", "spend/drill?category=Food&month=2026-07",
                 "map?owner=alex"]
_token = ""  # filled by the client fixture (per-launch server token)


@pytest.fixture(scope="module")
def client():
    """The app, bound to the throwaway vault copy.

    tools/vault.py resolves FINANCE_VAULT when first imported, so the env
    switch happens HERE (test time, this module only) and is restored right
    after import — the sibling test modules' fresh_vault fixture keeps
    rebuilding the conftest scratch vault, never this copy.
    """
    global _token
    before = os.environ.get("FINANCE_VAULT")
    os.environ["FINANCE_VAULT"] = str(VAULT)
    try:
        from sara.server.app import create_app
        from sara.server.security import TOKEN as launch_token
    finally:
        if before is not None:
            os.environ["FINANCE_VAULT"] = before
    _token = launch_token
    import vault as tools_vault
    if tools_vault.VAULT != VAULT:
        pytest.skip("tools/vault bound to a different vault before this "
                    "module ran — run test_server_app.py on its own")
    app = create_app(port=8787)
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        yield c


def _cli(*args: str) -> str:
    out = subprocess.run(
        [str(VENV_PY), str(Path(__file__).resolve().parents[2] / "tools" / args[0]),
         *args[1:]],
        capture_output=True, text=True,
        env={**os.environ, "FINANCE_VAULT": str(VAULT)})
    assert out.returncode == 0, out.stderr
    return out.stdout


def _body(r: httpx.Response) -> dict[str, object]:
    """Response JSON as a typed dict (the tests' one Any crossing)."""
    return as_dict(r.json())


def _dollars(text: str) -> float:
    m = re.search(r"(-?)\$([\d,]+)", text)
    assert m, f"no dollar figure in {text!r}"
    return float(m.group(2).replace(",", "")) * (-1 if m.group(1) else 1)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1.0  # both sides render whole dollars


# ------------------------------------------------------------------- reads
def test_every_get_returns_200(client: TestClient) -> None:
    for ep in GET_ENDPOINTS:
        r = client.get(f"/api/{ep}")
        assert r.status_code == 200, f"/api/{ep} -> {r.status_code}"
        assert _body(r) is not None


def test_cut_endpoints_are_gone(client: TestClient) -> None:
    """The simplicity cuts: findings, insights, drill, and map-by-owner
    answered their last request — anything under /api that isn't served is
    a hard 404, never the SPA fallback."""
    for ep in CUT_ENDPOINTS:
        r = client.get(f"/api/{ep}")
        assert r.status_code == 404, f"/api/{ep} -> {r.status_code}"


def test_glance_spotlight_and_hero_agree(client: TestClient) -> None:
    """The adaptive fourth tile is always present with a declared kind, and
    the hero line's decision count derives from the same live needs cards
    the autopilot queue renders."""
    glance = _body(client.get("/api/glance"))
    tiles = as_dict(glance["tiles"])
    assert "education" not in tiles
    spot = as_dict(tiles["spotlight"])
    assert spot["kind"] in ("win", "event", "edu")
    assert str(spot["label"])
    # a spotlight has ONE big slot: verdict or fig, never neither
    assert str(spot["verdict"]) or str(spot["fig"])
    line = str(glance["sara"])
    cards = as_dicts(as_dict(_body(client.get("/api/autopilot"))["needs"])["cards"])
    n_alerts = sum(1 for c in cards if c["kind"] == "alert")
    m = re.match(r"(\w+) things? wants? a decision", line)
    words = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5,
             "Six": 6, "Seven": 7, "Eight": 8, "Nine": 9}
    if m:
        counted = words.get(m.group(1), None)
        if counted is None and m.group(1).isdigit():
            counted = int(m.group(1))
        assert counted == n_alerts
    else:
        assert n_alerts == 0


def test_activity_bad_cursor_is_ignored(client: TestClient) -> None:
    """A mangled keyset cursor serves the first page, never a 500."""
    clean_page = _body(client.get("/api/activity?limit=5"))
    for bad in ("not-a-date:12", "2026-13-99:7", "::", "2026-01-01:abc"):
        r = client.get(f"/api/activity?limit=5&cursor={bad}")
        assert r.status_code == 200, f"cursor {bad!r} -> {r.status_code}"
        assert _body(r)["rows"] == clean_page["rows"]


def test_register_bad_cursor_is_ignored(client: TestClient) -> None:
    account = "Assets:US:Chase:Checking4321"
    r = client.get(f"/api/register?account={account}&cursor=9999-99-99:1")
    assert r.status_code == 200
    assert _body(r)["found"] is True


def test_networth_figures_match_query_py(client: TestClient) -> None:
    cli = _cli("query.py", "networth")
    m = re.search(r"Liquid net worth: (-?\$[\d,]+)\s+\(assets (-?\$[\d,]+)"
                  r" · liabilities (-?\$[\d,]+)\)", cli)
    assert m, cli
    liquid, assets = _dollars(m.group(1)), _dollars(m.group(2))
    api = _body(client.get("/api/networth"))
    assert _close(_dollars(str(as_dict(api["headline"])["liquid"])), liquid)   # fig 1
    cap = str(as_dict(api["map"])["caption"])
    assert _close(_dollars(cap.split("·")[0]), assets)                  # fig 2
    glance = _body(client.get("/api/glance"))
    nw_tile = as_dict(as_dict(glance["tiles"])["networth"])
    assert _close(_dollars(str(nw_tile["value"])), liquid)              # fig 3


def test_spend_figures_match_query_py(client: TestClient) -> None:
    api = _body(client.get("/api/spend"))
    rooms = as_dict(api["rooms"])
    periods = {str(p["key"]): p for p in as_dicts(rooms["periods"])}
    ym = re.match(r"(\w+) (\d{4})", str(as_dict(periods["cur"])["win"]))
    assert ym
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
              "Sep", "Oct", "Nov", "Dec"]
    period = f"{ym.group(2)}-{months.index(ym.group(1)):02d}"
    cli = _cli("query.py", "spend", period)
    total_line = next(ln for ln in cli.splitlines() if "TOTAL" in ln)
    assert _close(_dollars(str(as_dict(periods["cur"])["total"])),
                  _dollars(total_line))                                 # fig 4
    # the biggest category, to the dollar                                  # fig 5
    top_cli = cli.splitlines()[0]
    cat_m = re.match(r"\s*(-?\$[\d,]+)\s+(.+)$", top_cli)
    assert cat_m, top_cli
    cat_name = cat_m.group(2).strip()
    cats = as_dicts(rooms["cats"])
    order = [int(str(i)) for i in as_list(as_dict(rooms["order"])["cur"])]
    top_api = next(c for c in (cats[i] for i in order)
                   if str(c["name"]) == cat_name)
    cur_amt = str(as_dict(as_dict(top_api["per"])["cur"])["amt"])
    assert _close(_dollars(cur_amt), _dollars(top_cli))


def test_cashflow_income_matches_query_py(client: TestClient) -> None:
    api = as_dict(_body(client.get("/api/spend"))["cheshbon"])
    ym = re.match(r"(\w+) (\d{4})", str(api["window"]))
    assert ym
    cli = _cli("query.py", "cashflow", ym.group(2))
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
              "Sep", "Oct", "Nov", "Dec"]
    row = [ln for ln in cli.splitlines()
           if ln.startswith(f"{ym.group(2)}-{months.index(ym.group(1)):02d}")]
    assert row, cli
    figs = re.findall(r"-?\$[\d,]+", row[0])
    assert _close(_dollars(str(api["inc"])), _dollars(figs[0]))         # fig 6
    assert _close(_dollars(str(api["exp"])), _dollars(figs[1]))         # fig 7


def test_uncategorized_count_matches_query_py(client: TestClient) -> None:
    cli = _cli("query.py", "uncategorized")
    m = re.search(r"(\d+) uncategorized postings", cli)
    assert m
    api = _body(client.get("/api/activity"))
    uncat = as_dict(api["uncategorized"])
    assert uncat["count"] == int(m.group(1))                            # count
    assert int(str(uncat["count"])) >= 2  # the planted pair shows


def test_positions_match_query_py(client: TestClient) -> None:
    cli = _cli("query.py", "positions")
    api = _body(client.get("/api/investments"))
    by_symbol = {str(p["symbol"]): p for p in as_dicts(api["positions"])}
    checked = 0
    for line in cli.splitlines():
        m = re.match(r"\s*(-?\$[\d,]+)\s+(\S+)\s", line)
        if not m or m.group(2) not in by_symbol:
            continue
        assert _close(_dollars(str(as_dict(by_symbol[m.group(2)])["value"])),
                      _dollars(m.group(1)))                             # fig 8+
        checked += 1
    assert checked >= 1


# --------------------------------------------------------------- security
def test_bad_host_rejected(client: TestClient) -> None:
    r = client.get("/api/glance", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    r = client.get("/api/glance", headers={"Host": "attacker.io:8787"})
    assert r.status_code == 403


def test_actions_require_token(client: TestClient) -> None:
    r = client.post("/api/actions/dismiss",
                    json={"finding_id": "abcdef123456", "until": "2026-09-01"})
    assert r.status_code == 403
    r = client.post("/api/actions/dismiss",
                    headers={"X-Sara-Token": "wrong"},
                    json={"finding_id": "abcdef123456", "until": "2026-09-01"})
    assert r.status_code == 403


def test_actions_reject_foreign_origin(client: TestClient) -> None:
    r = client.post("/api/actions/set-goal",
                    headers={"X-Sara-Token": _token,
                             "Origin": "https://evil.example"},
                    json={"key": "education_target", "value": 1})
    assert r.status_code == 403


def test_token_rides_index_when_frontend_built(client: TestClient) -> None:
    r = client.get("/")
    if r.status_code == 503:
        pytest.skip("frontend not built — token-injection covered elsewhere")
    assert _token in r.text
    assert r.headers["cache-control"] == "no-store"


# ----------------------------------------------------------------- actions
def _auth(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"X-Sara-Token": _token, **(extra or {})}


def test_categorize_end_to_end(client: TestClient) -> None:
    target = next(str(a["account"])
                  for a in as_dicts(_body(client.get("/api/activity"))["categories"])
                  if str(a["account"]).startswith("Expenses:"))
    r = client.post("/api/actions/categorize", headers=_auth(),
                    json={"payee_pattern": "PLANTED COFFEE",
                          "account": target, "apply_history": True})
    assert r.status_code == 200, r.text
    body = _body(r)
    assert body["changed"] == 2 and body["applied"] is True
    rules_text = (VAULT / "rules.toml").read_text()
    assert '"PLANTED COFFEE"' in rules_text and target in rules_text
    ledger = PLANT_FILE.read_text()
    assert "Expenses:Uncategorized" not in ledger.split(PLANT_PAYEE, 1)[1].split("\n\n")[0]
    assert f"  {target}" in ledger.split(PLANT_PAYEE, 1)[1]
    check = subprocess.run([str(SOURCE / ".venv" / "bin" / "bean-check"),
                            str(VAULT / "ledger" / "main.beancount")],
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert "bean-check passed" in str(body["report"])


def test_categorize_rejects_bad_input(client: TestClient) -> None:
    r = client.post("/api/actions/categorize", headers=_auth(),
                    json={"payee_pattern": "(", "account": "Expenses:Food",
                          "apply_history": False})
    assert r.status_code == 422
    r = client.post("/api/actions/categorize", headers=_auth(),
                    json={"payee_pattern": "x", "account": "Assets:US:Somewhere",
                          "apply_history": False})
    assert r.status_code == 422


def test_set_goal_end_to_end(client: TestClient) -> None:
    before = {str(s["key"]): s["value"]
              for s in as_dicts(_body(client.get("/api/goals"))["settings"])}
    r = client.post("/api/actions/set-goal", headers=_auth(),
                    json={"key": "education_target", "value": 155000})
    assert r.status_code == 200, r.text
    assert _body(r)["value"] == 155000
    assert _body(r)["previous"] == before["education_target"]
    after = {str(s["key"]): s["value"]
             for s in as_dicts(_body(client.get("/api/goals"))["settings"])}
    assert after["education_target"] == 155000
    r = client.post("/api/actions/set-goal", headers=_auth(),
                    json={"key": "cash_cushion_months", "value": 3})
    assert r.status_code == 422  # not on the allowlist


def test_dismiss_end_to_end(client: TestClient) -> None:
    queue = as_dicts(_body(client.get("/api/autopilot"))["queue"])
    if not queue:
        pytest.skip("test vault has no open findings to dismiss")
    fid, title = str(queue[0]["id"]), str(queue[0]["title"])
    r = client.post("/api/actions/dismiss", headers=_auth(),
                    json={"finding_id": fid, "until": "2026-12-31",
                          "title": title})
    assert r.status_code == 200, r.text
    after = _body(client.get("/api/autopilot"))
    assert all(q["id"] != fid for q in as_dicts(after["queue"]))
    assert any(d["id"] == fid and d["active"] for d in as_dicts(after["dismissed"]))
    stored = as_dict(json.loads((VAULT / "reports" / "dismissals.json").read_text()))
    assert fid in as_dict(stored["dismissed"])
    r = client.post("/api/actions/dismiss", headers=_auth(),
                    json={"finding_id": fid, "until": None})
    assert r.status_code == 200 and _body(r)["removed"] is True
    restored = as_dicts(_body(client.get("/api/autopilot"))["queue"])
    assert any(q["id"] == fid for q in restored)


# ------------------------------------------------- v2: the exploratory reads
def _sql_cell(bql: str, col: str) -> str:
    """One value out of query.py sql's TSV output."""
    out = _cli("query.py", "sql", bql)
    lines = out.strip().splitlines()
    assert len(lines) >= 2, out
    header = lines[0].split("\t")
    return lines[1].split("\t")[header.index(col)]


def test_activity_search_matches_query_py(client: TestClient) -> None:
    """The ILIKE payee filter, held to bean-query's regex count + sum."""
    n = int(float(_sql_cell(
        "SELECT count(*) AS n WHERE account ~ '^(Income|Expenses)' "
        "AND payee ~ 'Trader'", "n")))
    spent_cell = _sql_cell(
        "SELECT sum(convert(position,'USD')) AS v "
        "WHERE account ~ '^Expenses' AND payee ~ 'Trader'", "v")
    spent = float(re.sub(r"[^\d.-]", "", spent_cell.split(" USD")[0]))
    api = _body(client.get("/api/activity?q=trader&limit=200"))
    assert api["matched"] == n and n >= 4                               # count
    assert _close(_dollars(str(as_dict(api["totals"])["spent"])), spent)  # fig 9
    for row in as_dicts(api["rows"]):
        blob = f"{row['payee']} {row['narration']}".lower()
        assert "trader" in blob


def test_activity_amount_filter(client: TestClient) -> None:
    api = _body(client.get("/api/activity?amount_min=1000&limit=200"))
    n = int(float(_sql_cell(
        "SELECT count(*) AS n WHERE account ~ '^(Income|Expenses)' "
        "AND (number >= 1000 OR number <= -1000)", "n")))
    assert api["matched"] == n and n > 0


def test_register_running_balance_matches_query_py(client: TestClient) -> None:
    """The newest register row's running balance IS the account balance."""
    account = "Assets:US:Chase:Checking4321"
    cell = _sql_cell(
        f"SELECT sum(convert(position,'USD')) AS v WHERE account = '{account}'", "v")
    truth = float(re.sub(r"[^\d.-]", "", cell.split(" USD")[0]))
    api = _body(client.get(f"/api/register?account={account}"))
    assert api["found"] is True and len(as_dicts(api["rows"])) > 0
    newest = as_dicts(api["rows"])[0]
    got = float(re.sub(r"[^\d.-]", "", str(newest["balance"])))
    assert abs(got - truth) < 0.01                                      # fig 10 (cents)
    assert _close(_dollars(str(api["balance"])), truth)                 # header, whole $


def test_lots_sum_matches_positions(client: TestClient) -> None:
    """Per-lot values sum to the symbol's position value (independent path:
    DB lots x latest price vs bean-query convert)."""
    api = _body(client.get("/api/investments"))
    lots = as_dicts(api["lots"])
    assert lots, "demo vault should carry cost-basis lots"
    for lot in lots:
        assert lot["term"] in ("LT", "ST") and lot["acquired"]
    agg: dict[str, float] = {}
    for lot in lots:
        agg[str(lot["symbol"])] = agg.get(str(lot["symbol"]), 0.0) + float(str(lot["valueN"]))
    by_symbol = {str(p["symbol"]): p for p in as_dicts(api["positions"])}
    checked = 0
    for sym, total in agg.items():
        if sym in by_symbol and by_symbol[sym]["value"] is not None:
            assert _close(total, _dollars(str(by_symbol[sym]["value"])))  # fig 11+
            checked += 1
    assert checked >= 2


def test_lots_verdict_matches_the_numbers(client: TestClient) -> None:
    """The one-sentence harvest verdict is computed from the same gainN
    numbers the table carries — no second arithmetic path."""
    api = _body(client.get("/api/investments"))
    lots = as_dicts(api["lots"])
    verdict = api.get("lots_verdict")
    if not lots:
        assert verdict is None
        return
    assert isinstance(verdict, str) and verdict
    losses = [float(str(lot["gainN"])) for lot in lots
              if isinstance(lot.get("gainN"), (int, float))
              and float(str(lot["gainN"])) < 0]
    if not losses or abs(min(losses)) < 250:
        assert verdict.startswith("Nothing worth harvesting")
    else:
        assert "harvesting look" in verdict


def test_goals_room_is_education_only(client: TestClient) -> None:
    """The walk-away/retirement framing is gone: the app's writable goal
    surface is the college target, nothing else."""
    api = _body(client.get("/api/goals"))
    keys = [str(s["key"]) for s in as_dicts(api["settings"])]
    assert keys == ["education_target"]
    ask = api.get("ask")
    if ask is not None:
        a = as_dict(ask)
        assert re.fullmatch(r"[0-9a-f]{12}", str(a["id"]))
        assert isinstance(a["dismissed"], bool)
    for gone in ("retirement_target", "show_walkaway"):
        r = client.post("/api/actions/set-goal", headers=_auth(),
                        json={"key": gone, "value": 1})
        assert r.status_code in (403, 422)


def test_networth_carries_cash_story_not_thesis(client: TestClient) -> None:
    api = _body(client.get("/api/networth"))
    assert "thesis" not in api
    cash = api.get("cash")
    if cash is not None:
        c = as_dict(cash)
        assert "$" in str(c["line"]) and c["cls"] in ("", "bad")


def test_owner_lens_conserves_totals(client: TestClient) -> None:
    """Per-owner spend slices sum to the household total (every demo account
    carries an owner, so nothing leaks out of the lens)."""
    total = _dollars(str(as_dict(_body(client.get(
        "/api/activity?date_to=2026-07-31"))["totals"])["spent"]))
    sliced = 0.0
    for owner in ("alex", "jordan", "joint"):
        api = _body(client.get(f"/api/activity?owner={owner}&date_to=2026-07-31"))
        sliced += _dollars(str(as_dict(api["totals"])["spent"]))
        for row in as_dicts(api["rows"]):
            assert row["owner"] == owner
    assert abs(sliced - total) <= 3.0  # three whole-dollar renderings


DEMO_OWNERS = ("alex", "jordan", "joint")


def test_spend_all_path_is_the_snapshot(client: TestClient) -> None:
    """No lens (and owner=all) serves summary.json's app.spend verbatim —
    the household path must stay byte-identical to the snapshot."""
    base = _body(client.get("/api/spend"))
    assert _body(client.get("/api/spend?owner=all")) == base
    snapshot = json.loads((VAULT / "reports" / "summary.json").read_text())
    assert base == snapshot["app"]["spend"]


def test_spend_owner_slices_conserve_household_totals(client: TestClient) -> None:
    """The lens is a partition: per-owner paced-month expenses sum to the
    household total from the bean-query-built snapshot, to the dollar
    (every demo funding account carries an owner). Fig 12."""
    household = as_dict(_body(client.get("/api/spend"))["cheshbon"])
    hh_month = str(household["window"]).split(" · ")[0]
    sliced = 0.0
    for owner in DEMO_OWNERS:
        api = _body(client.get(f"/api/spend?owner={owner}"))
        assert api["owner"] == owner
        ches = as_dict(api["cheshbon"])
        assert str(ches["window"]).split(" · ")[0] == hh_month  # same paced month
        sliced += _dollars(str(ches["exp"]))
    # four whole-dollar renderings stack at most $0.50 of rounding each
    assert abs(sliced - _dollars(str(household["exp"]))) <= 4.0


def test_spend_owner_month_matches_activity_engine(client: TestClient) -> None:
    """Cross-engine to the dollar: the lens cheshbon's month expenses equal
    the Activity engine's owner-filtered month total (independent SQL path
    over the same DB — a wrong owner-join column breaks this)."""
    for owner in ("alex", "jordan"):
        ches = as_dict(_body(client.get(f"/api/spend?owner={owner}"))["cheshbon"])
        m = re.match(r"(\w+) (\d{4})", str(ches["window"]))
        assert m
        months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                  "Sep", "Oct", "Nov", "Dec"]
        y, mo = int(m.group(2)), months.index(m.group(1))
        d0 = f"{y:04d}-{mo:02d}-01"
        d1 = f"{y:04d}-{mo:02d}-28" if mo == 2 else f"{y:04d}-{mo:02d}-31"
        act = _body(client.get(
            f"/api/activity?owner={owner}&date_from={d0}&date_to={d1}&limit=1"))
        assert _close(_dollars(str(as_dict(act["totals"])["spent"])),
                      _dollars(str(ches["exp"])))                       # fig 13


def test_spend_owner_categories_conserve(client: TestClient) -> None:
    """Owner category sums: each owner's rail is internally consistent
    (rows sum to the period total) and the owners' slices of the top
    household category sum back to the household row. Fig 14."""
    hh_rooms = as_dict(_body(client.get("/api/spend"))["rooms"])
    hh_cats = as_dicts(hh_rooms["cats"])
    top_idx = int(str(as_list(as_dict(hh_rooms["order"])["six"])[0]))
    top = hh_cats[top_idx]
    top_name = str(top["name"])
    hh_amt = _dollars(str(as_dict(as_dict(top["per"])["six"])["amt"]))
    sliced = 0.0
    for owner in DEMO_OWNERS:
        rooms = as_dict(_body(client.get(f"/api/spend?owner={owner}"))["rooms"])
        cats = as_dicts(rooms["cats"])
        # internal consistency: visible six-period rows sum to the header
        period_total = next(
            _dollars(str(p["total"])) for p in as_dicts(rooms["periods"])
            if p["key"] == "six")
        row_sum = sum(
            _dollars(str(as_dict(as_dict(c["per"])["six"])["amt"]))
            for c in cats if "six" in as_dict(c["per"]))
        assert abs(row_sum - period_total) <= len(cats) + 1.0
        mine = next((c for c in cats if str(c["name"]) == top_name), None)
        if mine and "six" in as_dict(mine["per"]):
            sliced += _dollars(str(as_dict(as_dict(mine["per"])["six"])["amt"]))
    assert abs(sliced - hh_amt) <= 4.0


def test_spend_owner_lens_actually_differs(client: TestClient) -> None:
    """The reported bug: every owner used to get the identical payload.
    Distinct demo owners must see distinct numbers and a titled card."""
    alex = _body(client.get("/api/spend?owner=alex"))
    jordan = _body(client.get("/api/spend?owner=jordan"))
    assert as_dict(alex["pace"])["title"] == "Alex\u2019s spending"
    assert as_dict(jordan["pace"])["title"] == "Jordan\u2019s spending"
    a_exp = _dollars(str(as_dict(alex["cheshbon"])["exp"]))
    j_exp = _dollars(str(as_dict(jordan["cheshbon"])["exp"]))
    assert abs(a_exp - j_exp) > 25  # the demo owners spend differently
    assert alex["tile"] != jordan["tile"] or alex["pace_chart"] != jordan["pace_chart"]
    # the pace card mirrors the snapshot semantics: a median-path baseline
    sub = str(as_dict(alex["pace"])["sub"])
    assert "median path" in sub and "full months" in sub
    tile = as_dict(alex["tile"])
    assert str(tile["verdict"]) in ("Under pace", "On pace", "Running hot",
                                    "Finding pace")
    assert "typical" in str(tile["sub"])


def test_sara_line_verdict_only_with_night_daypart(client: TestClient) -> None:
    """The snapshot's sara lines are verdicts (no on-page navigation) and
    carry the late-evening "tonight" variant for the live picker."""
    summary = json.loads((VAULT / "reports" / "summary.json").read_text())
    by_dp = as_dict(as_dict(as_dict(summary["app"])["glance"])["sara_by_daypart"])
    assert set(by_dp) == {"morning", "afternoon", "evening", "night"}
    for line in by_dp.values():
        low = str(line).lower()
        assert "next line" not in low and "below" not in low
        assert "autopilot" not in low


def test_connections_payload_shape(client: TestClient) -> None:
    api = _body(client.get("/api/connections"))
    assert api["configured"] is True
    slots = as_dict(api["slots"])
    assert slots["used"] == 1 and slots["total"] == 10
    item = next(i for i in as_dicts(api["items"]) if i["alias"] == "demo")
    assert item["status"] in ("fresh", "stale", "dead", "never", "no-token")
    assert item["token_present"] is True
    routed = {str(a["ledger_account"]) for a in as_dicts(item["accounts"])}
    assert routed == {"Assets:US:Chase:Checking4321", "Liabilities:US:Chase:Card5678"}


# --------------------------------------------------- v2: the upload flow
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_upload_plan_and_confirm_end_to_end(client: TestClient) -> None:
    """Drop a fixture OFX: plan (dry importer run shown) → confirm (filed,
    imported through the gated writer, bean-check green, feed sees it)."""
    payload = (FIXTURES / "upload.checking4321.qfx").read_bytes()
    r = client.post("/api/actions/upload", headers=_auth(),
                    files={"file": ("../../evil-name.qfx", payload,
                                    "application/octet-stream")})
    assert r.status_code == 200, r.text
    plan = _body(r)
    assert plan["recognized"] is True
    assert "Chase" in str(plan["files_to"]) and ".." not in str(plan["files_to"])
    assert "BLUE HERON BOOKS" in str(plan["report"])       # the dry-run report
    assert not (VAULT.parent / "evil-name.qfx").exists()   # name discarded
    assert not (VAULT / "evil-name.qfx").exists()
    staged = list((VAULT / "inbox").glob("upload-*.qfx"))
    assert len(staged) == 1                                # server-named stage

    r = client.post("/api/actions/upload-confirm", headers=_auth(),
                    json={"upload_id": str(plan["upload_id"])})
    assert r.status_code == 200
    stream = r.text
    assert "✓ imported and verified" in stream, stream
    check = subprocess.run([str(SOURCE / ".venv" / "bin" / "bean-check"),
                            str(VAULT / "ledger" / "main.beancount")],
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    ledger = "".join(f.read_text() for f in (VAULT / "ledger").glob("*.beancount"))
    assert "BLUE HERON BOOKS" in ledger and "UPLD-0001" in ledger
    # the confirm regenerated the read model — the feed can find the row
    api = _body(client.get("/api/activity?q=blue+heron&limit=10"))
    assert int(str(api["matched"])) >= 1
    # a second confirm of the same plan is refused
    r = client.post("/api/actions/upload-confirm", headers=_auth(),
                    json={"upload_id": str(plan["upload_id"])})
    assert "unknown or already-applied" in r.text


def test_upload_refusals(client: TestClient) -> None:
    r = client.post("/api/actions/upload", headers=_auth(),
                    files={"file": ("evil.sh", b"#!/bin/sh\nrm -rf /\n",
                                    "text/plain")})
    assert r.status_code == 422                       # extension not accepted
    r = client.post("/api/actions/upload", headers=_auth(),
                    files={"file": ("fake.csv", b"%PDF-1.4 not a csv",
                                    "text/csv")})
    assert r.status_code == 422                       # content sniff disagrees
    r = client.post("/api/actions/upload",
                    files={"file": ("x.qfx", b"OFXHEADER:100", "text/plain")})
    assert r.status_code == 403                       # no token, no upload


def test_plaid_disable_comments_config_out(client: TestClient) -> None:
    r = client.post("/api/actions/plaid-disable", headers=_auth(),
                    json={"item": "demo"})
    assert r.status_code == 200, r.text
    assert _body(r)["disabled"] is True
    rules_text = (VAULT / "rules.toml").read_text()
    assert "# disabled in Sara App" in rules_text
    assert 'PLAID_DEMO_ACCESS_TOKEN' in (VAULT / ".secrets" / "plaid.env").read_text()
    api = _body(client.get("/api/connections"))
    assert all(i["alias"] != "demo" for i in as_dicts(api["items"]))
    assert as_dict(api["slots"])["used"] == 1          # the slot is preserved
    r = client.post("/api/actions/plaid-sync", headers=_auth(),
                    json={"item": "demo"})
    assert r.status_code == 422                        # disabled item refuses
