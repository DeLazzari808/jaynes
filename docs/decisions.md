# Decisions

What was decided, what it costs, and what is still open. Newest first.

## 2026-11-07: A stop no longer holds the run

**Decided.** A run does not wait for the person's answer. It goes on with the
agent's best choice, and the answer can arrive later. An accept changes
nothing; a return redoes the work from that step on, and after delivery it
counts as a correction. Only an action that cannot be undone, such as
publishing, sending or spending, waits for the person. "Always asks" now means
the stop is always shown, not that the run waits.

**Why.** The levels help over time, but a new project with a new specialist
starts with every stop at "always asks", so the start would stay as slow as
today. On the measured project, 67 of 71 hours were waits, mostly while the
person was away, not on hard questions.

**Tradeoffs.**

- A return on an early step throws away the work done after it, a few dollars
  of agent time against hours of waiting.
- A misunderstood goal is built in full before anyone sees it.
- More results pile up for the person, so the queue and a way to halt and undo
  matter more.

## 2026-11-07: Proof the maker cannot see

**Decided.** Two parts, both before the delivery stop may rise a level.

- Hidden checks: a separate agent reads only the request, never the work, and
  writes the cases before the work starts. They sit in the engine's area of
  files no agent receives. The person sees them in the samples. In software
  they are usage scenarios; in marketing, what a piece must meet, such as the
  audience, the promise and what it must not say; in research, the questions
  the report must answer with a source.
- A real run, which each role defines for its area: the real command against
  the real service in software, the screen with real data in design. Where the
  real run cannot be undone, as spending money in marketing, it always asks and
  the proof comes from the outcome after delivery.

When a hidden check fails, the maker sees the case described in words, never
the check itself. After two attempts that still fail, it becomes an exception
and calls the person.

**Tradeoffs.**

- Each failed attempt tells the maker a little about the hidden cases; two
  attempts limit how much.
- The case writer shares the model's blind spots, so the real run stays.
- One more agent per request costs tokens.

## 2026-11-07: Levels per stop, and when the count starts over

**Decided.** A stop has three levels:

1. Always asks: at the start, or after the count showed too many errors.
2. By sample: it has proven right and asks one time in ten, with a concrete
   question. Jaynes does this today.
3. Almost alone: it has proven right for much longer and asks one time in
   fifty, a number to tune. There is no level without samples, because some
   errors never become corrections: the person just lives with a mediocre
   result.

At levels 2 and 3 a stop also asks on doubt. The engine's signals hold in any
area: a check failed and the agent redid the work, the agent asked for
information it lacked, a reviewing agent disagrees, or the specialist works on
that project for the first time. A role adds the signals of its area, such as
touching files outside the agreed ones in software or spending above the usual
in marketing.

An action that cannot be undone always asks, at any level. The rule is the
engine's; each role names which actions of its area cannot be undone.

The count starts over when what the agent receives changes, such as the step's
text or its question. Adding a check does not start it over, because a check
only stops more work and does not make what passes worse.

**Why.** Today any change to a role's text, checks or question starts the
count over, so a specialist that improves every week would never earn trust.

**Tradeoffs.**

- A new check makes the agent retry, and a retry can turn out different, so
  keeping the count is a close approximation, not an exact guarantee.
- Doubt signals ask more often at the start of each project.

## 2026-11-07: Engine, role and specialist; corrections after delivery

**Decided.** The test for where something belongs: if it works the same for
marketing and for software, it is the engine; if it depends on the area, it is
the role.

- The engine runs the steps in order and gives each agent only its step,
  checks that each step delivered, runs the checks the role asked for, stops
  where the role says, records everything, counts right and wrong per stop and
  decides when a stop may ask less.
- A role is the method of one kind of work: its steps and their order, what
  each delivers, its checks, where it stops and with what question, and what a
  delivery is in that area.
- A specialist is several roles with knowledge, memory and a track record.

When someone corrects a delivery afterwards, the count of the stop behind it
changes. That rule is the engine's. How to find which delivery a correction
touched is the role's: the code history in software, the screen in design, the
campaign in marketing.

A correction is one of three kinds:

- An error: it was wrong by what was already known, including a preference
  already written where the agent could read it. It counts as an error.
- A new preference: nobody knew. It does not count and becomes taste memory, of
  the project or general.
- New information: the world changed or a new fact appeared. It does not count.

The agent making the correction proposes the kind and which step it came from;
the person sees it in the result, with no stop. A delivery earns its right
answer after a fixed time per project, 7 days by default, and a correction
found later turns it into an error.

**Tradeoffs.**

- The agent can file a real error as a new preference and keep the count too
  optimistic; the person sees the kind in the result, and samples catch part
  of it.
- A fixed wait ignores how much the delivery was used in that time.

## 2026-11-07: Jaynes promises specialists of any area

**Decided.** The engine serves any area, and nothing in it may depend on code.
The public promise is open from the start: Jaynes runs specialists of any area,
not only software.

