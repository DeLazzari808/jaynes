"""A stop's count, made of what happened to what it let through, not of the answers alone.

A stop lets an output through when it is accepted, chosen or skipped. That release counts as right only once its
run delivered and no correction reached it for a while (``SETTLE``, 7 days); a correction of kind error turns it
into an error, even after that. An error found inside the run, a change at the stop or a return to it, counts at
once. Only the last release of a stop in a run counts, since only it was delivered.

Nobody looked at a release by an agent or at a small change the role let through, so they never earn a right; a
correction still counts against them, so the count errs towards asking.

The brake: ``BRAKE_ERRORS`` errors among a stop's last ``BRAKE_LAST`` outcomes make it ask on every run and hold
the run, until the outcomes after the brake certify it on their own.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from jaynes import bounds
from jaynes.store import Event

SETTLE = 7 * 24 * 3600
BRAKE_ERRORS, BRAKE_LAST = 2, 10
KINDS = ("error", "preference", "info")  # wrong by what was known; a preference nobody knew; the world changed
TYPES = ("StopAnswered", "StopSkipped", "RunSentBack", "RunFinished", "DeliveryCorrected")  # what a count reads


@dataclass
class Item:
    run_id: str
    step: str
    fingerprint: str
    ts: float  # when the stop let it through, or when the error was found inside the run
    state: str  # pending, right, error, none (superseded, dismissed, context, or nobody looked)
    earns: bool = True  # whether it can earn a right
    corrected: str | None = None  # the correction that made it an error


@dataclass(frozen=True)
class Count:
    n: int  # outcomes known
    k: int  # of them errors
    pending: int  # releases still waiting for their delivery to hold
    braked: bool = False
    since: float | None = None  # when the brake started
    after: tuple[int, int] = (0, 0)  # outcomes and errors since the brake


def items(events: Iterable[Event], now: float, settle: float = SETTLE) -> list[Item]:
    """Every outcome of every stop, from the events in ``TYPES``, in log order."""
    out: list[Item] = []
    last: dict[tuple[str, str], Item] = {}  # the latest release of a step in a run
    finished: dict[str, tuple[float, str]] = {}
    corrections: dict[tuple[str, str], dict[str, Any]] = {}  # the latest of each, by run and id

    def release(item: Item) -> None:
        prev = last.get((item.run_id, item.step))
        if prev is not None and prev.state == "pending":
            prev.state = "none"  # it ran again since: only what was delivered counts
        out.append(item)
        last[(item.run_id, item.step)] = item

    def revise(run: str, to: str, r: dict[str, Any], ts: float) -> None:
        item = last.pop((run, to), None)
        if item is not None and item.fingerprint == r["fingerprint"]:
            item.state = "error" if r["label"] == "error" else "none" if r["label"] == "context" else item.state
        elif r["label"] == "error":  # its stop was still open: the error counts there all the same
            out.append(Item(run, to, str(r["fingerprint"]), ts, "error", earns=False))

    for e in events:
        p, run = e.payload, e.run_id or ""
        if e.type == "StopAnswered":
            if p.get("by") == "agent":  # an agent's answer counts nothing: only what came of it
                if p["answer"] in ("accept", "choose", "change") and not p.get("to"):
                    release(Item(run, str(p["step"]), str(p["fingerprint"]), e.ts, "pending", earns=False))
                continue
            if p.get("to"):
                if p.get("revises"):
                    revise(run, str(p["to"]), p["revises"], e.ts)
            elif p["answer"] in ("accept", "choose", "change"):
                release(Item(run, str(p["step"]), str(p["fingerprint"]), e.ts,
                             "error" if p.get("label") == "error" else "pending"))
            elif p["answer"] == "return":
                out.append(Item(run, str(p["step"]), str(p["fingerprint"]), e.ts, "error", earns=False))
        elif e.type == "StopSkipped":
            release(Item(run, str(p["step"]), str(p["fingerprint"]), e.ts, "pending",
                         earns=p.get("status") != "small"))
        elif e.type == "RunSentBack":
            if p.get("revises") and p.get("by") != "agent":
                revise(run, str(p["to"]), p["revises"], e.ts)
        elif e.type == "RunFinished":
            finished[run] = (e.ts, str(p.get("outcome", "delivered")))
        elif e.type == "DeliveryCorrected":
            corrections[(run, str(p["id"]))] = p
    for (run, cid), p in corrections.items():
        hit = last.get((run, str(p.get("stop") or "")))
        if p.get("kind") == "error" and hit is not None:
            hit.state, hit.corrected = "error", cid
    for item in out:
        if item.state != "pending":
            continue
        end = finished.get(item.run_id)
        if end is None:
            continue  # its run is still going
        if end[1] == "dismissed" or not item.earns:
            item.state = "none"
        elif now >= end[0] + settle:
            item.state = "right"
    return out


def count(all_items: Iterable[Item], fingerprint: str, budget: float | None = None, delta: float = 0.05) -> Count:
    """The count of one stop. With its ``budget`` and ``delta``, also whether it is braked."""
    mine = sorted((i for i in all_items if i.fingerprint == fingerprint), key=lambda i: i.ts)
    known = [i for i in mine if i.state in ("right", "error")]
    pending = sum(1 for i in mine if i.state == "pending" and i.earns)
    n, k = len(known), sum(i.state == "error" for i in known)
    if budget is None:
        return Count(n, k, pending)
    braked, since, start = False, None, 0
    for j, it in enumerate(known):
        seen = known[start:j + 1]
        if not braked and sum(x.state == "error" for x in seen[-BRAKE_LAST:]) >= BRAKE_ERRORS:
            braked, since, start = True, it.ts, j + 1
        elif braked and bounds.certified(len(seen), sum(x.state == "error" for x in seen), budget, delta):
            braked, since, start = False, None, j + 1  # the errors before the release no longer brake it
    after = known[start:] if braked else []
    return Count(n, k, pending, braked, since, (len(after), sum(x.state == "error" for x in after)))
