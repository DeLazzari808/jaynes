# Roles anyone writes

2026-11-02. Status: design, approved 2026-11-03.

## Why

The engine works. In its first real run it kept an agent to its steps, refused
answers whose numbers did not come from the data, and stopped only where the
role said. It is also exactly as good as the role it runs. That run's role
assumed the person gets stuck at moments on a screen that is otherwise useful.
When the person returned the output saying the screen itself did not help
them, the role had nowhere to put that answer, and it asked the same question
again.

Two lessons follow:

1. **Roles are the product.** The engine is like GitHub Actions without
   workflows: nothing happens until someone writes the work down. Jaynes
   spreads if many people write roles, the way people write skills today.
2. **A role is not a skill.** A skill is text an agent reads and may follow.
   A role is different because of its checks: code that runs after each step
   and refuses an output that breaks a rule, such as "the tests pass" or
   "every number comes from the data". A role made of text alone is a skill
   with more steps, and it loses the guarantee.

So the question is not how to let people write roles. It is how to let people
write roles **with checks, without writing code**.

## What exists today

- A role can be written in a TOML file (`src/jaynes/role_file.py`): its steps,
  needs, stops, questions and instructions.
- Each step's output still needs a Pydantic model in Python, and each check is
  a Python function. Only people who code can write a role that checks
  anything.
- The built-in roles hold good checks, but each one is welded to its role:
  - `fix_bug.fails_for_the_bug` and `fixed`: a test that fails before and passes after;
  - `small_change.passes`: the project's suite passes;
  - `redesign.cited` and `screen_design.moments_cited`: what the agent quotes is really there;
  - `visual_identity.readable`: contrast;
  - `screen_design.slice_works`: a path through the product reaches an ending.
- The engine already lets a return go to an earlier step, chosen by the person
  under More at the stop.

## Goals

1. A person who does not code writes a role in one text file, with real checks.
2. Every role starts by confirming what the person wants, and "not this" turns
   the run back to that start, instead of repeating a step.
3. A role installs with one command from a GitHub repository and has a page.
4. Every role shows its record: how often people accepted at each of its stops.

## Not goals

- An agent that invents the role in each conversation. That gives the method
  back to the agent, which is the problem Jaynes exists to solve.
- A marketplace with accounts, payments or ratings typed by people. The record
  comes from answers at stops, never from stars.
- Replacing Python roles. Python stays for checks the menu does not have.

## The format

A role is one Markdown file with a TOML header. The prose is the method, as a
person would write it. The header holds the steps. Example, a bug fix:

```markdown
+++
name = "fix-bug"
summary = "Reproduce a bug with a failing test, then fix it without touching other tests."

[[step]]
name = "goal"
kind = "goal"                      # the confirmation every role starts with
ask = "What should work that does not?"
offer = ["the bug as described", "a narrower case of it", "not a bug: it is how it should work"]

[[step]]
name = "reproduce"
does = "Write a test that fails because of the bug."
gives = { test = "path of the new test", why = "what the test shows, in a sentence" }
check = ["fails before the change: {test}"]

[[step]]
name = "fix"
does = "Change the code so the test passes."
gives = { summary = "what changed, in a sentence" }
changes = ["src/**"]               # what the step may touch; everything else is refused
check = ["passes: {reproduce.test}", "the suite passes"]

[[step]]
name = "accept"
kind = "stop"
ask = "Keep this fix?"
shows = ["{fix.summary}", "the diff"]
+++

Reproduce first. A fix without a failing test is a guess.
Never change a test to make it pass.
```

What each part means:

- `does` is the instruction for the agent.
- `gives` is the output, as named fields with a sentence each. Jaynes builds
  the model from it. A field is text unless it says otherwise, as in
  `files = "list: the files changed"` or `count = "number: ..."`.
- `check` lists checks from the menu, in plain words. A field of an earlier
  step goes in braces.
- `changes` lists what a step may change in the project, as globs.
- `kind = "stop"` waits for the person. `ask` is the question and `shows` is
  what the stop shows in front: sentences, pictures, the diff.
- A step with `stop = { budget = 0.2 }` instead of `kind = "stop"` stops until
  its record lets Jaynes skip it, as today.

A Python check still works anywhere a menu check does, as `module:function`.

## The menu of checks

Each item is a check Jaynes ships, tested, and named in words. Most come out of
the built-in roles. The first set:

| In the role | What Jaynes checks | Comes from |
|---|---|---|
| `the suite passes` | The project's test command exits 0 on the step's copy | `small_change.passes` |
| `fails before the change: {test}` | The test fails on the project as it was | `fix_bug.fails_for_the_bug` |
| `passes: {test}` | The test passes on the changed project | `fix_bug.fixed` |
| `only changes {glob}` | Nothing outside the globs changed (also `changes =`) | `may_change` |
| `quotes {file}` | Every quote in the output appears in that file, word for word | `screens.cited` |
| `numbers come from {data}` | Every number is computed from the data, not typed | `screens.counted`, `loose_numbers` |
| `the command passes: {command}` | Any command exits 0, in the box | `suite` |
| `readable` | Text and background contrast meet WCAG AA | `visual_identity.readable` |
| `reaches {route} showing {text}` | A path through the product ends where it should | `screen_design.slice_works` |
| `between {n} and {m} items in {field}` | A list's length | new |
| `changes after a return` | After the person returns it, the output differs, or says why it does not | new, see below |
| `tests hidden from the implementer` | One agent writes the acceptance tests and the agent that builds never sees them | new, from an agent team's own pipeline |

Adding to the menu is how Jaynes gets better for everyone. A check in the menu
has tests, a plain name and a refusal message a person can read.

## The goal step

