"""The FastAPI app: nine read endpoints, three write actions, one page.

Reads assemble the verified builders (assemble.py); writes go through the
gated machinery (actions.py); security.py owns the network posture. The
prebuilt frontend ships inside this package (server/static/) so installing
the skill needs no node — `python -m sara.server` and the app is up.
"""
# Route handlers are registered (hence used) by their decorators, which
# pyright cannot see:
# pyright: reportUnusedFunction=false
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assemble, security
from .actions import ActionError, categorize, dismiss, set_goal

STATIC_DIR = Path(__file__).resolve().parent / "static"


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


def create_app(port: int = 8787) -> FastAPI:
    app = FastAPI(title="Sara App", docs_url=None, redoc_url=None,
                  openapi_url=None)
    security.install(app, port)

    # ---- reads: sync defs so FastAPI runs them in its threadpool ---------
    @app.get("/api/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/glance")
    def glance() -> JSONResponse:
        return JSONResponse(assemble.glance())

    @app.get("/api/activity")
    def activity(month: str | None = None) -> JSONResponse:
        return JSONResponse(assemble.activity(month))

    @app.get("/api/spend")
    def spend() -> JSONResponse:
        return JSONResponse(assemble.spend())

    @app.get("/api/networth")
    def networth() -> JSONResponse:
        return JSONResponse(assemble.networth())

    @app.get("/api/investments")
    def investments() -> JSONResponse:
        return JSONResponse(assemble.investments())

    @app.get("/api/goals")
    def goals() -> JSONResponse:
        return JSONResponse(assemble.goals_payload())

    @app.get("/api/autopilot")
    def autopilot() -> JSONResponse:
        return JSONResponse(assemble.autopilot())

    @app.get("/api/findings")
    def findings() -> JSONResponse:
        return JSONResponse(assemble.findings_payload())

    @app.get("/api/freshness")
    def freshness() -> JSONResponse:
        return JSONResponse(assemble.freshness())

    # ---- writes: the whitelist ------------------------------------------
    @app.post("/api/actions/categorize")
    def act_categorize(body: CategorizeBody) -> JSONResponse:
        return JSONResponse(_act(lambda: categorize(
            body.payee_pattern, body.account, body.apply_history)))

    @app.post("/api/actions/set-goal")
    def act_set_goal(body: SetGoalBody) -> JSONResponse:
        return JSONResponse(_act(lambda: set_goal(body.key, body.value)))

    @app.post("/api/actions/dismiss")
    def act_dismiss(body: DismissBody) -> JSONResponse:
        return JSONResponse(_act(lambda: dismiss(
            body.finding_id, body.until, body.title)))

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
