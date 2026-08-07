# Repo invariants — the sync chains

Every surface below is mirrored somewhere. A change that touches one
end of a chain lands the other end in the same commit — that's the
whole rule.

- **New tool or script** → its one-line trigger in
  `skills/finance/SKILL.md` (References & tools) → a health row in
  `skills/finance/scripts/doctor.sh` → any new dependency folded into
  `install.sh` (install.sh does everything; the README never grows
  setup steps).
- **Reference added / renamed** → the pointers in
  `skills/finance/SKILL.md` (mode table + inventory) still resolve.
- **Onboarding layer added / changed** → its block in
  `skills/finance/references/onboarding.md` (checklist template
  included) → doctor's layers panel.
- **App change** (`app/`) → rebuild and commit the prebuilt bundle
  (`cd app && npm run build` →
  `skills/finance/sara/sara/server/static/`); users never run node.
- **MCP change** (`integrations/cloudflare-mcp/`) → the same change in
  the private twin worker repo → the tool routing in
  `skills/sara-lite/SKILL.md` → both READMEs. `tsc --noEmit` in both.
- **Rejected concept** → one file in `.out-of-scope/` (decision ·
  reasoning · escape hatch); read that directory before pitching.
- **Shipped work** → a dated paragraph in `CHANGELOG.md`, newest
  first — the failure it kills, not the diff it made.

Voice: anything user-facing passes the string gate in
`skills/finance/tools/checks.py` (the style addendum lives in its
docstring). Public writing never pitches.
- Queue rows carry `fix` (civilian) and optionally `how` (operator command). A surface that renders `how` must make it look like operator territory — mono, folded, labeled — never inline beside `fix`.
