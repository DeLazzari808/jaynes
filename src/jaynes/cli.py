"""``jaynes`` command line.

  jaynes hook                       Claude Code hook (reads the event on stdin)
  jaynes autonomy [--config P]      status, labels and evidence per category, from the log
  jaynes history  [--config P]      classify past tool calls from transcripts; audit load estimate
  jaynes parity HOOK [--config P]   compare floors against an existing deny hook on past Bash calls
  jaynes simulate                   Monte Carlo check of the certification guarantee
  jaynes                            the review page, opened in the browser
  jaynes run [ID] --role R --task T [--project DIR] [--preview CMD] [--suite CMD]
                                    start a run of a built-in role (visual-identity, redesign, fix-bug, build-feature, small-change, screen-design), a role
                                    file (.toml) or module:attribute, on the git repository at DIR
                                    --here: started from an agent's terminal, which asks the stops there
  jaynes run ID                     continue a run from its log (after a crash, a pause or a stop)
  jaynes plan FILE [--project DIR]  a request that needs more than one role: its runs, each starting once the
                                    runs it follows delivered; - reads the plan from stdin
  jaynes check ROLE                 find what a role gets wrong before any agent runs
  jaynes queue                      stops waiting for an answer, blocked and idle runs, then running and paused
  jaynes answer RUN STEP ANSWER     accept, change (--output JSON), return or context (--note); the run continues
                                    recorded as an agent's: it releases the stop but is not a label
  jaynes ask RUN                    the run's open stop as input for Claude Code's question dialog; the person's
                                    answer there is recorded as theirs by jaynes answered, the dialog's hook
  jaynes review [--port 8740]       the review page on 127.0.0.1: queue, stops, runs, roles, log
  jaynes policy                     print the built-in policy, a starting point for ~/.jaynes/jaynes.toml
  jaynes demo [--port 8740]         a scripted run that stops for you, then the review page
  jaynes skill                      add /jaynes to Claude Code: /jaynes <task> runs the task with Jaynes, and
                                    the hook that records answers given in its question dialog
  jaynes guide                      how an agent carries out a change through Jaynes: the skill's steps
  jaynes update                     install the newest Jaynes from where this one came from, if there is one
  jaynes version                    the commit this Jaynes was installed from
"""

from __future__ import annotations

import argparse
import collections
import importlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jaynes import bounds, gate, hook, skill, update, verify
from jaynes.history import past_calls, report
from jaynes.install import BUILT_IN_POLICY
from jaynes.policy import Policy, classify, find_policy, jaynes_home, load_policy
from jaynes.sim import simulate
from jaynes.store import Store

if TYPE_CHECKING:  # the conductor pulls in pydantic; `jaynes hook` runs on every tool call and needs none of it
    from jaynes.conductor import Conductor
    from jaynes.role import Role


def _policy(path: str | None) -> Policy:
    """--config, else jaynes.toml found from cwd, else the built-in policy (these commands only read)."""
    found = Path(path) if path else find_policy(str(Path.cwd()))
    return load_policy(found or BUILT_IN_POLICY)


def cmd_autonomy(args: argparse.Namespace) -> None:
    store = Store(Path(args.db) if args.db else hook.db_path())
    rows: dict[str, dict[str, object]] = {}
    totals: collections.Counter[str] = collections.Counter()
    verdicts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    latest_fp: dict[str, tuple[int, str, str]] = {}
    for e in store.events("GateDecided"):
        cat = str(e.payload.get("category"))
        totals[cat] += 1
        verdicts[cat][str(e.payload.get("verdict"))] += 1
        latest_fp[cat] = (e.seq, str(e.payload.get("fingerprint")), str(e.payload.get("status")))
    policy = _policy(args.config)
    for cat, (_, fp, _) in latest_fp.items():
        w = store.window(fp)
        c = policy.category(cat)
        status = gate.status_of(c, w, policy)
        need = gate.labels_needed(w, policy) if status in ("undetermined", "refuted") else None
        rows[cat] = {"status": status, "calls": totals[cat], "n": w.n, "k": w.k,
                     "auto": verdicts[cat]["allow"], "asked": verdicts[cat]["ask"],
                     "observed": (w.k / w.n) if w.n else None,
                     "cp_upper": bounds.clopper_pearson_upper(w.n, w.k, 1 - policy.delta) if w.n else None,
                     "evidence": gate.evidence(w, policy), "need": need}
    total_calls = sum(totals.values())
    unattended = sum(v["allow"] + v["defer"] for v in verdicts.values())
    print(f"policy {policy.source}  budget {policy.budget:.0%}  delta {policy.delta:.0%}  audit {policy.audit_rate:.0%}")
    print(f"calls {total_calls}  ran without Jaynes asking {unattended} ({unattended / max(total_calls, 1):.1%})\n")
    print(f"{'category':14} {'status':12} {'calls':>6} {'asked':>6} {'labels':>7} {'errors':>6} {'obs':>6} {'evidence':>8} {'to go':>6}")
    for cat, r in sorted(rows.items(), key=lambda kv: -int(kv[1]["calls"])):  # type: ignore[arg-type]
        obs = "" if r["observed"] is None else f"{float(r['observed']):.1%}"  # type: ignore[arg-type]
        need = "" if r["need"] is None else str(r["need"])
        print(f"{cat:14} {str(r['status']):12} {r['calls']:>6} {r['asked']:>6} {r['n']:>7} {r['k']:>6} {obs:>6} "
              f"{float(r['evidence']):>8.0%} {need:>6}")  # type: ignore[arg-type]
    store.close()


