"""The FastAPI app: the CQRS read surface, the gated write doors, one page.

READS never parse the ledger. Snapshot rooms (glance/spend/networth/
investments/goals/autopilot) come from summary.json's ``app`` section with
live file-backed overlays (live.py); exploratory endpoints (activity,
search, register, insights, drills, owners, map-by-owner) run parameterized
SQL against reports/analytics.duckdb (dbq.py). Both artifacts hot-reload on
mtime, so a background regeneration lands without a restart.

WRITES are a whitelist behind the per-launch token (security.py): the three
original actions, plus the Connections doors (sync/repair/disable) and the
two-step upload flow — every one a thin door onto existing gated machinery.
"""
# Route handlers are registered (hence used) by their decorators, which
# pyright cannot see:
# pyright: reportUnusedFunction=false
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import TOOLS_DIR, connections, dbq, live, regen, security, spendlens, uploads
from .actions import ActionError, categorize, dismiss, set_goal
from .readmodel import SUMMARY, ReadModelMissing, contribution_limits

STATIC_DIR = Path(__file__).resolve().parent / "static"
REFERENCES_DIR = TOOLS_DIR.parent / "references"


class CategorizeBody(BaseModel):
    payee_pattern: str = Field(min_length=1, max_length=200)
    account: str = Field(min_length=1, max_length=200)
    apply_history: bool = False


