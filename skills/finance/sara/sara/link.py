"""Link an institution to Plaid, locally — one command, one browser tab.

Usage:
  python -m sara.link <alias> [--products transactions,investments]
  python -m sara.link --repair <alias>        # fix a broken connection (FREE)
  python -m sara.link --print-only [out.html] # render the page, no network
  (aliases are yours: ally, chase, vanguard, ...)

What happens: a link_token is minted with YOUR keys (from
$VAULT/.secrets/plaid.env), a page opens on 127.0.0.1 and hands off to
Plaid's own Link window, and when the institution approves, the one-time
public_token comes back to this process and is exchanged server-side for
the long-lived access token — written straight into plaid.env (0600),
never printed, never leaving this machine. The command finishes by
printing the exact rules.toml block for the new item, accounts discovered
and ready to route.

THE 10-SLOT RULE (loud on purpose): Plaid's free Trial plan allows 10
institution links ("Items") FOR LIFE — removing an Item does NOT refund
its slot. So: one Item per institution, and when a connection breaks,
ALWAYS `--repair <alias>` (Link's update mode on the existing Item, costs
nothing) instead of linking fresh. This tool refuses to be the reason a
slot burns silently.

Local security posture: the server binds 127.0.0.1 only, accepts a single
exchange on a random per-run path, checks the Host header, and shuts down
after one result or 10 minutes. The public_token it receives is useless
without your client secret, which never leaves the process.

OAuth institutions (Chase, Vanguard): register
`http://localhost:8484/` as an Allowed redirect URI once at
dashboard.plaid.com -> Developers -> API, and set
PLAID_REDIRECT_URI=http://localhost:8484/ in plaid.env; the page resumes
Link automatically when the bank bounces back. Full walkthrough:
references/fetching.md.
"""

from __future__ import annotations

import json
import re
import secrets
import string
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sara.cli.shared import err
from sara.plaid_api import (
    PlaidCreds,
    api_error_summary,
    create_link_token,
    exchange_public_token,
    get_accounts,
    make_client,
)
from sara.vault import PLAID_ENV_FILE, VAULT, load_env_file, require_vault, write_secret_file

PORT = 8484  # fixed so the OAuth redirect URI can be registered once
LIFETIME_ITEMS = 10
WAIT_SECONDS = 600
DEFAULT_PRODUCTS = ["transactions"]