def cmd_history(args: argparse.Namespace) -> None:
    policy = _policy(args.config)
    calls = past_calls(days=args.days)
    if args.cwd:
        calls = [c for c in calls if c.cwd.startswith(args.cwd)]
    r = report(policy, calls, args.top)
    if r is None:
        sys.exit("no calls found")
    print(f"{r.calls} calls over {r.weeks:.1f} weeks; {r.answered} answered by a person "
          f"({r.answered / r.weeks:.1f} per week)\n")
    print(f"{'category':14} {'status':10} {'calls':>6} {'/week':>7} {'audits/wk':>9} {'weeks to certify':>16}  approved by today")
    for row in r.rows:
        wk = "" if row.weeks_to_certify is None else f"{row.weeks_to_certify:.1f}"
        approvers = ", ".join(f"{k} {v}" for k, v in row.approvers)
        print(f"{row.category:14} {row.kind:10} {row.calls:>6} {row.per_week:>7.0f} {row.audits_per_week:>9.1f} "
              f"{wk:>16}  {approvers}")
    print(f"\naudit prompts per week at {policy.audit_rate:.0%}: {r.audits_per_week:.0f} "
          f"(weeks to certify assume zero errors: {r.labels_to_certify} labels)")
    unknown = sum(row.calls for row in r.rows if row.category == "unknown")
    print(f"unknown: {unknown} calls ({unknown / r.calls:.1%})")
    for k, v in r.unknown:
        print(f"  {v:5}  {k}")


def cmd_parity(args: argparse.Namespace) -> None:
    policy = _policy(args.config)
    calls = [c for c in past_calls(days=args.days) if c.tool == "Bash"]

    def run_hook(command: str) -> bool:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        r = subprocess.run([args.hook], input=payload, capture_output=True, text=True, timeout=30)
        return r.returncode == 2

    commands = [str(c.tool_input.get("command", "")) for c in calls]
    with ThreadPoolExecutor(max_workers=8) as pool:
        theirs = list(pool.map(run_hook, commands))
    ours = [bool(classify(policy, "Bash", {"command": cmd}, "/").floor) for cmd in commands]
    both = sum(1 for a, b in zip(theirs, ours) if a and b)
    only_hook = [cmd for cmd, a, b in zip(commands, theirs, ours) if a and not b]
    only_jaynes = [cmd for cmd, a, b in zip(commands, theirs, ours) if b and not a]
    print(f"{len(commands)} past Bash calls; hook blocks {sum(theirs)}, Jaynes floors {sum(ours)}, both {both}")
    print(f"blocked by the hook only: {len(only_hook)}")
    for cmd in only_hook[: args.top]:
        print("  ---", cmd[:400].replace("\n", "\n      "))
    print(f"floored by Jaynes only: {len(only_jaynes)}")
    for cmd in only_jaynes[: args.top]:
        print("  ---", cmd[:400].replace("\n", "\n      "))


def cmd_simulate(args: argparse.Namespace) -> None:
    print(f"budget {args.budget:.0%}  delta {args.delta:.0%}  runs {args.runs}  horizon {args.horizon} labels\n")
    print(f"{'true error':>10} {'rule':>8} {'certified':>10} {'median labels':>14}")
    for p in args.p:
        for rule in ("fixed", "anytime"):
            o = simulate(rule, p, budget=args.budget, delta=args.delta, runs=args.runs, horizon=args.horizon, seed=args.seed)
            med = "" if o.median_labels_to_certify is None else f"{o.median_labels_to_certify:.0f}"
            flag = "  <-- breaks the guarantee" if p >= args.budget and o.certified_fraction > args.delta else ""
            print(f"{p:>10.1%} {rule:>8} {o.certified_fraction:>10.1%} {med:>14}{flag}")


