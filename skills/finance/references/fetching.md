# Fetching mode — pulling documents from institutions

Use when the task is 'grab my statements / download from X / file my
exports'. The user logs in; Claude drives Chrome. Per-institution site
quirks live in `institutions.md` (shareable); who-logs-into-what lives in the
vault's `facts/household/institutions.md` (private). Read the vault's
`CLAUDE.md` before filing anything — it defines where documents, facts,
and transactions go, and `rules.toml` decides categorization.

## Two firm boundaries

**The user logs in; Claude never types a password, MFA code, or SSN.** These
are the user's live financial accounts and the tools log everything typed. If a
login or password screen appears, hand back: "log in and tell me when
you're on the dashboard." This isn't caution theater — a leaked credential
here is unrecoverable damage.

**Never click a control that changes account state** — Exercise, Sell, Trade,
Transfer, Withdraw, Submit-payment. These portals put "Exercise" buttons an
inch from "View." The job is read-only: navigate, read, screenshot,
view/download/close documents. If a wrong click could move money, don't
click it at all; describe it to the user instead.

Downloading files needs the user's OK **once per session** — ask before the
first download (a blanket "grab everything" counts for the rest).

## The workflow

**1. Get in.** Load claude-in-chrome tools in ONE ToolSearch call
(`tabs_context_mcp, navigate, computer, read_page, get_page_text, find,
browser_batch`) — one round trip, not seven. Create a tab, navigate to the
dashboard. If it redirects to a login, hand off. Check
`references/institutions.md` for the real app URL (marketing pages
frequently sit in front of it).

**2. Read cheaply before you screenshot.** `get_page_text` for text pages,
`read_page` for the accessibility tree — both are near-free. Financial
portals love embedding the important table inside a widget the a11y tree
can't see; when the tree comes back empty, switch to screenshots and read
the pixels. Chain steps with `browser_batch` (navigate → wait → screenshot)
to avoid round trips.

**3. Bank the on-screen numbers first.** Valuations, balances, vesting
schedules shown on-screen go into `facts/` immediately (`verified: <today>`,
`source:` the live page) before any downloads. If the session dies
mid-download, the summary is already saved. The screen is a source.

**4. Download one document at a time, verifying each.**
- Open the doc's viewer/modal → **screenshot and confirm the title** before
  clicking download. This step exists because a Shareworks pull downloaded
  the same file twice: I clicked "NQSO" while the modal still showed the
  previous "ISO." The title check catches that.
- Click download → wait ~3s → close the viewer.
- Confirm a NEW file actually landed: `ls -t ~/Downloads | head`.
- Layouts differ per document type — re-screenshot each time rather than
  reusing coordinates. Coordinates map to the *screenshot's* space, and the
  viewport can resize mid-session, so trust fresh captures over memory.

**5. Identify, dedupe, rename, file — use the bundled script.**
`scripts/file_downloads.py` (in this skill dir) does the repetitive part:
- `inspect` — fingerprints every PDF by content (grant/account/period
  fields), so files are identified by what's inside, not their names.
- `dedupe` — deletes byte-identical duplicates (`(1)`-suffixed re-downloads).
- `move SRC DEST_DIR YYYY-MM-DD.name` — renames and files it. The date
  prefix isn't cosmetic: `documents/` sorts chronologically by filename and
  Beancount's document auto-discovery keys on the leading date.
Destination mirrors the ledger account:
`documents/<Assets|Liabilities>/US/<Institution>/<Owner>/`. Superseded docs
go to an `archive/` subfolder, never deleted.

**6. Close the loop.** Fold what the documents settle back into the
`facts/` file (turn open questions into cited answers). Run `bean-check` if
the ledger changed. Clean `~/Downloads`. `git commit`. Report in one or two
lines what was filed and anything ambiguous left for the user.

Then **update `references/institutions.md`** with anything learned about
this institution — that's how the next pull becomes one-shot.

## When the extension is blocked on a domain

Some domains (Google admin console, other admin surfaces) refuse the
extension outright — screenshot and page-read both return "Permission
denied for this action on this domain," and it can't be overridden. Real
fallback: `screencapture -x file.png` reads the actual screen (Screen
Recording permission), and `cliclick c:x,y` clicks (Accessibility
permission). Points on a retina display ≈ the downscaled screenshot's
pixels; verify with a fresh capture after every click.

The failure mode is **focus theft**: iTerm, Slack, and other windows jump in
front and eat the click. Before clicking, hide the offenders
(`osascript … set visible of process "Slack" to false`), focus the exact
tab by matching its URL via AppleScript, and screenshot to confirm the
target is frontmost. If it fights back twice, stop — hand the one click to
the user rather than burning ten minutes losing to window management.

## Done means
Downloads clean; docs renamed and filed to the mirrored path; facts updated
with cited sources; ledger validates if touched; committed; and
`references/institutions.md` carries whatever this pull taught us.