PAGE = string.Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Sara · link $alias</title>
<style>
  :root{
    --bg:#0a0e1e; --surface:#151d33; --line:rgba(233,235,250,.08);
    --ink:#e9ebfa; --ink-2:#b3b8d6; --muted:#8b91b2;
    --accent:#8f88ff; --pos:#31c48d;
    --aurora:linear-gradient(115deg,#5449ec 0%,#3f86d6 35%,#e0559a 68%,#e9953f 100%);
  }
  *{box-sizing:border-box;margin:0}
  html,body{height:100%}
  body{
    background:var(--bg); color:var(--ink);
    font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
    display:grid; grid-template-columns:minmax(0, 30rem);
    align-content:center; justify-content:center; padding:24px;
  }
  body::before{
    content:""; position:fixed; inset:-40% -20% auto; height:70%;
    background:var(--aurora); opacity:.16; filter:blur(80px);
    pointer-events:none;
  }
  main{
    position:relative; width:100%;
    background:var(--surface); border:1px solid var(--line);
    border-radius:20px; padding:2.2rem 2.1rem 1.9rem;
    box-shadow:0 24px 70px rgba(3,6,20,.55);
  }
  main::before{
    content:""; position:absolute; inset:0 0 auto; height:4px;
    border-radius:20px 20px 0 0; background:var(--aurora);
  }
  .who{display:flex; align-items:center; gap:.65rem; margin-bottom:1.4rem}
  .dot{
    width:2.15rem; height:2.15rem; border-radius:50%;
    background:var(--aurora); display:grid; place-items:center;
    font-weight:700; font-size:.95rem; color:#fff; letter-spacing:.02em;
  }
  .who small{color:var(--muted); font-size:.8rem}
  h1{font-size:1.6rem; line-height:1.2; letter-spacing:-.015em; text-wrap:balance}
  h1 em{font-style:normal; color:var(--accent)}
  .say{color:var(--ink-2); margin:.7rem 0 1.5rem; max-width:44ch}
  button{
    width:100%; border:0; border-radius:12px; cursor:pointer;
    padding:.95rem 1.2rem; font:600 1.02rem/1 inherit; color:#fff;
    background:var(--aurora); background-size:150% 100%;
    transition:transform .18s cubic-bezier(.22,1,.36,1), box-shadow .18s ease, background-position .35s ease;
    box-shadow:0 10px 26px rgba(84,73,236,.35);
  }
  button:hover{transform:translateY(-1px); background-position:60% 0; box-shadow:0 14px 32px rgba(84,73,236,.45)}
  button:active{transform:translateY(0)}
  button:disabled{opacity:.55; cursor:default; transform:none}
  #status{
    margin-top:1.05rem; min-height:1.5rem; font-size:.92rem; color:var(--muted);
    display:flex; align-items:center; gap:.5rem;
  }
  #status.ok{color:var(--pos)}
  #status .spin{
    width:.8rem; height:.8rem; border-radius:50%;
    border:2px solid var(--line); border-top-color:var(--accent);
    animation:spin .8s linear infinite;
  }
  @keyframes spin{to{transform:rotate(360deg)}}
  @media (prefers-reduced-motion: reduce){
    #status .spin{animation:none}
    button{transition:none}
  }
  .fine{
    margin-top:1.5rem; padding-top:1.05rem; border-top:1px solid var(--line);
    font-size:.8rem; color:var(--muted);
  }
  .fine p+p{margin-top:.45rem}
  .fine strong{color:var(--ink-2); font-weight:600}
</style>
</head>
<body>
<main>
  <div class="who"><div class="dot">S</div><div><strong>Sara</strong><br><small>$mode</small></div></div>
  <h1>Let's link <em>$alias</em>.</h1>
  <p class="say">Plaid's window does the sensitive part — your password goes
  to them, never to me. I get a read-only feed, and the keys are yours.</p>
  <button id="go">Open the secure Plaid window</button>
  <p id="status"></p>
  <div class="fine">
    <p><strong>Read-only, on your machine.</strong> The access token lands in
    your vault's <code>.secrets/</code> (never in git); every sync runs locally.</p>
    <p><strong>The 10-slot rule.</strong> Plaid's free plan allows 10 institution
    links for life. A broken link gets repaired free (<code>sara.link --repair</code>) —
    it never spends a new slot.</p>
  </div>
</main>
<!-- Plaid requires Link to load from their CDN (evergreen, no pinnable build). -->
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
  const status = document.getElementById("status");
  const go = document.getElementById("go");
  const say = (t, cls) => {
    status.className = cls || "";
    status.replaceChildren();
    if (cls === "wait") {
      const spin = document.createElement("span");
      spin.className = "spin";
      status.append(spin);
    }
    status.append(document.createTextNode(t));
  };
  const finish = (body) =>
    fetch("/exchange/$nonce", { method:"POST",
      headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) })
    .then(() => body.public_token
      ? say("Linked. You can close this tab — Sara takes it from here.", "ok")
      : say("Window closed — nothing linked. Close this tab and re-run when ready.", ""));
  const oauth = new URLSearchParams(window.location.search).has("oauth_state_id");
  const handler = Plaid.create({
    token: "$link_token",
    ...(oauth ? { receivedRedirectUri: window.location.href } : {}),
    onSuccess: (public_token) => finish({ public_token }),
    onExit: (err) => finish({ exit: err ? err.error_code || "exit" : "exit" }),
  });
  go.addEventListener("click", () => { say("Plaid window open — I'm right here.", "wait");
    go.disabled = true; handler.open(); });
  if (oauth) { say("Picking back up where the bank left off…", "wait");
    go.disabled = true; handler.open(); }