DEFAULT_RUNTIMES = {"claude": "jaynes.runtimes:ClaudeRuntime", "codex": "jaynes.runtimes:CodexRuntime"}


def role_ref(name: str) -> str:
    """A built-in role's name, as its module:attribute; anything with a colon is already one, and a role file is
    its absolute path, so a run continued from another folder finds it."""
    from jaynes.roles import BUILT_IN
    if name.endswith(".toml"):
        return str(Path(name).resolve())
    if ":" in name:
        return name
    if name not in BUILT_IN:
        sys.exit(f"no built-in role {name!r}: use one of {', '.join(BUILT_IN)}, a role file (.toml), or "
                 "module:attribute")
    return BUILT_IN[name]


def load_ref(ref: str) -> Any:
    module, _, attr = ref.partition(":")
    if not module or not attr:
        sys.exit(f"expected module:attribute, got {ref!r}")
    if os.getcwd() not in sys.path:  # a role in the current folder, as continue_in_background already allows
        sys.path.insert(0, os.getcwd())
    return getattr(importlib.import_module(module), attr)


def _home(args: argparse.Namespace) -> Path:
    return Path(args.home) if args.home else jaynes_home()


def _started(c: Conductor, run_id: str) -> tuple[Role, str, str, dict[str, str]]:
    """The role, its reference, the task and the runtime overrides a CLI run was started with."""
    first = next(iter(c.store.events("RunStarted", run_id=run_id)), None)
    if first is None:
        sys.exit(f"no run {run_id} in {c.home}")
    if "ref" not in first.payload:
        sys.exit(f"run {run_id} was started from Python; continue it with Conductor.run")
    ref = str(first.payload["ref"])
    return _role(ref), ref, str(first.payload["task"]), dict(first.payload["runtimes"])


def _role(ref: str) -> Role:
    from jaynes.role import Role
    from jaynes.role_file import RoleFileError, load
    try:
        r = load(Path(ref)) if ref.endswith(".toml") else load_ref(ref)
    except RoleFileError as err:
        sys.exit(str(err))
    if not isinstance(r, Role):
        sys.exit(f"{ref} is not a role")
    return r


def cmd_run(args: argparse.Namespace) -> None:
    from jaynes.conductor import Conductor, new_run_id
    c = Conductor(_home(args))
    if args.id and not args.role:
        r, ref, task, extra = _started(c, args.id)
        run_id = args.id
        if problems := verify.verify(r):  # warned, not refused: the run is under way and its role may have changed
            print(verify.report(r, problems), file=sys.stderr)
    else:
        if not args.role or args.task is None:
            sys.exit("a new run needs --role and --task")
        if any("=" not in pair for pair in args.runtime):
            sys.exit("--runtime takes name=module:attribute")
        ref, task, run_id = role_ref(args.role), args.task, args.id or new_run_id()
        extra = dict(pair.split("=", 1) for pair in args.runtime)
        r = _role(ref)
        if problems := verify.verify(r):  # before any agent works for it
            sys.exit(verify.report(r, problems))
    # imported on first use, so a run that never calls an agent needs neither CLI installed
    c.runtimes = {name: (lambda t=target: load_ref(t)()) for name, target in {**DEFAULT_RUNTIMES, **extra}.items()}
    started: dict[str, Any] = {"ref": ref, "runtimes": extra}
    if not args.id or args.role:
        if args.here:
            started["here"] = True
        if args.preview:
            started["preview"] = {"command": args.preview, "path": args.preview_path or "/",
                                  "ports": _ports(args.preview_ports or "")}
        if args.suite:
            started["suite"] = args.suite
        if args.base:
            started["base"] = args.base
        if args.request:
            rid, _, name = args.request.partition(":")
            started["request"] = {"id": rid, "name": name}
        if args.input:
            if any("=" not in pair for pair in args.input):
                sys.exit("--input takes name=value")
            started["inputs"] = dict(pair.split("=", 1) for pair in args.input)
    from jaynes.inputs import InputError
    from jaynes.project import ProjectError
    from jaynes.review import ensure_page
    skill.refresh()
    ensure_page(c.home)  # the person sees the run, and is called at its stops, wherever it started
    from jaynes.conductor import RunBusy
    told = False
    while True:
        try:
            result = c.run(r, task, run_id, started=started,
                           project=Path(args.project) if args.project and args.role else None)
            break
        except ProjectError as err:
            sys.exit(str(err))
        except InputError as err:
            sys.exit("\n".join(err.reasons))
        except RunBusy:
            # a stop does not hold the run, so an answer can come while a process runs it; this one goes on once
            # that one lets go, so an answer it may not have seen is never left without a process
            if args.role:
                raise
            if not told:
                print(f"run {run_id} is running in another process; going on once it lets go", file=sys.stderr)
                told = True
            time.sleep(2)
    if result.status in ("waiting", "blocked"):
        ensure_page(c.home)  # in case it was closed while the run worked
    _next_in_plan(c, run_id)
    print(json.dumps(_where(c, run_id, result), ensure_ascii=False))


