# Jaynes

Make your AI agents follow the method, check what they deliver, and call you
only where it matters. For people who run a team of agents in Claude Code.

> **Alpha.** Jaynes runs end to end on macOS with Claude Code, but its role
> API and stored formats will still change, and it is not on PyPI yet. See
> [Status and roadmap](#status-and-roadmap).

## The problem

Your agents say "done". They skipped half the process, the automated checks
passed, and the result is still wrong, so you fix it yourself. You either
review everything and become the bottleneck, or let them run and have no idea
how often they get it wrong.

In a one-person company run by agents, over seven weeks: 11,894 tool calls,
25 of them seen by the person. Of 8 design screens, none followed the first
four steps of the design method, and screens that passed every automated check
were still sent back.

## How it works

1. **Describe a role as a method.** Ordered steps, each with a required output
   and a check.
2. **Jaynes conducts the agent one step at a time, always on the same model.**
   Claude Opus at high effort unless you set `JAYNES_CLAUDE_MODEL` or
   `JAYNES_CLAUDE_EFFORT`; a turn another model answers does not count. Claude Code runs a single
   step and can only write that step's output. The next step starts
   when the check passes. Evidence the agent could fake, like the references
   captured from the web, is produced by Jaynes.
3. **Where the method needs you, it shows you, and goes on.** The run
   continues with the agent's choice and delivers its branch; the question and
   its evidence wait for you in the review page, all of them at once, or come
   to you in Claude Code's question dialog. A return corrects on top: only
   the steps that used what changed run again. What cannot be undone, and a
   change to files that run outside the box, waits for your answer. A role
   whose delivery is a merge proves its work with checks written from your
   request alone, which its maker never sees, and merges by itself once they
   all pass; Undo reverts it.
4. **What counts is what happened after delivery.** An accept counts as
   right only once its delivery went 7 days with nobody correcting it; a
   change or a return counts as an error at once, and so does a correction
   found later, on the first stop after the step that erred. A stop that
   keeps proving right becomes sampled: it asks one time in ten. Two errors
   among its last ten outcomes brake it: it asks on every run and the run
   waits for you there, until the deliveries after the brake prove it right
   again.
5. **Underneath, every tool action goes through a gate.** Some actions, like
   pushing or deleting history, never run without you.

The whole flow, from a role to the evidence behind each stop, is in
[docs/how-it-works.png](docs/how-it-works.png) (source:
[how-it-works.drawio](docs/how-it-works.drawio)).

A sketch of the `screen-design` role (a working role is under
[Write a role](#write-a-role)):

```python
from jaynes import role, stop
from checks import cites_real_data, tests_pass, contrast_aa, tokens_only

design = role("screen-design")

moments = design.step("moments", output=Moments, stop=stop(budget=0.20))
objects = design.step("object-map", output=ObjectMap, needs=moments, check=cites_real_data)
walk = design.step("walkthrough", output=Walkthrough, needs=objects, run_by="jaynes")
reqs = design.step("requirements", output=Requirements, needs=walk, stop=stop(budget=0.10))
build = design.step("implement", output=Patch, needs=reqs, check=[tests_pass, contrast_aa, tokens_only])
done = design.step("accept", needs=build, stop=stop(budget=0.05, sampled=False))
```

## Concepts

| Term | Meaning |
|---|---|
| Role | A kind of work: screen design, copy review, bug fix |
| Package | A set of roles, installed at a version and used by the projects that list it, as an npm library; the engine has no role of its own |
| Step | One unit of a role's method, with a required output |
| Check | Code that must pass before the next step starts |
| Stop | A step where the method needs a person |
| Label | Your answer at a stop: accepted, changed, or returned |
| Budget | The error rate a stop may have once it stops being mandatory |
| Floor | An action or stop that never runs without a person |
| Effect | What a trusted step does to the world: `pure`, `read`, `write-copy`, `web` or `irreversible` |

## The guarantee

A stop becomes sampled only when its evidence clears a threshold that holds no
matter how often it is checked: if the true rate at which you change that
stop's output is at or above its budget, the chance it ever becomes sampled is
at most 5%. With a 5% budget that takes 88 clean deliveries in a row; with
20%, it takes 20. The test is a mixture likelihood ratio, valid at any stopping time
by Ville's inequality. Details in [SPEC.md](SPEC.md).

## Status and roadmap

| Part | State |
|---|---|
| Action gate for Claude Code: categories, floors, sampled audits, labels from the transcript, anytime-valid certification | Works |
| Guard in every Claude Code session with the plugin: asks before git and GitHub actions that cannot be undone (push, reset --hard, clean, branch -D, tag -d, pull requests and repositories created, merged, closed, deleted or archived) outside your free owners' repositories, and before changes to its own files; switch and owners on Setup | Works in tests |
| History report and guarantee simulation | Works |
| Step conductor for Claude Code (Codex also runs): roles in Python or TOML, write boundary, checks, retries, resume after a crash, quota pause | Works |
| Review page and CLI answers: queue, stops, runs, roles, log; return to an earlier step; dismiss a run; running and paused runs in the queue, a paused run continuing at its reset | Works |
| Answers bound to who gave them and to the evidence shown; floor paths; irreversible steps with idempotency keys | Works |
| Counts from the outcome: an accept counts once its delivery held 7 days, corrections after delivery (`jaynes correct`, kind changeable on the page), the brake after two errors in ten | Works in tests; no real correction yet |
| Proof the maker cannot see, in build-feature and fix-bug: cases written from the request alone before the work, run by Jaynes against the changed project served in a box; a failing case sends the maker back with its sentence only | Works in tests; no real run yet |
| Merging alone, in build-feature and fix-bug: once every hidden check passes, Jaynes merges the branch into the branch you started from, only over a clean checkout, never pushing; a return merges the correction on top; Undo on the run's page reverts it | Works in tests; no real run yet |
| Halt all on the page (and `jaynes halt`): no run takes a step or merges until you resume | Works in tests |
| The page's home in four parts: what holds a run waits for you, the samples the runs went past, what was delivered since your last visit with its hidden checks, and what runs now; the browser opens by itself only for what holds a run | Works in tests and on a scratch page |
| A local API with every action of the page (`/api/` on the page's address, JSON), and the MCP server (`jaynes mcp`) the plugin starts in Claude Code: an agent answers what holds a run and sends a blocked run back; only you answer samples, drop a run, undo and resume | Works in tests; a Claude Code agent used it on a scratch home |
| Real projects: steps on copies, previews before and after, a branch at the end | Works |
| Box for previews on macOS; web access per step, with every address recorded | Works |
| Package design, which comes with Jaynes: visual-identity, redesign (from references) | Works, first real runs in progress |
| Package software, which comes with Jaynes: fix-bug, a failing test first, then a fix, both run in the box | Works; first real run delivered its branch |
| Package software: build-feature (the agreed plan's tests first, shown as examples with sketches of the screens, then code that keeps the project's identity) and small-change (no stop unless the change is big) | build-feature delivered four slices of a real Go project; small-change works in tests |
| Package design: screen-design, your design method from how the screen is used (moments, objects, walkthrough, requirements), then one slice measured, proved and reviewed; Jaynes's own Chrome on data frozen at the start | Works in tests on an invented product; no real run yet |
| Inputs a role declares, frozen at the start; choosing among an output's items at a stop; Jaynes sending a step back with what it found | Works |
| A request as a plan of runs (`jaynes plan`): each run starts once the runs it builds on delivered, from where their delivery landed, without waiting for samples; the page shows the plan | Works in tests and on a scratch page |
| Roles are packages, as npm libraries: `jaynes add` installs one at a fixed version, from git or a folder of yours, for one project or every project; `jaynes roles` lists what a project can start; the engine has no role of its own | Works in tests and on a scratch home with a private package |
| A role whose step delivers a plan, which Jaynes checks and starts once its run delivered | Works in tests; a private direction role planned a real request with Claude Code |

Missing, roughly in order:

- **Published package.** Not on PyPI yet; install from GitHub (below).
- **A first real case**, measured against the first user's baseline.
- **Corrections after delivery.** A return corrects the branch until you
  answer the last stop; after that, a correction is a new run, which the
  agent records with `jaynes correct` so it counts. The agent finds the
  delivery from the list `jaynes delivered` prints, not yet from the code's
  history; a new preference is not yet kept as taste memory; every project
  waits 7 days, not yet a time of its own. A step cannot yet start over from
  scratch, giving its reason, instead of correcting on top.
- **Changing a plan.** A role can plan a request, and its plan's sample
  counts; but a plan cannot be started from the page or changed once it
  started, so a return makes a new plan that does not replace it.
- **small-change merging alone.** It has no hidden checks yet, so the
  person still merges it.
- **Halting a turn under way.** Halt all stops every run before its next
  step; a turn already running finishes first.
- **A turn past its deadline keeps its work.** The next attempt starts from
  nothing, even when the tests were almost written.
- **Old runs leave the queue.** A run waiting for two days from a test that
  was dropped sits next to today's with the same weight.
- **The page as an app.** Stops are answered on the page, and the terminal
  only says where; next, the page installed as an app (a PWA) with macOS
  notifications.
- **More roles in the software package** that need no design taste to check,
  such as upgrading dependencies.
- **Two or three directions to choose from** in redesign. Stops can let you
  choose among an output's items now; the design roles still propose one
  direction.
- **The slices after the first** in screen-design. A run builds the first
  slice of the requirements you approved; how the next run starts from them
  is open.
- **A box on Linux.** There, previews and test runs refuse to run unless
  `JAYNES_UNBOXED=1`.
- **Roles without Python**, for people who don't write code.
- **Judges calibrated on your labels**, so fewer answers are needed.
- **Keeping a stop's labels across agent model versions.** Today a new model
  starts the stop's count over; keeping them needs a detector that withdraws
  the certification if errors rise.
- **Answers only you can give.** An agent running as your user can start its
  own review page and read the link it prints, then answer as you. Closing
  this needs a confirmation the agent cannot reach, such as Touch ID.

## Get started

Python 3.12 or later.

The shortest way: type this in Claude Code, answer `y` to add the
marketplace, and press Enter to install it for your user.

```text
/plugin install jaynes --marketplace alk0x1/jaynes
```

Then work as usual: talk the change through with the agent and approve the
plan ("go ahead", "do it"). The agent hands it to Jaynes by itself; you can
also ask for a task "with Jaynes", or say "without Jaynes" for one the agent
should make itself. To turn Jaynes off in a project, run
`claude plugin disable jaynes@jaynes --scope local` there. The first time, the agent installs the
`jaynes` command with uv. It picks the role and starts the run; a small
change goes through with the suite and no stop. At each stop the agent shows you the
question and its evidence in Claude Code's question dialog, and you answer
there: Accept, or Return with a note. The review page, opened by `jaynes`,
shows a stop in full and offers Change and Context too. When a newer Jaynes
is out, the page offers to update and restarts itself.

The plugin is the skill, the hook that records your answers in the dialog,
the guard below and Jaynes's MCP server, installed by you: no agent writes
your Claude Code settings. The MCP server gives any agent in Claude Code what
waits in Jaynes and the answers an agent may give: a stop that holds its run,
a blocked run sent back with a note. Samples, Undo and Resume stay yours, on
the page. The skill only
says when to use Jaynes; the steps come from `jaynes guide`, so they change
with the `jaynes` command. The agent runs `jaynes update` at the start of each
task, and it brings the plugin to the same commit with Claude Code's own
`claude plugin update`, used from the next session.

The plugin also brings a guard, in every Claude Code session where it is on,
not only in Jaynes runs. Before an agent pushes, runs `git reset --hard`,
`git clean`, `git branch -D` or `git tag -d`, or creates, merges or closes a
pull request or creates, deletes or archives a repository with gh, Claude Code
asks you, saying what cannot be undone and why: for example, *git push
publishes commits to github.com/quill-labs/site, whose owner is not one of
your free owners*. It asks instead of blocking, so you approve with one key;
in `claude -p`, where nobody can answer, Claude Code refuses it. Repositories
on GitHub whose owner is one of your free owners pass freely: at first your
own GitHub user, found once with `gh`, and you add or remove owners on the
Setup page. Everything else, like `git merge` or `git branch -d`, runs as it
did. It also asks before anything changes its own files, and it needs only
python3, not the `jaynes` command. To turn it off, press Turn off under Guard
in Claude Code on the Setup page, or set `"on": false` in
`~/.jaynes/guard.json` yourself (Claude Code asks first if an agent tries);
disabling the plugin turns it off with the rest.

Or by hand:

```sh
uv tool install --reinstall git+https://github.com/alk0x1/jaynes
jaynes skill                   # without the plugin: /jaynes in Claude Code, stops in its dialog
jaynes                         # opens the page on http://127.0.0.1:8740
```

Roles come in packages. Jaynes installs two by itself, `software`
(build-feature, fix-bug, small-change) and `design` (visual-identity,
redesign, screen-design), for every project. Add your own for one project or
for all:

```sh
jaynes add ~/my-team --project .            # a folder with jaynes-package.toml, linked: edits apply at once
jaynes add https://github.com/you/roles     # from git, at its newest commit; jaynes update moves it
jaynes roles                                # what this project can start, your packages first
jaynes packages                             # what is installed, and where each is used
```

Everything else happens in the page:

- **New run** starts a run: pick a role, write the task, and point it at a git
  repository. The demo needs no repository, API key or quota: a scripted agent
  works through a screen-design role, a check sends one step back, and the run
  stops for your answer. **visual-identity** comes with Jaynes: the agent reads
  the project's current colours and fonts, proposes a direction (Jaynes checks
  its contrast), and applies it to the style files. **redesign** goes further:
  it also changes motion and the markup of the screens (HTML, JSX, TSX, Vue,
  Svelte), keeping what each screen does. It starts from references: put one
  or more links or image paths of interfaces you like in the task. Jaynes
  captures each link with headless Chrome and gives the pictures to the agent,
  and the direction must say what it takes from each one. Agents run without
  the web unless a step allows it (`web=True`); the direction step does, so
  the agent can read the references' sites and look up fonts, and the stop
  lists every address it read. Shell commands stay offline. **fix-bug** first
  finds how the project runs its tests (pytest, a `test` script in
  `package.json`, `go test`, `cargo test` or a Makefile `test` target), so the
  agent never chooses what counts as the suite; when it finds none, the run
  stops and asks you to start it again with `--suite "<command>"` (or Test
  suite on New run). Then the agent writes a test that fails because of the
  bug, changing test files only and naming them; Jaynes runs them in the box
  and requires them to fail with the message the agent quotes, and you see
  the test. Then the agent fixes the code, never the
  tests, until Jaynes sees those tests and the whole suite pass. If the agent
  finds no bug a test can show, it says why and you decide: accepting ends
  the run with nothing changed. The agent runs the tests itself too.
  **build-feature** works the same way from the plan you approved: tests that
  fail because the feature does not exist yet, a stop where you see one to
  five examples of what they check, each with a sketch of its screen drawn by
  Jaynes's Chrome from the agent's page, then the code, then a stop before the
  merge, with each page photographed on the real screen when the run has a
  preview. That photo shows what the product reads on your computer, your
  data included, and stays on it. The code keeps the project's identity: Jaynes reads the colours and
  fonts the project's files already set, and refuses a sketch or a screen
  that brings others; changing them is visual-identity's work.
  **small-change** is the light path: the agent makes the change and Jaynes
  runs the suite, with no stop, unless the diff has more than 30 changed lines
  or touches a path that runs later; then you see it.
  **screen-design** carries out your own design method on one screen; you
  give it as inputs, with your finishing heuristics:
  `--input method=method.md --input heuristics=heuristics.md`. It needs a
  preview command and the screen's path. Jaynes opens the screen itself, in
  headless Chrome, in the theme you judge in (`--input theme=dark`), with a
  copy of the data the product reads frozen at the start
  (`--input data=~/app/data:data`), so it never touches live data. Agents read
  a summary of each API response, and a number they write must name a query
  that Jaynes computes. You choose and order the moments where you get
  stuck, from cases quoted from the screen. The agent maps the objects you
  think in and writes each moment's path in a closed vocabulary (open, click
  a text, open a row, press a button, scroll, type, back). Jaynes walks each
  path with every request refused but the product's own GETs, minus those you
  list as having effects (`--input blocked_gets=api/logo`). You approve one
  requirement per break, and the first slice with where its moment must
  end. The slice's test is a path that must fail on the screen as it is and
  pass once it is built. Your own measuring tools then check it
  (`--input measure=...`, `--input compare=...`, each printing
  `{"failures": [...]}`), against the version you last approved
  (`--input approved=<commit>`). A reviewer looks at captures in both themes,
  on desktop and phone, and at each transition, and what fails goes back to
  the implementer. The final stop shows new tokens per step against the
  budget (`--input budget=150000`). After it, every note you gave becomes a
  heuristic for your file. The box
  reaches no internet, only local ports (tests that start servers or use a
  local database work), so the suite must pass at your last commit with its
  dependencies already installed (`node_modules`, `.venv`). With a preview command, such as
  `npm run dev -- --port {port}`, each stop shows the project before and after.
  The preview runs the agent's changes, so on macOS it runs in a box: it writes
  only to its copy, cannot read where secrets live, and reaches no network but
  the local ports you list (a backend it needs). Elsewhere previews refuse to
  start unless `JAYNES_UNBOXED=1` accepts running them unboxed.
  At a stop, return and context can go back to an earlier step: it runs again
  with your note, and so does every step after it. Steps work on copies of the
  last commit; when the run finishes, the accepted changes are a branch,
  `jaynes/<run>`, and your checkout is untouched. The queue also lists runs
  that are working and runs paused because the agent's plan ran out; a paused
  run continues by itself when the plan resets, while the page is open. At a
  stop that must never pass without you, such as the final accept, the A key
  accepts only when pressed twice.
- **History** reads your Claude Code transcripts and shows how many tool calls a
  person approved. It changes nothing.
- **Setup** installs the action gate in Claude Code: `jaynes hook` on
  `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop` and `SessionStart`,
  with the policy in `~/.jaynes/jaynes.toml`. Your other hooks stay, and
  `settings.json` is copied to `settings.json.jaynes-backup` first. On an
  internal error the hook prints nothing and Claude Code decides as if Jaynes
  were not installed. It also adds or removes `/jaynes`, and shows which
  commit of Jaynes runs.

The same from a terminal or an agent: `jaynes demo`, `jaynes history`,
`jaynes simulate`, `jaynes policy` (prints the built-in policy), `jaynes queue`,
`jaynes answer`, `jaynes wait` (returns once you answered a run's stop on the page), `jaynes ask`, `jaynes output` (a step's checked output, as JSON), `jaynes delivered` (the latest deliveries, with their steps and files), `jaynes correct` (records that a delivery was corrected afterwards), `jaynes halt` (halts every run until you resume on the page), `jaynes guide`, `jaynes update` and `jaynes version`. `jaynes --help` lists them all.

Only you answer as a person. The page answers only in a browser opened with
the link `jaynes` prints when it starts (it opens it for you), which it keeps
as a cookie; anything else, like an agent with `curl`, can read the page but
not answer. An answer from `jaynes answer` releases the stop but is recorded
as an agent's: what it let through never counts as right, so an agent cannot
make a stop sampled by accepting its own work, and a floor stop and a braked
one refuse it. An answer in
Claude Code's dialog is yours: the plugin, or `jaynes skill`, adds a hook that Claude Code
runs after the dialog with what you chose, and it records the answer only
when the dialog showed the stop exactly as `jaynes ask` rendered it. Every answer
carries the hash of what was shown, so a page left open cannot accept a newer
round it never showed.

Some files run later, outside any box: agent and editor settings (`.claude`,
`.vscode`, `CLAUDE.md`), git attributes and hooks, `.envrc`, `package.json`,
`pyproject.toml`, CI workflows. A step that changes the project must list what
it may change (`may_change`, `["**"]` for anything), and even then a change to
one of those files is rejected in a step without a stop, and turns a step's
stop into a floor that shows the file's diff.

### Write a role

A role is a Python module. Save this as `count_words.py`:

```python
from pydantic import BaseModel

from jaynes.role import Fail, Pass, StepContext, Witnesses, role


class Topic(BaseModel):
    topic: str
    words: int


class Sentence(BaseModel):
    sentence: str


def read_topic(ctx: StepContext) -> Topic:
    return Topic(topic=ctx.task, words=len(ctx.task.split()) + 5)


def matches(out: Sentence, needs: Witnesses) -> Pass | Fail:
    want = needs[TOPIC].value.words
    got = len(out.sentence.split())
    return Pass() if got == want else Fail(f"the sentence has {got} words; it must have exactly {want}")


ROLE = role("count-words", agent="claude")
TOPIC = ROLE.step("topic", output=Topic, run_by=read_topic)
SENTENCE = ROLE.step("sentence", output=Sentence, needs=[TOPIC], check=matches,
                     instructions="Write one sentence about the topic in inputs/topic.json with exactly "
                                  "the number of words given there.")
```

A trusted step that acts on the world declares it: `effect="irreversible"`
(book, pay, send) needs only steps with floor stops, so you approve its whole
payload. It runs once, with `ctx.idempotency_key` for the service
it calls, and a run that stopped while it was running blocks instead of running
it again.

Jaynes runs the first step itself; Claude Code (with your login) writes the
sentence, and the check sends it back until the count is right. Use
`agent="codex"` for Codex. Start `jaynes` from the folder that holds the file,
then pick the role under New run (type `count_words:ROLE` the first time), or:

```sh
jaynes run --role count_words:ROLE --task "lighthouses in fog"
```

Before any agent works, Jaynes checks the role and refuses it with a list of
what to fix: a check, question or executor that reads a step its step does not
need, or a step of another role; a check whose first argument is annotated
with another step's output type; a step, other than the last, whose output no
later step needs and no stop shows, unless it changes the project or acts.
`jaynes check count_words:ROLE` runs the same check alone.

The steps can also live in a TOML file, with the models and checks staying in
Python. Save the classes and functions above, without the last three lines,
as `count_checks.py`, and change the check to read its need by name,
`needs["topic"]`. Then save this as `count_words.toml`:

```toml
name = "count-words"
agent = "claude"

[[step]]
name = "topic"
output = "count_checks:Topic"
run_by = "count_checks:read_topic"

[[step]]
name = "sentence"
output = "count_checks:Sentence"
needs = ["topic"]
check = ["count_checks:matches"]
stop = { budget = 0.2 }
question = "Is this sentence right: {out.sentence}?"
instructions = "Write one sentence about the topic in inputs/topic.json with exactly the number of words given there."
```

```sh
jaynes run --role count_words.toml --task "lighthouses in fog"
```

A key Jaynes does not know, a reference that does not import, or a question
that names a field the output lacks is refused with the step it is in.

### From a clone

```sh
uv sync
uv run pytest
```

## Foundations

Typestate and session types for methods that cannot be skipped; object
capabilities for per-step write access; algebraic effect handlers for one
mechanism behind gating, auditing and replay; anytime-valid inference with
e-values for the guarantee; prediction-powered and active statistical
inference for needing fewer of your answers. References in the
[design document](docs/specs/2026-10-21-landing-and-readme-design.md#5-foundations).

## License

Apache-2.0
