"""Live overlays on the snapshot: everything whose inputs are plain files.

The CQRS split (readmodel.py) freezes ledger-derived numbers at report time.
This module recomputes the parts that must move INSTANTLY after an in-app
action or a facts edit — findings + dismissals (reports/*.md|json), goals
(facts/goals), the obligations calendar (dated facts bullets), and the
request-time greeting. All of it is file reads through the same verified
builders the reports use; none of it touches the ledger.

Same declared-boundary posture as assemble.py: the tools are untyped, so the
Unknown-propagation diagnostics are off HERE ONLY.
"""
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false, reportUnknownLambdaType=false
# pyright: reportPrivateUsage=false
import re
from datetime import date, datetime
from typing import cast

from sara.advisor.checks import goals as goals_config
from sara.advisor.dismissals import active_ids, finding_id, load_dismissals
from sara.advisor.home import (
    SMALL_NUMS,
    EduAccount,
    _education_ctx,
    _next_ctx,
    findings_date,
    must_move,
    needs_you,
)
from sara.advisor.snapshot import (
    GOAL_KEYS,
    _card,
    _friendly_date,
    _milestones,
    _text,
    ask_529,
    clean,
    spotlight_tile,
)
from sara.advisor.webview import action_queue, milestone_state, parse_findings
from sara.vault import household


def daypart(now: datetime) -> str:
    # small hours belong to the night owls, not the morning people
    if now.hour < 5:
        return "evening"
    return ("morning" if now.hour < 12
            else "afternoon" if now.hour < 18 else "evening")


# "Three things want a decision this evening." — the count-carrying shape
_COUNT_LINE = re.compile(r"^\w+ things? wants? a decision", re.I)


def _alert_line(n: int, daypart_word: str) -> str:
    """sara_line's alert branch, rebuilt from the LIVE cards so the hero
    count always agrees with the queue it sits above."""
    when = ("tonight" if daypart_word == "night"
            else f"this {daypart_word}")
    n_lbl = SMALL_NUMS[n] if n < 10 else str(n)
    verb = "wants" if n == 1 else "want"
    thing = "thing" if n == 1 else "things"
    return f"{n_lbl} {thing} {verb} a decision {when}."


def patch_glance(snapshot: dict[str, object],
                 now: datetime | None = None) -> dict[str, object]:
    """The snapshot glance with the request-time pieces swapped in: the
    greeting, Sara's line for THIS part of day, the live Next line, and the
    spotlight tile. Hero line, Next, and tile all derive from the same
    needs_you/must_move call — the counts can never disagree."""
    now = now or datetime.now()
    today = now.date()
    out = dict(snapshot)
    dp = daypart(now)
    names = household("names")
    out["greet"] = f"Good {dp}, {names}" if names else f"Good {dp}"
    by_daypart = out.pop("sara_by_daypart", None)
    # Sara's line says "tonight" after ten; older snapshots carry only the
    # three plain dayparts, so the evening line stands in
    sara_dp = "night" if (now.hour >= 22 or now.hour < 5) else dp
    if isinstance(by_daypart, dict):
        line = by_daypart.get(sara_dp) or by_daypart.get(dp)
        if line:
            out["sara"] = line
    cards, more, needs_state = needs_you(today)
    all_moves = must_move(today)
    moves = [mv for mv in all_moves if not mv["plumbing"]]
    n_alerts = sum(1 for c in cards if c.kind == "alert")
    if n_alerts:
        out["sara"] = _alert_line(n_alerts, sara_dp)
    elif _COUNT_LINE.match(str(out.get("sara") or "")):
        # the snapshot counted alerts that have since been handled
        out["sara"] = "Nothing needs a decision right now — I'm keeping watch."
    tiles = out.get("tiles")
    if isinstance(tiles, dict):
        t = cast("dict[str, object]", dict(tiles))
        edu_raw = t.get("spotlight") or t.get("education") or {}
        edu = edu_raw if isinstance(edu_raw, dict) else {}
        t.pop("education", None)
        t["spotlight"] = clean(spotlight_tile(
            cast("dict[str, object]", edu), today))
        out["tiles"] = t
    out["generated_at"] = now.isoformat(timespec="seconds")
    out["next"] = cast("dict[str, object]",
                       clean(_next_ctx(needs_state, cards, more, moves)))
    return out


def autopilot_live(today: date | None = None) -> dict[str, object]:
    """Everything in the Autopilot payload that reads files, computed now:
    findings queue, dismissals, needs-you cards, the obligations calendar."""
    today = today or date.today()
    findings, counts, errors = parse_findings()
    queue = action_queue(findings) if findings else []
    dismissed = load_dismissals()
    silenced = active_ids(today)
    all_moves = must_move(today)
    cards, more, needs_state = needs_you(today)
    return cast("dict[str, object]", clean({
        "needs": {"state": needs_state, "cards": [_card(c) for c in cards],
                  "more": more},
        "queue": [{**f, "id": finding_id(f["check"], f["title"]),
                   "fix": _text(f["fix"])} for f in queue],
        "dismissed": [{"id": fid,
                       "until": entry.get("until"),
                       "title": entry.get("title", ""),
                       "active": fid in silenced}
                      for fid, entry in sorted(dismissed.items())],
        "moves": [mv for mv in all_moves if not mv["plumbing"]],
        "plumbing": [mv for mv in all_moves if mv["plumbing"]],
        "counts": counts,
        "errors": errors,
        "checks_from": _friendly_date(findings_date()),
        "findings_ran": findings is not None,
    }))


def goals_live(summary_data: dict[str, object],
               now: datetime | None = None) -> dict[str, object]:
    """The Goals room's live half: education re-scored against the CURRENT
    facts/goals targets (accounts + pace resurrected from the summary — the
    ledger numbers didn't move, the target might have), settings, and the
    milestone meter."""
    now = now or datetime.now()
    today = now.date()
    goals = goals_config()
    edu_raw = summary_data.get("education_529")
    edu_section = edu_raw if isinstance(edu_raw, dict) else {}
    accounts = [EduAccount(account=str(a.get("account", "")),
                           kid=str(a.get("kid", "")),
                           value=float(a.get("value") or 0.0),
                           at_cost=bool(a.get("at_cost")))
                for a in edu_section.get("accounts", [])
                if isinstance(a, dict)]
    pace_raw = edu_section.get("contribution_pace_monthly")
    pace = float(pace_raw) if isinstance(pace_raw, (int, float)) else None
    education = _education_ctx(accounts, pace, goals, today)
    nw_raw = summary_data.get("networth")
    liquid = float((nw_raw or {}).get("liquid") or 0.0) if isinstance(nw_raw, dict) else 0.0

    def _setting(key: str) -> dict[str, object]:
        raw = goals.get(key)
        return {"key": key, "value": raw if raw is not None else None}

    return cast("dict[str, object]", clean({
        "education": education,
        "ask": ask_529(cast("dict[str, object]", education), goals, today),
        "milestones": _milestones(liquid),
        "settings": [_setting(k) for k in GOAL_KEYS],
    }))


def milestones_live(liquid: float) -> dict[str, object] | None:
    ms = milestone_state(liquid)
    if not ms:
        return None
    return _milestones(liquid)
