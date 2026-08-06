"""The app's network posture, in one place.

Binding to 127.0.0.1 keeps remote hosts out, but a browser on this machine
will happily carry requests FOR a remote page — so two checks close the two
browser holes:

- Host validation (every request): a DNS-rebinding page reaches this port
  with its own hostname in Host; only 127.0.0.1/localhost/[::1] pass. This
  is what makes the read surface private, not a promise.
- Write token (every /api/actions/* POST): a per-launch random token rides
  the served index.html and must come back in an X-Sara-Token header.
  Cross-site HTML forms cannot set custom headers, and a cross-origin fetch
  carrying one triggers a CORS preflight this server never approves (no
  CORS headers are ever emitted). An Origin header, when present, must also
  be exactly our own origin.

SARA_DEV_ORIGIN (dev only) lets the Vite dev server through: that exact
origin skips the token and receives CORS headers so `npm run dev` can talk
to a running backend. Never set it outside development.
"""
import os
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

TOKEN: str = secrets.token_urlsafe(32)
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def dev_origin() -> str | None:
    return os.environ.get("SARA_DEV_ORIGIN") or None


def allowed_origins(port: int) -> set[str]:
    return ({f"http://{h}:{port}" for h in _LOCAL_HOSTS}
            | {f"http://{h}" for h in _LOCAL_HOSTS})


def host_ok(host_header: str | None) -> bool:
    """True only for a loopback Host, with or without a numeric port."""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):                     # [::1] or [::1]:8787
        name, bracket, rest = host.partition("]")
        if not bracket:
            return False
        name += "]"
    else:
        name, colon, port = host.partition(":")
        rest = f":{port}" if colon else ""
    if name not in _LOCAL_HOSTS:
        return False
    return rest == "" or (rest.startswith(":") and rest[1:].isdigit())


def install(app: FastAPI, port: int) -> None:
    """Wire the middleware: Host check on everything, token + Origin on writes."""
    own_origins = allowed_origins(port)
    dev = dev_origin()

    @app.middleware("http")
    async def _guard(request: Request,  # pyright: ignore[reportUnusedFunction] — registered by the decorator
                     call_next: Callable[[Request], Awaitable[Response]],
                     ) -> Response:
        if not host_ok(request.headers.get("host")):
            return Response("bad Host header", status_code=403)
        origin = request.headers.get("origin")
        from_dev = dev is not None and origin == dev
        if from_dev and request.method == "OPTIONS":
            return Response(status_code=204, headers={
                "Access-Control-Allow-Origin": dev or "",
                "Access-Control-Allow-Headers": "content-type, x-sara-token",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Vary": "Origin"})
        if request.url.path.startswith("/api/actions/") and not from_dev:
            if origin is not None and origin not in own_origins:
                return Response("bad Origin", status_code=403)
            if request.method == "POST":
                token = request.headers.get("x-sara-token") or ""
                if not secrets.compare_digest(token, TOKEN):
                    return Response("missing or stale token — reload the app",
                                    status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if from_dev:
            response.headers["Access-Control-Allow-Origin"] = dev or ""
            response.headers["Access-Control-Allow-Headers"] = (
                "content-type, x-sara-token")
            response.headers["Vary"] = "Origin"
        return response