def _next_in_plan(c: Conductor, run_id: str) -> None:
    """Start the runs of the run's request that waited for its delivery."""
    from jaynes import plan
    try:
        if (found := plan.of_run(c, run_id)) is not None:
            plan.advance(c, found[0].id)
    except plan.PlanError as err:  # the page looks again, and shows why
        print(f"the next runs of the plan did not start: {err}", file=sys.stderr)


def cmd_plan(args: argparse.Namespace) -> None:
    """Record a request's plan, start the runs that follow no other, and print where each run is."""
    from jaynes import plan
    from jaynes.conductor import Conductor
    from jaynes.project import ProjectError
    from jaynes.review import ensure_page
    c = Conductor(_home(args))
    try:
        raw = sys.stdin.read() if args.plan == "-" else Path(args.plan).read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError as err:
        sys.exit(f"the plan was not read: {err}")
    except ValueError as err:
        sys.exit(f"the plan is not JSON: {err}")
    try:
        text, runs = plan.parse(data, has_project=bool(args.project))
        req = plan.record(c, text, runs, Path(args.project) if args.project else None)
    except plan.PlanError as err:
        sys.exit("the plan is refused:\n" + "\n".join(f"- {r}" for r in err.reasons))
    except ProjectError as err:
        sys.exit(str(err))
    ensure_page(c.home)  # the person sees the plan, and is called at what holds its runs
    plan.advance(c, req.id)
    print(json.dumps(_request_where(c, req.id), ensure_ascii=False))


def _request_where(c: Conductor, rid: str) -> dict[str, Any]:
    """Where a request is, as one line of JSON: each run's place, and what holds it for the person."""
    from jaynes import plan
    from jaynes.ask import page
    [req] = plan.requests(c, rid)
    holding = {w.run_id: w for w in c.queue() if w.holding}
    runs: list[dict[str, Any]] = []
    for x in plan.places(c, req):
        one: dict[str, Any] = {"name": x.planned.name, "run": x.planned.run, "state": x.state}
        if x.why:
            one["why"] = x.why
        if (w := holding.get(x.planned.run)) is not None:
            one["holds"] = {"question": w.question, "reasons": list(w.reasons),
                            "page": f"{page(c.home)}/{'stop' if w.kind == 'stop' else 'run'}/{w.run_id}"
                                    + (f"/{w.step}" if w.kind == "stop" else "")}
        runs.append(one)
    return {"request": rid, "status": plan.status(c, req), "page": f"{page(c.home)}/request/{rid}", "runs": runs}


def _where(c: Conductor, run_id: str, result: Any) -> dict[str, Any]:
    """Where a run is, as one line of JSON; at a stop, its question and the page where the person answers it."""
    from jaynes.ask import page
    out: dict[str, Any] = {"run": run_id, "status": result.status, "step": result.step, "until": result.until,
                           "reasons": list(result.reasons)}
    if result.status == "waiting":  # as far as it goes without the person: its stops wait, its branch is made
        from jaynes.conductor import project
        stops = [x for x in c.queue() if x.run_id == run_id and x.kind == "stop"]
        w = next((x for x in stops if x.step == result.step), stops[0] if stops else None)
        out["question"] = w.question if w else None
        out["page"] = f"{page(c.home)}/stop/{run_id}/{result.step}"
        out["stops"] = len(stops)
        made = project(c.store.events(run_id=run_id)).branch
        if made:
            out["branch"] = made["branch"]
    elif result.status in ("blocked", "halted"):
        out["page"] = f"{page(c.home)}/run/{run_id}"
    if result.status in ("waiting", "finished"):  # merged alone, or why not
        from jaynes.conductor import project
        st = project(c.store.events(run_id=run_id))
        if st.merged and st.undone is None:
            out["merged"] = st.merged[-1]["into"]
        elif st.not_merged is not None:
            out["not_merged"] = st.not_merged["reason"]
    return out