</script>
</body>
</html>
""")


def render_page(alias: str, link_token: str, nonce: str, repair: bool) -> str:
    return PAGE.substitute(alias=alias, link_token=link_token, nonce=nonce,
                           mode=("repairing a connection — no slot spent" if repair
                                 else "linking a new institution"))


def token_var(alias: str) -> str:
    clean = "".join(c for c in alias.upper().replace("-", "_") if c.isalnum() or c == "_")
    return f"PLAID_{clean}_ACCESS_TOKEN"


def slots_used(env: dict[str, str]) -> int:
    return sum(1 for k in env if k.startswith("PLAID_") and k.endswith("_ACCESS_TOKEN"))


def upsert_env_var(name: str, value: str) -> None:
    """Set NAME=value in plaid.env, replacing any existing line (0600)."""
    lines = PLAID_ENV_FILE.read_text().splitlines() if PLAID_ENV_FILE.is_file() else []
    lines = [ln for ln in lines if not ln.strip().startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    write_secret_file(PLAID_ENV_FILE, "\n".join(lines) + "\n")


def _suggest_ledger_account(alias: str, acct: dict[str, object]) -> str:
    inst = alias.replace("-", " ").title().replace(" ", "")
    mask = str(acct.get("mask") or "").strip()
    ty = str(acct.get("type") or "")
    subtype = str(acct.get("subtype") or "").replace(" ", "").title() or "Account"
    leaf = f"{subtype}{mask}"
    root = "Liabilities" if ty in ("credit", "loan") else "Assets"
    return f"{root}:US:{inst}:{leaf}"


def print_rules_snippet(alias: str, accounts: list[dict[str, object]], products: list[str]) -> None:
    print("\nPaste into $VAULT/rules.toml (then route each account):\n")
    print(f"[sources.plaid.items.{alias}]")
    print(f'access_token_env = "{token_var(alias)}"')
    print(f'products = [{", ".join(repr(p) for p in products)}]')
    print(f"[sources.plaid.items.{alias}.accounts]")
    for a in accounts:
        label = f"{a.get('name', '')} ...{a.get('mask', '')} ({a.get('subtype', '')})"
        print(f'"{a.get("account_id", "")}" = "{_suggest_ledger_account(alias, a)}"  # {label}')
    print("\nThen:  tools/run ingest.py            (report only — read it)")
    print("       tools/run ingest.py --write    (apply through the gated writer)")


class ExchangeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, page: str, nonce: str) -> None:
        super().__init__(("127.0.0.1", PORT), _Handler)
        self.page = page
        self.nonce = nonce
        self.result: dict[str, str] = {}
        self.done = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    server: ExchangeServer  # type: ignore[assignment]

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in (f"localhost:{PORT}", f"127.0.0.1:{PORT}")

    def do_GET(self) -> None:
        if not self._host_ok():
            self.send_error(403)
            return
        body = self.server.page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._host_ok() or self.path != f"/exchange/{self.server.nonce}":
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload: object = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
        except ValueError:
            payload = {}
        from sara.typed import as_dict

        self.server.result = {k: str(v) for k, v in as_dict(payload).items()}
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.server.done.set()

    def log_message(self, format: str, *args: object) -> None:
        pass  # a link session prints its own story; no access-log noise


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = __doc__ or ""
    if not argv or argv[0] in ("-h", "--help"):
        raise SystemExit(usage)

    if argv[0] == "--print-only":
        out = Path(argv[1]) if len(argv) > 1 else Path("link-preview.html")
        out.write_text(render_page("ally", "link-preview-placeholder", "preview", False))
        print(f"wrote {out} (placeholder token — for design review only)")
        return

    repair = argv[0] == "--repair"
    if repair and len(argv) < 2:
        raise SystemExit(f"--repair needs the item's alias\n\n{usage}")
    alias = argv[1] if repair else argv[0]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", alias):
        raise SystemExit("alias must be short lowercase letters/digits/-/_ "
                         "(it names the token var and the rules.toml block), e.g. ally")
    products = DEFAULT_PRODUCTS
    if "--products" in argv:
        i = argv.index("--products")
        if i + 1 >= len(argv):
            raise SystemExit(f"--products needs a comma-separated list\n\n{usage}")
        products = [p.strip() for p in argv[i + 1].split(",") if p.strip()]

    require_vault()
    env = load_env_file(PLAID_ENV_FILE)
    if not env.get("PLAID_CLIENT_ID") or not env.get("PLAID_SECRET"):
        raise SystemExit(
            f"No Plaid keys yet. Mint your own free Trial keys (10 lifetime links) and put\n"
            f"them in {PLAID_ENV_FILE} (chmod 600):\n\n"
            f"  PLAID_CLIENT_ID=...\n  PLAID_SECRET=...\n  PLAID_ENV=production\n\n"
            f"Signup walkthrough (exact use-case wording included): references/fetching.md")
    creds = PlaidCreds(client_id=env["PLAID_CLIENT_ID"], secret=env["PLAID_SECRET"],
                       environment=env.get("PLAID_ENV", "production"))

    var_name = token_var(alias)
    existing = env.get(var_name, "")
    if repair and not existing:
        raise SystemExit(f"nothing to repair: {var_name} is not in plaid.env — "
                         f"link it first: python -m sara.link {alias}")
    used = slots_used(env)
    if not repair:
        if existing:
            raise SystemExit(
                f"{alias} already has a token ({var_name}). A broken connection is fixed\n"
                f"FREE with:  python -m sara.link --repair {alias}\n"
                f"Re-linking fresh burns one of your {LIFETIME_ITEMS} lifetime slots — if you truly "
                f"want that, remove the {var_name} line from plaid.env first.")
        err("┌─ HEADS UP — Plaid Trial slots are LIFETIME ─────────────────────┐")
        err(f"│ This link will occupy slot {used + 1} of {LIFETIME_ITEMS}. Removing an Item never   │")
        err("│ refunds a slot, and broken connections repair FREE with --repair.│")
        err("└──────────────────────────────────────────────────────────────────┘")

    client = make_client(creds)
    try:
        link_token = create_link_token(
            client, client_name="Call Sara (personal finance vault)",
            user_id=f"sara-{VAULT.name}", products=products,
            redirect_uri=env.get("PLAID_REDIRECT_URI", ""),
            access_token=existing if repair else "")
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"link_token creation failed — {api_error_summary(e)}") from e

    nonce = secrets.token_urlsafe(16)
    server = ExchangeServer(render_page(alias, link_token, nonce, repair), nonce)
    url = f"http://localhost:{PORT}/"
    print(f"Opening {url} — finish the link in your browser.")
    webbrowser.open(url)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if not server.done.wait(WAIT_SECONDS):
            raise SystemExit("timed out waiting for the browser (10 min) — nothing changed; re-run when ready")
    finally:
        server.shutdown()

    result = server.result
    if "public_token" not in result:
        raise SystemExit(f"link did not complete ({result.get('exit', 'closed')}) — nothing changed")
    try:
        access_token, item_id = exchange_public_token(client, result["public_token"])
    except Exception as e:
        raise SystemExit(f"token exchange failed — {api_error_summary(e)}") from e
    if not access_token:
        raise SystemExit("Plaid returned no access token — nothing changed")
    upsert_env_var(var_name, access_token)
    print(f"\n✓ {alias} linked ({'repaired, no slot spent' if repair else f'slot {used + 1} of {LIFETIME_ITEMS}'})"
          f" — token saved to {PLAID_ENV_FILE.name} as {var_name} (item ...{item_id[-4:]})")
    try:
        accounts = get_accounts(client, access_token)
    except Exception as e:
        print(f"(couldn't list accounts yet — {api_error_summary(e)}; run ingest to retry)")
        accounts = []
    if accounts and not repair:
        print_rules_snippet(alias, accounts, products)
    elif repair:
        print("Connection repaired — run:  tools/run ingest.py")


if __name__ == "__main__":
    main()
