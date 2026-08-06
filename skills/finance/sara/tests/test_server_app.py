"""Sara App server contract tests — run against a REAL built vault.

Set FINANCE_TEST_VAULT to any initialized vault directory (init_vault.sh
--demo makes a rich one). The suite copies it to a temp dir, points the
server there, and drives the real FastAPI app with TestClient:

  * every GET endpoint answers 200
  * eight-plus dollar figures match tools/run query.py to the dollar
  * categorize posts a rule into rules.toml, recategorize rewrites the
    planted postings, and bean-check still passes
  * set-goal edits facts/goals and survives a re-read; dismiss silences a
    finding everywhere and undoes cleanly

Without FINANCE_TEST_VAULT the whole module skips (same convention as the
FINANCE_TEST_VENV-gated importer paths).
"""
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

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sara.typed import as_dict, as_dicts, as_list  # noqa: E402

GET_ENDPOINTS = ["ping", "glance", "activity", "spend", "networth",
                 "investments", "goals", "autopilot", "findings", "freshness"]
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
    assert api["uncategorized_total"] == int(m.group(1))                # count
    assert int(str(api["uncategorized_total"])) >= 2  # the planted pair shows


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
