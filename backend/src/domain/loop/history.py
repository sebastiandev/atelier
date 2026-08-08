"""The run's own account of itself, as a stage is told it.

A stage is built fresh from its declared inputs, so without this it knows only
what its declared reports say and has no idea how the run got here -- whether
this is the first attempt or the fourth, what was already tried, what the user
already asked for and got.

Substance is the whole point. ``pass 4 -- review: changes requested (2
findings)`` tells an agent nothing it can act on, and an ambiguous one-liner is
worse than no history at all because it invites guessing. Every line carries
either what a stage actually said or an explicit admission that it said
nothing.
"""

from __future__ import annotations

from typing import Any

from src.domain.loop import actions, feedback
from src.domain.loop.dtos import LoopHistoryLevel

ROLL_UP_AFTER_PASSES = 4
"""Passes rendered in full before older ones collapse to one line each.

A rendering safeguard, not a setting: ``full`` history grows without bound, and
an agent handed forty passes verbatim reads none of them. Deliberately not
configurable -- a knob here would only let a loop reintroduce the problem.
"""

_NO_SUMMARY = "no summary reported"


def lines(
    loop: dict[str, Any],
    level: LoopHistoryLevel,
    rendered_in_full: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Return the run's account at the level a stage declared, oldest first.

    One source per fact: ``rendered_in_full`` names the stages whose latest
    report the prompt already carries as a declared report block, and that
    occurrence is left out here. Earlier occurrences of the same stage stay,
    because the report block only ever shows the newest one.

    Preconditions: ``loop`` is a loop-run snapshot; ``level`` is the stage's
    declared history level.
    Postconditions: empty at :data:`LoopHistoryLevel.NONE` or when nothing has
    happened yet. Otherwise one entry per stage report and per answered
    request, in the order they occurred, with passes older than
    :data:`ROLL_UP_AFTER_PASSES` collapsed to one line each whatever the level.
    """
    if level == LoopHistoryLevel.NONE:
        return ()
    # A dismissed finding is settled. The report ledger keeps it, deliberately,
    # as the record of what the reviewer said -- but replaying it here would
    # put it back in front of an agent as live account of the run, which is
    # the very loop the dismissal exists to break.
    dismissed = frozenset(feedback.dismissed(loop))
    events = sorted(
        (*_stage_events(loop, rendered_in_full, dismissed), *_feedback_events(loop)),
        key=lambda event: (event["pass_number"], event["at"], event["seq"]),
    )
    if not events:
        return ()
    # From the events, not the run: a snapshot with no pass counter would
    # otherwise roll nothing up and hand a long run every pass in full, which
    # is the failure this cap exists to prevent. It has to fail closed.
    current = max(
        max(1, actions.int_or_default(loop.get("pass_number"), 1)),
        max(event["pass_number"] for event in events),
    )
    oldest_rendered = current - ROLL_UP_AFTER_PASSES + 1
    rolled = [event for event in events if event["pass_number"] < oldest_rendered]
    recent = [event for event in events if oldest_rendered <= event["pass_number"] <= current]
    return (
        *_rolled_up(rolled),
        *(_render(event, level) for event in recent),
    )


def _stage_events(
    loop: dict[str, Any],
    rendered_in_full: frozenset[str],
    dismissed: frozenset[str],
) -> list[dict[str, Any]]:
    """Return one event per stage report occurrence in the run."""
    rows = loop.get("stages")
    events: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = actions.str_or_empty(row.get("name")) or actions.str_or_empty(row.get("id"))
        reports = row.get("reports")
        occurrences = [item for item in reports if isinstance(item, dict)] if isinstance(
            reports, list
        ) else []
        if actions.str_or_empty(row.get("id")) in rendered_in_full and occurrences:
            occurrences = occurrences[:-1]
        for report in occurrences:
            events.append(
                {
                    "kind": "stage",
                    "name": name,
                    # Zero means "written before the run recorded passes", not
                    # pass one. Defaulting to 1 told an agent a fourth attempt
                    # was the first and grouped unrelated passes together.
                    "pass_number": max(0, actions.int_or_default(report.get("pass_number"), 0)),
                    # `seq` counts one agent's transcript, so it cannot order
                    # two stages against each other; the timestamp can.
                    "at": actions.str_or_empty(report.get("recorded_at")),
                    "seq": actions.int_or_default(report.get("seq"), 0),
                    "outcome": actions.str_or_empty(report.get("outcome")),
                    "summary": actions.str_or_empty(report.get("summary")).strip(),
                    "findings": [
                        item
                        for item in actions.str_list(report.get("findings"))
                        if item not in dismissed
                    ],
                    "evidence": actions.str_or_empty(report.get("validation_evidence")).strip(),
                }
            )
    return events


def _feedback_events(loop: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one event per answered request.

    Only answered ones. An open request is an instruction and belongs in the
    stage's task, not in its account of the past -- rendering it in both places
    is what let a settled request keep reading as a live order.
    """
    return [
        {
            "kind": "feedback",
            "name": "",
            "pass_number": max(1, entry["pass_number"]),
            # After every stage report of the same pass: a request is answered
            # by work, so it reads last.
            "at": "\uffff",
            "seq": 10**9,
            "note": _one_line(entry["note"]) or _items_gist(entry["items"]),
        }
        for entry in feedback.records(loop)
        if entry["state"] == feedback.ANSWERED
        # An attempt-scoped note was a hint for one relaunch, not something the
        # user asked of the run; reading it back as a standing request that got
        # answered overstates it.
        and entry["scope"] == feedback.SCOPE_OPEN
        # Something to quote, or the line says nothing at all -- which is the
        # ambiguous one-liner this module exists to avoid.
        and (_one_line(entry["note"]) or _items_gist(entry["items"]))
    ]


def _render(event: dict[str, Any], level: LoopHistoryLevel) -> str:
    """Render one event at the requested level."""
    if event["kind"] == "feedback":
        return f"{_when(event)} -- you asked: {event['note']}  (answered)"
    head = f"{_when(event)} -- {event['name']}: "
    if level == LoopHistoryLevel.FULL:
        return head + _full_body(event)
    return head + _summary_body(event)


def _when(event: dict[str, Any]) -> str:
    """Return how an event is dated, without inventing a pass it may not have."""
    number = event["pass_number"]
    return f"pass {number}" if number else "earlier"


def _summary_body(event: dict[str, Any]) -> str:
    """Return the one-line account of one stage report.

    A bare outcome and a count is the thing this replaces, so what a stage
    said and what a review found both survive: dropping the summary in favour
    of the findings loses the reason, and dropping the outcome makes a
    changes-requested report read exactly like a passing one.
    """
    summary = _one_line(event["summary"]) or _NO_SUMMARY
    gist = _gist(event["findings"])
    outcome = event["outcome"].replace("_", " ")
    if outcome and outcome != "pass":
        return f"{outcome} -- {summary}" + (f" ({gist})" if gist else "")
    return summary + (f" ({gist})" if gist else "")


def _full_body(event: dict[str, Any]) -> str:
    """Return the verbatim account of one stage report.

    Carries the outcome as the summary level does: without it the higher level
    was the less informative of the two, which cannot be right.
    """
    outcome = event["outcome"].replace("_", " ")
    summary = event["summary"] or _NO_SUMMARY
    parts = [f"{outcome} -- {summary}" if outcome and outcome != "pass" else summary]
    if event["findings"]:
        parts.extend(f"  - {item}" for item in event["findings"])
    if event["evidence"]:
        parts.append(f"  evidence: {event['evidence']}")
    return "\n".join(parts)


def _rolled_up(events: list[dict[str, Any]]) -> tuple[str, ...]:
    """Collapse each older pass to one line that still says what happened."""
    by_pass: dict[int, list[str]] = {}
    for event in events:
        if event["kind"] == "feedback":
            entry = "you asked, and it was answered"
        else:
            entry = f"{event['name']}: {_outcome_gist(event)}"
        by_pass.setdefault(event["pass_number"], []).append(entry)
    return tuple(
        f"{'pass ' + str(number) if number else 'earlier'} -- " + "; ".join(entries)
        for number, entries in sorted(by_pass.items())
    )


def _outcome_gist(event: dict[str, Any]) -> str:
    """Return the shortest account of one stage report that still says something.

    A rolled-up pass is still meant to be readable, so it keeps the outcome
    and the summary. Collapsing to the bare outcome saved nothing -- at
    ``summaries`` the event was already one line -- and threw away the only
    part an agent could act on.
    """
    outcome = event["outcome"].replace("_", " ") or "no outcome reported"
    summary = _one_line(event["summary"])
    return f"{outcome} -- {summary}" if summary else outcome


def _one_line(value: str) -> str:
    """Collapse text to a single line so one event stays one line."""
    return " ".join(value.split())


def _gist(findings: list[str]) -> str:
    """Return the findings as one readable clause, or nothing to say."""
    return "; ".join(
        gist for item in findings if (gist := _one_line(item).rstrip("."))
    )


def _items_gist(items: list[dict[str, Any]]) -> str:
    """Return what a request's selected items asked for, as one clause."""
    return "; ".join(
        body
        for item in items
        if (body := _one_line(actions.str_or_empty(item.get("body"))).rstrip("."))
    )


__all__ = ["ROLL_UP_AFTER_PASSES", "lines"]