def cmd_wait(args: argparse.Namespace) -> None:
    """Wait until the person answers the run's stop on the page, then go on with the run as ``jaynes run`` does,
    and print where it is then. The page may already be going on with it; this waits for that to let go. A
    request waits until it delivered, stopped, or something new holds one of its runs."""
    from jaynes.conductor import Conductor, RunBusy
    c = Conductor(_home(args))
    if args.run.startswith("q"):
        from jaynes import plan
        if not plan.requests(c, args.run):
            sys.exit(f"no request {args.run} in {c.home}")
        held = {r["run"] for r in _request_where(c, args.run)["runs"] if "holds" in r}
        while True:
            out = _request_where(c, args.run)
            if out["status"] != "working" or {r["run"] for r in out["runs"] if "holds" in r} - held:
                print(json.dumps(out, ensure_ascii=False))
                return
            time.sleep(args.every)
    while any(w.run_id == args.run and w.needs_person for w in c.queue()):
        time.sleep(args.every)
    while True:
        try:
            cmd_run(argparse.Namespace(id=args.run, role=None, home=args.home, here=False, preview=None,
                                       preview_path=None, preview_ports=None, suite=None, input=[], runtime=[],
                                       project=None, base=None, request=None))
            return
        except RunBusy:
            time.sleep(args.every)


def cmd_check(args: argparse.Namespace) -> None:
    r = _role(role_ref(args.role))
    problems = verify.verify(r)
    print(verify.report(r, problems))
    if problems:
        sys.exit(1)


def cmd_queue(args: argparse.Namespace) -> None:
    from jaynes.conductor import Conductor
    from jaynes.review import age
    c = Conductor(_home(args))
    items = c.queue()
    if not items:
        print("nothing is waiting")
    now = c.clock()
    for w in items:
        when = f"until {time.strftime('%H:%M', time.localtime(w.until))}" if w.until else f"{age(now - w.since)} ago"
        print(f"{w.run_id}  {w.kind}  {w.step}  {when}  {w.role}: {w.task}")
        for line in ([w.question] if w.question else []) + list(w.reasons) + (
                [f"evidence {w.evidence}"] if w.evidence else []):
            print(f"    {line}")


def _ports(text: str) -> list[int]:
    try:
        ports = [int(p) for p in text.replace(",", " ").split()]
    except ValueError:
        sys.exit(f"--preview-ports takes port numbers, not {text!r}")
    if any(not 0 < p < 65536 for p in ports):
        sys.exit(f"--preview-ports takes port numbers, not {text!r}")
    return ports


def cmd_answer(args: argparse.Namespace) -> None:
    from jaynes.conductor import Answer, AnswerRejected, Conductor, RunBusy, continue_in_background
    c = Conductor(_home(args))
    r, _, _, _ = _started(c, args.run)
    output = None
    if args.output is not None:
        text = Path(args.output[1:]).read_text() if args.output.startswith("@") else args.output
        try:
            output = json.loads(text)
        except ValueError as err:
            sys.exit(f"--output is not JSON: {err}")
    answer: Answer = args.answer
    try:
        c.answer(r, args.run, args.step, answer, note=args.note, output=output, to=args.to, by="agent",
                 evidence=args.evidence, chosen=[x.strip() for x in (args.chosen or "").split(",") if x.strip()])
    except AnswerRejected as err:
        sys.exit("answer not recorded: " + "; ".join(err.reasons))
    except RunBusy:
        sys.exit(f"run {args.run} is busy in another process; try again in a moment")
    continue_in_background(c.home, args.run)
    print(json.dumps({"run": args.run, "step": args.step, "answer": answer, "continuing": True}))


def cmd_delivered(args: argparse.Namespace) -> None:
    """Runs that delivered, newest first: what each was asked, its steps (stops marked), what it changed and its
    corrections, so an agent asked to fix something finds which delivery the fix corrects."""
    from jaynes import project as repo
    from jaynes.conductor import Conductor, project
    from jaynes.review import headline
    c = Conductor(_home(args))
    root = None
    if args.project:
        try:
            root = str(repo.resolve(Path(args.project))[0])
        except repo.ProjectError as err:
            sys.exit(str(err))
    shown = 0
    for run_id in reversed(c.store.runs()):
        events = list(c.store.events(run_id=run_id))
        st = project(events)
        if st.started is None or not st.finished or st.outcome == "dismissed":
            continue
        if root is not None and cast(dict[str, Any], st.started.get("project") or {}).get("root") != root:
            continue
        when = max(e.ts for e in events if e.type == "RunFinished")
        steps = list(dict.fromkeys(str(e.payload["step"]) for e in events if e.type in ("StepStarted", "Checked")))
        stops = {str(e.payload["step"]) for e in events if e.type in ("StopOpened", "StopSkipped")}
        print(f"{run_id}  delivered {time.strftime('%Y-%m-%d %H:%M', time.localtime(when))}  "
              f"{st.started['role']}: {headline(str(st.started.get('task', '')))}")
        print("    steps: " + ", ".join(f"{x} (stop)" if x in stops else x for x in steps))
        if st.branch:
            files = sorted({line.split(" b/", 1)[1] for _, h in st.branch.get("patches", [])
                            for line in (c.home / "objects" / f"{h}.patch").read_text(errors="replace").splitlines()
                            if line.startswith("diff --git ") and " b/" in line})
            print(f"    branch {st.branch['branch']}: " + ", ".join(files))
        for _, p in c.corrections(run_id):
            print(f"    corrected: {p['kind']} in {p['erred']}" + (f", counted at {p['stop']}" if p["kind"] == "error"
                                                                    and p.get("stop") else "") + f": {p['note']}")
        shown += 1
        if shown >= args.limit:
            break
    if not shown:
        print("nothing was delivered" + (f" in {root}" if root else ""))