**Why.** The private company built on it needs every area, from marketing and
research to sales.

**Tradeoffs.**

- So far only software, and design for software, has been proven. In other
  areas the machine can check less by itself, so trust there rests mostly on
  the outcome after delivery.
- A promise wider than the proof can read as hype.

The positioning in one sentence: "An open engine for agent specialists in any
area. They work alone where they have proven right, and call you everywhere
else."

**Open.** How the page and the README show which areas have been proven.

## 2026-11-07: Jaynes is open, the specialists stay private for now

**Decided.** Jaynes is the open engine that anyone can use. The specialists
built on it, with their roles, checks, knowledge and memory, stay private for
now. Making it easy for anyone to create their own specialists through Jaynes
comes later.

**Tradeoffs.**

- Someone who installs Jaynes gets the engine and only the example roles, so
  they must build their own specialists before it does real work for them.
- Making specialist creation easy becomes a core part of Jaynes, not an extra.

The roles already inside Jaynes stay as public examples at their current
level. From now on specialist work happens in the private repository, and Jaynes
gets only engine improvements: anything a role needs from the engine is public,
the role itself is private. Removing the roles was rejected, because the
repository history is public and removing them would not make them private.

**Open.** How Jaynes finds private specialists that live outside it when the
person asks for a change.

## 2026-11-07: What counts as proof that a step got it right

**Decided.** The main proof comes from outside: the real run passed and no
defect traced to that step appeared for a while afterwards. The person's answer
counts only when they actually looked, and is used for the samples; there the
stop asks a concrete question instead of offering only Accept.

**Why.** Today every Accept counts as a correct answer, and many were given
without reading, so the count said a step was good when nobody had checked it.
The only correction is sending a run back to an earlier step inside the same
run. A defect found in real use after delivery never reached the count.

**Tradeoffs.**

- A step earns trust more slowly, since it waits for the time after delivery.
- Someone has to say which step a later defect came from.

**Settled later the same day:** see the next entry above.

## 2026-11-07: Where a project's context lives, and how a step gets it

**Decided.** A job title is not the unit of a specialist; it needs finer pieces.
The same specialist works on many projects, each for a different owner, so
context sits in three layers:

- The specialist's own roles, checks, techniques and general lessons travel to
  every project.
- A project's identity, users, decisions, glossary, real data and the history
  of its runs live in that project's repository, versioned with its code.
- The person's taste splits in two: general taste travels, and taste about one
  project lives in that project's repository.

A step gets context by address, not by similarity. The run knows its project,
each step declares the context it receives, and the record keeps what it was
shown. The agent may read more files, but only inside its copy of that project.
No similarity search (RAG) for now.

**Why.** One project's context files are about 27 KB today, about 7 thousand
tokens, and fit whole. A search by similarity across projects can return a
plausible fact from another project, and makes it hard to say afterwards why a
passage was sent. Some projects hold data from the companies the person works
for, which must never reach another project.

**Tradeoffs.**

- Taste kept in each repository cannot be seen in one place across projects.
- A step that declares its context misses what nobody declared; reading its
  own copy of the project covers part of that.

**Later.** Similarity search only when a project's history or references
outgrow what fits, such as hundreds of runs or a large reference library; even
then only after filtering by project, and with what was sent recorded.

## 2026-11-06: Jaynes is the engine of a dark software factory

**Decided.** Agents conduct the development cycle. The person writes what they
want and supervises results and exceptions: on the loop, not in it. Work
focuses on the roles; the engine works and needs improvements, not a rewrite.

**Agreed direction, not started.**

- When the external proof passes, the merge happens alone and the person sees
  the result afterwards. Irreversible effects, such as publishing or sending a
  message, keep a stop.
- External proof comes before automatic merge. External proof means checks the
  building agent cannot see or shape, and a run against the real service.

**Why.** On one project built from nothing, 15 delivered runs took about 71
hours, about 67 of them at stops, while agents worked less than 4. The first
screen came about 44 hours in, because the first slices were internal packages.
The stops were accepted without being read. The serious defects appeared only
in real use: one feature passed every agreed example and left most messages
unprocessed on its first real run.

This is one project, one person and a second reader nearby, so it does not show
that stops fail in general.

**Tradeoffs.**

- Automatic merge without external proof would have shipped that defect.
- A misunderstood task surfaces after the build, not before it.
- Hidden checks and extra agents cost tokens, and Jaynes already cost 23% to 61%
  more than direct work in three single measurements.
- A reviewing agent shares the blind spots of the model it reviews, so it does
  not replace a real run.

**Open.**

- A role for new projects whose first slice shows something working.
- A level per step: Jaynes alone, ask on doubt, ask by sample, always ask.
- A reviewing agent that leaves a short recommendation.
- Who writes the hidden scenarios, and who names the real command.
- A queue that starts runs, and a way to halt and undo.

**Not copied from existing factories.** Behavioral clones of external services
(one real run is cheaper at this scale), and removing the person from every
decision.
