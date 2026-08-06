"""Sara App's local server — the read/write JSON surface behind the web app.

Everything here ASSEMBLES the existing verified builders (tools/home.py,
webview.py, checks.py, reports.py) into JSON; it never recomputes a number
those modules already own. The tools live beside this package in the repo
(skills/finance/tools/) as flat modules, so importing any sara.server
submodule first puts that directory on sys.path — the same bootstrap the
tools use themselves.

Set FINANCE_VAULT before importing sara.server.app (tools/vault.py resolves
the vault at import time); `python -m sara.server` handles the ordering.
"""
import os
import sys
from pathlib import Path


def _tools_dir() -> Path:
    override = os.environ.get("SARA_TOOLS_DIR")
    if override:
        return Path(override).expanduser()
    # …/skills/finance/sara/sara/server/__init__.py -> …/skills/finance/tools
    return Path(__file__).resolve().parents[3] / "tools"


TOOLS_DIR = _tools_dir()
if not (TOOLS_DIR / "vault.py").is_file():
    raise ImportError(
        f"finance tools not found at {TOOLS_DIR} — sara.server assembles the "
        f"report builders in skills/finance/tools/ and needs the repo checkout "
        f"(set SARA_TOOLS_DIR if the layout is custom)")
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