class SetGoalBody(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: float | bool


class DismissBody(BaseModel):
    finding_id: str = Field(min_length=12, max_length=12)
    until: str | None = Field(default=None, max_length=10)
    title: str = Field(default="", max_length=300)


class ItemBody(BaseModel):
    item: str = Field(min_length=1, max_length=32)


class ConfirmBody(BaseModel):
    upload_id: str = Field(min_length=12, max_length=12)


def create_app(port: int = 8787) -> FastAPI:
    app = FastAPI(title="Sara App", docs_url=None, redoc_url=None,
                  openapi_url=None)
    security.install(app, port)

    # ---- snapshot rooms (summary.json + live file overlays) --------------
    @app.get("/api/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/glance")
    def glance() -> JSONResponse:
        return _read(lambda: live.patch_glance(SUMMARY.app("glance")))

    @app.get("/api/spend")
    def spend(owner: str | None = None) -> JSONResponse:
        lens = owner if owner and owner != "all" else None
        if lens is None:  # the household view IS the snapshot, verbatim
            return _read(lambda: SUMMARY.app("spend"))

        def build() -> dict[str, object]:
            out = spendlens.build(lens)
            # Sara's wins are parsed from dated notes — household by nature,
            # so the lens carries them through (the frontend badges them)
            out["wins"] = SUMMARY.app("spend").get("wins")
            return out
        return _read(build)

    @app.get("/api/networth")
    def networth() -> JSONResponse:
        return _read(lambda: SUMMARY.app("networth"))

    @app.get("/api/goals")
    def goals() -> JSONResponse:
        def build() -> dict[str, object]:
            out = dict(SUMMARY.app("goals"))
            out.update(live.goals_live(SUMMARY.data()))
            return out
        return _read(build)

    @app.get("/api/autopilot")
    def autopilot() -> JSONResponse:
        def build() -> dict[str, object]:
            snapshot = SUMMARY.app("autopilot")
            out = live.autopilot_live(date.today())
            out["machine"] = snapshot.get("machine")
            uncat = dbq.uncat_counts()
            n = int(str(uncat["count"]))
            out["review"] = {**uncat,
                             "note": ("teach a rule from the Activity room"
                                      if n else "review queue is clear")}
            return out
        return _read(build)

    @app.get("/api/findings")
    def findings() -> JSONResponse:
        def build() -> dict[str, object]:
            out = live.findings_live(date.today())
            out["review"] = dbq.uncat_counts()
            return out
        return _read(build)

    @app.get("/api/investments")
    def investments(owner: str | None = None) -> JSONResponse:
        def build() -> dict[str, object]:
            out = dict(SUMMARY.app("investments"))
            today = date.today()
            lens = owner if owner and owner != "all" else None
            if lens:
                out["positions"] = dbq.positions(lens)
                out["owner"] = lens
            out["lots"] = dbq.lots(today, lens)
            out["dividends_timeline"] = dbq.dividends_timeline(lens)
            limits = contribution_limits(REFERENCES_DIR)
            pace = dbq.contribution_pace(today.year, lens)
            if limits:
                for row in pace:
                    cap = limits.limits.get(str(row["key"]))
                    if cap is not None:
                        done = float(str(row["contributedN"]))
                        row["limit"] = dbq.money0(cap)
                        row["limit_year"] = limits.year
                        row["pct"] = round(min(100.0, 100.0 * done / cap), 1)
                        row["room"] = dbq.money0(max(0.0, cap - done))
            out["contribution_pace"] = {
                "year": today.year,
                "rows": pace,
                "source": limits.source if limits else None,
                "note": ("new money in this year vs the IRS limit — rollovers, "
                         "reinvested dividends, and opening seeds never count"),
            }
            return out
        return _read(build)

    # ---- exploratory reads (analytics.duckdb) ----------------------------
    @app.get("/api/activity")
    def activity(q: str | None = None, amount_min: float | None = None,
                 amount_max: float | None = None, category: str | None = None,
                 account: str | None = None, owner: str | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 uncategorized_only: bool = False, cursor: str | None = None,
                 limit: int = 60) -> JSONResponse:
        def build() -> dict[str, object]:
            filters = dbq.ActivityFilters(
                q=q, amount_min=amount_min, amount_max=amount_max,
                category=category, account=account, owner=owner,
                date_from=_iso_or_none(date_from), date_to=_iso_or_none(date_to),
                uncategorized_only=uncategorized_only)
            out = dbq.activity_page(filters, cursor, limit)
            if not cursor:
                out["categories"] = dbq.activity_categories()
                out["uncategorized"] = dbq.uncat_counts()
                out["owners"] = dbq.owners()
            return out
        return _read(build)

    @app.get("/api/register")
    def register(account: str, cursor: str | None = None,
                 owner: str | None = None) -> JSONResponse:
        if not account.startswith(("Assets:", "Liabilities:", "Income:", "Expenses:", "Equity:")):
            raise HTTPException(status_code=422, detail="not a ledger account")
        return _read(lambda: dbq.register(account, cursor, owner))

    @app.get("/api/accounts")
    def accounts(owner: str | None = None) -> JSONResponse:
        return _read(lambda: {"accounts": dbq.account_list(owner)})

    @app.get("/api/owners")
    def owners() -> JSONResponse:
        return _read(lambda: {"owners": dbq.owners(),
                              "slices": dbq.owner_slices()})

    @app.get("/api/search")
    def search(q: str) -> JSONResponse:
        needle = q.strip()
        if not needle:
            return JSONResponse({"accounts": [], "txns": []})
        def build() -> dict[str, object]:
            accts = [a for a in dbq.account_list(None)
                     if needle.lower() in str(a["account"]).lower()][:6]
            return {"accounts": accts, "txns": dbq.txn_search(needle, 8)}
        return _read(build)

    @app.get("/api/insights")
    def insights(owner: str | None = None) -> JSONResponse:
        return _read(lambda: dbq.insights(owner))

    @app.get("/api/spend/drill")
    def spend_drill(category: str, month: str,
                    owner: str | None = None) -> JSONResponse:
        if not _MONTH_OK(month):
            raise HTTPException(status_code=422, detail="month must be YYYY-MM")
        return _read(lambda: dbq.spend_drill(category, month, owner))

    @app.get("/api/map")
    def money_map(owner: str) -> JSONResponse:
        if not owner or owner == "all":
            raise HTTPException(status_code=422,
                                detail="the all-owners map rides /api/networth")
        return _read(lambda: dbq.map_tree(owner))

    @app.get("/api/connections")
    def connections_read() -> JSONResponse:
        return _read(lambda: connections.payload())

    @app.get("/api/freshness")
    def freshness() -> JSONResponse:
        def build() -> dict[str, object]:
            data = SUMMARY.data()
            build_info = dbq.DB.one(
                "SELECT built_at, txn_count, posting_count, max_date "
                "FROM build_info LIMIT 1") or {}
            return {
                "summary_generated_at": data.get("generated_at"),
                "ledger_through": data.get("ledger_through"),
                "db_built_at": str(build_info.get("built_at") or ""),
                "db_txns": int(float(str(build_info.get("txn_count") or 0))),
                "regen": regen.status(),
            }
        return _read(build)

    # ---- writes: the whitelist ------------------------------------------
    @app.post("/api/actions/categorize")
    def act_categorize(body: CategorizeBody) -> JSONResponse:
        result = _act(lambda: categorize(
            body.payee_pattern, body.account, body.apply_history))
        # the ledger changed: refresh the snapshot + DB in the background
        result["regenerating"] = regen.kick()
        return JSONResponse(result)

    @app.post("/api/actions/set-goal")
    def act_set_goal(body: SetGoalBody) -> JSONResponse:
        return JSONResponse(_act(lambda: set_goal(body.key, body.value)))

    @app.post("/api/actions/dismiss")
    def act_dismiss(body: DismissBody) -> JSONResponse:
        return JSONResponse(_act(lambda: dismiss(
            body.finding_id, body.until, body.title)))

    @app.post("/api/actions/plaid-sync")
    def act_plaid_sync(body: ItemBody) -> StreamingResponse:
        alias = _validated_alias(body.item)
        return StreamingResponse(connections.sync_stream(alias),
                                 media_type="text/plain; charset=utf-8")

    @app.post("/api/actions/link-update")
    def act_link_update(body: ItemBody) -> JSONResponse:
        return JSONResponse(_act(lambda: connections.link_update_token(body.item)))

    @app.post("/api/actions/plaid-disable")
    def act_plaid_disable(body: ItemBody) -> JSONResponse:
        return JSONResponse(_act(lambda: connections.disable(body.item)))

    @app.post("/api/actions/upload")
    async def act_upload(file: Annotated[UploadFile, File()]) -> JSONResponse:
        data = await file.read(uploads.MAX_BYTES + 1)
        try:
            return JSONResponse(uploads.plan(data, file.filename or ""))
        except ActionError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

    @app.post("/api/actions/upload-confirm")
    def act_upload_confirm(body: ConfirmBody) -> StreamingResponse:
        return StreamingResponse(uploads.confirm_stream(body.upload_id),
                                 media_type="text/plain; charset=utf-8")

    # ---- the page --------------------------------------------------------
    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        return _index_response()

    @app.get("/{name}", include_in_schema=False, response_model=None)
    def root_file(name: str) -> Response:
        f = _root_files().get(name)
        if f is not None:
            return FileResponse(str(f))
        if name.startswith("api"):
            raise HTTPException(status_code=404)
        return _index_response()  # SPA fallback (hash routes handle rooms)

    return app


def _MONTH_OK(month: str) -> bool:
    return (len(month) == 7 and month[4] == "-"
            and month[:4].isdigit() and month[5:].isdigit())


def _iso_or_none(v: str | None) -> str | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError as e:
        raise HTTPException(status_code=422,
                            detail="dates must be YYYY-MM-DD") from e


def _validated_alias(alias: str) -> str:
    try:
        connections.require_alias(alias)
    except ActionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return alias


def _read(fn: Callable[[], dict[str, object]]) -> JSONResponse:
    try:
        return JSONResponse(fn())
    except ReadModelMissing as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def _act(fn: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return fn()
    except ActionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


_root_cache: dict[str, Path] | None = None


def _root_files() -> dict[str, Path]:
    global _root_cache
    if _root_cache is None:
        _root_cache = ({p.name: p for p in STATIC_DIR.iterdir()
                        if p.is_file() and p.name != "index.html"}
                       if STATIC_DIR.is_dir() else {})
    return _root_cache


def _index_response() -> HTMLResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return HTMLResponse(
            "<h1>Sara App</h1><p>No built frontend found in sara/server/static/. "
            "Build it once from app/: <code>npm install && npm run build</code> "
            "(contributors only — releases ship it prebuilt).</p>",
            status_code=503)
    html = index.read_text()
    tag = f'<meta name="sara-token" content="{security.TOKEN}">'
    html = html.replace("</head>", tag + "</head>", 1) \
        if "</head>" in html else tag + html
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