def cmd_halt(args: argparse.Namespace) -> None:
    """Halt everything, as the page's Halt all does; only the person resumes, on the page."""
    from jaynes.ask import page
    from jaynes.conductor import Conductor
    c = Conductor(_home(args))
    c.halt(by="agent", note=args.note or "")
    print(json.dumps({"halted": True, "page": page(c.home)}))


def cmd_correct(args: argparse.Namespace) -> None:
    from jaynes.ask import page
    from jaynes.conductor import AnswerRejected, Conductor, RunBusy
    c = Conductor(_home(args))
    r, _, _, _ = _started(c, args.run)
    try:
        p = c.correct(r, args.run, args.step, args.kind, args.note or "", by="agent")
    except AnswerRejected as err:
        sys.exit("correction not recorded: " + "; ".join(err.reasons))
    except RunBusy:
        sys.exit(f"run {args.run} is busy in another process; try again in a moment")
    print(json.dumps({"run": args.run, "correction": p["id"], "kind": p["kind"],
                      "counted_at": p["stop"] if p["kind"] == "error" else None,
                      "page": f"{page(c.home)}/run/{args.run}"}))


def cmd_output(args: argparse.Namespace) -> None:
    from jaynes.conductor import Conductor
    c = Conductor(_home(args))
    r, _, _, _ = _started(c, args.run)
    step = next((s for s in r.steps if s.name == args.step), None)
    if step is None:
        sys.exit(f"{r.name} has no step {args.step!r}; its steps are {', '.join(s.name for s in r.steps)}")
    value = c.output(args.run, step)
    if value is None:
        sys.exit(f"{args.step} has no checked output in run {args.run} yet")
    print(value.model_dump_json(indent=2))


def cmd_ask(args: argparse.Namespace) -> None:
    from jaynes import ask
    from jaynes.conductor import Conductor
    if not skill.hooked():  # without it an answer in the dialog is recorded nowhere, and the stop asks again
        sys.exit("the hook that records answers given in Claude Code's dialog is not installed: run jaynes skill, "
                 "or answer in the review page, opened by jaynes")
    c = Conductor(_home(args))
    r, _, _, _ = _started(c, args.run)
    shown = ask.dialog(c, r, args.run)
    if shown is None:
        sys.exit(f"no stop is waiting in run {args.run}")
    print(json.dumps(shown, ensure_ascii=False))


def answered(stdin: Any = None, stdout: Any = None) -> int:
    """Claude Code's hook after its question dialog: record a Jaynes stop's answer, and tell the agent. Never
    fails the dialog: anything wrong is said to the agent, or nothing at all."""
    from jaynes import ask
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        event = json.load(stdin)
        said = ask.answered(cast(dict[str, Any], event), jaynes_home(), lambda c, run_id: _started(c, run_id)[0]) \
            if isinstance(event, dict) else None
    except Exception as err:  # a broken hook must not break the person's session
        said = f"Jaynes could not read this answer: {err}"
    if said:
        stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": said}}))
    return 0


def cmd_skill(args: argparse.Namespace) -> None:
    body = skill.SOURCE.read_text().split("---\n", 2)[2].strip()
    if skill.plugin():  # the plugin brings the skill and its hook
        skill.refresh()
        said = "The Jaynes plugin is installed: in Claude Code, /jaynes <task> runs the task with Jaynes."
    else:
        said = f"Installed {skill.install()}: in Claude Code, /jaynes <task> runs the task with Jaynes."
    print(f"{said}\n\nTo run a task now, do this, with the task in place of $ARGUMENTS:\n\n{body}\n\n"
          f"jaynes guide prints:\n\n{skill.GUIDE.read_text().strip()}")


def cmd_guide(args: argparse.Namespace) -> None:
    print(skill.GUIDE.read_text(), end="")


