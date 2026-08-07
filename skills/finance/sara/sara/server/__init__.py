"""Sara App's local server — the read/write JSON surface behind the web app.

Everything here ASSEMBLES the verified builders in sara.advisor into JSON;
it never recomputes a number that layer already owns. One process binds
one vault: set FINANCE_VAULT before importing sara.server.app
(`python -m sara.server` handles the ordering).
"""
from pathlib import Path

# skills/finance — home of references/ (IRS limits, playbook) beside the package
SKILL_DIR = Path(__file__).resolve().parents[3]