Every role starts with `kind = "goal"`. The agent turns the task into two to
four concrete readings, from the role's `offer` and the task's words, and the
person chooses one. If none fits, they write their own.

Later, a return whose note says "not this" goes back to the goal by default,
not to the step it came from. The stop's More menu still lets the person send
it anywhere else. This uses the return to an earlier step that the engine
already has. The new parts are the goal kind and the default target.

## Options at any stop

Choosing among two or three options is not a design idea. A designer shows
three directions in pictures, a bug fix shows three readings of the bug, a
writer shows three headlines. So any step may say `offer = 3`: its output is a
list of options, and its stop lets the person pick one, or write their own.
The role decides where options appear, never the agent: a clear task such as
"this test fails, fix it" gets none. The goal step is the first use of it.

The goal stop is in every role, and it fades like any stop: when the person
keeps choosing the agent's first reading, its record lets Jaynes skip it.

## Taste carried between runs

Jaynes stores every answer at every stop, with the person's note, but today a
note reaches the agent only within the run it was given in. The next run of
the same role starts from nothing, and can repeat what the person turned down.

New: at the start of each run, Jaynes gives the agent the person's earlier
choices and notes for that role: what they chose, what they turned down and
why. Taste builds up as the person uses the role, with no file to write or
keep. It costs little: an answer takes about 200 bytes in the log, and the
last 20 answers with notes take about 1,200 tokens, under 1% of a run. Text
the agent reads is still text it may ignore; the guarantee is the record,
which shows whether accepts on the first try go up.

## Roles from the descriptions people already have

People who orchestrate agents already describe each specialist in a text
file. `jaynes add` also takes such a file: an agent turns its method into a
role with menu checks, and the person approves the role on a page, without
reading the file. The translation to fixed menu phrases happens once, when
the role is written; at run time only fixed phrases count. A specialist is
then its roles, and work it does outside them arrives marked "no guarantee".

## Same answer after a return

Today an agent can hand back the same output after the person returns it,
and Jaynes shows it again with no word. In the first real run, that is what
the person saw.

New rule, in the engine and for every role: after a return, an output equal
to the one returned is refused unless it carries a reason, in a field the
engine adds. The stop shows that reason first.

## Installing and sharing

- `jaynes add github.com/someone/roles` copies the role files of a repository
  into `~/.jaynes/roles/`, pinned to a commit. `jaynes update` updates them.
- `jaynes roles` lists them, and the page's New run offers them.
- A role file from someone else runs in the same box as any other: its checks
  come from the menu, or from Python the person installed on purpose.
- jaynes.dev gets a gallery that reads a list of repositories. Each role has a
  page with its steps, its checks and its record.

## The record

Jaynes already stores every answer at every stop, by stop fingerprint. A role's
record is computed from that, and nothing new is collected:

- on the person's machine, in the review page: runs, and for each stop how
  often the answer was accept;
- shared only if the person opts in, as a file they publish next to the role.

Shared records are what make a role worth trusting before the first run. They
are also what a skill cannot have.

## How this is tested

The design holds if two roles fit in it without Python:

1. **fix-bug**, rewritten as above. Its tests today pass unchanged against the
   new file.
2. **screen-design**, restarted from the goal: "what should this screen do for
   you?". On Lantern, the invented book club product the tests use, its
   reading list offers options such as "show what we read next", "show which
   books were read already", "find a book fast", or "nothing: I don't need
   this list". The moments, the walkthrough and the slice follow the chosen
   goal. The test is Lantern's screen-design run; rerunning a real failed run
   happens on the user's side, outside this repository.

If one of them needs Python beyond a menu check, the format grows, or the menu
does, before anything ships.

## Screens keep the identity

A feature does not change how the product looks. Jaynes reads the colours and
fonts the project's files already set before any agent works (the identity
step of build-feature), shows them at the examples stop, and refuses a sketch
or a built screen that brings others. With no identity yet, anything goes, and
the stop says so. Changing the identity is visual-identity's work, where the
person sees the direction first. This is the tolerance a factory measures
against: taste enters once, at the direction; after it, the look is checkable.
Later it becomes a menu check any role can name.

The workflow for a new product, as tenure showed: a thin end-to-end slice
first, ugly, with real data (build-feature); then visual-identity on those
real screens; from then on every build-feature keeps that identity, and
screen-design improves one screen at a time with the person's method.

## Order of work

0. Done on 2026-11-04: sketches of the screens at the examples stop, stops
   answered on the page with the terminal only saying where, and the identity
   step and checks in build-feature.
1. The format: Markdown with a TOML header, `gives` to models, menu checks by name.
2. The menu, starting with the checks the two roles need.
3. Options at any stop, and the goal step built on them, with returns that say
   "not this" going back to it.
4. Taste carried between runs of a role.
5. The rule against the same answer after a return.
6. fix-bug and screen-design in the new format.
7. The converter: a specialist's text file to a role, approved on a page.
8. A week of real work: three specialists of an agent team (a product
   designer, a full stack engineer, a marketer) work through their roles, and
   accepts on the first try are measured from Jaynes's log.
9. `jaynes add` from repositories, tests hidden from the implementer, and the
   gallery on jaynes.dev.

## Decisions

- Checks are fixed phrases with slots, as in the table. An unknown phrase is
  an error that lists the menu.
- Design is measured against the project's own identity, never against a
  standard of Jaynes's: the identity is what the project's files set, and
  only visual-identity changes it.
- `jaynes add` runs only menu checks from a role someone else wrote; Python
  checks from others need an explicit flag.
- When a role changes, its record shows the current version and the earlier
  ones apart: the guarantee counts per version, and a young version does not
  look empty.