def cmd_update(args: argparse.Namespace) -> None:
    from jaynes.conductor import Conductor
    at = update.installed()
    if at is None:
        print(f"Jaynes is not installed from git ({Path(__file__).parent}); update it where it came from")
        return
    now = update.latest(at.url)
    if now is None:
        print(f"could not reach {at.url}; Jaynes stays at {at.short}")
        return
    if now == at.commit:
        print(f"Jaynes is up to date ({at.short})")
        if said := skill.update_plugin(now):
            print(said)
        return
    working = [w for w in Conductor(_home(args)).queue() if w.kind == "running"]
    if working:  # a run imports parts of Jaynes as it goes, so the files must not change under it
        print(f"{len(working)} run{' is' if len(working) == 1 else 's are'} working; "
              f"Jaynes updates after {'it finishes' if len(working) == 1 else 'they finish'}")
        return
    ok, out = update.reinstall(at)
    if not ok:
        sys.exit(f"update failed:\n{out}")
    after = update.installed()
    print(f"Jaynes updated: {at.short} -> {after.short if after else now[:7]}. "
          "A review page that is open offers to restart.")
    if said := skill.update_plugin(now):
        print(said)


def cmd_version(args: argparse.Namespace) -> None:
    at = update.installed()
    print(f"{at.short} from {at.url}" if at else f"not installed from git ({Path(__file__).parent})")


def cmd_demo(args: argparse.Namespace) -> None:
    from jaynes.conductor import Conductor, new_run_id
    from jaynes.demo import ROLE
    home = _home(args)
    c = Conductor(home)
    c.runtimes = {"scripted": lambda: load_ref("jaynes.demo:Agent")()}
    run_id = new_run_id()
    result = c.run(ROLE, "Redesign the People screen", run_id,
                   started={"ref": "jaynes.demo:ROLE", "runtimes": {"scripted": "jaynes.demo:Agent"}})
    print(json.dumps({"run": run_id, "status": result.status, "step": result.step}), flush=True)
    if not args.no_serve:
        _open_page(home, args.port)


