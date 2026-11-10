# Write a role

A role is your method for one kind of work, written as steps in order. Each step has a required output and, usually, a check: code that must pass before the next step starts. A step can stop for you, and a step can be run by Jaynes instead of the agent, for evidence the agent could invent.

## A role in Python

Save this as `count_words.py`:

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


ROLE = role("count-words", agent="claude", about="Writes one sentence with an exact number of words.")
TOPIC = ROLE.step("topic", output=Topic, run_by=read_topic)
SENTENCE = ROLE.step("sentence", output=Sentence, needs=[TOPIC], check=matches,
                     instructions="Write one sentence about the topic in inputs/topic.json with exactly "
                                  "the number of words given there.")
```

Jaynes runs the first step itself. Claude Code, with your login, writes the sentence, and the check sends it back until the count is right. `about` says what the role is for; your agent reads it to pick a role for a request. Use `agent="codex"` for Codex.

Run it from the folder that holds the file:

```sh
jaynes run --role count_words:ROLE --task "lighthouses in fog"
```

Or start `jaynes` from that folder and type `count_words:ROLE` under New run. To run it by its name in a project, put it in a [package](packages.md#make-a-package).

## A role in TOML

The steps can live in a TOML file, with the models and checks staying in Python. Save the classes and functions above, without the last three lines, as `count_checks.py`, and change the check to read its need by name, `needs["topic"]`. Then save this as `count_words.toml`:

```toml
name = "count-words"
agent = "claude"
about = "Writes one sentence with an exact number of words."

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

A key Jaynes does not know, a reference that does not import, or a question that names a field the output lacks is refused, with the step it is in.

## Stops

`stop=stop(budget=0.2)` makes a step stop for you. The budget is the share of answers you would change that the stop may have once it asks less: with 0.2 it asks one time in ten after 20 clean deliveries in a row. `stop(budget=0.05, sampled=False)` always asks. Most stops show the output and the run goes on; `stop(0.2, waits=True)` holds the run until you answer, for a choice that is yours to make, such as a look among three. It always asks, and only you answer it. [Runs and stops](runs.md#a-stop-asks-less-as-it-proves-right) has how a stop earns that.

## Steps that act on the world

A step Jaynes runs that acts on the world declares it, with `effect`. A step with `effect="irreversible"`, such as booking, paying or sending, may only follow steps with floor stops, so you approve its whole payload. It runs once, with `ctx.idempotency_key` for the service it calls, and a run that stopped while it was running is blocked instead of running it again.

## A role that plans

A role whose step outputs `jaynes.plan.Plan` plans a request: its runs, each with a role, a task, the runs it builds on (`after`) and, when a team planned it, the `specialist` who does it; and why. The step sees the roles the project can use. When its run delivers, Jaynes checks the plan and starts it by itself.

```python
from jaynes.plan import Plan
from jaynes.role import role, stop

ROLE = role("direction", about="Plans a request: which roles, in what order, and why.")
PLAN = ROLE.step("plan", output=Plan, project="read", stop=stop(0.2),
                 instructions="Plan the request with the roles you were given.")
```

## Check a role before it runs

Before any agent works, Jaynes checks the role and refuses it with a list of what to fix:

- a check, question or executor that reads a step its step does not need, or a step of another role;
- a check whose first argument is annotated with another step's output type;
- a step, other than the last, whose output no later step needs and no stop shows, unless it changes the project or acts.

`jaynes check count_words:ROLE` runs the same check alone.

## What a role may import

A role may import these parts of Jaynes, the role API, version 1: `jaynes.role`, `jaynes.plan`, `jaynes.proof`, `jaynes.project`, `jaynes.runners`, `jaynes.suite`, `jaynes.sandbox`, `jaynes.screens`, `jaynes.browser` and `jaynes.inputs`. A package says which version it is written for, and Jaynes refuses one written for another.

The roles that come with Jaynes are the longest examples: they are in [packages/](https://github.com/alk0x1/jaynes/tree/main/packages) on GitHub.