def _open_page(home: Path, port: int) -> None:
    from jaynes.review import serve
    serve(home, port, browser=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jaynes")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("hook")
    a = sub.add_parser("autonomy")
    a.add_argument("--config")
    a.add_argument("--db")
    h = sub.add_parser("history")
    h.add_argument("--config")
    h.add_argument("--days", type=int, default=30)
    h.add_argument("--cwd")
    h.add_argument("--top", type=int, default=25)
    p = sub.add_parser("parity")
    p.add_argument("hook")
    p.add_argument("--config")
    p.add_argument("--days", type=int, default=50)
    p.add_argument("--top", type=int, default=10)
    s = sub.add_parser("simulate")
    s.add_argument("--budget", type=float, default=0.05)
    s.add_argument("--delta", type=float, default=0.05)
    s.add_argument("--runs", type=int, default=4000)
    s.add_argument("--horizon", type=int, default=2000)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--p", type=float, nargs="+", default=[0.0, 0.01, 0.02, 0.03, 0.05, 0.06, 0.08])
    r = sub.add_parser("run")
    r.add_argument("id", nargs="?")
    r.add_argument("--role", help="a built-in role (visual-identity, redesign, fix-bug, build-feature, small-change, screen-design), a role file (.toml) or module:attribute")
    r.add_argument("--task")
    r.add_argument("--runtime", action="append", default=[], help="name=module:attribute, overrides a runtime")
    r.add_argument("--project", help="a folder in a git repository, for steps that work on a project")
    r.add_argument("--preview", help="a command that serves the project on {port}, shown at stops")
    r.add_argument("--preview-path", help="the path to open in the preview, / by default")
    r.add_argument("--preview-ports", help="local ports the preview may connect to, such as a backend: 8789,5432")
    r.add_argument("--suite", help="the command that runs the project's whole test suite, when Jaynes does not "
                                   "find it (fix-bug, build-feature, small-change)")
    r.add_argument("--input", action="append", default=[],
                   help="name=value, an input the role declares, such as --input method=method.md")
    r.add_argument("--here", action="store_true",
                   help="started from an agent's terminal: its stops are asked there, and the page opens only when asked")
    r.add_argument("--home")
    r.add_argument("--base", help=argparse.SUPPRESS)  # a plan's later run: the commit it works from
    r.add_argument("--request", help=argparse.SUPPRESS)  # a plan's run: request:name
    pl = sub.add_parser("plan", help="a request that needs more than one role: its runs, each starting once the "
                                     "runs it follows delivered")
    pl.add_argument("plan", help='the plan as JSON in a file, or - for stdin: {"request": "<the person\'s words>", '
                                 '"runs": [{"name": "identity", "role": "visual-identity", "task": "...", '
                                 '"after": []}, ...]}; a run may also give preview, preview_path, preview_ports, '
                                 'suite and inputs, as jaynes run takes them')
    pl.add_argument("--project", help="the folder in a git repository every run works on")
    pl.add_argument("--home")
    k = sub.add_parser("check", help="find what a role gets wrong before any agent runs")
    k.add_argument("role", help="a built-in role (visual-identity, redesign, fix-bug, build-feature, small-change, screen-design), a role file (.toml) or module:attribute")
    q = sub.add_parser("queue")
    q.add_argument("--home")
    w = sub.add_parser("answer")
    w.add_argument("run")
    w.add_argument("step")
    w.add_argument("answer", choices=["accept", "change", "return", "context", "choose"])
    w.add_argument("--chosen", help="with choose: the ids kept, in order of importance, such as M3,M1")
    w.add_argument("--note")
    w.add_argument("--output", help="the edited output for change: JSON, or @file")
    w.add_argument("--to", help="with return or context: an earlier step to run again, with every step after it")
    w.add_argument("--evidence", help="the evidence hash jaynes queue printed; refuses the answer if the stop changed")
    w.add_argument("--home")
    v = sub.add_parser("review")
    v.add_argument("--port", type=int, default=8740)
    v.add_argument("--home")
    v.add_argument("--open", action="store_true", help="open the page in the browser, unless it already runs")
    sub.add_parser("policy")
    d = sub.add_parser("demo")
    d.add_argument("--port", type=int, default=8740)
    d.add_argument("--home")
    d.add_argument("--no-serve", action="store_true", help="start the run and exit, without the review page")
    sub.add_parser("skill", help="add /jaynes to Claude Code")
    sub.add_parser("guide", help="how an agent carries out a change through Jaynes; the skill runs it")
    u = sub.add_parser("update", help="install the newest Jaynes, if there is one")
    u.add_argument("--home")
    sub.add_parser("version")
    dl = sub.add_parser("delivered", help="runs that delivered, newest first, with their steps, files and corrections")
    dl.add_argument("--project", help="only runs on the git repository that holds this folder")
    dl.add_argument("--limit", type=int, default=10)
    dl.add_argument("--home")
    hl = sub.add_parser("halt", help="halt every run before its next step, and every merge, until the person "
                                     "resumes on the page")
    hl.add_argument("--note", help="why, in a sentence")
    hl.add_argument("--home")
    cr = sub.add_parser("correct", help="record that a delivery was corrected afterwards, and why")
    cr.add_argument("run", help="the run that delivered what is being corrected")
    cr.add_argument("--step", required=True, help="the step whose output was wrong")
    cr.add_argument("--kind", required=True, choices=["error", "preference", "info"],
                    help="error: wrong by what was already known; preference: a new one nobody had written; "
                         "info: the world changed or a new fact appeared. Only an error counts")
    cr.add_argument("--note", required=True, help="what was wrong, in a sentence")
    cr.add_argument("--home")
    o = sub.add_parser("output", help="the checked output of a step of a run, as JSON")
    o.add_argument("run")
    o.add_argument("step")
    o.add_argument("--home")
    wt = sub.add_parser("wait", help="wait until the person answers a run's stop on the page, then go on with the "
                                     "run")
    wt.add_argument("run")
    wt.add_argument("--home")
    wt.add_argument("--every", type=float, default=2.0, help="seconds between looks")
    ak = sub.add_parser("ask", help="the open stop of a run, as input for Claude Code's question dialog")
    ak.add_argument("run")
    ak.add_argument("--home")
    sub.add_parser("answered", help="Claude Code's hook after its question dialog")
    mc = sub.add_parser("mcp", help="the MCP server for agents, over stdio: what waits, and the answers an agent "
                                    "may give; Claude Code starts it with the plugin")
    mc.add_argument("--home")
    args = ap.parse_args(argv)
    if args.cmd is None:
        _open_page(jaynes_home(), 8740)
        return 0
    if args.cmd == "hook":
        return hook.main()
    if args.cmd == "answered":
        return answered()
    if args.cmd == "policy":
        print(BUILT_IN_POLICY.read_text(), end="")
        return 0
    if args.cmd == "mcp":
        from jaynes import mcp
        mcp.serve(_home(args))
        return 0
    if args.cmd == "review":
        from jaynes.review import serve
        serve(_home(args), args.port, browser=args.open, point=not args.open)
        return 0
    {"autonomy": cmd_autonomy, "history": cmd_history, "parity": cmd_parity, "simulate": cmd_simulate,
     "run": cmd_run, "check": cmd_check, "queue": cmd_queue, "answer": cmd_answer, "demo": cmd_demo,
     "skill": cmd_skill, "update": cmd_update, "version": cmd_version, "ask": cmd_ask, "wait": cmd_wait, "output": cmd_output,
     "guide": cmd_guide, "delivered": cmd_delivered, "correct": cmd_correct, "halt": cmd_halt,
     "plan": cmd_plan}[args.cmd](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

